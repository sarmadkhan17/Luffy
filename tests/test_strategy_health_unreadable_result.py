"""strategy-health-unreadable-result.v1: one context_only structural result
per fully re-verified strategy-health-unreadable-evidence.v1 record, always
INCONCLUSIVE / NOT_ASSESSED / no_registered_falsifier_predicates, persisted
apart from research-result.v1, whose tables, fixtures and the source
registry stay byte-identical.
"""
import ast
import hashlib
import json
import re
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
from trader.cognition import research_result as rr
from trader.cognition import research_sources as rs
from trader.cognition import research_unreadable_evidence as ue
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_result as ur
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]
BASE = "2cf53e0"                         # HEAD this package was cut from


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _evidenced(j):
    assert not uq.record_from_journal(j, now_ms=1)["refusals"]
    assert not up.record_from_journal(j, now_ms=1)["refusals"]
    assert not ue.record_from_journal(j, now_ms=1)["refusals"]
    return ue.load(j)


def _stored(tmp_path, *sweeps):
    j = _journal(tmp_path, *(sweeps or ({"s1": COMPLETE}, {"s1": NO_FRAME})))
    _evidenced(j)
    res = ur.record_from_journal(j, now_ms=5)
    assert res["inserted"] and not res["refusals"]
    return j


def _spec_rows(j, spec="s1"):
    return [r for r in j.strategy_health_rows()
            if r["kind"] == ho.KIND_SPEC and r["subject"] == spec]


def _tamper_id(j, rid, fn):
    with j._tx() as c:
        [detail] = c.execute("SELECT detail FROM brain_events WHERE id=?",
                             (rid,)).fetchone()
        c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                  (fn(detail), rid))


def _tables(j, skip=("research_unreadable_results",)):
    names = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f'SELECT * FROM "{t}"') for t in names
            if t not in skip}


def _decay_journal(tmp_path, verdicts=(W, CF, D), name="d.db"):
    j = Journal(tmp_path / name)
    for r in _FullHist().seq("s1", list(verdicts)).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    rp.record_from_journal(j, now_ms=1)
    re_.record_from_journal(j, now_ms=1)
    rr.record_from_journal(j, now_ms=1)
    return j


def _expect(ev):
    sp = ev["source_plan"]
    rec = {"schema": ur.SCHEMA, "result_kind": uq.QUESTION_KIND,
           "resolver_id": ur.RESOLVER_ID, "authority": "context_only",
           "source_evidence": {
               "schema": ue.SCHEMA, "evidence_id": ev["evidence_id"],
               "evidence_kind": ue.EVIDENCE_KIND,
               "collector_id": ue.COLLECTOR_ID, "plan_id": sp["plan_id"],
               "plan_sha256": sp["canonical_sha256"],
               "question_id": sp["question_id"],
               "canonical_sha256": _sha(ue.canonical(ev))},
           "scope": ev["scope"], "status": "INCONCLUSIVE",
           "assessment": "NOT_ASSESSED",
           "reason": "no_registered_falsifier_predicates",
           "semantics": ur.SEMANTICS}
    rec["result_id"] = _sha(ur.canonical({
        "schema": ur.SCHEMA, "result_kind": uq.QUESTION_KIND,
        "resolver_id": ur.RESOLVER_ID, "evidence_id": ev["evidence_id"],
        "evidence_canonical_sha256": _sha(ue.canonical(ev))}))
    return rec


# ── 1-2 compile_failed / evaluation_failed give deterministic results ───
def test_compile_failed_evidence_yields_deterministic_inconclusive(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"})
    [ev] = _evidenced(j)
    obs = json.loads(ev["routes"][0]["items"][0]["source_content"])
    assert obs["verdict"] == CF
    rec = ur.build(ev)
    assert rec == _expect(ev)
    assert ur.canonical(ur.build(ev)) == ur.canonical(rec)
    assert ur.from_json(ur.canonical(rec), ev) == rec
    assert ur.record_from_journal(j, now_ms=2)["inserted"] == [
        rec["result_id"]]
    assert ur.load(j) == [rec]


@pytest.mark.parametrize("outcome", ["raise", NO_FRAME, {"branch": "idle"}])
def test_evaluation_failed_evidence_yields_deterministic_inconclusive(
        tmp_path, outcome):
    j = _journal(tmp_path, {"s1": outcome})
    [ev] = _evidenced(j)
    obs = json.loads(ev["routes"][0]["items"][0]["source_content"])
    assert obs["verdict"] == EF
    a, b = ur.build(ev), ur.build(json.loads(ue.canonical(ev)))
    assert ur.canonical(a) == ur.canonical(b)
    assert a == _expect(ev)
    assert ur.from_json(ur.canonical(a), ev) == a


def test_fixed_rows_give_fixed_result():
    h = _FullHist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": CF}, 20)
    [q] = uq.derive(h.rows).questions
    qtext = uq.canonical(q)
    ptext = up.canonical(up.build(qtext, h.rows))
    e1 = ue.collect(ptext, qtext, h.rows)
    e2 = ue.collect(ptext, qtext, list(reversed(h.rows)))
    assert ur.canonical(ur.build(e1)) == ur.canonical(ur.build(e2))


# ── 3-4 only NOT_ASSESSED + reason; no diagnosis / support / refute ─────
def test_only_the_structural_outcome_is_emitted(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": "compile"},
                 {"s1": NO_FRAME}, {"s1": "raise"})
    evs = _evidenced(j)
    assert len(evs) == 3
    for ev in evs:
        rec = ur.build(ev)
        assert set(rec) == {"schema", "result_id", "result_kind",
                            "resolver_id", "authority", "source_evidence",
                            "scope", "status", "assessment", "reason",
                            "semantics"}
        assert (rec["status"], rec["assessment"], rec["reason"]) == (
            "INCONCLUSIVE", "NOT_ASSESSED",
            "no_registered_falsifier_predicates")
    assert ur.STATUSES == ("INCONCLUSIVE",)
    assert ur.ASSESSMENTS == ("NOT_ASSESSED",)
    assert ur.REASONS == ("no_registered_falsifier_predicates",)


def test_module_defines_no_support_refute_or_diagnosis_outcome(tmp_path):
    code = Path(ur.__file__).read_text().split('"""', 2)[2]
    consts = {n.value for n in ast.walk(ast.parse(code))
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    consts.discard(ur.SEMANTICS)
    text = " ".join(consts).lower()
    for banned in ("supported", "refuted", "supports", "refutes",
                   "diagnos", "data_failure", "capability_failure", "cause",
                   "threshold", "regime", "symbol", "salience", "priority",
                   "rank", "score", "usefulness", "budget", "cost",
                   "suppress", "cooldown", "repeat", "label", "vendor"):
        assert banned not in text, banned
    j = _journal(tmp_path, {"s1": "compile"})
    [ev] = _evidenced(j)
    rec = ur.build(ev)
    body = ur.canonical({k: v for k, v in rec.items()
                         if k != "semantics"}).lower()
    for banned in ("support", "refut", "diagnos", "data_failure", "cause",
                   "compile_failed", "evaluation_failed", "verdict",
                   "threshold", "regime", "btc", "score", "rank", "label"):
        assert banned not in body, banned


@pytest.mark.parametrize("field,value", [
    ("status", "SUPPORTED"), ("status", "REFUTED"),
    ("assessment", "ASSESSED"), ("assessment", "SUPPORTED"),
    ("reason", "data_failure"), ("reason", "compile_failed")])
def test_other_outcomes_are_rejected_even_when_rehashed(tmp_path, field,
                                                        value):
    j = _journal(tmp_path, {"s1": "compile"})
    [ev] = _evidenced(j)
    bad = dict(ur.build(ev), **{field: value})
    bad["result_id"] = ur.result_id(bad)
    with pytest.raises(ur.UnreadableResultError, match="^status$"):
        ur.from_json(ur.canonical(bad), ev)


@pytest.mark.parametrize("key", ["diagnosis", "supports", "score",
                                 "hypotheses", "limitations", "label"])
def test_added_fields_are_rejected(tmp_path, key):
    j = _journal(tmp_path, {"s1": "compile"})
    [ev] = _evidenced(j)
    bad = dict(ur.build(ev), **{key: None})
    with pytest.raises(ur.UnreadableResultError, match="^keys$"):
        ur.from_json(ur.canonical(bad), ev)
    bad = ur.build(ev)
    bad["source_evidence"][key] = None
    with pytest.raises(ur.UnreadableResultError,
                       match="^source_evidence_keys$"):
        ur.from_json(ur.canonical(bad), ev)


# ── 5 tampered evidence fails closed ────────────────────────────────────
def test_tampered_stored_evidence_fails_closed(tmp_path):
    j = _stored(tmp_path)
    before = j.research_unreadable_results()
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_evidence SET canonical_json="
                  "replace(canonical_json, 'strategy_health_sweep', "
                  "'strategy_health_observed')")
    with pytest.raises(ur.UnreadableResultError, match="^source_evidence:"):
        ur.load(j)
    res = ur.record_from_journal(j, now_ms=9)
    assert res["inserted"] == res["duplicate"] == [] and res["refusals"]
    assert j.research_unreadable_results() == before


def test_rebuilt_evidence_substitution_is_rejected(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    a, b = _evidenced(j)
    text = ur.canonical(ur.build(a))
    with pytest.raises(ur.UnreadableResultError,
                       match="^source_evidence_mismatch$"):
        ur.from_json(text, b)
    altered = json.loads(ue.canonical(a))
    altered["scope"]["spec_id"] = "s2"
    with pytest.raises(ur.UnreadableResultError,
                       match="^source_evidence_mismatch$"):
        ur.from_json(text, altered)


def test_tampered_stored_result_fails_closed(tmp_path):
    j = _stored(tmp_path)
    [row] = j.research_unreadable_results()
    for edit in (
            lambda t: t.replace('"NOT_ASSESSED"', '"ASSESSED"'),
            lambda t: t.replace('"INCONCLUSIVE"', '"REFUTED"'),
            lambda t: t.replace('"spec_id":"s1"', '"spec_id":"s2"'),
            lambda t: t + " "):
        text = edit(row["canonical_json"])
        assert text != row["canonical_json"]
        with j._tx() as c:
            c.execute("UPDATE research_unreadable_results SET "
                      "canonical_json=?, canonical_sha256=?",
                      (text, _sha(text)))
        with pytest.raises(ur.UnreadableResultError):
            ur.load(j)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_results SET canonical_json=?, "
                  "canonical_sha256=?, status='SUPPORTED'",
                  (row["canonical_json"], row["canonical_sha256"]))
    with pytest.raises(ur.UnreadableResultError, match="^row_projection$"):
        ur.load(j)


# ── 6 deleted / changed live provenance follows the evidence contract ───
def test_changed_live_source_fails_closed(tmp_path):
    j = _stored(tmp_path)
    [_, src] = _spec_rows(j)
    before = j.research_unreadable_results()
    _tamper_id(j, src["id"], lambda d: d.replace('"no_frame"', '"no_frame" '))
    with pytest.raises(ue.UnreadableEvidenceError):
        ue.load(j)
    with pytest.raises(ur.UnreadableResultError,
                       match="^source_evidence:plan_invalid:"):
        ur.load(j)
    assert ur.record_from_journal(j, now_ms=9)["inserted"] == []
    assert j.research_unreadable_results() == before


def test_deleted_live_sources_fail_closed(tmp_path):
    for target in ("history", "plan", "question", "evidence"):
        j = _stored(tmp_path / target)
        with j._tx() as c:
            if target == "history":
                [hist, _] = _spec_rows(j)
                c.execute("DELETE FROM brain_events WHERE id=?",
                          (hist["id"],))
            elif target == "plan":
                c.execute("DELETE FROM research_unreadable_plans")
            elif target == "question":
                c.execute("DELETE FROM research_unreadable_questions")
            else:
                c.execute("DELETE FROM research_unreadable_evidence")
        with pytest.raises(ur.UnreadableResultError, match="^source_evidence"):
            ur.load(j)


# ── 7 deterministic ID / JSON / hash ────────────────────────────────────
def test_id_canonical_json_and_hash_are_deterministic(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE}, {"s1": NO_FRAME})
    [ev] = _evidenced(j)
    rec = ur.build(ev)
    assert rec["result_id"] == _expect(ev)["result_id"]
    row = ur.row_for(rec)
    assert row["canonical_json"] == ur.canonical(rec)
    assert row["canonical_sha256"] == _sha(row["canonical_json"])
    [erow] = j.research_unreadable_evidence()
    assert row["evidence_sha256"] == erow["canonical_sha256"]
    assert rec["source_evidence"]["plan_sha256"] == erow["plan_sha256"]
    with pytest.raises(ur.UnreadableResultError, match="not_canonical"):
        ur.from_json(json.dumps(rec, sort_keys=True), ev)
    with pytest.raises(ur.UnreadableResultError, match="^result_id$"):
        ur.from_json(ur.canonical(dict(rec, result_id="0" * 64)), ev)
    with pytest.raises(ur.UnreadableResultError, match="^undecodable$"):
        ur.from_json("{", ev)


# ── 8-9 idempotence and conflicts ───────────────────────────────────────
def test_duplicate_write_is_idempotent(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    _evidenced(j)
    first = ur.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2 and not first["refusals"]
    again = ur.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and \
        sorted(again["duplicate"]) == sorted(first["inserted"])
    j2 = Journal(j.db_path)
    assert sorted(ur.record_from_journal(j2, now_ms=3)["duplicate"]) == \
        sorted(first["inserted"])
    assert sorted(r["result_id"] for r in ur.load(j2)) == \
        sorted(first["inserted"])
    assert {r["recorded_at_ms"]
            for r in j2.research_unreadable_results()} == {1}


def test_conflict_never_overwrites(tmp_path):
    j = _stored(tmp_path)
    [row] = j.research_unreadable_results()
    base = ur.row_for(json.loads(row["canonical_json"]))
    for other in (dict(base, canonical_json=row["canonical_json"] + " ",
                       canonical_sha256="0" * 64),
                  dict(base, status="SUPPORTED"),
                  dict(base, result_id="f" * 64)):
        assert j.record_research_unreadable_result(
            other, recorded_at_ms=9) == "conflict"
    assert j.research_unreadable_results() == [row]
    ur.load(j)


# ── 10-11 the two result families refuse each other's evidence ──────────
def test_decay_result_rejects_unreadable_evidence(tmp_path):
    j = _stored(tmp_path)
    [ev] = ue.load(j)
    [urow] = j.research_unreadable_results()
    with pytest.raises(rr.ResearchResultError, match="^evidence_contract$"):
        rr.build(ev)
    with pytest.raises(rr.ResearchResultError, match="^keys$"):
        rr.from_json(urow["canonical_json"], ev)
    assert rr.record_from_journal(j, 7) == {
        "inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    assert j.research_results() == []


def test_unreadable_result_rejects_decay_evidence(tmp_path):
    j = _decay_journal(tmp_path)
    [ev] = re_.load(j)
    [drow] = j.research_results()
    with pytest.raises(ur.UnreadableResultError, match="^evidence_contract$"):
        ur.build(ev)
    with pytest.raises(ur.UnreadableResultError, match="^keys$"):
        ur.from_json(drow["canonical_json"], ev)
    with pytest.raises(ur.UnreadableResultError, match="^evidence_contract$"):
        ur.build(json.loads(ur.canonical(rr.build(ev))))
    for bad in (None, {}, {"schema": ue.SCHEMA}):
        with pytest.raises(ur.UnreadableResultError):
            ur.build(bad)
    # research_evidence rows are never read by the unreadable resolver
    assert ur.record_from_journal(j, now_ms=2) == {
        "inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    assert j.research_unreadable_results() == []


# ── 12-14 decay fixtures, tables and registry byte-identical ────────────
def test_existing_decay_evidence_fixtures_are_byte_identical():
    assert fixture_text(build_all()) == FIXTURE.read_text()


@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, EF, D], [CF, EF, D]])
def test_recording_leaves_decay_chain_byte_identical(tmp_path, verdicts):
    j = _decay_journal(tmp_path, verdicts, name="j.db")
    uq.record_from_journal(j, now_ms=1)
    up.record_from_journal(j, now_ms=1)
    ue.record_from_journal(j, now_ms=1)
    assert j.research_questions() and j.research_plans() \
        and j.research_evidence() and j.research_results()
    before = _tables(j)
    res_before = [rr.canonical(r) for r in rr.load(j)]
    ids_before = [(r["result_id"], r["canonical_sha256"])
                  for r in j.research_results()]
    res = ur.record_from_journal(j, now_ms=2)
    assert len(res["inserted"]) == verdicts.count(CF) + verdicts.count(EF)
    assert not res["refusals"]
    ur.load(j)
    assert _tables(j) == before
    for t in ("research_questions", "research_plans", "research_evidence",
              "research_results", "research_registrations",
              "research_unreadable_questions", "research_unreadable_plans",
              "research_unreadable_evidence"):
        assert t in before
    assert [rr.canonical(r) for r in rr.load(j)] == res_before
    assert [(r["result_id"], r["canonical_sha256"])
            for r in j.research_results()] == ids_before
    assert rr.record_from_journal(j, 3)["inserted"] == []


def test_source_registry_bytes_and_hash_unchanged(tmp_path):
    pinned = "17b68ba56d2e8d925d1448910527a7d7a7b9f5460601743fe88eb549e25e928e"
    before = rs.canonical_registry()
    assert rs.REGISTRY_SHA256 == pinned == _sha(before)
    j = _stored(tmp_path)
    ur.load(j)
    assert rs.canonical_registry() == before and _sha(before) == pinned


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
    "trader/cognition/research_result.py",
    "trader/cognition/research_run.py",
    "trader/cognition/research_bank.py",
    "trader/cognition/research_recall.py",
    "trader/cognition/research_unreadable_question.py",
    "trader/cognition/research_unreadable_plan.py",
    "trader/cognition/research_unreadable_evidence.py",
    "tests/fixtures/research_evidence_v1_pre_source_registry.json",
    "tests/fixtures/research_plan_v1_pre_source_registry.json"])
def test_upstream_modules_and_fixtures_are_byte_identical(path):
    assert (ROOT / path).read_text() == _at_base(path)


def test_recording_writes_nothing_but_its_own_table(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    [ev] = _evidenced(j)
    before = _tables(j)
    ur.build(ev)                         # build is pure
    assert _tables(j, skip=()) == {**before,
                                   "research_unreadable_results": []}
    ur.record_from_journal(j, now_ms=1)
    ur.load(j)
    assert _tables(j) == before
    assert len(j.research_unreadable_results()) == 1
    assert j.query("SELECT * FROM research_registrations") == []
    for t in ("research_results", "research_runs", "research_bank_objects",
              "research_next_questions"):
        assert j.query(f'SELECT * FROM "{t}"') == []


# ── 15 no run / Bank / recall / registration / live integration ─────────
def test_module_imports_only_the_contract_module():
    src = Path(ur.__file__).read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imports.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
    assert imports == {"__future__:annotations", "hashlib", "json",
                       "trader.cognition:research_unreadable_evidence"}
    code = src.split('"""', 2)[2]
    for word in ("research_result", "research_evidence ", "research_run",
                 "research_recall", "research_bank", "research_sources",
                 "registration", "attention", "kernel", "risk", "executor",
                 "orchestrator", "requests", "urllib", "socket", "BrainLLM"):
        assert word not in code, word


def test_nothing_calls_the_new_module():
    root = ROOT / "trader"
    pat = re.compile(r"research_unreadable_result|"
                     r"record_research_unreadable_result")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_unreadable_result.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive (and names the module only in
    # its schema comment); nothing imports the contract
    assert callers == ["core/journal.py"]
    assert "research_unreadable_result import" not in \
        (root / "core/journal.py").read_text()


def test_legacy_journal_without_result_table_gets_it_additively(tmp_path):
    j = _stored(tmp_path)
    with j._tx() as c:
        c.execute("DROP TABLE research_unreadable_results")
    j2 = Journal(j.db_path)
    assert j2.research_unreadable_results() == []
    res = ur.record_from_journal(j2, now_ms=6)
    assert len(res["inserted"]) == 1
