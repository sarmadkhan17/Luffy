"""Real shadow producer -> read-only source -> external worker, offline only."""
import os
import sqlite3
import sys
import time

import pytest

from tests.test_external_research_router import FakeChild, metadata
from tests.test_strategy_decay_research_plan import _FullHist, W, D
from trader.cognition import research_shadow, research_question as Q
from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import external_research as R
from trader.research.generated_question_source import GeneratedQuestionSource


def produced(tmp_path, verdicts=(W, D)):
    source = Journal(tmp_path / "events.db")
    # Only health events are supplied. The actual subprocess producer generates
    # and registers questions; this test never calls a question registration API.
    for row in _FullHist().seq("s1", verdicts).rows:
        source.log_brain_event(row["kind"], row["subject"], row["detail"])
    assert source.research_questions() == []
    shadow_path = tmp_path / "shadow.db"
    result = research_shadow.invoke(source_db=source.db_path, shadow_db=shadow_path,
        max_sources=8, wall_clock_deadline_s=30, invocation_key="generated-e2e",
        recorded_at_ms=int(time.time()*1000), _python=sys.executable)
    assert result["status"] == "recorded", result
    assert result["receipt"]["outcome"] == "OK", result
    reader = GeneratedQuestionSource(shadow_path, source.db_path)
    worker = Journal(tmp_path / "worker.db")
    return source, reader, worker


def test_real_generated_e2e_identity_readonly_and_restart(tmp_path):
    source, reader, worker = produced(tmp_path)
    before = reader.journal.research_questions()
    events = source.query("SELECT * FROM brain_events")
    child = FakeChild()
    bank = R.research_pass(worker, question_source=reader, child=child)["results"][0]["bank"]
    assert bank["question"] == Q.from_json(before[0]["canonical_json"])
    assert bank["plan"]["question_source"]["canonical_sha256"] == R.sha(before[0]["canonical_json"])
    assert bank["plan"]["question_source"]["recorded_at_ms"] == before[0]["recorded_at_ms"]
    assert bank["plan"]["route"]["source_classes"] == ["academic_factors"]
    assert bank["sources"] and bank["collection_status"] == "COLLECTED"
    assert [c[0] for c in child.calls] == ["search", "retrieve"]
    assert worker.research_questions() == []  # no copy/import or manual registration
    assert reader.journal.research_questions() == before
    assert source.query("SELECT * FROM brain_events") == events
    row = worker.research_bank_object(bank["bank_object_id"])
    assert R.verify_bank(worker, row) == bank
    reader.close()
    reader = GeneratedQuestionSource(tmp_path / "shadow.db", source.db_path)
    assert R.research_pass(Journal(worker.db_path), question_source=reader, child=child)["results"] == []
    assert len(child.calls) == 2
    assert worker.research_bank_objects(schema=R.BANK_SCHEMA) == [row]
    assert all(worker.query("SELECT * FROM " + t) == [] for t in
               ("strategies", "decisions", "trades"))
    assert not worker.query("SELECT name FROM sqlite_master WHERE name='strategy_versions'")
    reader.close()


@pytest.mark.parametrize("corrupt", ["question", "source", "registration", "receipt"])
def test_question_integrity_fail_closed(tmp_path, corrupt):
    source, reader, worker = produced(tmp_path)
    reader.close()
    path = source.db_path if corrupt == "source" else tmp_path / "shadow.db"
    with sqlite3.connect(path) as conn:
        # Deliberate on-disk tampering bypasses immutable-table protection.
        triggers = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        for (name,) in triggers:
            conn.execute('DROP TRIGGER "' + name + '"')
        if corrupt == "question":
            conn.execute("UPDATE research_questions SET canonical_json='{}'")
        elif corrupt == "source":
            conn.execute("UPDATE brain_events SET detail='{}'")
        elif corrupt == "registration":
            conn.execute("UPDATE research_registrations SET envelope_sha256=? WHERE record_type=?", ("0"*64, Q.SCHEMA))
        else:
            conn.execute("UPDATE research_shadow_invocations SET canonical_json='{}'")
    reader = GeneratedQuestionSource(tmp_path / "shadow.db", source.db_path)
    child = FakeChild()
    result = R.research_pass(worker, question_source=reader, child=child)
    assert result["results"][0]["status"] == "REFUSED", result
    assert not child.calls and not worker.research_bank_objects(schema=R.BANK_SCHEMA)
    reader.close()


@pytest.mark.parametrize("negative", ["EMPTY", "UNAVAILABLE"])
def test_independent_question_reconsiders_and_history_retained(tmp_path, negative):
    _, reader, worker = produced(tmp_path, (W, D, W, D))
    failing = FakeChild([]) if negative == "EMPTY" else FakeChild(failure=ChildResult(ok=False, error="offline"))
    a = R.research_pass(worker, question_source=reader, child=failing)["results"][0]["bank"]
    a_row = worker.research_bank_object(a["bank_object_id"])
    attempts = worker.query("SELECT * FROM brain_events WHERE kind=?", (R.DONE,))
    succeeding = FakeChild()
    b = R.research_pass(worker, question_source=reader, child=succeeding)["results"][0]["bank"]
    assert a["collection_status"] == negative
    assert b["collection_status"] == "COLLECTED"
    assert a["question"]["question_id"] != b["question"]["question_id"]
    assert [c[0] for c in succeeding.calls] == ["search", "retrieve"]
    assert a["searches"][0]["request_id"] != b["searches"][0]["request_id"]
    identity = a["searches"][0]["attempt_identity"]
    assert identity["question_id"] == a["question"]["question_id"]
    assert identity["source_class"] and identity["retriever"] and identity["registry_schema"]
    assert identity["registry"] == R.S.REGISTRY_SHA256 and identity["normalized_query"]
    assert a["searches"][0]["attempted_at_ms"] > 0
    assert a["searches"][0]["source_available_ms"] is None
    assert worker.research_bank_object(a["bank_object_id"]) == a_row
    assert worker.query("SELECT * FROM brain_events WHERE kind=? ORDER BY id", (R.DONE,))[:1] == attempts
    assert R.verify_bank(worker, a_row) == a
    assert R.verify_bank(worker, worker.research_bank_object(b["bank_object_id"])) == b
    assert R.RECONSIDERATION_POLICIES == {}
    reader.close()


def test_test_registered_policy_defers_without_overwriting_history(tmp_path):
    _, reader, worker = produced(tmp_path, (W, D, W, D))
    a = R.research_pass(worker, question_source=reader, child=FakeChild([]))["results"][0]["bank"]
    banks = worker.research_bank_objects(schema=R.BANK_SCHEMA)
    attempts = worker.query("SELECT * FROM brain_events")
    seen = []
    def defer(q, history, now):
        seen.append((q, history, now))
        return R.RetryEligibility(False, "test_owner_budget_not_authorized")
    policy = R.ReconsiderationPolicy("test.owner", "test.v1", defer)
    child = FakeChild()
    result = R.research_pass(worker, question_source=reader, child=child,
                            policy_id=policy.policy_id, policies={policy.policy_id: policy})
    assert result["results"][0]["status"] == "POLICY_BLOCKED" and not child.calls
    assert seen[0][1] == (a,) and seen[0][2] > 0
    assert worker.research_bank_objects(schema=R.BANK_SCHEMA) == banks
    assert worker.query("SELECT * FROM brain_events") == attempts
    # Absence of policy does not inherit a previous scheduling decision.
    assert R.research_pass(worker, question_source=reader, child=child)["results"][0]["bank"]["collection_status"] == "COLLECTED"
    assert not R.RECONSIDERATION_POLICIES
    reader.close()


def test_positive_exact_content_dedup_independent_search(tmp_path):
    _, reader, worker = produced(tmp_path, (W, D, W, D))
    abstract = "<p>Trading strategy performance decay.</p>"
    class ExactFixture(FakeChild):
        def __call__(self, fn, operation, arguments, *args, **kwargs):
            result = super().__call__(fn, operation, arguments, *args, **kwargs)
            if operation == "retrieve":
                result.value["metadata"]["abstract"] = abstract
            return result
    first = ExactFixture([metadata(abstract=abstract)])
    a = R.research_pass(worker, question_source=reader, child=first)["results"][0]["bank"]
    second = ExactFixture([metadata(abstract=abstract)])
    b = R.research_pass(worker, question_source=reader, child=second)["results"][0]["bank"]
    assert [c[0] for c in second.calls] == ["search"]
    assert b["evidence"][0]["cache"] is True
    assert b["evidence"][0]["request_id"] == a["evidence"][0]["request_id"]
    assert R.verify_bank(worker, worker.research_bank_object(b["bank_object_id"])) == b
    reader.close()


def test_generated_question_interrupted_worker_resume(tmp_path):
    source, reader, worker = produced(tmp_path)
    child = FakeChild()
    def interrupted(fn, operation, *args, **kwargs):
        if operation == "retrieve":
            raise KeyboardInterrupt()
        return child(fn, operation, *args, **kwargs)
    with pytest.raises(KeyboardInterrupt):
        R.research_pass(worker, question_source=reader, child=interrupted)
    reader.close()
    reader = GeneratedQuestionSource(tmp_path / "shadow.db", source.db_path)
    restart_child = FakeChild()
    bank = R.research_pass(Journal(worker.db_path), question_source=reader, child=restart_child)["results"][0]["bank"]
    assert not restart_child.calls  # completed search and uncertain retrieval are not repeated
    assert bank["evidence"][0]["reason"] == "INTERRUPTED_REQUEST_NOT_RETRIED"
    assert len(worker.research_bank_objects(schema=R.BANK_SCHEMA)) == 1
    assert R.verify_bank(worker, worker.research_bank_object(bank["bank_object_id"])) == bank
    assert R.research_pass(worker, question_source=reader, child=restart_child)["results"] == []
    reader.close()


def test_cli_refuses_question_store_alias_before_worker_initialization(tmp_path, monkeypatch):
    source, reader, _ = produced(tmp_path)
    reader.close()
    alias = tmp_path / "source-alias.db"
    os.link(source.db_path, alias)
    monkeypatch.setattr(R, "Journal", lambda *_: pytest.fail("worker Journal initialized on source"))
    with pytest.raises(ValueError, match="worker_journal_is_question_source"):
        R.main(["--enable", "--once", "--journal", str(alias),
                "--question-shadow-db", str(tmp_path / "shadow.db"),
                "--question-source-db", str(source.db_path)])


def test_legacy_negative_checkpoint_remains_history_not_authority(tmp_path):
    _, reader, worker = produced(tmp_path)
    src = R.S.select("academic_factors")[0]
    q, _ = reader.read(reader.candidates(1, set())[0]["question_id"])
    query = R.S.route(q)["queries"][0]
    identity = dict(source_id=src.source_id, registry=R.S.REGISTRY_SHA256,
                    operation="search", arguments=dict(query=query, limit=R.Bounds().max_metadata),
                    response_byte_bound=R.Bounds().max_response_bytes)
    key = R.sha(R.canonical(identity))
    R._write(worker, R.START, key, dict(identity=identity, started_ms=1))
    R._write(worker, R.DONE, key, dict(identity=identity, result=dict(
        status="EMPTY", reason=None, metadata=[], bytes=0, content_sha256="a"*64,
        retrieved_at_ms=1, available_ms=1)))
    old = worker.query("SELECT * FROM brain_events WHERE subject=?", (key,))
    child = FakeChild()
    bank = R.research_pass(worker, question_source=reader, child=child)["results"][0]["bank"]
    assert bank["collection_status"] == "COLLECTED" and len(child.calls) == 2
    assert worker.query("SELECT * FROM brain_events WHERE subject=?", (key,)) == old
    assert R._checkpoint(worker, R.DONE, key)["result"]["status"] == "EMPTY"
    reader.close()
