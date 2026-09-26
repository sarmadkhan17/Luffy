"""research-next-question.v1: context_only records of the structural gaps a
verified Research Bank object's result names.

Each record maps one result hypothesis entry one-for-one from a registered
(status, reason) pair to a question kind that states the gap only. It binds
the exact bank object and result IDs and canonical hashes, fails closed on
anything unregistered or unverifiable, never modifies the bank or result,
and nothing live, Attention, Risk or Execution calls it.
"""
import ast
import copy
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_bank import (
    CHAIN_TABLES, D, W, _filed, _ran)
from tests.test_strategy_decay_research_run import _copy, _FORBIDDEN, _all_keys
from trader.cognition import research_bank as rb
from trader.cognition import research_next_question as nq
from trader.cognition import research_plan as rp
from trader.cognition import research_result as rr
from trader.core.journal import Journal

FALSIFIER = nq.REGISTERED_FALSIFIER_PREDICATES_MISSING
WORLDMODEL = nq.TRUTHFUL_WORLDMODEL_SOURCE_MISSING


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _bank_id(j):
    [r] = j.research_bank_objects()
    return r["bank_object_id"]


def _recorded(tmp_path, **kw):
    j, _ = _filed(tmp_path, **kw)
    bid = _bank_id(j)
    res = nq.record_for_bank_object(j, bid, now_ms=3)
    assert res["refusals"] == [] and len(res["inserted"]) == 4
    return j, bid, res


def _verified(j, bid):
    return nq._verified_bank(j, bid)


def _rebound(bank, result):
    """Re-bind a (mutated) result into its bank link, so derive() reaches
    the mapping instead of the link check."""
    bank = copy.deepcopy(bank)
    bank["links"]["result"]["canonical_sha256"] = _sha(rb.canonical(result))
    return bank


# ── 1/2 exact, deterministic one-for-one mappings ──────────────────────
def test_registered_mappings_are_exactly_the_two_structural_reasons():
    assert nq.MAPPINGS == {
        ("NOT_ASSESSED", "no_registered_falsifier_predicates"):
            "REGISTERED_FALSIFIER_PREDICATES_MISSING",
        ("UNAVAILABLE", "no_truthful_worldmodel_source"):
            "TRUTHFUL_WORLDMODEL_SOURCE_MISSING"}
    assert nq.QUESTION_KINDS == (FALSIFIER, WORLDMODEL)
    assert rr.NO_REGISTERED_FALSIFIER_PREDICATES == \
        "no_registered_falsifier_predicates"
    assert rp.NO_TRUTHFUL_WORLDMODEL_SOURCE == "no_truthful_worldmodel_source"


def test_one_record_per_result_hypothesis_entry(tmp_path):
    j, bid, res = _recorded(tmp_path)
    bank, result = _verified(j, bid)
    recs = nq.load(j)
    assert [r["next_question_id"] for r in recs] == res["inserted"]
    assert len(recs) == len(result["hypotheses"])
    for i, (rec, h) in enumerate(zip(recs, result["hypotheses"])):
        src = rec["source"]
        assert src["entry"] == h and src["index"] == i
        assert src["field"] == "hypotheses"
        assert rec["question_kind"] == nq.MAPPINGS[(h["status"], h["reason"])]
        assert rec["authority"] == "context_only"
        assert rec["scope"] == result["scope"] == bank["scope"]


def test_falsifier_reason_maps_exactly(tmp_path):
    j, _, _ = _recorded(tmp_path)
    recs = [r for r in nq.load(j) if r["question_kind"] == FALSIFIER]
    assert [r["source"]["entry"]["hypothesis"] for r in recs] == [
        rp.GENUINE_DETERIORATION, rp.INSUFFICIENT_RECENT_OPPORTUNITIES,
        rp.DATA_FAILURE]
    for r in recs:
        assert (r["source"]["entry"]["status"],
                r["source"]["entry"]["reason"]) == (
            rr.NOT_ASSESSED, rr.NO_REGISTERED_FALSIFIER_PREDICATES)
        assert r["statement"] == (
            "No registered falsifier predicate exists for hypothesis "
            f"{r['source']['entry']['hypothesis']}; this records the gap "
            "only.")


def test_worldmodel_unavailable_maps_exactly(tmp_path):
    j, bid, _ = _recorded(tmp_path)
    _, result = _verified(j, bid)
    [r] = [r for r in nq.load(j) if r["question_kind"] == WORLDMODEL]
    entry = r["source"]["entry"]
    assert entry == {"hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
                     "evidence_status": rp.UNAVAILABLE,
                     "status": rr.UNAVAILABLE,
                     "reason": rp.NO_TRUTHFUL_WORLDMODEL_SOURCE}
    assert {"hypothesis": entry["hypothesis"], "reason": entry["reason"]} \
        in result["unavailable_hypotheses"]
    assert r["statement"] == (
        "No truthful WorldModel/regime evidence source is available for "
        "hypothesis temporary_regime_absence; this records the gap only.")


# ── 3 nothing proposed ──────────────────────────────────────────────────
_PROPOSAL = re.compile(
    r"propos|recommend|should|must|consider|suggest|use |adopt|vendor"
    r"|provider|threshold|[<>=]|\d|rule|plan|method|implement|enqueue"
    r"|next step", re.I)


def test_records_propose_no_predicate_threshold_source_or_method(tmp_path):
    j, _, _ = _recorded(tmp_path)
    for rec in nq.load(j):
        assert not _PROPOSAL.search(rec["statement"])
        assert rec["statement"].endswith("this records the gap only.")
        keys = set(_all_keys(rec))
        assert not {k for k in keys if re.search(
            r"predicate|threshold|regime|vendor|provider|url|method|plan"
            r"|recommend|proposal|action|rule", k)}
        # the only values are the fixed contract, the verbatim source
        # entry, the bound IDs/hashes and the scope
        assert set(rec) == set(nq._RECORD_KEYS)


# ── 4 unregistered reasons fail closed ──────────────────────────────────
@pytest.mark.parametrize("status,reason", [
    ("NOT_ASSESSED", "something_else"),
    ("UNAVAILABLE", "no_registered_falsifier_predicates"),
    ("NOT_ASSESSED", "no_truthful_worldmodel_source"),
    ("SUPPORTED", "no_registered_falsifier_predicates"),
    ("UNAVAILABLE", "no_worldmodel"),
])
def test_unregistered_reason_derives_nothing(tmp_path, status, reason):
    j, _ = _filed(tmp_path)
    bank, result = _verified(j, _bank_id(j))
    result = copy.deepcopy(result)
    result["hypotheses"][1]["status"] = status
    result["hypotheses"][1]["reason"] = reason
    with pytest.raises(nq.ResearchNextQuestionError,
                       match=re.escape(f"unregistered_reason:{status}:"
                                       f"{reason}")):
        nq.derive(_rebound(bank, result), result)


def test_unavailable_entry_missing_from_unavailable_list_fails(tmp_path):
    j, _ = _filed(tmp_path)
    bank, result = _verified(j, _bank_id(j))
    result = copy.deepcopy(result)
    result["unavailable_hypotheses"] = []
    with pytest.raises(nq.ResearchNextQuestionError,
                       match="source_mismatch:unavailable_hypotheses"):
        nq.derive(_rebound(bank, result), result)


def test_unregistered_reason_through_writer_writes_nothing(
        tmp_path, monkeypatch):
    j, _ = _filed(tmp_path)
    bid = _bank_id(j)
    bank, result = _verified(j, bid)
    bad = copy.deepcopy(result)
    bad["hypotheses"][-1]["reason"] = "new_unregistered_reason"
    monkeypatch.setattr(nq, "_verified_bank",
                        lambda _j, _b: (_rebound(bank, bad), bad))
    res = nq.record_for_bank_object(j, bid, now_ms=3)
    assert res["inserted"] == res["duplicate"] == res["conflict"] == []
    assert res["refusals"] == [(
        bid, "unregistered_reason:UNAVAILABLE:new_unregistered_reason")]
    assert j.research_next_questions() == []


# ── 5 source tampering fails closed ─────────────────────────────────────
def test_missing_bank_object_is_refused(tmp_path):
    j, _ = _filed(tmp_path)
    for bid in ("0" * 64, "", None):
        res = nq.record_for_bank_object(j, bid, now_ms=3)
        assert res["refusals"] == [(bid, "source_missing:bank_object")]
    assert j.research_next_questions() == []


def test_tampered_bank_object_is_refused(tmp_path):
    j, _ = _filed(tmp_path)
    bid = _bank_id(j)
    with j._tx() as c:
        c.execute("UPDATE research_bank_objects SET canonical_json="
                  "replace(canonical_json, 'INCONCLUSIVE', 'SUPPORTED')")
    [(rid, why)] = nq.record_for_bank_object(j, bid, now_ms=3)["refusals"]
    assert rid == bid and why.startswith("source_invalid:bank:")
    assert j.research_next_questions() == []


@pytest.mark.parametrize("table", CHAIN_TABLES[:4])
def test_tampered_chain_record_is_refused(tmp_path, table):
    j, _ = _filed(tmp_path)
    bid = _bank_id(j)
    with j._tx() as c:
        c.execute(f"UPDATE {table} SET canonical_json="
                  "replace(canonical_json, 's1', 's9')")
    [(_, why)] = nq.record_for_bank_object(j, bid, now_ms=3)["refusals"]
    assert why.startswith("source_invalid:bank:")
    assert j.research_next_questions() == []


@pytest.mark.parametrize("table", CHAIN_TABLES)
def test_deleted_chain_record_is_refused(tmp_path, table):
    j, _ = _filed(tmp_path)
    bid = _bank_id(j)
    with j._tx() as c:
        c.execute(f"DELETE FROM {table}")
    [(_, why)] = nq.record_for_bank_object(j, bid, now_ms=3)["refusals"]
    assert why.startswith("source_invalid:bank:")
    assert j.research_next_questions() == []


def test_source_tampered_after_recording_fails_load(tmp_path):
    j, _, _ = _recorded(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_results SET canonical_json="
                  "replace(canonical_json, 's1', 's9')")
    with pytest.raises(nq.ResearchNextQuestionError,
                       match="source_invalid:bank:"):
        nq.load(j)


def test_derive_refuses_a_result_the_bank_does_not_bind(tmp_path):
    j, _ = _filed(tmp_path)
    bank, result = _verified(j, _bank_id(j))
    other = copy.deepcopy(result)
    other["limitations"] = other["limitations"] + [{"x": 1}]
    with pytest.raises(nq.ResearchNextQuestionError,
                       match="source_mismatch:bank->result"):
        nq.derive(bank, other)
    scoped = copy.deepcopy(result)
    scoped["scope"] = {**scoped["scope"], "spec_id": "other"}
    with pytest.raises(nq.ResearchNextQuestionError,
                       match="source_mismatch"):
        nq.derive(_rebound(bank, scoped), scoped)


# ── 6 deterministic serialization, hash and ID ──────────────────────────
def test_serialization_hash_and_id_are_deterministic(tmp_path):
    j, bid, res = _recorded(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    with ref._tx() as c:
        c.execute("DELETE FROM research_next_questions")
    again = nq.record_for_bank_object(ref, bid, now_ms=99)
    assert again["inserted"] == res["inserted"]
    a, b = j.research_next_questions(), ref.research_next_questions()
    assert [r["canonical_json"] for r in a] == \
        [r["canonical_json"] for r in b]
    assert [r["recorded_at_ms"] for r in a] == [3] * 4
    assert [r["recorded_at_ms"] for r in b] == [99] * 4
    bank, result = _verified(j, bid)
    assert [nq.canonical(x) for x in nq.derive(bank, result)] == \
        [r["canonical_json"] for r in a]
    for r in a:
        rec = json.loads(r["canonical_json"])
        assert r["canonical_sha256"] == _sha(r["canonical_json"])
        assert rec["next_question_id"] == _sha(nq.canonical({
            "schema": nq.SCHEMA, "generator_id": nq.GENERATOR_ID,
            "question_kind": rec["question_kind"],
            "hypothesis": rec["source"]["entry"]["hypothesis"],
            "bank_object_id": bid,
            "bank_canonical_sha256": _sha(nq.canonical(bank)),
            "result_id": result["result_id"],
            "result_canonical_sha256": _sha(nq.canonical(result))}))
    assert len({r["next_question_id"] for r in a}) == 4


def test_binding_is_the_exact_bank_and_result_ids_and_hashes(tmp_path):
    j, bid, _ = _recorded(tmp_path)
    [brow] = j.research_bank_objects()
    [rrow] = j.research_results()
    for rec in nq.load(j):
        assert rec["source"]["bank_object"] == {
            "schema": rb.SCHEMA, "bank_object_id": bid,
            "canonical_sha256": brow["canonical_sha256"]}
        assert rec["source"]["result"] == {
            "schema": rr.SCHEMA, "result_id": rrow["result_id"],
            "canonical_sha256": rrow["canonical_sha256"]}


def test_each_bank_object_gets_its_own_records(tmp_path):
    j = _ran(tmp_path, verdicts=(W, D, D, W, D))
    rb.record_from_journal(j, now_ms=2)
    bids = [r["bank_object_id"] for r in j.research_bank_objects()]
    assert len(bids) == 2
    for bid in bids:
        assert len(nq.record_for_bank_object(j, bid, 3)["inserted"]) == 4
    assert [len(nq.load(j, bank_object_id=b)) for b in bids] == [4, 4]
    assert len({r["next_question_id"] for r in nq.load(j)}) == 8


# ── 7 idempotent duplicate ──────────────────────────────────────────────
def test_retry_is_idempotent(tmp_path):
    j, bid, res = _recorded(tmp_path)
    before = j.research_next_questions()
    again = nq.record_for_bank_object(j, bid, now_ms=50)
    assert again == {"inserted": [], "duplicate": res["inserted"],
                     "conflict": [], "refusals": []}
    assert j.research_next_questions() == before


# ── 8 conflicting content fails closed ──────────────────────────────────
def test_conflicting_same_id_is_reported_never_overwritten(tmp_path):
    j, bid, res = _recorded(tmp_path)
    [first, *_] = j.research_next_questions()
    before = j.research_next_questions()
    row = {k: first[k] for k in j._NEXT_QUESTION_COLUMNS}
    forged = {**row, "canonical_json": row["canonical_json"] + " "}
    assert j.record_research_next_question(forged, recorded_at_ms=9) == \
        "conflict"
    other_id = {**row, "next_question_id": "f" * 64}
    assert j.record_research_next_question(other_id, recorded_at_ms=9) == \
        "conflict"
    assert j.research_next_questions() == before


def test_stored_conflict_is_reported_by_the_writer(tmp_path):
    j, bid, res = _recorded(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_next_questions SET canonical_sha256='x' "
                  "WHERE rowid=1")
    again = nq.record_for_bank_object(j, bid, now_ms=50)
    assert again["conflict"] == res["inserted"][:1]
    assert again["duplicate"] == res["inserted"][1:]


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(statement=r["statement"] + " Use X."), "statement"),
    (lambda r: r.update(question_kind=WORLDMODEL), "mapping"),
    (lambda r: r.update(authority="authoritative"), "contract"),
    (lambda r: r.update(priority=1), "keys"),
    (lambda r: r["source"]["entry"].update(reason="other"), "mapping"),
    (lambda r: r["source"].update(index=3), "next_question_id|rederive"),
    (lambda r: r["source"]["bank_object"].update(canonical_sha256="0" * 64),
     "rederive_mismatch"),
])
def test_forged_stored_record_fails_load(tmp_path, mutate, code):
    j, _, _ = _recorded(tmp_path)
    [first, *_] = j.research_next_questions()
    rec = json.loads(first["canonical_json"])
    mutate(rec)
    if set(rec) == set(nq._RECORD_KEYS):
        rec["next_question_id"] = nq.next_question_id(rec)
    text = nq.canonical(rec)
    with j._tx() as c:
        c.execute("UPDATE research_next_questions SET canonical_json=?, "
                  "canonical_sha256=? WHERE rowid=1", (text, _sha(text)))
    with pytest.raises(nq.ResearchNextQuestionError, match=code):
        nq.load(j)


def test_non_canonical_and_projection_are_verified(tmp_path):
    j, _, _ = _recorded(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_next_questions SET canonical_json="
                  "canonical_json || ' ' WHERE rowid=1")
    with pytest.raises(nq.ResearchNextQuestionError, match="not_canonical"):
        nq.load(j)
    j2, _, _ = _recorded(tmp_path / "b")
    with j2._tx() as c:
        c.execute("UPDATE research_next_questions SET hypothesis='x' "
                  "WHERE rowid=1")
    with pytest.raises(nq.ResearchNextQuestionError, match="row_projection"):
        nq.load(j2)


def test_duplicate_json_key_fails_closed(tmp_path):
    j, _, _ = _recorded(tmp_path)
    text = j.research_next_questions()[0]["canonical_json"]
    with pytest.raises(nq.ResearchNextQuestionError,
                       match="duplicate_json_key"):
        nq.from_json(text[:-1] + ',"schema":"x"}')


# ── 9 existing bank/result content and IDs unchanged ────────────────────
def test_bank_and_result_records_are_unchanged(tmp_path):
    j, _ = _filed(tmp_path)
    tables = ("research_bank_objects", "research_registrations",
              "brain_events", "decisions", "trades", "strategies") \
        + CHAIN_TABLES
    before = {t: j.query(f"SELECT * FROM {t} ORDER BY rowid")
              for t in tables}
    bank_before = [rb.canonical(o) for o in rb.load(j)]
    nq.record_for_bank_object(j, _bank_id(j), now_ms=3)
    nq.record_for_bank_object(j, _bank_id(j), now_ms=4)
    nq.load(j)
    assert {t: j.query(f"SELECT * FROM {t} ORDER BY rowid")
            for t in tables} == before
    assert [rb.canonical(o) for o in rb.load(j)] == bank_before
    # bank v1 still says it has no next-question generator
    assert all(o["next_questions"] == {
        "status": "NOT_AVAILABLE", "reason": "no_next_question_generator"}
        for o in rb.load(j))


def test_bank_and_result_modules_are_untouched_contracts():
    assert rb.SCHEMA == "research-bank-object.v1"
    assert rr.SCHEMA == "research-result.v1"
    assert "research_next_question" not in Path(rb.__file__).read_text()
    assert "research_next_question" not in Path(rr.__file__).read_text()


# ── 10 no enqueue / live / priority / suppression authority ─────────────
_MANDATED = {"status", "reason", "evidence_status"}


def test_record_carries_no_evaluative_or_authority_fields(tmp_path):
    j, _, _ = _recorded(tmp_path)
    for rec in nq.load(j):
        keys = set(_all_keys(rec)) - _MANDATED
        assert not {k for k in keys if _FORBIDDEN.search(k)}
        assert not {k for k in keys if re.search(
            r"novel|suppress|cooldown|repeat|queue|due|run_at|retry|next_run",
            k)}
        assert rec["authority"] == "context_only"


def test_writer_touches_only_its_own_table(tmp_path):
    j, _ = _filed(tmp_path)
    tables = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name != 'research_next_questions'")]
    before = {t: j.query(f"SELECT * FROM \"{t}\"") for t in tables}
    nq.record_for_bank_object(j, _bank_id(j), now_ms=3)
    assert {t: j.query(f"SELECT * FROM \"{t}\"") for t in tables} == before


def test_module_imports_only_the_bank_contract():
    src = Path(nq.__file__).read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "trader.cognition"}
    assert names == {"annotations", "research_bank"}
    assert not re.search(r"record_research_(question|plan|run|result)\b"
                         r"|record_research_bank_object|\.run\(", src)


def test_nothing_in_the_live_path_calls_it():
    root = Path(nq.__file__).resolve().parents[1]
    pat = re.compile(r"research_next_question|record_research_next_question")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_next_question.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive only
    assert callers == ["core/journal.py"]
    assert "import research_next_question" not in \
        (root / "core/journal.py").read_text()


def test_legacy_journal_gains_the_table_and_stays_readable(tmp_path):
    p = tmp_path / "legacy.db"
    Journal(p)
    with sqlite3.connect(p) as c:
        c.execute("DROP TABLE research_next_questions")
    j = Journal(p)
    assert j.research_next_questions() == []
    assert nq.load(j) == []
