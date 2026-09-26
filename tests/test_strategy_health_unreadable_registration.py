"""First-registration receipts for strategy-health-unreadable-question.v1
and strategy-health-unreadable-bank-object.v1.

Each first insert writes one research-registration.v1 receipt in the same
transaction, binding the object's schema, ID, canonical SHA-256 and the
insert-time recorded_at_ms. Duplicates and conflicts never register;
legacy rows filed before receipts existed are never backfilled. Plan,
evidence, result and run rows never register; decay receipts and recall are
unchanged. Provenance only: no recall, suppression or no-repeat here.
"""
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_plan import CF, D, EF, W
from tests import test_strategy_health_unreadable_question as uq_pinned
from tests.test_strategy_health_unreadable_question import (NO_FRAME,
                                                            _journal, _write,
                                                            pinned)
from tests.test_strategy_health_unreadable_run import (_copy, _decay_journal,
                                                       _two)
from trader.cognition import research_bank as rb
from trader.cognition import research_recall as rc
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_evidence as ue
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_result as ur
from trader.cognition import research_unreadable_run as ru
from trader.core.journal import Journal

ROOT = Path(__file__).resolve().parents[1]
BASE = "4126c3e"                         # HEAD this package was cut from
QT, BT = "research_unreadable_questions", "research_unreadable_bank_objects"


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _regs(j, record_type=None):
    if record_type is None:
        return j.query("SELECT * FROM research_registrations ORDER BY rowid")
    return j.query("SELECT * FROM research_registrations WHERE record_type=? "
                   "ORDER BY rowid", (record_type,))


def _assert_receipt(j, record_type, record_id, table, key):
    """The one receipt binds exactly the stored row's schema, ID, canonical
    SHA-256 and insert-time recorded_at_ms, in a canonical envelope."""
    [row] = j.query(f"SELECT * FROM {table} WHERE {key}=?", (record_id,))
    [reg] = [r for r in _regs(j) if r["record_id"] == record_id]
    assert reg["record_type"] == record_type == row["schema"]
    assert reg["canonical_sha256"] == row["canonical_sha256"] == \
        _sha(row["canonical_json"])
    assert reg["recorded_at_ms"] == row["recorded_at_ms"]
    env = Journal.registration_envelope(record_type, record_id,
                                        row["canonical_sha256"],
                                        row["recorded_at_ms"])
    assert reg["envelope_json"] == env
    assert reg["envelope_sha256"] == _sha(env)
    assert json.loads(env) == {"schema": "research-registration.v1",
                               "record_type": record_type,
                               "record_id": record_id,
                               "canonical_sha256": row["canonical_sha256"],
                               "recorded_at_ms": row["recorded_at_ms"]}


def _filed(tmp_path, j=None):
    j = j if j is not None else _two(tmp_path)
    ru.run(j, "k", 5)
    res = ub.record_from_journal(j, now_ms=9)
    assert res["inserted"] and res["refusals"] == [] and res["conflict"] == []
    return j, res


def _sub(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def _pre_bank(tmp_path, name):
    """(src, pre, rows): src has its bank objects filed; pre is a copy of
    the same journal taken after the unreadable run, before bank filing;
    rows are src's filed bank-object rows."""
    base = _two(_sub(tmp_path, name))
    ru.run(base, "k", 5)
    pre = _copy(tmp_path, base, f"{name}-pre.db")
    src = base
    res = ub.record_from_journal(src, now_ms=9)
    assert res["inserted"] and not res["refusals"] and not res["conflict"]
    rows = [{k: r[k] for k in src._UNREADABLE_BANK_COLUMNS}
            for r in src.research_unreadable_bank_objects()]
    return src, pre, rows


def _q_row(j):
    [q] = uq.derive(j.strategy_health_rows()).questions
    return uq.row_for(q)


def _raw_insert(j, table, row, ms):
    """The pre-receipt writer: insert the row with no registration."""
    cols = list(row) + ["recorded_at_ms"]
    with j._tx() as c:
        c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES "
                  f"({','.join('?' * len(cols))})", tuple(row.values()) + (ms,))


# ── 1-4 first insert writes one exact receipt ───────────────────────────
def test_first_question_insert_creates_one_exact_receipt(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    res = uq.record_from_journal(j, now_ms=41)
    [qid] = res["inserted"]
    [reg] = _regs(j)
    assert reg["record_type"] == uq.SCHEMA == \
        "strategy-health-unreadable-question.v1"
    assert reg["record_id"] == qid and reg["recorded_at_ms"] == 41
    _assert_receipt(j, uq.SCHEMA, qid, QT, "question_id")
    assert j.research_registration(uq.SCHEMA, qid) == reg


def test_first_bank_insert_creates_one_exact_receipt(tmp_path):
    j, res = _filed(tmp_path)
    regs = _regs(j, ub.SCHEMA)
    assert ub.SCHEMA == "strategy-health-unreadable-bank-object.v1"
    assert sorted(r["record_id"] for r in regs) == sorted(res["inserted"])
    assert len(regs) == len(j.research_unreadable_bank_objects()) == 2
    for r in regs:
        assert r["recorded_at_ms"] == 9
        _assert_receipt(j, ub.SCHEMA, r["record_id"], BT, "bank_object_id")


def test_receipt_time_is_insert_time_not_an_object_timestamp(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    row = _q_row(j)
    assert j.record_research_unreadable_question(
        row, recorded_at_ms=123456) == "inserted"
    [reg] = _regs(j)
    assert reg["recorded_at_ms"] == 123456
    src = json.loads(row["canonical_json"])["source"]
    assert all(v != 123456 for v in src.values())


# ── 5-6 duplicate / conflict never register ─────────────────────────────
def test_duplicate_refile_creates_no_new_or_changed_receipt(tmp_path):
    j, _ = _filed(tmp_path)
    before = _regs(j)
    assert uq.record_from_journal(j, now_ms=77)["inserted"] == []
    assert ub.record_from_journal(j, now_ms=78)["inserted"] == []
    [run] = j.research_unreadable_runs()
    assert ub.record_from_run(j, run["run_id"], now_ms=80)["inserted"] == []
    assert _regs(j) == before


def test_conflicting_question_writes_no_receipt(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    row = _q_row(j)
    assert j.record_research_unreadable_question(row, recorded_at_ms=1) \
        == "inserted"
    before = (_regs(j), j.research_unreadable_questions())
    other = {**row, "question_id": "0" * 64}           # same source event
    assert j.record_research_unreadable_question(other, recorded_at_ms=2) \
        == "conflict"
    same_id = {**row, "canonical_json": row["canonical_json"] + " "}
    assert j.record_research_unreadable_question(same_id, recorded_at_ms=3) \
        == "conflict"
    assert (_regs(j), j.research_unreadable_questions()) == before


def test_conflicting_bank_object_writes_no_receipt(tmp_path):
    j, _ = _filed(tmp_path)
    before = (_regs(j), j.research_unreadable_bank_objects())
    row = {k: j.research_unreadable_bank_objects()[0][k]
           for k in j._UNREADABLE_BANK_COLUMNS}
    for bad in ({**row, "bank_object_id": "0" * 64},   # same (run, result)
                {**row, "canonical_json": row["canonical_json"] + " "}):
        assert j.record_research_unreadable_bank_object(
            bad, recorded_at_ms=99) == "conflict"
    assert (_regs(j), j.research_unreadable_bank_objects()) == before


@pytest.mark.parametrize("kind", ["question", "bank"])
@pytest.mark.parametrize("same_content", [True, False])
def test_orphan_receipt_for_an_absent_object_is_a_conflict(
        tmp_path, kind, same_content):
    """A receipt already present for an absent row, even one binding the
    very same content, is never adopted: the insert is refused, nothing is
    written, and the receipt is untouched. Otherwise the receipt would keep
    a time other than the row's insert time."""
    if kind == "question":
        j = _journal(tmp_path, {"s1": NO_FRAME})
        row, schema, table, key = _q_row(j), uq.SCHEMA, QT, "question_id"
        write = j.record_research_unreadable_question
    else:
        _, j, rows = _pre_bank(tmp_path, "b")
        row, schema, table, key = rows[0], ub.SCHEMA, BT, "bank_object_id"
        write = j.record_research_unreadable_bank_object
    content = row["canonical_json"] if same_content else "{}"
    with j._tx() as c:
        assert j._register(c, schema, row[key], content, 5)
    before = (_regs(j), j.query(f"SELECT * FROM {table}"))
    assert write(row, recorded_at_ms=123456) == "conflict"
    assert (_regs(j), j.query(f"SELECT * FROM {table}")) == before
    assert j.research_registration(schema, row[key])["recorded_at_ms"] == 5


def test_every_receipt_time_equals_its_row_insert_time(tmp_path):
    j, _ = _filed(tmp_path)
    uq.record_from_journal(j, now_ms=77)
    for t, key in ((QT, "question_id"), (BT, "bank_object_id")):
        for r in j.query(f"SELECT * FROM {t}"):
            reg = j.research_registration(r["schema"], r[key])
            assert reg["recorded_at_ms"] == r["recorded_at_ms"]


# ── 7-8 legacy rows stay receipt-less ───────────────────────────────────
def test_legacy_question_without_receipt_stays_receipt_less(tmp_path):
    j = _journal(tmp_path, {"s1": NO_FRAME})
    row = _q_row(j)
    _raw_insert(j, QT, row, 3)
    stored = j.research_unreadable_questions()
    assert uq.record_from_journal(j, now_ms=50)["duplicate"] == \
        [row["question_id"]]
    assert j.record_research_unreadable_question(row, recorded_at_ms=51) \
        == "duplicate"
    assert _regs(j) == [] and j.research_unreadable_questions() == stored
    assert j.research_registration(uq.SCHEMA, row["question_id"]) is None
    uq.load(j)                           # the legacy row still verifies


def test_legacy_bank_object_without_receipt_stays_receipt_less(tmp_path):
    # the same history, run and objects, filed by the pre-receipt writer
    _, j, rows = _pre_bank(tmp_path, "legacy")
    for row in rows:
        _raw_insert(j, BT, row, 4)
    before, stored = _regs(j), j.research_unreadable_bank_objects()
    assert _regs(j, ub.SCHEMA) == []
    res = ub.record_from_journal(j, now_ms=60)
    assert res["inserted"] == [] and sorted(res["duplicate"]) == \
        sorted(r["bank_object_id"] for r in rows)
    assert _regs(j) == before and _regs(j, ub.SCHEMA) == []
    assert j.research_unreadable_bank_objects() == stored
    assert len(ub.load(j)) == len(rows)  # legacy objects still verify


def test_no_backfill_when_a_new_row_is_filed_next_to_legacy_rows(tmp_path):
    j = _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})
    qs = uq.derive(j.strategy_health_rows()).questions
    old, new = uq.row_for(qs[0]), uq.row_for(qs[1])
    _raw_insert(j, QT, old, 3)
    res = uq.record_from_journal(j, now_ms=70)
    assert res == {"inserted": [new["question_id"]],
                   "duplicate": [old["question_id"]], "conflict": [],
                   "refusals": []}
    assert [r["record_id"] for r in _regs(j)] == [new["question_id"]]
    assert j.research_registration(uq.SCHEMA, old["question_id"]) is None


# ── 9 a failed registration rolls the insert back ───────────────────────
@pytest.mark.parametrize("kind", ["question", "bank"])
def test_failed_registration_rolls_back_the_insert(tmp_path, kind,
                                                   monkeypatch):
    if kind == "question":
        j = _journal(tmp_path, {"s1": NO_FRAME})
        rows, table = [_q_row(j)], QT
        write = j.record_research_unreadable_question
    else:
        _, j, rows = _pre_bank(tmp_path, "b")
        table, write = BT, j.record_research_unreadable_bank_object
    before = (_regs(j), j.query(f"SELECT * FROM {table}"))

    def boom(*a, **k):
        raise RuntimeError("registration failed")
    monkeypatch.setattr(j, "_register", boom)
    with pytest.raises(RuntimeError, match="registration failed"):
        write(rows[0], recorded_at_ms=8)
    assert (_regs(j), j.query(f"SELECT * FROM {table}")) == before

    monkeypatch.setattr(j, "_register", lambda *a, **k: False)
    assert write(rows[0], recorded_at_ms=8) == "conflict"
    assert (_regs(j), j.query(f"SELECT * FROM {table}")) == before

    monkeypatch.undo()
    assert write(rows[0], recorded_at_ms=8) == "inserted"
    assert len(j.query(f"SELECT * FROM {table}")) == len(before[1]) + 1
    assert len(_regs(j)) == len(before[0]) + 1


def test_insert_and_receipt_share_one_transaction(tmp_path):
    """The receipt is written after the row inside the same open
    transaction: a failure after both statements leaves neither."""
    j = _journal(tmp_path, {"s1": NO_FRAME})
    row = _q_row(j)
    real = j._register
    seen = {}

    def spy(c, *a):
        seen["row_visible"] = c.execute(
            f"SELECT COUNT(*) FROM {QT}").fetchone()[0]
        seen["in_tx"] = c.in_transaction
        assert real(c, *a)
        raise RuntimeError("after both")
    j._register = spy
    with pytest.raises(RuntimeError):
        j.record_research_unreadable_question(row, recorded_at_ms=1)
    assert seen == {"row_visible": 1, "in_tx": True}
    assert _regs(j) == [] and j.research_unreadable_questions() == []


# ── 10 plan / evidence / result / run never register ────────────────────
def test_plan_evidence_result_run_create_no_receipts(tmp_path):
    j = _two(tmp_path)
    out = ru.run(j, "k", 5)
    assert out["status"] == "inserted"
    ids = {"plan": [r["plan_id"] for r in j.research_unreadable_plans()],
           "evidence": [r["evidence_id"]
                        for r in j.research_unreadable_evidence()],
           "result": [r["result_id"] for r in j.research_unreadable_results()],
           "run": [r["run_id"] for r in j.research_unreadable_runs()]}
    assert all(ids.values())
    regs = _regs(j)
    assert {r["record_type"] for r in regs} == {uq.SCHEMA}
    assert sorted(r["record_id"] for r in regs) == sorted(
        r["question_id"] for r in j.research_unreadable_questions())
    reg_ids = {r["record_id"] for r in regs}
    assert not reg_ids & {i for v in ids.values() for i in v}
    for m in (up, ue, ur, ru):
        assert not _regs(j, m.SCHEMA)


# ── 11-12 decay receipts and recall unchanged ───────────────────────────
@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, CF, EF, D]])
def test_decay_receipts_and_recall_unchanged(tmp_path, verdicts):
    a = _decay_journal(tmp_path, verdicts, name="a.db")
    b = _copy(tmp_path, a, "b.db")
    _filed(tmp_path, b)                  # unreadable filing first on b
    assert _regs(b, uq.SCHEMA) and _regs(b, ub.SCHEMA)
    ra, rb_ = rb.record_from_journal(a, now_ms=11), \
        rb.record_from_journal(b, now_ms=11)
    assert ra["inserted"] and ra == rb_
    decay = ("SELECT * FROM research_registrations WHERE record_type "
             "NOT IN (?,?) ORDER BY rowid")
    assert a.query(decay, (uq.SCHEMA, ub.SCHEMA)) == \
        b.query(decay, (uq.SCHEMA, ub.SCHEMA)) == _regs(a)
    for q in a.research_questions():
        oa = rc.recall(a, q["question_id"], max_objects=200)
        ob = rc.recall(b, q["question_id"], max_objects=200)
        assert json.dumps(oa, sort_keys=True) == json.dumps(ob, sort_keys=True)


def test_recall_ignores_unreadable_receipts_sharing_decay_ids(tmp_path):
    """Worst case: unreadable-typed receipts under the very IDs recall
    reads. Recall is record-type scoped and does not change."""
    a = _decay_journal(tmp_path, [W, CF, D], name="a.db")
    rb.record_from_journal(a, now_ms=11)
    b = _copy(tmp_path, a, "b.db")
    ids = [q["question_id"] for q in b.research_questions()] + \
        [o["bank_object_id"] for o in b.research_bank_objects()]
    with b._tx() as c:
        for i in ids:
            for t in (uq.SCHEMA, ub.SCHEMA):
                assert b._register(c, t, i, "{}", 0)
    for q in a.research_questions():
        assert json.dumps(rc.recall(a, q["question_id"]), sort_keys=True) \
            == json.dumps(rc.recall(b, q["question_id"]), sort_keys=True)
    scope = a.research_bank_objects()[0]
    args = (rb.SCHEMA, scope["scope_kind"], scope["scope_id"])
    assert a.research_bank_registrations(*args) == \
        b.research_bank_registrations(*args)


def test_decay_writers_are_unchanged():
    src = (ROOT / "trader/core/journal.py").read_text()
    base = subprocess.run(["git", "show", f"{BASE}:trader/core/journal.py"],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout
    for name in ("_register", "registration_envelope", "research_registration",
                 "record_research_question", "record_research_bank_object",
                 "research_bank_registrations", "record_research_plan",
                 "record_research_unreadable_plan",
                 "record_research_unreadable_evidence",
                 "record_research_unreadable_result",
                 "record_research_unreadable_run"):
        body = lambda s: re.split(r"\n    (?:def |class |# --)",
                                  s.split(f"def {name}(", 1)[1], 1)[0]
        assert body(src) == body(base), name


# ── 13 unreadable object bytes / IDs / hashes unchanged ─────────────────
def test_object_bytes_ids_and_hashes_match_a_receipt_less_filing(tmp_path):
    new, old, rows = _pre_bank(tmp_path, "o")
    for row in rows:
        _raw_insert(old, BT, row, 9)     # receipt-less filing of the same
    ub.load(old)
    # stored questions are exactly the pure derivation's row projection
    want = sorted((tuple(uq.row_for(q).values())
                   for q in uq.derive(new.strategy_health_rows()).questions))
    cols = ",".join(new._UNREADABLE_QUESTION_COLUMNS)
    assert sorted(tuple(r.values()) for r in new.query(
        f"SELECT {cols} FROM {QT}")) == want
    assert old.query(f"SELECT {cols} FROM {QT} ORDER BY question_id") == \
        new.query(f"SELECT {cols} FROM {QT} ORDER BY question_id")
    for t, key in ((QT, "question_id"), (BT, "bank_object_id")):
        cols = f"{key}, schema, canonical_sha256, canonical_json"
        assert new.query(f"SELECT {cols} FROM {t} ORDER BY {key}") == \
            old.query(f"SELECT {cols} FROM {t} ORDER BY {key}")
        for r in new.query(f"SELECT * FROM {t}"):
            assert r["canonical_sha256"] == _sha(r["canonical_json"])


def test_question_module_pin_still_catches_code_changes():
    text = (ROOT / uq_pinned.UQ_PATH).read_text()
    assert pinned(uq_pinned.UQ_PATH, text + "X = 1\n") != \
        pinned(uq_pinned.UQ_PATH, text)
    assert pinned(uq_pinned.UQ_PATH, text.replace("SCHEMA = ", "SCHEMA  = ")) \
        != pinned(uq_pinned.UQ_PATH, text)
    other = "trader/cognition/research_plan.py"
    assert pinned(other, "x") == "x"
    assert "Registration: the journal writes a research-registration.v1 " \
        "receipt" in text and "no registration receipt" not in text


def _code(path, rev=None):
    text = (ROOT / path).read_text() if rev is None else subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout
    tree = ast.parse(text)
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and \
            isinstance(body[0].value, ast.Constant):
        body = body[1:]                  # module docstring (contract note)
    return ast.dump(ast.Module(body=body, type_ignores=[]))


@pytest.mark.parametrize("path", [
    "trader/cognition/research_unreadable_question.py",
    "trader/cognition/research_unreadable_bank.py",
    "trader/cognition/research_recall.py",
    "trader/cognition/research_bank.py",
    "trader/cognition/research_question.py"])
def test_contract_code_unchanged(path):
    assert _code(path) == _code(path, BASE)


# ── 14-15 no recall / suppression / no-repeat authority added ───────────
def test_no_unreadable_recall_or_registration_reader_added():
    src = (ROOT / "trader/core/journal.py").read_text()
    base = subprocess.run(["git", "show", f"{BASE}:trader/core/journal.py"],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout
    defs = lambda s: set(re.findall(r"\n    def (\w+)\(", s))
    assert defs(src) - defs(base) == {"_record_registered"}
    assert "class _RegistrationConflict" in src
    # the set of modules touching registrations is exactly the base set
    grep = lambda *rev: sorted(subprocess.run(
        ["git", "grep", "-l", "research_registration", *rev, "--",
         "trader/"], cwd=ROOT, capture_output=True, text=True
    ).stdout.replace(f"{BASE}:", "").split())
    assert grep() == grep(BASE)
    for path in ("trader/cognition/research_unreadable_question.py",
                 "trader/cognition/research_unreadable_bank.py"):
        src_ = (ROOT / path).read_text()
        assert "_register(" not in src_ and \
            "research_registration(" not in src_, path


def test_receipt_carries_no_authority_fields(tmp_path):
    j, _ = _filed(tmp_path)
    for r in _regs(j):
        assert set(json.loads(r["envelope_json"])) == {
            "schema", "record_type", "record_id", "canonical_sha256",
            "recorded_at_ms"}
        assert set(r) == {"record_type", "record_id", "canonical_sha256",
                          "recorded_at_ms", "envelope_sha256",
                          "envelope_json"}
    # a registered question never blocks, cools down or suppresses a new
    # unreadable observation's question
    j2 = _journal(_sub(tmp_path, "n"), {"s1": NO_FRAME})
    uq.record_from_journal(j2, now_ms=1)
    _write(j2, {"s1": NO_FRAME}, 1)
    assert len(uq.record_from_journal(j2, now_ms=2)["inserted"]) == 1
    assert len(_regs(j2, uq.SCHEMA)) == 2
