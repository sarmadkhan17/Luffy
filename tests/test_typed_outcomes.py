"""Synthetic engineering evidence, never economic or predictive acceptance."""
import copy
import json
import sqlite3
from dataclasses import replace

import pytest

from trader.cognition import outcomes as O, investigation as I, memory as M
from trader.observability import outcomes as S, investigation as C, learning as L
from tests.test_attention_learning import publish
from tests.test_market_investigation import prefix


@pytest.fixture
def forecast(tmp_path):
    now=1_789_000_000_000//L.TF*L.TF+60_000
    src,dst=tmp_path/'attention.db',tmp_path/'attention_learning.db'
    publish(src,now); r=L.step(src,dst,now)
    end=r['recent'][0]['deadline_ms']
    publish(src,end+1,'resolution',120); L.step(src,dst,end+1)
    with sqlite3.connect(dst) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute("SELECT * FROM episodes WHERE status='resolved'")]
    for r in rows:
        r['prediction']=json.loads(r['prediction']);r['outcome']=json.loads(r['outcome'])
    return tmp_path,rows,end+2


def test_forecast_selected_ignored_exact_replay(forecast):
    path,rows,now=forecast
    records=[S._forecast(path/'attention.db',r,now) for r in rows]
    assert {r['kind'] for r in records}=={'selected_forecast','ignored_forecast'}
    for r in records:
        assert O.replay(json.loads(L.encode(r)))==r
        assert r['actual_execution'] is None and r['simulation'] is None
        assert len(r['source']['source_receipt'])==7


@pytest.mark.parametrize('mutation',['target_key','version','baseline','registration','resolution','measurement','direction'])
def test_forecast_tampering_refused(forecast,mutation):
    path,rows,now=forecast
    r=copy.deepcopy(rows[0])
    if mutation=='target_key': r['outcome']['target']['open_ms']+=L.TF
    if mutation=='version': r['outcome']['target']['version_id']='wrong'
    if mutation=='baseline': r['prediction']['baseline_close']+=1
    if mutation=='registration': r['prediction']['registered_ms']+=1
    if mutation=='resolution': r['outcome']['recorded_ms']=now+1
    if mutation=='measurement': r['outcome']['price_change_bps']+=1
    if mutation=='direction': r['prediction']['direction']*=-1
    with pytest.raises(ValueError): S._forecast(path/'attention.db',r,now)


def test_cross_retention_archive_and_terminal_immutability(forecast,tmp_path):
    path,rows,now=forecast
    with C.ledger(tmp_path/'memory.db') as db:
        result=S.ingest(db,path,now)
        assert result['added']==6 and not result['refused']
        archive=S.export(db)
        assert S.ingest(db,path,now+1)['added']==0
        record=archive['records'][0]
        altered=O.forecast(record['record']['source'],now+1)
        with pytest.raises(ValueError,match='terminal_conflict'): S.put(db,record['source_key'],altered)
        S.retain(db,now+S.RETENTION_MS+1)
        assert S.export(db)['records']==[]
    # Source ledgers need not exist on the destination/replay machine.
    with C.ledger(tmp_path/'restored.db') as db:
        S.restore(db,json.loads(L.encode(archive)),now+100)
        assert S.export(db)==archive
        assert db.execute('SELECT MIN(imported_ms) FROM typed_outcomes').fetchone()[0]==now+100
        broken=copy.deepcopy(archive);broken['records'][0]['record']['observation']['price_change_bps']=0
        with pytest.raises(ValueError): S.restore(db,broken,now+101)
        assert S.export(db)==archive


def test_frozen_typed_memory_changes_later_request_without_hindsight(forecast,prefix,tmp_path):
    path,rows,now=forecast
    _,snap,inv=prefix
    # Compatible symbol and chronological registration; trigger protocol stays unchanged.
    state=replace(inv.state,symbol=rows[0]['symbol'])
    inv=replace(inv,state=state,registered_ms=now+1)
    with C.ledger(tmp_path/'m.db') as db:
        S.ingest(db,path,now)
        ctx=S.register(db,inv)
        assert ctx['cases'] and ctx['counter_tests']
        update=I.advance(inv,I.measure(inv,(),inv.registered_ms,inv.registered_ms))
        base=M.retrieve(inv,[])
        changed=dict(base,typed_outcomes=ctx,counter_tests=ctx['counter_tests'])
        assert M.reasoning(update,changed)['changed']
        assert M.reasoning(update,changed)['without_memory']==M.reasoning(update,base)['with_memory']
        earlier=replace(inv,investigation_id='early',registered_ms=now)
        assert not S.register(db,earlier)['cases']
        # A late restoration cannot become earlier knowledge.
        archive=S.export(db)
    with C.ledger(tmp_path/'late.db') as db:
        S.restore(db,archive,now+10)
        assert not S.register(db,inv)['cases']


def decision():
    return dict(id='d',symbol='S/USDT',ts='2026-09-16T00:00:00+00:00',executed=False)


def test_journal_unknown_is_not_zero_or_estimated_actual():
    d=decision();now=O.timestamp(d['ts'])+10000
    skip=O.journal_record(d,None,now)
    assert skip['kind']=='skip' and skip['actual_execution']['net_pnl'] is None
    d['executed']=True
    t=dict(id='t',decision_id='d',symbol=d['symbol'],status='closed',closed_at=d['ts'],realized_pnl=123)
    c=O.journal_record(d,t,now)
    assert c['kind']=='executed_trade' and c['actual_execution']['net_pnl'] is None
    assert c['source']['trade']['realized_pnl']==123 and O.replay(c)==c
    t['symbol']='wrong'
    with pytest.raises(ValueError): O.journal_record(d,t,now)


def test_simulation_is_separate_and_links_are_point_in_time(forecast):
    _,rows,now=forecast;p=rows[0]['prediction'];target=rows[0]['outcome']['target']
    registration=dict(schema_version='close-counterfactual.v1',decision_kind='missed_opportunity',
        registered_ms=p['registered_ms'],target_open_ms=p['target_open_ms'],baseline=p['input_window'][-1],
        direction=p['direction'],cost_bps=10,notional=100)
    links=[dict(kind=k,id=k,version='v1',available_ms=p['registered_ms']) for k in O.LINK_TYPES]
    c=O.counterfactual(registration,target,now,links)
    assert c['actual_execution'] is None and c['simulation']['status']=='simulated'
    assert O.replay(c)==c
    links[0]['available_ms']+=1
    with pytest.raises(ValueError): O.counterfactual(registration,target,now,links)


def test_complete_execution_receipt_and_missing_accounting():
    r=dict(trade_id='t',symbol='S',venue='v',environment='demo',registered_ms=100)
    a=dict(schema_version='execution-accounting.v1',complete=True,trade_id='t',symbol='S',venue='v',environment='demo',
           reconciliation_version='receipt-v1',funding_complete=True,funding_net=-1,currency='USDT',
           resolved_ms=200,available_ms=201,fills=[dict(id='f',version='v1',trade_id='t',currency='USDT',
           event_ms=150,available_ms=160,realized_pnl=10,commission=2)])
    c=O.verified_execution(r,a,202)
    assert c['actual_execution']['net_pnl']==7 and c['actual_execution']['environment']=='demo'
    assert O.replay(c)==c
    a['funding_complete']=False
    assert O.verified_execution(r,a,202)['actual_execution']['net_pnl'] is None
    a['funding_complete']=True;a['fills'][0]['available_ms']=203
    with pytest.raises(ValueError): O.verified_execution(r,a,202)


def test_missing_target_logs_explicit_retry(forecast):
    path,rows,now=forecast
    with sqlite3.connect(path/'attention.db') as db:
        db.execute('DELETE FROM scan_versions')
        db.execute('DELETE FROM versions')
    with sqlite3.connect(path/'attention_learning.db') as db:
        db.execute('DELETE FROM forecast_sources')
    with C.ledger(path/'m.db') as db:
        report=S.ingest(db,path,now)
        assert len(report['refused'])==6
        assert all(r['reason']=='exact_source_version_missing_retry' for r in report['refused'])


def test_storage_capacity_and_archive_restore_is_atomic(forecast,tmp_path,monkeypatch):
    path,rows,now=forecast
    with C.ledger(tmp_path/'a.db') as db:
        S.ingest(db,path,now);archive=S.export(db)
    with C.ledger(tmp_path/'b.db') as db:
        monkeypatch.setattr(S,'MAX_CASES',1)
        with pytest.raises(ValueError,match='capacity'): S.restore(db,archive,now+1)
        assert S.export(db)['records']==[]
        monkeypatch.setattr(S,'MAX_CASES',256)
        broken=copy.deepcopy(archive)
        broken['records'][-1]['record']['observation']['price_change_bps']+=1
        body={k:broken[k] for k in ('schema_version','records')};broken['sha256']=O.digest(body)
        with pytest.raises(ValueError,match='replay_mismatch'): S.restore(db,broken,now+1)
        assert S.export(db)['records']==[]


def test_live_journal_import_forward_only_and_idempotent(tmp_path):
    now=O.timestamp(decision()['ts'])
    with sqlite3.connect(tmp_path/'luffy.db') as db:
        db.execute('CREATE TABLE decisions(id,cycle_id,ts,symbol,action,executed,skip_reason,strategy_ids,scan_id)')
        db.execute('CREATE TABLE trades(id,decision_id,symbol,status,closed_at,opened_at,realized_pnl,exec_mode,strategy_id)')
        db.execute("INSERT INTO decisions VALUES ('old','c','2026-09-15T00:00:00+00:00','S','BUY',0,'risk','s','scan')")
        db.execute("INSERT INTO decisions VALUES ('new','c','2026-09-16T00:00:01+00:00','S','BUY',0,'risk','s','scan')")
    with C.ledger(tmp_path/'m.db') as db:
        # Future decisions are refused at the actual consumer time.
        report=S.ingest(db,tmp_path,now)
        assert report['added']==0 and report['refused']
        assert S.ingest(db,tmp_path,now+2000)['added']==1
        assert S.ingest(db,tmp_path,now+3000)['added']==0
        assert S.export(db)['records'][0]['record']['actual_execution']['net_pnl'] is None


def test_forecast_receipts_survive_attention_retention(forecast):
    path,rows,now=forecast
    with sqlite3.connect(path/'attention.db') as db:
        db.execute('DELETE FROM scan_versions');db.execute('DELETE FROM versions');db.execute('DELETE FROM scans')
    with C.ledger(path/'m.db') as db:
        result=S.ingest(db,path,now)
        assert result['added']==6 and not result['refused']
        for item in S.export(db)['records']: O.replay(item['record'])


def test_counterfactual_registration_resolution_retry_and_no_rewrite(forecast,tmp_path):
    from types import SimpleNamespace
    from trader.cognition.contracts import Candle
    path,rows,now=forecast;p=rows[0]['prediction'];t=rows[0]['outcome']['target']
    r=dict(schema_version='close-counterfactual.v1',decision_kind='missed_opportunity',
        registered_ms=p['registered_ms'],target_open_ms=p['target_open_ms'],baseline=p['input_window'][-1],
        direction=p['direction'],cost_bps=10,notional=100)
    b=I.InputBar(t['version_id'],Candle(**{k:t[k] for k in ('symbol','open_ms','open','high','low','close','volume','available_ms')},source='fixture',close_ms=t['open_ms']+L.TF))
    with C.ledger(tmp_path/'m.db') as db:
        with pytest.raises(ValueError,match='not_forward'): S.register_counterfactual(db,r,now)
        key=S.register_counterfactual(db,r,r['registered_ms'])
        assert key==S.register_counterfactual(db,r,r['registered_ms'])
        assert S.resolve_counterfactuals(db,None,now)['retry']
        assert S.resolve_counterfactuals(db,SimpleNamespace(bars=[b]),now)['added']==1
        original=S.export(db)
        assert S.resolve_counterfactuals(db,SimpleNamespace(bars=[replace(b,candle=replace(b.candle,close=999,high=999))]),now+1)['added']==0
        assert S.export(db)==original
        assert original['records'][0]['record']['simulation']['status']=='simulated'


def test_pure_outcomes_imports_no_runtime():
    import subprocess
    import sys
    result=subprocess.run([sys.executable,'-c',
        "import sys, trader.cognition.outcomes; assert not any(n.startswith('trader.observability') for n in sys.modules)"],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_slow_import_cannot_make_coherent_collector_snapshot_future(tmp_path,monkeypatch):
    now=1_789_000_000_000//L.TF*L.TF+60_000
    src=tmp_path/'attention.db';publish(src,now)
    original=S.ingest
    def delayed(db,directory,clock):
        # A heartbeat legitimately advances while the independent journal import runs.
        (tmp_path/'attention_health.json').write_text(json.dumps(dict(status='ok',worker_alive=True,updated_ms=now+1)))
        return original(db,directory,clock)
    monkeypatch.setattr(S,'ingest',delayed)
    result=C.step(src,tmp_path/'m.db',now)
    assert result['status']=='ok' and result['registered']>0


def test_skip_retention_cannot_crowd_out_forecasts(forecast,tmp_path,monkeypatch):
    path,rows,now=forecast
    monkeypatch.setattr(S,'MAX_CASES',8)
    with C.ledger(tmp_path/'m.db') as db:
        first=O.forecast(rows[0],now);S.put(db,'forecast',first)
        for n in range(20):
            d=decision();d['id']=str(n)
            S.put(db,'skip:'+str(n),O.journal_record(d,None,O.timestamp(d['ts'])+n))
        archive=S.export(db)
        assert len(archive['records'])==5
        assert any(r['source_key']=='forecast' for r in archive['records'])
        assert db.execute("SELECT value FROM typed_outcome_meta WHERE key='evicted_total'").fetchone()[0]==16
