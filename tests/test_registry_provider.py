"""The live registry producer observes public metadata only and never fabricates."""
from dataclasses import FrozenInstanceError
import ast
import hashlib
import http.server
import json
from pathlib import Path
import threading
import time

import pytest

from trader.core.instrument_registry import AccountTrading, Capability, Eligibility, Presence
from trader.data import registry_provider as RP
from trader.data.registry_provider import (
    BinanceUsdmRegistryProvider, FetchError, HttpResponse, VenueTarget, urllib_fetch,
)

ROOT = Path(__file__).resolve().parents[1]
RECEIVED = 1_800_000_000_500


def _symbol(name="BTCUSDT", status="TRADING"):
    return {
        "symbol": name, "status": status, "contractType": "PERPETUAL",
        "baseAsset": name[:-4], "quoteAsset": "USDT", "marginAsset": "USDT",
        "onboardDate": 1700000000000, "pricePrecision": 2, "quantityPrecision": 0,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "1.000", "minQty": "2"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.00"},
        ],
    }


def _body(symbols=None, server_time=1_800_000_000_123):
    info = {"timezone": "UTC", "symbols": symbols or [_symbol(), _symbol("ETHUSDT")]}
    if server_time is not None:
        info["serverTime"] = server_time
    return json.dumps(info).encode()


class FakeFetch:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, *, timeout_s, max_bytes):
        self.calls.append((url, timeout_s, max_bytes))
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r if isinstance(r, HttpResponse) else HttpResponse(200, r)


class Clock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


def _provider(*responses, clock=None, target=None, **kw):
    fetch = FakeFetch(*responses)
    clock = clock or Clock(*[x for i in range(len(responses))
                             for x in (RECEIVED - 400 + 1000 * i, RECEIVED + 1000 * i)])
    return BinanceUsdmRegistryProvider(target or VenueTarget.production(),
                                       fetcher=fetch, clock_ms=clock, **kw), fetch


# 1-8: success, cut, UNKNOWN permission state, source identity, hash, serverTime

def test_successful_refresh_builds_snapshot_with_response_received_cut():
    body = _body()
    provider, fetch = _provider(body)
    assert provider.latest() is None and provider.latest_provenance() is None
    result = provider.refresh()
    snap = result.snapshot
    assert result.ok and provider.latest() is snap
    assert [r.instrument_id.value for r in snap.records] == [
        "binance_usdm:futures:BTCUSDT", "binance_usdm:futures:ETHUSDT"]
    assert snap.as_of_ms == RECEIVED
    assert all(r.observed_at_ms == RECEIVED for r in snap.records)
    p = result.provenance
    assert provider.latest_provenance() is p and provider.latest_attempt() is p
    assert (p.outcome, p.failure_reason, p.request_start_ms, p.response_received_ms) == (
        "SUCCESS", None, RECEIVED - 400, RECEIVED)
    assert p.body_sha256 == hashlib.sha256(body).hexdigest() and p.body_bytes == len(body)
    assert p.server_time_ms == 1_800_000_000_123 and p.http_status == 200
    assert (p.snapshot_id, p.as_of_ms, p.record_count) == (snap.snapshot_id, RECEIVED, 2)
    assert p.schema == RP.PROVENANCE_SCHEMA and p.method == "GET"
    with pytest.raises(FrozenInstanceError):
        p.outcome = "FAILED"
    assert fetch.calls == [("https://fapi.binance.com/fapi/v1/exchangeInfo",
                            RP.DEFAULT_TIMEOUT_S, RP.DEFAULT_MAX_BYTES)]


def test_all_account_and_symbol_permission_state_stays_unknown():
    snap = _provider(_body())[0].refresh().snapshot
    assert snap.account_trading is AccountTrading.UNKNOWN
    assert snap.account_scope == RP.ACCOUNT_SCOPE
    for r in snap.records:
        assert r.account_eligibility is Eligibility.UNKNOWN and r.eligibility_basis is None
        assert r.shortability is Capability.UNKNOWN
        assert r.symbol_config is r.leverage_bracket is r.data_availability is Presence.UNKNOWN
        assert r.evidence == ("exchangeInfo",)
    assert snap.by_account_eligibility(Eligibility.ELIGIBLE) == ()


@pytest.mark.parametrize("target,env,host", [
    (VenueTarget.production(), "production", "https://fapi.binance.com"),
    (VenueTarget.demo(), "demo", "https://demo-fapi.binance.com"),
])
def test_source_identifies_actual_host_endpoint_and_environment(target, env, host):
    provider, fetch = _provider(_body(), target=target)
    result = provider.refresh()
    url = host + "/fapi/v1/exchangeInfo"
    assert result.snapshot.source == f"binance-usdm:{env}:{url}"
    assert all(r.source == result.snapshot.source for r in result.snapshot.records)
    p = result.provenance
    assert (p.environment, p.base_url, p.endpoint_path, p.request_url) == (
        env, host, "/fapi/v1/exchangeInfo", url)
    assert fetch.calls[0][0] == url


def test_environment_is_declared_and_checked_never_inferred():
    for bad in [("https://fapi.binance.com", "demo"), ("https://demo-fapi.binance.com", "production"),
                ("https://fapi.binance.com", "unspecified"), ("https://example.com", "production"),
                ("http://fapi.binance.com", "production"), ("https://fapi.binance.com/fapi", "production"),
                ("https://u:p@fapi.binance.com", "production"), ("http://example.com", "unspecified")]:
        with pytest.raises(ValueError):
            VenueTarget(*bad)
    with pytest.raises(TypeError):
        BinanceUsdmRegistryProvider("https://fapi.binance.com")
    assert VenueTarget("http://127.0.0.1:9", "unspecified").source.startswith("binance-usdm:unspecified:")


def test_missing_server_time_is_recorded_as_absent_not_invented():
    result = _provider(_body(server_time=None))[0].refresh()
    assert result.ok and result.provenance.server_time_ms is None
    assert result.snapshot.as_of_ms == RECEIVED


def test_server_time_never_moves_the_cut():
    a = _provider(_body(server_time=1))[0].refresh().snapshot
    b = _provider(_body(server_time=2))[0].refresh().snapshot
    assert a.as_of_ms == b.as_of_ms == RECEIVED and a.snapshot_id == b.snapshot_id


# 9-15: fail closed, first and later

FAILURES = [
    (b"{not json", "malformed_json"),
    (b"\xff\xfe", "malformed_json"),
    (b'{"symbols": [{"symbol": "X"}], "serverTime": NaN}', "non_finite_json"),
    (b"[]", "malformed_exchange_info"),
    (b'{"serverTime": 1}', "malformed_exchange_info"),
    (b'{"symbols": []}', "malformed_exchange_info"),
    (b'{"symbols": ["BTCUSDT"]}', "malformed_exchange_info"),
    (json.dumps({"symbols": [_symbol()], "serverTime": "1"}).encode(), "malformed_server_time"),
    (json.dumps({"symbols": [{"symbol": "BTCUSDT"}]}).encode(), "translation_failed"),
    (json.dumps({"symbols": [_symbol(), _symbol()]}).encode(), "translation_failed"),
    (HttpResponse(503, _body()), "http_error"),
    (FetchError("http_error", 418), "http_error"),
    (FetchError("timeout"), "timeout"),
    (FetchError("oversized", 200), "oversized"),
    (FetchError("redirect_refused", 302), "redirect_refused"),
    (RuntimeError("boom"), "transport_error"),
]


@pytest.mark.parametrize("response,reason", FAILURES)
def test_first_failure_yields_no_snapshot(response, reason):
    provider, _ = _provider(response)
    result = provider.refresh()
    assert not result.ok and result.snapshot is None and provider.latest() is None
    assert provider.latest_provenance() is None
    p = provider.latest_attempt()
    assert p is result.provenance and (p.outcome, p.failure_reason) == ("FAILED", reason)
    assert p.snapshot_id is None and p.as_of_ms is None and p.record_count is None


@pytest.mark.parametrize("response,reason", FAILURES)
def test_later_failure_keeps_prior_snapshot_object_unchanged(response, reason):
    provider, _ = _provider(_body(), response)
    good = provider.refresh()
    snap, sid, as_of, json_before = good.snapshot, good.snapshot.snapshot_id, good.snapshot.as_of_ms, \
        good.snapshot.canonical_json()
    failed = provider.refresh()
    assert failed.snapshot is None and failed.provenance.failure_reason == reason
    assert provider.latest() is snap
    assert (snap.snapshot_id, snap.as_of_ms, snap.canonical_json()) == (sid, as_of, json_before)
    assert provider.latest_provenance() is good.provenance
    assert provider.latest_attempt() is failed.provenance
    assert failed.provenance.request_start_ms == RECEIVED + 600


def test_failed_body_is_hashed_but_not_translated():
    body = b"{not json"
    p = _provider(body)[0].refresh().provenance
    assert p.body_sha256 == hashlib.sha256(body).hexdigest() and p.body_bytes == len(body)
    assert p.response_received_ms == RECEIVED


def test_oversized_body_from_any_fetcher_fails_closed():
    provider, _ = _provider(_body(), max_bytes=100)
    assert provider.refresh().provenance.failure_reason == "oversized" and provider.latest() is None


# 21-23: determinism and atomic replacement

def test_identical_response_and_cut_give_identical_snapshot_id():
    body = _body()
    a = _provider(body, clock=Clock(1, RECEIVED))[0].refresh().snapshot
    b = _provider(body, clock=Clock(99, RECEIVED))[0].refresh().snapshot
    assert a is not b and a.snapshot_id == b.snapshot_id and a.canonical_json() == b.canonical_json()


def test_changed_response_cut_or_host_changes_snapshot_identity():
    base = _provider(_body())[0].refresh().snapshot.snapshot_id
    changed = _provider(_body([_symbol(), _symbol("ETHUSDT", "BREAK")]))[0].refresh().snapshot.snapshot_id
    later = _provider(_body(), clock=Clock(0, RECEIVED + 1))[0].refresh().snapshot.snapshot_id
    demo = _provider(_body(), target=VenueTarget.demo())[0].refresh().snapshot.snapshot_id
    assert len({base, changed, later, demo}) == 4


def test_successful_later_refresh_replaces_snapshot_by_reference():
    provider, _ = _provider(_body(), _body([_symbol("SOLUSDT")]))
    first = provider.refresh().snapshot
    second = provider.refresh().snapshot
    assert provider.latest() is second is not first
    assert first.as_of_ms == RECEIVED and second.as_of_ms == RECEIVED + 1000
    assert [r.instrument_id.venue_symbol for r in first.records] == ["BTCUSDT", "ETHUSDT"]


def test_publication_happens_only_after_translation_completes(monkeypatch):
    provider, _ = _provider(_body(), _body())
    first = provider.refresh().snapshot
    seen = []

    def translate(**kw):
        seen.append(provider.latest())
        raise ValueError("late failure")

    monkeypatch.setattr(RP, "from_binance_usdm_responses", translate)
    assert provider.refresh().provenance.failure_reason == "translation_failed"
    assert seen == [first] and provider.latest() is first


def test_translation_receives_only_exchange_info_no_fabricated_account_data(monkeypatch):
    captured = {}
    real = RP.from_binance_usdm_responses

    def spy(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(RP, "from_binance_usdm_responses", spy)
    _provider(_body())[0].refresh()
    assert set(captured) == {"exchange_info", "as_of_ms", "account_scope", "source"}


# 11-17 against a real local HTTP server: urllib_fetch boundary

class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).seen.append((self.command, self.path, dict(self.headers)))
        kind, payload = type(self).routes.get(self.path, ("status", 404))
        if kind == "body":
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif kind == "chunked_big":  # no Content-Length: size discovered while reading
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        elif kind == "redirect":
            self.send_response(302)
            self.send_header("Location", payload)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif kind == "sleep":
            time.sleep(payload)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        else:
            self.send_response(payload)
            self.send_header("Content-Length", "0")
            self.end_headers()

    do_POST = do_PUT = do_DELETE = do_GET


@pytest.fixture
def server():
    _Handler.routes, _Handler.seen = {}, []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _local(base, **kw):
    return BinanceUsdmRegistryProvider(VenueTarget(base, "unspecified"), **kw)


def test_local_http_success_sends_one_unauthenticated_get(server):
    httpd, base = server
    body = _body()
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("body", body)
    result = _local(base).refresh()
    assert result.ok and result.provenance.body_sha256 == hashlib.sha256(body).hexdigest()
    assert result.snapshot.source == f"binance-usdm:unspecified:{base}/fapi/v1/exchangeInfo"
    assert len(_Handler.seen) == 1
    method, path, headers = _Handler.seen[0]
    assert (method, path) == ("GET", "/fapi/v1/exchangeInfo")  # no query: no signature/timestamp
    lowered = {k.lower() for k in headers}
    assert not lowered & {"x-mbx-apikey", "authorization", "cookie"}


def test_local_http_error_fails_closed(server):
    _, base = server
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("status", 500)
    p = _local(base).refresh().provenance
    assert (p.failure_reason, p.http_status) == ("http_error", 500)


def test_local_malformed_json_fails_closed(server):
    _, base = server
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("body", b"<html>")
    assert _local(base).refresh().provenance.failure_reason == "malformed_json"


def test_local_oversized_by_content_length_and_by_stream(server):
    _, base = server
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("body", b"x" * 5000)
    assert _local(base, max_bytes=1000).refresh().provenance.failure_reason == "oversized"
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("chunked_big", b"x" * 5000)
    p = _local(base, max_bytes=1000).refresh().provenance
    assert p.failure_reason == "oversized" and p.body_sha256 is None


def test_local_timeout_fails_closed(server):
    _, base = server
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("sleep", 1.0)
    started = time.monotonic()
    p = _local(base, timeout_s=0.2).refresh().provenance
    assert p.failure_reason == "timeout" and time.monotonic() - started < 0.9


@pytest.mark.parametrize("location", ["https://evil.example/fapi/v1/exchangeInfo", "/elsewhere"])
def test_redirects_are_refused_even_same_host(server, location):
    _, base = server
    _Handler.routes["/fapi/v1/exchangeInfo"] = ("redirect", location)
    _Handler.routes["/elsewhere"] = ("body", _body())
    provider = _local(base)
    p = provider.refresh().provenance
    assert (p.failure_reason, p.http_status) == ("redirect_refused", 302)
    assert provider.latest() is None
    assert [s[1] for s in _Handler.seen] == ["/fapi/v1/exchangeInfo"]  # never followed


def test_unreachable_host_fails_closed():
    with pytest.raises(FetchError) as exc:
        urllib_fetch("http://127.0.0.1:9/fapi/v1/exchangeInfo", timeout_s=1, max_bytes=10)
    assert exc.value.reason == "network_error"


# 17-20: static boundaries

SOURCE = (ROOT / "trader/data/registry_provider.py").read_text()


def _code_only(source):
    """Executable code without docstrings or comments."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


CODE = _code_only(SOURCE)


def test_provider_imports_no_trading_credentials_ccxt_feed_universe_or_kernel():
    imported = set()
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported <= {"__future__", "hashlib", "json", "threading", "time", "urllib.error",
                        "urllib.parse", "urllib.request", "collections.abc", "dataclasses",
                        "typing", "..core.instrument_registry", ".binance_usdm_registry"}
    lowered = CODE.lower()
    for forbidden in ("ccxt", "apikey", "api_key", "secret", "signature", "x-mbx", "dotenv",
                      "os.environ", "/order", "create_order", "/account", "/fapi/v2", "/fapi/v3",
                      "symbolconfig", "leveragebracket", "feed", "universe", "executor", "kernel"):
        assert forbidden not in lowered, forbidden


def test_only_endpoint_is_public_exchange_info():
    assert RP.ENDPOINT_PATH == "/fapi/v1/exchangeInfo"
    assert CODE.count("/fapi/") == 1


def test_provider_wiring_is_confined_to_attention_integration():
    allowed = {ROOT / "trader" / "data" / "registry_provider.py",
               ROOT / "trader" / "kernel.py",
               ROOT / "trader" / "observability" / "attention.py",
               ROOT / "trader" / "observability" / "collector_health.py"}
    wired = {path for path in (ROOT / "trader").rglob("*.py")
             if "registry_provider" in path.read_text(errors="ignore")}
    assert wired == allowed
