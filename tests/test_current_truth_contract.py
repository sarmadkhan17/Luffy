"""LUFFY-CURRENT-TRUTH-CONTRACT-R1: adversarial tests.

Temp journals, fake feeds/exchanges and fixture records only: no venue, no
production database, no network. Every "current" answer must come from the
source's own time; missing, malformed, failed, future-dated or stale evidence
is never presented as a current good value.
"""
import ast
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests

from trader.core import truth
from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.dashboard import current_truth, owner_api, valuation
from trader.engine.risk import RiskManager, policy_from_config
from tests.owner_frontend_fixture import (RISK_FIXTURE_CFG, account_observation,
                                          news_guard_state, risk_assessment)

ROOT = Path(__file__).resolve().parents[1]
RISK_CFG = {"risk": {"risk_per_trade_pct": 1.0, "portfolio_heat_cap_pct": 15,
                     "per_symbol_risk_cap_pct": 8, "max_open_positions": 8,
                     "max_daily_loss_pct": 6, "halt_drawdown_pct": 20,
                     "leverage": 5, "stop_loss_atr_mult": 2.0,
                     "min_notional_usdt": 5}}


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.db")


def _kv(journal, key, rec):
    journal.kv_set(key, rec if isinstance(rec, str) else json.dumps(rec))


# ═════════════════════════ 6. generic freshness ═════════════════════════════
@pytest.mark.parametrize("value,expect,reason", [
    (None, "unavailable", "source_time_missing"),
    ("", "unavailable", "source_time_missing"),
    ("not-a-time", "invalid", "source_time_malformed"),
    (True, "invalid", "source_time_malformed"),
    (float("nan"), "invalid", "source_time_malformed"),
    (float("inf"), "invalid", "source_time_malformed"),
    (0, "invalid", "source_time_malformed"),                 # 1970: impossible
    ({"ts": 1}, "invalid", "source_time_malformed"),
])
def test_freshness_missing_and_malformed_are_never_fresh(value, expect, reason):
    f = truth.freshness_of(value, _now(), 300)
    assert f["freshness"] == expect and f["reason"] == reason and f["age_s"] is None


def test_freshness_future_is_invalid_clock_and_stale_is_preserved():
    now = _now()
    fut = truth.freshness_of((now + timedelta(seconds=60)).isoformat(), now, 300)
    assert fut["freshness"] == "invalid" and fut["reason"] == "source_time_in_future"
    for ahead in (0.000001, 0.01, 1, 2, 5):                # no positive skew allowance
        near = truth.freshness_of((now + timedelta(seconds=ahead)).isoformat(), now, 300)
        assert near["freshness"] == "invalid", ahead
        assert near["reason"] == "source_time_in_future" and near["age_s"] < 0
    assert truth.freshness_of(now.isoformat(), now, 300)["freshness"] == "fresh"
    old_src = now - timedelta(seconds=900)
    st = truth.freshness_of(old_src.isoformat(), now, 300)
    assert st["freshness"] == "stale" and st["observed_at"] == old_src.isoformat()
    assert st["age_s"] == pytest.approx(900, abs=1)        # age from the source time
    assert truth.classify_age(-6, 300) == "invalid"
    assert truth.classify_age(-0.01, 300) == "invalid"
    assert truth.classify_age(None, 300) == "unavailable"
    assert truth.classify_age(float("nan"), 300) == "unavailable"


def test_owner_generic_freshness_rejects_future_heartbeat(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "heartbeat_luffy.json").write_text(
        json.dumps({"timestamp": time.time() + 3600, "state": "ACTIVE"}))
    hb, err = owner_api.read_heartbeat(tmp_path, _now())
    assert err is None and hb["freshness"] == "invalid"
    assert owner_api._freshness(-60.0, 240) == "invalid"
    assert owner_api._freshness(10.0, 240) == "fresh"
    now = _now()
    assert owner_api._age(now + timedelta(seconds=0.01), now) < 0   # never rounds to 0.0
    (tmp_path / "data" / "heartbeat_luffy.json").write_text(
        json.dumps({"timestamp": "garbage", "state": "ACTIVE"}))
    hb, _ = owner_api.read_heartbeat(tmp_path, _now())
    assert hb["freshness"] == "invalid" and hb["observed_at"] is None


def test_supervisor_old_pass_is_not_current_proof(journal):
    old = (_now() - timedelta(hours=11)).isoformat()
    _kv(journal, "supervisor_status", {"outcome": "SAFE", "updated_at": old,
                                       "checks": {"venue_protection": True,
                                                  "reconciliation": True,
                                                  "venue_positions": True}})
    s, _ = owner_api.read_supervisor(journal, _now())
    assert s["freshness"] == "stale" and s["checks_are_current"] is False
    assert s["venue_protection"] is None and s["reconciliation"] is None
    assert s["last_reported_checks"]["venue_protection"] is True
    fut = (_now() + timedelta(hours=1)).isoformat()
    _kv(journal, "supervisor_status", {"outcome": "SAFE", "updated_at": fut,
                                       "checks": {"venue_protection": True}})
    s, _ = owner_api.read_supervisor(journal, _now())
    assert s["freshness"] == "invalid" and s["venue_protection"] is None


# ═════════════════════════ 9. protection ════════════════════════════════════
def test_protection_without_venue_snapshot_stays_unavailable(journal):
    assert owner_api.naked_exposure(None) == {
        "value": None, "status": "UNAVAILABLE", "reasons": ["no_protection_snapshot"],
        "observed_at": None}
    stale = {"freshness": "stale", "observed_at": "x", "status": "VERIFIED",
             "venue_positions": True, "complete_listing": True, "symbols": []}
    n = owner_api.naked_exposure(stale)
    assert n["value"] is None and n["status"] == "STALE"      # old zero ≠ current zero
    partial = dict(stale, freshness="fresh", complete_listing=False)
    assert owner_api.naked_exposure(partial)["value"] is None
    fresh = dict(stale, freshness="fresh", symbols=[{"stop_present": True},
                                                    {"stop_present": False}])
    assert owner_api.naked_exposure(fresh)["value"] == 1
    journal.open_trades()
    trade = {"symbol": "BTC/USDT", "opened_at": _now().isoformat()}
    assert owner_api.protection_for(trade, None)["status"] == "UNAVAILABLE"


# ═════════════════════════ 1. account provenance ════════════════════════════
def _kernel(journal, monkeypatch, reads):
    """Real Kernel._risk_step with a scripted venue read. Each item of `reads`
    is (value, basis|None, errors)."""
    from trader.engine.state import ControlStateMachine
    from trader.kernel import Kernel
    k = object.__new__(Kernel)
    k.journal = journal
    k.risk = RiskManager(RISK_CFG, journal)
    k.state_machine = ControlStateMachine(journal)
    k.notifier = NS(send=lambda *a: None)
    seq = list(reads)

    def read():
        value, basis, errors = seq.pop(0)
        k._balance_read = {"basis": basis, "errors": errors,
                           "completed_at": time.time()} if (basis or errors) else None
        return value
    monkeypatch.setattr(k, "_fetch_balance_fresh", read, raising=False)
    return k


def _obs(journal):
    return json.loads(journal.kv_get("account_observation"))


def test_account_fresh_success_is_its_own_source_time(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", [])])
    balance, status = k._risk_step()
    o = _obs(journal)
    assert balance == 1000.0 and status["risk_state"] == "ok"
    assert o["status"] == "FRESH" and o["value"] == 1000.0 and o["authoritative"]
    # timed at the read's completion, inside [attempt, record]
    assert o["attempted_at"] <= o["observed_at"] == o["successful_read_at"] <= o["recorded_at"]
    a, err = current_truth.read_account(journal, _now())
    assert err is None and a["status"] == "FRESH" and a["freshness"] == "fresh"
    assert a["equity"] == 1000.0 and a["basis"] == "venue_total_margin_balance"


def test_account_alternate_venue_path_is_labelled_fallback(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(990.0, "venue_wallet_usdt_total",
                                        ["venue_account_failed:Timeout"])])
    k._risk_step()
    o = _obs(journal)
    assert o["status"] == "VENUE_FALLBACK" and o["basis"] == "venue_wallet_usdt_total"
    assert o["reason"] == "primary_account_read_failed"
    assert o["attempt_errors"] == ["venue_account_failed:Timeout"]


def test_account_repeated_fallback_never_refreshes_source_time(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [
        (1000.0, "venue_total_margin_balance", []),
        (None, None, ["venue_account_failed:Timeout", "venue_balance_failed:Timeout"]),
        (None, None, ["venue_account_failed:Timeout", "venue_balance_failed:Timeout"])])
    def cycle_end(value):            # what cycle() writes: the row + its provenance
        journal.log_equity(value, value, 0, provenance=k._equity_provenance(value))
    k._risk_step()
    cycle_end(1000.0)
    first = _obs(journal)
    time.sleep(1.1)                  # a new equity row (ts has second resolution)
    balance, status = k._risk_step()
    cycle_end(balance)
    second = _obs(journal)
    time.sleep(1.1)
    k._risk_step()
    third = _obs(journal)
    rows = journal.query("SELECT e.ts, p.provenance FROM equity e LEFT JOIN "
                         "equity_provenance p ON p.ts=e.ts ORDER BY e.ts")
    kinds = [json.loads(r["provenance"])["row_kind"] for r in rows]
    assert kinds == ["venue_observation", "fallback_reuse"]
    assert json.loads(rows[1]["provenance"])["observed_at"] == first["observed_at"]
    assert balance == 1000.0                                  # behaviour unchanged
    for o in (second, third):
        assert o["status"] == "JOURNAL_FALLBACK" and o["authoritative"] is False
        assert o["basis"] == "journal_equity_row"
        assert o["observed_at"] == first["successful_read_at"]   # never re-aged
        assert o["attempted_at"] != first["attempted_at"]
        assert o["fallback"]["value_origin_read_at"] == first["successful_read_at"]
    assert third["attempted_at"] > second["attempted_at"]
    assert (second["consecutive_failures"], third["consecutive_failures"]) == (1, 2)
    # 400 s after the last good read, a fallback reused every cycle is stale
    later = datetime.fromisoformat(first["successful_read_at"]) + timedelta(seconds=400)
    a, _ = current_truth.read_account(journal, later)
    assert a["status"] == "STALE" and a["freshness"] == "stale"
    assert a["source_status"] == a["last_reported"] == "JOURNAL_FALLBACK"
    assert a["observed_at"] == first["successful_read_at"]
    assert "venue_read_failed" in a["reasons"]


@pytest.mark.parametrize("bad", [float("nan"), -5.0, 0.0, "abc", True, float("inf")])
def test_account_malformed_read_is_a_failure(journal, monkeypatch, bad):
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", []),
                                       (bad, "venue_total_margin_balance", [])])
    k._risk_step()
    journal.log_equity(1000.0, 1000.0, 0, provenance=k._equity_provenance(1000.0))
    k._risk_step()
    o = _obs(journal)
    assert o["status"] == "JOURNAL_FALLBACK" and o["authoritative"] is False
    assert o["reason"].startswith("venue_read_malformed")
    assert o["observed_at"] is not None                     # traced through the row


def test_account_failure_without_history_is_unavailable(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(None, None, ["venue_account_failed:X"])])
    balance, status = k._risk_step()
    o = _obs(journal)
    assert balance == 0.0 and status["risk_state"] == "uninitialized"   # unchanged
    assert o["status"] == "UNAVAILABLE" and o["value"] is None
    assert o["observed_at"] is None and o["successful_read_at"] is None
    a, err = current_truth.read_account(journal, _now())
    assert err is None and a["equity"] is None and a["freshness"] == "unavailable"


def test_account_fallback_value_of_unknown_origin_is_unproven(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", []),
                                       (None, None, [])])
    k._risk_step()
    journal.log_equity(1234.0, 1234.0, 0)                     # a row with no provenance
    k._risk_step()
    o = _obs(journal)
    assert o["status"] == "JOURNAL_FALLBACK" and o["observed_at"] is None
    assert "fallback_row_provenance_unknown" in o["reason"]
    a, _ = current_truth.read_account(journal, _now())
    assert a["freshness"] == "unavailable" and "value_source_time_unproven" in a["reasons"]


def test_account_observation_failure_never_changes_risk(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", [])])
    monkeypatch.setattr(journal, "kv_set", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    balance, status = k._risk_step()                          # swallowed, logged
    assert balance == 1000.0 and "halt_breached" in status


@pytest.mark.parametrize("mutation,why", [
    ({"value": "abc"}, "value_malformed"),
    ({"value": float("nan")}, None),                          # json can't carry NaN → malformed
    ({"status": "GREAT"}, "status_unrecognized"),
    ({"observed_at": "2026-01-01T00:00:00+00:00"}, "fresh_read_times_inconsistent"),
    ({"attempted_at": "garbage"}, "attempted_at_malformed"),
])
def test_account_reader_rejects_malformed_records(journal, mutation, why):
    rec = account_observation(_now())
    rec.update(mutation)
    raw = json.dumps(rec) if not any(isinstance(v, float) and math.isnan(v)
                                     for v in mutation.values()) else '{"value": NaN'
    _kv(journal, "account_observation", raw)
    a, err = current_truth.read_account(journal, _now())
    assert a is None and err.startswith("account_observation_malformed")
    if why:
        assert why in err


def test_account_reader_future_times_are_invalid(journal):
    fut = _now() + timedelta(hours=2)
    _kv(journal, "account_observation", account_observation(fut))
    a, err = current_truth.read_account(journal, _now())
    assert err is None and a["freshness"] == "invalid" and a["freshness"] != "fresh"
    rec = account_observation(_now(), status="JOURNAL_FALLBACK", authoritative=False,
                              observed_at=(_now() + timedelta(hours=1)).isoformat())
    _kv(journal, "account_observation", rec)
    a, err = current_truth.read_account(journal, _now())
    assert a is None and "observed_at_after_attempt" in err


def test_account_without_provenance_is_never_fresh(journal):
    journal.log_equity(5000.0, 5000.0, 0)                    # a brand-new row
    a, err = current_truth.read_account(journal, _now())
    assert err is None and a["equity"] == 5000.0
    assert a["status"] == "UNAVAILABLE" and a["freshness"] == "unavailable"
    assert a["source_status"] == "PROVENANCE_UNRECORDED"
    assert a["observed_at"] is None and a["row_written_at"]


# ═════════════════════════ 2. unrealized P&L ════════════════════════════════
def _trade(tid, sym, side, amount, entry):
    return {"id": tid, "symbol": sym, "side": side, "amount": amount, "entry_price": entry}


def _q(price, *, age=1.0, source_ms="none", field="last", now=None):
    now = now or time.time()
    return {"symbol": "?", "price": price, "field": field,
            "source_ms": None if source_ms == "none" else source_ms,
            "received_at": now - age, "error": None}


def test_valuation_complete_long_and_short():
    trades = [_trade("a", "BTC/USDT", "long", 0.01, 60000.0),
              _trade("b", "ETH/USDT:USDT", "short", 0.5, 3000.0)]
    v = valuation.estimate(trades, {"BTC/USDT": _q(61000.0), "ETH/USDT:USDT": _q(2900.0)})
    assert v["status"] == "COMPLETE" and v["total_upnl"] == pytest.approx(60.0)
    long_, short = v["positions"]
    assert long_["signed_quantity"] == 0.01 and long_["upnl_estimate"] == pytest.approx(10.0)
    assert short["signed_quantity"] == -0.5 and short["upnl_estimate"] == pytest.approx(50.0)
    assert long_["quote"]["basis"] == "ticker_last" and long_["quote"]["freshness"] == "fresh"
    assert v["fees_funding"] == "EXCLUDED_UNPROVEN" and "not the exchange mark" in v["basis"]
    assert v["coverage"] == {"positions": 2, "valued": 2, "complete": True}


def test_valuation_zero_pnl_is_a_real_zero_and_no_positions_is_zero():
    v = valuation.estimate([_trade("a", "BTC/USDT", "short", 1.0, 100.0)],
                           {"BTC/USDT": _q(100.0)})
    assert v["status"] == "COMPLETE" and v["total_upnl"] == 0.0
    e = valuation.estimate([], {})
    assert e["status"] == "NO_POSITIONS" and e["total_upnl"] == 0.0


@pytest.mark.parametrize("trade,quote,reason", [
    (_trade("b", "ETH/USDT", "long", 1, 3000.0), None, "quote_missing"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0), _q(3100.0, age=120), "quote_stale"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0), _q("abc"), "quote_price_missing_or_malformed"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0), _q(float("nan")),
     "quote_price_missing_or_malformed"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0), _q(-1.0), "quote_price_missing_or_malformed"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0), _q(3100.0, field="mark"),
     "quote_field_unrecognized"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0),
     _q(3100.0, source_ms=(time.time() + 3600) * 1000), "quote_source_in_future"),
    (_trade("b", "ETH/USDT", "long", 1, 3000.0),
     _q(3100.0, age=30, source_ms=(time.time() - 1) * 1000), "quote_time_after_receipt"),
    (_trade("b", "ETH/USDT", "long", -2, 3000.0), _q(3100.0), "quantity_malformed"),
    (_trade("b", "ETH/USDT", "flat", 1, 3000.0), _q(3100.0), "side_malformed"),
    (_trade("b", "ETH/USDT", "long", 1, "x"), _q(3100.0), "entry_price_malformed"),
    (_trade("b", "ETH/USD", "long", 1, 3000.0), _q(3100.0),
     "settlement_currency_incompatible"),
])
def test_valuation_any_gap_makes_total_unavailable_never_partial(trade, quote, reason):
    good = _trade("a", "BTC/USDT", "long", 0.01, 60000.0)
    quotes = {"BTC/USDT": _q(61000.0)}
    if quote is not None:
        quotes[trade["symbol"]] = quote
    v = valuation.estimate([good, trade], quotes)
    assert v["status"] == "UNAVAILABLE" and v["total_upnl"] is None   # not 10.0
    assert v["positions"][0]["status"] == "OK"
    bad = v["positions"][1]
    assert bad["status"] == "UNAVAILABLE" and bad["upnl_estimate"] is None
    assert reason in bad["reasons"]
    assert v["coverage"] == {"positions": 2, "valued": 1, "complete": False}


def test_valuation_quote_uses_ticker_time_and_age():
    now = _now()
    src = (now.timestamp() - 30) * 1000
    v = valuation.estimate([_trade("a", "BTC/USDT", "long", 1, 10.0)],
                           {"BTC/USDT": _q(11.0, age=5, source_ms=src)}, now)
    q = v["positions"][0]["quote"]
    assert q["time_basis"] == "ticker_timestamp" and q["age_s"] == pytest.approx(30, abs=1)


def test_ticker_quote_identity_and_failure():
    from trader.data.feed import DataFeed
    f = object.__new__(DataFeed)
    f.data_ex = NS(fetch_ticker=lambda s: {"last": None, "close": 5.0,
                                           "timestamp": 1_790_000_000_000})
    q = f.ticker_quote("X/USDT")
    assert q["price"] == 5.0 and q["field"] == "close" and q["source_ms"] == 1_790_000_000_000
    assert f.price("X/USDT") == 5.0                           # unchanged valuation
    f.data_ex = NS(fetch_ticker=lambda s: (_ for _ in ()).throw(TimeoutError("t")))
    q = f.ticker_quote("X/USDT")
    assert q["price"] is None and q["error"].startswith("ticker_fetch_failed")


def test_cached_quote_keeps_its_receipt_time(journal, monkeypatch):
    from trader.dashboard import server
    calls = []

    class Feed:
        def ticker_quote(self, sym):
            calls.append(sym)
            return {"symbol": sym, "price": 1.0, "field": "last", "source_ms": None,
                    "received_at": time.time() - 3, "error": None}
    monkeypatch.setattr(server, "_quotes_feed", Feed(), raising=False)
    monkeypatch.setattr(server, "_QUOTE_CACHE", {})
    with journal._tx() as c:
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,status) "
                  "VALUES('t1','Q/USDT','long',1,1,?, 'open')", (_now().isoformat(),))
    _, a = server._position_quotes(journal)
    _, b = server._position_quotes(journal)
    assert calls == ["Q/USDT"] and a["Q/USDT"]["received_at"] == b["Q/USDT"]["received_at"]


# ═════════════════════════ 3/4. Risk ════════════════════════════════════════
def _risk_kernel(journal, monkeypatch, equity, basis="venue_total_margin_balance"):
    k = _kernel(journal, monkeypatch, [(equity, basis if equity else None, [])] * 1)
    return k


def _assess(k, state=ControlState.ACTIVE):
    balance, status = k._risk_step()
    allowed = state == ControlState.ACTIVE and status.get("risk_state") == "ok"
    k._record_risk_assessment(state, status, allowed, "" if allowed else "x")
    return json.loads(k.journal.kv_get("risk_assessment"))


def _names(rec, result):
    return {c["name"] for c in rec["constraints"] if c["result"] == result}


def test_risk_pass_with_policy_identity(journal, monkeypatch):
    k = _risk_kernel(journal, monkeypatch, 1000.0)
    rec = _assess(k)
    assert rec["status"] == "PASS" and rec["reasons"] == []
    assert rec["policy"]["digest"] == policy_from_config(RISK_CFG)["digest"]
    assert rec["policy"]["risk_manager_identity"] == k.risk._identity
    assert {"halt_drawdown", "daily_loss_breaker", "max_open_positions",
            "portfolio_heat", "total_margin"} <= _names(rec, "pass")
    assert _names(rec, "applies_at_entry") == {"per_symbol_risk_cap", "per_position_margin"}
    assert rec["control"]["entries_permitted_by_control"] is True
    r, err = current_truth.read_risk(journal, _now(), RISK_CFG)
    assert err is None and r["current"]["status"] == "PASS"
    assert r["policy_match"] == "MATCH"


def test_risk_block_on_halt_breach(journal, monkeypatch):
    RiskManager(RISK_CFG, journal).update_equity(1000.0)       # durable peak 1000
    k = _risk_kernel(journal, monkeypatch, 700.0)
    rec = _assess(k)
    assert rec["status"] == "BLOCK" and "halt_drawdown_block" in rec["reasons"]
    assert rec["halt_breached"] is True and rec["drawdown_pct"] == 30.0


def test_risk_block_on_daily_breaker_and_max_positions(journal, monkeypatch):
    RiskManager(RISK_CFG, journal).update_equity(1000.0)
    k = _risk_kernel(journal, monkeypatch, 930.0)
    rec = _assess(k)
    assert rec["status"] == "BLOCK" and "daily_loss_breaker_block" in rec["reasons"]
    with journal._tx() as c:
        for i in range(8):
            c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,notional_usdt,"
                      "stop_loss,opened_at,status) VALUES(?,?, 'long',1,1,1,0.99,?, 'open')",
                      (f"t{i}", f"S{i}/USDT", _now().isoformat()))
    k2 = _risk_kernel(journal, monkeypatch, 1000.0)
    rec = _assess(k2)
    assert "max_open_positions_block" in rec["reasons"] and rec["status"] == "BLOCK"


def test_risk_degraded_on_journal_fallback(journal, monkeypatch):
    RiskManager(RISK_CFG, journal).update_equity(1000.0)
    journal.log_equity(1000.0, 1000.0, 0)
    k = _risk_kernel(journal, monkeypatch, None)
    rec = _assess(k)
    assert rec["status"] == "DEGRADED"
    assert "equity_input_not_fresh_authoritative" in rec["reasons"]
    assert rec["equity"]["authoritative"] is False
    assert rec["equity"]["fresh_at_assessment"] is False


def test_risk_unavailable_without_baseline(journal, monkeypatch):
    k = _risk_kernel(journal, monkeypatch, None)
    rec = _assess(k)
    assert rec["status"] == "UNAVAILABLE" and rec["reasons"][0] == "risk_state_uninitialized"


def test_risk_corrupt_baseline_blocks(journal, monkeypatch):
    RiskManager(RISK_CFG, journal).update_equity(1000.0)
    journal.kv_set("risk_state", "{broken")
    k = _risk_kernel(journal, monkeypatch, 1000.0)
    rec = _assess(k, ControlState.FROZEN)
    assert rec["status"] == "BLOCK" and "risk_baseline_block" in rec["reasons"]


def test_risk_frozen_applicability(journal, monkeypatch):
    k = _risk_kernel(journal, monkeypatch, 1000.0)
    rec = _assess(k, ControlState.FROZEN)
    assert rec["status"] == "PASS"                              # Risk itself passes …
    assert rec["control"]["state"] == "FROZEN"                  # … but control blocks
    assert rec["control"]["entries_permitted_by_control"] is False
    assert "blocked by control state" in rec["control"]["applicability"]
    assert rec["entry_gate"]["allowed"] is False


def test_risk_assessment_never_raises(journal):
    from trader.kernel import Kernel
    k = object.__new__(Kernel)
    k.journal, k.risk = journal, object()                       # broken Risk
    k._record_risk_assessment(ControlState.ACTIVE, {"risk_state": "ok"}, True, "")
    assert journal.kv_get("risk_assessment") is None


def test_risk_reader_stale_and_future_publish_no_current_numbers(journal):
    _kv(journal, "risk_assessment", risk_assessment(_now() - timedelta(hours=1)))
    r, err = current_truth.read_risk(journal, _now(), RISK_FIXTURE_CFG)
    cur = r["current"]
    assert err is None and cur["status"] == "STALE" and cur["last_reported"] == "PASS"
    assert cur["drawdown_pct"] is None and cur["daily_pnl_pct"] is None
    _kv(journal, "risk_assessment", risk_assessment(_now() + timedelta(hours=1)))
    cur = current_truth.read_risk(journal, _now())[0]["current"]
    assert cur["status"] == "UNAVAILABLE" and cur["drawdown_pct"] is None
    assert any(x.startswith("risk_assessment_invalid_clock") for x in cur["reasons"])


def _with(result_of: dict) -> list[dict]:
    """The fixture's complete constraint set with some results replaced."""
    return [dict(c, result=result_of.get(c["name"], c["result"]))
            for c in risk_assessment(_now())["constraints"]]


@pytest.mark.parametrize("over,problem", [
    ({"status": "PASS", "constraints": _with({"halt_drawdown": "block"})},
     "pass_with_blocking_constraint"),
    ({"status": "BLOCK"}, "block_without_blocking_constraint"),
    ({"constraints": [{"name": "x", "result": "pass"}]},
     "constraints_incomplete_or_duplicated"),
    ({"policy": None}, "policy_identity_missing"),
    ({"status": "OK"}, "status_unrecognized"),
    ({"control": {"state": "FROZEN", "entries_permitted_by_control": True}},
     "control_applicability_inconsistent"),
    ({"control": None}, "control_missing"),
])
def test_risk_reader_rejects_contradictions(journal, over, problem):
    rec = risk_assessment(_now())
    rec.update(over)
    _kv(journal, "risk_assessment", rec)
    r, err = current_truth.read_risk(journal, _now())
    assert r["current"]["status"] == "UNAVAILABLE" and problem in err


def test_risk_reader_missing_or_unparseable(journal):
    r, err = current_truth.read_risk(journal, _now())
    assert err == "risk_assessment_missing" and r["current"]["status"] == "UNAVAILABLE"
    journal.kv_set("risk_assessment", "not json")
    r, err = current_truth.read_risk(journal, _now())
    assert err == "risk_assessment_malformed"


def test_risk_config_identity_is_separate_from_current(journal):
    _kv(journal, "risk_assessment", risk_assessment(_now()))
    journal.kv_set("risk_state", json.dumps({"peak_equity": 5200.0,
                                             "day_start_equity": 5100.0, "day_key": "d"}))
    other = {"risk": dict(RISK_FIXTURE_CFG["risk"], max_open_positions=4)}
    r, _ = current_truth.read_risk(journal, _now(), other, ROOT)
    assert r["policy_match"] == "MISMATCH"
    assert r["configured"]["limits"]["max_open_positions"] == 4
    assert r["current"]["running_policy"]["limits"]["max_open_positions"] == 8
    assert "configuration, not current safety" in r["configured"]["source"]
    assert r["configured"]["config_file_sha256_now"]
    assert "not a current assessment" in r["baseline"]["note"]
    bad, err = current_truth.configured_policy({"risk": {}})
    assert bad is None and err.startswith("risk_config_unreadable")


def test_policy_parse_matches_running_risk_manager(journal):
    from trader.core.config import load_config
    cfg = load_config()
    rm = RiskManager(cfg, journal)
    assert rm.policy()["digest"] == policy_from_config(cfg)["digest"]
    lim = policy_from_config(cfg)["limits"]
    assert lim["max_open_positions"] == rm.max_positions
    assert lim["portfolio_heat_cap_pct"] == pytest.approx(rm.heat_cap * 100)
    assert lim["max_total_margin_pct"] == pytest.approx(rm.max_total_margin * 100)
    assert lim["max_position_margin_pct"] == pytest.approx(rm.max_pos_margin * 100)


# ═════════════════════════ 5. News Guard ════════════════════════════════════
RSS = ("<rss><channel>{}</channel></rss>")


def _item(title, pub=None, desc="x" * 60):
    p = f"<pubDate>{pub}</pubDate>" if pub else ""
    return f"<item><title>{title}</title><description>{desc}</description>{p}</item>"


def _rfc(ts):
    from email.utils import format_datetime
    return format_datetime(datetime.fromtimestamp(ts, timezone.utc))


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


def _get(monkeypatch, fn):
    import trader.brain.scraper as sc
    calls = []

    def get(*a, **k):
        calls.append(a)
        return fn()
    monkeypatch.setattr(sc.requests, "get", get)
    return calls


def _guard(journal=None, **cfg):
    from trader.agents.news_guard import NewsGuard
    return NewsGuard({"scouts": {"news_guard": {"enabled": True, **cfg}}}, journal)


def _raise(e):
    def f():
        raise e
    return f


@pytest.mark.parametrize("fn,code", [
    (_raise(requests.Timeout("slow")), "fetch_timeout"),
    (_raise(requests.ConnectionError("dns")), "fetch_connection"),
    (_raise(RuntimeError("?")), "fetch_error"),
    (lambda: _Resp("<html>busy</html>", 503), "http_status"),
    (lambda: _Resp("", 200), "empty_body"),
    (lambda: _Resp("<html><body>captcha</body></html>", 200), "parse_unrecognized_document"),
    (lambda: _Resp(RSS.format(_item("x", desc="short")), 200), "parse_no_usable_items"),
])
def test_fetch_feed_reports_failure_explicitly(monkeypatch, fn, code):
    from trader.brain.scraper import fetch_feed, scrape_feed
    _get(monkeypatch, fn)
    r = fetch_feed("https://feed.test/rss")
    assert r["ok"] is False and r["error_code"] == code and r["items"] == []
    assert scrape_feed("https://feed.test/rss") == []          # harvesters unchanged


def test_fetch_feed_empty_channel_is_a_successful_quiet_read(monkeypatch):
    from trader.brain.scraper import fetch_feed
    _get(monkeypatch, lambda: _Resp(RSS.format(""), 200))
    r = fetch_feed("https://feed.test/rss")
    assert r["ok"] is True and r["items"] == [] and r["error_code"] is None


def _publish(journal, rec):
    from trader.kernel import Kernel
    k = object.__new__(Kernel)
    k.journal = journal
    k._publish_news_guard(rec)
    return json.loads(journal.kv_get("news_guard_state"))


def test_news_armed_records_dampening_once(journal, monkeypatch):
    now = time.time()
    _get(monkeypatch, lambda: _Resp(RSS.format(_item("Exchange hacked", _rfc(now - 600))), 200))
    st = _guard(journal).check()
    assert st["active"] is True and st["truth"] == "ARMED"
    assert st["dampening"] == {"applied": True, "threshold_add": 0.08, "score_mult": 0.75,
                               "enforcement": "dampen_only"}
    assert st["succeeded_at"] and st["assessed_at"] >= st["attempted_at"]
    assert st["newest_publication_at"] is not None
    pub = _publish(journal, st)
    v, err = current_truth.read_news_guard(journal, _now())
    assert err is None and v["status"] == "ARMED" and v["clear"] is False
    assert pub["published_at"] and pub["ts"] == st["assessed_at"]


def test_news_quiet_is_the_only_clear(journal, monkeypatch):
    _get(monkeypatch, lambda: _Resp(RSS.format(_item("Bitcoin steady", _rfc(time.time()))), 200))
    st = _guard(journal).check()
    assert st["truth"] == "QUIET" and st["why"] == "feed quiet" and not st["active"]
    _publish(journal, st)
    v, _ = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "QUIET" and v["clear"] is True and v["freshness"] == "fresh"


@pytest.mark.parametrize("fn,truth_,code", [
    (_raise(requests.Timeout("slow")), "FETCH_FAILED", "fetch_timeout"),
    (lambda: _Resp("bad gateway", 502), "FETCH_FAILED", "http_status"),
    (lambda: _Resp("<html>not a feed</html>", 200), "PARSE_FAILED",
     "parse_unrecognized_document"),
])
def test_news_failure_is_never_quiet_and_fails_open(journal, monkeypatch, fn, truth_, code):
    _get(monkeypatch, fn)
    st = _guard(journal).check()
    assert st["active"] is False                                  # fail-open, no veto
    assert st["truth"] == truth_ and st["failure_codes"] == [code]
    assert st["why"] != "feed quiet" and st["dampening"]["applied"] is False
    assert st["succeeded_at"] is None
    _publish(journal, st)
    v, _ = current_truth.read_news_guard(journal, _now())
    assert v["status"] == truth_ and v["clear"] is False


def test_news_partial_feed_failure_is_not_quiet_but_still_arms(journal, monkeypatch):
    from trader.agents import news_guard as ng
    monkeypatch.setattr(ng, "FEEDS", ["https://a.test/rss", "https://b.test/rss"])
    ok = {"ok": True, "items": [], "error_code": None, "http_status": 200}
    bad = {"ok": False, "items": [], "error_code": "fetch_timeout", "http_status": None}
    monkeypatch.setattr(ng, "fetch_feed", lambda url: bad if "a." in url else ok)
    st = _guard(journal).check()
    assert st["truth"] == "FETCH_FAILED" and st["active"] is False
    assert st["failure_codes"] == ["fetch_timeout"] and st["succeeded_at"]
    hot = dict(ok, items=[{"title": "Exchange hacked", "text": "x" * 60, "age_h": 0.1,
                           "published_ts": time.time() - 360}])
    monkeypatch.setattr(ng, "fetch_feed", lambda url: bad if "a." in url else hot)
    st = _guard(journal).check()
    assert st["truth"] == "ARMED" and st["active"] is True     # arming is unchanged
    undated = dict(ok, items=[{"title": "Exchange hacked", "text": "x" * 60, "age_h": None}])
    monkeypatch.setattr(ng, "fetch_feed", lambda url: undated)
    st = _guard(journal).check()
    assert st["active"] is True and st["truth"] == "UNCERTAIN"   # dampened, not "known"


def test_news_failure_after_success_keeps_last_success_time(journal, monkeypatch):
    g = _guard(journal, refresh_minutes=0)
    _get(monkeypatch, lambda: _Resp(RSS.format(_item("Exchange hacked")), 200))
    first = g.check()
    time.sleep(0.01)
    _get(monkeypatch, _raise(requests.Timeout("slow")))
    second = g.check()
    assert second["truth"] == "FETCH_FAILED" and second["active"] is False
    assert second["succeeded_at"] == first["succeeded_at"] < second["attempted_at"]
    events = [r["event"] for r in journal.query("SELECT event FROM control_events")]
    assert events == ["news_blackout", "news_guard_fail_open"]   # never a false news_clear


def test_news_cache_is_never_re_aged(journal, monkeypatch):
    calls = _get(monkeypatch, lambda: _Resp(RSS.format(_item("calm", _rfc(time.time()))),
                                            200))
    g = _guard(journal)
    a = g.check()
    time.sleep(0.01)
    b = g.check()
    assert len(calls) == 1 and a == b                            # same assessed_at
    p1 = _publish(journal, b)
    time.sleep(0.01)
    p2 = _publish(journal, g.check())
    assert p1["assessed_at"] == p2["assessed_at"] and p1["published_at"] < p2["published_at"]
    later = datetime.fromisoformat(a["assessed_at"]) + timedelta(seconds=g.stale_after_s + 1)
    v, _ = current_truth.read_news_guard(journal, later)
    assert v["status"] == "STALE" and v["last_reported"] == "QUIET" and v["clear"] is False


def test_news_old_publication_is_outside_the_window(journal, monkeypatch):
    old = _rfc(time.time() - 48 * 3600)
    _get(monkeypatch, lambda: _Resp(RSS.format(_item("Exchange hacked", old)), 200))
    st = _guard(journal).check()
    # a successful fetch of an all-old feed is not a current quiet feed
    assert st["truth"] == "FEED_STALE" and st["active"] is False
    assert st["oldest_publication_at"] is not None and st["dated_in_window"] == 0
    _publish(journal, st)
    v, _ = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "FEED_STALE" and v["clear"] is False


def test_news_future_publication_is_uncertain_not_clear(journal, monkeypatch):
    fut = _rfc(time.time() + 3600)
    _get(monkeypatch, lambda: _Resp(RSS.format(_item("Markets calm", fut)), 200))
    st = _guard(journal).check()
    assert st["truth"] == "UNCERTAIN" and st["future_dated_items"] == 1
    _publish(journal, st)
    v, _ = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "UNCERTAIN" and v["clear"] is False
    # a future-dated severe headline still dampens exactly as before (age
    # clamps to 0) but is not dated evidence: UNCERTAIN, not ARMED
    _get(monkeypatch, lambda: _Resp(RSS.format(_item("Exchange hacked", fut)), 200))
    st = _guard().check()
    assert st["active"] is True and st["truth"] == "UNCERTAIN"
    assert st["dampening"]["applied"] is True


def test_news_disabled_and_exception_paths(journal, monkeypatch):
    from trader.agents import news_guard as ng
    st = ng.NewsGuard({"scouts": {"news_guard": {"enabled": False}}}).check()
    assert st["truth"] == "DISABLED" and st["why"] == "disabled" and not st["active"]
    monkeypatch.setattr(ng, "fetch_feed", lambda url: (_ for _ in ()).throw(ValueError("x")))
    st = _guard().check()
    assert st["truth"] == "FETCH_FAILED" and st["why"].startswith("fetch error")
    assert st["failure_codes"] == ["fetch_exception"] and st["active"] is False
    # the feed was read; assessing it raised → ASSESSMENT_FAILED, fail-open
    monkeypatch.setattr(ng, "fetch_feed", lambda url: {
        "ok": True, "items": [{"title": None, "text": None, "age_h": 0.1,
                               "published_ts": time.time() - 60}]})
    st = _guard(journal).check()
    assert st["truth"] == "ASSESSMENT_FAILED" and st["active"] is False
    assert st["failure_codes"] == ["assessment_exception"] and st["fetched_at"]
    _publish(journal, st)
    v, err = current_truth.read_news_guard(journal, _now())
    assert err is None and v["status"] == "ASSESSMENT_FAILED" and not v["clear"]


@pytest.mark.parametrize("over,problem", [
    ({"active": True, "dampening": {"applied": True, "threshold_add": 0.08,
                                     "score_mult": 0.75}}, "active_contradicts_truth"),
    ({"active": True}, "dampening_record_inconsistent"),
    ({"hits": 3, "severe": 1}, "active_contradicts_counts"),
    ({"failure_codes": ["fetch_timeout"]}, "quiet_with_failure_or_uncertain_items"),
    ({"undated_items": 1}, "quiet_with_failure_or_uncertain_items"),
    ({"malformed_dates": 1}, "quiet_with_failure_or_uncertain_items"),
    ({"future_dated_items": 1}, "quiet_with_failure_or_uncertain_items"),
    ({"truth": "FETCH_FAILED"}, "failure_without_code"),
    ({"truth": "PARSE_FAILED", "failure_codes": ["fetch_timeout"]},
     "parse_failure_code_mismatch"),
    ({"truth": "ASSESSMENT_FAILED"}, "assessment_failure_without_code"),
    ({"severe": 2, "hits": 1, "truth": "ARMED", "active": True,
      "dampening": {"applied": True, "threshold_add": 0.08, "score_mult": 0.75}},
     "counts_not_nested"),
    ({"items_considered": 9}, "counts_not_nested"),
    ({"dated_hits": 1}, "dated_counts_not_nested"),
    ({"min_headlines": 0}, "min_headlines_malformed"),
    ({"hits": -1}, "hits_malformed"),
    ({"dated_in_window": 0}, "quiet_without_current_dated_items"),
    ({"truth": "FEED_STALE", "why": "x"}, "feed_stale_inconsistent"),
    ({"truth": "EMPTY_FEED"}, "empty_feed_with_items"),
    ({"truth": "UNCERTAIN"}, "uncertain_without_cause"),
    ({"truth": "ARMED", "active": True, "hits": 2, "severe": 1, "dated_hits": 0,
      "dated_severe": 0, "dampening": {"applied": True, "threshold_add": 0.08,
                                       "score_mult": 0.75}},
     "armed_without_dated_evidence"),
    ({"dampening": {"applied": False, "threshold_add": 0.1, "score_mult": 0.75}},
     "dampening_record_inconsistent"),
    ({"truth": "CLEAR"}, "truth_unrecognized"),
    ({"assessed_at": "yesterday"}, "assessed_at_malformed"),
    ({"succeeded_at": None}, "assessment_without_successful_fetch"),
    ({"fetched_at": None}, "assessment_without_successful_fetch"),
    ({"stale_after_s": 1e9}, "stale_after_malformed"),
])
def test_news_reader_refuses_contradictions(journal, over, problem):
    rec = news_guard_state(_now())
    rec.update(over)
    _kv(journal, "news_guard_state", rec)
    v, err = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "UNAVAILABLE" and v["clear"] is False and problem in err


def test_news_reader_time_relationships(journal):
    now = _now()
    # any publication later than the fetch is impossible unless recorded as future
    for ahead in (timedelta(seconds=1), timedelta(hours=1)):
        rec = news_guard_state(now - timedelta(minutes=1),
                               newest_publication_at=(now - timedelta(minutes=1)
                                                      + ahead).isoformat())
        _kv(journal, "news_guard_state", rec)
        assert "future_publication_not_recorded" in \
            current_truth.read_news_guard(journal, now)[1]
    rec = news_guard_state(now, fetched_at=(now + timedelta(seconds=1)).isoformat(),
                           published_at=(now + timedelta(seconds=2)).isoformat())
    _kv(journal, "news_guard_state", rec)
    assert "fetch_after_assessment" in current_truth.read_news_guard(journal, now)[1]
    rec = news_guard_state(now, attempted_at=(now + timedelta(minutes=5)).isoformat())
    _kv(journal, "news_guard_state", rec)
    assert "attempt_after_assessment" in current_truth.read_news_guard(journal, now)[1]
    rec = news_guard_state(now, published_at=(now - timedelta(minutes=5)).isoformat())
    _kv(journal, "news_guard_state", rec)
    assert "assessment_after_publication" in current_truth.read_news_guard(journal, now)[1]
    fut = now + timedelta(hours=1)
    _kv(journal, "news_guard_state", news_guard_state(fut))
    v, err = current_truth.read_news_guard(journal, now)
    assert v["status"] == "UNAVAILABLE" and v["last_reported"] == "QUIET" and not v["clear"]


def test_news_missing_or_legacy_record_is_unavailable(journal):
    v, err = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "UNAVAILABLE" and err == "news_guard_state_missing"
    _kv(journal, "news_guard_state", {"active": False, "why": "feed quiet",
                                      "ts": _now().isoformat()})   # pre-R1 shape
    v, err = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "UNAVAILABLE" and not v["clear"]


def test_kernel_publication_tolerates_minimal_doubles(journal):
    pub = _publish(journal, {"active": False})
    assert pub["active"] is False and pub["ts"] is None and pub["published_at"]
    v, _ = current_truth.read_news_guard(journal, _now())
    assert v["status"] == "UNAVAILABLE"


def test_orchestrator_dampening_is_applied_exactly_once():
    from trader.agents import news_guard as ng
    src = (ROOT / "trader/engine/orchestrator.py").read_text()
    tree = ast.parse(src)
    blocks = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and "news.get('active')" in ast.unparse(n.test)
              and any(isinstance(s, ast.AugAssign) for s in n.body)]
    assert len(blocks) == 1
    body = [ast.unparse(s) for s in blocks[0].body]
    assert body == [f"threshold += {ng.THRESHOLD_ADD}", f"net *= {ng.SCORE_MULT}"]
    assert len(re.findall(r"threshold \+= 0\.08", src)) == 1
    assert len(re.findall(r"net \*= 0\.75", src)) == 1


def test_orchestrator_uses_the_guard_record_unchanged(monkeypatch):
    """A full NewsGuard record drives decide() exactly like the old {active, why}."""
    from tests.test_scout_upgrades import _orch, _snap
    rec = news_guard_state(_now(), truth="ARMED")
    full = _orch(0.5, None, news_guard=NS(check=lambda: dict(rec)))
    old = _orch(0.5, None, news_guard=NS(check=lambda: {"active": True, "why": "x"}))
    quiet = _orch(0.5, None)
    d_full = full.decide(_snap(), population=[], entry_allowed=True)
    d_old = old.decide(_snap(), population=[], entry_allowed=True)
    d_q = quiet.decide(_snap(), population=[], entry_allowed=True)
    assert d_full.threshold == pytest.approx(d_old.threshold)
    assert d_full.threshold - d_q.threshold == pytest.approx(0.08)
    assert d_full.score == pytest.approx(d_old.score)
    assert d_full.score == pytest.approx(d_q.score * 0.75)


# ═════════════════════════ 7. false defaults ════════════════════════════════
def _gql(journal, query):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from trader.api.graphql_schema import make_graphql_router
    app = FastAPI()
    app.include_router(make_graphql_router(journal))
    return TestClient(app).post("/graphql", json={"query": query}).json()


def test_graphql_missing_values_are_null_not_defaults(journal):
    d = _gql(journal, "{ status { control_state equity drawdown_pct daily_pnl_pct "
                      "equity_freshness risk_status } news_guard { active status clear why } }")
    s, n = d["data"]["status"], d["data"]["news_guard"]
    assert s["control_state"] is None                     # not ACTIVE
    assert s["equity"] is None                            # not "0"
    assert s["drawdown_pct"] is None and s["daily_pnl_pct"] is None   # not "0"
    assert s["risk_status"] == "UNAVAILABLE" and s["equity_freshness"] == "unavailable"
    assert n["active"] is None and n["status"] == "UNAVAILABLE" and n["clear"] is False


def test_graphql_uses_fresh_risk_only(journal):
    journal.kv_set("control_state", "FROZEN")
    _kv(journal, "risk_assessment", risk_assessment(_now()))
    _kv(journal, "account_observation", account_observation(_now()))
    _kv(journal, "news_guard_state", news_guard_state(_now(), truth="ARMED"))
    d = _gql(journal, "{ status { control_state equity drawdown_pct daily_pnl_pct } "
                      "news_guard { active status } }")["data"]
    assert d["status"] == {"control_state": "FROZEN", "equity": "5164.5",
                           "drawdown_pct": "1.2", "daily_pnl_pct": "-0.4"}
    assert d["news_guard"] == {"active": True, "status": "ARMED"}
    _kv(journal, "risk_assessment", risk_assessment(_now() - timedelta(hours=2)))
    _kv(journal, "news_guard_state", news_guard_state(_now() - timedelta(hours=2)))
    d = _gql(journal, "{ status { drawdown_pct daily_pnl_pct risk_status } "
                      "news_guard { active status } }")["data"]
    assert d["status"] == {"drawdown_pct": None, "daily_pnl_pct": None, "risk_status": "STALE"}
    assert d["news_guard"] == {"active": None, "status": "STALE"}


def test_legacy_company_never_defaults_active_or_clear(journal):
    from trader.dashboard import server
    src = (ROOT / "trader/dashboard/server.py").read_text()
    assert 'kv_get("control_state", "ACTIVE")' not in src
    company = json.dumps(server.build_company(journal, {"risk": {}}))
    assert '["Control", "UNKNOWN"]' in company and '"ACTIVE"' not in company
    assert '["Guard", "unavailable"]' in company            # missing news ≠ clear
    _kv(journal, "news_guard_state", news_guard_state(_now()))
    assert '["Guard", "clear"]' in json.dumps(server.build_company(journal, {"risk": {}}))


# ═════════════════════════ 8. exact latest beyond 500 ═══════════════════════
def _decisions(journal, rows):
    with journal._tx() as c:
        c.execute("INSERT OR IGNORE INTO cycles(id,ts,symbol) VALUES('c','t','X')")
        c.executemany(
            "INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,"
            "executed,skip_reason) VALUES(?, 'c', ?, 'X/USDT', ?, 0, 0.3, 0, ?, ?)", rows)


def test_latest_matches_beyond_the_newest_500(journal):
    from trader.dashboard import owner_reads
    ts = (_now() - timedelta(minutes=5)).isoformat()
    _decisions(journal, [("d-exec", ts, "BUY", 1, None),
                         ("d-rej", ts, "SELL", 0, "risk budget exhausted"),
                         ("d-req", ts, "BUY", 0, None)])
    later = _now().isoformat()
    _decisions(journal, [(f"h{i}", later, "HOLD", 0, None) for i in range(1200)])
    summary = owner_reads.overview_activity(journal)["decisions"]
    # the canonical summary's executed/skipped are exact, not the 500 window
    assert [r["id"] for r in summary["executed"]] == ["d-exec"]
    assert [r["id"] for r in summary["skipped"]] == ["d-req", "d-rej"]
    lat = owner_reads.latest_activity(journal)
    assert lat["latest_decision"]["record"]["id"] == "h1199"
    assert lat["latest_executed"]["record"]["id"] == "d-exec"
    assert lat["latest_rejection"]["record"]["id"] == "d-rej"
    assert lat["latest_requested"]["record"]["id"] == "d-req"
    ops = owner_reads.operations(journal)
    assert ops["latest"]["latest_rejection"]["record"]["id"] == "d-rej"
    assert owner_reads.overview_activity(journal)["latest"]["latest_executed"]["found"]


def test_latest_reads_have_no_lookback_bound_and_use_indexes(journal):
    from trader.dashboard import owner_reads
    old = (_now() - timedelta(days=400)).isoformat()
    _decisions(journal, [("ancient-rej", old, "SELL", 0, "risk")])
    _decisions(journal, [(f"h{i}", _now().isoformat(), "HOLD", 0, None)
                         for i in range(250_000)])            # > the old 200k bound
    lat = owner_reads.latest_activity(journal)
    assert lat["latest_rejection"]["record"]["id"] == "ancient-rej"
    c = journal._conn()
    for where, index in owner_reads._INDEX_FOR.items():
        plan = " ".join(r[3] for r in c.execute(
            f"EXPLAIN QUERY PLAN SELECT id FROM decisions INDEXED BY {index} "
            f"WHERE {where} ORDER BY ts DESC, rowid DESC LIMIT 1"))
        assert index in plan and "TEMP B-TREE" not in plan, plan
    src = (ROOT / "trader/dashboard/owner_reads.py").read_text()
    body = src[src.index("def latest_activity"):src.index("def overview_activity")]
    assert "MAX(rowid)" not in body and "MIN(rowid)" not in body


def test_indexes_are_created_by_normal_journal_initialization(tmp_path):
    j = Journal(tmp_path / "fresh.db")
    names = {r["name"] for r in j.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_decisions_executed_ts", "idx_decisions_directional_ts",
            "idx_decisions_skipped_ts", "idx_decisions_rejected_ts"} <= names
    tables = {r["name"] for r in j.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "equity_provenance" in tables


def test_latest_journal_execution_is_not_a_proven_fill(journal):
    from trader.dashboard import owner_reads
    with journal._tx() as c:
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,exec_mode,opened_at,"
                  "status) VALUES('t1','X/USDT','long',1,1,'live',?, 'open')",
                  (_now().isoformat(),))
    lat = owner_reads.latest_activity(journal)
    ex, fill = lat["latest_journal_execution"], lat["latest_proven_fill"]
    assert "latest_venue_filled" not in lat
    assert ex["record"]["id"] == "t1" and ex["fill_evidence"] == "JOURNAL_BOOKED_UNVERIFIED"
    assert fill == {"status": "UNKNOWN", "record": None, "ref": None,
                    "reason": "latest_journal_execution_not_verified"}
    empty = owner_reads.latest_activity(Journal(Path(journal.db_path).parent / "e.db"))
    assert empty["latest_proven_fill"]["status"] == "UNKNOWN"
    assert empty["latest_rejection"] == {"found": False, "record": None, "ref": None}


# ═════════════════════════ owner API wiring ═════════════════════════════════
@pytest.fixture
def live(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import TOKEN, make_app
    app, journal, _ = make_app(tmp_path, monkeypatch)
    return TestClient(app), journal, {"x-luffy-token": TOKEN}


def test_owner_overview_exposes_truth_sections(live):
    c, journal, h = live
    o = c.get("/owner-api/v1/overview", headers=h).json()
    assert o["errors"] == {}
    assert o["account"]["status"] == "FRESH" and o["account"]["freshness"] == "fresh"
    assert o["news_guard"]["status"] == "QUIET" and o["news_guard"]["clear"] is True
    assert o["risk"]["current"]["status"] == "PASS"
    assert o["risk"]["current"]["control"]["entries_permitted_by_control"] is False
    assert o["protection"]["naked_exposure"]["status"] in ("OBSERVED", "STALE",
                                                           "UNAVAILABLE")
    journal.kv_set("news_guard_state", "")
    o = c.get("/owner-api/v1/overview", headers=h).json()
    assert o["news_guard"]["status"] == "UNAVAILABLE" and o["errors"]["news_guard"]


def test_owner_valuation_and_marks_never_partial(live):
    c, journal, h = live
    v = c.get("/owner-api/v1/valuation", headers=h).json()["valuation"]
    assert v["status"] == "UNAVAILABLE" and v["total_upnl"] is None   # ETH has no quote
    btc = next(p for p in v["positions"] if p["symbol"] == "BTC/USDT")
    assert btc["upnl_estimate"] == pytest.approx(10.0)
    m = c.get("/owner-api/v1/enrichment/marks", headers=h).json()
    assert set(m["marks"]) == {"BTC/USDT"} and "ETH/USDT" in m["unvalued"]
    assert m["total_upnl"] is None and m["observed_at"]
    assert "not the exchange mark" in m["source"]


def test_owner_risk_and_latest_routes(live):
    c, journal, h = live
    r = c.get("/owner-api/v1/risk", headers=h).json()["risk"]
    assert r["current"]["status"] == "PASS" and r["policy_match"] in ("MATCH", "UNKNOWN")
    lat = c.get("/owner-api/v1/activity/latest", headers=h).json()
    assert lat["latest_decision"]["record"]["id"] == "d1"
    n = c.get("/owner-api/v1/news-guard", headers=h).json()
    assert n["news_guard"]["status"] == "QUIET"
    for path in ("risk", "valuation", "activity/latest", "news-guard"):
        assert c.post(f"/owner-api/v1/{path}", headers=h).status_code in (401, 403, 405)
