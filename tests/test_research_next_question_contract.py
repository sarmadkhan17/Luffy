"""Offline durable gap filing: compatibility, fail-closed reads and owner consumers."""
import sqlite3

import pytest

from tests.test_strategy_decay_research_bank import _filed
from trader.cognition import research_bank as rb
from trader.cognition import research_bank_view as view
from trader.cognition import research_next_question as nq
from trader.core.journal import Journal
from trader.owner.queries import query


def _source(tmp_path):
    journal, filed = _filed(tmp_path)
    return journal, filed["inserted"][0]


def _history(journal):
    tables = ("research_questions", "research_plans", "research_evidence",
              "research_results", "research_runs", "research_bank_objects",
              "research_registrations", "state_kv", "strategies", "trades")
    return {t: journal.query(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}


def test_gap_filing_retries_reopens_and_preserves_historical_authority(tmp_path):
    journal, bid = _source(tmp_path)
    journal.kv_set("control_state", "FROZEN")
    journal.kv_set("macro_guard_operator_hold", "1")
    journal.kv_set("research_authority", "DENIED")
    before = _history(journal)
    first = nq.record_for_bank_object(journal, bid, 3)
    assert first["inserted"] and not first["refusals"]
    records = nq.load(journal, bid)
    assert {r["question_kind"] for r in records} == set(nq.QUESTION_KINDS)
    assert all(r["authority"] == "context_only" for r in records)
    reopened = Journal(journal.db_path)
    again = nq.record_for_bank_object(reopened, bid, 999)
    assert again["duplicate"] == first["inserted"]
    assert not again["inserted"] and not again["conflict"]
    assert nq.load(reopened, bid) == records
    assert all(r["recorded_at_ms"] == 3 for r in reopened.research_next_questions(bid))
    assert _history(reopened) == before
    assert reopened.kv_get("control_state") == "FROZEN"
    assert reopened.kv_get("macro_guard_operator_hold") == "1"


@pytest.mark.parametrize("kind", ["research", "capability", "cost"])
def test_filed_gaps_reach_bank_view_and_typed_owner_consumers(tmp_path, kind):
    journal, bid = _source(tmp_path)
    legacy = rb.load(journal)[0]
    assert view.build(journal, bank_object_id=bid, max_prior_objects=5)[
        "linked_next_questions"]["stored_count"] == 0
    nq.record_for_bank_object(journal, bid, 3)
    built = view.build(journal, bank_object_id=bid, max_prior_objects=5)
    assert built["bank_object"] == legacy
    assert built["linked_next_questions"]["completeness"] == "NOT_ASSERTED"
    expected = nq.load(journal, bid)
    assert [r["record"] for r in built["linked_next_questions"]["records"]] == expected
    out = query(journal, kind, bid, root=tmp_path)
    [research] = [r for r in out["records"] if r["kind"] == "research"]
    assert research["verification"] == "VERIFIED"
    assert research["value"] == built


def test_tampered_upstream_chain_refuses_filing_loading_and_owner_view(tmp_path):
    journal, bid = _source(tmp_path)
    nq.record_for_bank_object(journal, bid, 3)
    before = journal.research_next_questions(bid)
    with journal._tx() as conn:
        conn.execute("UPDATE research_results SET canonical_sha256=?", ("0" * 64,))
    refusal = nq.record_for_bank_object(journal, bid, 4)
    assert refusal["refusals"] and not refusal["inserted"]
    assert journal.research_next_questions(bid) == before
    with pytest.raises(nq.ResearchNextQuestionError, match="source_invalid:bank"):
        nq.load(journal, bid)
    with pytest.raises(view.ResearchBankViewError):
        query(journal, "research", bid, root=tmp_path)


def test_projection_conflict_never_overwrites_and_reader_fails_closed(tmp_path):
    journal, bid = _source(tmp_path)
    nq.record_for_bank_object(journal, bid, 3)
    with journal._tx() as conn:
        conn.execute("UPDATE research_next_questions SET scope_id='tampered'")
    before = journal.research_next_questions(bid)
    retry = nq.record_for_bank_object(journal, bid, 4)
    assert len(retry["conflict"]) == len(before) and not retry["inserted"]
    assert journal.research_next_questions(bid) == before
    with pytest.raises(nq.ResearchNextQuestionError, match="row_projection"):
        nq.load(journal, bid)


def test_historical_missing_table_read_does_not_create_or_backfill(tmp_path):
    journal, bid = _source(tmp_path)
    before = _history(journal)
    with journal._tx() as conn:
        conn.execute("DROP TABLE research_next_questions")
    readonly = view.open_readonly(journal.db_path)
    try:
        with pytest.raises(view.ResearchBankViewError, match="no such table"):
            view.build(readonly, bank_object_id=bid, max_prior_objects=5)
        assert readonly.query("SELECT name FROM sqlite_master WHERE name=?",
                              ("research_next_questions",)) == []
        with pytest.raises(sqlite3.OperationalError):
            readonly.kv_set("control_state", "ACTIVE")
        assert _history(readonly) == before
    finally:
        readonly._conn().close()


def test_readonly_bank_view_resolves_logical_evidence_without_writes(tmp_path):
    journal, bid = _source(tmp_path)
    nq.record_for_bank_object(journal, bid, 3)
    before = _history(journal)
    expected = view.build(journal, bank_object_id=bid, max_prior_objects=5)
    schema = journal.query("SELECT name,sql FROM sqlite_master ORDER BY name")
    readonly = view.open_readonly(journal.db_path)
    try:
        assert readonly.query("SELECT name FROM sqlite_master WHERE name=?",
                              ("brain_events_logical_v1",))
        assert readonly.query("SELECT * FROM brain_events ORDER BY id") == \
            journal.query("SELECT * FROM brain_events ORDER BY id")
        assert view.build(readonly, bank_object_id=bid, max_prior_objects=5) == expected
        assert nq.load(readonly, bid) == nq.load(journal, bid)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            readonly._conn().execute("DELETE FROM research_next_questions")
        readonly._conn().rollback()
        assert _history(readonly) == before
        assert readonly.query("SELECT name,sql FROM sqlite_master ORDER BY name") == schema
        with journal._tx() as conn:
            conn.execute("UPDATE research_evidence SET canonical_sha256=?", ("0" * 64,))
        with pytest.raises(view.ResearchBankViewError):
            view.build(readonly, bank_object_id=bid, max_prior_objects=5)
    finally:
        readonly._conn().close()
