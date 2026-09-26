"""Health observation as a production candidate on the live kernel path.

Two properties the TESTED contract did not prove on its own:

1. The diagnostics side channel is non-authoritative. Any failure while it
   is being filled changes nothing has_decayed returns, nothing the Analyst
   selects and nothing the kernel retires — only the health record, which
   is then refused (evaluation_failed / coverage_unavailable).
2. Kernel._mechanism_once, on a real SQLite journal, persists exactly the
   rows the sweep attempted, after the retirement transaction permits it,
   and never fabricates or backfills a row.
"""
import ast
import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from trader import kernel as kmod
from trader.brain import llm as llm_mod
from trader.brain import spec_writer as writer_mod
from trader.brain.analyst import Analyst
from trader.core.journal import Journal
from trader.strategy import health_observation as ho
from trader.strategy import rolling
from trader.strategy.compile import compile_spec

from tests.test_strategy_health_observation import (CFG, DOWN, RARE, UP,
                                                    _Journal, _analyst,
                                                    _frame, _review, _spec,
                                                    _strip_ts)

BROKEN = DOWN.drop(columns=["high"])          # raises inside evaluation
MIXED = {"BTC/USDT": DOWN, "ETH/USDT": BROKEN, "_btc_1h": DOWN}


def _canon(result) -> str:
    """Byte-level form of an authoritative result (floats by repr)."""
    return json.dumps(result, default=repr, allow_nan=True)


# ── 1. diagnostics failure is not authoritative ─────────────────────────
class _RaisingDict(dict):
    """A diagnostics target whose every mutation raises."""
    def _boom(self, *a, **k):
        raise RuntimeError("diagnostics sink broken")
    clear = update = __setitem__ = setdefault = _boom


class _BranchRaises(dict):
    """Fills normally, then refuses the branch."""
    def __setitem__(self, k, v):
        if k == "branch":
            raise RuntimeError("branch sink broken")
        super().__setitem__(k, v)


def _boom(*a, **k):
    raise RuntimeError("injected diagnostics failure")


#: every place the side channel is written, each made to fail on its own
INJECTIONS = ["_d_open", "_d_frame", "_d_errored", "_d_scored", "_d_close",
              "_d_branch", "_diag_error"]


def _decay(frames, diagnostics=None, spec=None):
    c = compile_spec(spec or _spec())
    kw = {} if diagnostics is None else {"diagnostics": diagnostics}
    return rolling.has_decayed(c, frames, CFG["risk"], "1h", recent_days=60,
                               min_trades=3, floor_pf=0.85, **kw)


@pytest.mark.parametrize("frames", [MIXED, {"A": UP}, {"A": _frame(500)}],
                         ids=["decayed+errored", "still_working", "short"])
@pytest.mark.parametrize("point", INJECTIONS)
def test_injected_diagnostics_failure_leaves_decay_result_identical(
        monkeypatch, frames, point):
    plain = _decay(frames)
    ok_diag = {}
    assert _decay(frames, ok_diag) == plain
    assert _canon(_decay(frames, {})) == _canon(plain)
    calls = []

    def boom(*a, **k):
        calls.append(point)
        raise RuntimeError("injected diagnostics failure")
    monkeypatch.setattr(rolling, point, boom)
    failed = {}
    got = _decay(frames, failed)
    assert got == plain and _canon(got) == _canon(plain)
    if calls:
        # the window is refused whole, never handed on half-built
        assert rolling.DIAGNOSTICS_FAILED in failed
        assert "scored" not in failed and "attempted" not in failed
    else:
        assert failed.keys() == ok_diag.keys()


@pytest.mark.parametrize("sink", [_RaisingDict, _BranchRaises])
def test_broken_diagnostics_target_leaves_decay_result_identical(sink):
    for frames in (MIXED, {"A": UP}, {"A": _frame(500)}):
        plain = _decay(frames)
        got = _decay(frames, sink())
        assert got == plain and _canon(got) == _canon(plain)


def test_genuine_scoring_exception_still_raises_identically():
    """Guarding the side channel must not swallow real evaluation errors."""
    c = compile_spec(_spec())

    def derivs_for(sym):
        raise RuntimeError("derivatives store down")
    raised = []
    for diag in (None, {}, _RaisingDict()):
        kw = {} if diag is None else {"diagnostics": diag}
        with pytest.raises(RuntimeError) as ei:
            rolling.has_decayed(c, {"A": DOWN}, CFG["risk"], "1h",
                                recent_days=60, min_trades=3,
                                derivs_for=derivs_for, **kw)
        raised.append((type(ei.value), str(ei.value)))
    assert raised == [(RuntimeError, "derivatives store down")] * 3


def test_per_symbol_evaluation_error_is_swallowed_exactly_as_before(
        monkeypatch):
    """A per-symbol failure was logged and skipped before telemetry
    existed; a failing diagnostics write in that handler must not turn it
    into a raise."""
    plain = _decay(MIXED)
    monkeypatch.setattr(rolling, "_diag_error", _boom)
    assert _decay(MIXED, {}) == plain


SPECS3 = lambda: [_spec("d1"), _spec("d2", entry=RARE), _spec("d3")]


@pytest.mark.parametrize("point", INJECTIONS)
def test_analyst_selection_identical_and_record_refused(monkeypatch, point):
    base = _analyst(MIXED)
    base_actions = _review(base, SPECS3())
    base_decayed = _strip_ts([e for e in base.journal.events
                              if e[0] == "spec_decayed"])
    monkeypatch.setattr(rolling, point, _boom)
    a = _analyst(MIXED)
    assert _canon(_review(a, SPECS3())) == _canon(base_actions)
    assert _strip_ts([e for e in a.journal.events
                      if e[0] == "spec_decayed"]) == base_decayed
    obs = a.journal.of(ho.KIND_SPEC)
    [sw] = a.journal.of(ho.KIND_SWEEP)
    # telemetry never aborts the sweep
    assert sw["status"] == ho.SWEEP_COMPLETED
    assert sw["observation_recorded_spec_ids"] == ["d1", "d2", "d3"]
    base_obs = {o["spec_id"]: o for o in base.journal.of(ho.KIND_SPEC)}
    for o in obs:
        assert o["retirement_action_selected"] == \
            base_obs[o["spec_id"]]["retirement_action_selected"]
        assert o["verdict"] == ho.EVALUATION_FAILED
        assert o["verdict_reason"] == ho.COVERAGE_UNAVAILABLE
        assert o["error"] is None                   # evaluated variant
        c = o["coverage"]
        assert c["coverage_complete"] is False and c["coverage_faults"] == []
        assert c["diagnostics_error"]["stage"] == "diagnostics"
        assert c["attempted_symbols"] is None       # unknown, not "none"
        if point != "_d_branch":
            assert o["lifecycle_branch"] == \
                base_obs[o["spec_id"]]["lifecycle_branch"]


def test_failed_diagnostics_marker_matches_health_contract():
    assert ho.DIAGNOSTICS_FAILED == rolling.DIAGNOSTICS_FAILED


# ── 2. kernel-level wiring on a real journal ────────────────────────────
class _StubWriter:
    def __init__(self, *_a, **_k):
        pass

    def write(self, **_k):
        return None, {"stub": "no LLM in tests"}


def _kernel(tmp_path, monkeypatch, frames):
    monkeypatch.setattr(Analyst, "frames",
                        lambda self, tf, extra=(): frames)
    monkeypatch.setattr(writer_mod, "SpecWriter", _StubWriter)
    monkeypatch.setattr(llm_mod, "BrainLLM", lambda cfg: None)
    k = kmod.Kernel.__new__(kmod.Kernel)
    k.cfg = {**CFG, "research": {"handoff": False}}
    k.journal = Journal(str(tmp_path / "luffy.db"))
    k.feed = None
    k.notifier = None
    k.population = []
    k._load_population = lambda: []
    k._strategist_knowledge = lambda: {}
    return k


def _install(j, specs, state="paper"):
    for s in specs:
        j.upsert_spec(s, state=state)


def _events(db):
    conn = sqlite3.connect(db)
    try:
        return [(r[0], r[1], json.loads(r[2]) if r[2] else None) for r in
                conn.execute("SELECT kind, subject, detail FROM brain_events "
                             "ORDER BY id")]
    finally:
        conn.close()


def _states(db):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT id, state FROM strategies"))
    finally:
        conn.close()


def _health(db):
    return [e for e in _events(db) if e[0] in (ho.KIND_SPEC, ho.KIND_SWEEP)]


def _disable_health(monkeypatch):
    monkeypatch.setattr(ho, "open_sweep", lambda *a, **k: ho._Disabled())


def test_normal_mechanism_cycle_persists_exact_rows(tmp_path, monkeypatch):
    k = _kernel(tmp_path, monkeypatch, {"BTC/USDT": UP, "_btc_1h": UP})
    _install(k.journal, [_spec("p1"), _spec("p2", entry=RARE)], "paper")
    _install(k.journal, [_spec("a1")], "active")
    rep = k._mechanism_once(per_cycle=1, max_book=3)     # book full: no LLM
    assert rep["retired"] == [] and rep["added"] == []
    db = str(tmp_path / "luffy.db")
    rows = _health(db)
    assert [r[0] for r in rows] == [ho.KIND_SPEC] * 3 + [ho.KIND_SWEEP]
    obs = {r[1]: r[2] for r in rows[:3]}
    sw = rows[3][2]
    ids = [s.id for _r, s in k.journal.list_specs(["paper", "active"])]
    assert sorted(obs) == sorted(ids)
    assert sw["intended_spec_ids"] == ids
    assert sw["evaluation_attempted_spec_ids"] == ids
    assert sw["evaluation_completed_spec_ids"] == ids
    assert sw["observation_recorded_spec_ids"] == ids
    assert sw["observation_failed_spec_ids"] == []
    assert sw["compile_failed_spec_ids"] == []
    assert sw["not_evaluated_spec_ids"] == []
    assert sw["status"] == ho.SWEEP_COMPLETED
    assert sw["n_retirement_actions_selected"] == 0
    assert {o["sweep_id"] for o in obs.values()} == {sw["sweep_id"]}
    assert obs["p1"]["verdict"] == ho.STILL_WORKING
    assert obs["p2"]["verdict"] == ho.IDLE
    # the rows round-trip through the read path
    h = ho.history_journal(k.journal)
    assert h["invalid_records"] == [] and h["sweeps_without_record"] == []
    assert not k.journal._conn().in_transaction


def _retire_cycle(tmp_path, monkeypatch, max_book, health=True):
    if not health:
        _disable_health(monkeypatch)
    k = _kernel(tmp_path, monkeypatch, {"BTC/USDT": DOWN, "_btc_1h": DOWN})
    _install(k.journal, [_spec("r1"), _spec("q2", entry=RARE),
                         _spec("r3")], "paper")
    rep = k._mechanism_once(per_cycle=1, max_book=max_book)
    return k, rep


def test_retirement_identical_and_flushed_after_commit(tmp_path,
                                                       monkeypatch):
    """Retirement leaves room: the replacement step's own write commits
    the retirement UPDATE, and only then are health rows written."""
    base_k, base_rep = _retire_cycle(tmp_path / "off", monkeypatch, 8,
                                     health=False)
    monkeypatch.undo()
    k, rep = _retire_cycle(tmp_path / "on", monkeypatch, 8)
    assert rep == base_rep and rep["retired"] == ["Health Probe"] * 2
    on, off = str(tmp_path / "on" / "luffy.db"), \
        str(tmp_path / "off" / "luffy.db")
    assert _states(on) == _states(off) == {"r1": "retired", "q2": "paper",
                                           "r3": "retired"}
    kinds = [e[0] for e in _events(on)]
    assert [e[0] for e in _events(off)] == [
        "spec_decayed", "spec_decayed", "spec_write_failed"]
    # authoritative events first, in the same order; health strictly after
    # the write that committed the retirement
    assert kinds == ["spec_decayed", "spec_decayed", "spec_write_failed",
                     ho.KIND_SPEC, ho.KIND_SPEC, ho.KIND_SPEC,
                     ho.KIND_SWEEP]
    sw = _events(on)[-1][2]
    assert sw["n_retirement_actions_selected"] == 2
    obs = {e[1]: e[2] for e in _events(on) if e[0] == ho.KIND_SPEC}
    assert {s: o["retirement_action_selected"] for s, o in obs.items()} == {
        "r1": True, "q2": False, "r3": True}


def test_retirement_left_uncommitted_defers_and_loses_telemetry(
        tmp_path, monkeypatch):
    """Book still full after retirement: nothing commits the kernel's
    UPDATEs this cycle. Telemetry must not commit (or roll back) them for
    it: no health row is written, and the transaction state is exactly
    what it is with telemetry off."""
    base_k, base_rep = _retire_cycle(tmp_path / "off", monkeypatch, 1,
                                     health=False)
    base_tx = base_k.journal._conn().in_transaction
    base_k.journal._conn().rollback()          # as a process death would
    monkeypatch.undo()
    k, rep = _retire_cycle(tmp_path / "on", monkeypatch, 1)
    assert rep == base_rep
    assert k.journal._conn().in_transaction is base_tx is True
    on = str(tmp_path / "on" / "luffy.db")
    # another connection sees only committed state: no health rows, and the
    # retirement not yet durable — the same as without telemetry
    assert _health(on) == []
    assert _states(on) == {"r1": "paper", "q2": "paper", "r3": "paper"}
    k.journal._conn().commit()                 # the kernel's later commit
    assert _states(on) == {"r1": "retired", "q2": "paper", "r3": "retired"}
    assert _health(on) == []                   # lost, never backfilled


def test_crash_between_cycles_keeps_durable_rows_and_backfills_nothing(
        tmp_path, monkeypatch):
    k = _kernel(tmp_path, monkeypatch, {"BTC/USDT": UP, "_btc_1h": UP})
    _install(k.journal, [_spec("s1"), _spec("s2")], "paper")
    k._mechanism_once(per_cycle=1, max_book=2)
    db = str(tmp_path / "luffy.db")
    first = _health(db)
    assert len(first) == 3

    # second cycle: the process dies after the sweep, before any flush
    real = Analyst.flush_health_observations

    def die(self):
        raise SystemExit("killed mid-cycle")
    monkeypatch.setattr(Analyst, "flush_health_observations", die)
    with pytest.raises(SystemExit):
        k._mechanism_once(per_cycle=1, max_book=2)
    k.journal._conn().close()                  # uncommitted state is gone
    monkeypatch.setattr(Analyst, "flush_health_observations", real)

    # restart: a fresh journal on the same file
    k2 = _kernel(tmp_path, monkeypatch, {"BTC/USDT": UP, "_btc_1h": UP})
    assert _health(db) == first                # durable rows untouched
    k2._mechanism_once(per_cycle=1, max_book=2)
    rows = _health(db)
    assert rows[:3] == first
    assert len(rows) == 6                      # one new sweep, no backfill
    h = ho.history_journal(k2.journal)
    assert len(h["sweeps"]) == 2 and h["sweeps_without_record"] == []
    assert len({o["sweep_id"] for s in h["specs"]
                for o in s["observations"]}) == 2


def test_evaluation_exception_propagates_and_aborted_sweep_is_recorded(
        tmp_path, monkeypatch):
    def run(sub, health):
        if not health:
            _disable_health(monkeypatch)
        k = _kernel(tmp_path / sub, monkeypatch,
                    {"BTC/USDT": UP, "_btc_1h": UP})
        _install(k.journal, [_spec("s1"), _spec("s2"), _spec("s3")])
        real = Analyst._ctx

        def ctx(self, tf, spec):
            if spec.id == "s2":
                raise RuntimeError("feed down")
            return real(self, tf, spec)
        monkeypatch.setattr(Analyst, "_ctx", ctx)
        with pytest.raises(RuntimeError) as ei:
            k._mechanism_once(per_cycle=1, max_book=3)
        monkeypatch.undo()
        return k, (type(ei.value), str(ei.value))

    _k0, off = run("off", False)
    k, on = run("on", True)
    assert on == off == (RuntimeError, "feed down")
    db, db0 = str(tmp_path / "on" / "luffy.db"), \
        str(tmp_path / "off" / "luffy.db")
    assert _states(db) == _states(db0)
    assert _health(db0) == []
    rows = _health(db)
    obs = {r[1]: r[2] for r in rows if r[0] == ho.KIND_SPEC}
    [sw] = [r[2] for r in rows if r[0] == ho.KIND_SWEEP]
    assert obs["s1"]["verdict"] == ho.STILL_WORKING
    assert obs["s2"]["verdict_reason"] == ho.EVALUATION_EXCEPTION
    assert obs["s2"]["error"] == {"stage": "context",
                                  "error_class": "RuntimeError",
                                  "message": "feed down"}
    assert "s3" not in obs                     # never evaluated, not invented
    assert sw["status"] == ho.SWEEP_ABORTED
    assert sw["not_evaluated_spec_ids"] == ["s3"]
    assert sw["aborted_at_spec_id"] == "s2"


def test_compile_failure_recorded_and_skip_unchanged(tmp_path, monkeypatch):
    def run(sub, health):
        if not health:
            _disable_health(monkeypatch)
        k = _kernel(tmp_path / sub, monkeypatch,
                    {"BTC/USDT": DOWN, "_btc_1h": DOWN})
        _install(k.journal, [_spec("c1", entry="close >>> nope("),
                             _spec("r2")])
        rep = k._mechanism_once(per_cycle=1, max_book=8)
        monkeypatch.undo()
        return rep

    assert run("on", True) == run("off", False)
    db, db0 = str(tmp_path / "on" / "luffy.db"), \
        str(tmp_path / "off" / "luffy.db")
    assert _states(db) == _states(db0) == {"c1": "paper", "r2": "retired"}
    obs = {r[1]: r[2] for r in _health(db) if r[0] == ho.KIND_SPEC}
    [sw] = [r[2] for r in _health(db) if r[0] == ho.KIND_SWEEP]
    assert obs["c1"]["verdict"] == ho.COMPILE_FAILED
    assert obs["c1"]["error"]["stage"] == "compile"
    assert "coverage" not in obs["c1"]
    assert obs["r2"]["verdict"] == ho.DECAYED
    assert sw["compile_failed_spec_ids"] == ["c1"]
    assert sw["evaluation_completed_spec_ids"] == ["r2"]
    assert sw["status"] == ho.SWEEP_COMPLETED


# ── 3. no trading authority reachable from the package ──────────────────
_FORBIDDEN = ("engine", "execution", "exchange", "risk", "protective",
              "supervisor", "orchestrator", "executor", "control")


def test_health_observation_imports_no_trading_module():
    tree = ast.parse(Path(inspect.getsourcefile(ho)).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.add("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert mods == {"__future__", "hashlib", "json", "logging", "math",
                    "uuid", "dataclasses", "datetime", "pathlib",
                    "..core.types"}
    for m in mods:
        assert not any(w in m for w in _FORBIDDEN)


def test_inlined_signal_fingerprint_matches_signal_occurrence_contract():
    """Pinned to signal_occurrence.spec_fingerprint(_spec()) as computed
    in a b3639d8 checkout."""
    assert ho.SIGNAL_FINGERPRINT_SCHEMA == \
        "strategy-spec-signal-fingerprint.v1"
    assert ho.SIGNAL_FINGERPRINT_FIELDS == (
        "entry_long", "entry_short", "filters", "timeframe", "universe",
        "direction")
    assert ho.signal_fingerprint(_spec()) == (
        "c0bbe5260cf5fd5ab6eb6695b62158bbbffd2e44b4a91c1f4bb2d088bf3416b2")
