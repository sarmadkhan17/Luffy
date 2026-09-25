"""SDD-STAGE-3-OPPORTUNITY-CONTEXT-PERSISTENCE-V1.

The registration transaction persists opportunity-context.v1 from evidence it
already holds. Synthetic; no network, LLM or trading.
"""
import json
import sqlite3
import zlib
from unittest.mock import patch

import pytest

from trader.cognition import investigation as I
from trader.cognition import opportunity_context as oc
from trader.observability import attention as A, investigation as C
from trader.observability.store import Store
from tests.test_investigation_state_feedback import ALL, paths, publish  # noqa: F401  (fixture)


def rows(dest):
    with sqlite3.connect(dest) as db:
        return db.execute("SELECT id,investigation_id,symbol,as_of_ms,payload FROM opportunity_contexts "
                          "ORDER BY investigation_id").fetchall()


def evidence(dest, source, iid):
    """The scan and allocation the registration held, read back upstream."""
    with sqlite3.connect(dest) as db:
        case = I.investigation_from_dict(json.loads(
            db.execute("SELECT payload FROM cases WHERE id=?", (iid,)).fetchone()[0]))
        alloc = json.loads(db.execute("SELECT payload FROM allocation_decisions WHERE scan_id=?",
                                      (case.state.scan_id,)).fetchone()[0])
    with sqlite3.connect(source) as db:
        scan = json.loads(db.execute("SELECT payload FROM scans WHERE scan_id=?",
                                     (case.state.scan_id,)).fetchone()[0])
    return scan, alloc


def case_ids(dest):
    with sqlite3.connect(dest) as db:
        return sorted(r[0] for r in db.execute("SELECT id FROM cases"))


@pytest.fixture
def registered(paths):  # noqa: F811
    source, dest, now = paths
    publish(source, now, "s1", ALL)
    detail = C.step(source, dest, now)
    assert detail["registered"] >= 1
    return source, dest, now, detail


def test_each_registration_persists_one_verified_context(registered):
    source, dest, now, detail = registered
    stored = rows(dest)
    assert [r[1] for r in stored] == case_ids(dest)
    assert detail["opportunity_context"] == {"written": detail["registered"], "duplicate": 0, "refused": {}}
    for cid, iid, symbol, as_of, payload in stored:
        ctx = oc.OpportunityContext.from_json(payload)      # canonical, immutable, re-verified
        assert ctx.context_id == cid and cid.startswith("opportunity-context.v1:")
        d = ctx.to_dict()
        assert d["as_of_ms"] == as_of == now and d["instrument"]["symbol_key"] == symbol
        assert d["investigation"]["investigation_id"] == iid
        assert d["attention"]["allocation"]["candidate_allocated"] is True
        assert d["attention"]["payload_binding"]["bound_by"] == "allocation_decision"
        # Evidence the registration does not hold stays UNKNOWN, never inferred.
        for section in ("registry_selection", "world_model", "signal_occurrences", "instrument"):
            assert d[section]["status"] == "UNKNOWN", section
        assert C.context_for(dest, iid) == ctx


def test_replay_reproduces_the_persisted_context_id(registered):
    source, dest, _now, _ = registered
    for cid, iid, *_ in rows(dest):
        assert C.replay_context(dest, iid, source).context_id == cid


def test_retry_and_restart_are_idempotent(registered):
    source, dest, now, _ = registered
    before = rows(dest)
    # Retry of the same scan: allocation execution is closed, nothing re-registered.
    again = C.step(source, dest, now + 1000)
    assert again["registered"] == 0 and rows(dest) == before
    # Re-persisting the identical context is a no-op.
    with C.ledger(dest) as db:
        for cid, iid, *_ in before:
            assert C.persist_context(db, iid, C.context_for(dest, iid), *evidence(dest, source, iid)) is False
    assert rows(dest) == before


def test_conflicting_duplicate_fails_closed(registered):
    source, dest, _now, _ = registered
    (cid, iid, *_), (other_cid, other_iid, *_) = rows(dest)[:2]
    other = C.context_for(dest, other_iid)
    scan, alloc = evidence(dest, source, iid)
    with C.ledger(dest) as db:
        # Another context for an already-contextualised case.
        with pytest.raises(C.ContextConflict):
            C.persist_context(db, iid, other, scan, alloc)
        # Same context ID with different bytes.
        forged = oc.OpportunityContext(cid, C.context_for(dest, iid).canonical_json + " ")
        with pytest.raises(C.ContextConflict):
            C.persist_context(db, "someone_else", forged, scan, alloc)
        # Retained evidence under an existing hash must be byte-identical.
        sha = db.execute("SELECT scan_sha256 FROM opportunity_contexts WHERE id=?", (cid,)).fetchone()[0]
        db.execute("UPDATE context_evidence SET payload=? WHERE sha256=?", (zlib.compress(b"{}"), sha))
        with pytest.raises(C.ContextConflict):
            C.persist_context(db, iid, C.context_for(dest, iid), scan, alloc)
    assert C.context_for(dest, iid).context_id == cid


def test_context_failure_does_not_change_registration(paths):  # noqa: F811
    source, dest, now = paths
    publish(source, now, "s1", ALL)
    control = paths[1].with_name("control.db")
    with patch.object(C, "_record_context", lambda *a: None):
        base = C.step(source, control, now)

    def refuse(*_a, **_k):
        raise oc.OpportunityContextRefused(oc.FUTURE_EVIDENCE, "forced")
    with patch.object(oc, "build", refuse):
        detail = C.step(source, dest, now)
    assert detail["registered"] == base["registered"] and detail["status"] == base["status"]
    assert detail["opportunity_context"] == {"written": 0, "duplicate": 0,
                                             "refused": {"future_evidence": detail["registered"]}}
    assert case_ids(dest) == case_ids(control) and rows(dest) == []
    with sqlite3.connect(dest) as a, sqlite3.connect(control) as b:
        for table in ("cases", "updates", "allocation_decisions"):
            q = f"SELECT * FROM {table} ORDER BY 1"
            assert a.execute(q).fetchall() == b.execute(q).fetchall(), table
        q = "SELECT case_id,stage FROM resource_receipts ORDER BY 1,2"
        assert a.execute(q).fetchall() == b.execute(q).fetchall()


def test_write_error_rolls_back_only_the_context(paths):  # noqa: F811
    source, dest, now = paths
    publish(source, now, "s1", ALL)

    def broken(db, *_a):
        db.execute("INSERT INTO opportunity_contexts VALUES ('x','y','z',0,'a','b','{}')")
        raise sqlite3.OperationalError("disk")
    with patch.object(C, "persist_context", broken):
        detail = C.step(source, dest, now)
    assert detail["registered"] >= 1 and rows(dest) == []
    assert detail["opportunity_context"]["refused"] == {
        "opportunity_context_write_failed": detail["registered"]}


def test_legacy_cases_without_context_remain_supported(registered):
    source, dest, now, _ = registered
    with C.ledger(dest) as db:
        db.execute("DELETE FROM opportunity_contexts")
    with sqlite3.connect(dest) as db:
        iid = db.execute("SELECT id FROM cases LIMIT 1").fetchone()[0]
    assert C.context_for(dest, iid) is None
    # A pre-table ledger reads as legacy too.
    legacy = dest.with_name("legacy.db")
    sqlite3.connect(legacy).close()
    assert C.context_for(legacy, iid) is None
    # Legacy cases keep advancing; dossiers are unchanged.
    assert C.step(source, dest, now + 1000)["status"] in ("ok", "degraded")
    assert all("investigation" in d for d in C.dossiers(dest))


def test_replay_refuses_missing_evidence(registered):
    source, dest, _now, _ = registered
    iid = rows(dest)[0][1]
    with C.ledger(dest) as db:
        db.execute("DELETE FROM context_evidence")
    with pytest.raises(C.LedgerRefused, match="replay_evidence_missing"):
        C.replay_context(dest, iid, source)


def test_retention_removes_contexts_with_their_case(registered):
    source, dest, now, _ = registered
    iid = rows(dest)[0][1]
    with C.ledger(dest) as db:
        db.execute("DELETE FROM cases WHERE id=?", (iid,))
    C.step(source, dest, now + 1000)
    assert iid not in [r[1] for r in rows(dest)]


def tables(dest, names=("cases", "updates", "allocation_decisions", "inputs", "case_inputs")):
    with sqlite3.connect(dest) as db:
        return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in names}


def test_real_capacity_exhaustion_retries_registration_without_contexts(paths):  # noqa: F811
    """SQLITE_FULL at the page ceiling discards the whole transaction; the pass
    is redone without contexts and registration matches a context-free run."""
    source, dest, now = paths
    publish(source, now, "s1", ALL)
    control = dest.with_name("control.db")
    with patch.object(C, "_record_context", lambda *a: None):
        base = C.step(source, control, now)
    real, lost = C.persist_context, []

    def at_ceiling(db, *a):
        db.execute(f"PRAGMA max_page_count={db.execute('PRAGMA page_count').fetchone()[0]}")
        try:
            return real(db, *a)
        except sqlite3.OperationalError:
            lost.append(db.in_transaction)
            raise
    with patch.object(C, "persist_context", at_ceiling):
        detail = C.step(source, dest, now)
    assert lost == [False]                     # a real whole-transaction loss occurred
    assert detail["registered"] == base["registered"] >= 1 and detail["status"] == base["status"]
    assert detail["opportunity_context"] == {"written": 0, "duplicate": 0, "refused": {},
                                             "disabled": "opportunity_context_transaction_lost"}
    assert tables(dest) == tables(control) and rows(dest) == []
    with sqlite3.connect(dest) as db:          # attempt-local bookkeeping restarted once
        assert db.execute("SELECT COUNT(DISTINCT execution_id) FROM resource_receipts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0] == 1


def _tamper(dest, sql, iid):
    with C.ledger(dest) as db:
        db.execute(sql, (iid,))


@pytest.mark.parametrize("sql,reason", [
    # Only the persisted case's question: the rebuilt context is internally
    # valid, but its case payload hash differs from the saved one.
    ("UPDATE cases SET payload=json_set(payload,'$.question',json_extract(payload,'$.question')||'?') "
     "WHERE id=?", "replay_mismatch"),
    # The initial update no longer replays from its case and evidence.
    ("UPDATE updates SET payload=json_set(payload,'$.observed_ms',json_extract(payload,'$.observed_ms')+1) "
     "WHERE case_id=?", "replay_evidence_refused"),
])
def test_replay_refuses_altered_case_or_update(registered, sql, reason):
    source, dest, _now, _ = registered
    iid = rows(dest)[0][1]
    _tamper(dest, sql, iid)
    with pytest.raises(C.LedgerRefused, match=reason):
        C.replay_context(dest, iid, source)


def test_replay_refuses_altered_saved_context_or_upstream_copy(registered):
    source, dest, _now, _ = registered
    cid, iid, *_ = rows(dest)[0]
    with C.ledger(dest) as db:
        db.execute("UPDATE allocation_decisions SET payload=json_set(payload,'$.mode','x')")
    with pytest.raises(C.LedgerRefused, match="replay_evidence_conflict"):
        C.replay_context(dest, iid, source)
    with C.ledger(dest) as db:
        db.execute("DELETE FROM allocation_decisions")
        db.execute("UPDATE opportunity_contexts SET payload=replace(payload,'\"as_of_ms\":','\"as_of_ms\": ') "
                   "WHERE id=?", (cid,))
    with pytest.raises(C.LedgerRefused, match="replay_context_corrupt"):
        C.replay_context(dest, iid, source)


def test_question_is_bound_by_the_case_payload_hash(registered):
    """The tamper above changes only content the context references by hash."""
    assert "question" not in json.dumps(C.context_for(registered[1], rows(registered[1])[0][1]).to_dict())


def test_replay_survives_allocation_and_attention_pruning(registered):
    source, dest, now, _ = registered
    before = rows(dest)
    # Allocation retention: decisions older than RETENTION_MS are deleted while
    # the (now terminal, not yet expired) cases and their contexts remain.
    later = now + C.RETENTION_MS + I.TF
    C.step(source, dest, later)
    with sqlite3.connect(dest) as db:
        assert db.execute("SELECT COUNT(*) FROM allocation_decisions").fetchone()[0] == 0
    assert rows(dest) == before
    # Attention store pruning by age (its own independent schedule).
    store = Store(source, A.settings())
    with store.db:
        store._prune(later)
    store.close()
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    for cid, iid, *_ in before:
        assert C.replay_context(dest, iid, source).context_id == cid
        assert C.replay_context(dest, iid).context_id == cid


def test_retained_evidence_is_shared_and_removed_with_its_last_context(registered):
    source, dest, now, _ = registered
    with sqlite3.connect(dest) as db:
        # One scan and one allocation shared by every registration of the scan.
        assert db.execute("SELECT COUNT(*) FROM context_evidence").fetchone()[0] == 2
    with C.ledger(dest) as db:
        db.execute("DELETE FROM cases")
    C.step(source, dest, now + 1000)
    with sqlite3.connect(dest) as db:
        assert db.execute("SELECT COUNT(*) FROM context_evidence").fetchone()[0] == 0


@pytest.mark.parametrize("blob", [b"not zlib", zlib.compress(b"\xff\xfe invalid utf-8"), "text, not bytes"])
def test_unreadable_retained_evidence_is_telemetry_not_a_registration_failure(paths, blob):  # noqa: F811
    source, dest, now = paths
    publish(source, now, "s1", ALL)
    control = dest.with_name("control.db")
    with patch.object(C, "_record_context", lambda *a: None):
        base = C.step(source, control, now)
    real = C.persist_context

    def corrupted_first(db, iid, ctx, scan, allocation):
        # An existing, unreadable copy under the scan's own hash.
        db.execute("INSERT OR REPLACE INTO context_evidence VALUES (?,?)",
                   (ctx.to_dict()["attention"]["scan_sha256"], blob))
        return real(db, iid, ctx, scan, allocation)
    with patch.object(C, "persist_context", corrupted_first):
        detail = C.step(source, dest, now)
    assert detail["registered"] == base["registered"] >= 1 and detail["status"] == base["status"]
    assert detail["opportunity_context"] == {
        "written": 0, "duplicate": 0,
        "refused": {"opportunity_context_evidence_conflict": detail["registered"]}}
    assert tables(dest) == tables(control) and rows(dest) == []
    with sqlite3.connect(dest) as db:      # the savepoint also discarded the corrupt row
        assert db.execute("SELECT COUNT(*) FROM context_evidence").fetchone()[0] == 0
