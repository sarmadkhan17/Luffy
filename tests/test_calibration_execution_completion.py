"""Incomplete computation is resumable work, never a calibration sample."""
import sqlite3
import json
import os
from pathlib import Path

import pytest

from trader.research.closure import ClosureRefused, calibration as C


def _worker_isolation_probe(payload):
    import resource
    result=dict(pid=os.getpid(),memory_limit=resource.getrlimit(resource.RLIMIT_AS)[0],nice=os.nice(0))
    Path(payload['store_path']+'.worker-probe').write_text(json.dumps(result))
    # Deliberately not a research result: isolation must not grant evidence.
    return {}


def test_calibration_uses_the_scheduled_research_worker_limits(tmp_path,monkeypatch):
    monkeypatch.setattr(C.S,'develop_job',_worker_isolation_probe)
    p=dict(protocol(),unit_timeout_s=17,risk=dict(taker_fee_pct=.08,risk_per_trade_pct=.25,max_open_positions=3))
    result=C._replicate(p,p['cases'][0],0,10000,tmp_path)
    probe=json.loads(next(tmp_path.glob('*/closure.db.worker-probe')).read_text())
    assert probe['pid']!=os.getpid()
    assert probe['memory_limit']==C.S._limits({'search':None})['memory_mb']*1024*1024
    assert probe['nice']>=19
    with sqlite3.connect(next(tmp_path.glob('*/closure.db'))) as db:
        study=json.loads(db.execute('SELECT payload FROM studies').fetchone()[0])
        envelope=json.loads(db.execute('SELECT payload FROM envelopes').fetchone()[0])
    assert study['unit_timeout_s']==17 and envelope['risk']['taker_fee_pct']==.08
    assert envelope['risk']['risk_per_trade_pct']==.25 and envelope['risk']['max_open_trades']==3
    assert result['failed_units']==1 and result['trials']==0
    with pytest.raises(ClosureRefused,match='calibration_replicate_incomplete'):
        C._require_completed_replicate(result)


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
    assert report['cost_id']==C.digest(C._risk(protocol()))


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
