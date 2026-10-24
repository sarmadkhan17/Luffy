"""Offline acceptance for requirement-driven scans and trade-only persistence."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time

import numpy as np
import pandas as pd
import pytest

from tests.test_compile import _spec
from tests.test_entry_recovery import setup
from tests.entry_authority_fixtures import permission
from trader import attention_admission as A
from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType, Snapshot, Action
from trader.data import market_provenance as mp
from trader.engine.orchestrator import Orchestrator
from trader.kernel import Kernel
from trader.scheduling.lean_scan import prepare, run, BatchFeed
from trader.scheduling.scan_plan import build
from trader.strategy.compile import compile_spec
from trader.strategy.library import register_evaluator


def population(spec, *, eligible=True):
    compiled = compile_spec(spec)
    register_evaluator('spec:'+spec.id, compiled.to_evaluator())
    st = NS(id=spec.id, name=spec.name, is_trade_eligible=eligible)
    g = NS(strategy_id=spec.id, family='spec:'+spec.id, params={},
        markets=frozenset(spec.markets), regime_filter=frozenset(spec.regime_filter),
        symbols=frozenset(spec.universe.get('include', [])))
    return (st, g), (compiled.spec, compiled.data_requires)


def view_for(specs, symbols):
    populations, metadata = [], {}
    for spec in specs:
        pop, meta = population(spec)
        populations.append(pop); metadata[spec.id] = meta
    return NS(population=populations, _strategy_inputs=metadata, market_type=MarketType.FUTURES,
        cfg={'timeframes':{'execution':'15m','context':['1h','4h']}, 'risk':{}},
        _symbol_roles=NS(deep=symbols, peers=['PEER/USDT']))


def test_plan_only_eligible_strategy_dependencies_and_explicit_guards():
    a = _spec(id='a', timeframe='1h', entry_long="htf('1d', zscore(close, 450)) > 0", filters=[],regime_filter=[],
              universe={'include':['A/USDT']})
    b = _spec(id='b', entry_long="funding_z(24) > 0",filters=[],regime_filter=[],universe={'include':['B/USDT']})
    v = view_for([a,b], ['A/USDT','B/USDT','C/USDT'])
    plans, errors = build(v, v._symbol_roles.deep)
    assert not errors and set(plans)=={'A/USDT','B/USDT'}
    assert set(plans['A/USDT'].limits)=={'15m','1h','4h','1d'}
    assert plans['A/USDT'].limits['1d'] > 450
    assert not plans['A/USDT'].requires - {'ohlcv'}
    assert 'funding' in plans['B/USDT'].requires
    assert '1h' not in plans['B/USDT'].limits  # config alone cannot demand it
    assert not plans['A/USDT'].btc and not plans['B/USDT'].peers


def test_empty_or_inactive_population_performs_no_collection():
    v = view_for([], ['A/USDT'])
    v.feed = NS(fetch_ohlcv=Mock(side_effect=AssertionError('unexpected read')))
    assert prepare(v)=={}
    v._prepared_scan=(None,{}, {},{}, {})
    assert run(v)['no_strategy']==1
    v.feed.feed.fetch_ohlcv.assert_not_called()


def test_unknown_fitted_dependency_is_explicit_abstention():
    s = _spec(entry_long='fitted("'+'a'*64+'") > 0', filters=[])
    # Unknown fitted artifacts can fail during compile too; planner also
    # handles a previously loaded artifact subsequently becoming unavailable.
    v = view_for([], ['A/USDT'])
    st=NS(id=s.id,is_trade_eligible=True)
    g=NS(family='spec:'+s.id,markets=['futures'],symbols=[])
    v.population=[(st,g)];v._strategy_inputs={s.id:(s,('ohlcv',))}
    plans, errors = build(v,['A/USDT'])
    assert not plans and s.id in errors


def test_shared_refs_btc_peers_only_when_requested_and_once(monkeypatch):
    s = _spec(entry_long="ref('spx', ret(2)) > 0 and btc_ret(2) > 0 and xs_rank(ret(2)) > .5",filters=[],regime_filter=[])
    v=view_for([s],['A/USDT','B/USDT'])
    calls=[]
    v.feed=NS(fetch_ohlcv=lambda sym,tf,limit=400: calls.append((sym,tf,limit)))
    refs=[]
    monkeypatch.setattr('trader.strategy.spec_evidence.load_refs',lambda req,**kw: refs.append(req) or {})
    prepare(v)
    assert len(refs)==1 and refs[0]==['ref:spx']
    assert calls and any(sym=='BTC/USDT' for sym,_,_ in calls)
    assert len(calls)==len(set(calls))
    assert any(sym=='PEER/USDT' for sym,_,_ in calls)


def frame(tf, cut):
    step = {'15m':900000,'4h':14400000}[tf]
    end=(cut//step)*step
    ts=np.arange(end-400*step,end,step,dtype=np.int64)
    close=100+np.sin(np.arange(400)/8)
    raw=pd.DataFrame(dict(ts=pd.to_datetime(ts,unit='ms',utc=True),open=close,
        high=close+1,low=close-1,close=close,volume=np.full(400,100.)))
    return mp.annotate(raw,instrument_id='offline:futures:AUSDT',source='offline:fixture',
        kind='candle',received_ms=cut,timeframe=tf)


def offline_scan(tmp_path, monkeypatch, *, stale=False, actionable=False):
    symbols=[f'C{i}/USDT' for i in range(30)]
    s=_spec(id='lean',entry_long='close > 0' if actionable else 'close < 0',filters=[],regime_filter=[])
    v=view_for([s],symbols)
    j=Journal(tmp_path/'journal.db'); v.journal=j
    v.state_machine=NS(state=ControlState.FROZEN,refresh=lambda:ControlState.FROZEN,can_enter=lambda:False)
    v.risk=NS(daily_loss_block=.05);v.executor=NS(recovery_pending=lambda:False)
    v._split_runtime=NS(account_snapshot=lambda _: (1000,{'equity':1000,'drawdown_pct':0,'risk_state':'ok'}),
        decision_completed=Mock(),queue_portfolio=Mock())
    observed=dict(schema='broad-crypto.v1',selection='volume',source_cut_ms=1000,
        rows={sym:dict(symbol=sym,asset_class='CRYPTO',quality='VALID',available_at_ms=1000,
            quote_volume=100-i,missing_reasons=[]) for i,sym in enumerate(symbols)},
        peer_cohort=[],strategy_relevance={sym:['lean'] for sym in symbols},context_anchors=[],
        exposure_required=[],source_identity='offline',status='VALID')
    receipt=A.admit(observed,A.AdmissionPolicy(30,0,0,0),A.initial_state())
    batch=A.VerifiedAdmissionBatch(receipt)
    cut=int(time.time()*1000)
    frames={tf:frame(tf,cut) for tf in ('15m','4h')}
    reads=[]
    v.feed=NS(fetch_ohlcv=lambda sym,tf,limit=400: reads.append((sym,tf,limit)) or frames[tf],
        ttl={'15m':180,'4h':1800},_local=NS(multi_diagnostics={}))
    # Real Kernel snapshot construction and admission path, real strategy
    # compilation, real orchestrator, real temporary journal; no venue/API.
    k=Kernel.__new__(Kernel); k.__dict__.update(vars(v))
    k.universe=NS(_volume_receipts={},_listing_cache={})
    k._verified_admission_batch=batch
    k._snapshot_for=Kernel._snapshot_for.__get__(k)
    k._candidate_snapshot_for=Kernel._candidate_snapshot_for.__get__(k)
    k.orchestrator=Orchestrator([NS(name='unneeded',evaluate=lambda s:pytest.fail('analyst ran'))],j,cfg=k.cfg)
    monkeypatch.setattr('trader.engine.entry_authority.proposal_binding',lambda *a:None)
    k._scan_membership_receipts={}
    prepare(k)
    k._prepared_scan=(batch.receipt,{}, {},{}, {})
    if stale:
        monkeypatch.setattr(mp,'current_frame_status',lambda *a,**kw:(None,'stale'))
    queries=[]
    j._conn().set_trace_callback(queries.append)
    return k, reads, queries


@pytest.mark.parametrize('stale',[False,True])
def test_thirty_coin_real_pipeline_does_not_write_nontrades(tmp_path,monkeypatch,stale):
    k,reads,queries=offline_scan(tmp_path,monkeypatch,stale=stale)
    result=run(k)
    assert result['persisted_attempts']==0 and result['entries']==0
    assert result['decisions']==(0 if stale else 30)
    assert len(reads)==60 and len(set(reads))==60
    assert not any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','REPLACE','CREATE')) for sql in queries)
    assert not k.journal.query('SELECT id FROM decisions')
    assert not k.journal.query('SELECT id FROM cycles')
    assert not k.journal.query('SELECT id FROM brain_events')
    k._split_runtime.queue_portfolio.assert_not_called()


def test_order_evidence_hook_runs_before_submission_and_failure_prevents_orders(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    seen=[]
    def persist():
        assert not ex.sent
        seen.append('saved')
    d._persist_trade_evidence=persist
    e.open(d,2,2,95,110,'strategy','strategy',**kw)
    assert seen==['saved'] and ex.sent


def test_order_evidence_failure_never_submits(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    def fail():raise RuntimeError('storage unavailable')
    d._persist_trade_evidence=fail
    with pytest.raises(RuntimeError,match='storage unavailable'):
        e.open(d,2,2,95,110,'strategy','strategy',**kw)
    assert not ex.sent


def test_all_applicable_strategies_are_evaluated_without_analyst_work(tmp_path,monkeypatch):
    k,reads,_=offline_scan(tmp_path,monkeypatch)
    other=_spec(id='other',entry_long='close < -1',filters=[],regime_filter=[])
    pop, meta=population(other)
    k.population.append(pop);k._strategy_inputs[other.id]=meta
    prepare(k)
    seen=[]
    from trader.strategy import library
    real=library.evaluate
    def evaluate(g,snap,**kw):
        seen.append((g.strategy_id,snap.symbol))
        return real(g,snap,**kw)
    monkeypatch.setattr(library,'evaluate',evaluate)
    result=run(k)
    assert result['decisions']==30 and len(seen)==60
    assert all(sum(coin==symbol for _,coin in seen)==2 for symbol in k._symbol_roles.deep)
    assert result['persisted_attempts']==0


def test_ambiguous_submission_retains_intent_and_evidence(setup):
    from ccxt import RequestTimeout
    ex,j,e,d=setup
    kw=permission(e,d)
    called=[]
    d._persist_trade_evidence=lambda:called.append('evidence')
    def ambiguous(*a,**kw):
        assert called==['evidence']
        raise RequestTimeout('ambiguous fixture')
    ex.create_order=ambiguous
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert called==['evidence']
    assert j.query('SELECT * FROM execution_requests WHERE decision_id=?',(d.id,))
    assert e.recovery.pending()


def test_paper_entry_keeps_pre_entry_evidence_hook(tmp_path,monkeypatch):
    from tests.test_d7_paper_signal_execution import Run
    from trader.engine.paper import PaperExecutor
    original=PaperExecutor.enter
    events=[]
    def enter(self,d,snap,*a,**kw):
        def persist():
            assert not self.journal.query("SELECT * FROM versioned_paper_trades WHERE status='open'")
            events.append('saved')
        d._persist_trade_evidence=persist
        return original(self,d,snap,*a,**kw)
    monkeypatch.setattr(PaperExecutor,'enter',enter)
    r=Run(tmp_path,'long')
    assert events==['saved']  # refusal and duplicate do not persist
    assert r.dec._paper_entry_id==r.tid


def test_nontrade_cooldown_never_writes_control_event(tmp_path,monkeypatch):
    k,_,queries=offline_scan(tmp_path,monkeypatch,actionable=True)
    # A recent prior real trade decision causes a HOLD. Its existing durable
    # record is retained; this scan must not write another cooldown event.
    k.journal.query=lambda *a,**kw:[{'action':'BUY','ts':pd.Timestamp.now(tz='UTC').isoformat()}]
    result=run(k)
    assert result['decisions']==30 and result['persisted_attempts']==0
    assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in queries)


def test_only_one_admitted_trade_saves_full_evidence_among_thirty_signals(tmp_path,monkeypatch):
    k,_,_=offline_scan(tmp_path,monkeypatch,actionable=True)
    k.state_machine=NS(state=ControlState.ACTIVE,refresh=lambda:ControlState.ACTIVE,can_enter=lambda:True)
    attempts=[]
    def enter(d,snap,*args):
        attempts.append(d.symbol)
        if len(attempts)>1:
            d.skip_reason='risk: fixture position limit'
            return False
        assert not k.journal.query('SELECT id FROM decisions')
        d._persist_trade_evidence()
        assert len(k.journal.query('SELECT id FROM decisions'))==1
        d.executed=True
        d.size_usdt=100
        return True
    k._try_enter=enter
    result=run(k)
    assert result['decisions']==30 and result['entries']==1 and result['persisted_attempts']==1
    assert len(attempts)==30
    assert len(k.journal.query('SELECT id FROM decisions'))==1
    assert k.journal.query('SELECT executed FROM decisions')[0]['executed']==1
    assert len(k.journal.query('SELECT id FROM cycles'))==1
    assert not k.journal.query('SELECT * FROM outcomes')  # actual executor schedules fills, not raw signals


def test_inactive_strategy_adds_no_data_requirement():
    s=_spec(entry_long="htf('1d', close) > 0",filters=[])
    v=view_for([s],['A/USDT'])
    v.population[0][0].is_trade_eligible=False
    plans,errors=build(v,['A/USDT'])
    assert not plans and not errors


def test_actual_volume_shortlist_from_440_never_reads_broad_history_or_persists(tmp_path,monkeypatch):
    import yaml
    from pathlib import Path
    from tests.test_hierarchical_admission import broad_kernel, CUT
    cfg=yaml.safe_load(Path('config.yaml').read_text())
    cfg['attention']['selection']='volume'
    cfg['attention']['admission']=dict(max_deep_slots=30,event_reserve=0,exploration_reserve=0,minimum_slots=0)
    k=broad_kernel(tmp_path,cfg,n=440)
    tickers=k.universe.ex.fetch_tickers.return_value
    expected=sorted(tickers,key=lambda symbol:(-tickers[symbol]['quoteVolume'],symbol))[:30]
    k._lean_scan=True
    k._exposure_symbols=lambda:[]
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:CUT/1000)
    writes=[]
    k.journal._conn().set_trace_callback(writes.append)
    receipt,_=k._admission_plan()
    assert receipt['admitted_symbols']==expected
    assert len(receipt['roles']['broad_crypto'])==440
    prepare(k)
    assert not k._scan_plan
    k.feed.feed.cached_ohlcv.assert_not_called()
    k.feed.feed.fetch_ohlcv.assert_not_called()
    assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE')) for q in writes)


def test_learning_application_is_independent_and_requests_new_model_batch(tmp_path,monkeypatch):
    import threading
    from trader.scheduling.kernel_runtime import KernelRuntime
    events=[]
    runtime=KernelRuntime.__new__(KernelRuntime)
    runtime.decision_lock=threading.RLock()
    runtime.kernel=NS(journal=object(),cfg={})
    runtime.reload_model=lambda: events.append('reload')
    runtime.jobs={'market':NS(trigger=lambda:events.append('rescan'))}
    def checkpoint(*a,**kw):
        assert runtime.decision_lock._is_owned()
        return [{'result':'APPLIED'}]
    monkeypatch.setattr('trader.learning.runtime.checkpoint',checkpoint)
    assert runtime.apply_learning()==[{'result':'APPLIED'}]
    assert events==['reload','rescan']


def test_shared_candle_windows_read_maximum_once_then_slice():
    reads=[]
    raw=pd.DataFrame({'close':range(600)})
    feed=BatchFeed(NS(fetch_ohlcv=lambda sym,tf,limit:reads.append(limit) or raw.tail(limit)))
    feed.limits['BTC/USDT','15m']=600
    first=feed.fetch_ohlcv('BTC/USDT','15m',limit=400)
    second=feed.fetch_ohlcv('BTC/USDT','15m',limit=600)
    assert reads==[600] and len(first)==400 and len(second)==600
    first.iloc[-1,0]=-1
    assert second.iloc[-1,0]==599


def test_hold_scan_never_requests_writable_entry_probe(tmp_path,monkeypatch):
    k,_,queries=offline_scan(tmp_path,monkeypatch,actionable=False)
    k.state_machine=NS(state=ControlState.ACTIVE,refresh=lambda:ControlState.ACTIVE,
        can_enter=lambda:pytest.fail('entry probe belongs at submission boundary'))
    monkeypatch.setattr('trader.observability.safety.entry_refusal',
        lambda journal,probe=False: pytest.fail('writable probe in scan') if probe else None)
    result=run(k)
    assert result['decisions']==30 and result['persisted_attempts']==0
    assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in queries)


def test_analysis_never_needs_account_refresh_before_a_trade(tmp_path,monkeypatch):
    k,_,_=offline_scan(tmp_path,monkeypatch)
    k._split_runtime.account_snapshot=lambda *a:pytest.fail('account refresh in analysis')
    result=run(k)
    assert result['decisions']==30 and result['equity'] is None
    assert not result['entries'] and not result['persisted_attempts']


def test_slow_worker_diagnostics_are_bounded_locations_only(tmp_path):
    import threading
    from tests.test_split_kernel_runtime import minimal
    _,runtime=minimal(tmp_path)
    worker=runtime.jobs['account']
    worker._thread=threading.current_thread()
    worker._running_mono=time.monotonic()-31
    worker._metrics['running_since']=time.time()-31
    locations=runtime.snapshot()['slow_worker_stacks']['account']
    assert 0<len(locations)<=16
    assert all(set(frame)=={'file','line','function'} for frame in locations)


def test_stop_request_ends_scan_between_coins(tmp_path,monkeypatch):
    k,reads,_=offline_scan(tmp_path,monkeypatch)
    k._split_runtime.kernel=NS(_stop=True)
    result=run(k)
    assert result['interrupted'] and result['decisions']==0 and not reads
