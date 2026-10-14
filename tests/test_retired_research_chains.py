"""RP-04: the strategy-decay and strategy-health-unreadable research chains are
retired. Surviving consumers refuse their historical kinds clearly, legacy
journal files still open, and no writer or reader of the retired tables remains."""
import importlib
import json
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


# ── historical approvals that bound the retired research_bank source ─────
def _legacy_bank_item(j, at=1000):
    from trader.owner import approvals as A
    A.ensure(j)
    body = dict(schema="owner-approval-item.v1", action_type="capability",
                affected_object="legacy-research-bank", reason="r",
                required_owner_action="a",
                bindings=[dict(kind="research_bank", identity="b" * 64, sha256="c" * 64)],
                context={"x": 1}, config_sha256=A.digest({}), created_at_ms=at,
                available_at_ms=at, valid_until_ms=10 ** 13)
    item = dict(body, item_id=A.digest(body))
    with j._tx() as c:
        c.execute("INSERT INTO owner_approval_items VALUES (?,?)",
                  (item["item_id"], A.canonical(item)))
    return item


def test_historical_research_bank_approval_reports_retired_source_without_mutation(tmp_path):
    from trader.owner import approvals as A
    j = Journal(tmp_path / "j.db")
    item = _legacy_bank_item(j)
    before = j.query("SELECT * FROM owner_approval_items")
    state, reasons = A.validity(j, {}, item, 2000)
    assert state == "STALE"            # never VALID, so never approvable
    assert reasons == ["source_retired:research_bank:" + "b" * 64]
    listed = A.items(j, {}, now_ms=2000)["items"]
    assert [(i["item_id"], i["validity"]) for i in listed] == [(item["item_id"], "STALE")]
    assert j.query("SELECT * FROM owner_approval_items") == before      # identity/evidence intact
    assert not A.exists(j, "owner_approval_receipts") or not j.query("SELECT * FROM owner_approval_receipts")


def test_new_binding_to_retired_source_refuses_with_structured_error_and_no_write(tmp_path):
    from trader.owner import approvals as A
    j = Journal(tmp_path / "j.db")
    before = j._conn().total_changes
    for call in (lambda: A.source(j, "research_bank", "x"), lambda: A.binding(j, "research_bank", "x")):
        with pytest.raises(A.RetiredSource, match="^retired_source:research_bank$"):
            call()
    with pytest.raises(ValueError, match="^unregistered_source_kind$"):
        A.source(j, "never_existed", "x")
    assert j._conn().total_changes == before


def test_surviving_approval_sources_are_unaffected(tmp_path):
    from trader.owner import approvals as A
    assert set(A.SOURCES) == {"strategy_version", "validation", "probation", "context"}
    j = Journal(tmp_path / "j.db")
    j.kv_set("owner_proposal_context:ok", json.dumps({"scope": "s"}))
    b = A.binding(j, "context", "owner_proposal_context:ok")
    assert b["kind"] == "context" and len(b["sha256"]) == 64
    assert A.source(j, "strategy_version", "missing") is None   # no table -> None, unchanged
