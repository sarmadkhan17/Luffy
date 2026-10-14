"""RP-07 phase 2: an identical deterministic refusal is not relaunched.

Everything is synthetic: a throwaway SQLite file stands in for the candle
store (only `market_revisions` and its covering index matter to the stamp),
the child is a counting fake, and the clock is injected — nothing waits.
"""
import os
import sqlite3

import pytest

from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import control, job, refusal, universe, vocab
from trader.research.combo import Combination
from trader.research.ledger import Ledger
from trader.research.planner import Batch
from trader.research.runner import ResearchRunner
from trader.research.vocab import Part

TF = "4h"
STEP = 14_400_000
DONCH = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")


def _store(path, symbols, n=40):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS market_revisions (revision_id "
                "TEXT PRIMARY KEY, series_key TEXT NOT NULL, event_ms "
                "INTEGER NOT NULL, available_ms INTEGER, observed_ms "
                "INTEGER NOT NULL, record_json TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS market_revision_cut ON "
                "market_revisions(series_key, event_ms, available_ms, "
                "observed_ms)")
    for s in symbols:
        k = refusal.series_key(s, TF)
        con.executemany(
            "INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)",
            [(f"{k}-{b}", k, b * STEP, b * STEP + STEP + 5,
              b * STEP + STEP + 9, "{}") for b in range(n)])
    con.commit()
    con.close()


def _exec(path, sql, *a):
    con = sqlite3.connect(path)
    con.execute(sql, a)
    con.commit()
    con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "candles.db")
    inc = control.INCUMBENT_UNIVERSE
    _store(db, list(inc) + list(universe.DISCOVERY))
    monkeypatch.setattr(universe, "coverage", lambda tfs=None: {
        tf: {s: 11000 for s in universe.DISCOVERY + universe.HELDOUT}
        for tf in (tfs or ["4h"])})
    return {"db": db, "tmp": tmp_path}


CFG_BASE = {"risk": {"risk_per_trade_pct": 0.5, "max_open_trades": 8},
            "research": {"enabled": True, "horizons": ["4h"],
                         "geometries": ["trail"], "batch_combos": 3,
                         "beam": 2, "max_parts": 2, "batch_seconds": 60,
                         "nice": 19, "skip_if_cycle_s": 45,
                         "discovery_null_draws": 20}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _refusal(reason="insufficient_replay_history", deterministic=True,
             loaded=0, **extra):
    def make(payload):
        return {"admitted": False, "reason": reason,
                "deterministic": deterministic, "loaded_symbols": loaded,
                "requested_symbols": len(payload["symbols"]),
                "required_symbols": payload["min_symbols"], "tf": TF,
                "cut_ms": 0, "dropped": {}, **extra}
    return make


def _measure(payload):
    return ChildResult(ok=True, elapsed_s=1.0, value={
        "tf": payload["tf"], "cut_ms": 1_700_000_000_000,
        "gauges": {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0,
                       "n": 90000, "finite_frac": 0.99, "usable": True}
                   for e in vocab.expressions(payload["tf"])},
        "counts": {"discovery": {"BTC/USDT": 7000},
                   "heldout_a": {"UNI/USDT": 11000},
                   "heldout_b": {"BTC/USDT": 3000}}})


class Child:
    """A counting fake child. `mode` picks what an evaluate launch returns."""

    def __init__(self):
        self.mode = _refusal()
        self.launches = []

    def __call__(self, fn, payload, timeout_s=0, nice=0, **kw):
        if fn.__name__ == "measure_job":
            return _measure(payload)
        self.launches.append(payload)
        m = self.mode
        if m == "crash":
            return ChildResult(ok=False, elapsed_s=1.0, error="boom")
        if m == "noadm":
            return ChildResult(ok=True, elapsed_s=1.0, value={
                "results": [], "done": 0, "skipped": 0,
                "loaded_symbols": 0})
        if m == "admit":
            return ChildResult(ok=True, elapsed_s=1.0, value={
                "results": [], "done": 0, "skipped": 0,
                "loaded_symbols": payload["min_symbols"], "coverage": {},
                "admission": {"admitted": True, "reason": "admitted",
                              "deterministic": False,
                              "loaded_symbols": payload["min_symbols"],
                              "requested_symbols": len(payload["symbols"]),
                              "required_symbols": payload["min_symbols"]}})
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "results": [], "done": 0, "skipped": 0, "loaded_symbols": 0,
            "coverage": {}, "admission": m(payload)})


@pytest.fixture
def rig(env):
    cfg = {**CFG_BASE, "research": {**CFG_BASE["research"],
                                    "paths": {"candles": env["db"]}}}
    clock, child = Clock(), Child()
    r = ResearchRunner(Journal(env["tmp"] / "j.db"), cfg, run=child,
                       clock=clock)
    r.step()                                     # measure
    return r, child, clock, env


def _rows(r):
    out = {}
    for t in ("research_batches", "research_combos", "research_controls",
              "research_tests", "research_candidates"):
        out[t] = r.journal.query(f"SELECT COUNT(*) n FROM {t}")[0]["n"]
    return out


def _control_batch():
    return Batch(kind="evaluate", tf=TF, geo="trail", round="control",
                 combos=[Combination((DONCH,), TF, "trail", round="control")])


def _single_batch():
    return Batch(kind="evaluate", tf=TF, geo="trail", round="singles",
                 combos=[Combination((DONCH,), TF, "trail",
                                     round="singles")])


# ── suppression ──────────────────────────────────────────────────────────
def test_identical_deterministic_refusal_launches_once(rig):
    r, child, clock, _ = rig
    first = r.step()
    assert first["ok"] is False and len(child.launches) == 1
    before = _rows(r)
    for _ in range(5):
        clock.t += 60
        out = r.step()
        assert out["skipped"] == "bundle_refused_unchanged"
        assert out["reason"] == "insufficient_replay_history"
    assert len(child.launches) == 1
    assert _rows(r) == before          # no ledger writes, no looks, no alpha
    assert r.ledger.tests() == []


def test_a_skipped_step_returns_the_existing_skip_shape(rig):
    r, _, clock, _ = rig
    r.step()
    clock.t += 60
    out = r.step()
    assert "skipped" in out and out["skipped"] not in (None, "idle")
    assert "ok" not in out or out["ok"] is not True


@pytest.mark.parametrize("mode", [
    _refusal("transient_read_failure", deterministic=False),
    _refusal("read_error", deterministic=False),
    _refusal("insufficient_symbol_coverage", deterministic=False, loaded=3),
    "noadm", "crash"])
def test_anything_not_confirmed_deterministic_is_always_relaunched(rig, mode):
    r, child, clock, _ = rig
    child.mode = mode
    for i in range(1, 5):
        clock.t += 60
        r.step()
        assert len(child.launches) == i


def test_a_deterministic_looking_refusal_the_runner_disagrees_with_is_ignored(
        rig):
    r, child, clock, _ = rig
    child.mode = _refusal()
    child.mode_orig = child.mode

    def lying(payload):
        d = _refusal()(payload)
        d["required_symbols"] = payload["min_symbols"] + 1   # wrong floor
        return d
    child.mode = lying
    r.step()
    clock.t += 60
    r.step()
    assert len(child.launches) == 2


def test_the_hardened_classifier_withholds_deterministic_for_unknown_drops():
    class B:
        tf, cut = "4h", 0
        frames = {}
        requested = ("A", "B", "C", "D")
    b = B()
    b.dropped = {"A": {"reason": "short_history", "bars": 3},
                 "B": {"reason": "short_history", "bars": 3},
                 "C": {"reason": "frames_error", "error": "ValueError: x"},
                 "D": {"reason": "short_before_cut", "bars": 1}}
    a = job.admit_bundle(b, 4)
    assert a["reason"] == job.INSUFFICIENT_REPLAY_HISTORY
    assert a["deterministic"] is False and a["unknown_symbols"] == ["C"]
    b.dropped["C"] = {"reason": "short_history", "bars": 0}
    assert job.admit_bundle(b, 4)["deterministic"] is True
    b.dropped["C"] = {"reason": "read_error", "transient": True, "error": ""}
    a = job.admit_bundle(b, 4)       # 0 loaded + 1 transient < floor 4
    assert a["deterministic"] is False and a["transient_symbols"] == ["C"]
    b.dropped["C"] = {"reason": "something_new"}
    assert job.admit_bundle(b, 4)["deterministic"] is False


def test_a_frames_error_can_make_the_floor_reachable_and_is_read_error():
    class B:
        tf, cut = "4h", 0
        frames = {"A": 1, "B": 1, "C": 1}
        requested = ("A", "B", "C", "D")
    b = B()
    b.dropped = {"D": {"reason": "frames_error", "error": "KeyError: x"}}
    a = job.admit_bundle(b, 4)
    assert a["reason"] == job.READ_ERROR and a["deterministic"] is False


# ── reopening ────────────────────────────────────────────────────────────
def _reopens(rig, mutate):
    r, child, clock, env = rig
    r.step()
    assert len(child.launches) == 1
    clock.t += 60
    assert r.step()["skipped"] == "bundle_refused_unchanged"
    mutate(r, env)
    clock.t += refusal.STAMP_CHECK_S + 1      # next throttled stamp check
    r.step()
    return len(child.launches)


def test_an_appended_revision_reopens_admission(rig):
    assert _reopens(rig, lambda r, e: _store(
        e["db"], ["BTC/USDT"], n=41)) == 2


def test_a_pre_cut_backfill_reopens_admission(rig):
    def backfill(r, e):
        k = refusal.series_key("ETH/USDT", TF)
        _exec(e["db"], "INSERT INTO market_revisions VALUES (?,?,?,?,?,?)",
              "bf-1", k, -STEP, -STEP + STEP + 1, 99_999_999, "{}")
    assert _reopens(rig, backfill) == 2


def test_a_revised_receipt_reopens_admission(rig):
    def revise(r, e):
        _exec(e["db"], "UPDATE market_revisions SET observed_ms=observed_ms+7 "
              "WHERE series_key=? AND event_ms=?",
              refusal.series_key("SOL/USDT", TF), 3 * STEP)
    assert _reopens(rig, revise) == 2


def test_a_replaced_store_file_reopens_admission(rig):
    def replace(r, e):
        new = str(e["tmp"] / "new.db")
        _store(new, list(control.INCUMBENT_UNIVERSE)
               + list(universe.DISCOVERY))
        os.replace(new, e["db"])
    assert _reopens(rig, replace) == 2


def test_a_contract_change_reopens_admission(rig, monkeypatch):
    def bump(r, e):
        monkeypatch.setattr(refusal, "CONTRACT", "rp07.refusal.v2")
    assert _reopens(rig, bump) == 2


def test_a_changed_code_identity_reopens_admission(rig, monkeypatch):
    def swap(r, e):
        monkeypatch.setattr(refusal, "code_identity", lambda: "different")
    assert _reopens(rig, swap) == 2


def test_a_changed_frozen_cut_reopens_admission(rig, monkeypatch):
    def move(r, e):
        monkeypatch.setattr(r.ledger, "spent_cut", lambda tf: 1_000_000)
    assert _reopens(rig, move) == 2


def test_a_changed_floor_reopens_admission(rig, monkeypatch):
    def lower(r, e):
        monkeypatch.setattr(control, "MIN_CONTROL_SYMBOLS", 11)
    assert _reopens(rig, lower) == 2


def test_a_changed_symbol_set_reopens_admission(rig, monkeypatch):
    def shrink(r, e):
        monkeypatch.setattr(control, "incumbent_universe",
                            lambda journal=None: list(
                                control.INCUMBENT_UNIVERSE)[:-1])
    assert _reopens(rig, shrink) == 2


def test_an_unreadable_store_never_confirms_unchanged(rig):
    def lose(r, e):
        os.replace(e["db"], e["db"] + ".gone")
    assert _reopens(rig, lose) == 2


def test_stamp_detection_is_throttled_and_the_latency_is_the_check_interval(
        rig):
    r, child, clock, env = rig
    r.step()
    _store(env["db"], ["BTC/USDT"], n=41)          # evidence moves now
    clock.t += refusal.STAMP_CHECK_S - 10          # inside the throttle
    assert r.step()["skipped"] == "bundle_refused_unchanged"
    assert len(child.launches) == 1
    clock.t += 11                                  # check is due
    r.step()
    assert len(child.launches) == 2


def test_metadata_only_change_is_covered_by_the_hourly_probe(rig):
    """A rewrite of `record_json` alone moves no indexed column, so the
    stamp cannot see it; the bounded re-probe is what catches it."""
    r, child, clock, env = rig
    r.step()
    _exec(env["db"], "UPDATE market_revisions SET record_json='{\"q\":1}'")
    clock.t += refusal.STAMP_CHECK_S + 1
    assert r.step()["skipped"] == "bundle_refused_unchanged"
    clock.t += refusal.REPROBE_S
    r.step()
    assert len(child.launches) == 2


# ── bounded re-probe, restart, clearing ─────────────────────────────────
def test_the_hourly_reprobe_uses_the_injected_clock_not_real_time(rig):
    r, child, clock, _ = rig
    r.step()
    t0 = clock.t
    launches = []
    for k in range(1, 130):                    # a 130-minute simulated run
        clock.t = t0 + 60 * k
        r.step()
        launches.append(len(child.launches))
    assert refusal.REPROBE_S < 3600
    # first probe at t0, re-probes no more than ~one per REPROBE_S window
    assert launches[0] == 1
    assert len(child.launches) == 3            # t0, ~55 min, ~110 min
    gaps = [b - a for a, b in zip(launches, launches[1:]) if b > a]
    assert all(g == 1 for g in gaps)
    # the gap between two real launches never exceeds 60 min
    at = [i for i, (a, b) in enumerate(zip([0] + launches, launches), 1)
          if b > a]
    assert all((b - a) * 60 <= 3600 for a, b in zip(at, at[1:]))


def test_a_restart_forgets_suppression_and_probes_once(rig, env=None):
    r, child, clock, e = rig
    r.step()
    clock.t += 60
    assert r.step()["skipped"] == "bundle_refused_unchanged"
    r2 = ResearchRunner(r.journal, r.cfg, run=child, clock=clock)
    clock.t += 60
    out = r2.step()
    assert out.get("skipped") != "bundle_refused_unchanged"
    assert len(child.launches) == 2
    clock.t += 60
    assert r2.step()["skipped"] == "bundle_refused_unchanged"
    assert len(child.launches) == 2


def test_a_successful_admission_clears_the_refusal(rig):
    r, child, clock, _ = rig
    r.step()
    child.mode = "admit"
    clock.t += refusal.REPROBE_S + 1           # probe is due
    r.step()
    assert len(child.launches) == 2 and len(r._refusals) == 0
    child.mode = _refusal()                    # now refused again
    clock.t += 60
    r.step()
    assert len(child.launches) == 3            # nothing was suppressing it


def test_a_crash_on_reprobe_clears_the_entry(rig):
    r, child, clock, _ = rig
    r.step()
    child.mode = "crash"
    clock.t += refusal.REPROBE_S + 1
    r.step()
    assert len(r._refusals) == 0


def test_control_and_discovery_refusals_never_suppress_each_other(rig):
    r, child, clock, _ = rig
    disc = list(universe.DISCOVERY)
    r.step()                                   # control refused + recorded
    assert len(child.launches) == 1
    assert r._suppressed(_control_batch(), disc) is not None
    assert r._suppressed(_single_batch(), disc) is None      # not suppressed
    out = r._evaluate(_single_batch(), disc, [])
    assert out["ok"] is False
    assert child.launches[-1]["min_symbols"] == 16           # discovery floor
    assert len(child.launches) == 2
    assert r._suppressed(_single_batch(), disc) is not None
    assert r._suppressed(_control_batch(), disc) is not None
    # a different discovery symbol set is its own slot
    assert r._suppressed(_single_batch(), disc[:-1]) is None


def test_predictive_experiments_are_never_suppressed(rig):
    r, _, _, _ = rig
    r.step()
    r.predictive_experiment = {"split": {}}
    assert r._suppressed(_control_batch(), list(universe.DISCOVERY)) is None


def test_skipped_steps_do_not_touch_the_stamp_between_checks(rig, monkeypatch):
    r, child, clock, _ = rig
    r.step()
    calls = []
    real = refusal.stamp
    monkeypatch.setattr(refusal, "stamp",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(4):
        clock.t += 60                          # 240 s < STAMP_CHECK_S
        r.step()
    assert calls == []
    clock.t += 100
    r.step()
    assert calls == [1]
