"""Stage-5 strategy capacity receipt (strategy-capacity-receipt.v1).

Every test runs on a temporary journal built through the real factory path
(referee candidate -> version -> exact paper install -> probation -> owner
approval). The account / Risk records are written in the exact shape the
Kernel publishes them (`_record_account_observation`,
`_record_risk_assessment`) and read back through `dashboard.current_truth`.
"""
import copy
import json
import math
import pathlib
import re
from datetime import datetime, timezone

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType, Position, Side
from trader.data.binance_usdm_registry import from_binance_usdm_responses
from trader.engine.risk import RiskManager, policy_from_config
from trader.observability.portfolio_observation import observe_positions
from trader.strategy import capacity as C
from trader.strategy import factory_handoff as F
from tests.test_strategy_factory_handoff import (  # noqa: F401
    INPUTS, _approved, _dump, _journal, cfg)

H4 = 14_400_000
NOW = 1_794_000_000_000 - (1_794_000_000_000 % H4) + 3_600_000  # 1h into a bar
BTC = "binance_usdm:futures:BTCUSDT"


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


# ── fixtures in the Kernel's own record shapes ───────────────────────────
def _account_obs(equity, observed_ms):
    return {"schema": 2, "currency": "USDT", "attempted_at": _iso(observed_ms - 200),
            "recorded_at": _iso(observed_ms + 100), "risk_input": equity,
            "authoritative": True, "attempt_errors": [],
            "source": "kernel _risk_step (Risk's equity input this cycle)",
            "status": "FRESH", "value": equity,
            "basis": "venue_total_margin_balance",
            "observed_at": _iso(observed_ms), "successful_read_at": _iso(observed_ms),
            "successful_value": equity, "successful_basis": "venue_total_margin_balance",
            "fallback": None, "consecutive_failures": 0,
            "completion_relation": "ok", "reason": None}


def _risk_assessment(cfg, equity, observed_ms, *, peak=None, day_start=None,
                     status="PASS", blocking=(), policy=None):
    pol = policy or policy_from_config(cfg)
    peak = peak or equity
    day_start = day_start or equity
    dd = round(max(0.0, (peak - equity) / peak) * 100, 2)
    day = round((equity - day_start) / day_start * 100, 2)
    cons = []
    for name in ("risk_baseline", "halt_drawdown", "daily_loss_breaker",
                 "max_open_positions", "portfolio_heat", "total_margin"):
        res = "block" if name in blocking else "pass"
        cons.append({"name": name, "limit": 1.0 if name != "risk_baseline" else "ok",
                     "observed": 0.0 if name != "risk_baseline" else "ok",
                     "result": res, "unit": "x", "basis": "fixture"})
    for name in ("per_symbol_risk_cap", "per_position_margin"):
        cons.append({"name": name, "limit": 1.0, "observed": None,
                     "result": "applies_at_entry", "unit": "pct", "basis": "fixture"})
    return {"schema": 2, "assessed_at": _iso(observed_ms + 500), "status": status,
            "reasons": [f"{b}_block" for b in blocking], "constraints": cons,
            "risk_state": "ok", "drawdown_pct": dd, "daily_pnl_pct": day,
            "halt_breached": False,
            "baseline": {"peak_equity": peak, "day_start_equity": day_start,
                         "day_key": _iso(observed_ms)[:10]},
            "equity": {"value": equity, "status": "FRESH",
                       "basis": "venue_total_margin_balance",
                       "observed_at": _iso(observed_ms), "age_at_assessment_s": 0.5,
                       "fresh_at_assessment": True, "authoritative": True},
            "book": {"source": "journal open trades", "read_at": _iso(observed_ms),
                     "positions": 0, "malformed": []},
            "policy": {"digest": pol["digest"], "risk_manager_identity": "x",
                       "effective": pol["effective"], "limits": pol["limits"]},
            "control": {"state": "ACTIVE", "entries_permitted_by_control": True,
                        "applicability": "entries"},
            "entry_gate": {"allowed": True, "blocked_reason": None, "basis": "x"},
            "source": "fixture"}


def _publish(j, cfg, equity=10_000.0, observed_ms=NOW - 30_000, *, account=True,
             **kw):
    if account:
        j.kv_set("account_observation",
                 json.dumps(_account_obs(equity, observed_ms)))
    ra = _risk_assessment(cfg, equity, observed_ms, **kw)
    j.kv_set("risk_assessment", json.dumps(ra))
    j.kv_set("risk_state", json.dumps(ra["baseline"]))


def _open(j, tid, symbol, side, amount, entry, stop, lev=5):
    with j._tx() as c:
        c.execute("INSERT INTO trades (id, symbol, side, amount, entry_price, "
                  "notional_usdt, leverage, stop_loss, strategy_id, market_type, "
                  "exec_mode, opened_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,"
                  "'open')", (tid, symbol, side, amount, entry, amount * entry, lev,
                              stop, "other", "futures", "live", _iso(NOW - H4)))


def _positions(rows, as_of_ms=NOW - 10_000):
    return observe_positions(
        [{"info": {"symbol": s.replace("/", "")}, "symbol": f"{s}:USDT",
          "contracts": q, "side": side} for s, side, q in rows],
        exchange_id="binanceusdm", market_type=MarketType.FUTURES,
        environment="demo", source_ref="https://demo-fapi.binance.com",
        request_start_ms=as_of_ms - 50, response_received_ms=as_of_ms)


def _registry(as_of_ms=NOW - 60_000, status="TRADING"):
    info = {"symbols": [{
        "symbol": "BTCUSDT", "status": status, "baseAsset": "BTC",
        "quoteAsset": "USDT", "marginAsset": "USDT", "contractType": "PERPETUAL",
        "pricePrecision": 1, "quantityPrecision": 3,
        "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001",
                     "minQty": "0.001", "maxQty": "1000"},
                    {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001",
                     "minQty": "0.001", "maxQty": "120"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"}]}]}
    return from_binance_usdm_responses(
        exchange_info=info, as_of_ms=as_of_ms,
        account_scope="unauthenticated-public-metadata")


def _market(n=30, now=NOW, price=60_000.0, rng=600.0):
    last = now - (now % H4) - H4                 # the last fully closed bar
    bars = []
    for i in range(n):
        ts = last - (n - 1 - i) * H4
        c = price + (i % 5) * 10.0
        bars.append({"ts": ts, "high": c + rng / 2, "low": c - rng / 2, "close": c})
    return {"timeframe": "4h", "source": "candles.db ohlcv (fixture)", "bars": bars}


@pytest.fixture
def world(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    return j, F.load_version(j, v["version_id"])


def _inputs(j, cfg, version, **kw):
    args = dict(instrument_id=BTC, market_type="futures", as_of_ms=NOW,
                registry=_registry(), positions=_positions([]), market=_market())
    args.update(kw)
    return C.gather(j, cfg, version, **args)


def _dims(res):
    return {f"{g}.{k}": d for g, s in res["dimensions"].items()
            for k, d in s.items()}


# ── 5. risk-geometry bound = RiskManager.check_entry, exactly ────────────
def test_risk_bound_is_the_existing_check_entry_rule(world, cfg, tmp_path):
    j, v = world
    _open(j, "e1", "ETH/USDT", "long", 2.0, 3_000.0, 2_900.0)
    peak, eq = 11_000.0, 10_000.0                   # 9.09% dd -> derisk 0.5
    _publish(j, cfg, eq, peak=peak)
    res = C.compute(_inputs(j, cfg, v, positions=_positions([("ETH/USDT", "long", 2.0)])))
    rb = _dims(res)["risk.risk_rule"]
    assert rb["status"] == "ESTABLISHED" and rb["reason"] is None
    assert rb["rule"] == "engine.risk.RiskManager.check_entry"
    assert rb["policy_digest"] == policy_from_config(cfg)["digest"]

    # the real RiskManager on its own journal, same baseline, same book
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    j2 = Journal(tmp_path / "risk.db")
    j2.kv_set("risk_state", json.dumps({"peak_equity": peak, "day_start_equity": eq,
                                        "day_key": today}))
    rm = RiskManager(cfg, j2)
    st = _dims(res)["strategy.stop_geometry"]
    s = rm.check_entry(ControlState.ACTIVE, "BTC/USDT", st["price"], st["atr"],
                       st["stop_frac"],
                       open_positions=[Position(id="e1", symbol="ETH/USDT",
                                                side=Side.LONG, amount=2.0,
                                                entry_price=3_000.0,
                                                notional_usdt=6_000.0, leverage=5,
                                                stop_loss=2_900.0)],
                       equity=eq, closed_trades_count=rb["closed_trades_count"],
                       market_type="futures")
    assert s.ok
    assert (rb["max_quantity"], rb["max_margin_usdt"], rb["risk_usdt"],
            rb["size_mult"]) == (s.amount, s.size_usdt, s.risk_usdt, s.size_mult)
    assert s.size_mult == 0.25          # derisk 0.5 x proving 0.5 (15 closed < 30)

    # the stop is the admitted fixed version's own: 3.0 x ATR(14) on its declared 4h bars
    from trader.agents.indicators import atr
    full = C._bars_frame(_market()["bars"])
    assert st["geometry"] == {"timeframe": "4h", "stop_atr_mult": 3.0, "stop_pct": 0.0}
    assert math.isclose(st["atr"], atr(full)) and st["stop_distance"] == max(
        3.0 * st["atr"], st["price"] * 0.004)


def test_current_exposure_reduces_available_capacity(world, cfg):
    j, v = world
    _publish(j, cfg)
    flat = _dims(C.compute(_inputs(j, cfg, v)))["risk.risk_rule"]
    # $34,500 open at 5x = $6,900 margin of the $7,000 (70%) total cap
    for i, s in enumerate(("ETH/USDT", "SOL/USDT", "XRP/USDT")):
        _open(j, f"o{i}", s, "long", 46.0, 250.0, 240.0)
    held = _positions([(s, "long", 46.0) for s in ("ETH/USDT", "SOL/USDT", "XRP/USDT")])
    busy = _dims(C.compute(_inputs(j, cfg, v, positions=held)))["risk.risk_rule"]
    assert busy["open_margin_usdt"] > 0 and busy["open_risk_usdt"] > 0
    assert 0 < busy["max_quantity"] < flat["max_quantity"]
    assert math.isclose(busy["max_margin_usdt"], 100.0)        # the room left

    # already exposed on the instrument: Risk's own refusal, capacity 0
    _open(j, "b1", "BTC/USDT", "long", 0.01, 60_000.0, 59_000.0)
    held = _positions([(s, "long", 46.0) for s in ("ETH/USDT", "SOL/USDT", "XRP/USDT")]
                      + [("BTC/USDT", "long", 0.01)])
    rb = _dims(C.compute(_inputs(j, cfg, v, positions=held)))["risk.risk_rule"]
    assert (rb["status"], rb["max_quantity"]) == ("ESTABLISHED", 0.0)
    # Production's existing SizingResult predates structured reason codes.
    assert rb['reason'] in ('risk_already_exposed', 'risk_refused')


def test_risk_block_is_zero_not_unknown(world, cfg):
    j, v = world
    _publish(j, cfg, status="BLOCK", blocking=("daily_loss_breaker",))
    rb = _dims(C.compute(_inputs(j, cfg, v)))["risk.risk_rule"]
    assert (rb["status"], rb["reason"], rb["max_notional_usdt"]) == (
        "ESTABLISHED", "RISK_ASSESSMENT_BLOCK", 0.0)
    assert rb["blocking_constraints"] == ["daily_loss_breaker"]


# ── 6. current portfolio: unknown stays unknown ──────────────────────────
def test_portfolio_unconfirmed_by_venue_is_unavailable(world, cfg):
    j, v = world
    _publish(j, cfg)
    _open(j, "e1", "ETH/USDT", "long", 2.0, 3_000.0, 2_900.0)
    cases = {
        "VENUE_POSITIONS_NOT_OBSERVED": None,
        "JOURNAL_VENUE_BOOK_DISAGREES": _positions([]),            # venue flat
        "VENUE_POSITIONS_STALE": _positions([("ETH/USDT", "long", 2.0)],
                                            as_of_ms=NOW - 10 * 60_000),
    }
    for why, obs in cases.items():
        res = C.compute(_inputs(j, cfg, v, positions=obs))
        d = _dims(res)
        assert d["portfolio.venue_confirmed_book"]["reason"] == why
        assert d["risk.risk_rule"]["status"] == "UNAVAILABLE"
    size = _dims(C.compute(_inputs(j, cfg, v, positions=_positions(
        [("ETH/USDT", "long", 1.5)]))))                               # >1% apart
    assert size["portfolio.venue_confirmed_book"]["reason"] == \
        "JOURNAL_VENUE_BOOK_DISAGREES"


# ── account evidence ──────────────────────────────────────────────────────
def test_missing_equity_is_unavailable(world, cfg):
    j, v = world
    _publish(j, cfg, account=False)
    d = _dims(C.compute(_inputs(j, cfg, v)))
    assert d["account.equity"]["status"] == "UNAVAILABLE"
    assert d["account.equity"]["reason"] == "ACCOUNT_EQUITY_MISSING"
    assert d["risk.risk_rule"]["status"] == "UNAVAILABLE"
    assert "max_quantity" not in d["risk.risk_rule"]


def test_stale_account_evidence_is_unavailable(world, cfg):
    j, v = world
    _publish(j, cfg, observed_ms=NOW - 10 * 60_000)      # 600 s > 300 s bound
    d = _dims(C.compute(_inputs(j, cfg, v)))
    assert d["account.equity"]["reason"] == "ACCOUNT_EQUITY_STALE"
    assert d["risk.risk_rule"]["status"] == "UNAVAILABLE"


def test_account_funding_is_not_equity(world, cfg):
    j, v = world
    _publish(j, cfg)
    d = _dims(C.compute(_inputs(j, cfg, v)))
    assert d["account.equity"]["status"] == "ESTABLISHED"
    assert d["account.funding"]["reason"] == "NO_RECORDED_AVAILABLE_MARGIN_OBSERVATION"
    assert "max_quantity" not in d["account.funding"]


def test_running_policy_must_be_the_configured_one(world, cfg):
    j, v = world
    other = copy.deepcopy(cfg)
    other["risk"]["risk_per_trade_pct"] = 0.75
    _publish(j, cfg, policy=policy_from_config(other))
    res = C.compute(_inputs(j, cfg, v))
    assert res["risk_assessment"]["why"] == "RISK_POLICY_NOT_PROVEN_RUNNING"
    assert _dims(res)["risk.risk_rule"]["status"] == "UNAVAILABLE"


# ── venue: minimums yes, maxima never guessed ────────────────────────────
def test_venue_bound_and_venue_max_is_not_capacity(world, cfg):
    j, v = world
    _publish(j, cfg)
    res = C.compute(_inputs(j, cfg, v))
    d = _dims(res)
    m = d["venue.minimums"]
    assert (m["minimum_quantity"], m["quantity_step"], m["minimum_notional_usdt"]) \
        == ("0.001", "0.001", "100")
    assert m["minimum_order_notional_at_price"] == max(
        100.0, 0.001 * d["strategy.stop_geometry"]["price"])
    assert d["venue.instrument_status"]["venue_allows_order"] is True
    # exchangeInfo carried LOT_SIZE maxQty 1000 / MARKET_LOT_SIZE maxQty 120:
    # Luffy sends market orders, so 120 is the venue's per-order cap — a
    # venue limit, never a liquidity capacity; no max notional is published
    mx = d["venue.maximums"]
    assert mx["status"] == "ESTABLISHED" and mx["max_quantity"] == 120.0
    assert (mx["market_order_maximum_quantity"], mx["limit_order_maximum_quantity"]) \
        == ("120", "1000")
    assert mx["maximum_notional"] == "NOT_PUBLISHED_IN_EXCHANGEINFO"
    assert d["venue.leverage"]["status"] == "UNAVAILABLE"
    assert d["venue.account_eligibility"]["reason"] == "ACCOUNT_SYMBOL_ELIGIBILITY_UNKNOWN"
    assert res["effective"]["status"] == "UNAVAILABLE"
    assert "max_quantity" not in res["effective"]
    assert not any(m.startswith("venue.maximums") for m in res["effective"]["missing"])
    assert "liquidity.liquidity_capacity_model:NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL" \
        in res["effective"]["missing"]

    halted = _dims(C.compute(_inputs(j, cfg, v, registry=_registry(status="SETTLING"))))
    assert halted["venue.instrument_status"]["venue_allows_order"] is False


# ── strategy geometry ─────────────────────────────────────────────────────
def test_missing_or_invalid_stop_geometry(world, cfg):
    j, v = world
    _publish(j, cfg)
    nostop = copy.deepcopy(v)
    nostop["spec"]["exit"]["stop"] = {"kind": "none"}
    d = _dims(C.compute(_inputs(j, cfg, nostop)))
    assert d["strategy.stop_geometry"]["reason"] == "SPEC_DECLARES_NO_STOP_GEOMETRY"
    assert d["risk.risk_rule"]["status"] == "UNAVAILABLE"
    cases = {
        "MARKET_BARS_NOT_SUPPLIED": None,
        "MARKET_BARS_INSUFFICIENT": _market(n=10),
        "MARKET_BARS_WRONG_TIMEFRAME": {**_market(), "timeframe": "1h"},
        "MARKET_BARS_NOT_LATEST_CLOSED": _market(now=NOW - 2 * H4),
        "MARKET_BAR_NOT_CLOSED": _market(now=NOW + H4),
    }
    for why, market in cases.items():
        d = _dims(C.compute(_inputs(j, cfg, v, market=market)))
        assert d["strategy.stop_geometry"]["reason"] == why, why
        assert d["risk.risk_rule"]["status"] == "UNAVAILABLE"


# ── liquidity ─────────────────────────────────────────────────────────────
def test_raw_volume_cannot_establish_liquidity_capacity(world, cfg):
    j, v = world
    _publish(j, cfg)
    vol = [{"kind": "volume_24h", "symbol": "BTCUSDT", "quote_volume": 9.0e9},
           {"kind": "slippage_atr_frac", "value": cfg["risk"]["slippage_atr_frac"]}]
    res = C.compute(_inputs(j, cfg, v, liquidity_evidence=vol))
    liq = _dims(res)["liquidity.liquidity_capacity_model"]
    assert (liq["status"], liq["reason"], liq["supplied_evidence_ignored"]) == (
        "UNAVAILABLE", "NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL", 2)
    assert "max_quantity" not in liq
    assert C.REGISTERED_LIQUIDITY_MODELS == {}


def test_absent_liquidity_model_fails_closed_everywhere(world, cfg):
    j, v = world
    _publish(j, cfg)
    res = C.compute(_inputs(j, cfg, v))
    assert res["status"] == "PARTIAL"          # some bounds known, effective not
    assert res["effective"] == {
        "status": "UNAVAILABLE", "reason": "MANDATORY_DIMENSION_UNAVAILABLE",
        "missing": res["effective"]["missing"]}
    assert "liquidity.liquidity_capacity_model:NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL" \
        in res["effective"]["missing"]
    # the risk number exists as its own bound, and is not effective capacity
    assert _dims(res)["risk.risk_rule"]["max_quantity"] > 0


# ── receipt: persistence, replay, currency ───────────────────────────────
def _receipt(j, cfg, v, **kw):
    _publish(j, cfg)
    out = F.evaluate_capacity(j, cfg, v["version_id"], instrument_id=BTC,
                              market_type="futures", as_of_ms=NOW,
                              registry=_registry(), positions=_positions([]),
                              market=_market(), at_ms=NOW + 1, **kw)
    return out


def test_replay_gives_the_same_receipt(world, cfg):
    j, v = world
    out = _receipt(j, cfg, v)
    assert out["status"] == "inserted" and out["status_capacity"] == "PARTIAL"
    stored = C.load(j, out["receipt_id"])
    again = C.build(copy.deepcopy(stored["inputs"]))
    assert C.canonical(again) == C.canonical(stored)
    assert C.build(_inputs(j, cfg, v))["receipt_id"] == out["receipt_id"]
    assert _receipt(j, cfg, v)["status"] == "duplicate"
    tampered = copy.deepcopy(stored)
    tampered["result"]["dimensions"]["risk"]["risk_rule"]["max_quantity"] *= 2
    with pytest.raises(C.CapacityRefused, match="capacity_replay_mismatch"):
        C.verify(tampered)
    tampered = copy.deepcopy(stored)
    tampered["inputs"]["state_kv"]["account_observation"] = None
    with pytest.raises(C.CapacityRefused, match="capacity_receipt_id_mismatch"):
        C.verify(tampered)
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        with j._tx() as c:
            c.execute(f"UPDATE {C.TABLE} SET status='ESTABLISHED'")


def test_wrong_strategy_version_is_refused(world, cfg):
    j, v = world
    out = _receipt(j, cfg, v)
    from trader.strategy.spec import StrategySpec
    spec = StrategySpec.from_dict(copy.deepcopy(v["spec"]))
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    b = F.derive_version(j, v["version_id"], spec, at_ms=NOW)
    vb = F.load_version(j, b["version_id"])
    c = C.check_current(j, cfg, vb, out["receipt_id"], now_ms=NOW + 2)
    assert "capacity_receipt_wrong_version" in c["reasons"] and not c["current"]
    e = F.eligible_for_first_live(j, b["version_id"], cfg=cfg, available_inputs=INPUTS,
                                  capacity_receipt_id=out["receipt_id"], now_ms=NOW + 2)
    assert "capacity_receipt_wrong_version" in e.reasons and not e.eligible


def test_changed_risk_policy_makes_old_receipt_not_current(world, cfg):
    j, v = world
    out = _receipt(j, cfg, v)
    stricter = copy.deepcopy(cfg)
    stricter["risk"]["risk_per_trade_pct"] = 0.25
    c = C.check_current(j, stricter, v, out["receipt_id"], now_ms=NOW + 2)
    assert "capacity_risk_policy_changed" in c["reasons"]


def test_stale_or_superseded_receipt_fails_closed(world, cfg):
    j, v = world
    out = _receipt(j, cfg, v)
    rid = out["receipt_id"]
    now = C.check_current(j, cfg, v, rid, now_ms=NOW + 2)
    assert now["reasons"] == ["capacity_not_established"]      # only liquidity etc.
    late = C.check_current(j, cfg, v, rid, now_ms=NOW + 10 * 60_000)
    assert {"capacity_account_evidence_stale", "capacity_risk_assessment_not_current",
            "capacity_venue_positions_stale"} <= set(late["reasons"])
    bar = C.check_current(j, cfg, v, rid, now_ms=NOW + H4)
    assert "capacity_market_bar_superseded" in bar["reasons"]
    _publish(j, cfg, equity=10_050.0)                             # next cycle
    sup = C.check_current(j, cfg, v, rid, now_ms=NOW + 2)
    assert "capacity_inputs_superseded:account_observation" in sup["reasons"]
    _open(j, "n1", "ETH/USDT", "long", 1.0, 3_000.0, 2_900.0)
    sup = C.check_current(j, cfg, v, rid, now_ms=NOW + 2)
    assert "capacity_inputs_superseded:trades" in sup["reasons"]
    assert C.check_current(j, cfg, v, "f" * 64, now_ms=NOW)["reasons"] == [
        "capacity_receipt_missing"]


# ── 8. strategy factory integration ─────────────────────────────────────
def test_first_live_eligibility_requires_current_established_capacity(world, cfg):
    j, v = world
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg, available_inputs=INPUTS)
    assert not e.eligible and e.reasons == ("capacity_receipt_not_asserted",)
    out = _receipt(j, cfg, v)
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg, available_inputs=INPUTS,
                                  capacity_receipt_id=out["receipt_id"], now_ms=NOW + 2)
    assert not e.eligible and e.reasons == ("capacity_not_established",)
    assert e.capacity["receipt_id"] == out["receipt_id"]
    assert e.capacity["effective"]["status"] == "UNAVAILABLE"
    assert e.capacity["contract"] == C.SCHEMA
    # the approval froze no number
    req = F.approval_request(j, v["version_id"])
    assert req["capacity"] == F.CAPACITY == C.CONTRACT_REF


def test_read_only_eligibility_check_mutates_nothing(world, cfg, tmp_path):
    j, v = world
    out = _receipt(j, cfg, v)
    before = _dump(tmp_path / "j.db")
    for _ in range(3):
        F.eligible_for_first_live(j, v["version_id"], cfg=cfg, available_inputs=INPUTS,
                                  capacity_receipt_id=out["receipt_id"], now_ms=NOW + 2)
        C.check_current(j, cfg, v, out["receipt_id"], now_ms=NOW + 2)
        C.gather(j, cfg, v, instrument_id=BTC, market_type="futures", as_of_ms=NOW,
                 registry=_registry(), positions=_positions([]), market=_market())
    assert _dump(tmp_path / "j.db") == before


def test_refusals(world, cfg):
    j, v = world
    _publish(j, cfg)
    for kw, code in (({"instrument_id": "binance_usdm:futures:FOOUSDT",
                       "registry": None}, "instrument_outside_spec_universe"),
                     ({"market_type": "spot"}, "market_type_mismatch"),
                     ({"instrument_id": "BTCUSDT"}, "instrument_id_not_canonical"),
                     ({"as_of_ms": 0}, "as_of_ms_invalid"),
                     ({"positions": _positions([], as_of_ms=NOW + 5)},
                      "venue_positions_after_as_of")):
        with pytest.raises(C.CapacityRefused, match=code):
            _inputs(j, cfg, v, **kw)


# ── no order / Risk / control-state behavior change ──────────────────────
def test_capacity_places_no_order_and_changes_no_risk_or_control():
    src = pathlib.Path("trader/strategy/capacity.py").read_text()
    code = src.split('"""', 2)[2]
    writes = set(re.findall(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|"
                            r"DELETE\s+FROM)\s+(\{?\w+\}?)", code, re.I))
    assert writes - {"ON"} <= {"{TABLE}"}, writes
    for bad in ("kv_set", "upsert_spec", "control_events", "set_control",
                "create_order", "executor", "orchestrator", "notifier",
                "state_machine", "repair_baseline", "_save_state"):
        assert bad not in code, bad
    for f in pathlib.Path("trader").rglob("*.py"):
        if f.name not in ("capacity.py", "factory_handoff.py"):
            t = f.read_text()
            assert "strategy.capacity" not in t and "import capacity" not in t, f
    from trader.core.config import load_config
    c = load_config()
    assert c["research"]["referee"] is False and c["research"]["handoff"] is False


def test_effective_is_the_tightest_bound_only_when_every_dimension_is_known(
        world, cfg, monkeypatch):
    """No such sources exist today; hypothetical dimensions prove the rule:
    effective = min of every established bound, floored to the venue step,
    and 0 below the venue minimum — never a number while one is missing."""
    j, v = world
    _publish(j, cfg)
    inputs = _inputs(j, cfg, v)
    risk_q = _dims(C.compute(inputs))["risk.risk_rule"]["max_quantity"]
    real_venue = C._venue

    def venue(reg, price, cap_q):
        out = real_venue(reg, price)
        out["maximums"] = C._dim("ESTABLISHED", None, max_quantity=cap_q)
        out["leverage"] = C._dim("ESTABLISHED", None)
        out["account_eligibility"] = C._dim("ESTABLISHED", None,
                                            account_eligibility="ELIGIBLE")
        return out
    monkeypatch.setattr(C, "_funding", lambda i: C._dim(
        "ESTABLISHED", None, max_quantity=1.0))
    monkeypatch.setattr(C, "_liquidity", lambda i: C._dim(
        "ESTABLISHED", None, max_quantity=risk_q * 0.5))
    monkeypatch.setattr(C, "_venue", lambda r, p: venue(r, p, 5.0))
    eff = C.compute(inputs)["effective"]
    assert eff["status"] == "ESTABLISHED" and eff["reason"] is None
    assert eff["max_quantity"] == C._floor_step(risk_q * 0.5, "0.001") \
        < risk_q * 0.5
    monkeypatch.setattr(C, "_liquidity", lambda i: C._dim(
        "ESTABLISHED", None, max_quantity=0.0015))       # $90: under $100 min
    eff = C.compute(inputs)["effective"]
    assert (eff["status"], eff["reason"], eff["max_quantity"]) == (
        "ESTABLISHED", "BELOW_VENUE_MINIMUM", 0.0)
    monkeypatch.setattr(C, "_liquidity", lambda i: C._dim("UNAVAILABLE", "x"))
    eff = C.compute(inputs)["effective"]
    assert eff["status"] == "UNAVAILABLE" and "max_quantity" not in eff
