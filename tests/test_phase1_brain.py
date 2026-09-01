"""Phase-1 quantitative core regression tests.

EWA: fold math, recovery mixing, prior shrinkage, normalization.
Meta-labeling: feature shape, size bounds, validation gate (bad brier →
passthrough; separable data → live judgment).
Adaptive threshold: fallback, tightening under low hit rates.

All state files are monkeypatched to tmp paths — production
data/*.json is never touched from tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import now_utc


def _journal(tmp_path) -> Journal:
    return Journal(tmp_path / "p1.db")


def _seed(j, *, cid, did, symbol, action, ok, votes, regime="RANGING"):
    ts = now_utc().isoformat()
    j.query("INSERT OR REPLACE INTO cycles(id,ts,symbol,price,regime,adx,"
            "btc_trend,market_type,mode) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, ts, symbol, 100.0, regime, 20.0, "NEUTRAL", "futures",
             "paper"))
    j.query("INSERT OR REPLACE INTO decisions(id,cycle_id,ts,symbol,action,"
            "score,threshold,confidence,executed,skip_reason,signals_json,"
            "meta_p) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, cid, ts, symbol, action, 0.3, 0.24, 0.5, 0, "", "[]",
             None))
    j.query("INSERT OR REPLACE INTO outcomes(decision_id,cycle_id,symbol,"
            "ts,action,entry_price,resolved_at,fwd_ret_1h,correct_1h,"
            "fwd_ret_4h,correct_4h) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (did, cid, symbol, ts, action, 100.0, ts, 0.01, 1, 0.02, ok))
    for i, (agent, conv) in enumerate(votes):
        j.query("INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,"
                "confidence,rationale,meta) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, ts, symbol, agent,
                 "long" if conv > 0 else "short", conv, 0.7, "", "{}"))
    j._conn().commit()


# ── EWA ──────────────────────────────────────────────────────────────────
def test_ewa_fold_and_recovery(tmp_path, monkeypatch):
    from trader.agents import weights_online as ewa
    monkeypatch.setattr(ewa, "PATH", tmp_path / "ewa.json")
    j = _journal(tmp_path)

    # agent A always wrong (10 calls), agent B always right (10 calls)
    for i in range(10):
        _seed(j, cid=f"cw{i}", did=f"dw{i}", symbol=f"W{i}/USDT",
              action="BUY", ok=0,            # BUY lost → long voters wrong
              votes=[("alpha", +0.5), ("beta", -0.5)])
    st = ewa.update(j, eta=0.5, mix=0.05)
    a, b = st["agents"]["alpha"]["w"], st["agents"]["beta"]["w"]
    assert b > a            # beta (dissenting=right) holds up
    assert a > 0.01         # mixing keeps alpha alive (no zero-pinning)

    # recovery: alpha goes on a 40-call correct streak → converges toward 1
    for i in range(10, 50):
        _seed(j, cid=f"cw{i}", did=f"dw{i}", symbol=f"W{i}/USDT",
              action="SELL", ok=1,           # SELL won → short voters right
              votes=[("alpha", -0.5), ("beta", +0.5)])
    st = ewa.update(j, eta=0.5, mix=0.05)
    assert st["agents"]["alpha"]["w"] > 0.8   # recovered


def test_ewa_blended_normalized(tmp_path, monkeypatch):
    from trader.agents import weights_online as ewa
    monkeypatch.setattr(ewa, "PATH", tmp_path / "ewa.json")
    prior = {"a": 0.5, "b": 0.3, "c": 0.2}
    # no state → pure prior, normalized
    out = ewa.blended_weights(prior, prior_k=30.0)
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["a"] > out["b"] > out["c"]


def _write_ewa(monkeypatch, tmp_path, agents):
    """Put a hand-built EWA state on disk for blended_weights to read."""
    import json
    from trader.agents import weights_online as ewa
    path = tmp_path / "ewa.json"
    monkeypatch.setattr(ewa, "PATH", path)
    path.write_text(json.dumps({"agents": agents, "cursor": "", "updated": 0}))
    return ewa


def test_ewa_equal_evidence_leaves_the_prior_untouched(tmp_path, monkeypatch):
    """No agent out-performed any other, so nothing should move.

    EWA weights are unnormalized multiplicative scores; the prior is a
    distribution summing to 1. Blending them without putting them on one
    scale lets the EWA term swamp the prior — identical evidence then
    flattens 0.50/0.30/0.20 toward uniform, which is a scale artefact, not
    a measurement.
    """
    prior = {"a": 0.5, "b": 0.3, "c": 0.2}
    ewa = _write_ewa(monkeypatch, tmp_path,
                     {a: {"w": 0.6, "n": 100} for a in prior})
    out = ewa.blended_weights(prior, prior_k=30.0)
    assert abs(sum(out.values()) - 1.0) < 1e-9
    for a, p in prior.items():
        assert abs(out[a] - p) < 1e-9, f"{a}: {out[a]:.4f} != prior {p}"


def test_ewa_ranks_the_better_expert_above_the_worse_one(tmp_path, monkeypatch):
    """Equal priors, unequal scores → the stronger expert gains share."""
    prior = {"a": 0.5, "b": 0.5}
    ewa = _write_ewa(monkeypatch, tmp_path,
                     {"a": {"w": 1.0, "n": 100}, "b": {"w": 0.5, "n": 100}})
    out = ewa.blended_weights(prior, prior_k=30.0)
    assert out["a"] > 0.5 > out["b"]
    # shrunk toward the prior, never all the way to the raw posterior 2:1
    assert out["a"] / out["b"] < 1.0 / 0.5


def test_ewa_without_a_peer_to_compare_against_keeps_the_prior(tmp_path,
                                                               monkeypatch):
    """One agent's score in isolation is not evidence about the others.

    A multiplicative score only means something relative to the other
    experts it is competing with. With a single agent carrying state, the
    old code still let its raw score move the whole distribution.
    """
    prior = {"a": 0.5, "b": 0.5}
    ewa = _write_ewa(monkeypatch, tmp_path, {"a": {"w": 0.6, "n": 100}})
    out = ewa.blended_weights(prior, prior_k=30.0)
    assert abs(out["a"] - 0.5) < 1e-9
    assert abs(out["b"] - 0.5) < 1e-9


# ── meta-labeling ────────────────────────────────────────────────────────
def test_meta_features_shape_and_size_bounds():
    from trader.brain import meta_label as ml
    f = ml.features(0.3, 0.24, 0.7, "RANGING", -0.4, 30.0,
                    "2026-08-31T14:30:00+00:00", 2, 1.0, False)
    assert len(f) == len(ml.FEATS)
    assert ml.size_mult(0.20) == 0.25     # clamped floor
    assert ml.size_mult(0.50) == 0.33     # (0.5-0.35)/0.45
    assert ml.size_mult(0.90) == 1.0      # clamped ceiling
    assert ml.size_mult(0.70) == 0.78


def test_meta_validation_gate(tmp_path, monkeypatch):
    from trader.brain import meta_label as ml
    monkeypatch.setattr(ml, "PATH", tmp_path / "meta.json")
    monkeypatch.setattr(ml, "_cache", {"m": None, "ts": 0.0})
    j = _journal(tmp_path)

    # label noise: outcome ok uncorrelated with anything → bad OOS brier
    import random
    random.seed(7)
    for i in range(60):
        ok = random.choice([0, 1])
        _seed(j, cid=f"cm{i}", did=f"dm{i}", symbol=f"M{i}/USDT",
              action="BUY" if i % 2 else "SELL", ok=ok,
              votes=[("alpha", 0.5 if i % 3 else -0.5)])
    st = ml.refit(j)
    # n_test < 30 → not ready regardless
    assert st["n"] == 60 and st["n_test"] < ml.META_MIN_TEST
    assert st["ready"] is False
    assert ml.judge([0.5] * len(ml.FEATS)) is None   # passthrough

    # a clean-separable pattern: BUY+high score wins, SELL loses
    # (120 rows → n_test = 36 ≥ META_MIN_TEST)
    j2 = _journal(tmp_path / "m2.db")
    for i in range(120):
        win = (i % 2 == 0)
        _seed(j2, cid=f"cg{i}", did=f"dg{i}", symbol=f"G{i}/USDT",
              action="BUY" if win else "SELL", ok=1 if win else 0,
              votes=[("alpha", 0.5 if win else -0.5)])
    st2 = ml.refit(j2)
    assert st2["n_test"] >= ml.META_MIN_TEST
    # deterministic pattern → both halves fit → ready, judge responds
    assert st2["ready"] is True
    p = ml.judge(ml.features(0.9, 0.24, 1.0, "RANGING", 0.0, 25.0,
                             "2026-08-31T14:00:00+00:00", 0, 0.5, False))
    assert p is not None and 0.0 <= p <= 1.0


# ── adaptive threshold ───────────────────────────────────────────────────
def test_adaptive_threshold_fallback_and_tighten(tmp_path):
    from trader.engine.orchestrator import Orchestrator
    j = _journal(tmp_path)
    o = Orchestrator([], j, base_threshold=0.24, cfg={})
    # no outcome data → static base
    assert abs(o._adaptive_base("RANGING") - 0.24) < 1e-6

    # a losing regime (hit rate 0.19, n=40) → base tightens toward cap
    for i in range(40):
        _seed(j, cid=f"cl{i}", did=f"dl{i}", symbol=f"L{i}/USDT",
              action="SELL", ok=0, votes=[("alpha", -0.5)],
              regime="TRENDING_DOWN")
    o2 = Orchestrator([], j, base_threshold=0.24, cfg={})
    base = o2._adaptive_base("TRENDING_DOWN")
    assert base > 0.30          # punished regime → higher bar
    # untouched regime still falls back
    assert abs(o2._adaptive_base("TRENDING_UP") - 0.24) < 1e-6
    # vol expansion scales modestly
    assert o2._adaptive_base("RANGING", vol_ratio=2.0) > \
        o2._adaptive_base("RANGING", vol_ratio=1.0)
