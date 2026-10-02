"""strategy-health-observation.v1: every decay-sweep verdict is remembered.

Idle, still-working, decayed, compile failure and evaluation failure are
distinct recorded outcomes. Idle never absorbs an evaluation fault. The
observation is telemetry: it cannot add, suppress or alter a retirement.
"""
import json
import random
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.brain.analyst import Analyst
from trader.core.journal import Journal
from trader.strategy import health_observation as ho
from trader.strategy import rolling
from trader.strategy import signal_occurrence as so
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(sid="h1", name="Health Probe", entry="close > ema(5)", **kw):
    base = dict(
        id=sid, name=name,
        thesis="A hypothesis of sufficient length for the validator, naming a "
               "plausible short-horizon inefficiency that a rolling gate can "
               "confirm or refute on recent data.",
        invalidation="Retire below profit factor 0.85 over the last 30 days.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="1h", direction="long", entry_long=entry, entry_short="",
        filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 24}),
        regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _frame(n=4000, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.006, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
        "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]


CFG = {"risk": {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
                "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
                "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
                "bar_minutes": 60},
       "strategies": {"decay_recent_days": 60, "decay_min_trades": 3,
                      "decay_floor_pf": 0.85}}
THRESHOLDS = {"decay_recent_days": 60.0, "decay_min_trades": 3,
              "decay_floor_pf": 0.85}

UP = _frame(4000, drift=0.004, seed=2)
DOWN = _frame(4000, drift=-0.004, seed=9)
RARE = "close > ema(200) and rsi(2) < 1"


class _Journal:
    def __init__(self, fail_kinds=()):
        self.events = []
        self.fail_kinds = set(fail_kinds)

    def log_brain_event(self, kind, subject, detail):
        if kind in self.fail_kinds:
            raise sqlite3.OperationalError("database is locked")
        self.events.append((kind, subject, detail))

    def of(self, kind):
        return [d for k, _s, d in self.events if k == kind]


def _analyst(frames, journal=None):
    a = Analyst(journal or _Journal(), CFG)
    a.frames = lambda tf, extra=(): frames
    return a


def _review(a, specs):
    """review_deployed, then the kernel's post-retirement flush."""
    try:
        return a.review_deployed(specs)
    finally:
        a.flush_health_observations()


def _obs(a):
    return a.journal.of(ho.KIND_SPEC)


def _sweeps(a):
    return a.journal.of(ho.KIND_SWEEP)


# ── verdict taxonomy ─────────────────────────────────────────────────────
def test_complete_coverage_and_too_few_trades_is_idle():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    assert _review(a, [_spec(entry=RARE)]) == []
    [o] = _obs(a)
    assert o["verdict"] == ho.IDLE and o["verdict_reason"] is None
    assert o["lifecycle_branch"] == ho.IDLE
    assert o["retirement_action_selected"] is False
    assert o["metrics"]["trades"] < 3
    assert o["coverage"]["usable_symbols"] == ["BTC/USDT"]
    assert o["coverage"]["coverage_faults"] == []
    assert o["coverage"]["coverage_complete"] is True
    sc = o["window"]["per_symbol"]["BTC/USDT"]
    assert sc["scorable_bars"] > 0 and sc["source_bars"] > sc["scorable_bars"]


def test_still_working_branch_records():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    assert _review(a, [_spec()]) == []
    [o] = _obs(a)
    assert o["verdict"] == ho.STILL_WORKING
    assert o["lifecycle_branch"] == ho.STILL_WORKING
    assert o["retirement_action_selected"] is False
    assert o["metrics"]["trades"] >= 3
    assert o["metrics"]["pooled_pf"] >= 0.85
    assert o["lifecycle_verdict_text"] == "still working"


def test_decayed_branch_records():
    a = _analyst({"BTC/USDT": DOWN, "_btc_1h": DOWN})
    actions = _review(a, [_spec()])
    assert len(actions) == 1
    [o] = _obs(a)
    assert o["verdict"] == ho.DECAYED
    assert o["retirement_action_selected"] is True
    assert o["metrics"]["pooled_pf"] < 0.85
    assert o["metrics"]["trades"] >= 3


def test_compile_failure_is_compile_failed_and_skipped_as_before():
    a = _analyst({"BTC/USDT": DOWN, "_btc_1h": DOWN})
    assert _review(a, [_spec(entry="close >>> nope(")]) == []
    [o] = _obs(a)
    assert o["verdict"] == ho.COMPILE_FAILED
    assert o["lifecycle_branch"] is None
    assert o["retirement_action_selected"] is False
    assert o["error"]["stage"] == "compile"
    assert o["error"]["error_class"] and o["error"]["message"]
    assert "metrics" not in o
    assert a.journal.of("spec_decayed") == []


def test_no_usable_scoring_is_evaluation_failed_not_idle():
    """Every symbol too short: zero trades would read 'idle' to the
    retirement predicate. The observation must not."""
    short = _frame(500, seed=3)
    a = _analyst({"BTC/USDT": short, "ETH/USDT": short, "_btc_1h": short})
    assert _review(a, [_spec()]) == []
    [o] = _obs(a)
    assert o["lifecycle_branch"] == ho.IDLE          # what retirement saw
    assert o["verdict"] == ho.EVALUATION_FAILED
    assert o["verdict_reason"] == ho.INSUFFICIENT_HISTORY
    assert o["coverage"]["usable_symbols"] == []
    assert o["coverage"]["skipped_insufficient_history"] == [
        "BTC/USDT", "ETH/USDT"]
    assert o["coverage"]["unavailable_symbols"]["BTC/USDT"] == {
        "stage": "frame_load", "reason": ho.INSUFFICIENT_HISTORY,
        "detail": {"bars": 500, "window_bars": 1440}}


def test_idle_cannot_hide_an_errored_symbol():
    broken = UP.drop(columns=["high"])
    a = _analyst({"BTC/USDT": UP, "ETH/USDT": broken, "_btc_1h": UP})
    _review(a, [_spec(entry=RARE)])
    [o] = _obs(a)
    assert o["lifecycle_branch"] == ho.IDLE
    assert o["verdict"] == ho.EVALUATION_FAILED
    assert o["verdict_reason"] == ho.SYMBOL_EVALUATION_ERRORS
    err = o["coverage"]["errored_symbols"]["ETH/USDT"]
    assert err["error_class"] == "KeyError"
    assert err["stage"] in ("strategy_evaluation", "simulation")
    assert err["message"] and len(err["message"]) <= ho.MAX_MESSAGE


def test_idle_needs_a_declared_symbol_scored():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec(entry=RARE,
                             universe={"include": ["SOL/USDT"]})])
    [o] = _obs(a)
    assert o["verdict"] == ho.EVALUATION_FAILED
    assert o["verdict_reason"] == ho.DECLARED_NOT_LOADED
    assert o["coverage"]["declared_not_loaded"] == ["SOL/USDT"]
    # the loader gave no reason; none is invented
    assert o["coverage"]["unavailable_symbols"]["SOL/USDT"] == {
        "stage": "frame_load", "reason": ho.DECLARED_NOT_LOADED,
        "detail": ho.NOT_SUPPLIED}


def test_evaluation_exception_aborts_as_before_and_is_recorded():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})

    def boom(tf, spec):
        if spec.id == "s2":
            raise RuntimeError("feed down")
        return Analyst._ctx(a, tf, spec)
    a._ctx = boom
    specs = [_spec("s1"), _spec("s2"), _spec("s3")]
    with pytest.raises(RuntimeError, match="feed down"):
        _review(a, specs)
    obs = {o["spec_id"]: o for o in _obs(a)}
    assert obs["s1"]["verdict"] == ho.STILL_WORKING
    assert obs["s2"]["verdict"] == ho.EVALUATION_FAILED
    assert obs["s2"]["verdict_reason"] == ho.EVALUATION_EXCEPTION
    assert obs["s2"]["error"] == {"stage": "context",
                                  "error_class": "RuntimeError",
                                  "message": "feed down"}
    assert "s3" not in obs
    [sw] = _sweeps(a)
    assert sw["status"] == ho.SWEEP_ABORTED
    assert sw["intended_spec_ids"] == ["s1", "s2", "s3"]
    assert sw["evaluation_attempted_spec_ids"] == ["s1", "s2"]
    assert sw["evaluation_completed_spec_ids"] == ["s1"]
    assert sw["observation_recorded_spec_ids"] == ["s1", "s2"]
    assert sw["aborted_at_spec_id"] == "s2"
    assert sw["not_evaluated_spec_ids"] == ["s3"]


# ── coverage, metrics, provenance ────────────────────────────────────────
def test_coverage_accounting_is_persisted():
    short = _frame(500, seed=3)
    broken = UP.drop(columns=["high"])
    a = _analyst({"BTC/USDT": UP, "ETH/USDT": broken, "XRP/USDT": short,
                  "NIL/USDT": None, "_btc_1h": UP})
    _review(a, [_spec(universe={"include": [
        "BTC/USDT", "ETH/USDT", "XRP/USDT", "NIL/USDT", "ADA/USDT"]})])
    c = _obs(a)[0]["coverage"]
    assert c["declared_symbols"] == ["BTC/USDT", "ETH/USDT", "XRP/USDT",
                                     "NIL/USDT", "ADA/USDT"]
    assert c["declared_source"] == "universe.include"
    assert c["attempted_symbols"] == ["BTC/USDT", "ETH/USDT", "XRP/USDT",
                                      "NIL/USDT"]
    assert c["returned_symbols"] == ["BTC/USDT"]
    assert c["usable_symbols"] == ["BTC/USDT"]
    assert c["skipped_insufficient_history"] == ["XRP/USDT"]
    assert c["skipped_no_frame"] == ["NIL/USDT"]
    assert c["declared_not_loaded"] == ["ADA/USDT"]
    assert c["errored_symbols"]["ETH/USDT"]["error_class"] == "KeyError"
    assert c["coverage_complete"] is False
    assert c["coverage_faults"] == [
        ho.SYMBOL_EVALUATION_ERRORS, ho.DECLARED_NOT_LOADED, ho.NO_FRAME,
        ho.INSUFFICIENT_HISTORY]
    assert c["unavailable_symbols"]["NIL/USDT"]["detail"] == ho.NOT_SUPPLIED


def test_thresholds_and_raw_metrics_are_exact():
    frames = {"BTC/USDT": UP, "_btc_1h": UP}
    a = _analyst(frames)
    _review(a, [_spec()])
    o = _obs(a)[0]
    assert o["thresholds"] == THRESHOLDS
    # the same numbers the retirement predicate computed
    spec = _spec()
    fr, btc, dv, risk = a._ctx("1h", spec)
    diag = {}
    _dead, ev = rolling.has_decayed(compile_spec(spec), fr, risk, "1h",
                                    recent_days=60, min_trades=3,
                                    floor_pf=0.85, btc=btc, derivs_for=dv,
                                    diagnostics=diag)
    m = o["metrics"]
    assert m["trades"] == ev["trades"]
    assert m["pooled_pf"] == ev["pooled_pf"] and m["pnl"] == ev["pnl"]
    assert m["gross_win"] == pytest.approx(diag["gross_win"])
    assert m["gross_loss"] == pytest.approx(diag["gross_loss"])
    assert m["wins"] == diag["wins"]
    assert m["winrate"] == pytest.approx(diag["wins"] / diag["trades"])
    assert m["per_symbol"]["BTC/USDT"]["pf"] == ev["per_symbol"][
        "BTC/USDT"]["pf"]
    assert m["pooled_pf_kind"] == ho.PF_RATIO


def test_pf_sentinels_are_interpretable():
    assert ho.pf_kind(10.0, 0.0) == ho.PF_NO_LOSSES
    assert ho.pf_kind(0.0, 0.0) == ho.PF_NO_GROSS
    assert ho.pf_kind(1.0, 2.0) == ho.PF_RATIO
    m = ho._metrics({"pooled_pf": 99.0, "trades": 4, "pnl": 10.0},
                    {"trades": 4, "wins": 4, "gross_win": 10.0,
                     "gross_loss": 0.0, "scored": {}})
    assert m["pooled_pf"] == 99.0 and m["pooled_pf_kind"] == ho.PF_NO_LOSSES
    assert m["gross_loss"] == 0.0 and m["trades"] == 4
    # no trades: 0.0 PF and an undefined (null) winrate, never 0.0
    short = _frame(500, seed=3)
    a = _analyst({"BTC/USDT": UP, "_btc_1h": short})
    _review(a, [_spec(entry="close < 0")])
    m = _obs(a)[0]["metrics"]
    assert m["trades"] == 0 and m["pooled_pf"] == 0.0
    assert m["pooled_pf_kind"] == ho.PF_NO_GROSS
    assert m["winrate"] is None


def test_window_bounds_and_last_closed_bar():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec()])
    w = _obs(a)[0]["window"]
    n = rolling.bars("1h", 60)
    assert w["window_bars"] == n
    assert w["window_start_bar_open_ts"] == UP["ts"].iloc[-n].isoformat()
    p = w["per_symbol"]["BTC/USDT"]
    assert p["source_bars"] == n + 210
    assert p["source_start_bar_ts"] == UP["ts"].iloc[-n - 210].isoformat()
    assert p["scorable_start_bar_ts"] == UP["ts"].iloc[-n].isoformat()
    # the last bar can never open a trade
    assert p["scorable_end_bar_ts"] == UP["ts"].iloc[-2].isoformat()
    assert p["scorable_range_bars"] == n - 1
    assert p["scorable_bars"] == n - 1
    assert w["window_end_bar_open_ts"] == UP["ts"].iloc[-1].isoformat()
    assert w["last_bar_open_ts"] == UP["ts"].iloc[-1].isoformat()
    assert w["last_bar_close_ms"] == (
        UP["ts"].iloc[-1].value // 1_000_000 + 3_600_000)
    assert w["last_bar_closed_at_sweep"] is True


def test_spec_provenance_is_persisted():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    spec = _spec()
    _review(a, [spec])
    o = _obs(a)[0]
    assert o["schema"] == ho.SCHEMA
    assert o["spec_id"] == "h1"
    assert o["spec_fingerprint"] == so.spec_fingerprint(spec)
    assert o["spec_fingerprint_schema"] == so.FINGERPRINT_SCHEMA
    assert o["spec_content_sha256"] == ho.spec_content_sha256(spec)
    assert o["health_fingerprint"] == ho.health_fingerprint(spec)
    man = o["code_manifest"]
    assert set(man) == set(ho._CODE) and all(man.values())
    # the manifest claims no more than it covers
    assert o["code_manifest_kind"] == "partial_code_manifest"
    assert "NOT establish complete reproducibility" in o["code_manifest_note"]


# ── sweep identity ───────────────────────────────────────────────────────
def test_one_review_has_one_sweep_id_and_later_ones_differ():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec("s1"), _spec("s2", entry="close >>> x(")])
    first = {o["sweep_id"] for o in _obs(a)}
    [sw] = _sweeps(a)
    assert first == {sw["sweep_id"]}
    assert sw["status"] == ho.SWEEP_COMPLETED
    assert sw["intended_spec_ids"] == ["s1", "s2"]
    assert sw["evaluation_completed_spec_ids"] == ["s1"]
    assert sw["compile_failed_spec_ids"] == ["s2"]
    assert sw["not_evaluated_spec_ids"] == []
    _review(a, [_spec("s1")])
    second = {o["sweep_id"] for o in _obs(a)} - first
    assert len(second) == 1 and second.isdisjoint(first)


# ── retirement isolation ─────────────────────────────────────────────────
def _strip_ts(events):
    return [(k, s, {**d, "ts": None}) for k, s, d in events]


def test_observation_write_failure_does_not_change_retirement():
    frames = {"BTC/USDT": DOWN, "_btc_1h": DOWN}
    specs = [_spec("d1"), _spec("d2", entry=RARE), _spec("d3")]
    ok = _analyst(frames)
    base = _review(ok, specs)
    bad = _analyst(frames, _Journal(fail_kinds={ho.KIND_SPEC, ho.KIND_SWEEP}))
    assert _review(bad, specs) == base
    assert _strip_ts(bad.journal.events) == _strip_ts(
        [e for e in ok.journal.events if e[0] == "spec_decayed"])


def test_observation_construction_failure_does_not_change_retirement(
        monkeypatch):
    frames = {"BTC/USDT": DOWN, "_btc_1h": DOWN}
    ok = _analyst(frames)
    base = _review(ok, [_spec()])

    def explode(*a, **k):
        raise ValueError("bad record")
    monkeypatch.setattr(ho, "coverage", explode)
    monkeypatch.setattr(ho, "code_manifest", explode)
    a = _analyst(frames)
    assert _review(a, [_spec()]) == base
    assert len(a.journal.of("spec_decayed")) == 1


def test_spec_decayed_behavior_is_unchanged():
    """Payload, order and retirement evidence are exactly has_decayed's."""
    frames = {"BTC/USDT": DOWN, "_btc_1h": DOWN}
    a = _analyst(frames)
    spec = _spec()
    actions = _review(a, [spec])
    assert a.journal.events[0][0] == "spec_decayed"
    fr, btc, dv, risk = a._ctx("1h", spec)
    dead, ev = rolling.has_decayed(compile_spec(spec), fr, risk, "1h",
                                   recent_days=60, min_trades=3,
                                   floor_pf=0.85, btc=btc, derivs_for=dv)
    assert dead is True
    assert actions == [{"spec": "h1", "name": spec.name, "action": "retire",
                        "evidence": ev}]
    [d] = a.journal.of("spec_decayed")
    assert d["evidence"] == ev and set(d) == {"name", "evidence", "ts"}
    for k in ("branch", "attempted", "scored", "gross_win"):
        assert k not in ev


def test_diagnostics_never_change_the_returned_evidence():
    c = compile_spec(_spec())
    for frames in ({"A": UP}, {"A": DOWN}, {"A": _frame(500)}):
        plain = rolling.has_decayed(c, frames, CFG["risk"], "1h",
                                    recent_days=60, min_trades=3)
        with_d = rolling.has_decayed(c, frames, CFG["risk"], "1h",
                                     recent_days=60, min_trades=3,
                                     diagnostics={})
        assert plain == with_d


# ── history ──────────────────────────────────────────────────────────────
def _rows(a):
    return [{"id": i + 1, "ts": f"2026-09-25T00:00:{i:02d}+00:00",
             "kind": k, "subject": s, "detail": json.dumps(d)}
            for i, (k, s, d) in enumerate(a.journal.events)]


def test_history_is_ordered_and_deterministic():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec("s1")])
    a.frames = lambda tf, extra=(): {"BTC/USDT": UP, "_btc_1h": _frame(500)}
    a._frames = {}
    _review(a, [_spec("s1", entry=RARE)])
    rows = _rows(a)
    h = ho.history(rows)
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    assert ho.history(shuffled) == h
    [s] = h["specs"]
    assert [o["verdict"] for o in s["observations"]] == [
        ho.STILL_WORKING, ho.IDLE]
    assert s["latest_verdict"] == ho.IDLE
    assert s["first_observed_ts"] < s["last_observed_ts"]
    assert s["verdict_changes"][0]["from"] == ho.STILL_WORKING
    assert s["verdict_changes"][0]["spec_version_changed"] is True
    # the entry changed: a behavioural version change
    assert len(s["health_fingerprints"]) == 2
    assert len(s["health_fingerprint_changes"]) == 1
    assert h["counts_are"] == "sweep_observations_not_independent_evidence"
    assert len(h["sweeps"]) == 2 and h["sweeps_without_record"] == []
    assert h["invalid_records"] == []


def test_malformed_observations_are_reported_separately():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec()])
    rows = _rows(a)
    good = json.loads(rows[0]["detail"])
    bad = [
        {"id": 90, "ts": "t", "kind": ho.KIND_SPEC, "subject": "h1",
         "detail": "{not json"},
        {"id": 91, "ts": "t", "kind": ho.KIND_SPEC, "subject": "h1",
         "detail": json.dumps({**good, "schema": "v0"})},
        {"id": 92, "ts": "t", "kind": ho.KIND_SPEC, "subject": "h1",
         "detail": json.dumps({**good, "verdict": "healthy"})},
        {"id": 93, "ts": "t", "kind": ho.KIND_SPEC, "subject": "zz",
         "detail": json.dumps(good)},
        {"id": 94, "ts": "t", "kind": ho.KIND_SWEEP, "subject": "x",
         "detail": json.dumps({"schema": ho.SCHEMA})},
    ]
    h = ho.history(rows + bad)
    assert [(r["id"], r["reason"]) for r in h["invalid_records"]] == [
        (90, "detail_undecodable"), (91, "schema_unsupported"),
        (92, "verdict_unknown"), (93, "subject_mismatch"),
        (94, "missing_fields")]
    [s] = h["specs"]
    assert len(s["observations"]) == 1


def test_missing_sweep_record_is_visible():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec()])
    rows = [r for r in _rows(a) if r["kind"] == ho.KIND_SPEC]
    h = ho.history(rows)
    assert h["sweeps"] == []
    assert h["sweeps_without_record"] == [
        json.loads(rows[0]["detail"])["sweep_id"]]


def test_journal_accessor_is_read_only(tmp_path):
    j = Journal(tmp_path / "j.db")
    a = _analyst({"BTC/USDT": DOWN, "_btc_1h": DOWN}, j)
    _review(a, [_spec()])
    conn = sqlite3.connect(tmp_path / "j.db")
    before = conn.execute("SELECT * FROM brain_events ORDER BY id").fetchall()
    rows = j.strategy_health_rows()
    assert {r["kind"] for r in rows} == {ho.KIND_SPEC, ho.KIND_SWEEP}
    h = ho.history_journal(j)
    assert h["specs"][0]["latest_verdict"] == ho.DECAYED
    assert conn.execute(
        "SELECT * FROM brain_events ORDER BY id").fetchall() == before
    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM brain_events ORDER BY id")]
    assert kinds == ["spec_decayed", ho.KIND_SPEC, ho.KIND_SWEEP]


# ── boundaries ───────────────────────────────────────────────────────────
def test_no_attention_trigger_or_salience():
    import inspect
    src = inspect.getsource(ho)
    code = src.split('"""', 2)[2]      # below the module docstring
    for word in ("import attention", "cognition", "salience(",
                 "trigger(", "notifier", "investigat"):
        assert word not in code
    from trader.brain import analyst
    body = inspect.getsource(analyst.Analyst.review_deployed)
    assert "attention" not in body.lower()


# ── correction regressions (ASTRA NEEDS_SMALL_CORRECTION) ────────────────
def _baseline(frames, specs):
    ok = _analyst(frames)
    base = _review(ok, specs)
    return base, _strip_ts([e for e in ok.journal.events
                            if e[0] == "spec_decayed"])


RETIRE_SPECS = lambda: [_spec("d1"), _spec("d2", entry=RARE), _spec("d3")]
DOWN_FRAMES = {"BTC/USDT": DOWN, "_btc_1h": DOWN}


def test_uuid_failure_leaves_retirement_identical(monkeypatch):
    base, decayed = _baseline(DOWN_FRAMES, RETIRE_SPECS())

    def boom():
        raise OSError("no entropy")
    monkeypatch.setattr(ho.uuid, "uuid4", boom)
    a = _analyst(DOWN_FRAMES)
    assert _review(a, RETIRE_SPECS()) == base
    assert _strip_ts(a.journal.events) == decayed      # spec_decayed only
    assert _obs(a) == [] and _sweeps(a) == []


def test_clock_failure_leaves_retirement_identical(monkeypatch):
    base, decayed = _baseline(DOWN_FRAMES, RETIRE_SPECS())

    class _BrokenClock:
        @staticmethod
        def now(tz=None):
            raise RuntimeError("clock")
    monkeypatch.setattr(ho, "datetime", _BrokenClock)
    a = _analyst(DOWN_FRAMES)
    assert _review(a, RETIRE_SPECS()) == base
    assert _strip_ts(a.journal.events) == decayed
    assert _obs(a) == []


def test_no_health_io_until_flush_and_spec_decayed_order_unchanged():
    base, decayed = _baseline(DOWN_FRAMES, RETIRE_SPECS())
    a = _analyst(DOWN_FRAMES)
    assert a.review_deployed(RETIRE_SPECS()) == base
    # nothing but the pre-existing lifecycle events before the flush
    assert _strip_ts(a.journal.events) == decayed
    assert [d["name"] for d in a.journal.of("spec_decayed")] == [
        "Health Probe", "Health Probe"]
    assert a.flush_health_observations() is True
    kinds = [k for k, _s, _d in a.journal.events]
    n = len(decayed)
    assert kinds[:n] == ["spec_decayed"] * n
    assert set(kinds[n:]) == {ho.KIND_SPEC, ho.KIND_SWEEP}
    assert a.flush_health_observations() is True      # idempotent
    assert len(a.journal.events) == len(kinds)


def test_flush_defers_while_retirement_uncommitted(tmp_path):
    """The journal's _tx rolls back on failure; telemetry must never write
    on a connection holding the kernel's uncommitted retirement UPDATEs."""
    j = Journal(tmp_path / "j.db")
    a = _analyst(DOWN_FRAMES, j)
    actions = a.review_deployed([_spec()])
    j.query("INSERT INTO brain_events(ts,kind,subject,detail) "
            "VALUES ('t','kernel_update_stand_in','h1','{}')")
    assert j._conn().in_transaction
    assert a.flush_health_observations() is False
    assert j._conn().in_transaction                   # untouched
    j._conn().commit()
    assert a.flush_health_observations() is True
    kinds = [r["kind"] for r in j.query(
        "SELECT kind FROM brain_events ORDER BY id")]
    assert kinds == ["spec_decayed", "kernel_update_stand_in",
                     ho.KIND_SPEC, ho.KIND_SWEEP]
    assert len(actions) == 1


def test_errored_symbol_turns_decayed_into_evaluation_failed_branch_kept():
    broken = DOWN.drop(columns=["high"])
    a = _analyst({"BTC/USDT": DOWN, "ETH/USDT": broken, "_btc_1h": DOWN})
    actions = _review(a, [_spec()])
    [o] = _obs(a)
    assert len(actions) == 1                          # authority unchanged
    assert o["lifecycle_branch"] == ho.DECAYED
    assert o["retirement_action_selected"] is True
    assert o["verdict"] == ho.EVALUATION_FAILED
    assert o["verdict_reason"] == ho.SYMBOL_EVALUATION_ERRORS


def test_errored_symbol_turns_still_working_into_evaluation_failed():
    broken = UP.drop(columns=["high"])
    a = _analyst({"BTC/USDT": UP, "ETH/USDT": broken, "_btc_1h": UP})
    assert _review(a, [_spec()]) == []
    [o] = _obs(a)
    assert o["lifecycle_branch"] == ho.STILL_WORKING
    assert o["verdict"] == ho.EVALUATION_FAILED


def test_zero_scorable_bars_is_evaluation_failed():
    """The frame covers the window, so it is not 'short' — but after the
    WARMUP floor no bar can open a trade. simulate() still returns."""
    cfg = {**CFG, "strategies": {**CFG["strategies"], "decay_recent_days": 5}}
    a = Analyst(_Journal(), cfg)
    f = _frame(150, seed=4)
    a.frames = lambda tf, extra=(): {"BTC/USDT": f, "_btc_1h": f}
    assert _review(a, [_spec()]) == []
    [o] = _obs(a)
    assert o["lifecycle_branch"] == ho.IDLE
    assert o["verdict"] == ho.EVALUATION_FAILED
    assert o["verdict_reason"] == ho.NO_SCORABLE_BARS
    c = o["coverage"]
    assert c["returned_symbols"] == ["BTC/USDT"] and c["usable_symbols"] == []
    p = o["window"]["per_symbol"]["BTC/USDT"]
    assert p["source_bars"] == 150 and p["scorable_bars"] == 0
    assert p["effective_score_from"] == 210
    assert p["scorable_start_bar_ts"] is None


def test_missing_required_context_is_evaluation_failed():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    real_ctx = a._ctx

    def no_derivs(tf, spec):
        fr, btc, _dv, risk = real_ctx(tf, spec)
        return fr, btc, (lambda s: {}), risk
    a._ctx = no_derivs
    _review(a, [_spec(entry="funding > 0.0005")])
    [o] = _obs(a)
    assert o["lifecycle_branch"] == ho.IDLE           # NaN read as no entry
    assert o["verdict"] == ho.EVALUATION_FAILED
    assert o["verdict_reason"] == ho.REQUIRED_CONTEXT_MISSING
    c = o["coverage"]
    assert c["missing_required_context"] == {"BTC/USDT": ["funding"]}
    assert c["unavailable_symbols"]["BTC/USDT"]["stage"] == "feature_context"


def test_symbol_failure_keeps_stage_and_bounded_reason(monkeypatch):
    real = rolling.simulate

    def sim(lo, sh, df, *a, symbol=None, **k):
        if symbol == "ETH/USDT":
            raise ValueError("x" * 1000)
        return real(lo, sh, df, *a, symbol=symbol, **k)
    monkeypatch.setattr(rolling, "simulate", sim)
    a = _analyst({"BTC/USDT": UP, "ETH/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec(entry=RARE)])
    [o] = _obs(a)
    err = o["coverage"]["errored_symbols"]["ETH/USDT"]
    assert err["stage"] == "simulation"
    assert err["error_class"] == "ValueError"
    assert err["message"] == "x" * ho.MAX_MESSAGE
    u = o["coverage"]["unavailable_symbols"]["ETH/USDT"]
    assert u["stage"] == "simulation"
    assert u["reason"] == ho.SYMBOL_EVALUATION_ERRORS


def test_health_fingerprint_ignores_provenance_and_prose():
    spec = _spec()
    other = _spec(name="Renamed", provenance={"source_kind": "other"},
                  thesis=spec.thesis + " More prose.",
                  invalidation="Something else entirely.",
                  regime_filter=["RANGING"], markets=["spot"],
                  generation=7, data_requires=["ohlcv", "funding"])
    assert ho.health_fingerprint(other) == ho.health_fingerprint(spec)
    assert ho.spec_content_sha256(other) != ho.spec_content_sha256(spec)


def test_changing_an_exit_changes_health_fingerprint():
    spec = _spec()
    other = _spec(exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                                target={"kind": "rr", "v": 2.0},
                                trail={"kind": "none"}, time={"max_bars": 12}))
    # the signal fingerprint cannot see exits; the health one must
    assert so.spec_fingerprint(other) == so.spec_fingerprint(spec)
    assert ho.health_fingerprint(other) != ho.health_fingerprint(spec)
    for f in ("entry_long", "filters", "universe", "direction", "timeframe"):
        assert f in ho.HEALTH_FINGERPRINT_FIELDS


def test_applied_simulation_settings_are_persisted():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec()])
    st = _obs(a)[0]["simulation_settings"]
    assert st["applied"]["taker_fee_pct"] == 0.05
    assert st["applied"]["slippage_atr_frac"] == 0.06
    assert st["applied"]["risk_per_trade_pct"] == 1.5
    assert st["applied"]["bar_minutes"] == 60       # risk_for, per timeframe
    assert st["source"]["taker_fee_pct"] == "config"
    assert st["source"]["real_funding"] == "simulate_default"
    assert st["fixed"]["warmup_bars"] == 210 and st["sha256"]


def test_record_build_failure_keeps_sweep_sets_truthful(monkeypatch):
    real = ho.Sweep._build

    def build(self, kind, spec, payload, manifest):
        if spec.id == "s1":
            raise ValueError("bad record")
        return real(self, kind, spec, payload, manifest)
    monkeypatch.setattr(ho.Sweep, "_build", build)
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec("s1"), _spec("s2")])
    [sw] = _sweeps(a)
    assert sw["status"] == ho.SWEEP_COMPLETED
    assert sw["evaluation_completed_spec_ids"] == ["s1", "s2"]
    assert sw["observation_recorded_spec_ids"] == ["s2"]
    assert sw["observation_failed_spec_ids"] == ["s1"]
    assert sw["observation_failures"]["s1"]["step"] == "build"
    assert sw["not_evaluated_spec_ids"] == []


def test_write_failure_is_not_reported_as_not_evaluated():
    a = _analyst(DOWN_FRAMES, _Journal(fail_kinds={ho.KIND_SPEC}))
    _review(a, [_spec("s1")])
    [sw] = _sweeps(a)
    assert sw["evaluation_completed_spec_ids"] == ["s1"]
    assert sw["observation_failed_spec_ids"] == ["s1"]
    assert sw["not_evaluated_spec_ids"] == []
    assert sw["n_retirement_actions_selected"] == 1


def test_retirement_field_means_selected_not_applied():
    a = _analyst(DOWN_FRAMES)
    _review(a, [_spec()])
    [o] = _obs(a)
    assert "retired" not in o
    assert o["retirement_action_selected"] is True


def test_lifecycle_state_is_explicitly_unknown():
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec(), _spec("c", entry="close >>> x(")])
    for o in _obs(a):
        assert o["lifecycle_state"] is None
        assert o["lifecycle_state_reason"] == ho.NOT_SUPPLIED


def test_history_keeps_coverage_qualification():
    broken = UP.drop(columns=["high"])
    a = _analyst({"BTC/USDT": UP, "ETH/USDT": broken, "_btc_1h": UP})
    _review(a, [_spec(entry=RARE)])
    h = ho.history(_rows(a))
    [s] = h["specs"]
    assert s["latest"] == {
        "verdict": ho.EVALUATION_FAILED,
        "verdict_reason": ho.SYMBOL_EVALUATION_ERRORS,
        "coverage_complete": False,
        "coverage_faults": [ho.SYMBOL_EVALUATION_ERRORS],
        "lifecycle_branch": ho.IDLE,
        "retirement_action_selected": False}
    o = s["observations"][0]
    assert o["coverage_complete"] is False and o["coverage_faults"]
    assert h["counts_are"] == "sweep_observations_not_independent_evidence"
    for word in ("probability", "rate", "confidence", "salience"):
        assert not any(word in k for k in s)
