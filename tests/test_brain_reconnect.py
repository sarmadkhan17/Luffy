"""Phase-0 brain-reconnect regression tests.

Covers the four critical repairs:
1. per-VOTE labels (dissenting agents get flipped correctness, not the
   decision's outcome)
2. shadow outcomes for near-threshold HOLDs (calibration data supply)
3. weight normalization (Σ=1.0 after merging measured weights)
4. evidence-scaled accuracy multipliers (small n can't pin the floor)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import (Action, Decision, Side, Snapshot, Vote,
                               now_utc)


def _mk_journal(tmp_path) -> Journal:
    return Journal(tmp_path / "brain.db")


def _seed_outcome_cycle(journal: Journal, *, symbol: str, cycle_id: str,
                        decision_id: str, action: str, correct_4h: int):
    """One cycle + one directional decision + one resolved outcome."""
    ts = now_utc().isoformat()
    journal.query(
        "INSERT OR REPLACE INTO cycles(id,ts,symbol,price,regime,adx,"
        "btc_trend,market_type,mode) VALUES (?,?,?,?,?,?,?,?,?)",
        (cycle_id, ts, symbol, 100.0, "RANGING", 20.0, "NEUTRAL",
         "futures", "paper"))
    journal.query(
        "INSERT OR REPLACE INTO decisions(id,cycle_id,ts,symbol,action,"
        "score,threshold,confidence,executed,skip_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (decision_id, cycle_id, ts, symbol, action, 0.3, 0.24, 0.5, 0, ""))
    journal.query(
        "INSERT OR REPLACE INTO outcomes(decision_id,cycle_id,symbol,ts,"
        "action,entry_price,resolved_at,fwd_ret_1h,correct_1h,fwd_ret_4h,"
        "correct_4h) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, cycle_id, symbol, ts, action, 100.0, ts,
         0.01, 1, 0.02, correct_4h))
    journal._conn().commit()


def _mk_vote(journal: Journal, cycle_id: str, symbol: str, agent: str,
             conviction: float):
    journal.query(
        "INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,"
        "confidence,rationale,meta) VALUES (?,?,?,?,?,?,?,?,?)",
        (cycle_id, now_utc().isoformat(), symbol, agent,
         "long" if conviction > 0 else "short", conviction, 0.7, "", "{}"))
    journal._conn().commit()


# ── 1. per-vote labels ───────────────────────────────────────────────────
def test_vote_labels_own_direction(tmp_path):
    j = _mk_journal(tmp_path)
    # BUY decision that WON: agreeing LONG vote → correct; dissenting
    # SHORT vote must ALSO be correct (it was wrong about direction)
    _seed_outcome_cycle(j, symbol="X/USDT", cycle_id="c1",
                        decision_id="d1", action="BUY", correct_4h=1)
    _mk_vote(j, "c1", "X/USDT", "momentum", +0.5)   # agreed → inherits 1
    _mk_vote(j, "c1", "X/USDT", "value", -0.5)      # dissented → flipped 0
    # BUY decision that LOST
    _seed_outcome_cycle(j, symbol="Y/USDT", cycle_id="c2",
                        decision_id="d2", action="BUY", correct_4h=0)
    _mk_vote(j, "c2", "Y/USDT", "momentum", +0.5)   # agreed → inherits 0
    _mk_vote(j, "c2", "Y/USDT", "value", -0.5)      # dissented → flipped 1
    rows = {r["agent"]: r for r in j.agent_accuracy(since_hours=9999)}
    assert rows["momentum"]["n"] == 2
    assert rows["momentum"]["accuracy"] == 0.5    # 1 then 0
    assert rows["value"]["n"] == 2
    assert rows["value"]["accuracy"] == 0.5       # 0 then 1
    # the old bug gave both agents the decision label (1.0 / 0.0 uniform)


def test_calibration_labels_match_vote_direction(tmp_path):
    from trader.agents import calibration
    j = _mk_journal(tmp_path)
    _seed_outcome_cycle(j, symbol="X/USDT", cycle_id="c1",
                        decision_id="d1", action="SELL", correct_4h=1)
    _mk_vote(j, "c1", "X/USDT", "flow", -0.6)     # agreed with SELL → 1
    _mk_vote(j, "c1", "X/USDT", "structure", +0.4)  # dissented → 0
    data = calibration.collect_training_data(j)
    assert data["flow"][1] == [1.0]
    assert data["structure"][1] == [0.0]


# ── 2. shadow outcomes for near-threshold HOLDs ──────────────────────────
def _orch(journal, base_threshold=0.24):
    from trader.engine.orchestrator import Orchestrator
    return Orchestrator([], journal, base_threshold=base_threshold, cfg={})


def _seed_hold(journal: Journal, symbol: str, cid: str, did: str):
    """Parent rows for a HOLD decision (outcomes FK requires them)."""
    ts = now_utc().isoformat()
    journal.query(
        "INSERT OR REPLACE INTO cycles(id,ts,symbol,price,regime,adx,"
        "btc_trend,market_type,mode) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, ts, symbol, 100.0, "RANGING", 20.0, "NEUTRAL",
         "futures", "paper"))
    journal.query(
        "INSERT OR REPLACE INTO decisions(id,cycle_id,ts,symbol,action,"
        "score,threshold,confidence,executed,skip_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (did, cid, ts, symbol, "HOLD", 0.0, 0.24, 0.3, 0, ""))
    journal._conn().commit()


def _hold_decision(score: float, threshold: float = 0.24,
                   cid: str = "ch1", did: str = "dh1",
                   symbol: str = "Z/USDT") -> Decision:
    return Decision(id=did, cycle_id=cid, symbol=symbol,
                    ts=now_utc().isoformat(), action=Action.HOLD,
                    score=score, threshold=threshold, confidence=0.3,
                    votes=[], strategy_signals=[])


def test_shadow_outcome_scheduled_for_leaners(tmp_path):
    j = _mk_journal(tmp_path)
    o = _orch(j)
    o.shadow_sample_rate = 1.0                     # deterministic in tests
    _seed_hold(j, "Z/USDT", "ch1", "dh1")
    snap = Snapshot(symbol="Z/USDT", ts=now_utc().isoformat(), price=100.0,
                    dfs={}, market_type="futures")
    # score 0.18 ≥ 0.6*0.24 → in the shadow band
    o._maybe_shadow_outcome(_hold_decision(0.18), snap)
    rows = j.query("SELECT * FROM outcomes")
    assert len(rows) == 1 and rows[0]["action"] == "BUY"


def test_shadow_outcome_skipped_for_flat_holds(tmp_path):
    j = _mk_journal(tmp_path)
    o = _orch(j)
    o.shadow_sample_rate = 1.0
    _seed_hold(j, "Z/USDT", "ch1", "dh1")
    _seed_hold(j, "W/USDT", "ch2", "dh2")
    snap = Snapshot(symbol="Z/USDT", ts=now_utc().isoformat(), price=100.0,
                    dfs={}, market_type="futures")
    o._maybe_shadow_outcome(_hold_decision(0.02), snap)   # no lean
    assert j.query("SELECT * FROM outcomes") == []
    # per-symbol cooldown: same symbol inside 90 min → no second outcome
    o._maybe_shadow_outcome(_hold_decision(-0.18), snap)  # SELL lean
    assert len(j.query("SELECT * FROM outcomes")) == 1
    # a different symbol may schedule its own shadow (SELL lean)
    snap2 = Snapshot(symbol="W/USDT", ts=now_utc().isoformat(), price=50.0,
                     dfs={}, market_type="futures")
    o._maybe_shadow_outcome(_hold_decision(-0.18, cid="ch2", did="dh2",
                                           symbol="W/USDT"), snap2)
    rows = j.query("SELECT * FROM outcomes")
    assert len(rows) == 2
    w = [r for r in rows if r["symbol"] == "W/USDT"]
    assert len(w) == 1 and w[0]["action"] == "SELL"


# ── 3. weight normalization ──────────────────────────────────────────────
def test_weights_sum_to_one(tmp_path):
    j = _mk_journal(tmp_path)
    o = _orch(j)
    total = sum(o.base_weights.values())
    assert abs(total - 1.0) < 1e-9


# ── 4. evidence-scaled multipliers ───────────────────────────────────────
def test_multiplier_evidence_scaling(tmp_path):
    j = _mk_journal(tmp_path)
    o = _orch(j)
    # same terrible accuracy, different sample sizes
    j.query("INSERT OR REPLACE INTO state_kv(key,value) "
            "VALUES ('noop','1')")
    # inject rows via the real path: one cluster of 6 outcomes, acc 0.2
    for i in range(6):
        cid, did = f"c{i}", f"d{i}"
        _seed_outcome_cycle(j, symbol=f"S{i}/USDT", cycle_id=cid,
                            decision_id=did, action="BUY", correct_4h=0)
        _mk_vote(j, cid, f"S{i}/USDT", "momentum", +0.5)
    mults = o._accuracy_multipliers()
    # n=6, acc=0 → mult = 1 + (-1)*(6/26) ≈ 0.77 — NOT the old 0.6 floor
    assert 0.70 <= mults["momentum"] <= 0.85
