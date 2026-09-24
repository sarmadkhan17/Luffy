"""Registry-backed supplemental Attention wired into the Kernel (one slot).

Locked order: snapshot -> cursor -> pure select -> atomic selection commit ->
fetch -> separate fetch-outcome commit -> result to Attention capture.
All transport is mocked; no test touches the network.
"""
import sqlite3
import threading
import time
from unittest.mock import Mock, patch

import pytest

from trader.core.journal import Journal
from trader.core.types import MarketType
from trader.data.registry_provider import (
    BinanceUsdmRegistryProvider, HttpResponse, VenueTarget,
)
from trader.kernel import Kernel
from trader.observability import attention as A
from trader.observability import registry_selector as rs
from trader.observability import selection_persistence as sp
from trader.observability import supplemental as S
from trader.observability.attention import (
    RegistryAttention, RegistryRefresher, capture, reason_token, registry_settings,
    settings, supplemental_mode,
)
from tests.test_attention_supplemental import FakeEx, kernel, scan_frames, trading_calls
from tests.test_registry_provider import _body, _symbol

PROD_KLINES = "https://fapi.binance.com/fapi/v1/klines"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


class Fetch:
    """Scripted exchangeInfo transport."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, *, timeout_s, max_bytes):
        self.calls.append(url)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return HttpResponse(200, r)


def provider(*bases, fetch=None, target=None):
    body = _body([_symbol(b + "USDT") for b in bases])
    fetch = fetch or Fetch(body)
    return BinanceUsdmRegistryProvider(target or VenueTarget.production(), fetcher=fetch), fetch


def source(ex=None):
    src = S.IsolatedSource(2, url=PROD_KLINES)
    if ex is not None:
        src.acquire = ex.fetch_ohlcv
    return src


def slot(tmp_path, *bases, ex=None, refresh=True, clock=None, **kw):
    prov, fetch = provider(*bases or ("BTC", "ETH"))
    if refresh:
        assert prov.refresh().ok
    journal = Journal(tmp_path / "luffy.db")
    ex = ex or FakeEx(total=60)
    reg = RegistryAttention(prov, source(ex), journal,
                            max_snapshot_age_ms=kw.get("max_age", A.MAX_SNAPSHOT_AGE_MS),
                            **({"clock_ms": clock} if clock else {}))
    return reg, prov, journal, ex


def rows(journal):
    return journal.query("SELECT * FROM attention_selections")


def cursor(journal):
    return journal.attention_cursor(sp.CURSOR_NAME)


def fresh_kernel(market=MarketType.FUTURES, journal=None):
    k = object.__new__(Kernel)
    k._attention_error = None
    k._attention_extra = k._attention_source = None
    k._attention_mode = "off"
    k._attention_registry = k._attention_refresher = None
    k.market_type = market
    k.journal = journal
    return k


def registry_kernel(tmp_path, reg, prov=None):
    k, data, decided = kernel(tmp_path, None, Mock())
    k.market_type = MarketType.FUTURES
    k._attention_registry = reg
    k._attention_refresher = RegistryRefresher(prov or reg.provider)
    return k, data, decided


# 1 ── mode resolution and backward compatibility ──────────────────────────

@pytest.mark.parametrize("raw,mode", [
    ({}, "off"), ({"supplemental_symbol": None}, "off"),
    ({"supplemental_symbol": "C/USDT"}, "manual"),
    ({"supplemental_mode": "off"}, "off"), ({"supplemental_mode": False}, "off"),
    ({"supplemental_mode": "manual", "supplemental_symbol": "C/USDT"}, "manual"),
    ({"supplemental_mode": "registry"}, "registry"),
])
def test_supplemental_mode_and_backward_compatibility(raw, mode):
    assert supplemental_mode(raw) == mode


@pytest.mark.parametrize("raw", [{"supplemental_mode": "auto"},
                                 {"supplemental_mode": "manual"}])
def test_invalid_mode_refused(raw):
    with pytest.raises(ValueError):
        supplemental_mode(raw)


def test_kernel_setup_modes(tmp_path):
    k = fresh_kernel(journal=Journal(tmp_path / "j.db"))
    k._attention_supplemental_setup({})
    assert k._attention_mode == "off" and k._attention_source is None
    k = fresh_kernel()
    k._attention_supplemental_setup({"supplemental_symbol": "C/USDT"})
    assert k._attention_mode == "manual" and k._attention_extra == "C/USDT"
    assert k._attention_registry is None
    k = fresh_kernel(journal=Journal(tmp_path / "j.db"))
    k._attention_supplemental_setup({"supplemental_mode": "registry",
                                     "observation_target": "production"})
    assert k._attention_mode == "registry" and k._attention_extra is None
    assert isinstance(k._attention_registry, RegistryAttention)
    assert k._attention_refresher.thread is None  # started by boot(), not setup


# 2 ── conflict disables supplemental only ─────────────────────────────────

def test_registry_plus_manual_symbol_is_a_conflict(tmp_path):
    raw = {"supplemental_mode": "registry", "supplemental_symbol": "C/USDT",
           "observation_target": "production"}
    with pytest.raises(ValueError, match="supplemental_mode_conflict"):
        supplemental_mode(raw)
    k = fresh_kernel(journal=Journal(tmp_path / "j.db"))
    k._attention_supplemental_setup(raw)
    assert k._attention_error == "supplemental_mode_conflict"
    assert k._attention_mode == "off"
    assert k._attention_extra is None and k._attention_registry is None


def test_conflict_keeps_trading_and_scan_capture(tmp_path):
    (tmp_path / "b").mkdir(); (tmp_path / "c").mkdir()
    base, _, base_decided = kernel(tmp_path / "b", None, Mock())
    base.market_type = MarketType.FUTURES
    base_stats = base.cycle()
    k, data, decided = kernel(tmp_path / "c", None, Mock())
    k.market_type = MarketType.FUTURES
    k._attention_supplemental_setup({"supplemental_mode": "registry",
                                     "supplemental_symbol": "C/USDT"})
    stats = k.cycle()
    assert trading_calls(k, decided) == trading_calls(base, base_decided)
    assert stats["entries"] == base_stats["entries"] == 2
    event = k._attention.queue.get_nowait()
    assert event["kind"] == "scan" and "supplemental" not in event["scope"]
    assert stats["attention"]["kernel_error"] == "supplemental_mode_conflict"


# 3 ── production venue, explicit, never BINANCE_DEMO ──────────────────────

@pytest.mark.parametrize("demo", ["true", "false"])
def test_production_target_is_explicit_not_binance_demo(tmp_path, monkeypatch, demo):
    monkeypatch.setenv("BINANCE_DEMO", demo)
    k = fresh_kernel(journal=Journal(tmp_path / "j.db"))
    k._attention_supplemental_setup({"supplemental_mode": "registry",
                                     "observation_target": "production"})
    reg = k._attention_registry
    assert reg.provider.target == VenueTarget.production()
    assert reg.source.url == PROD_KLINES
    assert reg.source.url.startswith(reg.provider.target.base_url)


@pytest.mark.parametrize("target", [None, "demo", "Production", "unspecified"])
def test_missing_or_non_production_target_refused(tmp_path, monkeypatch, target):
    monkeypatch.setenv("BINANCE_DEMO", "false")
    raw = {"supplemental_mode": "registry"}
    if target is not None:
        raw["observation_target"] = target
    with pytest.raises(ValueError):
        registry_settings(raw)
    k = fresh_kernel(journal=Journal(tmp_path / "j.db"))
    k._attention_supplemental_setup(raw)
    assert k._attention_registry is None and k._attention_mode == "off"
    assert k._attention_error == "supplemental_refused"


def test_slot_refuses_demo_provider_or_mismatched_fetch_venue(tmp_path):
    j = Journal(tmp_path / "j.db")
    demo, _ = provider("BTC", target=VenueTarget.demo())
    with pytest.raises(ValueError):
        RegistryAttention(demo, source(), j, max_snapshot_age_ms=1)
    prod, _ = provider("BTC")
    other = S.IsolatedSource(2, url="https://demo-fapi.binance.com/fapi/v1/klines")
    with pytest.raises(ValueError):
        RegistryAttention(prod, other, j, max_snapshot_age_ms=1)


# 4 ── FUTURES only ────────────────────────────────────────────────────────

def test_registry_mode_refused_outside_futures(tmp_path):
    k = fresh_kernel(market=MarketType.SPOT, journal=Journal(tmp_path / "j.db"))
    k._attention_supplemental_setup({"supplemental_mode": "registry",
                                     "observation_target": "production"})
    assert k._attention_registry is None and k._attention_refresher is None
    assert k._attention_error == "supplemental_refused"


# 5-6 ── scan identity boundary ────────────────────────────────────────────

def test_scan_symbols_translated_for_selector_only(tmp_path):
    k = fresh_kernel()
    assert k._attention_registry_scan(["BTC/USDT", "ETH/USDT:USDT", "X/BTC",
                                       "btc/USDT", "TOO_LONG_BASE_ASSET_123456/USDT"]) == [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "X/BTC", "btc/USDT",
        "TOO_LONG_BASE_ASSET_123456/USDT"]
    reg, _, journal, _ = slot(tmp_path, "BTC", "ETH")
    k, _, _ = registry_kernel(tmp_path, reg)
    k._scan_symbols = Mock(return_value=["BTC/USDT"])
    seen = []
    real = rs.select
    rs_select = Mock(side_effect=lambda *a, **kw: seen.append(kw["strategy_scan"]) or real(*a, **kw))
    with patch.object(rs, "select", rs_select):
        k.cycle()
    assert seen == [["BTC/USDT:USDT"]]
    assert k._scan_symbols() == ["BTC/USDT"]  # the trading scan keeps its spelling


def test_scan_member_is_never_selected(tmp_path):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH")
    for _ in range(4):
        result, _, tel = reg.run(["BTC/USDT:USDT"], "4h")
        assert tel["selected_id"] != "binance_usdm:futures:BTCUSDT"
        assert result.symbol == "ETH/USDT:USDT"
    (tmp_path / "x").mkdir()
    reg2, _, _, ex2 = slot(tmp_path / "x", "BTC")
    result, _, tel = reg2.run(["BTC/USDT:USDT"], "4h")
    assert result is None and tel["outcome"] == rs.NOTHING_SELECTABLE and ex2.calls == []


# 7-8 ── background single-flight refresh ──────────────────────────────────

def test_cycle_never_refreshes_or_touches_network(tmp_path):
    reg, prov, journal, _ = slot(tmp_path, "BTC", "ETH")
    fetch = prov._fetch
    prov.refresh = Mock(side_effect=AssertionError("refresh in cycle"))
    k, _, _ = registry_kernel(tmp_path, reg)
    before = len(fetch.calls)
    stats = k.cycle()
    assert len(fetch.calls) == before and prov.refresh.call_count == 0
    assert stats["attention_registry"]["stage"] == "captured"


def test_refresher_is_one_background_thread():
    prov, fetch = provider("BTC")
    r = RegistryRefresher(prov, refresh_s=3600, retry_s=3600)
    t = r.start()
    assert r.start() is t and t.name == "attention-registry-refresh" and t.daemon
    deadline = time.time() + 5
    while prov.latest() is None and time.time() < deadline:
        time.sleep(.01)
    r.close()
    assert prov.latest() is not None and len(fetch.calls) == 1
    assert threading.current_thread() is not t


def test_refresh_health_records_last_success_and_failure():
    body = _body([_symbol("BTCUSDT")])
    prov, _ = provider(fetch=Fetch(body, OSError("down")))
    refresher = RegistryRefresher(prov)
    assert refresher.refresh_once() is True
    success = refresher.health()
    assert success["last_success_ms"] is not None
    assert success["last_failure_ms"] is None
    assert refresher.refresh_once() is False
    failed = refresher.health()
    assert failed["last_success_ms"] == success["last_success_ms"]
    assert failed["last_failure_ms"] is not None
    assert failed["last_attempt_outcome"] == "FAILED"
    assert failed["snapshot_id"] == prov.latest().snapshot_id


def test_hung_refresh_does_not_block_cycle(tmp_path):
    gate, entered = threading.Event(), threading.Event()
    body = _body([_symbol("BTCUSDT"), _symbol("ETHUSDT")])

    class Hang(Fetch):
        def __call__(self, url, *, timeout_s, max_bytes):
            if self.calls:
                entered.set()
                gate.wait(10)
            return super().__call__(url, timeout_s=timeout_s, max_bytes=max_bytes)
    prov, _ = provider(fetch=Hang(body, body))
    assert prov.refresh().ok
    reg = RegistryAttention(prov, source(FakeEx(total=60)), Journal(tmp_path / "j.db"),
                            max_snapshot_age_ms=A.MAX_SNAPSHOT_AGE_MS)
    k, _, _ = registry_kernel(tmp_path, reg)
    k._attention_refresher = RegistryRefresher(prov, refresh_s=3600, retry_s=3600)
    k._attention_refresher.start()
    assert entered.wait(5)
    try:
        t0 = time.monotonic()
        stats = k.cycle()
        assert time.monotonic() - t0 < 5
        assert stats["attention_registry"]["refresh"]["in_flight"] is True
        assert stats["attention_registry"]["stage"] == "captured"  # last good snapshot
        assert stats["entries"] == 2
    finally:
        gate.set()
        k._attention_refresher.close()


# 9-12 ── snapshot and selector outcomes ───────────────────────────────────

def test_no_snapshot_no_selector_no_row_no_fetch(tmp_path, monkeypatch):
    reg, _, journal, ex = slot(tmp_path, "BTC", refresh=False)
    monkeypatch.setattr(rs, "select", Mock(side_effect=AssertionError))
    result, prov_, tel = reg.run([], "4h")
    assert (result, prov_, tel["reason"]) == (None, None, "no_snapshot")
    assert rows(journal) == [] and ex.calls == []


def test_stale_snapshot_persisted_cursor_unchanged_no_fetch(tmp_path):
    reg, prov, journal, ex = slot(tmp_path, "BTC", "ETH", max_age=1_000,
                                  clock=lambda: prov_as_of[0] + 5_000)
    prov_as_of = [prov.latest().as_of_ms]
    result, _, tel = reg.run([], "4h")
    assert result is None and tel["outcome"] == rs.SNAPSHOT_STALE
    [row] = rows(journal)
    assert row["outcome"] == rs.SNAPSHOT_STALE and row["cursor_after"] is None
    assert cursor(journal) is None and ex.calls == []
    assert prov.latest().as_of_ms == prov_as_of[0]  # never re-stamped


def test_failed_refresh_keeps_fresh_previous_snapshot(tmp_path):
    body = _body([_symbol("BTCUSDT"), _symbol("ETHUSDT")])
    prov, _ = provider(fetch=Fetch(body, OSError("down")))
    assert prov.refresh().ok
    good = prov.latest()
    assert not prov.refresh().ok and prov.latest() is good
    reg = RegistryAttention(prov, source(FakeEx(total=60)), Journal(tmp_path / "j.db"),
                            max_snapshot_age_ms=A.MAX_SNAPSHOT_AGE_MS)
    result, provenance, tel = reg.run([], "4h")
    assert tel["outcome"] == rs.SELECTED and provenance["snapshot_id"] == good.snapshot_id
    assert result.usable


def test_failed_registry_refresh_does_not_change_trading_calls(tmp_path):
    (tmp_path / "base").mkdir(); (tmp_path / "registry").mkdir()
    base, _, base_decided = kernel(tmp_path / "base", None, Mock())
    base.market_type = MarketType.FUTURES
    baseline = base.cycle()
    body = _body([_symbol("BTCUSDT"), _symbol("ETHUSDT")])
    prov, _ = provider(fetch=Fetch(body, OSError("down")))
    refresher = RegistryRefresher(prov)
    assert refresher.refresh_once() is True
    assert refresher.refresh_once() is False
    reg = RegistryAttention(prov, source(FakeEx(total=60)), Journal(tmp_path / "j.db"),
                            max_snapshot_age_ms=A.MAX_SNAPSHOT_AGE_MS)
    k, _, decided = registry_kernel(tmp_path / "registry", reg)
    k._attention_refresher = refresher
    result = k.cycle()
    assert result["attention_registry"]["refresh"]["last_attempt_outcome"] == "FAILED"
    assert trading_calls(k, decided) == trading_calls(base, base_decided)
    assert result["entries"] == baseline["entries"]


def test_selector_refusal_persists_nothing(tmp_path):
    reg, prov, journal, ex = slot(tmp_path, "BTC", clock=lambda: 1)  # snapshot in the future
    result, _, tel = reg.run([], "4h")
    assert result is None and tel["reason"] == rs.SNAPSHOT_FUTURE and tel["detail"]
    assert rows(journal) == [] and cursor(journal) is None and ex.calls == []


# 13-15 ── cursor and persistence failures ─────────────────────────────────

def test_corrupt_cursor_disables_for_process_without_repair(tmp_path, monkeypatch):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH")
    with journal._tx() as c:
        c.execute("INSERT INTO attention_cursor(name,value) VALUES (?,?)",
                  (sp.CURSOR_NAME, "garbage"))
    _, _, tel = reg.run([], "4h")
    assert tel["reason"] == sp.CORRUPT_CURSOR and reg.disabled == sp.CORRUPT_CURSOR
    monkeypatch.setattr(sp, "load_cursor", Mock(side_effect=AssertionError))
    _, _, tel = reg.run([], "4h")
    assert tel["stage"] == "disabled"
    assert cursor(journal) == "garbage" and rows(journal) == [] and ex.calls == []


def test_cursor_conflict_no_fetch(tmp_path, monkeypatch):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH")
    reg.run([], "4h")
    advanced, calls = cursor(journal), len(ex.calls)
    monkeypatch.setattr(sp, "load_cursor", lambda j: None)  # a stale writer's view
    result, _, tel = reg.run([], "4h")
    assert result is None and tel["reason"] == sp.CURSOR_CONFLICT
    assert cursor(journal) == advanced and len(rows(journal)) == 1 and len(ex.calls) == calls


def test_selection_persistence_failure_no_fetch_trading_continues(tmp_path):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH")
    journal.record_attention_selection = Mock(side_effect=sqlite3.OperationalError("locked"))
    k, _, _ = registry_kernel(tmp_path, reg)
    stats = k.cycle()
    assert stats["attention_registry"]["stage"] == "persist_selection"
    assert stats["attention_registry"]["reason"] == "persistence_error:OperationalError"
    assert ex.calls == [] and "attention_supplemental" not in stats
    assert stats["entries"] == 2


# 16-19 ── ordering, cursor, outcome tokens ────────────────────────────────

def test_selection_committed_before_fetch(tmp_path):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH")
    seen = {}
    inner = ex.fetch_ohlcv

    def acquire(symbol, tf, limit):
        other = Journal(journal.db_path)  # a separate connection sees the commit
        seen["rows"] = other.query("SELECT selection_id FROM attention_selections")
        seen["cursor"] = other.attention_cursor(sp.CURSOR_NAME)
        seen["outcomes"] = other.query("SELECT * FROM attention_fetch_outcomes")
        return inner(symbol, tf, limit=limit)
    reg.source.acquire = acquire
    _, provenance, tel = reg.run([], "4h")
    assert seen["rows"] == [{"selection_id": provenance["selection_id"]}]
    assert seen["cursor"] == tel["cursor_after"] and seen["outcomes"] == []
    [out] = journal.attention_fetch_outcomes(provenance["selection_id"])
    assert out["status"] == "usable" and out["reason"] == "complete_window"
    assert out["started_ms"] == tel["started_ms"] and out["ended_ms"] == tel["ended_ms"]


def test_fetch_uses_exact_selector_cut_not_fetch_start(tmp_path, monkeypatch):
    reg, prov, journal, _ = slot(tmp_path, "BTC")
    cut = prov.latest().as_of_ms + 100
    ticks = iter((cut, cut + 100, cut + 200, cut + 300))
    reg._clock = lambda: next(ticks)
    seen = Mock(return_value=S.SupplementalResult(
        "BTC/USDT:USDT", "4h", cut, S.MISSING, "no_data", 27, 30, 0,
        cut - 27 * 14_400_000, cut - 14_400_000))
    monkeypatch.setattr(S, "fetch", seen)
    result, provenance, tel = reg.run([], "4h")
    assert result.status == S.MISSING
    assert seen.call_args.args[3] == cut
    assert tel["started_ms"] == cut + 200
    assert sp.load_selection(journal, provenance["selection_id"]).cycle_as_of_ms == cut


def test_cursor_advances_despite_fetch_failure(tmp_path):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH", ex=FakeEx(fail=OSError("down")))
    result, provenance, tel = reg.run([], "4h")
    assert result.status == S.ERROR and tel["status"] == S.ERROR
    assert cursor(journal) == tel["cursor_after"] != tel["cursor_before"]
    [out] = journal.attention_fetch_outcomes(provenance["selection_id"])
    assert out["status"] == "error" and out["reason"] == "fetch_failed_oserror"
    assert tel["fetch_reason"] == "fetch_failed:OSError"
    result2, _, tel2 = reg.run([], "4h")  # next cycle moves on; no rollback
    assert tel2["cursor_before"] == tel["cursor_after"]


def test_fetch_error_reason_stays_exact_in_runtime_and_capture(tmp_path):
    reg, _, journal, _ = slot(tmp_path, "BTC", ex=FakeEx(fail=OSError("down")))
    k, _, _ = registry_kernel(tmp_path, reg)
    stats = k.cycle()
    sid = stats["attention_registry"]["selection_id"]
    assert stats["attention_registry"]["fetch_reason"] == "fetch_failed:OSError"
    assert stats["attention_supplemental"]["reason"] == "fetch_failed:OSError"
    [outcome] = journal.attention_fetch_outcomes(sid)
    assert outcome["reason"] == "fetch_failed_oserror"
    event = k._attention.queue.get_nowait()
    assert event["scope"]["supplemental"]["reason"] == "fetch_failed:OSError"


def test_reason_token_normalization():
    assert reason_token("fetch_failed:OSError") == "fetch_failed_oserror"
    assert reason_token("Invalid Response:row-time") == "invalid_response_row_time"
    long = "X" * 100 + ":y"
    assert reason_token(long) == "x" * 64
    assert reason_token("") == "unknown"
    for r in ("a:b", "ÄÖ", "fetch_timeout"):
        tok = reason_token(r)
        assert len(tok) <= 64 and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in tok)


def test_fetch_outcome_persist_failure_blocks_capture(tmp_path):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH", ex=FakeEx(fail=OSError("Down!")))
    journal.record_attention_fetch_outcome = Mock(side_effect=sqlite3.OperationalError("x"))
    k, _, _ = registry_kernel(tmp_path, reg)
    stats = k.cycle()
    tel = stats["attention_registry"]
    assert tel["stage"] == "persist_fetch_outcome" and tel["fetch_reason"] == "fetch_failed:OSError"
    assert tel["persistence_error"] == "x"
    assert "attention_supplemental" not in stats and len(ex.calls) == 1
    [row] = rows(journal)
    assert row["selection_id"] == tel["selection_id"] and cursor(journal) == tel["cursor_after"]
    event = k._attention.queue.get_nowait()
    assert "supplemental" not in event["scope"]
    assert not any(i["symbol"] == tel["symbol"] for i in event["issues"])
    assert stats["entries"] == 2


# 20-23 ── provenance, single slot, legacy, trading unchanged ──────────────

def test_provenance_links_snapshot_selection_and_capture(tmp_path):
    reg, prov, journal, _ = slot(tmp_path, "BTC", "ETH")
    k, _, _ = registry_kernel(tmp_path, reg)
    stats = k.cycle()
    p = stats["attention_supplemental"]["provenance"]
    assert p == {"mode": "registry", "selection_id": stats["attention_registry"]["selection_id"],
                 "snapshot_id": prov.latest().snapshot_id}
    sel = sp.load_selection(journal, p["selection_id"])
    assert sel.snapshot_id == p["snapshot_id"]
    assert sel.selected_symbol == stats["attention_supplemental"]["symbol"]
    [out] = journal.attention_fetch_outcomes(p["selection_id"])
    assert out["status"] == stats["attention_supplemental"]["status"]
    event = k._attention.queue.get_nowait()
    sup = event["scope"]["supplemental"]
    assert sup["provenance"] == p and sup["symbol"] == sel.selected_symbol
    assert any(m["symbol"] == sel.selected_symbol for m in event["input"]["membership"])


def test_fetch_outcome_is_committed_before_attention_capture(tmp_path):
    reg, _, journal, _ = slot(tmp_path, "BTC")
    k, _, _ = registry_kernel(tmp_path, reg)
    original = k._attention.begin
    observed = []

    def begin(*args):
        selection = journal.query("SELECT selection_id FROM attention_selections")
        assert len(selection) == 1
        observed.extend(journal.attention_fetch_outcomes(selection[0]["selection_id"]))
        return original(*args)

    k._attention.begin = begin
    k.cycle()
    assert len(observed) == 1 and observed[0]["status"] == S.USABLE


def test_never_two_supplemental_slots(tmp_path):
    reg, _, journal, ex = slot(tmp_path, "BTC", "ETH")
    k, _, _ = registry_kernel(tmp_path, reg)
    manual = S.IsolatedSource(2)
    manual.acquire = Mock(side_effect=AssertionError("second slot"))
    k._attention_source, k._attention_extra = manual, "C/USDT"  # forced; config forbids it
    k._attention.begin = Mock(wraps=k._attention.begin)
    for _ in range(3):
        k.cycle()
    assert manual.acquire.call_count == 0 and len(ex.calls) == 3
    assert len(rows(journal)) == 3  # at most one rotation slot per cycle
    for call in k._attention.begin.call_args_list:
        assert call.args[3] is not None and call.args[3].symbol.endswith(":USDT")


def test_legacy_manual_capture_has_no_provenance():
    ex = FakeEx(total=60)
    now = int(time.time() * 1000)
    r = S.fetch(source(ex), "C/USDT", "4h", now)
    event = capture(scan_frames(["A/USDT"]), ["A/USDT"], "s", settings(), now, r)
    assert "provenance" not in event["scope"]["supplemental"]
    with pytest.raises(ValueError):
        capture(scan_frames(["A/USDT"]), ["A/USDT"], "s", settings(), now, None,
                {"mode": "registry", "selection_id": "a", "snapshot_id": "b"})
    with pytest.raises(ValueError):
        capture(scan_frames(["A/USDT"]), ["A/USDT"], "s", settings(), now, r,
                {"mode": "manual", "selection_id": "a", "snapshot_id": "b"})


def test_legacy_kernel_without_new_attributes_uses_manual_path(tmp_path):
    feed = S.IsolatedSource(2)
    feed.acquire = FakeEx(total=60).fetch_ohlcv
    k, _, _ = kernel(tmp_path, "C/USDT", feed)  # built without registry attributes
    stats = k.cycle()
    assert stats["attention_supplemental"] == {
        "symbol": "C/USDT", "status": "usable", "reason": "complete_window"}
    assert "attention_registry" not in stats
    event = k._attention.queue.get_nowait()
    assert "provenance" not in event["scope"]["supplemental"]


def test_trading_scan_orchestrator_risk_execution_unchanged(tmp_path):
    (tmp_path / "b").mkdir(); (tmp_path / "r").mkdir()
    base, _, base_decided = kernel(tmp_path / "b", None, Mock())
    base.market_type = MarketType.FUTURES
    base_stats = base.cycle()
    reg, _, journal, ex = slot(tmp_path / "r", "BTC", "ETH", "SOL")
    k, data, decided = registry_kernel(tmp_path / "r", reg)
    stats = k.cycle()
    selected = stats["attention_supplemental"]["symbol"]
    assert trading_calls(k, decided) == trading_calls(base, base_decided)
    assert selected not in repr(trading_calls(k, decided))
    assert selected not in data and k._scan_symbols() == ["A/USDT", "B/USDT"]
    for key in ("scanned", "decisions", "entries", "skips"):
        assert stats[key] == base_stats[key]
    assert k.feed.method_calls == []
