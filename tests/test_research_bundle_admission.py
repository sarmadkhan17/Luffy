"""RP-07 phase 1: a child that cannot fill the symbol floor refuses early.

The runner has always refused a bundle that loaded too few symbols, but only
AFTER the child had scored every combination (273 batches, ~14.2 child-hours
on 2026-09-13/14). The floor now travels to the child, which judges the
bundle immediately after `load_bundle` and returns a typed diagnosis without
scoring anything.

Everything here is synthetic: the candle read is stubbed per symbol, so no
store, no provenance table and no held-out price is touched.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import control, evaluate as ev, job, vocab
from trader.research.combo import Combination
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner
from trader.research.vocab import Part

TF_MS = 14_400_000
DONCH = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
SYMS = [f"S{i}/USDT" for i in range(5)]


def _frame(seed: int, bars: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    px, rows = 100.0, []
    for b in range(bars):
        drift = 0.006 if (b // 60) % 2 == 0 else 0.0
        px *= 1.0 + rng.normal(drift, 0.008)
        rows.append((b * TF_MS, px, px * 1.004, px * 0.996, px, 10.0, 5.0))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close",
                                     "volume", "taker_buy"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


@pytest.fixture
def reads(monkeypatch, tmp_path):
    """Stub the candle read; `plan` maps symbol -> behaviour."""
    from trader.data.feed import DataFeed
    plan = {s: "ok" for s in SYMS}

    def cached(self, symbol, tf="15m", limit=20000, *, as_of_ms=None):
        how = plan.get(symbol, "none")
        if how == "ok":
            return _frame(SYMS.index(symbol) + 1)
        if how == "none":            # raw rows exist, nothing replay-eligible
            return None
        if how == "short":
            return _frame(1, bars=120)
        if how == "locked":
            raise sqlite3.OperationalError("database is locked")
        if how == "corrupt":
            raise ValueError("market_revision_content_corrupt")
        raise AssertionError(how)

    monkeypatch.setattr(DataFeed, "cached_ohlcv", cached)
    return plan


@pytest.fixture
def evaluations(monkeypatch):
    calls = []
    real = ev.evaluate

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(ev, "evaluate", counting)
    return calls


def _payload(tmp_path, **kw):
    base = {"tf": "4h", "symbols": list(SYMS), "heldout_symbols": [],
            "requires": ["ohlcv"],
            "cfg": {"risk": {"risk_per_trade_pct": 0.5,
                             "taker_fee_pct": 0.04,
                             "slippage_atr_frac": 0.015,
                             "funding_rate_8h": 0.0001,
                             "real_funding": False, "max_open_trades": 8},
                    "research": {"min_threshold_samples": 100}},
            "paths": {"candles": str(tmp_path / "c.db"),
                      "derivs": str(tmp_path / "d.db")},
            "combos": [Combination((DONCH,), "4h", "trail").as_dict()],
            "draws": 20, "seed": 3, "soft_deadline_s": 300.0}
    base.update(kw)
    return base


def _strip(out):
    out = dict(out)
    out.pop("elapsed_s", None)
    out.pop("admission", None)
    for r in out.get("results", []):
        r.pop("elapsed_s", None)
    return out


# ── the four diagnoses ───────────────────────────────────────────────────
def test_raw_rows_but_no_replay_eligible_history_refuses_without_scoring(
        reads, evaluations, tmp_path):
    for s in SYMS:
        reads[s] = "none"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=3))
    a = out["admission"]
    assert a["admitted"] is False
    assert a["reason"] == job.INSUFFICIENT_REPLAY_HISTORY
    assert (a["loaded_symbols"], a["required_symbols"],
            a["requested_symbols"]) == (0, 3, 5)
    assert a["tf"] == "4h" and "cut_ms" in a
    assert out["loaded_symbols"] == 0 and out["results"] == []
    assert evaluations == []            # zero strategy evaluations
    assert all(d["reason"] == "short_history" for d in a["dropped"].values())


def test_short_history_symbols_are_also_replay_history_failures(
        reads, evaluations, tmp_path):
    for s in SYMS:
        reads[s] = "short"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=3))
    assert out["admission"]["reason"] == job.INSUFFICIENT_REPLAY_HISTORY
    assert evaluations == []


def test_one_symbol_short_of_the_floor_is_a_coverage_refusal(
        reads, evaluations, tmp_path):
    reads[SYMS[4]] = "none"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=5))
    a = out["admission"]
    assert a["reason"] == job.INSUFFICIENT_SYMBOL_COVERAGE
    assert (a["loaded_symbols"], a["required_symbols"]) == (4, 5)
    assert evaluations == []


def test_the_same_bundle_is_admitted_under_the_correct_floor(
        reads, evaluations, tmp_path):
    reads[SYMS[4]] = "none"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=4))
    assert out["admission"] == {**out["admission"], "admitted": True,
                                "reason": job.ADMITTED}
    assert out["done"] == 1 and len(evaluations) == 1


def test_a_request_smaller_than_the_floor_is_a_coverage_refusal(
        reads, evaluations, tmp_path):
    out = job.evaluate_job(_payload(tmp_path, symbols=SYMS[:2],
                                    min_symbols=5))
    assert out["admission"]["reason"] == job.INSUFFICIENT_SYMBOL_COVERAGE
    assert evaluations == []


# ── transient failures are never filed as missing history ────────────────
def test_a_locked_store_is_transient_not_missing_history(
        reads, evaluations, tmp_path):
    reads[SYMS[3]] = "locked"
    reads[SYMS[4]] = "locked"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=5))
    a = out["admission"]
    assert a["reason"] == job.TRANSIENT_READ_FAILURE
    assert a["transient_symbols"] == [SYMS[3], SYMS[4]]
    assert a["dropped"][SYMS[3]]["transient"] is True
    assert evaluations == []


def test_total_lock_out_is_transient_even_with_zero_loaded(
        reads, evaluations, tmp_path):
    for s in SYMS:
        reads[s] = "locked"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=3))
    assert out["admission"]["reason"] == job.TRANSIENT_READ_FAILURE
    assert out["loaded_symbols"] == 0


def test_a_lock_that_could_not_have_met_the_floor_is_not_blamed(
        reads, tmp_path):
    reads[SYMS[0]] = "locked"
    reads[SYMS[1]] = "none"
    reads[SYMS[2]] = "none"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=5))
    # 2 loaded + 1 locked = 3 < 5: even a clean retry cannot admit it
    assert out["admission"]["reason"] == job.INSUFFICIENT_SYMBOL_COVERAGE
    assert out["admission"]["transient_symbols"] == [SYMS[0]]


def test_a_non_transient_read_error_is_its_own_reason(reads, tmp_path):
    reads[SYMS[4]] = "corrupt"
    out = job.evaluate_job(_payload(tmp_path, min_symbols=5))
    a = out["admission"]
    assert a["reason"] == job.READ_ERROR
    assert a["dropped"][SYMS[4]]["transient"] is False


# ── a valid bundle is untouched ──────────────────────────────────────────
def test_a_valid_bundle_gives_the_baseline_result_plus_the_diagnosis(
        reads, tmp_path):
    base = job.evaluate_job(_payload(tmp_path))
    new = job.evaluate_job(_payload(tmp_path, min_symbols=5))
    assert "admission" not in base
    assert new["admission"]["admitted"] is True
    assert new["admission"]["loaded_symbols"] == 5
    assert _strip(new) == _strip(base)


def test_without_a_floor_the_child_behaves_exactly_as_before(
        reads, evaluations, tmp_path):
    for s in SYMS:
        reads[s] = "none"
    out = job.evaluate_job(_payload(tmp_path))     # no min_symbols
    assert out["loaded_symbols"] == 0 and "admission" not in out


def test_the_diagnosis_crosses_the_pipe(reads, tmp_path):
    import json
    reads[SYMS[1]] = "locked"
    json.dumps(job.evaluate_job(_payload(tmp_path, min_symbols=5)))


# ── the runner hands the child the APPLICABLE floor ──────────────────────
CFG = {"risk": {"risk_per_trade_pct": 0.5, "max_open_trades": 8},
       "research": {"enabled": True, "horizons": ["4h"],
                    "geometries": ["trail"], "batch_combos": 3, "beam": 2,
                    "max_parts": 2, "batch_seconds": 60, "nice": 19,
                    "skip_if_cycle_s": 45, "discovery_null_draws": 20}}


@pytest.fixture
def covered(monkeypatch):
    from trader.research import universe
    monkeypatch.setattr(universe, "coverage", lambda tfs=None: {
        tf: {s: 11000 for s in universe.DISCOVERY + universe.HELDOUT}
        for tf in (tfs or ["4h"])})


def _measure(payload):
    return ChildResult(ok=True, elapsed_s=1.0, value={
        "tf": payload["tf"], "cut_ms": 1_700_000_000_000,
        "gauges": {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0,
                       "n": 90000, "finite_frac": 0.99, "usable": True}
                   for e in vocab.expressions(payload["tf"])},
        "counts": {"discovery": {"BTC/USDT": 7000},
                   "heldout_a": {"UNI/USDT": 11000},
                   "heldout_b": {"BTC/USDT": 3000}}})


def _refused(reason):
    def run(fn, payload, timeout_s=0, nice=0, **kw):
        if fn.__name__ == "measure_job":
            return _measure(payload)
        run.payloads.append(payload)
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "results": [], "done": 0, "skipped": 0, "loaded_symbols": 0,
            "cut_ms": 1_700_000_000_000, "coverage": {},
            "admission": {"admitted": False, "reason": reason,
                          "loaded_symbols": 0,
                          "required_symbols": payload["min_symbols"]}})
    run.payloads = []
    return run


def test_the_control_floor_and_the_discovery_floor_stay_distinct(
        tmp_path, covered):
    run = _refused(job.INSUFFICIENT_REPLAY_HISTORY)
    r = ResearchRunner(Journal(tmp_path / "j.db"), CFG, run=run)
    r.step()                                     # measure
    out = r.step()                               # control round
    assert run.payloads[-1]["min_symbols"] == control.MIN_CONTROL_SYMBOLS
    assert out["ok"] is False
    assert out["admission"]["reason"] == job.INSUFFICIENT_REPLAY_HISTORY
    assert "[insufficient_replay_history]" in out["error"]
    assert "MIN_CONTROL_SYMBOLS=12" in out["error"]
    # RP-06 semantics: a refusal records no control and spends nothing
    assert Ledger(r.journal).control("4h", "ohlcv") is None


def test_a_non_control_batch_carries_the_discovery_floor(tmp_path, covered):
    run = _refused(job.TRANSIENT_READ_FAILURE)
    r = ResearchRunner(Journal(tmp_path / "j.db"), CFG, run=run)
    r.step()
    # a non-control batch: drive `_evaluate` directly with a single-round one
    from trader.research.planner import Batch
    c = Combination((DONCH,), "4h", "trail", round="singles")
    out = r._evaluate(Batch(kind="evaluate", tf="4h", geo="trail",
                            round="singles", combos=[c]),
                      ["BTC/USDT"] * 16, [])
    assert run.payloads[-1]["min_symbols"] == 16
    assert out["ok"] is False and "min_discovery_symbols=16" in out["error"]


def test_missing_drop_diagnosis_is_unknown_not_deterministic():
    from types import SimpleNamespace
    bundle = SimpleNamespace(frames={}, requested=("A", "B"), dropped={
        "A": {"reason": "short_history"}}, tf="4h", cut=0)
    result = job.admit_bundle(bundle, 2)
    assert result["unknown_symbols"] == ["B"]
    assert result["deterministic"] is False


def test_short_bundle_skips_auxiliary_loads_and_keeps_typed_refusal(
        reads, tmp_path, monkeypatch):
    from trader.data.references import RefStore
    from trader.data.derivatives import DerivFeed
    for symbol in SYMS:
        reads[symbol] = "none"
    def forbidden(*a, **kw):
        pytest.fail("auxiliary read for already refused candle bundle")
    monkeypatch.setattr(RefStore, "load", forbidden)
    monkeypatch.setattr(DerivFeed, "__init__", forbidden)
    monkeypatch.setattr(ev, "load_world", lambda paths, requires, cut:
                        None if "world" not in requires else forbidden())
    result = job.evaluate_job(_payload(tmp_path, min_symbols=5,
        requires=["ohlcv", "ref:vix", "funding", "world"]))
    assert result["admission"]["reason"] == job.INSUFFICIENT_REPLAY_HISTORY
    assert result["done"] == 0
