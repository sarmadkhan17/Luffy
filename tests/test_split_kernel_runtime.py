from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock
from pathlib import Path
import threading
import time

import pandas as pd
import pytest
import yaml

from trader.core.types import Snapshot, ControlState
from trader.scheduling.runtime import Worker
from trader.scheduling.kernel_runtime import KernelRuntime
from trader.scheduling.coordination import input_fingerprint, coordinated


def config():
    return yaml.safe_load(Path('config.yaml').read_text())


def minimal(tmp_path):
    k = NS(cfg={}, journal=NS(db_path=str(tmp_path/'j.db')), feed=NS(),
           _stop=False, _boot_complete=True, heartbeat=Mock(),
           state_machine=NS(state=ControlState.FROZEN))
    return k, KernelRuntime(k)


def test_complete_schedule_contract(tmp_path):
    k, r = minimal(tmp_path)
    assert {n:w.interval for n,w in r.jobs.items() if w.interval} == {
        'commands':1, 'account':30, 'positions':30, 'market':60, 'candles':15,
        'strategy_health':3600, 'health':10, 'learning_apply':60, 'learning_check':60, 'outcomes':60,
        'reports':3600, 'postmortem':21600, 'derivatives':900, 'derivative_inputs':300, 'references':3600,
        'input_metadata':30, 'input_warm':2, 'derivative_needs':60, 'alts_history':300, 'coordinated_paper':60}
    assert r.jobs['research'].calendar.hours == (9,21)
    assert r.jobs['validation'].calendar.hours == (3,)
    assert r.jobs['validation'].calendar.weekday == 6
    assert r.jobs['daily_report'].calendar.minute == 10
    assert r.jobs['decisions'].interval is None


def test_slow_analysis_does_not_hold_position_worker():
    entered, release, serviced = threading.Event(), threading.Event(), threading.Event()
    market = Worker('market', lambda: (entered.set(), release.wait(3)), immediate=True)
    positions = Worker('positions', serviced.set, immediate=True)
    market.start()
    try:
        assert entered.wait(1)
        positions.start()
        assert serviced.wait(1)
        assert market.snapshot()['status'] == 'running'
    finally:
        market.stop(); positions.stop(); release.set()
        assert market.join(2) and positions.join(2)


def test_health_cannot_refresh_from_market_or_report_completion(tmp_path):
    k, r = minimal(tmp_path)
    with pytest.raises(ValueError, match='essential_cycles'):
        r.health()
    k.heartbeat.beat.assert_not_called()
    r.account_data = (time.monotonic()-61, 100, {})
    r.positions_at = time.monotonic()
    with pytest.raises(ValueError):
        r.health()
    k.heartbeat.beat.assert_not_called()
    r.account_data = (time.monotonic()-20, 100, {})
    r.jobs['account']._metrics['last_success_at'] = time.time()-20
    r.jobs['positions']._metrics['last_success_at'] = time.time()
    r.health()
    record = k.heartbeat.beat.call_args.args[0]
    assert 19 <= time.time() - record['last_successful_cycle_at'] <= 22
    assert record['cycle_basis'] == 'account_and_positions'


def test_actual_cycle_uses_prepared_scan_and_does_not_manage_positions(tmp_path, monkeypatch):
    from tests.test_hierarchical_admission import full_cycle_kernel
    k = full_cycle_kernel(tmp_path, config(), monkeypatch)
    receipt, frames = k._admission_plan()
    k._prepared_scan = (receipt, frames, k._peer_frames(k._symbol_roles.peers), {}, {})
    r = KernelRuntime(k)
    k._split_runtime = r
    k._model_generation = 0
    k._maintenance = r.maintenance
    r.account_data = (time.monotonic(), 1000, {'equity':1000, 'drawdown_pct':0, 'risk_state':'ok'})
    k._risk_step = lambda: pytest.fail('account work in decision cycle')
    k._admission_plan = lambda: pytest.fail('scan work in decision cycle')
    k._service_exposure = lambda *a: pytest.fail('position work in decision cycle')
    k._drain_panic = lambda: pytest.fail('commands in decision cycle')
    r.changed = lambda *a: ('input', True)
    result = k.cycle()
    assert result['decisions'] > 0
    k._manage_one.assert_not_called()
    k._detect_exchange_exits.assert_not_called()
    k.heartbeat.beat.assert_not_called()


def test_changed_input_key_ignores_scan_clock_but_includes_market_context():
    s = Snapshot('BTC/USDT', '2026-01-01T00:00:00+00:00', 10,
                 {'15m': pd.DataFrame({'ts':[1], 'close':[10.]})})
    kw = dict(generation=1, control='FROZEN', funding=None, oi=None, book={}, learning=[])
    first = input_fingerprint(s, **kw)
    s.ts = '2026-01-01T00:01:00+00:00'
    assert input_fingerprint(s, **kw) == first
    s.market = {'DXY':pd.DataFrame({'ts':[1], 'close':[5.]})}
    assert input_fingerprint(s, **kw) != first
    second = input_fingerprint(s, **kw)
    s.market['DXY'].loc[0,'close'] = 6
    assert input_fingerprint(s, **kw) != second


def test_stale_decision_cannot_reach_order_submission():
    calls=[]
    @coordinated
    def _try_enter(self, decision, snap, equity, count):
        calls.append(1)
    r=NS(execution_lock=threading.RLock())
    k=NS(_split_runtime=r)
    d=NS(skip_reason='')
    snap=NS(ts='2000-01-01T00:00:00+00:00')
    assert _try_enter(k,d,snap,100,0) is False
    assert not calls and 'stale' in d.skip_reason


def test_new_owner_hold_refuses_previously_approved_decision():
    calls=[]
    @coordinated
    def _try_enter(self, decision, snap, equity, count):
        calls.append(1)
    root=NS(_risk_step=lambda:(100,{'risk_state':'ok'}),
            state_machine=NS(can_enter=lambda:False))
    k=NS(_split_runtime=NS(execution_lock=threading.RLock(),kernel=root))
    d=NS(skip_reason='')
    snap=NS(ts=datetime.now(timezone.utc).isoformat())
    assert _try_enter(k,d,snap,100,0) is False
    assert not calls and 'current_risk' in d.skip_reason


def test_pending_scan_is_not_replaced_before_completion(tmp_path, monkeypatch):
    import trader.scheduling.kernel_runtime as module
    k,r = minimal(tmp_path)
    def view():
        return NS(_refresh_btc_context=lambda:None,cfg={},
                  _admission_plan=lambda:({},{}),_peer_frames=lambda p:{},
                  _symbol_roles=NS(peers=(),deep=()),population=[],feed=NS(),_market_aux={},_DERIVS_TTL=300)
    monkeypatch.setattr(module,'analysis_context',lambda runtime:view())
    r.market(); first=r.pending_scan
    r.market()
    assert r.pending_scan is first and r.scan_replaced == 0
    assert r.scan_in_flight and r.scan_busy_ticks == 1
    assert r.jobs['decisions'].snapshot()['pending']


def test_contexts_do_not_share_scan_caches_or_strategy_parameters(tmp_path):
    from trader.kernel import Kernel
    from trader.scheduling.coordination import analysis_context
    k=object.__new__(Kernel)
    k.cfg=config(); k.exchange=NS(); k.journal=NS(db_path=str(tmp_path/'j.db'))
    k.feed=NS();k._spec_exits={}; k._spec_rows=[]
    k.population=[(NS(id='s',params={'window':5},state='paper'),NS(params={'window':5}))]
    k.exits=NS(spec_exits={},genomes={});k._book_cache={'old':1}
    r=KernelRuntime(k)
    k._split_runtime=r
    a,b=analysis_context(r),analysis_context(r)
    a.population[0][0].params['window']=9
    a._book_cache['new']=2
    assert b.population[0][0].params['window']==5
    assert k.population[0][0].params['window']==5
    assert a.positioning_agent is not b.positioning_agent
    assert a.orchestrator is not b.orchestrator
    assert b._book_cache=={} and k._book_cache=={'old':1}
    assert a.journal is b.journal and a._split_runtime is r


def test_candle_pending_does_not_confuse_queue_acceptance_with_storage(tmp_path, monkeypatch):
    k,r=minimal(tmp_path)
    k.cfg={'timeframes':{'context':['1h','4h']}}
    k._exposure_symbols=lambda:set()
    k.universe=NS(symbols=lambda:['BTC/USDT'])
    stored={}
    k.feed=NS(reobservation_due=lambda *a:False,tip_complete=lambda symbol,tf,expected:stored.get((symbol,tf),0)>=expected,
              _queue_background_request=Mock(return_value=True))
    monkeypatch.setattr('trader.scheduling.kernel_runtime.time.time',lambda:1800000000.)
    r.candles()
    assert r.candle_stats['pending']==3 and r.candle_stats['confirmed_stored']==0
    assert r.candle_stats['queued']==3
    from trader.core.types import TF_MS
    for tf in ('15m','1h','4h'):
        stored[('BTC/USDT',tf)]=1800000000000//TF_MS[tf]*TF_MS[tf]-TF_MS[tf]
    r.candles()
    assert r.candle_stats['pending']==0 and r.candle_stats['confirmed_stored']==3


def test_failed_required_position_service_does_not_advance_health(tmp_path, monkeypatch):
    import trader.scheduling.kernel_runtime as module
    k,r=minimal(tmp_path)
    view=NS(_exposure_symbols=lambda:{'BTC/USDT'},_refresh_btc_context=lambda:None,
            _peer_frames=lambda p:{},_service_exposure=lambda *a:0)
    monkeypatch.setattr(module,'analysis_context',lambda _:view)
    with pytest.raises(ValueError,match='exposure_incomplete'):
        r.positions()
    assert r.positions_at is None


def test_candle_events_wake_held_positions_only_once_per_boundary(tmp_path):
    k,r=minimal(tmp_path)
    r.held_symbols={'BTC/USDT'}
    r.jobs['positions']._pending=None
    r.candle_acquired('ETH/USDT','15m','completed')
    assert not r.jobs['positions'].snapshot()['pending']
    r.candle_acquired('BTC/USDT','15m','completed')
    assert r.jobs['positions'].snapshot()['pending']
    r.jobs['positions']._pending=None
    r.candle_acquired('BTC/USDT','15m','completed')
    assert not r.jobs['positions'].snapshot()['pending']


def test_paper_close_wakes_learning_and_strategy_health():
    @coordinated
    def _manage_paper(self, snap):
        return ['closed-paper-trade']
    runtime=NS(execution_lock=threading.RLock(),trade_closed=Mock(),fill_event=Mock())
    assert _manage_paper(NS(_split_runtime=runtime),None)==['closed-paper-trade']
    runtime.trade_closed.assert_called_once_with()
    runtime.fill_event.assert_not_called()


def test_shutdown_refuses_new_entry_before_any_venue_read():
    @coordinated
    def _try_enter(self, *args):
        pytest.fail('entry during shutdown')
    runtime=NS(execution_lock=threading.RLock(),kernel=NS(_stop=True))
    decision=NS(skip_reason='')
    assert _try_enter(NS(_split_runtime=runtime),decision,None,0,0) is False
    assert 'stopping' in decision.skip_reason


def test_scan_does_not_consume_unrequested_derivative_inputs(tmp_path, monkeypatch):
    import trader.scheduling.kernel_runtime as module
    k,r=minimal(tmp_path)
    view=NS(_refresh_btc_context=lambda:None,cfg={},_admission_plan=lambda:({},{}),
            _peer_frames=lambda p:{},_symbol_roles=NS(peers=(),deep=()),population=[],feed=NS(),_market_aux={},_DERIVS_TTL=300)
    monkeypatch.setattr(module,'analysis_context',lambda _:view)
    now=int(time.time()*1000)
    r.derivative_inputs=({'BTC/USDT':.01},{'BTC/USDT':{'oi':10}},
        {'funding:BTC/USDT':{'available_at_ms':now-301000},
         'open_interest:BTC/USDT':{'available_at_ms':now-899000}})
    r.market()
    assert view._prepared_scan[3]=={}
    assert view._prepared_scan[4]=={}
    assert r.derivative_inputs[1]=={'BTC/USDT':{'oi':10}}  # independent producer untouched
    assert 'funding:BTC/USDT' not in view._market_aux


def test_reconciliation_failure_remains_visible_even_with_valid_position_snapshot(tmp_path, monkeypatch):
    from tests.test_hierarchical_admission import safety_kernel
    k=safety_kernel(tmp_path,config())
    k._detect_exchange_exits=Mock(side_effect=OSError('venue unavailable'))
    k._service_exposure(set(),{})
    assert k._exposure_errors and all(e=='reconcile:OSError' for e in k._exposure_errors)


def test_heartbeat_completion_timestamp_is_stable_between_real_completions(tmp_path):
    k, r = minimal(tmp_path)
    r.account_data = (time.monotonic(), 100, {})
    r.positions_at = time.monotonic()
    completed = time.time()-2
    r.jobs['account']._metrics['last_success_at'] = completed
    r.jobs['positions']._metrics['last_success_at'] = completed+1
    for _ in range(10):
        r.health()
    assert {call.args[0]['last_successful_cycle_at'] for call in k.heartbeat.beat.call_args_list} == {completed}


def test_derivative_history_waits_for_daily_boundary(tmp_path):
    k, r = minimal(tmp_path)
    history = r.jobs['derivative_history']
    assert history._pending is None
    assert history.calendar.hours == (3,)
    assert history.calendar.minute == 30
    assert r.jobs['derivative_inputs'].interval == 300
    assert r.jobs['derivatives'].interval == 900


def test_current_derivatives_never_backfill_or_block_input_refresh(tmp_path, monkeypatch):
    k, r = minimal(tmp_path)
    view = NS(journal=Mock(), cfg={'universe':{'majors':['BTC/USDT']}}, feed=Mock(),
              notifier=Mock(), universe=NS(symbols=lambda:['BTC/USDT']),
              _funding_map=Mock(return_value={'BTC/USDT':.01}),
              _oi_map=Mock(return_value={'BTC/USDT':{'oi':10}}), _market_aux={'receipt':1})
    monkeypatch.setattr('trader.scheduling.kernel_runtime.analysis_context', lambda rt:view)
    h = Mock(symbols=['BTC/USDT'])
    monkeypatch.setattr('trader.brain.harvester.Harvester', lambda *a:h)
    r.refresh_derivative_inputs()
    h.deriv.record_all.assert_not_called()
    assert r.derivative_inputs == ({'BTC/USDT':.01},{'BTC/USDT':{'oi':10}},{'receipt':1})
    r.derivatives()
    h.harvest_once.assert_not_called()
    h.deriv.record_all.assert_called_once()


def test_scan_start_waits_until_evaluation_finishes(tmp_path, monkeypatch):
    import trader.scheduling.kernel_runtime as module
    k, r = minimal(tmp_path)
    entered, release = threading.Event(), threading.Event()
    preparations = []
    def cycle():
        entered.set()
        assert release.wait(3)
        return {'decisions': 1}
    def view(runtime):
        preparations.append(1)
        return NS(_model_generation=0, cycle=cycle, cfg={},
            _admission_plan=lambda:({},{}), _symbol_roles=NS(peers=(),deep=()),
            population=[], feed=NS(), _market_aux={}, _DERIVS_TTL=300)
    monkeypatch.setattr(module, 'analysis_context', view)
    r.market()
    worker = threading.Thread(target=r.decisions)
    worker.start()
    try:
        assert entered.wait(1)
        for _ in range(3):
            r.market()
        assert len(preparations) == 1
        assert r.pending_scan is None and r.scan_in_flight
        assert r.scan_busy_ticks == 3
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not r.scan_in_flight
    r.market()
    assert len(preparations) == 2 and r.pending_scan is not None


@pytest.mark.parametrize('failure', ['prepare', 'evaluation', 'stale', 'stopped'])
def test_scan_reservation_released_on_failure_or_discard(tmp_path, monkeypatch, failure):
    import trader.scheduling.kernel_runtime as module
    k, r = minimal(tmp_path)
    k._last_cycle_s = 123
    def bad():
        raise ValueError('fixture')
    candidate = NS(_model_generation=0, cycle=bad, cfg={},
        _admission_plan=(bad if failure == 'prepare' else lambda:({},{})),
        _symbol_roles=NS(peers=(),deep=()), population=[], feed=NS(),
        _market_aux={}, _DERIVS_TTL=300)
    monkeypatch.setattr(module, 'analysis_context', lambda runtime:candidate)
    if failure == 'prepare':
        with pytest.raises(ValueError):
            r.market()
    else:
        if failure == 'stopped':
            r.jobs['decisions'].stop()
        r.market()
        if failure == 'evaluation':
            with pytest.raises(ValueError):
                r.decisions()
        elif failure == 'stale':
            candidate._prepared_at = time.monotonic() - 61
            r.decisions()
    assert not r.scan_in_flight and r.pending_scan is None
    assert k._last_cycle_s == 123


def test_completed_scans_busy_ticks_and_full_cycle_latency_are_reported_separately(tmp_path, monkeypatch):
    import trader.scheduling.kernel_runtime as module
    import trader.scheduling.lean_scan as lean
    k,r = minimal(tmp_path)
    k._last_cycle_s=None
    modes=[]
    from trader.data import market_provenance as mp
    monkeypatch.setattr(lean,'prepare',lambda view:modes.append(mp._CACHE_ONLY.get()) or {})
    def view():
        v=NS(_refresh_btc_context=lambda:None,cfg={},_admission_plan=lambda:({},{}),
             _symbol_roles=NS(peers=(),deep=('BTC/USDT',)),_model_generation=r.model[0],
             cycle=lambda:modes.append(mp._CACHE_ONLY.get()) or {'decisions':0},feed=NS())
        return v
    monkeypatch.setattr(module,'analysis_context',lambda runtime:view())
    r.market(); r.market()
    assert r.scan_busy_ticks==1 and r.scans_completed==0 and r.shortlisted=={'BTC/USDT'}
    r.decisions()
    assert r.scans_completed==1 and r.scans_discarded==0
    full=r.last_full_cycle
    assert set(full)=={'preparation_s','queue_s','evaluation_s','total_s'} and full['total_s']>=full['evaluation_s']
    assert modes==[True,True]               # preparation and evaluation are cache-only reads
    snap=r.snapshot()
    assert snap['scans_completed']==1 and snap['skipped_busy_scan_ticks']==1 and 'cache_hits' in snap['input_access']


def test_candle_checks_prioritise_held_then_shortlist_then_remaining_coverage(tmp_path, monkeypatch):
    k,r=minimal(tmp_path)
    k.cfg={'timeframes':{'context':[]}}
    k._exposure_symbols=lambda:{'HELD/USDT'}
    names=[f'C{i}/USDT' for i in range(70)]
    k.universe=NS(symbols=lambda:names+['SHORT/USDT'])
    r.shortlisted={'SHORT/USDT'}
    order=[]
    k.feed=NS(reobservation_due=lambda *a:False,tip_complete=lambda s,tf,e:order.append(s) or False,_queue_background_request=Mock(return_value=True))
    monkeypatch.setattr('trader.scheduling.kernel_runtime.time.time',lambda:1800000000.)
    r.candles()
    assert order[0]=='HELD/USDT' and 'SHORT/USDT' in order[:4] and len(order)==64
    first_pass=set(order); order.clear()
    r.candles()                                      # the rest of the coverage is not starved
    assert set(order)-first_pass


def test_history_work_yields_to_scan_and_position_cycles_at_boundaries(tmp_path, monkeypatch):
    import trader.scheduling.kernel_runtime as module
    k,r=minimal(tmp_path)
    k.cfg={}
    sleeps=[]
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps)==3:
            r.scan_in_flight=False
    monkeypatch.setattr(module.time,'sleep',sleep)
    r._history_yield()
    assert sleeps==[] and r.history_yields==0                  # idle: no waiting
    r.scan_in_flight=True
    r._history_yield()
    assert len(sleeps)==3 and r.history_yields==1              # current demand went first, then history resumed
