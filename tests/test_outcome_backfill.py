"""Backfilling the starved learning loop.

The journal holds ~111k decisions and 162 graded outcomes — one part in 700.
Every consumer of that signal (calibration's 60-sample gate, the online
expert weights, the meta-label model's 40-sample floor, the Theorist) is
reading a trickle, while a complete feature vector sits recorded against
every one of those decisions.

The supply was never missing, only unsampled: directional decisions are all
scheduled, but a HOLD is graded only if it leans near the threshold AND wins
a 15% dice roll AND its symbol is off a 90-minute cooldown. This backfills
the leaners that the dice threw away, from candles already on disk.

Grading semantics MUST match `engine/outcomes.resolve_pending` exactly, or
the backfilled rows are not comparable with the live ones.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import now_utc
from trader.engine.outcome_backfill import backfill_holds, grade

BASE = pd.Timestamp("2026-08-25T00:00:00Z")


def _candles(prices, start=BASE, minutes=15):
    ts = [start + pd.Timedelta(minutes=minutes * i) for i in range(len(prices))]
    return pd.DataFrame({"ts": ts, "open": prices, "high": prices,
                         "low": prices, "close": prices,
                         "volume": [1.0] * len(prices)})


# ── grading matches the live resolver ────────────────────────────────────
def test_a_rise_grades_a_buy_lean_correct():
    # 4h = 240 min = 16 bars of 15m, so index 16 IS the 4h candle
    df = _candles([100.0] * 16 + [110.0] * 9)
    g = grade(df, BASE, 100.0, "BUY")
    assert g["fwd_ret_4h"] == pytest.approx(0.10, rel=1e-3)
    assert g["correct_4h"] == 1


def test_a_rise_grades_a_sell_lean_wrong():
    """Return is signed by the lean's own direction, as the resolver does."""
    df = _candles([100.0] * 16 + [110.0] * 9)
    g = grade(df, BASE, 100.0, "SELL")
    assert g["fwd_ret_4h"] == pytest.approx(-0.10, rel=1e-3)
    assert g["correct_4h"] == 0


def test_a_fall_grades_a_sell_lean_correct():
    df = _candles([100.0] * 16 + [90.0] * 9)
    assert grade(df, BASE, 100.0, "SELL")["correct_4h"] == 1


def test_a_flat_market_is_not_a_win():
    """abs(ret) below the epsilon grades 0, never 1 — matching the resolver."""
    g = grade(_candles([100.0] * 25), BASE, 100.0, "BUY")
    assert g["correct_4h"] == 0


def test_horizons_beyond_the_data_are_left_null():
    """4h of candles cannot grade the 24h horizon. Say null, don't guess."""
    g = grade(_candles([100.0] * 17), BASE, 100.0, "BUY")
    assert g["correct_4h"] is not None      # index 16 exists
    assert g["fwd_ret_24h"] is None and g["correct_24h"] is None


def test_a_decision_with_no_forward_candles_grades_nothing():
    assert grade(_candles([100.0] * 2), BASE, 100.0, "BUY")["correct_4h"] is None


def test_the_first_candle_at_or_after_the_horizon_is_used():
    """Not the last, not an average — the resolver takes iloc[0] at/after."""
    prices = [100.0] * 16 + [130.0] + [999.0] * 8      # spike exactly at 4h
    assert grade(_candles(prices), BASE, 100.0, "BUY")["fwd_ret_4h"] == \
        pytest.approx(0.30, rel=1e-3)


# ── the backfill itself ──────────────────────────────────────────────────
@pytest.fixture
def journal(tmp_path) -> Journal:
    return Journal(tmp_path / "b.db")


def _decide(j, did, symbol, action, score, threshold, ts, price=100.0):
    j.query("INSERT OR REPLACE INTO cycles(id,ts,symbol,price,regime,adx,"
            "btc_trend,market_type,mode) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"cyc_{did}", ts, symbol, price, "RANGING", 20.0, "NEUTRAL",
             "futures", "paper"))
    j.query("INSERT OR REPLACE INTO decisions(id,cycle_id,ts,symbol,action,"
            "score,threshold,confidence,executed,skip_reason,entry_price) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (did, f"cyc_{did}", ts, symbol, action, score, threshold,
             0.5, 0, "", price))


def _frames(prices):
    return {"BTC/USDT": _candles(prices)}


OLD = (BASE + pd.Timedelta(hours=1)).isoformat()


def test_a_leaning_hold_gets_graded(journal):
    _decide(journal, "d1", "BTC/USDT", "HOLD", 0.20, 0.24, OLD)
    n = backfill_holds(journal, _frames([100.0] * 17 + [110.0] * 20))
    assert n == 1
    row = journal.query("SELECT * FROM outcomes WHERE decision_id='d1'")[0]
    assert row["action"] == "BUY"          # the lean's side
    assert row["correct_4h"] == 1
    assert row["resolved_at"] is not None  # written already resolved


def test_a_negative_lean_is_recorded_as_a_sell(journal):
    _decide(journal, "d1", "BTC/USDT", "HOLD", -0.20, 0.24, OLD)
    backfill_holds(journal, _frames([100.0] * 17 + [110.0] * 20))
    row = journal.query("SELECT * FROM outcomes WHERE decision_id='d1'")[0]
    assert row["action"] == "SELL" and row["correct_4h"] == 0


def test_a_flat_hold_is_skipped(journal):
    """A score near zero is noise, not a prediction. Grading it would bury
    the real signal under 100k coin flips."""
    _decide(journal, "d1", "BTC/USDT", "HOLD", 0.01, 0.24, OLD)
    assert backfill_holds(journal, _frames([100.0] * 40)) == 0


def test_directional_decisions_are_left_alone(journal):
    """Those are already scheduled by the orchestrator."""
    _decide(journal, "d1", "BTC/USDT", "BUY", 0.30, 0.24, OLD)
    assert backfill_holds(journal, _frames([100.0] * 40)) == 0


def test_an_already_graded_decision_is_not_double_counted(journal):
    _decide(journal, "d1", "BTC/USDT", "HOLD", 0.20, 0.24, OLD)
    frames = _frames([100.0] * 17 + [110.0] * 20)
    assert backfill_holds(journal, frames) == 1
    assert backfill_holds(journal, frames) == 0
    assert journal.query("SELECT COUNT(*) n FROM outcomes")[0]["n"] == 1


def test_a_symbol_with_no_candles_is_skipped_not_crashed(journal):
    _decide(journal, "d1", "DOGE/USDT", "HOLD", 0.20, 0.24, OLD)
    assert backfill_holds(journal, _frames([100.0] * 40)) == 0


def test_a_decision_too_recent_to_grade_is_left_pending(journal):
    """Its 4h has not elapsed — it is not evidence yet."""
    _decide(journal, "d1", "BTC/USDT", "HOLD", 0.20, 0.24,
            now_utc().isoformat())
    assert backfill_holds(journal, _frames([100.0] * 40)) == 0


def test_the_lean_threshold_is_configurable(journal):
    _decide(journal, "d1", "BTC/USDT", "HOLD", 0.10, 0.24, OLD)
    frames = _frames([100.0] * 17 + [110.0] * 20)
    assert backfill_holds(journal, frames, lean_frac=0.6) == 0   # 0.10 < 0.144
    assert backfill_holds(journal, frames, lean_frac=0.4) == 1   # 0.10 > 0.096


def test_a_limit_caps_the_work(journal):
    for i in range(5):
        _decide(journal, f"d{i}", "BTC/USDT", "HOLD", 0.20, 0.24, OLD)
    assert backfill_holds(journal, _frames([100.0] * 40), limit=2) == 2
