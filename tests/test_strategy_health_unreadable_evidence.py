"""strategy-health-unreadable-evidence.v1: one context_only frozen evidence
record per fully re-verified strategy-health-unreadable-plan.v1, freezing
exactly the records the plan routes (source observation, source sweep,
same-spec observation history) by event id and content hash, persisted apart
from research-evidence.v1, whose tables, fixtures and the source registry
stay byte-identical.
"""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_research_evidence_source_binding import (FIXTURE, build_all,
                                                         fixture_text)
from tests.test_strategy_decay_research_plan import CF, D, EF, W, _FullHist
from tests.test_strategy_health_unreadable_question import (COMPLETE,
                                                            NO_FRAME,
                                                            _journal)
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_sources as rs
from trader.cognition import research_unreadable_evidence as ue
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]
BASE = "0512b6a"                         # HEAD this package was cut from


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _planned(j):
    """[(plan text, question JSON)] after recording questions and plans."""
    assert not uq.record_from_journal(j, now_ms=1)["refusals"]
    assert not up.record_from_journal(j, now_ms=1)["refusals"]
    qs = {r["question_id"]: r["canonical_json"]
          for r in j.research_unreadable_questions()}
    return [(p["canonical_json"], qs[p["question_id"]])
            for p in j.research_unreadable_plans()]


def _collect(j, ptext, qtext):
    return ue.collect(ptext, qtext, j.strategy_health_rows())


def _route(rec, rid):
    [r] = [r for r in rec["routes"] if r["route_id"] == rid]
    return r


def _spec_rows(j, spec="s1"):
    return [r for r in j.strategy_health_rows()
            if r["kind"] == ho.KIND_SPEC and r["subject"] == spec]


def _tamper_id(j, rid, fn):
    with j._tx() as c:
        [detail] = c.execute("SELECT detail FROM brain_events WHERE id=?",
                             (rid,)).fetchone()
        c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                  (fn(detail), rid))


def _stored(tmp_path, *sweeps):
    j = _journal(tmp_path, *(sweeps or ({"s1": COMPLETE}, {"s1": NO_FRAME})))
    _planned(j)
    res = ue.record_from_journal(j, now_ms=5)
    assert res["inserted"] and not res["refusals"]
    return j


def _tables(j, skip=("research_unreadable_evidence",)):
    names = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f'SELECT * FROM "{t}"') for t in names
            if t not in skip}


# ── 1-2 compile_failed / evaluation_failed give deterministic evidence ──
def test_compile_failed_plan_yields_deterministic_evidence(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [(ptext, qtext)] = _planned(j)
    rec = _collect(j, ptext, qtext)
    assert rec["schema"] == ue.SCHEMA == "strategy-health-unreadable-evidence.v1"
    assert rec["evidence_kind"] == uq.QUESTION_KIND
    assert rec["authority"] == "context_only"
    assert rec["scope"] == {"kind": "strategy", "spec_id": "s1"}
    assert ue.canonical(_collect(j, ptext, qtext)) == ue.canonical(rec)
    rows = list(reversed(j.strategy_health_rows()))
    assert ue.canonical(ue.collect(ptext, qtext, rows)) == ue.canonical(rec)
    obs = json.loads(_route(rec, up.SOURCE_OBSERVATION)["items"][0][
        "source_content"])
    assert obs["verdict"] == CF          # the source's fact, copied verbatim
    assert ue.from_json(ue.canonical(rec), ptext, qtext) == rec


@pytest.mark.parametrize("outcome", ["raise", NO_FRAME, {"branch": "idle"}])
def test_evaluation_failed_plan_yields_deterministic_evidence(tmp_path,
                                                              outcome):
    j = _journal(tmp_path, {"s1": outcome})
    [(ptext, qtext)] = _planned(j)
    a, b = _collect(j, ptext, qtext), _collect(j, ptext, qtext)
    assert ue.canonical(a) == ue.canonical(b)
    obs = json.loads(_route(a, up.SOURCE_OBSERVATION)["items"][0][
        "source_content"])
    assert obs["verdict"] == EF
    assert ue.from_json(ue.canonical(a), ptext, qtext) == a


def test_fixed_rows_give_fixed_evidence():
    h = _FullHist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": CF}, 20)
    [q] = uq.derive(h.rows).questions
    qtext = uq.canonical(q)
    ptext = up.canonical(up.build(qtext, h.rows))
    e1 = ue.collect(ptext, qtext, h.rows)
    e2 = ue.collect(ptext, qtext, list(reversed(h.rows)))
    assert ue.canonical(e1) == ue.canonical(e2)
    assert [[(i["event_id"], i["source_content"]) for i in r["items"]]
            for r in e1["routes"]] == [[(3, h.rows[2]["detail"])],
                                       [(4, h.rows[3]["detail"])],
                                       [(1, h.rows[0]["detail"])]]


# ── 3 only the three routed evidence classes, only routed records ───────
def test_only_the_three_routed_classes_are_frozen(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE, "s2": COMPLETE},
                 {"s2": COMPLETE}, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [(ptext, qtext)] = _planned(j)
    plan, rec = json.loads(ptext), _collect(j, ptext, qtext)
    assert [r["route_id"] for r in rec["routes"]] == list(up.ROUTE_IDS)
    health = rs.descriptor(rs.HEALTH_SOURCE_ID)
    for r in rec["routes"]:
        assert r["source_id"] == rs.HEALTH_SOURCE_ID
        assert {k: r[k] for k in health} == health
    frozen = [i["event_id"] for r in rec["routes"] for i in r["items"]]
    routed = [b["event_id"] for r in plan["routes"] for b in r["records"]]
    assert frozen == routed
    # earlier sweeps (membership-only in the plan) and the other spec's
    # rows stay unrouted and unfrozen
    sweeps = [r["id"] for r in j.strategy_health_rows()
              if r["kind"] == ho.KIND_SWEEP]
    assert len(sweeps) == 4
    assert [i for i in frozen if i in sweeps] == [sweeps[-1]]
    others = {r["id"] for r in _spec_rows(j, "s2")}
    assert others and not others & set(frozen)
    text = ue.canonical({k: v for k, v in rec.items() if k != "semantics"})
    for other in (rs.QUESTION_SOURCE_ID, rs.DECISIONS_SOURCE_ID,
                  rs.WORLDMODEL_REGIME_SOURCE_ID, "journal.decisions",
                  "http", "external"):
        assert other not in text, other


# ── 4 exact routed IDs / hashes / route semantics preserved ─────────────
def test_exact_routed_ids_hashes_and_route_fields(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": "compile"},
                 {"s2": COMPLETE}, {"s1": NO_FRAME})
    rows = {r["id"]: r for r in j.strategy_health_rows()}
    planned = _planned(j)
    assert len(planned) == 2
    for ptext, qtext in planned:
        plan, rec = json.loads(ptext), _collect(j, ptext, qtext)
        q = json.loads(qtext)
        assert rec["source_plan"] == {
            "schema": up.SCHEMA, "plan_id": plan["plan_id"],
            "plan_kind": up.PLAN_KIND, "planner_id": up.PLANNER_ID,
            "question_id": q["question_id"], "canonical_sha256": _sha(ptext)}
        for pr, er in zip(plan["routes"], rec["routes"]):
            assert {k: v for k, v in er.items() if k != "items"} == \
                {k: v for k, v in pr.items() if k != "records"}
            assert [{"event_id": i["event_id"],
                     "record_sha256": i["record_sha256"]}
                    for i in er["items"]] == pr["records"]
            for i in er["items"]:
                row = rows[i["event_id"]]
                assert i["source_content"] == row["detail"]
                assert _sha(i["source_content"]) == i["record_sha256"]
                assert (i["kind"], i["subject"]) == (row["kind"],
                                                     row["subject"])
        sw = _route(rec, up.SOURCE_SWEEP)["items"][0]
        assert sw["subject"] == q["source"]["sweep_id"]


# ── 5 EMPTY history stays EMPTY ─────────────────────────────────────────
def test_empty_history_stays_empty(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [(ptext, qtext)] = _planned(j)
    hist = _route(_collect(j, ptext, qtext), up.SPEC_HISTORY)
    assert hist["status"] == up.EMPTY and hist["items"] == []


# ── 6 no diagnosis / support / refute semantics ─────────────────────────
def test_record_carries_no_classification_or_conclusion(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME},
                 {"s1": "raise"})
    for ptext, qtext in _planned(j):
        rec = _collect(j, ptext, qtext)
        assert set(rec) == {"schema", "evidence_id", "evidence_kind",
                            "collector_id", "authority", "source_plan",
                            "scope", "routes", "semantics"}
        for r in rec["routes"]:
            assert set(r) == {"route_id", "source_id", "store", "reader",
                              "record_schema", "record_kind", "status",
                              "items"}
            assert r["status"] in (up.AVAILABLE, up.EMPTY)
            for i in r["items"]:
                assert set(i) == {"event_id", "record_sha256", "kind",
                                  "subject", "source_content"}
        # everything but the disclaiming semantics and verbatim source
        # content (the writer's own facts)
        text = ue.canonical({**{k: v for k, v in rec.items()
                                if k != "semantics"},
                             "routes": [{**r, "items": [
                                 {k: v for k, v in i.items()
                                  if k != "source_content"}
                                 for i in r["items"]]}
                                 for r in rec["routes"]]}).lower()
        for banned in ("support", "refut", "contradict", "inconclusive",
                       "conclusion", "diagnos", "data_failure", "cause",
                       "threshold", "predicate", "falsif", "regime",
                       "symbol", "btc", "salience", "priority", "rank",
                       "score", "usefulness", "budget", "cost", "suppress",
                       "cooldown", "repeat", "novel", "credib", "vendor"):
            assert banned not in text, banned


# ── 7-9 tampered source records fail closed ─────────────────────────────
def test_tampered_source_observation_fails_closed(tmp_path):
    j = _stored(tmp_path)
    [_, src] = _spec_rows(j)
    before = j.research_unreadable_evidence()
    _tamper_id(j, src["id"], lambda d: d.replace('"no_frame"', '"no_frame" '))
    with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
        ue.load(j)
    res = ue.record_from_journal(j, now_ms=9)
    assert res["inserted"] == [] and len(res["refusals"]) == 1
    assert j.research_unreadable_evidence() == before


def test_tampered_source_sweep_fails_closed(tmp_path):
    j = _stored(tmp_path)
    sweeps = [r for r in j.strategy_health_rows() if r["kind"] == ho.KIND_SWEEP]
    _tamper_id(j, sweeps[-1]["id"], lambda d: d.replace('"status"',
                                                        '"status" '))
    with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
        ue.load(j)
    [(ptext, qtext)] = [(p["canonical_json"], q["canonical_json"])
                        for p in j.research_unreadable_plans()
                        for q in j.research_unreadable_questions()]
    with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
        _collect(j, ptext, qtext)


def test_tampered_history_row_fails_closed(tmp_path):
    j = _stored(tmp_path)
    [hist, _] = _spec_rows(j)
    before = j.research_unreadable_evidence()
    _tamper_id(j, hist["id"], lambda d: d.replace('"spec_name"',
                                                  '"spec_name" '))
    with pytest.raises(ue.UnreadableEvidenceError,
                       match="^plan_invalid:plan_content$"):
        ue.load(j)
    res = ue.record_from_journal(j, now_ms=9)
    assert res["inserted"] == [] and res["duplicate"] == []
    assert [r for _, r in res["refusals"]] == ["plan_invalid:plan_content"]
    assert j.research_unreadable_evidence() == before


def test_missing_history_row_fails_closed(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [(ptext, qtext)] = _planned(j)
    [hist, _] = _spec_rows(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (hist["id"],))
    with pytest.raises(ue.UnreadableEvidenceError,
                       match="plan_invalid:plan_content|history_incomplete"):
        _collect(j, ptext, qtext)
    assert ue.record_from_journal(j, now_ms=1)["inserted"] == []
    assert j.research_unreadable_evidence() == []


def test_frozen_content_is_checked_from_the_record_alone(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [(ptext, qtext)] = _planned(j)
    rec = _collect(j, ptext, qtext)
    for rid in up.ROUTE_IDS:
        bad = json.loads(ue.canonical(rec))
        [it] = _route(bad, rid)["items"][:1]
        it["source_content"] = it["source_content"].replace(
            '"schema"', '"schema" ')
        with pytest.raises(ue.UnreadableEvidenceError,
                           match=f"^source_binding_mismatch:{rid}:"):
            ue.from_json(ue.canonical(bad), ptext, qtext)
    bad = json.loads(ue.canonical(rec))
    _route(bad, up.SOURCE_SWEEP)["items"][0]["subject"] = "other-sweep"
    with pytest.raises(ue.UnreadableEvidenceError, match="^item_contract:"):
        ue.from_json(ue.canonical(bad), ptext, qtext)


# ── 10 rerouted / modified plan fails closed ────────────────────────────
def _modified_plans(ptext):
    plan = json.loads(ptext)
    out = []
    swap = json.loads(ptext)
    swap["routes"][0], swap["routes"][1] = swap["routes"][1], \
        swap["routes"][0]
    out.append(swap)
    status = json.loads(ptext)
    status["routes"][2]["status"] = up.EMPTY
    out.append(status)
    extra = json.loads(ptext)
    extra["routes"][2]["records"].append(plan["routes"][1]["records"][0])
    out.append(extra)
    rehash = json.loads(ptext)
    rehash["routes"][0]["records"][0]["record_sha256"] = "0" * 64
    out.append(rehash)
    reid = json.loads(ptext)
    reid["routes"][0]["records"][0]["event_id"] = \
        plan["routes"][2]["records"][0]["event_id"]
    out.append(reid)
    store = json.loads(ptext)
    store["routes"][1]["store"] = "journal.decisions"
    out.append(store)
    dropped = json.loads(ptext)
    dropped["routes"].pop()
    out.append(dropped)
    return [up.canonical(p) for p in out]


def test_rerouted_or_modified_plan_fails_closed(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [(ptext, qtext)] = _planned(j)
    good = ue.canonical(_collect(j, ptext, qtext))
    for bad in _modified_plans(ptext):
        with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
            _collect(j, bad, qtext)
        with pytest.raises(ue.UnreadableEvidenceError):
            ue.from_json(good, bad, qtext)
    for bad in (None, "", "{", ptext + " "):
        with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
            _collect(j, bad, qtext)
    with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
        _collect(j, ptext, qtext + " ")


def test_tampered_stored_plan_row_is_refused(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    _planned(j)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_plans SET canonical_json="
                  "replace(canonical_json, '\"AVAILABLE\"', '\"EMPTY\"')")
    res = ue.record_from_journal(j, now_ms=1)
    assert res["inserted"] == [] and len(res["refusals"]) == 1
    assert res["refusals"][0][1].startswith("plan_invalid:")
    assert j.research_unreadable_evidence() == []


def test_tampered_stored_evidence_fails_closed(tmp_path):
    j = _stored(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_evidence SET canonical_json="
                  "replace(canonical_json, 'strategy_health_sweep', "
                  "'strategy_health_observed')")
    with pytest.raises(ue.UnreadableEvidenceError):
        ue.load(j)


# ── 11 deterministic ID / JSON / hash ───────────────────────────────────
def test_id_canonical_json_and_hash_are_deterministic(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [(ptext, qtext)] = _planned(j)
    rec = _collect(j, ptext, qtext)
    assert rec["evidence_id"] == _sha(ue.canonical({
        "schema": ue.SCHEMA, "evidence_kind": ue.EVIDENCE_KIND,
        "collector_id": ue.COLLECTOR_ID,
        "plan_id": json.loads(ptext)["plan_id"],
        "plan_canonical_sha256": _sha(ptext)}))
    row = ue.row_for(rec)
    assert row["canonical_json"] == ue.canonical(rec)
    assert row["canonical_sha256"] == _sha(row["canonical_json"])
    assert row["source_event_id"] == json.loads(qtext)["source"]["event_id"]
    with pytest.raises(ue.UnreadableEvidenceError, match="not_canonical"):
        ue.from_json(json.dumps(rec, sort_keys=True), ptext, qtext)
    bad = dict(rec, evidence_id="0" * 64)
    with pytest.raises(ue.UnreadableEvidenceError, match="^evidence_id$"):
        ue.from_json(ue.canonical(bad), ptext, qtext)


# ── 12-13 idempotence and conflicts ─────────────────────────────────────
def test_duplicate_write_is_idempotent(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    _planned(j)
    first = ue.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2 and not first["refusals"]
    again = ue.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(j.db_path)
    assert ue.record_from_journal(j2, now_ms=3)["duplicate"] == \
        first["inserted"]
    assert [e["evidence_id"] for e in ue.load(j2)] == first["inserted"]
    assert {r["recorded_at_ms"]
            for r in j2.research_unreadable_evidence()} == {1}


def test_conflict_never_overwrites(tmp_path):
    j = _stored(tmp_path)
    [row] = j.research_unreadable_evidence()
    base = ue.row_for(json.loads(row["canonical_json"]))
    for other in (dict(base, canonical_json=row["canonical_json"] + " ",
                       canonical_sha256="0" * 64),
                  dict(base, evidence_id="f" * 64),
                  dict(base, evidence_id="e" * 64, plan_id="d" * 64)):
        assert j.record_research_unreadable_evidence(
            other, recorded_at_ms=9) == "conflict"
    assert j.research_unreadable_evidence() == [row]


# ── 14 decay evidence collector still rejects unreadable plans ──────────
def test_decay_evidence_collector_rejects_unreadable_plan(tmp_path):
    j = _stored(tmp_path)
    [prow] = j.research_unreadable_plans()
    [erow] = j.research_unreadable_evidence()
    questions = {r["question_id"]: r
                 for r in j.research_unreadable_questions()}
    with pytest.raises(rp.ResearchPlanError):
        re_._verified_plan(prow, questions, j.strategy_health_rows())
    with pytest.raises(re_.ResearchEvidenceError, match="^keys$"):
        re_.from_json(erow["canonical_json"], prow["canonical_json"])
    assert re_.record_from_journal(j, now_ms=7) == {
        "inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    assert j.research_evidence() == []


# ── 15 unreadable collector rejects decay plans ─────────────────────────
def test_unreadable_collector_rejects_decay_plan(tmp_path):
    j = Journal(tmp_path / "d.db")
    for r in _FullHist().seq("s1", [W, CF, D]).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    rp.record_from_journal(j, now_ms=1)
    re_.record_from_journal(j, now_ms=1)
    rows = j.strategy_health_rows()
    [prow] = j.research_plans()
    [qrow] = j.research_questions()
    [erow] = j.research_evidence()
    with pytest.raises(ue.UnreadableEvidenceError, match="^plan_invalid:"):
        ue.collect(prow["canonical_json"], qrow["canonical_json"], rows)
    with pytest.raises(ue.UnreadableEvidenceError):
        ue.from_json(erow["canonical_json"], prow["canonical_json"],
                     qrow["canonical_json"])
    # the decay plan table is never read by the unreadable collector
    uq.record_from_journal(j, now_ms=2)
    up.record_from_journal(j, now_ms=2)
    res = ue.record_from_journal(j, now_ms=2)
    assert len(res["inserted"]) == 1 and not res["refusals"]
    [e] = ue.load(j)
    assert e["source_plan"]["schema"] == up.SCHEMA


# ── 16-17 decay fixtures, tables and registry byte-identical ────────────
def test_existing_decay_evidence_fixtures_are_byte_identical():
    assert fixture_text(build_all()) == FIXTURE.read_text()


@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, EF, D], [CF, EF, D]])
def test_recording_leaves_decay_chain_byte_identical(tmp_path, verdicts):
    j = Journal(tmp_path / "j.db")
    for r in _FullHist().seq("s1", verdicts).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    rp.record_from_journal(j, now_ms=1)
    re_.record_from_journal(j, now_ms=1)
    uq.record_from_journal(j, now_ms=1)
    up.record_from_journal(j, now_ms=1)
    assert j.research_questions() and j.research_plans() \
        and j.research_evidence()
    before = _tables(j)
    ev_before = [re_.canonical(e) for e in re_.load(j)]
    res = ue.record_from_journal(j, now_ms=2)
    assert len(res["inserted"]) == verdicts.count(CF) + verdicts.count(EF)
    assert not res["refusals"]
    assert _tables(j) == before
    for t in ("research_questions", "research_plans", "research_evidence",
              "research_registrations", "research_unreadable_questions",
              "research_unreadable_plans"):
        assert t in before
    assert [re_.canonical(e) for e in re_.load(j)] == ev_before
    assert re_.record_from_journal(j, now_ms=3)["inserted"] == []


def test_source_registry_bytes_and_hash_unchanged(tmp_path):
    pinned = "17b68ba56d2e8d925d1448910527a7d7a7b9f5460601743fe88eb549e25e928e"
    before = rs.canonical_registry()
    assert rs.REGISTRY_SHA256 == pinned == _sha(before)
    j = _stored(tmp_path)
    ue.load(j)
    assert rs.canonical_registry() == before and _sha(before) == pinned
    assert up.canonical_routing() and _sha(up.canonical_routing()) == \
        up.ROUTING_SHA256


def _at_base(path):
    blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{path}"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        pytest.skip("baseline commit not available")
    return blob.stdout


@pytest.mark.parametrize("path", [
    "trader/cognition/research_sources.py",
    "trader/cognition/research_question.py",
    "trader/cognition/research_plan.py",
    "trader/cognition/research_evidence.py",
    "trader/cognition/research_unreadable_question.py",
    "trader/cognition/research_unreadable_plan.py",
    "tests/fixtures/research_evidence_v1_pre_source_registry.json",
    "tests/fixtures/research_plan_v1_pre_source_registry.json"])
def test_upstream_modules_and_fixtures_are_byte_identical(path):
    assert (ROOT / path).read_text() == _at_base(path)


# ── 18 no result / run / recall / registration / live integration ───────
def test_module_imports_only_the_contract_modules():
    src = (ROOT / "trader/cognition/research_unreadable_evidence.py"
           ).read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imports.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
    assert imports == {"__future__:annotations", "hashlib", "json",
                       "trader.cognition:research_question",
                       "trader.cognition:research_unreadable_plan",
                       "trader.strategy:health_observation"}
    code = src.split('"""', 2)[2]
    for word in ("research_evidence", "research_result", "research_run",
                 "research_recall", "research_bank", "research_sources",
                 "registration", "attention", "kernel", "risk", "executor",
                 "orchestrator", "requests", "urllib", "socket", "BrainLLM"):
        assert word not in code, word


def test_nothing_imports_the_new_module():
    hits = []
    for p in (ROOT / "trader").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            names = ([node.module or ""] + [a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else
                     [a.name for a in node.names]
                     if isinstance(node, ast.Import) else [])
            if any("research_unreadable_evidence" in n for n in names):
                hits.append(p.name)
    # only the offline sibling structural result, whose own tests guard its
    # callers
    assert hits == ["research_unreadable_result.py"]


def test_collection_writes_nothing_but_its_own_table(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    [(ptext, qtext)] = _planned(j)
    before = _tables(j)
    _collect(j, ptext, qtext)            # collect is pure
    assert _tables(j, skip=()) == {**before,
                                   "research_unreadable_evidence": []}
    ue.record_from_journal(j, now_ms=1)
    assert _tables(j) == before
    assert len(j.research_unreadable_evidence()) == 1
    assert j.query("SELECT * FROM research_registrations") == []
    for t in ("research_results", "research_runs", "research_bank_objects",
              "research_next_questions"):
        assert j.query(f'SELECT * FROM "{t}"') == []
