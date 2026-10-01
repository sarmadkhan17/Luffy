"""Read-only SQL and source-preservation checks against synthetic stores."""
import sqlite3
import pytest
from scripts import common_factor_shadow as S, opportunity_context_shadow as O
from trader.core.journal import Journal
from trader.engine.evidence_capture import record_snapshot
from trader.strategy import factory_handoff as F
from tests.test_opportunity_live_integration import snapshot


def sources(tmp_path):
    j=Journal(tmp_path/'sources'/'journal.db')
    F.ensure(j)
    record_snapshot(j,snapshot(),at_ms=1000)
    j.kv_set('control_state','FROZEN')
    return j,tmp_path/'sources'/'journal.db'


def test_shadow_preserves_sources_and_unknowns(tmp_path,monkeypatch):
    j,path=sources(tmp_path)
    before=j.query('SELECT * FROM state_kv')
    versions=j.query('SELECT * FROM strategy_versions')
    monkeypatch.setattr(O.time,'time_ns',lambda:1000*1000000)
    r=S.run(path,path.parent/'absent-attention.db',path.parent/'absent-investigation.db',
        {'risk':{'max_open_positions':8}},tmp_path/'out')
    assert r['candidate_count']==0 and len(r['holdings'])==1
    assert r['decision']=='NO_ALLOCATION' and r['replay']=='PASS'
    assert r['production_mutations']==r['authenticated_requests']==0
    assert j.query('SELECT * FROM state_kv')==before
    assert j.query('SELECT * FROM strategy_versions')==versions
    assert not (path.parent/'absent-attention.db').exists()
    assert not (path.parent/'absent-investigation.db').exists()


def test_shadow_refuses_stale_book_and_live_output(tmp_path,monkeypatch):
    _,path=sources(tmp_path)
    monkeypatch.setattr(O.time,'time_ns',lambda:1000000*1000000)
    with pytest.raises(ValueError,match='STALE'):
        S.run(path,path.parent/'absent-attention.db',path.parent/'absent-investigation.db',{'risk':{'max_open_positions':8}},tmp_path/'out')
    with pytest.raises(ValueError,match='OUTSIDE_PRODUCTION'):
        S.run(path,path.parent/'absent-attention.db',path.parent/'absent-investigation.db',{},path.parent/'out')


def test_sql_write_denied(tmp_path):
    from contextlib import ExitStack
    import time
    _,path=sources(tmp_path)
    with ExitStack() as stack:
        db=O._read(stack,path,time.monotonic()+5)
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE state_kv SET value='ACTIVE' WHERE key='control_state'")
