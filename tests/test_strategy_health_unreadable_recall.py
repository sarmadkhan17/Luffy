"""strategy-health-unreadable-prior-research-recall.v1 — context only.

A sibling recall for the unreadable strategy-health family: verified
strategy-health-unreadable-bank-object.v1 records of the same strategy,
registered strictly before the new unreadable question and sourced from a
strictly earlier health event, with exact identity facts only. Registration
times come only from research-registration.v1 receipts; receipt-less or
tampered rows fail closed. No suppression, cooldown, novelty, rank or
no-repeat authority; no writes; decay recall untouched.
"""
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_plan import CF, D, EF, W
from tests.test_strategy_decay_research_recall import (_TAMPER_KINDS,
                                                       _receipts, _tampers)
from tests.test_strategy_health_unreadable_question import (NO_FRAME,
                                                            _journal, _write)
from tests.test_strategy_health_unreadable_run import _copy, _decay_journal
from trader.cognition import research_bank as rb
from trader.cognition import research_question as rq
from trader.cognition import research_recall as rc
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_recall as urc
from trader.cognition import research_unreadable_run as ru
from trader.core.journal import Journal

ROOT = Path(__file__).resolve().parents[1]
BASE = "380fb9e"                         # HEAD this package was cut from
QT, BT = "research_unreadable_questions", "research_unreadable_bank_objects"
BOTH = {"s1": "compile", "s2": "compile"}


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _series(tmp_path, n=1, new_ms=None, name="j.db"):
    """n compile_failed sweeps of s1 and s2, each followed by an unreadable
    run (ms 10i+2) and bank filing (ms 10i+3); then one later
    evaluation_failed sweep of s1 whose question is registered at
    ``new_ms`` (default 10n+5). Returns (journal, new question id)."""
    j = Journal(tmp_path / name)
    for i in range(n):
        _write(j, BOTH, i)
        assert ru.run(j, f"k{i}", 10 * i + 2)["status"] == "inserted"
        res = ub.record_from_journal(j, now_ms=10 * i + 3)
        assert res["inserted"] and not res["refusals"] and not res["conflict"]
    _write(j, {"s1": NO_FRAME}, n)
    [qid] = uq.record_from_journal(
        j, now_ms=10 * n + 5 if new_ms is None else new_ms)["inserted"]
    return j, qid


def _raw_insert(j, table, row, ms):
    """The pre-receipt writer: insert the row with no registration."""
    cols = list(row) + ["recorded_at_ms"]
    with j._tx() as c:
        c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES "
                  f"({','.join('?' * len(cols))})", tuple(row.values()) + (ms,))


def _dump(j):
    names = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f'SELECT * FROM "{t}" ORDER BY rowid') for t in names}


def _ids(out):
    return ([p["bank_object_id"] for p in out["prior"]],
            [x["bank_object_id"] for x in out["excluded"]])


def _bank_ids(j, spec):
    return {r["bank_object_id"] for r in j.research_unreadable_bank_objects()
            if r["scope_id"] == spec}


def _unreadable_on(j, monkeypatch):
    """On a journal whose health rows carry two unreadable observations of
    s1: run and bank only the earlier one's question (ms 4 / 5), then
    register the later one's question at 20. Returns the later ID."""
    real = uq.derive
    first = min(q["source"]["event_id"]
                for q in real(j.strategy_health_rows()).questions)

    def earliest(rows):
        d = real(rows)
        d.questions = [q for q in d.questions
                       if q["source"]["event_id"] == first]
        return d
    monkeypatch.setattr(uq, "derive", earliest)
    assert ru.run(j, "u", 4)["status"] == "inserted"
    assert ub.record_from_journal(j, now_ms=5)["inserted"]
    monkeypatch.undo()
    new = uq.record_from_journal(j, now_ms=20)["inserted"]
    ub.load(j), uq.load(j)
    return max(new, key=lambda i: j.research_unreadable_question_by_id(i)
               ["source_event_id"])


def _dumps(out):
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


# ── 1 same-spec prior object registered strictly before ─────────────────
def test_recalls_same_spec_object_registered_and_sourced_before(tmp_path):
    j, qid = _series(tmp_path)
    out = urc.recall(j, qid, max_objects=5)
    [p] = out["prior"]
    assert out["schema"] == "strategy-health-unreadable-prior-research-recall.v1"
    assert out["authority"] == "context_only" and out["excluded"] == []
    assert p["bank_object_id"] in _bank_ids(j, "s1")
    assert p["bank_registered_at_ms"] == 3 < out["question"]["registered_at_ms"]
    assert p["source"]["event_id"] < out["question"]["source_event_id"]
    assert (p["result_status"], p["result_assessment"], p["result_reason"]) \
        == ("INCONCLUSIVE", "NOT_ASSESSED",
            "no_registered_falsifier_predicates")
    [row] = [r for r in j.research_unreadable_bank_objects()
             if r["bank_object_id"] == p["bank_object_id"]]
    assert p["bank_canonical_sha256"] == row["canonical_sha256"] == \
        _sha(row["canonical_json"])
    rec = json.loads(row["canonical_json"])
    assert p["question_id"] == rec["links"]["question"]["question_id"]
    assert p["result_id"] == rec["links"]["result"]["result_id"]
    assert p["recorded_reasons"] == rec["question"]["recorded_reasons"]
    q = out["question"]
    assert q["schema"] == uq.SCHEMA and q["question_id"] == qid
    assert q["scope"] == {"kind": "strategy", "spec_id": "s1"}
    assert q["registered_at_ms"] == 15 == \
        j.research_registration(uq.SCHEMA, qid)["recorded_at_ms"]


# ── 2-3 same / newer registration time excluded ─────────────────────────
@pytest.mark.parametrize("new_ms,recalled", [(1, 0), (2, 0), (3, 0), (4, 1),
                                             (10**9, 1)])
def test_only_strictly_earlier_registration_is_recalled(tmp_path, new_ms,
                                                        recalled):
    j, qid = _series(tmp_path, new_ms=new_ms)       # prior bank at ms 3
    out = urc.recall(j, qid, max_objects=5)
    assert len(out["prior"]) == recalled and out["excluded"] == []
    assert out["bound"]["examined"] == recalled
    assert out["cutoff"]["bank_registered_before_ms"] == new_ms


def test_objects_registered_after_the_question_are_not_examined(tmp_path):
    j, qid = _series(tmp_path)                      # question at 15
    before = urc.recall(j, qid, max_objects=5)
    ru.run(j, "later", 16)                          # its own chain + banks
    assert ub.record_from_journal(j, now_ms=17)["inserted"]
    assert len([r for r in j.research_unreadable_bank_objects()
                if r["scope_id"] == "s1"]) == 3
    assert _dumps(urc.recall(j, qid, max_objects=5)) == _dumps(before)


def test_changing_only_row_timestamps_does_not_change_eligibility(tmp_path):
    j, qid = _series(tmp_path)
    base = urc.recall(j, qid, max_objects=5)
    with j._tx() as c:
        c.execute(f"UPDATE {BT} SET recorded_at_ms=10000000")
        c.execute(f"UPDATE {QT} SET recorded_at_ms=0")
    assert _dumps(urc.recall(j, qid, max_objects=5)) == _dumps(base)
    reader = j.research_unreadable_bank_registrations(ub.SCHEMA, "strategy",
                                                      "s1")
    assert reader and all("recorded_at_ms" not in r for r in reader)


# ── 4 source event must be strictly earlier ─────────────────────────────
def test_object_with_a_later_source_is_excluded_not_recalled(tmp_path,
                                                             monkeypatch):
    """The later observation's question is filed, run and banked first;
    the earlier observation's question is only registered afterwards. Its
    registration is later than the bank object but its source is not."""
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    real = uq.derive

    def later_only(rows):
        d = real(rows)
        d.questions = [q for q in d.questions if q["source"]["event_id"] > 1]
        return d
    monkeypatch.setattr(uq, "derive", later_only)
    ru.run(j, "k", 2)
    [later_bank] = ub.record_from_journal(j, now_ms=3)["inserted"]
    monkeypatch.undo()
    [early] = uq.record_from_journal(j, now_ms=10)["inserted"]
    ub.load(j), ru.load(j), uq.load(j)              # everything verifies
    out = urc.recall(j, early, max_objects=5)
    assert out["prior"] == []
    assert out["excluded"] == [{"bank_object_id": later_bank,
                                "reason": "source_not_before_question"}]
    assert out["bound"]["examined"] == 1


# ── 5 different spec excluded ───────────────────────────────────────────
def test_other_spec_objects_never_appear(tmp_path):
    j, qid = _series(tmp_path, n=2)
    out = urc.recall(j, qid, max_objects=200)
    prior, excluded = _ids(out)
    assert set(prior) == _bank_ids(j, "s1") and excluded == []
    assert not set(prior) & _bank_ids(j, "s2")
    assert all(p["source"]["event_id"] < out["question"]["source_event_id"]
               for p in out["prior"])


# ── 6-7 receipt-less legacy rows fail closed ────────────────────────────
def test_legacy_question_without_receipt_fails_closed(tmp_path):
    k = Journal(tmp_path / "k.db")
    _write(k, BOTH, 0)
    ru.run(k, "k0", 2)
    ub.record_from_journal(k, now_ms=3)
    _write(k, {"s1": NO_FRAME}, 1)
    q = max((x for x in uq.derive(k.strategy_health_rows()).questions
             if x["scope"]["spec_id"] == "s1"),
            key=lambda x: x["source"]["event_id"])
    assert k.research_unreadable_question_by_id(q["question_id"]) is None
    _raw_insert(k, QT, uq.row_for(q), 15)
    assert k.research_registration(uq.SCHEMA, q["question_id"]) is None
    uq.load(k)                                      # the row itself verifies
    before = _dump(k)
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_missing:{q['question_id']}$"):
        urc.recall(k, q["question_id"], max_objects=5)
    assert _dump(k) == before                       # never backfilled


def _legacy_bank(tmp_path, legacy_spec):
    """Filed history whose bank rows of ``legacy_spec`` were written by the
    pre-receipt writer (no receipt), then a new s1 question at 15."""
    j = Journal(tmp_path / "j.db")
    _write(j, BOTH, 0)
    ru.run(j, "k0", 2)
    c = _copy(tmp_path, j, "c.db")
    ub.record_from_journal(c, now_ms=3)
    for r in c.research_unreadable_bank_objects():
        row = {k: r[k] for k in c._UNREADABLE_BANK_COLUMNS}
        if r["scope_id"] == legacy_spec:
            _raw_insert(j, BT, row, 3)
        else:
            assert j.record_research_unreadable_bank_object(
                row, recorded_at_ms=3) == "inserted"
    _write(j, {"s1": NO_FRAME}, 1)
    [qid] = uq.record_from_journal(j, now_ms=15)["inserted"]
    ub.load(j)                                      # legacy rows still verify
    return j, qid


def test_legacy_same_spec_bank_object_without_receipt_fails_closed(tmp_path):
    j, qid = _legacy_bank(tmp_path, "s1")
    [legacy] = _bank_ids(j, "s1")
    assert j.research_registration(ub.SCHEMA, legacy) is None
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_missing:{legacy}$"):
        urc.recall(j, qid, max_objects=5)
    assert j.research_registration(ub.SCHEMA, legacy) is None


def test_legacy_other_spec_bank_object_is_not_relevant(tmp_path):
    j, qid = _legacy_bank(tmp_path, "s2")
    out = urc.recall(j, qid, max_objects=5)
    assert _ids(out) == (sorted(_bank_ids(j, "s1")), [])


# ── 8 tampered registration fails closed ────────────────────────────────
@pytest.mark.parametrize("kind", _TAMPER_KINDS)
@pytest.mark.parametrize("target", ["question", "prior_bank", "later_bank"])
def test_a_tampered_registration_fails_closed(tmp_path, kind, target):
    j, qid = _series(tmp_path)
    if target == "later_bank":
        ru.run(j, "later", 16)
        ub.record_from_journal(j, now_ms=17)
    if target == "question":
        rtype, rid = uq.SCHEMA, qid
    else:
        [p] = urc.recall(j, qid, max_objects=5)["prior"]
        ids = _bank_ids(j, "s1") - {p["bank_object_id"]}
        rtype = ub.SCHEMA
        rid = p["bank_object_id"] if target == "prior_bank" else sorted(ids)[0]
    reg = j.research_registration(rtype, rid)
    clause, args = _tampers(reg)[kind]
    j = _receipts(j, f"UPDATE research_registrations SET {clause} "
                  "WHERE record_type=? AND record_id=?",
                  args + (rtype, rid))
    before = _dump(j)
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_invalid:{rid}:"):
        urc.recall(j, qid, max_objects=5)
    assert _dump(j) == before


def test_a_deleted_receipt_fails_closed(tmp_path):
    j, qid = _series(tmp_path)
    [p] = urc.recall(j, qid, max_objects=5)["prior"]
    j = _receipts(j, "DELETE FROM research_registrations WHERE record_id=?",
                  (p["bank_object_id"],))
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_missing:{p['bank_object_id']}$"):
        urc.recall(j, qid, max_objects=5)


def test_a_decay_typed_receipt_under_an_unreadable_id_is_not_adopted(
        tmp_path):
    j, _ = _series(tmp_path)
    [row] = [r for r in j.research_unreadable_bank_objects()
             if r["scope_id"] == "s1"]
    k = Journal(tmp_path / "k.db")
    _write(k, BOTH, 0)
    ru.run(k, "k0", 2)
    _raw_insert(k, BT, {c: row[c] for c in k._UNREADABLE_BANK_COLUMNS}, 3)
    with k._tx() as c:                    # a receipt, but of another family
        assert k._register(c, rb.SCHEMA, row["bank_object_id"],
                           row["canonical_json"], 3)
    _write(k, {"s1": NO_FRAME}, 1)
    [qid] = uq.record_from_journal(k, now_ms=15)["inserted"]
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_missing:{row['bank_object_id']}$"):
        urc.recall(k, qid, max_objects=5)


# ── 9 tampered bank object / chain fails closed ─────────────────────────
def test_tampered_bank_row_projection_fails_closed(tmp_path):
    j, qid = _series(tmp_path)
    [p] = urc.recall(j, qid, max_objects=5)["prior"]
    with j._tx() as c:
        c.execute(f"UPDATE {BT} SET plan_id=? WHERE bank_object_id=?",
                  ("0" * 64, p["bank_object_id"]))
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^bank_object_invalid:{p['bank_object_id']}:"
                             "row_projection$"):
        urc.recall(j, qid, max_objects=5)


def test_tampered_bank_json_fails_closed_at_its_receipt(tmp_path):
    j, qid = _series(tmp_path)
    [p] = urc.recall(j, qid, max_objects=5)["prior"]
    with j._tx() as c:
        c.execute(f"UPDATE {BT} SET canonical_json=canonical_json||' ' "
                  "WHERE bank_object_id=?", (p["bank_object_id"],))
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_invalid:{p['bank_object_id']}:"):
        urc.recall(j, qid, max_objects=5)


@pytest.mark.parametrize("table,key", [
    ("research_unreadable_results", "result_id"),
    ("research_unreadable_evidence", "evidence_id"),
    ("research_unreadable_plans", "plan_id"),
    ("research_unreadable_runs", "run_id")])
def test_tampered_linked_chain_record_fails_closed(tmp_path, table, key):
    j, qid = _series(tmp_path)
    [p] = urc.recall(j, qid, max_objects=5)["prior"]
    with j._tx() as c:
        c.execute(f"UPDATE {table} SET canonical_json=canonical_json||' ' "
                  f"WHERE {key}=?", (p[key],))
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^bank_object_invalid:{p['bank_object_id']}:"
                             "source_invalid:"):
        urc.recall(j, qid, max_objects=5)


def test_tampered_new_question_fails_closed(tmp_path):
    j, qid = _series(tmp_path)
    with j._tx() as c:
        c.execute(f"UPDATE {QT} SET source_event_id=999 WHERE question_id=?",
                  (qid,))
    with pytest.raises(urc.UnreadableRecallError,
                       match="^question_invalid:row_projection$"):
        urc.recall(j, qid, max_objects=5)


def test_changed_live_health_source_fails_closed(tmp_path):
    j, qid = _series(tmp_path)
    [ev] = [r["id"] for r in j.strategy_health_rows()
            if r["subject"] == "s1"][-1:]
    with j._tx() as c:
        c.execute("UPDATE brain_events SET detail=detail||' ' WHERE id=?",
                  (ev,))
    with pytest.raises(urc.UnreadableRecallError, match="^question_invalid:"):
        urc.recall(j, qid, max_objects=5)


@pytest.mark.parametrize("qid", [None, "", "0" * 64, 7])
def test_unknown_question_fails_closed(tmp_path, qid):
    j, _ = _series(tmp_path)
    with pytest.raises(urc.UnreadableRecallError, match="^question_missing$"):
        urc.recall(j, qid, max_objects=5)


# ── 10 deterministic ordering / output / hash ───────────────────────────
def test_output_is_deterministic_and_ordered(tmp_path):
    j, qid = _series(tmp_path, n=3, name="a.db")
    k = _copy(tmp_path, j, "b.db")
    out = urc.recall(j, qid, max_objects=200)
    assert _dumps(out) == _dumps(urc.recall(j, qid, max_objects=200))
    # the same stored history, reopened elsewhere, recalls the same bytes
    assert _sha(_dumps(out)) == _sha(_dumps(urc.recall(k, qid,
                                                       max_objects=200)))
    keys = [(-p["bank_registered_at_ms"], p["bank_object_id"])
            for p in out["prior"]]
    assert keys == sorted(keys) and len(keys) == 6
    assert [p["bank_registered_at_ms"] for p in out["prior"]] == \
        [23, 23, 23, 13, 13, 3]
    assert out["bound"]["order"] == \
        "bank_registered_at_ms_desc_then_bank_object_id_asc"


def test_same_result_and_evidence_across_runs_are_exact_ids(tmp_path):
    j, qid = _series(tmp_path, n=2)
    out = urc.recall(j, qid, max_objects=200)
    by = {p["bank_object_id"]: p for p in out["prior"]}
    for p in out["prior"]:
        for key in ("result_id", "evidence_id"):
            assert p[f"same_{key}_as"] == [
                o["bank_object_id"] for o in out["prior"]
                if o is not p and o[key] == p[key]]
            assert all(by[o][key] == p[key] for o in p[f"same_{key}_as"])
    # q0's result was reached by both runs: two objects, one result
    assert sum(1 for p in out["prior"] if p["same_result_id_as"]) == 2


# ── 11 max_objects enforced ─────────────────────────────────────────────
@pytest.mark.parametrize("m,examined,more", [(1, 1, True), (2, 2, True),
                                             (5, 5, True), (6, 6, False),
                                             (200, 6, False)])
def test_max_objects_bounds_examined_objects(tmp_path, m, examined, more):
    j, qid = _series(tmp_path, n=3)
    full = urc.recall(j, qid, max_objects=200)["prior"]
    out = urc.recall(j, qid, max_objects=m)
    assert out["bound"] == {"max_objects": m, "order": urc.ORDER,
                            "examined": examined, "more_known_before": more}
    assert [p["bank_object_id"] for p in out["prior"]] == \
        [p["bank_object_id"] for p in full][:examined]


def test_bound_leaves_unexamined_rows_unverified_but_receipts_checked(
        tmp_path):
    """max_objects bounds verified candidates, not receipt checks: a chain
    tamper beyond the bound is not examined, a receipt tamper is."""
    j, qid = _series(tmp_path, n=3)
    oldest = urc.recall(j, qid, max_objects=200)["prior"][-1]
    with j._tx() as c:
        c.execute(f"UPDATE {BT} SET plan_id=? WHERE bank_object_id=?",
                  ("0" * 64, oldest["bank_object_id"]))
    assert len(urc.recall(j, qid, max_objects=5)["prior"]) == 5
    with pytest.raises(urc.UnreadableRecallError, match="row_projection"):
        urc.recall(j, qid, max_objects=6)
    j = _receipts(j, "DELETE FROM research_registrations WHERE record_id=?",
                  (oldest["bank_object_id"],))
    with pytest.raises(urc.UnreadableRecallError, match="registration_missing"):
        urc.recall(j, qid, max_objects=1)


@pytest.mark.parametrize("m", [0, -1, 201, True, 2.0, "2", None])
def test_invalid_max_objects_is_refused(tmp_path, m):
    j, qid = _series(tmp_path)
    with pytest.raises(urc.UnreadableRecallError, match="^max_objects$"):
        urc.recall(j, qid, max_objects=m)


def test_max_objects_is_required(tmp_path):
    j, qid = _series(tmp_path)
    with pytest.raises(TypeError):
        urc.recall(j, qid)


# ── 12 exact EQUAL / DIFFERENT / NOT_EXPOSED facts only ─────────────────
_IDENTITY_KEYS = {
    "question_id", "question_canonical_sha256", "source_event_id",
    "source_record_sha256", "source_sweep_id", "source_sweep_started_at",
    "source_spec_name", "source_health_fingerprint",
    "source_spec_content_sha256", "source_sweep_record_event_id",
    "source_sweep_record_record_sha256", "recorded_verdict",
    "recorded_verdict_reason", "recorded_coverage_complete",
    "recorded_coverage_faults", "recorded_error_stage",
    "recorded_error_class"}


def test_identity_facts_are_exact_equalities(tmp_path):
    j, qid = _series(tmp_path)
    [p] = urc.recall(j, qid, max_objects=5)["prior"]
    q = uq.from_json(j.research_unreadable_question_by_id(qid)
                     ["canonical_json"])
    idv = p["identity_vs_question"]
    assert set(idv) == _IDENTITY_KEYS
    assert set(idv.values()) <= {"EQUAL", "DIFFERENT", "NOT_EXPOSED"}

    def want(a, b):
        if a is None or b is None:
            return "NOT_EXPOSED"
        return "EQUAL" if uq.canonical(a) == uq.canonical(b) else "DIFFERENT"
    for k, v in q["source"].items():
        if k in ("event_id", "record_sha256", "sweep_id", "sweep_started_at",
                 "spec_name", "health_fingerprint", "spec_content_sha256"):
            assert idv[f"source_{k}"] == want(v, p["source"][k])
    for k in ("event_id", "record_sha256"):
        assert idv[f"source_sweep_record_{k}"] == want(
            q["source"]["sweep_record"][k], p["source"]["sweep_record"][k])
    for k, v in q["recorded_reasons"].items():
        assert idv[f"recorded_{k}"] == want(v, p["recorded_reasons"][k])
    # this pair: same spec, other observation; compile vs evaluation gap
    assert idv["question_id"] == idv["source_event_id"] == "DIFFERENT"
    assert idv["source_spec_name"] == "EQUAL"
    assert idv["recorded_verdict"] == "DIFFERENT"
    assert idv["recorded_verdict_reason"] == "NOT_EXPOSED"   # compile: None
    assert idv["recorded_error_class"] == "NOT_EXPOSED"      # new: None


def test_repeated_same_reasons_are_equal_not_similar(tmp_path):
    j, _ = _series(tmp_path, n=2)
    rows = j.research_unreadable_questions("s1")     # source event order
    q1 = rows[1]["question_id"]
    # q1 (compile, sweep 1) vs the prior compile object of sweep 0
    out = urc.recall(j, q1, max_objects=5)
    [p] = out["prior"]
    idv = p["identity_vs_question"]
    assert idv["recorded_verdict"] == idv["recorded_error_stage"] == \
        idv["recorded_error_class"] == "EQUAL"
    assert idv["question_id"] == idv["source_record_sha256"] == "DIFFERENT"


def test_no_unavailable_fields_are_invented(tmp_path):
    j, qid = _series(tmp_path)
    text = _dumps(urc.recall(j, qid, max_objects=5))
    for word in ("simulation_settings", "spec_fingerprint", "regime",
                 "symbol", "asset", "similarity\"", "score\""):
        assert word not in text.replace(urc.SEMANTICS, ""), word


# ── 13 no similarity / novelty / suppression / no-repeat authority ─────
def test_output_shape_carries_no_authority(tmp_path):
    j, qid = _series(tmp_path)
    out = urc.recall(j, qid, max_objects=5)
    assert set(out) == {"schema", "authority", "question", "cutoff", "bound",
                        "prior", "excluded", "semantics"}
    assert out["authority"] == "context_only"
    keys = set()

    def walk(v):
        if isinstance(v, dict):
            keys.update(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk({k: v for k, v in out.items() if k != "semantics"})
    bad = re.compile(r"simil|novel|suppress|cooldown|cool_down|repeat|rank|"
                     r"priorit|salien|useful|score|answer|block|skip|relev")
    assert not [k for k in keys if bad.search(k)]


def test_recall_never_prevents_or_alters_research(tmp_path):
    j, qid = _series(tmp_path, name="a.db")
    _write(j, {"s1": NO_FRAME}, 5)
    k = _copy(tmp_path, j, "b.db")
    assert urc.recall(j, qid, max_objects=5)["prior"]
    a = (uq.record_from_journal(j, now_ms=50), ru.run(j, "n", 51),
         ub.record_from_journal(j, now_ms=52))
    b = (uq.record_from_journal(k, now_ms=50), ru.run(k, "n", 51),
         ub.record_from_journal(k, now_ms=52))
    assert len(a[0]["inserted"]) == 1 and a[2]["inserted"]
    assert a[1]["status"] == b[1]["status"] == "inserted"
    # run telemetry (copied into bank objects as cost) holds wall-clock
    # timings; every identity and every other record is identical
    assert a[1]["receipt"] == b[1]["receipt"]
    assert (a[0], a[2]) == (b[0], b[2])
    same = ("research_unreadable_questions", "research_unreadable_plans",
            "research_unreadable_evidence", "research_unreadable_results")
    assert {t: v for t, v in _dump(j).items() if t in same} == \
        {t: v for t, v in _dump(k).items() if t in same}


def test_module_defines_no_policy_surface():
    src = (ROOT / "trader/cognition/research_unreadable_recall.py").read_text()
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert funcs == {"_sha", "_eq", "_registered_at", "_question",
                     "_known_before", "_identity", "_prior", "recall", "bad"}


# ── 14 decay objects never enter the unreadable recall ─────────────────
def test_decay_questions_and_objects_never_enter(tmp_path, monkeypatch):
    j = _decay_journal(tmp_path, [W, CF, D, CF, D], name="d.db")
    assert rb.record_from_journal(j, now_ms=3)["inserted"]
    qid = _unreadable_on(j, monkeypatch)
    assert j.research_bank_objects() and j.research_unreadable_bank_objects()
    out = urc.recall(j, qid, max_objects=200)
    decay_ids = {r["bank_object_id"] for r in j.research_bank_objects()}
    prior, _ = _ids(out)
    assert prior and set(prior) <= _bank_ids(j, "s1")
    assert not set(prior) & decay_ids
    for q in j.research_questions():
        with pytest.raises(urc.UnreadableRecallError,
                           match="^question_missing$"):
            urc.recall(j, q["question_id"], max_objects=5)


def test_a_decay_row_forged_into_the_unreadable_table_fails_closed(
        tmp_path, monkeypatch):
    j = _decay_journal(tmp_path, [W, CF, D, CF, D], name="d.db")
    rb.record_from_journal(j, now_ms=3)
    qid = _unreadable_on(j, monkeypatch)
    [d] = j.research_bank_objects()[:1]
    forged = {k: d.get(k) for k in j._UNREADABLE_BANK_COLUMNS}
    _raw_insert(j, BT, forged, 1)
    with pytest.raises(urc.UnreadableRecallError,
                       match=f"^registration_missing:{d['bank_object_id']}$"):
        urc.recall(j, qid, max_objects=5)


# ── 15-16 unreadable objects never affect decay recall; bytes identical ─
@pytest.mark.parametrize("verdicts", [[W, CF, D, CF, D],
                                      [W, CF, D, EF, D, CF, D]])
def test_decay_recall_output_is_byte_identical(tmp_path, verdicts,
                                               monkeypatch):
    a = _decay_journal(tmp_path, verdicts, name="a.db")
    assert rb.record_from_journal(a, now_ms=3)["inserted"]
    b = _copy(tmp_path, a, "b.db")
    uqid = _unreadable_on(b, monkeypatch)
    assert urc.recall(b, uqid, max_objects=200)["prior"]
    for q in a.research_questions():
        for m in (1, 20, 200):
            assert _dumps(rc.recall(a, q["question_id"], max_objects=m)) == \
                _dumps(rc.recall(b, q["question_id"], max_objects=m))
    with pytest.raises(rc.RecallError, match="^question_missing$"):
        rc.recall(b, uqid)


def test_decay_recall_and_bank_modules_are_unchanged():
    for path in ("trader/cognition/research_recall.py",
                 "trader/cognition/research_bank.py",
                 "trader/cognition/research_question.py",
                 "trader/cognition/research_unreadable_bank.py",
                 "trader/cognition/research_unreadable_question.py"):
        diff = subprocess.run(["git", "diff", BASE, "--", path], cwd=ROOT,
                              capture_output=True, text=True, check=True)
        assert diff.stdout == "", path


def test_decay_journal_readers_are_unchanged():
    src = (ROOT / "trader/core/journal.py").read_text()
    base = subprocess.run(["git", "show", f"{BASE}:trader/core/journal.py"],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout
    defs = lambda s: set(re.findall(r"\n    def (\w+)\(", s))
    assert defs(src) - defs(base) == {"research_unreadable_bank_registrations"}
    assert defs(base) <= defs(src)
    for name in ("research_bank_registrations", "research_registration",
                 "research_unreadable_bank_objects", "_record_registered",
                 "_register"):
        body = lambda s: re.split(r"\n    (?:def |class |# --)",
                                  s.split(f"def {name}(", 1)[1], 1)[0]
        assert body(src) == body(base), name
    new = src.split("def research_unreadable_bank_registrations(", 1)[1]
    new = re.split(r"\n    (?:def |# --)", new, 1)[0]
    assert "SELECT" in new and not re.search(
        r"INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|_tx\(", new)


# ── 17 no writes ────────────────────────────────────────────────────────
def test_recall_writes_nothing(tmp_path):
    j, qid = _series(tmp_path, n=2)
    before = _dump(j)
    changes = j._conn().total_changes
    urc.recall(j, qid, max_objects=200)
    with pytest.raises(urc.UnreadableRecallError):
        urc.recall(j, "0" * 64, max_objects=5)
    assert _dump(j) == before and j._conn().total_changes == changes


# ── 18 no Kernel / Attention / Risk / Execution / live wiring ──────────
def test_module_imports_only_contract_modules():
    src = (ROOT / "trader/cognition/research_unreadable_recall.py").read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "trader.cognition"}
    assert names == {"annotations", "research_unreadable_bank",
                     "research_unreadable_question"}


def test_nothing_in_the_live_path_calls_it():
    root = ROOT / "trader"
    pat = re.compile(r"research_unreadable_recall\b|"
                     r"research_unreadable_bank_registrations\b")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_unreadable_recall.py"
                     and pat.search(p.read_text(errors="ignore")))
    assert callers == ["core/journal.py"]
    assert "research_unreadable_recall" not in \
        (root / "core/journal.py").read_text()
