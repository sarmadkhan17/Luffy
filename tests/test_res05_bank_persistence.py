"""RES-05 persistence negatives; synthetic local transports only."""
import copy
import socket
import sqlite3

import pytest

from tests.test_external_research_router import FakeChild, registered
from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import external_research as R


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("unexpected provider/network call")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    from trader.brain.llm import BrainLLM
    for method in ("chat", "chat_tools", "chat_json"):
        monkeypatch.setattr(BrainLLM, method, refuse)


def rewrite_fixture(j, original, forged):
    forged["result_id"] = R.sha(R.canonical(dict(plan_id=forged["plan_id"],
        evidence_id=forged["evidence_id"], status=forged["collection_status"])))
    forged.pop("bank_object_id")
    forged["bank_object_id"] = R.sha(R.canonical(forged))
    # Deliberately bypass immutability in this isolated fixture. A valid hash
    # and registration must not substitute for semantic verification.
    with sqlite3.connect(j.db_path) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute('DROP TRIGGER "' + name + '"')
        db.execute("DELETE FROM research_bank_objects WHERE bank_object_id=?", (original["bank_object_id"],))
    assert j.record_research_bank_object(R._row(forged), recorded_at_ms=1) == "inserted"
    return j.research_bank_object(forged["bank_object_id"])


@pytest.mark.parametrize("field,value", [
    ("extracted_claims", {"status": "EXTRACTED", "claims": ["invented edge"]}),
    ("supporting_evidence", {"status": "SUPPORTED", "source": "missing"}),
    ("contradictory_evidence", {"status": "REFUTED", "source": "missing"}),
    ("experiments", [{"status": "PASSED", "experiment_id": "invented"}]),
    ("next_questions", []),
    ("collection_status", "EMPTY"),
    ("cost", dict(scope="current_pass", network_attempts=0, received_bytes=0,
                  paid_cost_usd=0, llm_calls=0, total_operating_cost="NOT_MEASURED")),
])
def test_rehashed_registered_result_cannot_invent_or_drop_truth(tmp_path, field, value):
    j = registered(tmp_path)
    child = FakeChild(failure=ChildResult(ok=False, error="source unavailable")) if field in (
        "extracted_claims", "supporting_evidence", "contradictory_evidence") else FakeChild()
    bank = R.research_pass(j, child=child)["results"][0]["bank"]
    forged = copy.deepcopy(bank)
    forged[field] = value
    row = rewrite_fixture(j, bank, forged)
    with pytest.raises(ValueError, match="external_bank_(result_semantics|outcome_cost_binding)"):
        R.verify_bank(j, row)


@pytest.mark.parametrize("mode", ["collected", "empty", "unavailable"])
def test_normal_persistence_restart_and_duplicate_preserve_exact_record(tmp_path, mode):
    j = registered(tmp_path)
    child = (FakeChild([]) if mode == "empty" else FakeChild(
        failure=ChildResult(ok=False, error="missing source")) if mode == "unavailable" else FakeChild())
    bank = R.research_pass(j, child=child)["results"][0]["bank"]
    assert bank["question"]["question"] and bank["question"]["question_id"]
    assert bank["sources"] and bank["plan_id"] and bank["evidence_id"] and bank["result_id"]
    assert bank["result_status"] == "INCONCLUSIVE"
    assert all(bank[k] == v for k, v in R.result_fields().items())
    assert bank["limitations"] and bank["cost"]["total_operating_cost"] == "NOT_MEASURED"
    row = j.research_bank_object(bank["bank_object_id"])
    calls = len(child.calls)
    restarted = Journal(j.db_path)
    assert R.research_pass(restarted, child=child)["results"] == []
    duplicate = R._collect(restarted, bank["question"], R.Bounds(), child)
    assert duplicate == dict(status="DUPLICATE", bank=bank)
    assert len(child.calls) == calls
    assert restarted.research_bank_object(bank["bank_object_id"]) == row
    assert R.verify_bank(restarted, row) == bank
    conflict = dict(R._row(bank), canonical_json="{}")
    assert restarted.record_research_bank_object(conflict, recorded_at_ms=2) == "conflict"
    assert restarted.research_bank_object(bank["bank_object_id"]) == row


# Terminal inventory: preserve real refuted alternatives and bind follow-ups
# through the same original question and result identities.
from tests.test_investigation_state_feedback import paths  # noqa: E402,F401
from tests.test_investigation_research_family import measured, registered as investigation_registered  # noqa: E402
from trader.observability import investigation_research as IR  # noqa: E402


@pytest.mark.parametrize("completed", [False, True])
def test_investigation_terminal_record_has_linked_next_questions(paths, completed):
    _, dest, now = paths
    iid, cut = measured(paths, "same_direction") if completed else (investigation_registered(paths)[0], now)
    IR.run(dest, iid, recorded_at_ms=cut + 2)
    chain = IR.chain(dest, iid)
    last = chain["runs"][-1]
    bank = last["bank"]
    assert bank["links"]["question"]["id"] == chain["question"]["question_id"]
    assert bank["links"]["result"]["id"] == last["result"]["result_id"]
    assert bank["result_status"] == ("SUPPORTED" if completed else "INCONCLUSIVE")
    if completed:
        assert len(bank["contradictory_evidence"]["items"]) == 2
        assert all(i["status"] == "REFUTED" for i in bank["contradictory_evidence"]["items"])
    assert bank["next_questions"]["status"] == "AVAILABLE"
    assert last["next_questions"]
    assert all(q["lineage"]["result_id"] == last["result"]["result_id"]
               for q in last["next_questions"])
    assert bank["cost"]["status"] == "NOT_MEASURED"
