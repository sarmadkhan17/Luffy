"""LUFFY-CAPACITY-EVIDENCE-CAPTURE-R1: durable, replayable capacity evidence
from reads that already exist — and nothing invented where they are silent."""
import json
import pathlib
import sqlite3
from types import SimpleNamespace as NS

import pytest

from trader.core.instrument_registry import Eligibility
from trader.core.journal import Journal
from trader.core.types import MarketType
from trader.data.binance_usdm_registry import from_binance_usdm_responses
from trader.engine import evidence_capture as E
from trader.engine import execution_evidence as X
from trader.observability import depth_evidence as D
from trader.observability.portfolio_observation import observe_positions
from trader.strategy import capacity as C
from tests.test_strategy_capacity import (  # noqa: F401
    BTC, NOW, _dims, _inputs, _open, _publish, _registry, world)
from tests.test_strategy_factory_handoff import cfg  # noqa: F401
from tests.test_trade_provenance import REF, env, open_trade  # noqa: F401

ACCOUNT_URL = "https://demo-fapi.binance.com/fapi/v3/account"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _account_body(equity="10000.0", available="7250.5", **extra):
    body = {"totalMarginBalance": equity, "totalWalletBalance": equity,
            "assets": [], "positions": [], **extra}
    if available is not None:
        body["availableBalance"] = available
    return json.dumps(body).encode()


def _margin(body, received=NOW - 30_000, url=ACCOUNT_URL):
    return E.margin_observation(body, request_url=url, request_start_ms=received - 80,
                                received_ms=received)


# ── 1. available margin from the existing account response ──────────────
class _Resp:
    def __init__(self, body):
        self.content = body

    def json(self):
        return json.loads(self.content)


def _kernel(tmp_path, monkeypatch, body):
    import trader.kernel as K
    from trader.core import config
    k = object.__new__(K.Kernel)
    k.journal = Journal(tmp_path / "k.db")
    k.exchange = NS(urls={"api": {"fapiPrivate": "https://demo-fapi.binance.com/fapi/v1"}},
                    fetch_balance=lambda: pytest.fail("fallback read not expected"))
    k.risk = NS(update_equity=lambda b, authoritative: {"risk_state": "ok"})
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return _Resp(body)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(config.Env, "binance_keys", staticmethod(lambda: ("KEY", "SECRET")))
    return k, calls


def test_available_margin_persisted_from_the_same_account_response(tmp_path, monkeypatch):
    body = _account_body()
    k, calls = _kernel(tmp_path, monkeypatch, body)
    balance, _ = k._risk_step()
    assert balance == 10000.0                                   # Risk's input unchanged
    assert len(calls) == 1 and calls[0][0] == ACCOUNT_URL        # no additional request
    rec = json.loads(k.journal.kv_get(E.MARGIN_KV))
    assert E.verify_margin(rec) is rec or E.verify_margin(rec) == rec
    assert (rec["status"], rec["value_text"], rec["value"]) == ("AVAILABLE", "7250.5", 7250.5)
    assert (rec["asset"], rec["venue"], rec["market_type"], rec["environment"]) == \
        ("USDT", "binance_usdm", "futures", "demo")
    assert rec["endpoint"] == "/fapi/v3/account" and rec["field"] == "availableBalance"
    assert rec["response_sha256"] == E._sha(body) and rec["equity_value_text"] == "10000.0"
    assert rec["observed_at_ms"] == rec["received_at_ms"] >= rec["request_start_ms"]
    # neither the key, the secret nor the signed query is persisted
    text = k.journal.kv_get(E.MARGIN_KV)
    assert "KEY" not in text and "SECRET" not in text and "signature" not in text
    # the account observation Risk already publishes is unchanged in kind
    assert json.loads(k.journal.kv_get("account_observation"))["value"] == 10000.0


def test_absent_margin_field_stays_unavailable(tmp_path, monkeypatch):
    k, calls = _kernel(tmp_path, monkeypatch, _account_body(available=None))
    k._risk_step()
    rec = json.loads(k.journal.kv_get(E.MARGIN_KV))
    assert (rec["status"], rec["reason"], rec["value"]) == \
        ("UNAVAILABLE", "NOT_PRESENT_IN_EXISTING_AUTHORIZED_ACCOUNT_EVIDENCE", None)
    assert len(calls) == 1
    # and no response at all is its own reason, not a zero
    assert _margin(None)["reason"] == "NO_AUTHORIZED_ACCOUNT_RESPONSE_THIS_CYCLE"
    for bad, why in ((b"not json", "ACCOUNT_RESPONSE_MALFORMED"),
                     (_account_body(available="NaN"), "AVAILABLE_BALANCE_MALFORMED"),
                     (_account_body(available=True), "AVAILABLE_BALANCE_MALFORMED")):
        assert _margin(bad)["reason"] == why and _margin(bad)["value"] is None
    assert _margin(_account_body(), url="https://evil.example/fapi/v3/account")[
        "reason"] == "ACCOUNT_REQUEST_URL_UNRECOGNISED"


def test_no_additional_authenticated_request_or_credential_in_evidence_code():
    for mod in ("trader/engine/evidence_capture.py", "trader/engine/execution_evidence.py",
                "trader/observability/depth_evidence.py", "scripts/capacity_depth_shadow.py"):
        code = (ROOT / mod).read_text().lower()
        for bad in ("hmac", "binance_keys", "x-mbx-apikey", "signature", "import requests",
                    "requests.get", "import ccxt", "create_order", "leveragebracket(",
                    "/fapi/v1/leveragebracket", "/fapi/v3/account?"):
            assert bad not in code, (mod, bad)
    kernel = (ROOT / "trader/kernel.py").read_text()
    # the account endpoint is requested exactly where it already was
    assert kernel.count("requests.get(") == 2          # equity read + account snapshot


def test_evidence_failure_never_changes_the_risk_step(tmp_path, monkeypatch):
    k, _ = _kernel(tmp_path, monkeypatch, _account_body())
    monkeypatch.setattr(E, "record_margin", lambda *a: (_ for _ in ()).throw(OSError("x")))
    balance, status = k._risk_step()
    assert balance == 10000.0 and status == {"risk_state": "ok"}


# ── 2. venue filters: exactly what exchangeInfo publishes ────────────────
def test_public_venue_filters_extracted_and_absent_stays_none():
    rec = _registry().records[0]
    c = rec.constraints
    assert (c.minimum_quantity, c.maximum_quantity, c.quantity_step, c.minimum_notional,
            c.price_tick) == ("0.001", "1000", "0.001", "100", "0.1")
    assert (c.market_minimum_quantity, c.market_maximum_quantity,
            c.market_quantity_step) == ("0.001", "120", "0.001")
    assert (rec.venue_status, rec.contract_type) == ("TRADING", "PERPETUAL")
    bare = from_binance_usdm_responses(exchange_info={"symbols": [{
        "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
        "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}]}]},
        as_of_ms=1, account_scope="unauthenticated-public-metadata").records[0].constraints
    assert bare.maximum_quantity is None and bare.market_maximum_quantity is None
    out = C._venue({"record": _plain_record(bare), "account_trading": "UNKNOWN"}, 100.0)
    assert (out["maximums"]["status"], out["maximums"]["reason"]) == \
        ("UNAVAILABLE", "VENUE_MAXIMUM_NOT_CAPTURED")
    assert "max_quantity" not in out["maximums"]


def _plain_record(constraints):
    from dataclasses import asdict
    return {"venue_status": "TRADING", "venue_listing": "PRESENT",
            "constraints": asdict(constraints), "leverage_bracket": "UNKNOWN",
            "account_eligibility": "UNKNOWN"}


# ── 3/4. leverage brackets and eligibility are not invented ──────────────
def test_leverage_bracket_stays_unavailable_and_eligibility_unknown(world, cfg):
    j, v = world
    _publish(j, cfg)
    d = _dims(C.compute(_inputs(j, cfg, v)))
    assert d["venue.leverage"]["status"] == "UNAVAILABLE"
    assert d["venue.leverage"]["reason"] == "VENUE_LEVERAGE_BRACKET_NOT_CAPTURED"
    assert "notionalCap" in d["venue.leverage"]["detail"]
    # a registry that saw the symbol in a bracket population is presence, not a bracket
    rec = _plain_record(_registry().records[0].constraints)
    rec["leverage_bracket"] = "PRESENT"
    assert C._venue({"record": rec, "account_trading": "ENABLED"}, 1.0)["leverage"][
        "status"] == "UNAVAILABLE"
    assert all(r.account_eligibility is Eligibility.UNKNOWN for r in _registry().records)
    assert d["venue.account_eligibility"]["reason"] == "ACCOUNT_SYMBOL_ELIGIBILITY_UNKNOWN"
    # listing, venue status TRADING and account-global ENABLED never make it ELIGIBLE
    assert C._venue({"record": rec, "account_trading": "ENABLED"}, 1.0)[
        "account_eligibility"]["status"] == "UNAVAILABLE"


# ── 5. venue position snapshot: durable, immutable, replayable ──────────
def _rows(held):
    return [{"info": {"symbol": s.replace("/", ""), "entryPrice": px},
             "symbol": f"{s}:USDT", "contracts": q, "side": side}
            for s, side, q, px in held]


def _obs(rows, as_of=NOW - 10_000):
    return observe_positions(rows, exchange_id="binanceusdm", market_type=MarketType.FUTURES,
                             environment="demo", source_ref="https://demo-fapi.binance.com",
                             request_start_ms=as_of - 50, response_received_ms=as_of)


def test_venue_position_snapshot_persists_and_replays(world, cfg):
    j, v = world
    _open(j, "e1", "ETH/USDT", "long", 2.0, 3_000.0, 2_900.0)
    _publish(j, cfg)
    rows = _rows([("ETH/USDT", "long", 2.0, "3001.25")])
    snap = E.position_snapshot(_obs(rows), rows)
    assert snap["positions"] == [{"instrument_id": "binance_usdm:futures:ETHUSDT",
                                  "symbol": "ETHUSDT", "side": "long", "quantity": 2.0,
                                  "entry_price_text": "3001.25",
                                  "entry_price_basis": snap["positions"][0][
                                      "entry_price_basis"]}]
    assert snap["completeness"] == "COMPLETE" and snap["venue"] == "binance_usdm"
    assert snap["observed_at_ms"] == snap["received_at_ms"] == NOW - 10_000
    assert snap["source_identity"]["observation_id"] == snap["observation"]["observation_id"]
    assert E.record_snapshot(j, snap, at_ms=NOW) == "inserted"
    # the same venue book re-observed later is kept once; a changed book appends
    later = E.position_snapshot(_obs(rows, NOW - 5_000), rows)
    assert E.record_snapshot(j, later, at_ms=NOW) == "unchanged"
    assert [s["snapshot_id"] for s in E.snapshot_history(j)] == [snap["snapshot_id"]]
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        with j._tx() as c:
            c.execute(f"DELETE FROM {E.SNAPSHOT_TABLE}")
    # capacity reads the persisted latest snapshot when none is supplied
    inputs = _inputs(j, cfg, v, positions=None)
    assert inputs["venue_positions"]["snapshot_id"] == later["snapshot_id"]
    port = _dims(C.compute(inputs))["portfolio.venue_confirmed_book"]
    assert port["status"] == "ESTABLISHED"
    assert port["venue_observation_id"] == later["observation"]["observation_id"]
    receipt = C.build(inputs)
    assert C.verify(json.loads(json.dumps(receipt))) is not None      # replayable


def test_journal_is_not_a_venue_snapshot(world, cfg):
    j, v = world
    _open(j, "e1", "ETH/USDT", "long", 2.0, 3_000.0, 2_900.0)
    _publish(j, cfg)
    # no venue snapshot: the journal's open trade is not evidence of the venue
    port = _dims(C.compute(_inputs(j, cfg, v, positions=None)))[
        "portfolio.venue_confirmed_book"]
    assert (port["status"], port["reason"]) == ("UNAVAILABLE", "VENUE_POSITIONS_NOT_OBSERVED")
    # a venue snapshot without the position disagrees with the journal
    E.record_snapshot(j, E.position_snapshot(_obs([]), []), at_ms=NOW)
    port = _dims(C.compute(_inputs(j, cfg, v, positions=None)))[
        "portfolio.venue_confirmed_book"]
    assert port["reason"] == "JOURNAL_VENUE_BOOK_DISAGREES"
    # a tampered persisted snapshot is not an observation
    snap = json.loads(j.kv_get(E.SNAPSHOT_KV))
    snap["positions"] = [{"instrument_id": "x"}]
    j.kv_set(E.SNAPSHOT_KV, json.dumps(snap))
    assert _inputs(j, cfg, v, positions=None)["venue_positions"] is None
    assert "journal" in E.position_snapshot(_obs([]), [])["basis"]


def test_kernel_persists_the_reconciliation_observation_once(tmp_path, monkeypatch):
    import trader.kernel as K
    k = object.__new__(K.Kernel)
    k.journal = Journal(tmp_path / "k.db")
    rows = _rows([("BTC/USDT", "short", 0.5, "60000")])
    k.position_observation, k._position_rows = _obs(rows), rows
    k._record_capacity_evidence()
    k._record_capacity_evidence()                       # same observation: no rewrite
    hist = E.snapshot_history(k.journal)
    assert len(hist) == 1 and hist[0]["positions"][0]["side"] == "short"
    assert E.verify_snapshot(json.loads(k.journal.kv_get(E.SNAPSHOT_KV))) is not None


# ── 6. public order-book depth ───────────────────────────────────────────
PROD = NS(base_url="https://fapi.binance.com", environment="production")


def _depth(e=NOW, bids=None, asks=None, **kw):
    body = {"lastUpdateId": 7, "E": e, "T": e - 3,
            "bids": [["100.1", "2"], ["100.0", "5"]] if bids is None else bids,
            "asks": [["100.2", "1"], ["100.3", "4"]] if asks is None else asks, **kw}
    return json.dumps(body).encode()


def _observe(body, limit=5, received=NOW + 40):
    return D.observe(body, target=PROD, symbol="BTCUSDT", limit=limit,
                     request_start_ms=received - 30, received_ms=received, max_age_ms=5_000)


def test_bounded_public_depth_collection():
    import scripts.capacity_depth_shadow as S
    seen = []

    def fetch(url, *, timeout_s, max_bytes):
        seen.append(url)
        return NS(status=200, body=_depth(e=S.now_ms()))
    with sqlite3.connect(":memory:") as db:
        db.executescript(D.DDL)
        assert S.collect_one(db, PROD, "BTCUSDT", 5, 5_000, fetch=fetch) == "inserted"
        row = db.execute("SELECT symbol, configured_depth, source_endpoint, bids_json, "
                         "asks_json, response_sha256 FROM depth_observations").fetchone()
    assert seen == ["https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=5"]
    assert row[:3] == ("BTCUSDT", 5, seen[0])
    assert json.loads(row[3]) == [["100.1", "2"], ["100.0", "5"]]   # exact venue strings
    assert len(row[5]) == 64
    with pytest.raises(ValueError):
        D.request_url(PROD, "BTCUSDT", 7)                         # not a venue limit
    with pytest.raises(ValueError):
        D.request_url(PROD, "BTC&x=1", 5)


def test_runner_is_bounded_and_writes_only_its_shadow_dir(tmp_path, monkeypatch):
    import scripts.capacity_depth_shadow as S
    monkeypatch.setattr(S, "urllib_fetch", lambda url, *, timeout_s, max_bytes: NS(
        status=200, body=_depth(e=S.now_ms())))
    syms = [f"S{i}USDT" for i in range(40)]
    assert S.main(["--shadow-dir", str(tmp_path / "sh"), "--symbols", *syms,
                   "--max-symbols", "3", "--depth", "5", "--interval-s", "10",
                   "--max-cycles", "1"]) == 0
    man = json.loads((tmp_path / "sh" / "manifest.json").read_text())
    assert man["symbols"] == syms[:3] and man["credentials"] == "none"
    assert man["endpoint"] == "https://fapi.binance.com/fapi/v1/depth"
    with sqlite3.connect(tmp_path / "sh" / "depth.db") as db:
        assert db.execute("SELECT COUNT(*) FROM depth_observations").fetchone()[0] == 3
    st = json.loads((tmp_path / "sh" / "storage.json").read_text())
    assert st["snapshots"] == 3 and st["cycles"] == 1
    assert "stopped" in (tmp_path / "sh" / "collector_log.jsonl").read_text()
    with pytest.raises(SystemExit):
        S.main(["--shadow-dir", str(tmp_path / "x"), "--symbols", "A", "--interval-s", "1"])


@pytest.mark.parametrize("body,reason", [
    (b"<html>", "malformed_json"),
    (b"[]", "malformed_depth"),
    (_depth(e=NOW - 60_000), "stale_or_clock_skewed"),
    (_depth(asks=[["100.0", "1"]]), "crossed_book"),
    (_depth(bids=[["100.0", "1"], ["100.1", "1"]]), "levels_not_strictly_ordered"),
    (_depth(bids=[["100.1", "0"]]), "nonpositive_level"),
    (_depth(bids=[["x", "1"]]), "malformed_level"),
    (_depth(bids=[[100.1, 1]]), "malformed_level"),
    (_depth(asks=[]), "empty_side"),
    (_depth(bids=[[str(100 - i / 10), "1"] for i in range(6)]), "more_levels_than_requested"),
    (json.dumps({"bids": [], "asks": []}).encode(), "missing_update_id_or_venue_times"),
])
def test_malformed_or_stale_depth_is_rejected(body, reason):
    with pytest.raises(D.DepthRejected) as e:
        _observe(body)
    assert e.value.reason == reason


def test_rejection_is_telemetry_not_depth(tmp_path):
    import scripts.capacity_depth_shadow as S
    with sqlite3.connect(":memory:") as db:
        db.executescript(D.DDL)
        r = S.collect_one(db, PROD, "BTCUSDT", 5, 5_000, fetch=lambda url, **kw: NS(
            status=200, body=_depth(e=1_000)))
        assert r == "stale_or_clock_skewed"
        assert db.execute("SELECT COUNT(*) FROM depth_observations").fetchone()[0] == 0
        assert db.execute("SELECT reason FROM depth_rejections").fetchone()[0] == r

        def boom(url, **kw):
            raise S.FetchError("network_error")
        assert S.collect_one(db, PROD, "BTCUSDT", 5, 5_000, fetch=boom) == "fetch:network_error"


# ── 7. execution / slippage evidence ─────────────────────────────────────
def test_fill_and_slippage_reconstructed_per_order(env):
    ex, j, e = env
    ex.split = 2
    pos = open_trade(env)
    ex.px = 104.0
    assert e.close(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0], 105.0)
    m, x = X.executed_orders(j.query)
    assert m["trade_id"] == pos.id and m["purpose"] == "entry" and m["side"] == "buy"
    assert m["instrument_id"] == "binance_usdm:futures:BTCUSDT"
    assert m["strategy"]["status"] == "VERIFIED" and m["strategy"]["spec_sha256"]
    assert m["requested_qty"] == 2.0 and m["fill_qty"] == 2.0 and len(m["fills"]) == 2
    assert m["fill_coverage"] == "COMPLETE" and m["vwap"] == 100.0
    assert m["commissions_by_asset"] == {"USDT": "0.08"}     # per asset, never in price
    s = m["slippage"]
    assert s["status"] == "MEASURED" and s["reference_price"] == REF["price"]
    assert s["adverse_bps"] == pytest.approx((100.0 - 99.5) / 99.5 * 1e4)
    assert m["first_fill_ms"] and m["submitted_ms"]
    assert X.executed_orders(j.query) == [m, x]               # deterministic
    assert len(m["measurement_id"]) == 64
    # the exit: a sell measured against the hint its caller recorded
    assert (x["purpose"], x["side"], x["vwap"]) == ("final_exit", "sell", 104.0)
    assert x["reference"]["basis"] == "exit_price_hint"
    assert x["slippage"]["adverse_bps"] == pytest.approx((105.0 - 104.0) / 105.0 * 1e4)


def test_missing_reference_price_makes_slippage_unavailable(env):
    ex, j, e = env
    pos = open_trade(env, reference=None)
    assert e.close(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0], 100.0)
    m = X.executed_orders(j.query)[0]
    assert m["reference"]["status"] == "UNAVAILABLE" and m["reference"]["price"] is None
    assert m["slippage"] == {"status": "UNAVAILABLE", "reason": "NO_REFERENCE_PRICE"}
    assert m["vwap"] == 100.0                                 # fills still measured
    # a sell against a recorded reference is side-signed
    leg = {"id": 1, "trade_id": "t", "purpose": "final_exit", "market_type": "futures",
           "symbol": "BTC/USDT", "venue_order_id": "9", "side": "sell",
           "booked_qty": 1.0, "reference_json": json.dumps({"price": 101.0,
                                                           "basis": "exit_price_hint"})}
    out = X.measure(leg, [{"qty": 1.0, "price": 100.0, "commission": None,
                           "commission_asset": None}], None)
    assert out["slippage"]["adverse_bps"] == pytest.approx(1e4 / 101.0)
    assert out["commission_status"] == "PARTIAL" and out["strategy"]["status"] == "UNKNOWN"


# ── 8. storage growth: measured, not a policy ────────────────────────────
def test_storage_growth_is_measured(tmp_path):
    db = D.connect(tmp_path / "d.db")
    for i in range(10):
        D.store(db, _observe(_depth(e=NOW + i * 60_000), received=NOW + i * 60_000 + 40))
    first, last = db.execute("SELECT MIN(received_ms), MAX(received_ms) FROM "
                             "depth_observations").fetchone()
    size = (tmp_path / "d.db").stat().st_size
    st = D.storage(db, size, first, last)
    assert st["snapshots"] == 10 and st["bytes_per_snapshot_on_disk"] == size / 10
    assert st["snapshots_per_hour"] == pytest.approx(10 / 0.15)
    assert st["projected_30_day_bytes"] == pytest.approx(size / 10 * 10 / 0.15 * 24 * 30)
    assert "not a retention policy" in st["basis"]


# ── 9. capacity consumes the sources; liquidity is never invented ───────
def test_capacity_consumes_evidence_without_inventing_liquidity(world, cfg):
    j, v = world
    _publish(j, cfg)
    E.record_margin(j, _margin(_account_body()))
    E.record_snapshot(j, E.position_snapshot(_obs([]), []), at_ms=NOW)
    res = C.compute(_inputs(j, cfg, v, positions=None))
    d = _dims(res)
    f = d["account.funding"]
    assert f["status"] == "ESTABLISHED" and f["available_margin_usdt"] == 7250.5
    assert "max_quantity" not in f                     # no leverage -> quantity guess
    assert res["groups"]["account"] == "ESTABLISHED"
    assert res["groups"]["portfolio"] == "ESTABLISHED"
    assert d["venue.maximums"]["status"] == "ESTABLISHED"
    assert d["liquidity.liquidity_capacity_model"]["status"] == "UNAVAILABLE"
    assert res["effective"]["status"] == "UNAVAILABLE" and "max_quantity" not in res["effective"]
    assert sorted(res["effective"]["missing"]) == [
        "liquidity.liquidity_capacity_model:NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL",
        "venue.account_eligibility:ACCOUNT_SYMBOL_ELIGIBILITY_UNKNOWN",
        "venue.leverage:VENUE_LEVERAGE_BRACKET_NOT_CAPTURED"]
    # depth observations are not a liquidity model either
    inp = _inputs(j, cfg, v, positions=None, liquidity_evidence=[{"depth": "raw"}])
    assert _dims(C.compute(inp))["liquidity.liquidity_capacity_model"][
        "supplied_evidence_ignored"] == 1
    assert C.REGISTERED_LIQUIDITY_MODELS == {}


@pytest.mark.parametrize("change,reason", [
    (dict(body=_account_body(available=None)),
     "NOT_PRESENT_IN_EXISTING_AUTHORIZED_ACCOUNT_EVIDENCE"),
    (dict(received=NOW - 600_000), "AVAILABLE_MARGIN_STALE"),
    (dict(body=_account_body(equity="9999.0")), "AVAILABLE_MARGIN_NOT_FROM_THE_EQUITY_READ"),
])
def test_untruthful_margin_is_not_established(world, cfg, change, reason):
    j, v = world
    _publish(j, cfg)
    E.record_margin(j, _margin(change.get("body", _account_body()),
                               received=change.get("received", NOW - 30_000)))
    f = _dims(C.compute(_inputs(j, cfg, v)))["account.funding"]
    assert (f["status"], f["reason"]) == ("UNAVAILABLE", reason)


def test_tampered_margin_record_is_refused(world, cfg):
    j, v = world
    _publish(j, cfg)
    rec = _margin(_account_body())
    rec["value"] = 1e9
    j.kv_set(E.MARGIN_KV, json.dumps(rec))
    f = _dims(C.compute(_inputs(j, cfg, v)))["account.funding"]
    assert f["reason"] == "AVAILABLE_MARGIN_OBSERVATION_CORRUPT"


def test_new_evidence_supersedes_an_old_receipt(world, cfg):
    j, v = world
    _publish(j, cfg)
    E.record_margin(j, _margin(_account_body()))
    rec = C.build(_inputs(j, cfg, v))
    C.record(j, rec, at_ms=NOW)
    E.record_margin(j, _margin(_account_body(available="1.0")))
    out = C.check_current(j, cfg, v, rec["receipt_id"], now_ms=NOW + 1)
    assert "capacity_inputs_superseded:account_margin_observation" in out["reasons"]


# ── 10. no trading behavior change ───────────────────────────────────────
def test_no_trading_behavior_change():
    for mod in ("trader/engine/evidence_capture.py", "trader/engine/execution_evidence.py",
                "trader/observability/depth_evidence.py"):
        code = (ROOT / mod).read_text()
        for bad in ("create_order", "cancel_order", "state_machine", "set_control",
                    "RiskManager", "update_equity", "notifier", "leverage=",
                    "kv_set(\"risk", "control_events"):
            assert bad not in code, (mod, bad)
    for f in (ROOT / "trader").rglob("*.py"):
        t = f.read_text()
        if f.name not in ("capacity_depth_shadow.py",):
            assert "depth_evidence" not in t or f.name in {"depth_evidence.py", "execution_shadow.py", "execution_calibration.py"}, f
            assert "execution_evidence" not in t or f.name in {"execution_evidence.py", "execution_calibration.py"}, f
    kernel = (ROOT / "trader/kernel.py").read_text()
    assert kernel.count("evidence_capture") == 1 and "_record_capacity_evidence()" in kernel
    from trader.core.config import load_config
    c = load_config()
    assert c["research"]["referee"] is False and c["research"]["handoff"] is False
