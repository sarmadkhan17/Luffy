"""Synthetic-only observability checks. No venue or trading DB access."""
import copy
import json
import sqlite3
import subprocess
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from trader.observability.attention import capture, settings, evaluate_snapshot
from trader.observability.collector import Collector
from trader.observability.store import Store, read_latest, export_scan


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*a, **kw):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def frames(count=6, now=None):
    now = now or int(time.time() * 1000)
    tf = 14_400_000
    anchor = now // tf * tf
    out = {}
    for j in range(count):
        c = 100 * np.exp(np.cumsum(np.sin(np.arange(30) + j) * .01))
        out[f'S{j}/USDT'] = {'4h': pd.DataFrame({
            'ts': pd.to_datetime([anchor - (30-i)*tf for i in range(30)], unit='ms', utc=True),
            'open': c, 'high': c*1.01, 'low': c*.99, 'close': c,
            'volume': 100 + np.arange(30) % 7})}
    return out


def event(sid='s1', now=None, data=None, cfg=None):
    data = frames(now=now) if data is None else data
    return capture(data, list(data), sid, cfg or settings(), now)


def test_capture_freezes_closed_bars_and_observed_availability():
    now = int(time.time()*1000)
    data = frames(now=now)
    captured = event(now=now, data=data)
    data['S0/USDT']['4h'].loc[:, 'close'] = 999
    assert all(c['close'] != 999 for c in captured['input']['candles'])
    assert all(c['available_ms'] == now and c['open_ms']+14_400_000 <= now
               for c in captured['input']['candles'])
    result = evaluate_snapshot(captured)
    assert len(result['rows']) == 6
    assert all(r['status'] == 'ok' for r in result['rows'])
    assert result['market']['cohort_size'] == 6


def test_missing_invalid_and_cap_are_explicit():
    data = frames(4)
    data['S0/USDT'] = {}
    data['S1/USDT']['4h'].loc[29, 'close'] = float('nan')
    result = evaluate_snapshot(event(data=data, cfg=settings({'max_symbols': 3})))
    assert result['scope']['excluded_count'] == 1
    assert result['issues'][0]['reason'] == 'missing_timeframe'
    assert result['rejected_inputs'][0]['reason'] == 'non_finite'
    assert result['rows'][0]['status'] == 'missing'
    assert result['rows'][1]['status'] == 'stale'


def test_versions_first_seen_revisions_export_and_retention(tmp_path):
    now = int(time.time()*1000)
    data = frames(now=now)
    store = Store(tmp_path/'attention.db', settings({'max_scans': 3}))
    store.write(event(now=now, data=data))
    before = store.db.execute('SELECT payload FROM scans WHERE scan_id="s1"').fetchone()[0]
    store.write(event('s2', now+1, data))
    assert store.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 6*26
    data['S0/USDT']['4h'].loc[29, 'volume'] += 100
    store.write(event('s3', now+2, data))
    assert store.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 6*26+1
    v = store.db.execute('SELECT * FROM versions WHERE previous_value_hash IS NOT NULL').fetchone()
    assert v['first_seen_ms'] == now+2
    assert store.db.execute('SELECT payload FROM scans WHERE scan_id="s1"').fetchone()[0] == before
    store.write({'kind': 'causes', 'scan_id': 's3', 'as_of_ms': now+2,
                 'items': [{'symbol': 'S0/USDT', 'decision_id': 'd1'}]})
    latest = read_latest(store.path, now_ms=now+2, health={'updated_ms':now+2, 'status':'ok'})
    assert latest['status'] == 'health_unknown' and latest['causes_complete']  # legacy receipt has no v2 proof
    assert latest['causes'][0]['decision_id'] == 'd1'
    export_scan(store.path, 's1', tmp_path/'frozen.json')
    with pytest.raises(FileExistsError):
        export_scan(store.path, 's1', tmp_path/'frozen.json')
    store.write(event('s4', now+3, data))
    assert store.db.execute('SELECT COUNT(*) FROM scans').fetchone()[0] == 3
    assert not store.db.execute('SELECT 1 FROM scans WHERE scan_id="s1"').fetchone()
    assert json.loads((tmp_path/'frozen.json').read_text())['scan']['scan_id'] == 's1'
    store.close()


def test_age_prune_and_size_bound(tmp_path, monkeypatch):
    now = int(time.time()*1000)
    cfg = settings({'max_age_seconds':60, 'max_bytes': 512*1024})
    store = Store(tmp_path/'attention.db', cfg)
    for i in range(12):
        store.write(event(f's{i}', now+i, frames(2, now), cfg))
        assert store.path.stat().st_size <= cfg['max_bytes']
    monkeypatch.setattr('trader.observability.store.time.time', lambda: now/1000+120)
    store.write(event('future', now+120000, frames(2, now+120000), cfg))
    assert [r[0] for r in store.db.execute('SELECT scan_id FROM scans')] == ['future']
    store.close()


def test_queue_full_and_mutation_are_visible_without_storage(tmp_path):
    collector = Collector(tmp_path, {'queue_size':1}, start=False)
    sid = collector.begin(frames(), ['S0/USDT'])
    assert sid
    assert collector.begin(frames(), ['S0/USDT']) is None
    assert collector.health()['dropped'] == 1
    assert not list(tmp_path.iterdir())
    collector.queue.get_nowait()
    items = [{'symbol':'S0/USDT', 'evaluations':[{'reason':'returned_none'}]}]
    collector.causes(sid, items)
    items[0]['evaluations'][0]['reason'] = 'changed'
    assert collector.queue.get_nowait()['items'][0]['evaluations'][0]['reason'] == 'returned_none'


def test_worker_persists_and_timeout_is_bounded(tmp_path, monkeypatch):
    collector = Collector(tmp_path, start=False)
    collector._run(event())
    assert read_latest(collector.path)['status'] == 'pending_causes'
    def timeout(*a, **kw):
        assert kw['timeout'] == 10
        raise subprocess.TimeoutExpired('worker', 10)
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        collector._run(event('second'))


@pytest.mark.parametrize('failure', [subprocess.TimeoutExpired('worker', 10), OSError('SECRET')])
def test_worker_failure_health_and_queue_continue(tmp_path, monkeypatch, failure):
    collector = Collector(tmp_path, start=False)
    collector._put(event())
    def fail(_):
        collector.close()
        raise failure
    monkeypatch.setattr(collector, '_run', fail)
    collector._loop()
    health = json.loads(collector.health_path.read_text())
    assert health['worker_errors'] == 1 and health['status'] == 'error'
    assert 'SECRET' not in json.dumps(health)
    assert collector.queue.unfinished_tasks == 0


def test_read_staleness_and_failed_new_scan_never_look_current(tmp_path):
    now = int(time.time()*1000)
    store = Store(tmp_path/'attention.db', settings())
    store.write(event(now=now))
    assert read_latest(store.path, now_ms=now+301000)['status'] == 'stale'
    assert read_latest(store.path, health={'last_scan_id':'lost','last_error':'queue_full'})['status'] == 'error'
    store.close()
    (tmp_path/'broken.db').write_text('invalid database')
    assert read_latest(tmp_path/'broken.db')['status'] == 'unavailable'


def test_strategy_receipts_distinguish_swallowed_errors(monkeypatch):
    from trader.strategy import library
    from trader.observability.diagnostics import Receipts
    genome = NS(family='fixture', params={}, strategy_id='s')
    rec = Receipts(True, 3)
    callback = lambda reason, exc=None: rec.add('strategy','s',reason,exc)
    library.evaluate(genome, None, diagnostic=callback)
    monkeypatch.setitem(library.EVALUATORS, 'fixture', lambda *a: None)
    library.evaluate(genome, None, diagnostic=callback)
    def fail(*a): raise RuntimeError('SECRET')
    monkeypatch.setitem(library.EVALUATORS, 'fixture', fail)
    assert library.evaluate(genome, None, diagnostic=callback) is None
    assert [i['reason'] for i in rec.items] == ['missing_evaluator','returned_none','evaluation_failed']
    assert 'SECRET' not in json.dumps(rec.items)
    assert rec.items[-1]['frames'][-1]['function'] == 'fail'
    rec.add('strategy','s','more')
    assert rec.omitted == 1


def test_journal_scan_link_is_nullable(tmp_path):
    from trader.core.journal import Journal
    from trader.core.types import Decision, Action
    j = Journal(tmp_path/'journal.db')
    d = Decision('d','c','S',Action.HOLD,0,.2,0,[],[])
    from trader.core.types import Snapshot
    j.log_cycle(Snapshot('S',d.ts,100,{}), 'c', 'test')
    j.log_decision(d)
    assert j.query('SELECT scan_id FROM decisions')[0]['scan_id'] is None
    d.scan_id = 's1'
    j.log_decision(d)
    assert j.query('SELECT scan_id FROM decisions')[0]['scan_id'] == 's1'


def test_orchestrator_flag_preserves_decision_and_records_every_path(tmp_path, monkeypatch):
    from trader.engine import orchestrator as mod
    from trader.strategy import library
    from trader.core.journal import Journal
    from trader.core.types import Snapshot, Action, StrategySignal
    monkeypatch.setattr(mod, '_measured_weights', lambda: ({},{}))
    monkeypatch.setattr(mod, 'classify', lambda *a: {'regime':'RANGING','adx':20})
    monkeypatch.setattr(mod, 'htf_trend_score', lambda *a: 0)
    monkeypatch.setattr(mod, 'regime_allows', lambda g, r: g.strategy_id != 'regime')
    monkeypatch.setattr(mod, 'symbol_allows', lambda g, r: g.strategy_id != 'symbol')
    def boom(_): raise ValueError('SECRET')
    analysts = [NS(name='silent', evaluate=lambda _: None), NS(name='broken', evaluate=boom)]
    population = []
    for sid in ['state','market','regime','symbol','missing','silent','signal','broken']:
        population.append((NS(id=sid,is_trade_eligible=sid!='state'),
                           NS(strategy_id=sid, family=sid, markets=['spot'] if sid=='market' else ['futures'],params={})))
    monkeypatch.setitem(library.EVALUATORS,'silent',lambda *a: None)
    monkeypatch.setitem(library.EVALUATORS,'broken',lambda *a: boom(None))
    monkeypatch.setitem(library.EVALUATORS,'signal',lambda *a: StrategySignal('signal','signal','S',Action.BUY,.7,'fixture'))
    decisions=[]
    for enabled in [False,True]:
        orc = mod.Orchestrator(analysts, Journal(tmp_path/f'{enabled}.db'), cfg={
            'attention':{'enabled':enabled}, 'scouts':{k:{'enabled':False} for k in
                ['calibration','ewa','meta','adaptive_threshold']}})
        monkeypatch.setattr(orc, '_strategy_weights', lambda *a: {})
        snap=Snapshot(symbol='S',ts='2026-09-16T00:00:00+00:00',price=100,dfs={})
        decisions.append(orc.decide(snap,population))
    off,on=decisions
    for field in ['action','score','threshold','confidence','votes','strategy_signals','skip_reason','meta_p','meta_size']:
        assert getattr(off,field) == getattr(on,field)
    assert not off.evaluation_causes
    assert [c['reason'] for c in on.evaluation_causes] == [
        'returned_none','evaluation_failed','ineligible_state','ineligible_market',
        'ineligible_regime','ineligible_symbol','missing_evaluator','returned_none','emitted_signal','evaluation_failed']


@pytest.mark.parametrize('mode', ['disabled','normal','full','broken'])
@pytest.mark.parametrize('state_name', ['ACTIVE','FROZEN','HALTED','RECOVERY'])
@pytest.mark.parametrize('recovery_pending', [False, True])
def test_real_kernel_cycle_keeps_entry_exit_behavior(tmp_path, mode, state_name, recovery_pending):
    from trader.kernel import Kernel
    from trader.core.types import Decision, Action, MarketType, ControlState
    state=ControlState(state_name)
    k=object.__new__(Kernel)
    k.cfg={'timeframes':{'execution':'15m'}}
    k.population=[]
    k.state_machine=NS(refresh=lambda:state)
    k._fetch_balance=lambda:1000
    k.risk=NS(update_equity=lambda _: {'equity':1000,'drawdown_pct':0},daily_loss_block=.05)
    k._drain_close_requests=lambda:0
    k.journal=NS(kv_get=lambda key, default=None:default, kv_set=lambda *a:None,
                 query=lambda *a:[{'n':0}], update_decision_outcome=Mock(),
                 open_trades=lambda:[{'symbol':'S0/USDT'}],log_equity=Mock())
    k.macro_guard=k.news_guard=NS(check=lambda:{'active':False})
    k.market_type=MarketType.SPOT
    if recovery_pending:
        k.market_type=MarketType.FUTURES
    k.executor=NS(recovery_pending=lambda:recovery_pending, recover_entries=Mock())
    k._funding_map=k._oi_map=lambda:{}
    k._refresh_btc_context=lambda:None
    k._scan_symbols=lambda:['S0/USDT','missing']
    data=frames(2)
    k._universe_frames=lambda _:data
    k._snapshot_for=lambda s,**kw: NS(symbol=s,price=100) if s!='missing' else None
    k.positioning_agent=k.depth_agent=NS(set_context=lambda *a:None)
    k._order_book=lambda _:{}
    d=Decision('d','c','S0/USDT',Action.BUY,.7,.2,.8,[],[],skip_reason='' if state==ControlState.ACTIVE else 'blocked')
    k.orchestrator=NS(decide=lambda *a,**kw:d,journalize=Mock())
    def enter(*a): d.executed=True; return True
    k._try_enter=Mock(side_effect=enter)
    k._detect_exchange_exits=Mock(return_value=0)
    k._manages_exits=lambda:state!=ControlState.HALTED
    k._manage_one=Mock(return_value=None)
    k._manage_orphan_positions=Mock(return_value=1)
    k._maybe_resolve_outcomes=Mock()
    k.heartbeat=NS(beat=Mock())
    k.notifier=NS(send=Mock())
    if mode!='disabled':
        k._attention=Collector(tmp_path,{'queue_size':1 if mode=='full' else 16},start=False)
        if mode=='full': k._attention._put({'occupied':True})
        if mode=='broken':
            k._attention.begin=Mock(side_effect=OSError('SECRET'))
            k._attention.causes=Mock(side_effect=OSError('SECRET'))
    result=k.cycle()
    assert result['scanned']==result['decisions']==1
    assert result['entries']==int(state==ControlState.ACTIVE and not recovery_pending)
    assert k._try_enter.call_count==int(state==ControlState.ACTIVE and not recovery_pending)
    assert k.executor.recover_entries.call_count==int(recovery_pending and state!=ControlState.HALTED)
    assert k._manage_one.call_count==int(state!=ControlState.HALTED)
    k._detect_exchange_exits.assert_called_once_with('S0/USDT')
    k._manage_orphan_positions.assert_called_once_with({'S0/USDT'})
    k._maybe_resolve_outcomes.assert_called_once()
    if mode=='normal':
        assert d.scan_id
        k._attention.queue.get_nowait()
        causes=k._attention.queue.get_nowait()
        assert causes['items'][1]['reason']=='missing_snapshot'
        assert causes['items'][0]['decision_id']=='d'
    else:
        assert d.scan_id is None
    if mode=='broken': assert result['attention']['kernel_error']=='OSError'


def test_compiled_spec_reports_missing_and_failed_inputs(monkeypatch):
    from trader.strategy import compile as mod, library
    from trader.core.types import Snapshot
    compiled=object.__new__(mod.CompiledStrategy)
    compiled.spec=NS(timeframe='4h',id='fixture')
    fn=compiled.to_evaluator()
    monkeypatch.setitem(library.EVALUATORS,'fixture',fn)
    genome=NS(family='fixture',params={},strategy_id='fixture')
    results=[]
    def record(reason,exc=None): results.append(reason)
    assert library.evaluate(genome,Snapshot('S','now',100,{}),record) is None
    assert results==['missing_closed_timeframe']
    def broken(*a,**kw):raise RuntimeError('SECRET')
    monkeypatch.setattr(compiled,'entries',broken)
    results.clear()
    assert library.evaluate(genome,Snapshot('S','now',100,frames(2)['S0/USDT']),record) is None
    assert results==['evaluation_failed']


def test_replay_export_reconstructs_inputs_and_evidence_without_dangling_ids(tmp_path):
    from trader.cognition.contracts import INPUT_SCHEMA
    from trader.observability.attention import digest
    now=int(time.time()*1000)
    store=Store(tmp_path/'attention.db',settings())
    original=event(now=now)
    store.write(copy.deepcopy(original))
    export_scan(store.path,'s1',tmp_path/'export.json')
    out=json.loads((tmp_path/'export.json').read_text())
    versions={v['version_id']:v for v in out['versions']}
    candles=[]
    for ref in out['scan']['input_versions']:
        v=versions[ref['version_id']]
        candles.append({**json.loads(v['payload']),'available_ms':v['first_seen_ms']})
    restored={**original,'input':{'schema':INPUT_SCHEMA,'timeframe':out['scan']['timeframe'],
              'membership':out['scan']['membership'],'candles':candles,
              'decision_times':[now],'participation':[]}}
    assert digest(restored['input'])==out['scan']['input_hash']
    replay=evaluate_snapshot(restored)
    assert replay['rows']==out['scan']['rows']
    ids={o['obs_id'] for o in replay['observations']}
    assert all(set(row['evidence'])<=ids for row in replay['rows'])
    store.close()


def test_late_arrival_changes_only_future_scan(tmp_path):
    now=int(time.time()*1000)
    data=frames(2,now)
    complete=data['S0/USDT']['4h'].copy()
    data['S0/USDT']['4h']=complete.iloc[:-1]
    store=Store(tmp_path/'attention.db',settings())
    store.write(event('before',now,data))
    before=store.db.execute('SELECT payload FROM scans WHERE scan_id="before"').fetchone()[0]
    assert json.loads(before)['rows'][0]['reason']=='stale'
    data['S0/USDT']['4h']=complete
    store.write(event('after',now+1,data))
    latest=read_latest(store.path)['scan']
    assert latest['rows'][0]['status']=='ok'
    assert latest['rows'][0]['components']['relative_return_divergence'] is None
    assert store.db.execute('SELECT payload FROM scans WHERE scan_id="before"').fetchone()[0]==before
    anchor=complete.ts.iloc[-1].value//1_000_000
    assert store.db.execute('SELECT first_seen_ms FROM versions WHERE symbol=? AND open_ms=?',('S0/USDT',int(anchor))).fetchone()[0]==now+1
    store.close()


def test_locked_telemetry_db_does_not_lock_trading_journal(tmp_path):
    from trader.observability.collector import WorkerError
    from trader.core.journal import Journal
    collector=Collector(tmp_path,start=False)
    store=Store(collector.path,collector.cfg)
    store.db.execute('BEGIN EXCLUSIVE')
    try:
        with pytest.raises(WorkerError, match='OperationalError'):
            collector._run(event())
        journal=Journal(tmp_path/'trading.db')
        journal.kv_set('isolation','ok')
        assert journal.kv_get('isolation')=='ok'
        assert collector.begin(frames(),['S0/USDT'])
    finally:
        store.db.rollback()
        store.close()


def test_bad_receipt_callback_does_not_change_strategy_result(monkeypatch):
    from trader.strategy import library
    output=object()
    monkeypatch.setitem(library.EVALUATORS,'fixture',lambda *a:output)
    def failed(*a): raise OSError('SECRET')
    assert library.evaluate(NS(family='fixture',params={}),None,failed) is output


def test_empty_store_preserves_failure_status(tmp_path):
    store=Store(tmp_path/'attention.db',settings())
    assert read_latest(store.path,health={'last_error':'worker_timeout'})['status']=='error'
    store.close()
