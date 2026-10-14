"""A control whose required series is absent from the discovery slice.

Measured in production (2026-09-14..10-03): five `funding|open_interest*`
controls were re-requested 21,444 times because open interest begins
2025-10-03 and the 4h discovery slice ends 2025-03-08, so the control could
never trade, was never recorded, and was never "done". These tests pin the
corrected semantics and the ones that must NOT change.
"""
import json

import pytest

from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import control, planner, vocab
from trader.research.combo import Combination
from trader.research.evaluate import _TF_SECONDS
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner
from trader.research.slices import MIN_SLICE_BARS

CUT = 1_741_452_480_000
STEP_MS = _TF_SECONDS["4h"] * 1000
OI_START = CUT + 205 * 86_400_000            # series begins after the cut
CFG = {"risk": {"risk_per_trade_pct": 0.5, "max_open_trades": 8},
       "research": {"enabled": True, "horizons": ["4h"],
                    "geometries": ["trail"], "batch_combos": 5, "beam": 2,
                    "max_parts": 2, "batch_seconds": 60, "nice": 19,
                    "skip_if_cycle_s": 45, "discovery_null_draws": 20}}


@pytest.fixture(autouse=True)
def registered_coverage(monkeypatch):
    from trader.research import universe
    monkeypatch.setattr(universe, "coverage", lambda tfs=None: {
        tf: {s: 11000 for s in universe.DISCOVERY + universe.HELDOUT}
        for tf in (tfs or ["4h"])})


def _row(c, **kw):
    row = {"hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
           "round": c.round, "parent": c.parent, "trigger": c.trigger,
           "parts": list(c.keys), "window": c.window,
           "entry_long": c.long, "entry_short": c.short, "symbols": {},
           "trades": 200, "scored_symbols": 8, "consistency_p": 0.001,
           "median_pf": 1.4, "median_rate": 0.01,
           "portfolio": {"total_pct": 15.0, "max_dd_pct": 20.0, "taken": 90},
           "projection": {"markets_a": 6, "markets_b": 6},
           "testable": True, "verdict": "scored"}
    row.update(kw)
    return row


def _measure(payload):
    return ChildResult(ok=True, elapsed_s=1.0, value={
        "tf": payload["tf"], "cut_ms": CUT,
        "gauges": {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0,
                       "n": 90000, "finite_frac": 0.99, "usable": True}
                   for e in vocab.expressions(payload["tf"])},
        "counts": {"discovery": {"BTC/USDT": 7000},
                   "heldout_a": {"UNI/USDT": 11000},
                   "heldout_b": {"BTC/USDT": 3000}}})


def _absent(first_ms=()):
    return lambda req: {"have": 0, "of": 16, "first_ms": list(first_ms)}


def _world(data_present=False, first_ms=(), cut=CUT):
    """A child where every non-candle requirement is absent from the slice
    (or present, when `data_present`). `state` lets a test move the world."""
    state = {"cut": cut, "present": data_present, "first_ms": list(first_ms),
             "calls": [], "fail": False, "defer_all": False}

    def reply(fn, payload):
        if fn.__name__ == "measure_job":
            return _measure(payload)
        if state["fail"]:
            return ChildResult(ok=False, elapsed_s=1.0, error="boom")
        combos = [Combination.from_dict(d) for d in payload["combos"]]
        state["calls"].append([(c.round, c.window, c.hash) for c in combos])
        reqs = [r for r in payload["requires"]
                if r != "ohlcv" and r != "world"]
        cov = {r: {"have": 16 if state["present"] else 0, "of": 16,
                   "first_ms": state["first_ms"]} for r in reqs}
        if state["defer_all"]:
            return ChildResult(ok=True, elapsed_s=1.0, value={
                "results": [], "done": 0, "skipped": len(combos),
                "loaded_symbols": 16, "cut_ms": state["cut"],
                "coverage": cov, "elapsed_s": 1.0})
        rows = []
        for c in combos:
            if c.round == "control" and c.window != "ohlcv" \
                    and not state["present"]:
                rows.append(_row(c, trades=0, consistency_p=None,
                                 verdict="empty", testable=False))
            else:
                rows.append(_row(c))
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "results": rows, "done": len(rows), "skipped": 0,
            "loaded_symbols": 16, "cut_ms": state["cut"], "coverage": cov,
            "elapsed_s": 1.0})
    return reply, state


def _runner(tmp_path, reply):
    j = Journal(tmp_path / "j.db")
    return ResearchRunner(j, CFG, run=lambda fn, payload, **kw:
                          reply(fn, payload))


def _drive(r, steps=80):
    out = []
    for _ in range(steps):
        o = r.step()
        out.append(o)
        if o.get("skipped") == "idle":
            break
    return out


def _controls(r):
    return {row["window"]: row for row in r.journal.query(
        "SELECT * FROM research_controls WHERE tf='4h'")}


# ── the defect ───────────────────────────────────────────────────────────
def test_impossible_controls_stop_repeating_and_work_proceeds(tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r)
    control_hashes = [h for call in st["calls"] for (rd, _w, h) in call
                      if rd == "control"]
    assert control_hashes, "fixture never asked for a restricted control"
    # every distinct control was asked for exactly once, never again
    assert len(control_hashes) == len(set(control_hashes))
    unavailable = {w: row for w, row in _controls(r).items()
                   if row["status"] == "unavailable"}
    assert unavailable
    # and the independent work behind them was reachable (not starved)
    assert any(rd != "control" for call in st["calls"] for (rd, _w, _h) in call)
    for row in unavailable.values():
        assert row["powered"] == 0 and row["consistency_p"] is None
        ev = json.loads(row["detail"])
        assert ev["reason"] == "required_data_unavailable"
        assert ev["identity"] == control.evidence_identity()


def test_repeated_planning_with_unchanged_input_never_re_requests(tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r)
    before = len(st["calls"])
    led = Ledger(r.journal)
    led.observe_cut("4h", CUT + 30 * 86_400_000)   # cut advances, data absent
    for _ in range(25):
        b = planner.next_batch(led, CFG, r.journal)
        assert b.round != "control" or all(
            c.window == "ohlcv" for c in b.combos)
    assert len(st["calls"]) == before


def test_unavailable_is_never_powered_or_a_no_edge_verdict(tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r)
    rows = r.journal.query(
        "SELECT w.label, w.window FROM research_combos w "
        "WHERE w.window != 'ohlcv'")
    unav = {w for w, c in _controls(r).items() if c["status"] == "unavailable"}
    assert any(x["window"] in unav for x in rows)
    for x in rows:
        if x["window"] in unav:
            assert x["label"] not in ("no_edge",)
    # the error budget was never touched by a calibration control
    assert r.journal.query("SELECT COUNT(*) n FROM research_tests")[0]["n"] == 0
    assert r.journal.query("SELECT COUNT(*) n FROM research_candidates")[0]["n"] == 0


# ── eligibility returns only when the evidence changes ───────────────────
def _unavailable_row(led, usable_from, identity=None):
    led.record_control("4h", "funding|open_interest", {
        "verdict": "unavailable", "consistency_p": None,
        "reason": "required_data_unavailable",
        "unavailable": {"open_interest": {
            "have": 0, "of": 16, "first_ms": [],
            "usable_from_ms": usable_from}},
        "identity": identity or control.evidence_identity()},
        False, status="unavailable")


@pytest.fixture
def led(tmp_path):
    lg = Ledger(Journal(tmp_path / "j.db"))
    lg.ensure()
    return lg


def test_cut_advance_alone_does_not_reopen_while_data_is_unusable(led):
    _unavailable_row(led, usable_from=OI_START + 420 * STEP_MS)
    assert led.control("4h", "funding|open_interest")["status"] == "unavailable"
    led.observe_cut("4h", CUT)
    led.observe_cut("4h", OI_START)                 # series still outside
    c = led.control("4h", "funding|open_interest")
    assert c["status"] == "unavailable" and c["powered"] is False


def test_reopens_when_the_slice_reaches_the_series(led):
    usable = OI_START + 420 * STEP_MS
    _unavailable_row(led, usable_from=usable)
    led.observe_cut("4h", usable - 1)
    assert led.control("4h", "funding|open_interest") is not None
    led.observe_cut("4h", usable)
    assert led.control("4h", "funding|open_interest") is None


def test_a_series_with_no_history_is_not_reopened_by_the_cut(led):
    _unavailable_row(led, usable_from=None)
    led.observe_cut("4h", 4_000_000_000_000)
    assert led.control("4h", "funding|open_interest")["status"] == "unavailable"


def test_reopens_when_the_evaluation_identity_changes(led):
    _unavailable_row(led, usable_from=None,
                     identity={"schema": 0, "min_symbols": 12, "channel": {}})
    assert led.control("4h", "funding|open_interest") is None


def test_reopened_control_that_now_trades_is_recorded_as_measured(tmp_path):
    reply, st = _world(first_ms=[OI_START] * 16)
    r = _runner(tmp_path, reply)
    _drive(r)
    n = len(st["calls"])
    unav = [w for w, c in _controls(r).items() if c["status"] == "unavailable"]
    assert unav
    # the world changes: the series is now inside the slice, long enough
    st.update(present=True,
              cut=OI_START + (MIN_SLICE_BARS + 5) * STEP_MS)
    r.ledger.observe_cut("4h", st["cut"])
    for w in unav:
        assert r.ledger.control("4h", w) is None
    _drive(r)
    for w in unav:
        c = r.ledger.control("4h", w)
        assert c is not None and c["status"] == "measured"
    assert len(st["calls"]) > n


# ── distinct semantics that must be retained ─────────────────────────────
def test_zero_trades_with_data_present_is_still_retried_not_recorded(tmp_path):
    def reply(fn, payload):
        if fn.__name__ == "measure_job":
            return _measure(payload)
        rows = [_row(Combination.from_dict(d), trades=0, consistency_p=None,
                     verdict="empty", testable=False)
                for d in payload["combos"]]
        cov = {x: {"have": 16, "of": 16, "first_ms": [0] * 16}
               for x in payload["requires"] if x != "ohlcv"}
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "results": rows, "done": len(rows), "skipped": 0,
            "loaded_symbols": 16, "cut_ms": CUT, "coverage": cov})
    r = _runner(tmp_path, reply)
    r.step()
    r.step()
    assert _controls(r) == {}


def test_failed_worker_and_deferred_controls_are_not_recorded(tmp_path):
    reply, st = _world()
    r = _runner(tmp_path, reply)
    r.step()                                   # measure
    st["fail"] = True
    out = r.step()
    assert out["ok"] is False and _controls(r) == {}
    st["fail"] = False
    st["defer_all"] = True                     # soft deadline: none reached
    out = r.step()
    assert out["ok"] is True and out["recorded"] == 0 and _controls(r) == {}


def test_an_old_child_without_coverage_keeps_the_previous_behaviour(tmp_path):
    def reply(fn, payload):
        if fn.__name__ == "measure_job":
            return _measure(payload)
        rows = [_row(Combination.from_dict(d), trades=0, consistency_p=None,
                     verdict="empty") for d in payload["combos"]]
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "results": rows, "done": len(rows), "skipped": 0,
            "loaded_symbols": 16})
    r = _runner(tmp_path, reply)
    r.step()
    r.step()
    assert _controls(r) == {}


def test_a_measured_control_is_numerically_unchanged_by_coverage(tmp_path):
    reply, st = _world(data_present=True)
    r = _runner(tmp_path, reply)
    _drive(r)
    rows = _controls(r)
    assert rows
    for row in rows.values():
        assert row["status"] == "measured"
        assert row["consistency_p"] == 0.001 and row["powered"] == 1


# ── the pure rule ────────────────────────────────────────────────────────
def test_unavailable_rule_uses_the_control_floor_and_references():
    cov = {"open_interest": {"have": 11, "of": 16, "first_ms": [5] * 16},
           "ref:vix": {"have": 0, "of": 1, "first_ms": []},
           "funding": {"have": 16, "of": 16, "first_ms": [1] * 16}}
    gap = control.unavailable("funding|open_interest|ref:vix", cov, 14400, 420)
    assert set(gap) == {"open_interest", "ref:vix"}
    assert gap["open_interest"]["usable_from_ms"] == 5 + 420 * 14400 * 1000
    assert gap["ref:vix"]["usable_from_ms"] is None
    cov["open_interest"]["have"] = control.MIN_CONTROL_SYMBOLS
    assert set(control.unavailable("open_interest", cov, 14400, 420) or ()) == set()
    assert control.unavailable("ohlcv", cov, 14400, 420) is None
    assert control.unavailable("funding", None, 14400, 420) is None


def test_child_coverage_reports_slice_counts_and_first_observations():
    from types import SimpleNamespace as NS
    from trader.research import job
    b = NS(market={"vix": [1, 2], "gold": []},
           derivs={"A": {"oi": [1]}, "B": {"oi": []}, "C": {}},
           first_obs={"open_interest": [30, 10], "ref:vix": [7]})
    cov = job._coverage(b, ["ohlcv", "open_interest", "ref:vix", "ref:gold",
                            "funding"])
    assert cov["open_interest"] == {"have": 1, "of": 3, "first_ms": [10, 30]}
    assert cov["ref:vix"] == {"have": 1, "of": 1, "first_ms": [7]}
    assert cov["ref:gold"]["have"] == 0
    assert cov["funding"] == {"have": 0, "of": 3, "first_ms": []}
    assert "ohlcv" not in cov
