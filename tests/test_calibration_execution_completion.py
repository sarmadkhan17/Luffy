"""Incomplete computation is resumable work, never a calibration sample."""
import sqlite3

import pytest

from trader.research.closure import ClosureRefused, calibration as C


def protocol():
    return dict(C.PROTOCOL, **C.SMOKE, families=['trend_breakout.v1'], max_revisions=0,
        policy=dict(min_threshold_samples=1000), cases=[C.PROTOCOL['cases'][-1]])


@pytest.mark.parametrize('details', [
    dict(process_status='IN_PROGRESS', stop_reason='BUDGET_EXHAUSTED:cpu_seconds'),
    dict(process_status='COMPLETED', failed_units=1),
    dict(process_status='COMPLETED', development_outcomes={'CODE_FAILURE': 1}),
])
def test_incomplete_replicate_is_not_cached_and_retry_resumes(tmp_path, monkeypatch, details):
    calls = []

    def run(p, case, i, seed, directory, run_id=''):
        calls.append(1)
        return dict(run_id=run_id, case=case['name'], index=i, seed=seed,
            outcome='NOT_REACHED', trials=0, **(details if len(calls) == 1 else
                                              dict(process_status='COMPLETED')))

    monkeypatch.setattr(C, '_replicate', run)
    with pytest.raises(ClosureRefused, match='calibration_replicate_incomplete'):
        C.run(protocol(), tmp_path, replicates=1)
    assert not list(tmp_path.glob('rep-*.json'))
    report = C.run(protocol(), tmp_path, replicates=1)
    assert len(calls) == 2 and len(report['replicates']) == 1
    assert report['calibration_status'] == 'INCONCLUSIVE'


def test_worker_interruption_closes_database(tmp_path, monkeypatch):
    stores = []
    real_store = C.Store

    def store(path):
        obj = real_store(path)
        stores.append((obj, obj._local.conn))
        return obj

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt('bounded interruption probe')

    monkeypatch.setattr(C, 'Store', store)
    monkeypatch.setattr(C.S, 'create_study', interrupted)
    with pytest.raises(KeyboardInterrupt):
        C._replicate(protocol(), C.PROTOCOL['cases'][-1], 0, 10000, tmp_path)
    assert len(stores) == 1
    assert stores[0][0]._local.conn is None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        stores[0][1].execute('SELECT 1')
