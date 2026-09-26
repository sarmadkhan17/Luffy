"""Context-only recall of prior verified research-bank-object.v1 records for
a new strategy_decay research-question.v1.

Recall is read-only, bounded and deterministic. It returns exact identity
facts about verified bank objects filed for the same strategy before the
question was registered and sourced, and never suppresses, ranks or alters
question, plan, evidence, result or run creation.
"""
import ast
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_evidence import D, W
from tests.test_strategy_decay_research_plan import _FullHist
from tests.test_strategy_decay_research_run import (
    _clock, _fresh, _FORBIDDEN, _all_keys)
from trader.cognition import research_bank as rb
from trader.cognition import research_question as rq
from trader.cognition import research_recall as rc
from trader.cognition import research_run as run_
from trader.core.journal import Journal

ALL_TABLES = ("brain_events", "decisions", "research_questions",
              "research_plans", "research_evidence", "research_results",
              "research_runs", "research_bank_objects",
              "research_registrations")
_TRIGGERS = ("research_registrations_no_update",
             "research_registrations_no_delete",
             "research_registrations_no_replace")


def _log(j, hist):
    for r in hist.rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])


def _episode(j, spec="s1", start=100, spec_over=None):
    """A later W -> D transition for ``spec`` appended to the journal."""
    h = _FullHist()
    h.sweep({spec: W}, start)
    h.sweep({spec: D}, start + 10, spec_over=spec_over)
    _log(j, h)


def _timeline(tmp_path, spec_over=None, other_spec=True):
    """q1 (s1) registered at 1 and banked at 2; q2 (s1, later episode)
    registered at 5 and banked at 6. Optionally an s2 question registered at
    1 and banked at 2."""
    j = _fresh(tmp_path, verdicts=(W, D))
    if other_spec:
        h = _FullHist()
        h.sweep({"s2": D}, 50)
        _log(j, h)
    run_.run(j, "k1", 1, clock=_clock())
    assert rb.record_from_journal(j, now_ms=2)["refusals"] == []
    _episode(j, spec_over=spec_over)
    run_.run(j, "k2", 5, clock=_clock())
    assert rb.record_from_journal(j, now_ms=6)["refusals"] == []
    return j


def _qid(j, spec="s1", ms=None):
    rows = [r for r in j.research_questions(scope_id=spec)
            if ms is None or r["recorded_at_ms"] == ms]
    return rows[-1]["question_id"]


def _bank(j, **where):
    return [r for r in j.research_bank_objects()
            if all(r[k] == v for k, v in where.items())]


def _set(j, table, col, value, key, key_value):
    with j._tx() as c:
        c.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?",
                  (value, key_value))


def _receipts(j, sql, args=()):
    """Run ``sql`` against research_registrations with its immutability
    triggers dropped (a test-only stand-in for out-of-band DB edits); the
    triggers come back when the journal reopens."""
    with j._tx() as c:
        for t in _TRIGGERS:
            c.execute(f"DROP TRIGGER {t}")
        c.execute(sql, args)
    return Journal(j.db_path)


def _first_registered_at(j, record_type, record_id, ms):
    """Fixture: rewrite history so ``record_id`` was first registered at
    ``ms`` — a consistent, valid receipt and a matching row column."""
    reg = j.research_registration(record_type, record_id)
    env = Journal.registration_envelope(record_type, record_id,
                                        reg["canonical_sha256"], ms)
    j = _receipts(j, "UPDATE research_registrations SET recorded_at_ms=?, "
                  "envelope_json=?, envelope_sha256=? WHERE record_type=? "
                  "AND record_id=?",
                  (ms, env, rc._sha(env), record_type, record_id))
    table, key = (("research_questions", "question_id")
                  if record_type == rq.SCHEMA
                  else ("research_bank_objects", "bank_object_id"))
    _set(j, table, "recorded_at_ms", ms, key, record_id)
    return j


def _question_late(j, q, ms=100):
    return _first_registered_at(j, rq.SCHEMA, q, ms)


def _ids(out):
    return ([p["bank_object_id"] for p in out["prior"]],
            [x["bank_object_id"] for x in out["excluded"]])


def _dump(j):
    return {t: j.query(f"SELECT * FROM {t} ORDER BY rowid")
            for t in ALL_TABLES}


# ── 1 same strategy prior bank object is recalled ───────────────────────
def test_same_strategy_prior_bank_object_is_recalled(tmp_path):
    j = _timeline(tmp_path)
    q1, q2 = _qid(j, ms=1), _qid(j, ms=5)
    out = rc.recall(j, q2)
    [row] = _bank(j, question_id=q1, recorded_at_ms=2)
    assert [p["bank_object_id"] for p in out["prior"]] == \
        [row["bank_object_id"]]
    assert out["authority"] == "context_only"
    assert out["question"]["question_id"] == q2
    assert out["question"]["scope"] == {"kind": "strategy", "spec_id": "s1"}
    assert out["prior"][0]["result_status"] == "INCONCLUSIVE"
    assert out["excluded"] == []


# ── 2 a different strategy is never recalled ────────────────────────────
def test_different_strategy_is_never_recalled(tmp_path):
    j = _timeline(tmp_path)
    s2 = {r["bank_object_id"] for r in _bank(j, scope_id="s2")}
    assert s2                                   # known before q2, other spec
    s1_ids = {p["bank_object_id"] for p in rc.recall(j, _qid(j, ms=5))["prior"]}
    assert not s1_ids & s2
    # an s2 question sees nothing of s1, even with everything known before it
    qs2 = _qid(j, spec="s2")
    j = _question_late(j, qs2, 10**6)
    out = rc.recall(j, qs2)
    assert all(p["source"]["event_id"] < out["question"]["source_event_id"]
               for p in out["prior"])
    assert not {p["bank_object_id"] for p in out["prior"]} - s2


def test_a_bank_object_whose_scope_differs_from_its_row_fails_closed(tmp_path):
    j = _timeline(tmp_path)
    [row] = _bank(j, scope_id="s2", recorded_at_ms=2)
    _set(j, "research_bank_objects", "scope_id", "s1", "bank_object_id",
         row["bank_object_id"])
    with pytest.raises(rc.RecallError, match="bank_object_invalid"):
        rc.recall(j, _qid(j, ms=5))


# ── 3 future / same-time / not-yet-known records are excluded ───────────
def test_objects_not_known_before_registration_are_excluded(tmp_path):
    j = _timeline(tmp_path)
    # q1 was registered at 1; every bank object was recorded later
    out = rc.recall(j, _qid(j, ms=1))
    assert out["prior"] == [] and out["bound"]["examined"] == 0
    # recorded at exactly the registration time: not strictly before
    q2 = _qid(j, ms=5)
    [row] = _bank(j, recorded_at_ms=2, scope_id="s1")
    j = _first_registered_at(j, rb.SCHEMA, row["bank_object_id"], 5)
    assert rc.recall(j, q2)["prior"] == []
    # registered after the question (future): excluded too
    j = _first_registered_at(j, rb.SCHEMA, row["bank_object_id"], 6)
    assert rc.recall(j, q2)["prior"] == []
    j = _first_registered_at(j, rb.SCHEMA, row["bank_object_id"], 4)
    [p] = rc.recall(j, q2)["prior"]
    assert p["bank_registered_at_ms"] == 4


def test_known_object_whose_source_is_not_earlier_is_excluded(tmp_path):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    # q2 registered late: its own bank object (same source) is now known
    j = _question_late(j, q2)
    out = rc.recall(j, q2)
    [own] = _bank(j, question_id=q2)
    assert out["excluded"] == [{"bank_object_id": own["bank_object_id"],
                                "reason": rc.SOURCE_NOT_BEFORE_QUESTION}]
    assert own["bank_object_id"] not in {p["bank_object_id"]
                                         for p in out["prior"]}
    assert all(p["source"]["event_id"] < out["question"]["source_event_id"]
               for p in out["prior"])


# ── 4 exact identity facts ──────────────────────────────────────────────
def test_exact_identity_facts(tmp_path):
    j = _timeline(tmp_path)
    q1, q2 = _qid(j, ms=1), _qid(j, ms=5)
    out = rc.recall(j, q2)
    [p] = out["prior"]
    [row] = _bank(j, question_id=q1, recorded_at_ms=2)
    [obj] = [o for o in rb.load(j) if o["bank_object_id"] ==
             row["bank_object_id"]]
    lk = obj["links"]
    assert (p["run_id"], p["question_id"], p["plan_id"], p["evidence_id"],
            p["result_id"]) == (row["run_id"], q1, row["plan_id"],
                                row["evidence_id"], row["result_id"])
    assert p["question_canonical_sha256"] == lk["question"]["canonical_sha256"]
    assert p["evidence_canonical_sha256"] == lk["evidence"]["canonical_sha256"]
    assert p["result_canonical_sha256"] == lk["result"]["canonical_sha256"]
    assert p["bank_canonical_sha256"] == row["canonical_sha256"]
    assert p["bank_registered_at_ms"] == 2
    [q1rec] = [q for q in rq.load(j) if q["question_id"] == q1]
    assert p["source"] == {k: q1rec["source"][k] for k in rc._SOURCE_FACTS}
    assert p["identity_vs_question"] == {
        "question_id": "DIFFERENT", "question_canonical_sha256": "DIFFERENT",
        "source_event_id": "DIFFERENT", "source_sweep_id": "DIFFERENT",
        "source_record_sha256": "DIFFERENT",
        "source_health_fingerprint": "EQUAL",
        "source_spec_fingerprint": "EQUAL",
        "source_spec_content_sha256": "EQUAL",
        "source_simulation_settings_sha256": "EQUAL"}
    assert p["same_result_id_as"] == [] and p["same_evidence_id_as"] == []


def test_same_result_filed_by_two_runs_is_reported_as_exact_equality(
        tmp_path):
    j = _timeline(tmp_path)
    q1, q2 = _qid(j, ms=1), _qid(j, ms=5)
    j = _question_late(j, q2)
    prior = rc.recall(j, q2)["prior"]
    a, b = [p for p in prior if p["question_id"] == q1]
    assert a["result_id"] == b["result_id"] and a["run_id"] != b["run_id"]
    assert a["same_result_id_as"] == [b["bank_object_id"]]
    assert b["same_evidence_id_as"] == [a["bank_object_id"]]


# ── 5 changed evidence / hash is only a different identity ──────────────
def test_changed_fingerprints_are_reported_only_as_identity(tmp_path):
    j = _timeline(tmp_path, spec_over={"health_fingerprint": "fp2",
                                       "spec_fingerprint": None})
    [p] = rc.recall(j, _qid(j, ms=5))["prior"]
    ident = p["identity_vs_question"]
    assert ident["source_health_fingerprint"] == "DIFFERENT"
    assert ident["source_spec_fingerprint"] == "NOT_EXPOSED"
    assert ident["source_spec_content_sha256"] == "EQUAL"
    assert set(ident.values()) <= {"EQUAL", "DIFFERENT", "NOT_EXPOSED"}
    keys = set(_all_keys(p))
    assert not {k for k in keys if re.search(
        r"new|novel|redundan|similar|repeat|changed|relevan|success|fail", k)}


# ── 6 deterministic ordering and bounded count ──────────────────────────
def test_order_is_deterministic_and_count_is_bounded(tmp_path):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    j = _question_late(j, q2)
    full = rc.recall(j, q2)
    assert full == rc.recall(j, q2)
    seen = [(r["recorded_at_ms"], r["bank_object_id"])
            for r in _bank(j, scope_id="s1")]
    want = [b for _, b in sorted(seen, key=lambda x: (-x[0], x[1]))]
    for part in (full["prior"], full["excluded"]):
        got = [x["bank_object_id"] for x in part]
        assert got == [b for b in want if b in got]
    assert len(full["prior"]) + len(full["excluded"]) == len(want)
    assert full["bound"] == {"max_objects": rc.DEFAULT_MAX_OBJECTS,
                             "order": rc.ORDER, "examined": 3,
                             "more_known_before": False}
    one = rc.recall(j, q2, max_objects=1)
    assert one["bound"]["examined"] == 1 and one["bound"]["more_known_before"]
    assert len(one["prior"]) + len(one["excluded"]) == 1
    assert (one["prior"] + one["excluded"])[0]["bank_object_id"] == want[0]


@pytest.mark.parametrize("n", [0, -1, rc.MAX_OBJECTS + 1, True, 2.0, "3"])
def test_bound_must_be_a_small_positive_int(tmp_path, n):
    j = _timeline(tmp_path, other_spec=False)
    with pytest.raises(rc.RecallError, match="max_objects"):
        rc.recall(j, _qid(j, ms=5), max_objects=n)


# ── 7 corrupt or unverifiable input fails closed ────────────────────────
def test_corrupt_bank_object_fails_closed(tmp_path):
    j = _timeline(tmp_path)
    [row] = _bank(j, recorded_at_ms=2, scope_id="s1")
    _set(j, "research_bank_objects", "canonical_json",
         row["canonical_json"].replace("INCONCLUSIVE", "SUPPORTED"),
         "bank_object_id", row["bank_object_id"])
    with pytest.raises(rc.RecallError, match="bank_object_invalid"):
        rc.recall(j, _qid(j, ms=5))


def test_bank_object_with_a_broken_chain_fails_closed(tmp_path):
    j = _timeline(tmp_path)
    [row] = _bank(j, recorded_at_ms=2, scope_id="s1")
    with j._tx() as c:
        c.execute("DELETE FROM research_plans WHERE plan_id=?",
                  (row["plan_id"],))
    with pytest.raises(rc.RecallError, match="bank_object_invalid"):
        rc.recall(j, _qid(j, ms=5))


def test_missing_or_unverifiable_question_fails_closed(tmp_path):
    j = _timeline(tmp_path)
    with pytest.raises(rc.RecallError, match="question_missing"):
        rc.recall(j, "0" * 64)
    q2 = _qid(j, ms=5)
    _set(j, "research_questions", "trigger", "first_truthful_decayed",
         "question_id", q2)
    with pytest.raises(rc.RecallError, match="question_invalid"):
        rc.recall(j, q2)


# ── 8 recall cannot suppress or alter creation ──────────────────────────
def test_recall_is_read_only(tmp_path):
    j = _timeline(tmp_path)
    before = _dump(j)
    rc.recall(j, _qid(j, ms=5))
    rc.recall(j, _qid(j, ms=1))
    assert _dump(j) == before


def test_prior_inconclusive_object_never_prevents_a_new_run(tmp_path):
    j = _timeline(tmp_path)
    assert rc.recall(j, _qid(j, ms=5))["prior"][0]["result_status"] == \
        "INCONCLUSIVE"
    _episode(j, start=200)                      # a third decay episode
    out = run_.run(j, "k3", 9, clock=_clock())
    assert all(s["status"] == run_.COMPLETED for s in out["receipt"]["steps"])
    assert all(len(s["outcomes"]["inserted"]) == 1
               for s in out["receipt"]["steps"])
    assert rb.record_from_journal(j, now_ms=10)["refusals"] == []
    q3 = _qid(j, ms=9)
    third = rc.recall(j, q3)
    assert {p["question_id"] for p in third["prior"]} == \
        {_qid(j, ms=1), _qid(j, ms=5)}


# ── 9 no score / rank / salience / usefulness fields ────────────────────
def test_recall_carries_no_evaluative_fields(tmp_path):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    j = _question_late(j, q2)
    out = rc.recall(j, q2)
    keys = set(_all_keys(out))
    assert not {k for k in keys if _FORBIDDEN.search(k)}
    assert not {k for k in keys if re.search(
        r"relevan|similar|novel|redundan|cooldown|suppress|success|credib",
        k)}
    assert set(out) == {"schema", "authority", "question", "cutoff", "bound",
                        "prior", "excluded", "semantics"}


# ── 10 no Attention / Kernel / Analyst / Risk / Execution / live wiring ─
def test_module_imports_only_contract_modules():
    src = Path(rc.__file__).read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "trader.cognition"}
    assert names == {"annotations", "research_bank", "research_question"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(rc.__file__).resolve().parents[1]
    pat = re.compile(r"research_recall\b|research_bank_registrations\b")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_recall.py"
                     and pat.search(p.read_text(errors="ignore")))
    assert callers == ["core/journal.py"]
    assert "research_recall" not in (root / "core/journal.py").read_text()


def test_legacy_journal_without_bank_rows_recalls_nothing(tmp_path):
    j = _fresh(tmp_path, verdicts=(W, D))
    rq.record_from_journal(j, now_ms=1)
    out = rc.recall(j, _qid(j))
    assert out["prior"] == [] and out["excluded"] == []
    assert isinstance(Journal(j.db_path).research_bank_objects(), list)


# ── 11 registration times are integrity-bound receipts ──────────────────
@pytest.mark.parametrize("ms", [0, 4, 5, 6, 100, 10**9])
def test_changing_only_a_bank_row_timestamp_does_not_change_eligibility(
        tmp_path, ms):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    base = rc.recall(j, q2)
    assert len(base["prior"]) == 1
    for row in _bank(j, scope_id="s1"):
        _set(j, "research_bank_objects", "recorded_at_ms", ms,
             "bank_object_id", row["bank_object_id"])
    assert rc.recall(j, q2) == base


@pytest.mark.parametrize("ms", [0, 2, 3, 100, 10**9])
def test_changing_only_a_question_row_timestamp_does_not_change_eligibility(
        tmp_path, ms):
    j = _timeline(tmp_path)
    q1, q2 = _qid(j, ms=1), _qid(j, ms=5)
    base1, base2 = rc.recall(j, q1), rc.recall(j, q2)
    for q in (q1, q2):
        _set(j, "research_questions", "recorded_at_ms", ms, "question_id", q)
    assert (rc.recall(j, q1), rc.recall(j, q2)) == (base1, base2)
    assert base2["question"]["registered_at_ms"] == 5


def _envelope(reg, **over):
    env = {"schema": rc.REGISTRATION_SCHEMA,
           "record_type": reg["record_type"], "record_id": reg["record_id"],
           "canonical_sha256": reg["canonical_sha256"],
           "recorded_at_ms": reg["recorded_at_ms"], **over}
    return rq.canonical(env)


def _tampers(reg):
    """(sql SET clause, args) — each an out-of-band receipt edit that leaves
    the receipt inconsistent with itself or with its record. (A complete,
    self-consistent rewrite of a receipt is indistinguishable from a real
    first registration without an external anchor; see
    _first_registered_at.)"""
    moved = _envelope(reg, recorded_at_ms=1)
    return {
        "column_ms": ("recorded_at_ms=?", (1,)),
        "column_ms_later": ("recorded_at_ms=?", (10**9,)),
        "json_ms": ("envelope_json=?", (moved,)),
        "json_and_digest_ms": ("envelope_json=?, envelope_sha256=?",
                               (moved, rc._sha(moved))),
        "json_float_ms": ("envelope_json=?", (
            reg["envelope_json"].replace(
                f'"recorded_at_ms":{reg["recorded_at_ms"]}',
                f'"recorded_at_ms":{reg["recorded_at_ms"]}.0'),)),
        "digest": ("envelope_sha256=?", ("0" * 64,)),
        "content_hash": ("canonical_sha256=?", ("0" * 64,)),
        "content_hash_everywhere": (
            "canonical_sha256=?, envelope_json=?, envelope_sha256=?",
            ("0" * 64, _envelope(reg, canonical_sha256="0" * 64),
             rc._sha(_envelope(reg, canonical_sha256="0" * 64)))),
        "schema": ("envelope_json=?, envelope_sha256=?", (
            _envelope(reg, schema="research-registration.v2"),
            rc._sha(_envelope(reg, schema="research-registration.v2")))),
        "not_json": ("envelope_json=?", ("{",)),
    }


_TAMPER_KINDS = sorted(_tampers({"record_type": "t", "record_id": "i",
                                 "canonical_sha256": "c", "recorded_at_ms": 2,
                                 "envelope_json": '"recorded_at_ms":2'}))


@pytest.mark.parametrize("kind", _TAMPER_KINDS)
@pytest.mark.parametrize("target", ["question", "prior_bank", "own_bank"])
def test_a_tampered_registration_receipt_fails_closed(tmp_path, kind,
                                                      target):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    if target == "question":
        rtype, rid = rq.SCHEMA, q2
    else:
        # the recalled prior object, or q2's own later object (never
        # examined: a receipt edit must not hide or reveal it silently)
        [row] = (_bank(j, scope_id="s1", recorded_at_ms=2)
                 if target == "prior_bank" else _bank(j, question_id=q2))
        rtype, rid = rb.SCHEMA, row["bank_object_id"]
    reg = j.research_registration(rtype, rid)
    clause, args = _tampers(reg)[kind]
    j = _receipts(j, f"UPDATE research_registrations SET {clause} "
                  "WHERE record_type=? AND record_id=?", args + (rtype, rid))
    with pytest.raises(rc.RecallError, match=f"registration_invalid:{rid}"):
        rc.recall(j, q2)


@pytest.mark.parametrize("target", ["question", "bank"])
def test_a_legacy_row_without_a_receipt_fails_closed(tmp_path, target):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    rid = (q2 if target == "question" else
           _bank(j, scope_id="s1", recorded_at_ms=6)[0]["bank_object_id"])
    j = _receipts(j, "DELETE FROM research_registrations WHERE record_id=?",
                  (rid,))
    with pytest.raises(rc.RecallError, match=f"registration_missing:{rid}"):
        rc.recall(j, q2)
    # nothing is backfilled or guessed by recall or a later duplicate write
    assert j.research_registration(
        rq.SCHEMA if target == "question" else rb.SCHEMA, rid) is None
    j2 = Journal(j.db_path)
    rq.record_from_journal(j2, now_ms=50)
    rb.record_from_journal(j2, now_ms=50)
    with pytest.raises(rc.RecallError, match="registration_missing"):
        rc.recall(j2, q2)


def test_receipts_are_immutable_in_the_database(tmp_path):
    j = _timeline(tmp_path)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        with j._tx() as c:
            c.execute("UPDATE research_registrations SET recorded_at_ms=0")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        with j._tx() as c:
            c.execute("DELETE FROM research_registrations")


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE",
                                  "INSERT OR IGNORE", "INSERT"])
@pytest.mark.parametrize("target", ["question", "prior_bank", "later_bank",
                                    "digest_of_another"])
def test_receipts_cannot_be_replaced(tmp_path, verb, target):
    """Replacement's implicit delete does not fire the DELETE trigger when
    recursive_triggers is off; the INSERT trigger refuses it, on both
    unique keys, with every trigger left in place."""
    j = _timeline(tmp_path)
    assert j.query("PRAGMA recursive_triggers") == [
        {"recursive_triggers": 0}]
    q1, q2 = _qid(j, ms=1), _qid(j, ms=5)
    before = j.query("SELECT * FROM research_registrations ORDER BY rowid")
    base = rc.recall(j, q2)
    if target == "question":                     # move q2 later
        rtype, rid, ms = rq.SCHEMA, q2, 100
    else:
        # the recalled object, or q1's later object (registered at 6)
        # moved to 4 so it would enter recall
        [row] = _bank(j, question_id=q1,
                      recorded_at_ms=2 if target == "prior_bank" else 6)
        rtype, rid, ms = rb.SCHEMA, row["bank_object_id"], 4
    reg = j.research_registration(rtype, rid)
    env = Journal.registration_envelope(rtype, rid, reg["canonical_sha256"],
                                        ms)
    new = (rtype, rid, reg["canonical_sha256"], ms, rc._sha(env), env)
    if target == "digest_of_another":
        # a fresh key carrying an existing receipt's digest: REPLACE would
        # silently delete that other receipt
        other = j.research_registration(rq.SCHEMA, q2)
        new = (rtype, "e" * 64, reg["canonical_sha256"], ms,
               other["envelope_sha256"], env)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        with j._tx() as c:
            c.execute(f"{verb} INTO research_registrations(record_type,"
                      "record_id,canonical_sha256,recorded_at_ms,"
                      "envelope_sha256,envelope_json) VALUES (?,?,?,?,?,?)",
                      new)
    assert {r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='trigger'")} >= \
        set(_TRIGGERS)
    assert j.query("SELECT * FROM research_registrations ORDER BY rowid") \
        == before
    assert rc.recall(j, q2) == base


def test_receipt_is_written_with_the_first_insert(tmp_path):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    qrow = [r for r in j.research_questions() if r["question_id"] == q2][0]
    reg = j.research_registration(rq.SCHEMA, q2)
    assert reg == {
        "record_type": "research-question.v1", "record_id": q2,
        "canonical_sha256": rc._sha(qrow["canonical_json"]),
        "recorded_at_ms": 5,
        "envelope_sha256": rc._sha(reg["envelope_json"]),
        "envelope_json": _envelope(reg)}
    for row in j.research_bank_objects():
        breg = j.research_registration(rb.SCHEMA, row["bank_object_id"])
        assert breg["canonical_sha256"] == row["canonical_sha256"]
        assert breg["recorded_at_ms"] == row["recorded_at_ms"]
    n = len(j.query("SELECT * FROM research_registrations"))
    assert n == len(j.research_questions()) + len(j.research_bank_objects())


def test_receipt_rolls_back_with_a_failed_insert(tmp_path):
    j = _timeline(tmp_path)
    qrow = j.research_questions()[0]
    row = {k: qrow[k] for k in j._QUESTION_COLUMNS}
    row.update(question_id="f" * 64, source_event_id=10**6, scope_id=None)
    before = j.query("SELECT * FROM research_registrations ORDER BY rowid")
    with pytest.raises(sqlite3.IntegrityError):    # scope_id NOT NULL
        j.record_research_question(row, recorded_at_ms=7)
    assert j.research_registration(rq.SCHEMA, "f" * 64) is None
    assert j.query("SELECT * FROM research_registrations ORDER BY rowid") \
        == before


def test_duplicate_and_restart_keep_the_first_registration_time(tmp_path):
    j = _timeline(tmp_path)
    q2 = _qid(j, ms=5)
    before = j.query("SELECT * FROM research_registrations ORDER BY rowid")
    base = rc.recall(j, q2)
    j2 = Journal(j.db_path)                     # restart
    for m in (rq, rb):
        m.record_from_journal(j2, now_ms=99)
    qrow = [r for r in j2.research_questions() if r["question_id"] == q2][0]
    assert j2.record_research_question(
        {k: qrow[k] for k in j2._QUESTION_COLUMNS},
        recorded_at_ms=99) == "duplicate"
    brow = _bank(j2, scope_id="s1", recorded_at_ms=2)[0]
    assert j2.record_research_bank_object(
        {k: brow[k] for k in j2._BANK_COLUMNS},
        recorded_at_ms=99) == "duplicate"
    assert j2.query("SELECT * FROM research_registrations ORDER BY rowid") \
        == before
    assert rc.recall(j2, q2) == base


def test_a_re_inserted_row_keeps_its_original_receipt(tmp_path):
    j = _timeline(tmp_path)
    [brow] = _bank(j, scope_id="s1", recorded_at_ms=2)
    bid = brow["bank_object_id"]
    reg = j.research_registration(rb.SCHEMA, bid)
    with j._tx() as c:
        c.execute("DELETE FROM research_bank_objects WHERE bank_object_id=?",
                  (bid,))
    cols = {k: brow[k] for k in j._BANK_COLUMNS}
    # other content under the same ID: conflict, nothing written
    other = dict(cols, canonical_json=cols["canonical_json"] + " ")
    assert j.record_research_bank_object(other, recorded_at_ms=3) == \
        "conflict"
    assert j.research_bank_object(bid) is None
    # the same content: the row returns, its first registration stays
    assert j.record_research_bank_object(cols, recorded_at_ms=4) == \
        "inserted"
    assert j.research_registration(rb.SCHEMA, bid) == reg
    [p] = rc.recall(j, _qid(j, ms=5))["prior"]
    assert p["bank_object_id"] == bid and p["bank_registered_at_ms"] == 2
