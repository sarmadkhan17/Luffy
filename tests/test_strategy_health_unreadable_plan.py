"""strategy-health-unreadable-plan.v1: one context_only evidence-routing plan
per verified strategy-health-unreadable-question.v1, routing only to the
exact source observation, its bound sweep record and the spec's earlier
observation history, persisted apart from research-plan.v1, whose tables,
fixtures and the source registry stay byte-identical.
"""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_research_source_registry import _fixture, _plans_now
from tests.test_strategy_decay_research_plan import CF, D, EF, W, _FullHist
from tests.test_strategy_health_unreadable_question import (COMPLETE,
                                                            NO_FRAME,
                                                            _journal)
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_sources as rs
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]
BASE = "2c5a70e"                         # HEAD this package was cut from


def _questions(j):
    res = uq.record_from_journal(j, now_ms=1)
    assert not res["refusals"]
    return j.research_unreadable_questions()


def _plan(j, qrow):
    return up.build(qrow["canonical_json"], j.strategy_health_rows())


def _route(plan, rid):
    [r] = [r for r in plan["routes"] if r["route_id"] == rid]
    return r


def _spec_rows(j):
    return [r for r in j.strategy_health_rows() if r["kind"] == ho.KIND_SPEC]


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


# ── 1-2 compile_failed / evaluation_failed get a deterministic plan ─────
def test_compile_failed_question_gets_deterministic_plan(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [qrow] = _questions(j)
    plan = _plan(j, qrow)
    assert plan["schema"] == up.SCHEMA == "strategy-health-unreadable-plan.v1"
    assert plan["plan_kind"] == uq.QUESTION_KIND
    assert plan["authority"] == "context_only"
    assert plan["scope"] == {"kind": "strategy", "spec_id": "s1"}
    assert up.canonical(_plan(j, qrow)) == up.canonical(plan)
    rows = j.strategy_health_rows()
    assert up.canonical(up.build(qrow["canonical_json"],
                                 list(reversed(rows)))) == up.canonical(plan)


@pytest.mark.parametrize("outcome", ["raise", NO_FRAME, {"branch": "idle"}])
def test_evaluation_failed_question_gets_deterministic_plan(tmp_path, outcome):
    j = _journal(tmp_path, {"s1": outcome})
    [qrow] = _questions(j)
    assert json.loads(qrow["canonical_json"])["recorded_reasons"][
        "verdict"] == EF
    a, b = _plan(j, qrow), _plan(j, qrow)
    assert up.canonical(a) == up.canonical(b)
    assert up.from_json(up.canonical(a), qrow["canonical_json"],
                        j.strategy_health_rows()) == a


# ── 3 only allowed existing internal evidence is routed ─────────────────
def test_only_the_three_internal_health_routes(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": "compile"})
    [qrow] = _questions(j)
    plan = _plan(j, qrow)
    assert [r["route_id"] for r in plan["routes"]] == list(up.ROUTE_IDS) == [
        "source_observation", "source_sweep", "spec_observation_history"]
    health = rs.descriptor(rs.HEALTH_SOURCE_ID)
    for r in plan["routes"]:
        assert r["source_id"] == rs.HEALTH_SOURCE_ID
        assert {k: r[k] for k in health} == health
    assert {r["record_kind"] for r in plan["routes"]} == {ho.KIND_SPEC,
                                                          ho.KIND_SWEEP}
    text = up.canonical(plan)
    for other in (rs.QUESTION_SOURCE_ID, rs.DECISIONS_SOURCE_ID,
                  rs.WORLDMODEL_REGIME_SOURCE_ID, "decisions",
                  "http", "external"):
        assert other not in text, other


def test_routing_mapping_is_frozen_and_pinned():
    text = up.canonical_routing()
    assert _sha(text) == up.ROUTING_SHA256
    m = up.routing()
    assert m["schema"] == up.ROUTING_SCHEMA
    assert m["question_schema"] == uq.SCHEMA
    assert m["registry"] == {"schema": rs.SCHEMA, "sha256": rs.REGISTRY_SHA256,
                             "source_id": rs.HEALTH_SOURCE_ID}
    assert [r["cardinality"] for r in m["routes"]] == [
        up.EXACTLY_ONE, up.EXACTLY_ONE, up.ZERO_OR_MORE]
    m["routes"].clear()                  # a returned copy cannot mutate it
    assert up.canonical_routing() == text


# ── 4 no diagnosis or new reason ────────────────────────────────────────
def test_plan_carries_no_diagnosis_or_new_reason(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME}, {"s1": "raise"})
    for qrow in _questions(j):
        plan = _plan(j, qrow)
        assert set(plan) == {"schema", "plan_id", "plan_kind", "planner_id",
                             "authority", "routing", "source_question",
                             "scope", "routes", "semantics"}
        for r in plan["routes"]:
            assert set(r) == {"route_id", "source_id", "store", "reader",
                              "record_schema", "record_kind", "status",
                              "records"}
            assert r["status"] in (up.AVAILABLE, up.EMPTY)
            for rec in r["records"]:
                assert set(rec) == {"event_id", "record_sha256"}
        text = up.canonical({k: v for k, v in plan.items()
                             if k != "semantics"})
        for banned in ("BTC", "symbol", "threshold", "predicate", "falsif",
                       "regime", "salience", "priority", "rank", "score",
                       "usefulness", "vendor", "credib", "novel", "budget",
                       "cost", "diagnos", "root_cause", "cause", "proven",
                       "no_frame", "evaluation_exception", "compile_failed",
                       "evaluation_failed", "coverage", "message", "boom"):
            assert banned not in text, banned


# ── 5 exact bindings ────────────────────────────────────────────────────
def test_exact_observation_sweep_and_history_bindings(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": "compile"},
                 {"s2": COMPLETE}, {"s1": NO_FRAME})
    qrows = _questions(j)
    assert len(qrows) == 2
    rows = {r["id"]: r for r in j.strategy_health_rows()}
    s1 = [r for r in _spec_rows(j) if r["subject"] == "s1"]
    for qrow in qrows:
        q = json.loads(qrow["canonical_json"])
        plan = _plan(j, qrow)
        src = q["source"]
        assert plan["source_question"] == {
            "schema": uq.SCHEMA, "question_id": q["question_id"],
            "question_kind": uq.QUESTION_KIND,
            "canonical_sha256": _sha(qrow["canonical_json"])}
        [obs] = _route(plan, "source_observation")["records"]
        assert obs == {"event_id": src["event_id"],
                       "record_sha256": src["record_sha256"]}
        assert obs["record_sha256"] == _sha(rows[obs["event_id"]]["detail"])
        [sw] = _route(plan, "source_sweep")["records"]
        assert sw == {"event_id": src["sweep_record"]["event_id"],
                      "record_sha256": src["sweep_record"]["record_sha256"]}
        assert rows[sw["event_id"]]["subject"] == src["sweep_id"]
        hist = _route(plan, "spec_observation_history")
        assert hist["records"] == [
            {"event_id": r["id"], "record_sha256": _sha(r["detail"])}
            for r in s1 if r["id"] < src["event_id"]]
        assert hist["status"] == up.AVAILABLE
        # never another spec's rows
        assert all(rows[x["event_id"]]["subject"] == "s1"
                   for x in hist["records"])
    first, second = (_plan(j, r) for r in qrows)
    assert len(_route(first, "spec_observation_history")["records"]) == 1
    assert len(_route(second, "spec_observation_history")["records"]) == 2


def test_no_prior_history_is_stated_empty(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [qrow] = _questions(j)
    hist = _route(_plan(j, qrow), "spec_observation_history")
    assert hist["status"] == up.EMPTY and hist["records"] == []


# ── 6 tampering / missing records fail closed ───────────────────────────
def _tamper_id(j, rid, fn):
    with j._tx() as c:
        [detail] = c.execute("SELECT detail FROM brain_events WHERE id=?",
                             (rid,)).fetchone()
        c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                  (fn(detail), rid))


def _stored(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    _questions(j)
    res = up.record_from_journal(j, now_ms=5)
    assert len(res["inserted"]) == 1 and not res["refusals"]
    return j


def test_tampered_source_observation_fails_closed(tmp_path):
    j = _stored(tmp_path)
    [_, src] = _spec_rows(j)
    _tamper_id(j, src["id"], lambda d: d.replace('"no_frame"', '"no_frame" '))
    with pytest.raises(up.UnreadablePlanError, match="question_evidence"):
        up.load(j)
    [qrow] = j.research_unreadable_questions()
    with pytest.raises(up.UnreadablePlanError, match="question_evidence"):
        _plan(j, qrow)


def test_tampered_sweep_record_fails_closed(tmp_path):
    j = _stored(tmp_path)
    sweeps = [r for r in j.strategy_health_rows() if r["kind"] == ho.KIND_SWEEP]
    _tamper_id(j, sweeps[-1]["id"], lambda d: d.replace('"status"',
                                                        '"status" '))
    with pytest.raises(up.UnreadablePlanError, match="question_evidence"):
        up.load(j)


def test_tampered_history_after_planning_fails_closed(tmp_path):
    j = _stored(tmp_path)
    [hist, _] = _spec_rows(j)
    _tamper_id(j, hist["id"], lambda d: d.replace('"spec_name"',
                                                  '"spec_name" '))
    with pytest.raises(up.UnreadablePlanError, match="plan_content"):
        up.load(j)
    # re-planning after the change is a conflict, never an overwrite
    before = j.research_unreadable_plans()
    res = up.record_from_journal(j, now_ms=9)
    assert res["inserted"] == [] and res["duplicate"] == []
    assert res["conflict"] == [before[0]["plan_id"]]
    assert j.research_unreadable_plans() == before


def test_missing_history_fails_closed(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [qrow] = _questions(j)
    [hist, _] = _spec_rows(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (hist["id"],))
    with pytest.raises(up.UnreadablePlanError, match="^history_incomplete$"):
        _plan(j, qrow)
    res = up.record_from_journal(j, now_ms=1)
    assert res["inserted"] == [] and len(res["refusals"]) == 1
    assert j.research_unreadable_plans() == []


def test_missing_source_and_sweep_fail_closed(tmp_path):
    for kind in (ho.KIND_SPEC, ho.KIND_SWEEP):
        j = _journal(tmp_path, {"s1": NO_FRAME}, name=f"{kind}.db")
        [qrow] = _questions(j)
        with j._tx() as c:
            c.execute("DELETE FROM brain_events WHERE kind=?", (kind,))
        with pytest.raises(up.UnreadablePlanError, match="question_evidence"):
            _plan(j, qrow)


def test_unsupported_or_malformed_question_fails_closed(tmp_path):
    j = Journal(tmp_path / "d.db")
    for r in _FullHist().seq("s1", [W, D]).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    [dq] = rq.derive(j.strategy_health_rows()).questions
    rows = j.strategy_health_rows()
    with pytest.raises(up.UnreadablePlanError, match="question_invalid"):
        up.build(rq.canonical(dq), rows)
    for bad in (None, "", "{", "[]", json.dumps({"schema": uq.SCHEMA})):
        with pytest.raises(up.UnreadablePlanError, match="question_invalid"):
            up.build(bad, rows)


def test_tampered_stored_question_row_is_refused(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    _questions(j)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_questions SET scope_id='s9'")
    res = up.record_from_journal(j, now_ms=1)
    assert res["inserted"] == []
    assert [r for _, r in res["refusals"]] == [
        "question_invalid:row_projection"]


def test_tampered_stored_plan_fails_closed(tmp_path):
    j = _stored(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_plans SET canonical_json="
                  "replace(canonical_json, 'EMPTY', 'AVAILABLE')")
        c.execute("UPDATE research_unreadable_plans SET canonical_json="
                  "replace(canonical_json, '\"records\":[{', "
                  "'\"records\":[{\"x\":1,')")
    with pytest.raises(up.UnreadablePlanError):
        up.load(j)


# ── 7 deterministic ID / JSON / hash ────────────────────────────────────
def test_id_canonical_json_and_hash_are_deterministic(tmp_path):
    a = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME}, name="a.db")
    b = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME}, name="b.db")
    [qa], [qb] = _questions(a), _questions(b)
    pa, pb = _plan(a, qa), _plan(b, qb)
    # identical content modulo the writer's run ids: compare the structure
    assert set(pa) == set(pb)
    plan = pa
    assert plan["plan_id"] == _sha(up.canonical({
        "schema": up.SCHEMA, "plan_kind": up.PLAN_KIND,
        "planner_id": up.PLANNER_ID, "routing_sha256": up.ROUTING_SHA256,
        "question_id": plan["source_question"]["question_id"],
        "question_canonical_sha256": _sha(qa["canonical_json"])}))
    row = up.row_for(plan)
    assert row["canonical_json"] == up.canonical(plan)
    assert row["canonical_sha256"] == _sha(row["canonical_json"])
    with pytest.raises(up.UnreadablePlanError, match="not_canonical"):
        up.from_json(json.dumps(plan, sort_keys=True), qa["canonical_json"],
                     a.strategy_health_rows())


def test_fixed_rows_give_a_fixed_plan():
    h = _FullHist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": CF}, 20)
    [q] = uq.derive(h.rows).questions
    p1 = up.build(uq.canonical(q), h.rows)
    p2 = up.build(uq.canonical(q), list(reversed(h.rows)))
    assert up.canonical(p1) == up.canonical(p2)
    assert [x["records"] for x in p1["routes"]] == [
        [{"event_id": 3, "record_sha256": _sha(h.rows[2]["detail"])}],
        [{"event_id": 4, "record_sha256": _sha(h.rows[3]["detail"])}],
        [{"event_id": 1, "record_sha256": _sha(h.rows[0]["detail"])}]]


# ── 8-9 idempotence and conflicts ───────────────────────────────────────
def test_duplicate_write_is_idempotent(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    _questions(j)
    first = up.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2 and not first["refusals"]
    again = up.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(j.db_path)
    assert up.record_from_journal(j2, now_ms=3)["duplicate"] == \
        first["inserted"]
    assert [p["plan_id"] for p in up.load(j2)] == first["inserted"]
    assert {r["recorded_at_ms"] for r in j2.research_unreadable_plans()} == {1}


def test_conflict_never_overwrites(tmp_path):
    j = _stored(tmp_path)
    [row] = j.research_unreadable_plans()
    plan = json.loads(row["canonical_json"])
    base = up.row_for(plan)
    for other in (dict(base, canonical_json=row["canonical_json"] + " ",
                       canonical_sha256="0" * 64),
                  dict(base, plan_id="f" * 64),
                  dict(base, plan_id="e" * 64, question_id="d" * 64)):
        assert j.record_research_unreadable_plan(other, recorded_at_ms=9) \
            == "conflict"
    assert j.research_unreadable_plans() == [row]


# ── 10 decay planner still refuses unreadable questions ─────────────────
def test_decay_planner_still_refuses_unreadable_question(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [qrow] = _questions(j)
    with pytest.raises(rp.ResearchPlanError, match="question_invalid:keys"):
        rp.build(qrow["canonical_json"], j.strategy_health_rows())
    up.record_from_journal(j, now_ms=1)
    res = rp.record_from_journal(j, now_ms=2)
    assert res == {"inserted": [], "duplicate": [], "conflict": [],
                   "refusals": []}
    assert j.research_plans() == []


def test_sibling_planner_refuses_decay_plan_input(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [qrow] = _questions(j)
    plan = _plan(j, qrow)
    with pytest.raises(rp.ResearchPlanError):
        rp.from_json(up.canonical(plan), qrow["canonical_json"],
                     j.strategy_health_rows())


# ── 11-12 decay fixtures and registry byte-identical ────────────────────
def test_existing_decay_plan_fixtures_are_byte_identical():
    for name, scen in _fixture().items():
        assert _plans_now(scen["verdicts"]) == [
            p["canonical_json"] for p in scen["plans"]], name


def _at_base(path):
    blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{path}"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        pytest.skip("baseline commit not available")
    return blob.stdout


@pytest.mark.parametrize("path", [
    "trader/cognition/research_sources.py",
    "trader/cognition/research_plan.py",
    "trader/cognition/research_question.py",
    "trader/cognition/research_unreadable_question.py"])
def test_upstream_modules_are_byte_identical(path):
    assert (ROOT / path).read_text() == _at_base(path)


def test_source_registry_bytes_and_hash_unchanged(tmp_path):
    pinned = "17b68ba56d2e8d925d1448910527a7d7a7b9f5460601743fe88eb549e25e928e"
    before = rs.canonical_registry()
    assert rs.REGISTRY_SHA256 == pinned == _sha(before)
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    _questions(j)
    up.record_from_journal(j, now_ms=1)
    up.load(j)
    assert rs.canonical_registry() == before and _sha(before) == pinned


# ── 13 recording the sibling plan leaves decay tables untouched ─────────
def _tables(j, skip=("research_unreadable_plans",)):
    names = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f'SELECT * FROM "{t}"') for t in names
            if t not in skip}


@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, EF, D], [CF, EF, D]])
def test_recording_leaves_decay_chain_byte_identical(tmp_path, verdicts):
    j = Journal(tmp_path / "j.db")
    for r in _FullHist().seq("s1", verdicts).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    rp.record_from_journal(j, now_ms=1)
    uq.record_from_journal(j, now_ms=1)
    assert j.research_questions() and j.research_plans()
    before = _tables(j)
    plans_before = [rp.canonical(p) for p in rp.load(j)]
    res = up.record_from_journal(j, now_ms=2)
    assert len(res["inserted"]) == verdicts.count(CF) + verdicts.count(EF)
    assert not res["refusals"]
    assert _tables(j) == before
    for t in ("research_questions", "research_plans",
              "research_registrations", "research_unreadable_questions"):
        assert t in before
    assert [rp.canonical(p) for p in rp.load(j)] == plans_before
    assert rp.record_from_journal(j, now_ms=3)["inserted"] == []


# ── 14 no evidence / result / run / recall / registration / live wiring ─
def test_module_imports_only_the_contract_modules():
    src = (ROOT / "trader/cognition/research_unreadable_plan.py").read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imports.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
    assert imports == {"__future__:annotations", "hashlib", "json",
                       "trader.cognition:research_question",
                       "trader.cognition:research_sources",
                       "trader.cognition:research_unreadable_question",
                       "trader.strategy:health_observation"}
    for word in ("research_evidence", "research_result", "research_run",
                 "research_recall", "research_bank", "registration",
                 "attention", "kernel", "risk", "executor", "requests",
                 "urllib", "BrainLLM"):
        assert word not in src.split('"""', 2)[2], word


def test_nothing_imports_the_new_module():
    hits = []
    for p in (ROOT / "trader").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            names = ([node.module or ""] + [a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else
                     [a.name for a in node.names]
                     if isinstance(node, ast.Import) else [])
            if any("research_unreadable_plan" in n for n in names):
                hits.append(p.name)
    # only the offline sibling evidence collector, the offline unreadable
    # runner and the offline unreadable bank filer, whose own tests guard
    # their callers
    assert sorted(hits) == ["research_unreadable_bank.py",
                            "research_unreadable_evidence.py",
                            "research_unreadable_run.py"]


def test_plan_writes_nothing_but_its_own_table(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    _questions(j)
    before = _tables(j)
    [qrow] = j.research_unreadable_questions()
    _plan(j, qrow)                       # build is pure
    assert _tables(j, skip=()) == {**before, "research_unreadable_plans": []}
    up.record_from_journal(j, now_ms=1)
    assert _tables(j) == before
    assert len(j.research_unreadable_plans()) == 1
