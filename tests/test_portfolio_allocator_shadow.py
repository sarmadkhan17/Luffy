from pathlib import Path
import sqlite3
import pytest

from scripts.portfolio_allocator_shadow import TABLES, gather
from trader.core.types import MarketType
from trader.observability.portfolio_observation import observe_positions
from trader.engine.evidence_capture import canonical, position_snapshot
from trader.portfolio.allocator import allocate


def database(tmp_path, *, versions=False):
    path = tmp_path / 'journal.db'
    observation = observe_positions([], exchange_id='binanceusdm', market_type=MarketType.FUTURES,
                                    environment='demo', source_ref='https://demo-fapi.binance.com',
                                    request_start_ms=900, response_received_ms=1000)
    snapshot = position_snapshot(observation, [])
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE state_kv (key TEXT PRIMARY KEY, value TEXT)')
        db.executemany('INSERT INTO state_kv VALUES (?,?)', [('control_state', 'FROZEN'),
                       ('venue_position_snapshot', canonical(snapshot))])
        for table in TABLES:
            db.execute('CREATE TABLE ' + table + ' (id TEXT)')
        if versions:
            db.execute("INSERT INTO strategy_versions VALUES ('a')")
    return path


def test_shadow_read_only_empty_actual_version_set(tmp_path):
    path = database(tmp_path)
    before = path.read_bytes()
    inputs, receipt = gather(path, {'max_positions': 3}, 1500)
    p = allocate(inputs)
    assert receipt['version_count'] == 0
    assert receipt['portfolio_fresh']
    assert inputs.candidates == ()
    assert 'NO_ALLOCATION' in p.result_json
    assert path.read_bytes() == before


def test_shadow_stale_truth_retained(tmp_path):
    path = database(tmp_path)
    _, receipt = gather(path, {'max_positions': 3}, 1000000)
    assert not receipt['portfolio_fresh']


def test_shadow_never_manufactures_opportunities_from_versions(tmp_path):
    path = database(tmp_path, versions=True)
    with pytest.raises(ValueError, match='OPPORTUNITY_ADAPTER_UNAVAILABLE'):
        gather(path, {}, 1500)


def test_shadow_refuses_tampered_snapshot(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE state_kv SET value='{}' WHERE key='venue_position_snapshot'")
    with pytest.raises(ValueError, match='SNAPSHOT_UNVERIFIED'):
        gather(path, {}, 1500)


def test_missing_database_never_created(tmp_path):
    path = tmp_path / 'absent.db'
    with pytest.raises(sqlite3.OperationalError):
        gather(path, {}, 1500)
    assert not path.exists()
