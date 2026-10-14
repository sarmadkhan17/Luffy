"""RP-04: the strategy-decay and strategy-health-unreadable research chains are
retired. Surviving consumers refuse their historical kinds clearly, legacy
journal files still open, and no writer or reader of the retired tables remains."""
import importlib
import sqlite3

import pytest

from trader.core.journal import Journal
from trader.learning import capture as C
from trader.owner import queries as Q

RETIRED_MODULES = (
    "research_question", "research_plan", "research_evidence", "research_result",
    "research_run", "research_bank", "research_recall", "research_cost",
    "research_families", "research_sources", "research_next_question",
    "research_bank_view", "research_unreadable_question",
    "research_unreadable_plan", "research_unreadable_evidence",
    "research_unreadable_result", "research_unreadable_run",
    "research_unreadable_bank", "research_unreadable_recall",
    "research_shadow", "research_shadow_contract", "research_shadow_store",
    "research_shadow_report", "external_sources", "source_governance")


@pytest.mark.parametrize("name", RETIRED_MODULES)
def test_retired_modules_are_gone(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.cognition." + name)


def test_retired_research_workers_are_gone():
    for name in ("external_research", "generated_question_source"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("trader.research." + name)


@pytest.mark.parametrize("kind", ["strategy_decay", "strategy_health_unreadable", "unknown"])
def test_historical_bank_kind_is_refused_not_substituted(kind):
    body = {"observation": {}, "kind": "RESEARCH_RESULT", "boundary": "REALIZED",
            "registration": {"profile": "RESEARCH", "decision_ms": 0},
            "lineage": {"trade_ids": []}}
    sources = {"outcome": {"observation": {}}, "bank": {"bank_kind": kind},
               "research_run": {"chain": {}}}
    with pytest.raises(ValueError, match="^retired_or_unsupported_research_bank_kind:" + kind + "$"):
        C.validate_chain(body, sources, {})


def test_journal_has_no_retired_chain_writers_or_readers(tmp_path):
    j = Journal(tmp_path / "j.db")
    retired = [n for n in dir(j) if n.startswith(("record_research_", "research_", "_capture_research"))]
    assert retired == []
    assert not hasattr(j, "registration_envelope")


def test_legacy_journal_with_retired_tables_opens_without_migration(tmp_path):
    path = tmp_path / "legacy.db"
    Journal(path)._conn().close()
    db = sqlite3.connect(path)
    db.execute("INSERT INTO research_questions(question_id,schema,question_kind,trigger,"
               "scope_kind,scope_id,source_kind,source_event_id,canonical_json,recorded_at_ms) "
               "VALUES('q','research-question.v1','strategy_decay','T','strategy','s','spec',1,'{}',1)")
    db.commit()
    before = db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
    db.close()
    j = Journal(path)
    assert j.query("SELECT COUNT(*) n FROM research_questions")[0]["n"] == 1
    db = sqlite3.connect(path)
    assert db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall() == before


def test_owner_research_query_reads_only_the_surviving_ledger(tmp_path):
    j = Journal(tmp_path / "j.db")
    out = Q.query(j, "research")
    # nothing in the surviving ledger: truthfully unavailable, never a retired record
    assert out["status"] == "UNAVAILABLE" and out["records"] == []
    assert "questions_page" not in out
