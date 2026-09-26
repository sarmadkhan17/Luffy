"""strategy-health-unreadable-question.v1: one context_only research question
per unreadable (compile_failed / evaluation_failed) strategy-health
observation, carrying the stored reasons verbatim, persisted apart from the
strategy-decay research-question.v1 chain, which stays byte-identical.
"""
import ast
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_research_source_registry import _fixture, _plans_now
from tests.test_strategy_decay_research_plan import (CF, D, EF, I, W,
                                                     _FullHist)
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_unreadable_question as uq
from trader.core.journal import Journal
from trader.strategy import health_observation as ho
from trader.strategy.spec import ExitSpec, StrategySpec

ROOT = Path(__file__).resolve().parents[1]
UQ_PATH = "trader/cognition/research_unreadable_question.py"


def pinned(path, text):
    """Byte-pin text for an upstream module: unchanged, except that the
    unreadable question module's docstring (its registration contract note)
    is dropped; every executable and comment byte after it stays pinned."""
    if path != UQ_PATH:
        return text
    doc = ast.parse(text).body[0]
    assert isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
    return "\n".join(text.split("\n")[doc.end_lineno:])
TH = {"decay_recent_days": 60.0, "decay_min_trades": 3, "decay_floor_pf": 0.85}
T0 = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _spec(sid="s1"):
    return StrategySpec(
        id=sid, name=f"name-{sid}",
        thesis="A hypothesis of sufficient length for the validator, naming a "
               "plausible short-horizon inefficiency that a rolling gate can "
               "confirm or refute on recent data.",
        invalidation="Retire below profit factor 0.85 over the last 30 days.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="1h", direction="long", entry_long="close > ema(5)",
        entry_short="", filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 24}),
        regime_filter=["TRENDING_UP"], markets=["futures"])


#: diag shapes the Analyst hands Sweep.observed
COMPLETE = {"branch": "still_working", "attempted": ["BTC/USDT"],
            "scored": {"BTC/USDT": {"scoring": {"scorable_bars": 10},
                                    "missing_context": []}}}
NO_FRAME = {"branch": "decayed", "attempted": ["BTC/USDT"], "scored": {},
            "no_frame": ["BTC/USDT"]}


def _write(j, outcomes, i):
    """One real health sweep through ho.Sweep: {spec_id: outcome} where
    outcome is 'compile', 'raise' or a diag dict."""
    specs = {sid: _spec(sid) for sid in outcomes}
    sw = ho.Sweep(j, list(specs.values()), TH,
                  now=T0 + timedelta(hours=i))
    for sid, out in outcomes.items():
        spec = specs[sid]
        sw.attempt(spec)
        if out == "compile":
            sw.compile_failed(spec, SyntaxError("bad entry"))
        elif out == "raise":
            sw.evaluation_raised(spec, "evaluate", RuntimeError("boom"))
            break
        else:
            sw.observed(spec, out.get("branch") == "decayed",
                        {"verdict": "t"}, dict(out), {})
    else:
        sw.finish()
    sw.flush()
    return sw.sweep_id


def _journal(tmp_path, *sweeps, name="j.db"):
    j = Journal(tmp_path / name)
    for i, s in enumerate(sweeps):
        _write(j, s, i)
    return j


def _verdicts(j):
    return [json.loads(r["detail"])["verdict"] for r in j.strategy_health_rows()
            if r["kind"] == ho.KIND_SPEC]


# ── 1-2 one deterministic question per unreadable observation ────────────
def test_compile_failed_produces_exactly_one_question(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    assert _verdicts(j) == [CF]
    d = uq.derive(j.strategy_health_rows())
    [q] = d.questions
    assert not d.refusals
    assert q["question_kind"] == uq.QUESTION_KIND and q["schema"] == uq.SCHEMA
    assert q["authority"] == "context_only"
    assert q["scope"] == {"kind": "strategy", "spec_id": "s1"}
    assert uq.derive(list(reversed(j.strategy_health_rows()))).questions == [q]


@pytest.mark.parametrize("outcome,reason", [
    ("raise", ho.EVALUATION_EXCEPTION),
    (NO_FRAME, ho.NO_FRAME),
    ({"branch": "idle"}, ho.COVERAGE_UNAVAILABLE)])
def test_evaluation_failed_produces_exactly_one_question(tmp_path, outcome,
                                                         reason):
    j = _journal(tmp_path, {"s1": outcome})
    assert _verdicts(j) == [EF]
    [q] = uq.derive(j.strategy_health_rows()).questions
    assert q["recorded_reasons"]["verdict"] == EF
    assert q["recorded_reasons"]["verdict_reason"] == reason


def test_each_unreadable_sweep_is_its_own_question_no_grouping(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": "compile"},
                 {"s1": COMPLETE}, {"s1": NO_FRAME})
    qs = uq.derive(j.strategy_health_rows()).questions
    assert [q["recorded_reasons"]["verdict"] for q in qs] == [CF, CF, EF]
    assert len({q["question_id"] for q in qs}) == 3
    assert [q["source"]["event_id"] for q in qs] == sorted(
        q["source"]["event_id"] for q in qs)


# ── 3 stored reasons preserved verbatim ─────────────────────────────────
def test_stored_reasons_are_copied_verbatim(tmp_path):
    j = _journal(tmp_path, {"s1": "compile", "s2": NO_FRAME, "s3": "raise"})
    recs = {r["subject"]: json.loads(r["detail"])
            for r in j.strategy_health_rows() if r["kind"] == ho.KIND_SPEC}
    qs = {q["scope"]["spec_id"]: q
          for q in uq.derive(j.strategy_health_rows()).questions}
    assert set(qs) == {"s1", "s2", "s3"}
    for sid, q in qs.items():
        rec, r = recs[sid], q["recorded_reasons"]
        assert r["verdict"] == rec["verdict"]
        assert r["verdict_reason"] == rec["verdict_reason"]
        cov, err = rec.get("coverage"), rec.get("error")
        assert r["coverage_faults"] == (cov and cov["coverage_faults"])
        assert r["coverage_complete"] == (cov and cov["coverage_complete"])
        assert r["error_stage"] == (err and err["stage"])
        assert r["error_class"] == (err and err["error_class"])
    assert qs["s2"]["recorded_reasons"]["coverage_faults"] == [ho.NO_FRAME]
    assert qs["s1"]["recorded_reasons"]["error_class"] == "SyntaxError"
    assert qs["s1"]["question"]["text"] == (
        "Does the unreadable strategy-health observation of strategy s1 in "
        f"decay sweep {qs['s1']['source']['sweep_id']} (verdict "
        "compile_failed; verdict_reason not_recorded; coverage_faults "
        "not_recorded) represent a capability or data-evaluation gap "
        "described by those recorded reasons?")


# ── 4 readable observations produce none ────────────────────────────────
@pytest.mark.parametrize("verdicts", [[W], [I], [D], [W, D, D, I]])
def test_readable_verdicts_produce_no_question(verdicts):
    d = uq.derive(_FullHist().seq("s1", verdicts).rows)
    assert d.questions == [] and d.refusals == []


def test_real_writer_truthful_verdict_produces_none(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE})
    assert _verdicts(j) == [W]
    assert uq.derive(j.strategy_health_rows()).questions == []


def test_decayed_branch_under_evaluation_failed_is_only_unreadable(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    [q] = uq.derive(j.strategy_health_rows()).questions
    assert "lifecycle_branch" not in json.dumps(q["recorded_reasons"])
    assert rq.derive(j.strategy_health_rows()).questions == []


# ── 5 no invented fields ────────────────────────────────────────────────
def test_exact_shape_has_no_invented_fields(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    [q] = uq.derive(j.strategy_health_rows()).questions
    assert set(q) == {"schema", "question_id", "question_kind", "authority",
                      "scope", "question", "source", "recorded_reasons",
                      "semantics"}
    assert set(q["source"]) == {
        "schema", "kind", "event_id", "event_ts", "record_sha256", "sweep_id",
        "sweep_started_at", "spec_id", "spec_name", "health_fingerprint",
        "spec_content_sha256", "sweep_record"}
    assert set(q["recorded_reasons"]) == {
        "verdict", "verdict_reason", "coverage_complete", "coverage_faults",
        "error_stage", "error_class"}
    text = uq.canonical({k: v for k, v in q.items() if k != "semantics"})
    for banned in ("BTC", "symbol", "threshold", "predicate", "regime",
                   "salience", "priority", "score", "usefulness", "vendor",
                   "diagnosis", "root_cause", "message", "boom", "bad entry"):
        assert banned not in text, banned


# ── 6 tampered or missing source fails closed ───────────────────────────
def _stored(tmp_path, outcomes=None):
    j = _journal(tmp_path, outcomes or {"s1": NO_FRAME})
    res = uq.record_from_journal(j, now_ms=1)
    assert len(res["inserted"]) == 1 and not res["refusals"]
    return j


def _tamper(j, kind, fn):
    with j._tx() as c:
        rid, detail = c.execute("SELECT id, detail FROM brain_events "
                                "WHERE kind=?", (kind,)).fetchone()
        c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                  (fn(detail), rid))


def test_tampered_source_observation_fails_closed(tmp_path):
    j = _stored(tmp_path)
    _tamper(j, ho.KIND_SPEC, lambda d: d.replace('"no_frame"', '"no_frame" '))
    with pytest.raises(uq.UnreadableQuestionError, match="derivation_mismatch"):
        uq.load(j)


def test_tampered_sweep_record_fails_closed(tmp_path):
    j = _stored(tmp_path)
    _tamper(j, ho.KIND_SWEEP, lambda d: d.replace('"status"', '"status" '))
    with pytest.raises(uq.UnreadableQuestionError, match="derivation_mismatch"):
        uq.load(j)


def test_missing_source_fails_closed(tmp_path):
    j = _stored(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE kind=?", (ho.KIND_SPEC,))
    with pytest.raises(uq.UnreadableQuestionError, match="derivation_mismatch"):
        uq.load(j)


def test_missing_sweep_record_fails_closed(tmp_path):
    j = _stored(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE kind=?", (ho.KIND_SWEEP,))
    with pytest.raises(uq.UnreadableQuestionError, match="evidence_missing"):
        uq.load(j)
    d = uq.derive(j.strategy_health_rows())
    assert d.questions == []
    assert [r.reason for r in d.refusals] == [rq.SWEEP_RECORD_MISSING]


def test_tampered_stored_question_fails_closed(tmp_path):
    j = _stored(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_questions SET canonical_json="
                  "replace(canonical_json, 'no_frame', 'no_scorable_bars')")
    with pytest.raises(uq.UnreadableQuestionError):
        uq.load(j)


@pytest.mark.parametrize("over,reason", [
    ({"verdict_reason": "made_up"}, uq.SOURCE_REASONS_MALFORMED),
    ({"error": None}, uq.SOURCE_REASONS_MALFORMED),
    ({"coverage": {"coverage_complete": False,
                   "coverage_faults": ["invented"]}},
     uq.SOURCE_REASONS_MALFORMED)])
def test_reasons_outside_writer_vocabulary_are_refused(over, reason):
    h = _FullHist()
    h.sweep({"s1": CF}, 10, spec_over=over)
    d = uq.derive(h.rows)
    assert d.questions == [] and [r.reason for r in d.refusals] == [reason]


def test_verdict_contradicting_sweep_outcome_is_refused():
    h = _FullHist()
    h.sweep({"s1": CF}, 10, sweep_over={"compile_failed_spec_ids": [],
                                        "evaluation_completed_spec_ids": ["s1"]})
    d = uq.derive(h.rows)
    assert d.questions == []
    assert [r.reason for r in d.refusals] == [rq.VERDICT_OUTCOME_MISMATCH]


def test_existing_history_refusals_are_reused():
    h = _FullHist()
    h.sweep({"s1": CF}, 10)
    h._add(ho.KIND_SWEEP, "junk", "{not json")
    d = uq.derive(h.rows)
    assert d.questions == []
    assert [r.reason for r in d.refusals] == [rq.MALFORMED_SWEEP_RECORD]


# ── 7 determinism ───────────────────────────────────────────────────────
def test_id_canonical_json_and_hash_are_deterministic(tmp_path):
    a = _journal(tmp_path, {"s1": NO_FRAME}, name="a.db")
    rows = a.strategy_health_rows()
    [q1] = uq.derive(rows).questions
    [q2] = uq.derive(list(reversed(rows))).questions
    assert uq.canonical(q1) == uq.canonical(q2)
    assert uq.from_json(uq.canonical(q1)) == q1
    src = q1["source"]
    assert q1["question_id"] == hashlib.sha256(uq.canonical({
        "schema": uq.SCHEMA, "question_kind": uq.QUESTION_KIND,
        "spec_id": "s1", "source_schema": ho.SCHEMA,
        "source_kind": ho.KIND_SPEC, "source_event_id": src["event_id"],
        "source_record_sha256": src["record_sha256"]}).encode()).hexdigest()
    [row] = [r for r in rows if r["kind"] == ho.KIND_SPEC]
    assert src["record_sha256"] == hashlib.sha256(
        row["detail"].encode()).hexdigest()
    row = uq.row_for(q1)
    assert row["canonical_sha256"] == hashlib.sha256(
        row["canonical_json"].encode()).hexdigest()


def test_non_canonical_text_is_refused(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [q] = uq.derive(j.strategy_health_rows()).questions
    with pytest.raises(uq.UnreadableQuestionError, match="not_canonical"):
        uq.from_json(json.dumps(q, sort_keys=True))


# ── 8-9 idempotence and conflicts ───────────────────────────────────────
def test_identical_write_is_idempotent(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    first = uq.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2
    again = uq.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(j.db_path)
    assert uq.record_from_journal(j2, now_ms=3)["duplicate"] == first["inserted"]
    assert [q["question_id"] for q in uq.load(j2)] == first["inserted"]
    assert {r["recorded_at_ms"] for r in j2.research_unreadable_questions()} == {1}


def test_conflicting_write_never_overwrites(tmp_path):
    j = _stored(tmp_path)
    [row] = j.research_unreadable_questions()
    q = json.loads(row["canonical_json"])
    other = dict(uq.row_for(q), canonical_json=row["canonical_json"] + " ",
                 canonical_sha256="0" * 64)
    assert j.record_research_unreadable_question(other, recorded_at_ms=9) \
        == "conflict"
    same_source = dict(uq.row_for(q), question_id="f" * 64)
    assert j.record_research_unreadable_question(same_source,
                                                 recorded_at_ms=9) == "conflict"
    assert j.research_unreadable_questions() == [row]


# ── 10 strategy-decay questions stay byte-identical ─────────────────────
def test_decay_question_module_is_unchanged():
    import subprocess
    blob = subprocess.run(
        ["git", "-C", str(ROOT), "show",
         "f5de897:trader/cognition/research_question.py"],
        capture_output=True, text=True)
    if blob.returncode != 0:
        pytest.skip("baseline commit not available")
    assert (ROOT / "trader/cognition/research_question.py").read_text() \
        == blob.stdout


def test_pinned_decay_plans_and_questions_are_byte_identical():
    for name, scen in _fixture().items():
        assert _plans_now(scen["verdicts"]) == [
            p["canonical_json"] for p in scen["plans"]], name


@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, EF, D], [CF, EF, D]])
def test_decay_questions_unchanged_by_unreadable_family(tmp_path, verdicts):
    j = Journal(tmp_path / "j.db")
    for r in _FullHist().seq("s1", verdicts).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    before = [rq.canonical(q) for q in rq.derive(j.strategy_health_rows()).questions]
    dq = rq.record_from_journal(j, now_ms=1)
    rows_before = j.research_questions()
    uqs = uq.record_from_journal(j, now_ms=2)
    assert len(uqs["inserted"]) == verdicts.count(CF) + verdicts.count(EF)
    assert j.research_questions() == rows_before
    assert [rq.canonical(q) for q in rq.load(j)] == before
    assert rq.record_from_journal(j, now_ms=3)["duplicate"] == dq["inserted"]


# ── 11-12 planner unchanged; new family cannot enter Q->P->E->R ─────────
def test_planner_behaviour_is_unchanged_by_new_rows(tmp_path):
    j = Journal(tmp_path / "j.db")
    for r in _FullHist().seq("s1", [W, CF, EF, D]).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    [qrow] = j.research_questions()
    before = rp.canonical(rp.build(qrow["canonical_json"],
                                   j.strategy_health_rows()))
    assert len(uq.record_from_journal(j, now_ms=1)["inserted"]) == 2
    assert rp.canonical(rp.build(qrow["canonical_json"],
                                 j.strategy_health_rows())) == before
    res = rp.record_from_journal(j, now_ms=2)
    assert len(res["inserted"]) == 1 and res["refusals"] == []
    assert [rp.canonical(p) for p in rp.load(j)] == [before]


def test_planner_refuses_unreadable_question_explicitly(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [q] = uq.derive(j.strategy_health_rows()).questions
    with pytest.raises(rp.ResearchPlanError, match="question_invalid:keys"):
        rp.build(uq.canonical(q), j.strategy_health_rows())
    with pytest.raises(rq.ResearchQuestionError):
        rq.from_json(uq.canonical(q))


def test_new_family_is_invisible_to_the_decay_chain_tables(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    tables = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT IN ('research_unreadable_questions', "
        "'research_registrations')")]
    before = {t: j.query(f'SELECT * FROM "{t}"') for t in tables}
    assert len(uq.record_from_journal(j, now_ms=1)["inserted"]) == 2
    assert {t: j.query(f'SELECT * FROM "{t}"') for t in tables} == before
    assert j.research_questions() == [] and j.research_plans() == []
    # only this family's own first-registration receipts appear
    assert {r["record_type"] for r in j.query(
        "SELECT * FROM research_registrations")} == {uq.SCHEMA}


# ── 13 no live wiring ───────────────────────────────────────────────────
def test_module_has_no_live_wiring():
    src = (ROOT / "trader/cognition/research_unreadable_question.py").read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imports.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
    assert imports == {"__future__:annotations", "hashlib", "json",
                       "dataclasses:dataclass,field",
                       "trader.cognition:research_question",
                       "trader.strategy:health_observation"}


def test_nothing_imports_the_new_module():
    hits = []
    for p in (ROOT / "trader").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            names = ([node.module or ""] + [a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else
                     [a.name for a in node.names]
                     if isinstance(node, ast.Import) else [])
            if any("research_unreadable_question" in n for n in names):
                hits.append(p.name)
    # only the offline sibling plan, the offline unreadable runner and the
    # offline unreadable bank filer, whose own tests guard their callers
    assert sorted(hits) == ["research_unreadable_bank.py",
                            "research_unreadable_plan.py",
                            "research_unreadable_run.py"]
