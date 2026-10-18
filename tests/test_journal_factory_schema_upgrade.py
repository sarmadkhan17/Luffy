"""A journal created before strategy_first_live_deployments opens, and the table starts empty."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.strategy import factory_handoff as fh


def _state(path):
    c = sqlite3.connect(path)
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = c.execute("SELECT count(*) FROM strategy_first_live_deployments").fetchone()[0] \
            if "strategy_first_live_deployments" in tables else None
        triggers = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        return tables, rows, triggers
    finally:
        c.close()


def test_old_database_without_the_table_opens_and_gets_it_empty(tmp_path):
    path = tmp_path / "old.db"
    fh.ensure(Journal(path))
    c = sqlite3.connect(path)
    c.execute("DROP TABLE strategy_first_live_deployments")     # an earlier release's shape
    c.commit(); c.close()
    assert "strategy_first_live_deployments" not in _state(path)[0]

    j = Journal(path)                                            # used to raise OperationalError
    tables, rows, triggers = _state(path)
    assert "strategy_first_live_deployments" in tables and rows == 0
    assert {"strategy_first_live_deployments_no_update", "strategy_first_live_deployments_no_delete",
            "first_live_deployment_insert_authority"} <= triggers
    assert fh._deployment(j, "any-version") is None
    assert fh.live_entry_block(j, "any-strategy") is not None    # no live authority exists


def test_fresh_database_starts_empty_and_grants_nothing(tmp_path):
    path = tmp_path / "fresh.db"
    j = Journal(path)
    fh.ensure(j)
    assert _state(path)[1] == 0
    Journal(path)                                                # reopening is idempotent
    assert _state(path)[1] == 0
    assert fh.live_entry_block(j, "any-strategy") is not None


def test_deployment_rows_still_need_the_owner_authority_function(tmp_path):
    path = tmp_path / "guard.db"
    j = Journal(path)
    fh.ensure(j)
    try:
        with j._tx() as c:
            c.execute("INSERT INTO strategy_first_live_deployments VALUES ('v','{}','x',1)")
    except Exception as exc:
        assert "STRATEGY_APPROVAL_AUTHORITY_REQUIRED" in str(exc)
    else:
        raise AssertionError("an unauthorised deployment row was accepted")
    assert _state(path)[1] == 0
