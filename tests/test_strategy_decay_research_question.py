"""research-question.v1: strategy-decay research questions.

A question is registered only when a deployed strategy is first truthfully
observed decayed, or moves from a truthful non-decayed verdict to decayed.
Repeated decayed sweeps add nothing. History is read fail-closed. The
question is strategy-scoped and has no asset, Attention, Risk, Execution
or trading authority.
"""
import ast
import json
import re
from pathlib import Path

import pytest

from trader.cognition import research_question as rq
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

TH = {"decay_recent_days": 60.0, "decay_min_trades": 3, "decay_floor_pf": 0.85}


class _Hist:
    """Builds raw brain_events rows exactly as Sweep.flush writes them."""

    def __init__(self):
        self.rows = []
        self.n = 0

    def _add(self, kind, subject, detail):
        self.n += 1
        self.rows.append({"id": self.n, "ts": f"2026-09-25T00:{self.n:02d}:00+00:00",
                          "kind": kind, "subject": subject,
                          "detail": detail if isinstance(detail, str)
                          else json.dumps(detail)})
        return self.n

    def spec_rec(self, sweep, spec, verdict, fp="fp1"):
        branch = verdict if verdict in ho.BRANCHES else None
        complete = verdict in ho.BRANCHES
        rec = {"schema": ho.SCHEMA, "record": "spec", "sweep_id": sweep["id"],
               "sweep_started_at": sweep["at"], "spec_id": spec,
               "spec_name": f"name-{spec}", "health_fingerprint": fp,
               "health_fingerprint_schema": ho.HEALTH_FINGERPRINT_SCHEMA,
               "spec_fingerprint": "sfp", "spec_fingerprint_schema": "x",
               "spec_content_sha256": "c1", "thresholds": TH,
               "verdict": verdict,
               "verdict_reason": None if complete else (
                   None if verdict == ho.COMPILE_FAILED else ho.NO_SYMBOL_SCORED),
               "lifecycle_branch": branch,
               "retirement_action_selected": verdict == ho.DECAYED,
               "simulation_settings": {"sha256": "sim1"},
               "metrics": {"trades": 5, "pooled_pf": 0.5},
               "coverage": {"coverage_complete": complete,
                            "coverage_faults": [] if complete else
                            [ho.NO_SCORABLE_BARS]},
               "window": {}}
        return rec

    def sweep(self, verdicts, minute, *, write_sweep=True, failed=(),
              sweep_over=None, spec_over=None):
        """One sweep: {spec: verdict}. Spec records, then the sweep record."""
        sw = {"id": f"sw{minute:03d}", "at": f"2026-09-{1 + minute // 1440:02d}"
              f"T{(minute // 60) % 24:02d}:{minute % 60:02d}:00+00:00"}
        recorded = []
        for spec, verdict in verdicts.items():
            if spec in failed:
                continue
            rec = self.spec_rec(sw, spec, verdict)
            rec.update(spec_over or {})
            self._add(ho.KIND_SPEC, spec, rec)
            recorded.append(spec)
        if write_sweep:
            rec = {"schema": ho.SCHEMA, "record": "sweep", "sweep_id": sw["id"],
                   "sweep_started_at": sw["at"], "status": ho.SWEEP_COMPLETED,
                   "intended_spec_ids": list(verdicts),
                   "evaluation_attempted_spec_ids": list(verdicts),
                   "evaluation_completed_spec_ids": [
                       s for s, v in verdicts.items() if v != ho.COMPILE_FAILED],
                   "compile_failed_spec_ids": [
                       s for s, v in verdicts.items() if v == ho.COMPILE_FAILED],
                   "not_evaluated_spec_ids": [],
                   "observation_recorded_spec_ids": recorded,
                   "observation_failed_spec_ids": sorted(failed),
                   "aborted_at_spec_id": None}
            rec.update(sweep_over or {})
            self._add(ho.KIND_SWEEP, sw["id"], rec)
        return sw

    def seq(self, spec, verdicts, start=10):
        for i, v in enumerate(verdicts):
            self.sweep({spec: v}, start + 10 * i)
        return self


def _qs(rows):
    return rq.derive(rows).questions


D, W, I = ho.DECAYED, ho.STILL_WORKING, ho.IDLE
CF, EF = ho.COMPILE_FAILED, ho.EVALUATION_FAILED


# ── 1-5 creation rule ───────────────────────────────────────────────────
def test_first_decayed_observation_creates_one_question():
    [q] = _qs(_Hist().seq("s1", [D]).rows)
    assert q["trigger"] == rq.FIRST_TRUTHFUL_DECAYED
    assert q["prior"] is None and q["changes_since_prior"] is None
    assert q["source"]["verdict"] == D and q["source"]["lifecycle_branch"] == D
    assert q["scope"] == {"kind": "strategy", "spec_id": "s1"}


def test_still_working_to_decayed_creates_one():
    h = _Hist().seq("s1", [W, D])
    [q] = _qs(h.rows)
    assert q["trigger"] == rq.TRANSITION_TO_DECAYED
    assert q["prior"]["verdict"] == W and q["prior"]["sweep_id"] == "sw010"
    assert q["source"]["sweep_id"] == "sw020"
    assert q["question"]["text"] == (
        "What explains strategy s1 moving from health verdict still_working "
        "in decay sweep sw010 to decayed in decay sweep sw020?")


def test_idle_to_decayed_creates_one():
    [q] = _qs(_Hist().seq("s1", [I, D]).rows)
    assert q["trigger"] == rq.TRANSITION_TO_DECAYED and q["prior"]["verdict"] == I


def test_decayed_to_decayed_creates_none():
    [q] = _qs(_Hist().seq("s1", [D, D, D]).rows)
    assert q["source"]["sweep_id"] == "sw010"


def test_decayed_still_working_decayed_creates_a_new_question():
    qs = _qs(_Hist().seq("s1", [D, W, D]).rows)
    assert [q["trigger"] for q in qs] == [rq.FIRST_TRUTHFUL_DECAYED,
                                          rq.TRANSITION_TO_DECAYED]
    assert [q["source"]["sweep_id"] for q in qs] == ["sw010", "sw030"]
    assert qs[0]["question_id"] != qs[1]["question_id"]


@pytest.mark.parametrize("bad", [CF, EF])
def test_unreadable_verdicts_create_no_question(bad):
    assert _qs(_Hist().seq("s1", [bad, bad]).rows) == []
    assert _qs(_Hist().seq("s1", [W, bad]).rows) == []


@pytest.mark.parametrize("bad", [CF, EF])
def test_unreadable_does_not_break_a_decayed_run(bad):
    [q] = _qs(_Hist().seq("s1", [D, bad, D]).rows)
    assert q["source"]["sweep_id"] == "sw010"


@pytest.mark.parametrize("bad", [CF, EF])
def test_unreadable_is_not_a_prior_verdict(bad):
    [q] = _qs(_Hist().seq("s1", [bad, D]).rows)
    assert q["trigger"] == rq.FIRST_TRUTHFUL_DECAYED
    assert q["unreadable_since_prior_event_ids"] == [1]
    [q] = _qs(_Hist().seq("s1", [W, bad, D]).rows)
    assert q["prior"]["verdict"] == W
    assert q["unreadable_since_prior_event_ids"] == [3]


def test_decayed_record_with_failed_verdict_is_not_decayed():
    """evaluation_failed keeps the underlying decayed branch; still no question."""
    h = _Hist()
    h.sweep({"s1": EF}, 10, spec_over={"lifecycle_branch": D,
                                       "retirement_action_selected": True})
    assert _qs(h.rows) == []


def test_specs_are_independent():
    h = _Hist()
    h.sweep({"a": W, "b": D}, 10)
    h.sweep({"a": D, "b": D}, 20)
    qs = _qs(h.rows)
    assert [(q["scope"]["spec_id"], q["trigger"]) for q in qs] == [
        ("a", rq.TRANSITION_TO_DECAYED), ("b", rq.FIRST_TRUTHFUL_DECAYED)]


# ── 6 idempotence ───────────────────────────────────────────────────────
def test_retry_and_restart_are_idempotent(tmp_path):
    h = _Hist().seq("s1", [W, D, D, W, D])
    db = tmp_path / "j.db"
    j = Journal(db)
    for i, r in enumerate(h.rows):
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    first = rq.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2 and not first["conflict"]
    again = rq.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(db)                       # restart: fresh connection
    third = rq.record_from_journal(j2, now_ms=3)
    assert third["inserted"] == [] and third["duplicate"] == first["inserted"]
    stored = rq.load(j2)
    assert [q["question_id"] for q in stored] == first["inserted"]
    assert {r["recorded_at_ms"] for r in j2.research_questions()} == {1}


def test_new_history_only_adds_new_questions(tmp_path):
    j = Journal(tmp_path / "j.db")
    h = _Hist().seq("s1", [D])
    for r in h.rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    [a] = rq.record_from_journal(j, 1)["inserted"]
    h.seq("s1", [D, W, D], start=100)
    for r in h.rows[2:]:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    res = rq.record_from_journal(j, 2)
    assert res["duplicate"] == [a] and len(res["inserted"]) == 1


def test_conflicting_content_is_refused_not_overwritten(tmp_path):
    j = Journal(tmp_path / "j.db")
    [q] = _qs(_Hist().seq("s1", [D]).rows)
    row = rq.row_for(q)
    assert j.record_research_question(row, recorded_at_ms=1) == "inserted"
    other = dict(row, canonical_json=row["canonical_json"].replace("s1", "s1"),
                 question_id="0" * 64)
    assert j.record_research_question(other, recorded_at_ms=2) == "conflict"
    tampered = dict(row, canonical_json=row["canonical_json"] + " ")
    assert j.record_research_question(tampered, recorded_at_ms=2) == "conflict"
    [stored] = j.research_questions()
    assert stored["canonical_json"] == row["canonical_json"]


# ── 7 determinism ───────────────────────────────────────────────────────
def test_ids_and_serialization_are_deterministic_and_order_independent():
    h = _Hist().seq("s1", [W, D, D, W, D])
    a = _qs(h.rows)
    b = _qs(list(reversed(h.rows)))
    assert a == b
    assert [rq.canonical(q) for q in a] == [rq.canonical(q) for q in b]
    for q in a:
        assert rq.from_json(rq.canonical(q)) == q


def test_question_id_binds_source_identity():
    h1 = _Hist().seq("s1", [D])
    h2 = _Hist().seq("s1", [D])
    assert _qs(h1.rows)[0]["question_id"] == _qs(h2.rows)[0]["question_id"]
    h3 = _Hist()
    h3.sweep({"s1": D}, 10, spec_over={"spec_content_sha256": "c2"})
    [q3] = _qs(h3.rows)
    # a different source record is a different source, hence a different id
    assert q3["source"]["record_sha256"] != _qs(h1.rows)[0]["source"]["record_sha256"]
    assert q3["question_id"] != _qs(h1.rows)[0]["question_id"]


def test_source_binding_fields():
    [q] = _qs(_Hist().seq("s1", [W, D]).rows)
    s = q["source"]
    assert s["schema"] == ho.SCHEMA and s["kind"] == ho.KIND_SPEC
    assert s["event_id"] == 3 and s["sweep_id"] == "sw020"
    assert s["health_fingerprint"] == "fp1" and s["spec_fingerprint"] == "sfp"
    assert s["thresholds"] == TH and len(s["record_sha256"]) == 64
    assert q["changes_since_prior"] == {"spec_version_changed": False,
                                        "simulation_settings_changed": False,
                                        "thresholds_changed": False}


def test_from_json_rejects_tampering():
    [q] = _qs(_Hist().seq("s1", [W, D]).rows)
    good = rq.canonical(q)
    for mutate in (
            lambda d: d.update(question_id="0" * 64),
            lambda d: d["question"].update(text="Buy s1 now"),
            lambda d: d.update(trigger=rq.FIRST_TRUTHFUL_DECAYED),
            lambda d: d["source"].update(verdict=W),
            lambda d: d["scope"].update(kind="asset"),
            lambda d: d.update(salience=1.0),
            lambda d: d["prior"].update(verdict=D)):
        d = json.loads(good)
        mutate(d)
        with pytest.raises(rq.ResearchQuestionError):
            rq.from_json(rq.canonical(d))
    with pytest.raises(rq.ResearchQuestionError):
        rq.from_json(json.dumps(q))          # not canonical form


# ── 8 fail-closed history ───────────────────────────────────────────────
def _refused(rows):
    d = rq.derive(rows)
    return d.questions, [(r.spec_id, r.reason) for r in d.refusals]


def test_undecodable_spec_record_refuses_that_spec_only():
    h = _Hist().seq("s1", [W, D])
    h.sweep({"s2": D}, 100)
    h._add(ho.KIND_SPEC, "s1", "{not json")
    qs, ref = _refused(h.rows)
    assert [q["scope"]["spec_id"] for q in qs] == ["s2"]
    assert ref == [("s1", rq.MALFORMED_SPEC_RECORD)]


def test_unknown_verdict_refuses_the_spec():
    h = _Hist()
    h.sweep({"s1": D}, 10, spec_over={"verdict": "decaying"})
    assert _refused(h.rows) == ([], [("s1", rq.MALFORMED_SPEC_RECORD)])


def test_unattributable_or_bad_sweep_record_refuses_everything():
    h = _Hist().seq("s1", [D])
    h._add(ho.KIND_SPEC, None, "{}")
    assert _refused(h.rows) == ([], [(None, rq.UNATTRIBUTABLE_SPEC_RECORD)])
    h = _Hist().seq("s1", [D])
    h._add(ho.KIND_SWEEP, "swX", json.dumps({"schema": ho.SCHEMA}))
    assert _refused(h.rows) == ([], [(None, rq.MALFORMED_SWEEP_RECORD)])
    h = _Hist()
    h.sweep({"s1": D}, 10, sweep_over={"observation_failed_spec_ids": None})
    assert _refused(h.rows) == ([], [(None, rq.MALFORMED_SWEEP_RECORD)])


def test_decayed_verdict_inconsistent_with_branch_or_coverage_fails_closed():
    for over in ({"lifecycle_branch": W},
                 {"coverage": {"coverage_complete": False,
                               "coverage_faults": []}},
                 {"coverage": {"coverage_complete": True,
                               "coverage_faults": [ho.NO_FRAME]}},
                 {"verdict_reason": ho.NO_FRAME}):
        h = _Hist()
        h.sweep({"s1": D}, 10, spec_over=over)
        assert _refused(h.rows) == ([], [("s1", rq.INCONSISTENT_VERDICT)])


def test_failed_observation_write_refuses_later_candidates():
    """A sweep that evaluated s1 but failed to write it may hide a verdict."""
    h = _Hist()
    h.sweep({"s1": D}, 10)
    h.sweep({"s1": W}, 20, failed=("s1",))
    h.sweep({"s1": D}, 30)
    qs, ref = _refused(h.rows)
    assert [q["source"]["sweep_id"] for q in qs] == ["sw010"]   # before gap
    assert ref == [("s1", rq.OBSERVATION_FAILED)]


def test_missing_observation_refuses_later_candidates():
    h = _Hist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": W}, 20)
    h.rows = [r for r in h.rows if not (r["kind"] == ho.KIND_SPEC and r["id"] == 3)]
    h.sweep({"s1": D}, 30)
    assert _refused(h.rows) == ([], [("s1", rq.OBSERVATION_MISSING)])


def test_missing_sweep_record_defers_the_candidate():
    h = _Hist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": D}, 20, write_sweep=False)     # flush still in progress
    assert _refused(h.rows) == ([], [("s1", rq.SWEEP_RECORD_MISSING)])


def test_sweep_record_disagreeing_with_observation_fails_closed():
    h = _Hist()
    h.sweep({"s1": D}, 10, sweep_over={"observation_recorded_spec_ids": []})
    assert _refused(h.rows) == ([], [("s1", rq.SWEEP_RECORD_MISMATCH)])


def test_ambiguous_chronology_fails_closed():
    # sweep order disagrees with write order
    h = _Hist()
    h.sweep({"s1": W}, 50)
    h.sweep({"s1": D}, 20)
    assert _refused(h.rows) == ([], [("s1", rq.AMBIGUOUS_CHRONOLOGY)])
    # two sweeps sharing one start
    h = _Hist()
    h.sweep({"s1": W}, 10)
    sw = h.sweep({"s1": D}, 20)
    for r in h.rows:
        d = json.loads(r["detail"])
        if d["sweep_id"] == sw["id"]:
            d["sweep_started_at"] = "2026-09-01T00:10:00+00:00"
            r["detail"] = json.dumps(d)
    assert _refused(h.rows) == ([], [("s1", rq.AMBIGUOUS_CHRONOLOGY)])


def test_duplicate_observation_in_one_sweep_fails_closed():
    h = _Hist()
    sw = h.sweep({"s1": W}, 10)
    h._add(ho.KIND_SPEC, "s1", h.spec_rec(sw, "s1", D))
    assert _refused(h.rows) == ([], [("s1", rq.DUPLICATE_SWEEP_OBSERVATION)])


def test_naive_timestamp_and_missing_fingerprint_fail_closed():
    h = _Hist()
    h.sweep({"s1": D}, 10, spec_over={"sweep_started_at": "2026-09-01T00:10:00"})
    assert _refused(h.rows) == ([], [("s1", rq.MALFORMED_SPEC_RECORD)])
    h = _Hist()
    h.sweep({"s1": D}, 10, spec_over={"health_fingerprint": None})
    assert _refused(h.rows) == ([], [("s1", rq.SOURCE_FINGERPRINT_MISSING)])


def test_load_rejects_a_tampered_stored_row(tmp_path):
    j = Journal(tmp_path / "j.db")
    [q] = _qs(_Hist().seq("s1", [D]).rows)
    row = rq.row_for(q)
    row["scope_id"] = "s9"
    j.record_research_question(row, recorded_at_ms=1)
    with pytest.raises(rq.ResearchQuestionError):
        rq.load(j)


# ── 9 no authority ──────────────────────────────────────────────────────
_FORBIDDEN_KEYS = {"symbol", "symbols", "asset", "instrument", "salience",
                   "rank", "priority", "urgency", "usefulness", "probability",
                   "score", "direction", "side", "size", "threshold"}


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


def test_question_carries_no_asset_or_priority_fields():
    for q in _qs(_Hist().seq("s1", [W, EF, D]).rows):
        assert not (set(_keys(q)) & _FORBIDDEN_KEYS)
        assert set(q) == set(rq._RECORD_KEYS)


def test_module_has_no_attention_risk_execution_network_or_llm_imports():
    src = Path(rq.__file__).read_text()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "hashlib", "json", "dataclasses",
                    "datetime", "trader.strategy"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(rq.__file__).resolve().parents[1]
    pat = re.compile(r"import\s+research_question|research_question\s+import"
                     r"|cognition\.research_question|record_from_journal")
    callers = [p for p in root.rglob("*.py")
               # research_plan.py / research_evidence.py /
               # research_result.py are the offline routing / collection /
               # result consumers, research_run.py the offline runner and
               # research_bank.py the offline bank filer and
               # research_recall.py the offline context-only recall and
               # research_sources.py the source registry (schema constants
               # only); their own tests guard that nothing live calls them
               if p.name not in ("research_question.py", "research_plan.py",
                                 "research_evidence.py", "research_result.py",
                                 "research_run.py", "research_bank.py",
                                 "research_recall.py", "research_sources.py")
               and pat.search(p.read_text(errors="ignore"))]
    assert callers == []


def test_derivation_is_read_only(tmp_path):
    j = Journal(tmp_path / "j.db")
    for r in _Hist().seq("s1", [W, D]).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    before = j.query("SELECT * FROM brain_events")
    rq.derive(j.strategy_health_rows())
    assert j.query("SELECT * FROM brain_events") == before
    assert j.research_questions() == []


# ── real health records ─────────────────────────────────────────────────
def test_real_health_observation_records_are_accepted():
    from tests.test_strategy_health_observation import (DOWN, UP, _analyst,
                                                        _review, _spec)
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP})
    _review(a, [_spec("s1")])
    a.frames = lambda tf, extra=(): {"BTC/USDT": DOWN, "_btc_1h": DOWN}
    _review(a, [_spec("s1")])
    rows = [{"id": i + 1, "ts": f"2026-09-25T00:00:{i:02d}+00:00",
             "kind": k, "subject": s, "detail": json.dumps(d)}
            for i, (k, s, d) in enumerate(a.journal.events)
            if k in (ho.KIND_SPEC, ho.KIND_SWEEP)]
    d = rq.derive(rows)
    assert d.refusals == []
    [q] = d.questions
    assert q["trigger"] == rq.TRANSITION_TO_DECAYED
    assert q["prior"]["verdict"] == ho.STILL_WORKING


# ── review regressions ──────────────────────────────────────────────────
# finding 1: an old gap must not suppress later, independently established
# transitions; gap-crossing episodes stay refused.
def test_old_gap_does_not_block_later_complete_episodes():
    h = _Hist()
    h.sweep({"s1": W}, 10, write_sweep=False)       # T1: unverifiable
    h.sweep({"s1": W}, 20)                          # T2: new baseline
    h.sweep({"s1": D}, 30)                          # T3
    h.sweep({"s1": W}, 40)                          # T4
    h.sweep({"s1": D}, 50)                          # T5
    d = rq.derive(h.rows)
    assert [(q["prior"]["sweep_id"], q["source"]["sweep_id"])
            for q in d.questions] == [("sw020", "sw030"), ("sw040", "sw050")]
    assert d.refusals == []


def test_decay_straight_after_a_gap_has_no_baseline_and_is_refused():
    h = _Hist()
    h.sweep({"s1": W}, 10, failed=("s1",))
    h.sweep({"s1": D}, 20)
    h.sweep({"s1": D}, 30)                          # same run: no new candidate
    h.sweep({"s1": W}, 40)
    h.sweep({"s1": D}, 50)
    d = rq.derive(h.rows)
    assert [(r.reason, r.event_id) for r in d.refusals] == [
        (rq.OBSERVATION_FAILED, 2)]
    [q] = d.questions
    assert q["source"]["sweep_id"] == "sw050" and q["prior"]["sweep_id"] == "sw040"


def test_gap_crossing_decayed_run_is_refused_then_recovers():
    h = _Hist()
    h.sweep({"s1": D}, 10)
    h.sweep({"s1": W}, 20, write_sweep=False)       # possible hidden W
    h.sweep({"s1": D}, 30)
    h.sweep({"s1": W}, 40)
    h.sweep({"s1": D}, 50)
    d = rq.derive(h.rows)
    assert [q["source"]["sweep_id"] for q in d.questions] == ["sw010", "sw050"]
    assert [(r.reason, r.sweep_id) for r in d.refusals] == [
        (rq.SWEEP_RECORD_MISSING, "sw020")]


def test_a_baseline_with_its_own_gap_is_not_a_baseline():
    h = _Hist()
    h.sweep({"s1": W}, 10, sweep_over={"observation_recorded_spec_ids": []})
    h.sweep({"s1": D}, 20)
    assert _refused(h.rows) == ([], [("s1", rq.SWEEP_RECORD_MISMATCH)])


# finding 3: contradictory sweep/spec evidence fails closed.
def test_decayed_observation_in_compile_failed_sweep_is_refused():
    h = _Hist()
    h.sweep({"s1": D}, 10, sweep_over={"evaluation_completed_spec_ids": [],
                                       "compile_failed_spec_ids": ["s1"]})
    assert _refused(h.rows) == ([], [("s1", rq.VERDICT_OUTCOME_MISMATCH)])


@pytest.mark.parametrize("over", [
    {"compile_failed_spec_ids": ["s1"]},                     # both outcomes
    {"evaluation_attempted_spec_ids": []},                   # never attempted
    {"intended_spec_ids": []},                               # not intended
    {"observation_failed_spec_ids": ["s1"]},                 # recorded+failed
    {"aborted_at_spec_id": "s1"},                            # raised+completed
])
def test_self_contradictory_sweep_lists_are_refused(over):
    h = _Hist()
    h.sweep({"s1": D}, 10, sweep_over=over)
    assert _refused(h.rows) == ([], [("s1", rq.SWEEP_RECORD_INCONSISTENT)])


def test_contradiction_in_another_spec_does_not_refuse_this_one():
    h = _Hist()
    h.sweep({"a": D, "b": D}, 10, sweep_over={"compile_failed_spec_ids": ["b"]})
    qs, ref = _refused(h.rows)
    assert [q["scope"]["spec_id"] for q in qs] == ["a"]
    assert ref == [("b", rq.SWEEP_RECORD_INCONSISTENT)]


def test_raised_evaluation_needs_the_sweep_abort_to_match():
    h = _Hist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": EF}, 20, spec_over={
        "verdict_reason": ho.EVALUATION_EXCEPTION},
        sweep_over={"status": ho.SWEEP_ABORTED, "aborted_at_spec_id": "s1",
                    "evaluation_completed_spec_ids": []})
    h.sweep({"s1": D}, 30)
    [q] = _qs(h.rows)                                  # consistent abort
    assert q["prior"]["sweep_id"] == "sw010"
    assert q["unreadable_since_prior_event_ids"] == [3]
    h = _Hist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": EF}, 20, spec_over={"verdict_reason": ho.EVALUATION_EXCEPTION})
    h.sweep({"s1": D}, 30)
    assert _refused(h.rows) == ([], [("s1", rq.VERDICT_OUTCOME_MISMATCH)])


def test_compile_failed_observation_needs_the_compile_failed_list():
    h = _Hist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": CF}, 20, sweep_over={"compile_failed_spec_ids": [],
                                        "evaluation_completed_spec_ids": ["s1"]})
    h.sweep({"s1": D}, 30)
    assert _refused(h.rows) == ([], [("s1", rq.VERDICT_OUTCOME_MISMATCH)])


# finding 2: load verifies nested schema and the bound journal evidence.
def _stored(tmp_path, verdicts):
    j = Journal(tmp_path / "j.db")
    for r in _Hist().seq("s1", verdicts).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, 1)
    return j


def _replace(j, q):
    """Overwrite the stored row with `q` (test-only tampering). The row's
    first-registration receipt binds its original content, so it goes too
    (immutability triggers dropped here, recreated on reopen)."""
    with j._tx() as c:
        c.execute("DELETE FROM research_questions")
        c.execute("DROP TRIGGER research_registrations_no_delete")
        c.execute("DELETE FROM research_registrations")
    j = Journal(j.db_path)
    assert j.record_research_question(rq.row_for(q), recorded_at_ms=1) == "inserted"


def test_load_verifies_genuine_questions_against_evidence(tmp_path):
    j = _stored(tmp_path, [W, EF, D])
    [q] = rq.load(j)
    assert q["unreadable_since_prior_event_ids"] == [3]


def _flip_thresholds(q):
    q["source"]["thresholds"] = dict(q["source"]["thresholds"], decay_floor_pf=0.1)
    q["changes_since_prior"]["thresholds_changed"] = True


@pytest.mark.parametrize("mutate", [
    _flip_thresholds,                                        # self-consistent
    lambda q: q["prior"].update(record_sha256="a" * 64),
    lambda q: q["prior"].update(event_id=2),
    lambda q: q["changes_since_prior"].update(spec_version_changed=True),
    lambda q: q.update(unreadable_since_prior_event_ids=[]),
    lambda q: q.update(unreadable_since_prior_event_ids=[2, 3]),
    lambda q: q["source"].update(symbol="BTC/USDT"),
    lambda q: q["source"].update(salience=1.0),
    lambda q: q["scope"].update(asset="BTC"),
    lambda q: q["prior"].update(salience=1.0),
    lambda q: q["question"].update(priority=1),
    lambda q: q["changes_since_prior"].update(score=1),
    lambda q: q["source"].update(event_id=True),
    lambda q: q["source"].update(retirement_action_selected="yes"),
])
def test_load_rejects_altered_content_under_the_same_id(tmp_path, mutate):
    j = _stored(tmp_path, [W, EF, D])
    [q] = rq.load(j)
    bad = json.loads(rq.canonical(q))
    mutate(bad)
    assert bad["question_id"] == q["question_id"]
    _replace(j, bad)
    with pytest.raises(rq.ResearchQuestionError):
        rq.load(j)


def test_load_rejects_a_question_whose_evidence_is_gone_or_changed(tmp_path):
    j = _stored(tmp_path, [W, D])
    [q] = rq.load(j)
    with j._tx() as c:
        c.execute("UPDATE brain_events SET detail=detail||' ' WHERE id=?",
                  (q["prior"]["event_id"],))
    with pytest.raises(rq.ResearchQuestionError, match="prior_evidence"):
        rq.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (q["source"]["event_id"],))
    with pytest.raises(rq.ResearchQuestionError, match="evidence_missing"):
        rq.load(j)


def test_load_rejects_a_truthful_observation_hidden_between(tmp_path):
    """A stored first-decay question is invalid if the journal shows an
    earlier truthful verdict."""
    j = _stored(tmp_path, [W, D])
    [q] = rq.load(j)
    bad = json.loads(rq.canonical(q))
    src = bad["source"]
    forged = {"schema": rq.SCHEMA, "question_kind": rq.QUESTION_KIND,
              "trigger": rq.FIRST_TRUTHFUL_DECAYED,
              "scope": bad["scope"], "source": src, "prior": None,
              "changes_since_prior": None,
              "unreadable_since_prior_event_ids": [],
              "semantics": rq.SEMANTICS,
              "question": {"template_id": rq.TEMPLATE_ID,
                           "text": rq.question_text(rq.FIRST_TRUTHFUL_DECAYED,
                                                    "s1", src["sweep_id"],
                                                    None, None)}}
    forged["question_id"] = rq.question_id(rq._identity(forged))
    rq.from_json(rq.canonical(forged))               # well-formed on its own
    _replace(j, forged)
    with pytest.raises(rq.ResearchQuestionError, match="unreadable_evidence"):
        rq.load(j)


# ── review regressions, round 2 ─────────────────────────────────────────
def _sweep_rows(j):
    return j.query("SELECT id, detail FROM brain_events WHERE kind=? ORDER BY id",
                   (ho.KIND_SWEEP,))


def _edit_sweep(j, sweep_id, **over):
    for r in _sweep_rows(j):
        d = json.loads(r["detail"])
        if d["sweep_id"] == sweep_id:
            d.update(over)
            with j._tx() as c:
                c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                          (json.dumps(d), r["id"]))


def test_load_rejects_when_all_supporting_sweeps_are_deleted(tmp_path):
    j = _stored(tmp_path, [W, D])
    rq.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE kind=?", (ho.KIND_SWEEP,))
    with pytest.raises(rq.ResearchQuestionError, match="source_sweep_missing"):
        rq.load(j)


def test_load_rejects_when_the_prior_sweep_is_deleted(tmp_path):
    j = _stored(tmp_path, [W, D])
    [q] = rq.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE kind=? AND subject=?",
                  (ho.KIND_SWEEP, q["prior"]["sweep_id"]))
    with pytest.raises(rq.ResearchQuestionError,
                       match="derivation_mismatch:sweep_record_missing"):
        rq.load(j)


def test_load_rejects_a_source_sweep_changed_to_compile_failed(tmp_path):
    j = _stored(tmp_path, [W, D])
    [q] = rq.load(j)
    _edit_sweep(j, q["source"]["sweep_id"], compile_failed_spec_ids=["s1"],
                evaluation_completed_spec_ids=[])
    assert rq.derive(j.strategy_health_rows()).questions == []
    with pytest.raises(rq.ResearchQuestionError,
                       match="derivation_mismatch:verdict_outcome_mismatch"):
        rq.load(j)


def test_load_rejects_an_intermediate_sweep_changed_to_a_gap(tmp_path):
    j = _stored(tmp_path, [W, EF, D])
    [q] = rq.load(j)
    _edit_sweep(j, "sw020", observation_recorded_spec_ids=[],
                observation_failed_spec_ids=["s1"])
    with pytest.raises(rq.ResearchQuestionError, match="derivation_mismatch"):
        rq.load(j)


def test_load_rejects_an_earlier_sweep_made_undecodable(tmp_path):
    j = _stored(tmp_path, [W, D])
    [first] = _sweep_rows(j)[:1]
    with j._tx() as c:
        c.execute("UPDATE brain_events SET detail='{broken' WHERE id=?",
                  (first["id"],))
    with pytest.raises(rq.ResearchQuestionError,
                       match="derivation_mismatch:malformed_sweep_record"):
        rq.load(j)


def test_later_unrelated_corruption_leaves_earlier_question_loadable(tmp_path):
    j = _stored(tmp_path, [W, D])
    [q] = rq.load(j)
    later = _Hist()
    later.n = 100
    later.sweep({"s1": W}, 900, failed=("s1",))              # later gap for s1
    later.sweep({"s1": D}, 910, sweep_over={                 # later contradiction
        "compile_failed_spec_ids": ["s1"]})
    later.sweep({"s2": D}, 920, spec_over={"verdict": "bogus"})  # other spec
    for r in later.rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    j.log_brain_event(ho.KIND_SWEEP, "swBAD", "{broken")     # later, undecodable
    j.log_brain_event(ho.KIND_SPEC, "s1", "{broken")         # later, same spec
    j.log_brain_event(ho.KIND_SPEC, None, "{}")              # unattributable
    # the full current history is now refused as a whole...
    assert rq.derive(j.strategy_health_rows()).questions == []
    # ...but the earlier question's bounded evidence is intact
    assert rq.load(j) == [q]


def _numeric(v):
    return int(v) if isinstance(v, bool) else v


@pytest.mark.parametrize("flag", rq._CHANGE_FIELDS)
def test_numeric_change_flags_are_rejected(tmp_path, flag):
    j = _stored(tmp_path, [W, D])
    [q] = rq.load(j)
    bad = json.loads(rq.canonical(q))
    bad["changes_since_prior"][flag] = 0                     # 0 == False
    assert bad["changes_since_prior"] == q["changes_since_prior"]
    with pytest.raises(rq.ResearchQuestionError, match="changes_since_prior"):
        rq.from_json(rq.canonical(bad))
    _replace(j, bad)
    with pytest.raises(rq.ResearchQuestionError, match="changes_since_prior"):
        rq.load(j)


def test_numeric_true_flag_is_rejected():
    h = _Hist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": D}, 20, spec_over={"health_fingerprint": "fp2"})
    [q] = _qs(h.rows)
    assert q["changes_since_prior"]["spec_version_changed"] is True
    bad = json.loads(rq.canonical(q))
    bad["changes_since_prior"] = {k: _numeric(v)
                                  for k, v in bad["changes_since_prior"].items()}
    assert bad["changes_since_prior"] == q["changes_since_prior"]
    with pytest.raises(rq.ResearchQuestionError, match="changes_since_prior"):
        rq.from_json(rq.canonical(bad))


# ── review regressions, round 3 ─────────────────────────────────────────
def test_load_rejects_question_preceded_by_unattributable_spec_row(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.log_brain_event(ho.KIND_SPEC, None, "{}")              # before W -> D
    h = _Hist()
    h.n = 1
    h.seq("s1", [W, D])
    for r in h.rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rows = j.strategy_health_rows()
    assert rq.derive(rows).refusals == [
        rq.Refusal(None, rq.UNATTRIBUTABLE_SPEC_RECORD, 1)]
    # a question derived while omitting that row...
    [q] = rq.derive([r for r in rows if r["subject"] is not None]).questions
    assert j.record_research_question(rq.row_for(q), recorded_at_ms=1) == "inserted"
    # ...must not load
    with pytest.raises(rq.ResearchQuestionError,
                       match="derivation_mismatch:unattributable_spec_record"):
        rq.load(j)


def test_load_rejects_same_spec_row_without_usable_event_id():
    h = _Hist().seq("s1", [W, D])
    [q] = _qs(h.rows)
    rows = h.rows + [dict(h.rows[0], id="x")]                # unplaceable copy
    with pytest.raises(rq.ResearchQuestionError,
                       match="derivation_mismatch:malformed_spec_record"):
        rq.verify_evidence(q, rows)


def test_identifiable_other_spec_rows_before_the_question_are_ignored():
    h = _Hist()
    h._add(ho.KIND_SPEC, "s2", "{broken")                   # other spec, earlier
    h.seq("s1", [W, D])
    [q] = _qs([r for r in h.rows if r["subject"] == "s1"
               or r["kind"] == ho.KIND_SWEEP])
    rq.verify_evidence(q, h.rows)
