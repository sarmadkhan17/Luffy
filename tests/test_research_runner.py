"""One step of the loop: plan, hand the batch to a child, write what comes
back, decide what it means.

The thread owns scheduling and writing; the child owns the arithmetic. Three
failure modes are handled here rather than in the child, because the child
may not survive them: a batch that times out, a batch that crashes, and a
trade cycle running slow enough that the search should stand down.
"""
from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import vocab
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner

CFG = {"risk": {"risk_per_trade_pct": 0.5, "max_open_trades": 8},
       "research": {"enabled": True, "horizons": ["4h"],
                    "geometries": ["trail"], "batch_combos": 3, "beam": 2,
                    "max_parts": 2, "batch_seconds": 60, "nice": 19,
                    "skip_if_cycle_s": 45,
                    "discovery_null_draws": 20}}


def _runner(tmp_path, reply):
    j = Journal(tmp_path / "j.db")
    calls = []

    def fake_run(fn, payload, timeout_s=0, nice=0, **kw):
        calls.append({"fn": fn.__name__, "payload": payload,
                      "timeout_s": timeout_s, "nice": nice})
        return reply(fn, payload)

    r = ResearchRunner(j, CFG, run=fake_run)
    return r, calls


def _measured(fn, payload):
    if fn.__name__ == "measure_job":
        exprs = vocab.expressions(payload["tf"])
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "tf": payload["tf"], "cut_ms": 1_700_000_000_000,
            "gauges": {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0,
                           "n": 90000, "finite_frac": 0.99, "usable": True}
                       for e in exprs},
            "counts": {"discovery": {"BTC/USDT": 7000},
                       "heldout_a": {"UNI/USDT": 11000},
                       "heldout_b": {"BTC/USDT": 3000}}})
    results = []
    for d in payload["combos"]:
        from trader.research.combo import Combination
        c = Combination.from_dict(d)
        results.append({
            "hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
            "round": c.round, "parent": c.parent, "trigger": c.trigger,
            "parts": list(c.keys), "window": c.window,
            "entry_long": c.long, "entry_short": c.short, "symbols": {},
            "trades": 200, "scored_symbols": 8, "consistency_p": 0.001,
            "median_pf": 1.4, "median_rate": 0.01,
            "portfolio": {"total_pct": 15.0, "max_dd_pct": 20.0,
                          "taken": 90},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": "scored"})
    return ChildResult(ok=True, elapsed_s=5.0, value={
        "results": results, "done": len(results), "skipped": 0,
        "elapsed_s": 5.0})


def test_a_disabled_search_does_nothing(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    r.cfg = {**CFG, "research": {**CFG["research"], "enabled": False}}
    assert r.step()["skipped"] == "disabled"
    assert calls == []


def test_a_slow_trade_cycle_stands_the_search_down(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    out = r.step(cycle_seconds=60.0)
    assert out["skipped"] == "busy"
    assert calls == []


def test_a_normal_cycle_does_not_stand_it_down(tmp_path):
    r, _ = _runner(tmp_path, _measured)
    assert r.step(cycle_seconds=17.7)["skipped"] != "busy"


def test_the_first_step_measures_and_records_the_thresholds(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    out = r.step()
    assert out["kind"] == "measure"
    assert calls[0]["fn"] == "measure_job"
    assert calls[0]["nice"] == 19
    led = Ledger(r.journal)
    assert led.gauges("4h")
    assert led.slices("4h")["cut_ms"] == 1_700_000_000_000


def test_results_are_written_with_a_verdict_and_a_reason(tmp_path):
    r, _ = _runner(tmp_path, _measured)
    r.step()                         # measure
    r.step()                         # control
    out = r.step()                   # singles
    led = Ledger(r.journal)
    rows = led.rows("4h", "trail", k=1)
    assert rows
    assert rows[0]["verdict"] in ("grow", "prune", "survivor")
    assert rows[0]["reason"]
    assert out["recorded"] == len(rows) or out["recorded"] > 0


def test_a_successful_evaluate_step_separates_skipped_from_deferred(
        tmp_path):
    """`"skipped"` is always a skip REASON (or None when the step ran) and
    `"deferred"` is always the child's own count of combos it did not
    finish — a consumer must never have to tell them apart by type."""
    r, _ = _runner(tmp_path, _measured)
    r.step()                         # measure
    out = r.step()                   # control — an evaluate step
    assert out["kind"] == "evaluate"
    assert out["skipped"] is None
    assert isinstance(out["deferred"], int) and out["deferred"] == 0


def test_a_stood_down_step_exposes_only_the_reason_string(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    disabled = {**CFG, "research": {**CFG["research"], "enabled": False}}
    r.cfg = disabled
    assert r.step()["skipped"] == "disabled"
    r.cfg = CFG
    assert r.step(cycle_seconds=60.0)["skipped"] == "busy"
    assert calls == []


def test_a_control_batch_records_the_window_s_power(tmp_path):
    r, _ = _runner(tmp_path, _measured)
    r.step()
    out = r.step()
    assert out["round"] == "control"
    c = Ledger(r.journal).control("4h", "ohlcv")
    assert c is not None and c["powered"] is True


def test_a_child_timeout_marks_the_batch_failed_and_raises_nothing(tmp_path):
    def timeout(fn, payload):
        return ChildResult(ok=False, timed_out=True, elapsed_s=61.0,
                           error="timed out after 60s")

    r, _ = _runner(tmp_path, timeout)
    out = r.step()
    assert out["ok"] is False and "timed out" in out["error"]
    rows = r.journal.query("SELECT * FROM research_batches")
    assert rows and rows[0]["ok"] == 0


def test_a_child_crash_is_recorded_not_raised(tmp_path):
    def boom(fn, payload):
        return ChildResult(ok=False, error="Traceback ... ValueError: x")

    r, _ = _runner(tmp_path, boom)
    out = r.step()
    assert out["ok"] is False
    assert "ValueError" in out["error"]


def test_the_batch_carries_the_horizon_s_symbols_to_the_child(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    r.step()
    payload = calls[0]["payload"]
    assert "BTC/USDT" in payload["symbols"]
    assert payload["heldout_symbols"]
    assert payload["tf"] == "4h"


def test_a_control_batch_runs_on_the_incumbent_universe(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    r.step()
    r.step()
    payload = calls[-1]["payload"]
    from trader.research.control import INCUMBENT_UNIVERSE
    assert set(payload["symbols"]) == set(INCUMBENT_UNIVERSE)


def test_the_kernel_records_how_long_its_cycle_took():
    import inspect

    from trader import kernel as K
    src = inspect.getsource(K.Kernel.run)
    assert "_last_cycle_s" in src


def test_the_kernel_updates_last_cycle_s_even_when_the_cycle_raises():
    """A cycle that raises must not leave the previous (possibly fast)
    value in `_last_cycle_s` in place — the research thread reads it to
    stand down while the kernel is busy, and a kernel that is slow AND
    failing would otherwise never register as busy."""
    import inspect

    from trader import kernel as K
    src = inspect.getsource(K.Kernel.run)
    finally_idx = src.index("finally:")
    assign_idx = src.index("self._last_cycle_s =")
    funding_idx = src.index("self._funding_cache = None")
    assert finally_idx < assign_idx < funding_idx


def test_the_research_loop_builds_its_runner_inside_the_retry_loop():
    """`ResearchRunner(...)` calls `Ledger.ensure()`. Building it BEFORE
    `while not self._stop:` would let one failure there (a locked database
    at boot) kill the thread silently for the rest of the process's life —
    it must be inside the loop's own try/except so a transient failure is
    just another skipped cycle."""
    import inspect

    from trader import kernel as K
    src = inspect.getsource(K.Kernel._research_loop)
    while_idx = src.index("while not self._stop:")
    ctor_idx = src.index("runner = ResearchRunner(")
    assert ctor_idx > while_idx


def test_the_kernel_spawns_the_research_thread_only_when_enabled():
    import inspect

    from trader import kernel as K
    src = inspect.getsource(K.Kernel.boot)
    assert 'name="research"' in src
    assert 'cfg.get("research"' in src


def test_discovery_null_draws_is_clamped_to_the_null_baseline_floor(
        tmp_path, caplog):
    """A hand-edited config below `null_baseline.MIN_DRAWS` (20) must not
    silently make every combination read `untestable` while still reporting
    real trades — clamp to the floor and say so loudly, do not refuse to
    run the loop over a config typo."""
    from trader.strategy.null_baseline import MIN_DRAWS

    cfg = {**CFG, "research": {**CFG["research"], "discovery_null_draws": 5}}
    r = ResearchRunner(Journal(tmp_path / "j.db"), cfg, run=None)
    with caplog.at_level("WARNING"):
        n = r._null_draws()
    assert n == MIN_DRAWS
    assert any("discovery_null_draws" in m for m in caplog.messages)


def test_symbols_returns_nothing_when_coverage_is_unavailable(
        monkeypatch, tmp_path):
    """An empty or failed coverage lookup must never fall back to 'use
    every configured symbol' — that silently disabled the
    `min_discovery_symbols` guard it was supposed to feed."""
    import trader.research.universe as universe_mod

    monkeypatch.setattr(universe_mod, "coverage",
                        lambda tfs=None, feed=None:
                        {"skipped": "coverage_unavailable"})
    r, _ = _runner(tmp_path, _measured)
    disc, held = r._symbols("4h")
    assert disc == [] and held == []


def _control_result(verdict, error=None):
    def reply(fn, payload):
        if fn.__name__ == "measure_job":
            return _measured(fn, payload)
        from trader.research.combo import Combination
        results = []
        for d in payload["combos"]:
            c = Combination.from_dict(d)
            row = {"hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
                   "round": c.round, "parent": c.parent,
                   "trigger": c.trigger, "parts": list(c.keys),
                   "window": c.window, "entry_long": c.long,
                   "entry_short": c.short, "symbols": {}, "trades": 0,
                   "verdict": verdict, "consistency_p": None,
                   "median_pf": 0.0,
                   "portfolio": {"total_pct": 0.0, "max_dd_pct": 0.0,
                                "taken": 0}}
            if error:
                row["error"] = error
            results.append(row)
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "results": results, "done": len(results), "skipped": 0,
            "elapsed_s": 1.0})
    return reply


def test_a_crashed_control_is_left_uncontrolled_for_retry(tmp_path, caplog):
    r, _ = _runner(tmp_path, _control_result("error", error="boom"))
    r.step()                             # measure
    with caplog.at_level("WARNING"):
        out = r.step()                   # control — evaluation raised
    assert out["round"] == "control"
    assert Ledger(r.journal).control("4h", "ohlcv") is None
    assert any("could not be evaluated" in m for m in caplog.messages)


def test_an_empty_control_is_left_uncontrolled_for_retry(tmp_path):
    r, _ = _runner(tmp_path, _control_result("empty"))
    r.step()                             # measure
    r.step()                             # control — took no trades
    assert Ledger(r.journal).control("4h", "ohlcv") is None


def test_an_untestable_control_is_recorded_under_its_own_status(tmp_path):
    r, _ = _runner(tmp_path, _control_result("untestable"))
    r.step()                             # measure
    r.step()                             # control — too few symbols scored
    c = Ledger(r.journal).control("4h", "ohlcv")
    assert c is not None
    assert c["status"] == "untestable" and c["powered"] is False


def _short_bundle(fn, payload):
    if fn.__name__ == "measure_job":
        return _measured(fn, payload)
    from trader.research.combo import Combination
    results = []
    for d in payload["combos"]:
        c = Combination.from_dict(d)
        results.append({
            "hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
            "round": c.round, "parent": c.parent, "trigger": c.trigger,
            "parts": list(c.keys), "window": c.window,
            "entry_long": c.long, "entry_short": c.short, "symbols": {},
            "trades": 200, "scored_symbols": 8, "consistency_p": 0.001,
            "median_pf": 1.4, "median_rate": 0.01,
            "portfolio": {"total_pct": 15.0, "max_dd_pct": 20.0,
                          "taken": 90},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": "scored"})
    return ChildResult(ok=True, elapsed_s=5.0, value={
        "results": results, "done": len(results), "skipped": 0,
        "loaded_symbols": 3, "elapsed_s": 5.0})


def test_a_batch_is_refused_when_the_bundle_loaded_too_few_symbols(
        tmp_path):
    """The guard reads the BUNDLE's own count, not the coverage-derived
    request size — a bundle that silently drops symbols must not have its
    degraded results recorded as if the full universe had been searched."""
    r, _ = _runner(tmp_path, _short_bundle)
    r.step()                             # measure
    out = r.step()                       # control — bundle undercounts
    assert out["ok"] is False
    assert "loaded" in out["error"]
    assert Ledger(r.journal).control("4h", "ohlcv") is None


def test_the_label_gate_uses_the_configured_control_max_p(tmp_path):
    """`control.label()` used to hardcode 0.01 while `powered()` read
    `control_max_p` from config — the two halves of one gate would then
    disagree whenever the config value differed from the default."""
    cfg = {**CFG, "research": {**CFG["research"], "control_max_p": 0.5}}

    def reply(fn, payload, timeout_s=0, nice=0, **kw):
        if fn.__name__ == "measure_job":
            return _measured(fn, payload)
        from trader.research.combo import Combination
        results = []
        for d in payload["combos"]:
            c = Combination.from_dict(d)
            results.append({
                "hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
                "round": c.round, "parent": c.parent, "trigger": c.trigger,
                "parts": list(c.keys), "window": c.window,
                "entry_long": c.long, "entry_short": c.short,
                "symbols": {}, "trades": 200, "scored_symbols": 8,
                "consistency_p": 0.3, "median_pf": 1.1, "median_rate": 0.01,
                "portfolio": {"total_pct": 5.0, "max_dd_pct": 20.0,
                             "taken": 90},
                "projection": {"markets_a": 6, "markets_b": 6},
                "testable": True, "verdict": "scored"})
        return ChildResult(ok=True, elapsed_s=5.0, value={
            "results": results, "done": len(results), "skipped": 0,
            "elapsed_s": 5.0})

    j = Journal(tmp_path / "j.db")
    r = ResearchRunner(j, cfg, run=reply)
    r.step()                             # measure
    r.step()                             # control: p=0.3 <= max_p 0.5 -> powered
    r.step()                             # singles: p=0.3 <= max_p 0.5 -> "scored"
    rows = Ledger(j).rows("4h", "trail", k=1)
    assert rows and rows[0]["label"] == "scored"


def test_discovery_null_draws_at_or_above_the_floor_passes_through(tmp_path):
    from trader.strategy.null_baseline import MIN_DRAWS

    cfg = {**CFG, "research": {**CFG["research"],
                               "discovery_null_draws": MIN_DRAWS + 10}}
    r = ResearchRunner(Journal(tmp_path / "j.db"), cfg, run=None)
    assert r._null_draws() == MIN_DRAWS + 10
