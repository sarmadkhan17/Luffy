import ast
from pathlib import Path
import sqlite3

import pytest
from scripts.expected_net_economics_inventory import inspect, TABLES


def test_read_only_inventory_no_candidates_or_mutation(tmp_path):
    path = tmp_path / 'journal.db'
    with sqlite3.connect(path) as db:
        for table in TABLES:
            db.execute('CREATE TABLE ' + table + '(id TEXT)')
        db.execute('CREATE TABLE state_kv(key TEXT,value TEXT)')
        db.execute("INSERT INTO state_kv VALUES('control_state','FROZEN')")
    before = path.read_bytes()
    r = inspect(path, 1500)
    assert r['real_economic_value'] == 'UNAVAILABLE'
    assert r['economic_model_status'] == 'INSUFFICIENT_EVIDENCE'
    assert r['current_opportunity_count'] == 0
    assert r['production_mutations'] == 0
    assert r['control_state'] == 'FROZEN'
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO strategy_versions VALUES('real-version')")
    before = path.read_bytes()
    r = inspect(path, 1500)
    assert r['current_opportunity_count'] == 'UNAVAILABLE'
    assert r['established_current_expected_net_receipts'] == 0
    assert path.read_bytes() == before


def test_missing_database_not_created(tmp_path):
    path = tmp_path / 'absent.db'
    with pytest.raises(sqlite3.OperationalError):
        inspect(path, 1500)
    assert not path.exists()


def test_economics_has_no_execution_risk_control_writes():
    tree = ast.parse(Path('trader/portfolio/economics.py').read_text())
    imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(any(bad in name for bad in ('executor', 'risk', 'kernel', 'journal', 'ccxt', 'llm', 'state')) for name in imports)
    # The only engine dependency is the existing read-only cost verifier.
    assert [n for n in imports if 'engine' in n] == ['engine']
