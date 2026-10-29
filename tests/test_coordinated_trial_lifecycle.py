"""Synthetic numerical/Factory wiring only; no claim of a qualified strategy."""
from copy import deepcopy
import numpy as np
import pytest
from tests.test_compose_core import spec as graph_spec
from tests.test_hedge_qualification import result as numerical_fixture
from trader.core.journal import Journal
from trader.research.closure import ClosureRefused,digest
from trader.research.closure import coordinated_trial as T
from trader.research.compose import graph,hedge as H
from trader.research.compose.managed_hedge import VERSION
from trader.strategy import factory_handoff as F
from trader.engine import coordinated_trials as runtime
from trader.engine.coordinated_paper import CoordinatedPaper


@pytest.fixture
def packet(monkeypatch):
    # A small explicit synthetic envelope stand-in. Economic comparisons are
    # re-derived by real assess(), not granted by the mock.
    monkeypatch.setattr(T.manifest,'verify_envelope',lambda e:None)
    hs=H.make_spec(purpose='protective_beta',primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},
                   fee_bps=5.,ratio={'method':'fixed','value':1.},management=dict(version=VERSION,
                       entry='PRIMARY_FIRST',pnl_basis='COMBINED_NET_EQUITY',
                       activate=dict(kind='combined_loss_pct',value=2.),stop_loss_pct=15.,take_profit_pct=5.))
    c=graph.make_candidate(graph_spec(question='synthetic objective fixture',structure='hedged',hedge=hs,short=None))
    combo=graph.realize(c,'fixture-envelope',entries_only=True)
    per=numerical_fixture();per.update(effective_spec=hs,funding_basis='RETAINED_SIGNED_HISTORY',cost_model={'fee_bps':5.})
    result=dict(stage='DEVELOPMENT',outcome='HEDGE_SUPPORTED',candidate_id=c['candidate_id'],envelope_id='fixture-envelope',
                combo=combo.as_dict(),combo_hash=combo.hash,hedge={'per_symbol':{'A/USDT':per}})
    result['result_id']=digest(result)
    env=dict(envelope_id='fixture-envelope',policy_class='CONFIGURED',risk={'taker_fee_pct':.05})
    return T.make(c,env,result)


def test_objective_supported_paper_factory_restart_execution_does_not_authorize_live(tmp_path,packet):
    journal=Journal(tmp_path/'journal.db');cfg={'risk':{},'research':{'closure':{'enabled':True,'paper_intake':True}}}
    issued=F.create_version(journal,cfg,dict(kind=T.KIND,packet=packet),at_ms=1)
    version=F.load_version(journal,issued['version_id'])
    assert F.verify_validation(journal,version)['semantics'].startswith('Numerical development')
    assert F.state_of(journal,version['version_id'])==F.VALIDATED
    install=F.record_coordinated_paper_install(journal,version['version_id'],at_ms=1,timing='NEXT_BAR_OPEN')
    assert F.state_of(journal,version['version_id'])==F.SHADOW
    assert F.record_coordinated_paper_install(Journal(journal.db_path),version['version_id'],at_ms=900,timing='NEXT_BAR_OPEN')==install
    assert F.multi_leg_block(version)=='multi_leg_requires_coordinated_execution'
    with pytest.raises(F.HandoffRefused,match='multi_leg_requires_coordinated_execution'):
        F.record_exact_install(journal,version['version_id'],at_ms=3)
    projection=journal.query('SELECT * FROM strategies')
    assert len(projection)==1 and projection[0]['kind']=='coordinated_spec' and projection[0]['state']=='paper'
    assert F.resolve_current_install(journal,version['strategy_id'])['install_id']==install['install']['install_id']
    from types import SimpleNamespace
    from trader.kernel import Kernel
    ordinary=SimpleNamespace(journal=journal,_load_spec_population=lambda:[])
    assert Kernel._load_population(ordinary)==[]  # never load the primary as an independent Genome
    first=install['install']['first_bar_ms'];step=14_400_000
    seen=[]
    def reader(feed,j,v,stamp):
        seen.append(stamp)
        price=97. if stamp>=first+2*step else 100.
        return dict(p=price,h=100.,po=price,ho=100.,signal=1,exit=False,ratio=1.,corr=1.,
                    funding={'primary':.0001,'hedge':.0001},primary_exit_observation=dict(
                        bar_ms=stamp,step_ms=step,open=100.,high=100.,low=100.,close=100.,atr=1.))
    assert runtime.run_once(journal,cfg,None,at_ms=first+step-1,reader=reader,allow_entry=True)==[]
    runtime.run_once(journal,cfg,None,at_ms=first+step,reader=reader,allow_entry=True)
    runtime.run_once(journal,cfg,None,at_ms=first+2*step,reader=reader,allow_entry=True)
    state=CoordinatedPaper(journal).state(install['book_id'])
    assert state['book']['pos']['primary']>0 and state['book']['pos']['hedge']==0
    runtime.run_once(journal,cfg,None,at_ms=first+3*step,reader=reader,allow_entry=True)
    runtime.run_once(journal,cfg,None,at_ms=first+4*step,reader=reader,allow_entry=True)
    state=CoordinatedPaper(journal).state(install['book_id'])
    assert state['book']['pos']['hedge']<0
    assert seen==[first,first+step,first+2*step,first+3*step]
    assert not journal.query('SELECT * FROM trades')
    # Same timer cut does not replay an event or submit another fill.
    assert runtime.run_once(journal,cfg,None,at_ms=first+4*step,reader=reader,allow_entry=True)==[]


def test_test_only_or_false_support_or_missing_actual_costs_cannot_enter(packet):
    for edit in ('TEST_ONLY','bad_objective','missing_funding','low_fee'):
        p=deepcopy(packet)
        if edit=='TEST_ONLY':p['envelope']['policy_class']='TEST_ONLY'
        elif edit=='bad_objective':p['result']['hedge']['per_symbol']['A/USDT']['comparators']['hedged']['max_drawdown_pct']=30
        elif edit=='missing_funding':p['result']['hedge']['per_symbol']['A/USDT']['funding_basis']='UNAVAILABLE'
        else:p['result']['hedge']['per_symbol']['A/USDT']['effective_spec']['fee_bps']=0
        r=p['result'];r['result_id']=digest({k:v for k,v in r.items() if k!='result_id'})
        p['trial_id']=digest({k:v for k,v in p.items() if k!='trial_id'})
        with pytest.raises(ClosureRefused):T.verify(p)


def test_real_cached_reader_uses_exact_cut_and_declared_native_frames(tmp_path,packet,monkeypatch):
    from types import SimpleNamespace
    import pandas as pd
    from trader.data import market_provenance as mp
    from trader.strategy import spec_evidence as S
    j=Journal(tmp_path/'j.db');v=F.create_version(j,{},dict(kind=T.KIND,packet=packet),at_ms=1)
    version=F.load_version(j,v['version_id'])
    step=14_400_000;n=120
    closes=np.r_[100*np.exp(np.cumsum(np.sin(np.arange(n-1))*0.01)),101.]
    df=pd.DataFrame(dict(ts=pd.date_range('2024-01-01',periods=n,freq='4h',tz='UTC'),
                        open=closes,high=closes*1.01,low=closes*.99,close=closes,volume=np.ones(n)))
    df['available_at_ms']=mp.ms(df['ts'])+step;df['quality']='VALID';df['bar_state']='FINAL';df.attrs['timeframe']='4h'
    reads=[]
    def cached(symbol,tf,limit,as_of_ms):
        reads.append((symbol,tf,limit,as_of_ms));return df.tail(limit).copy()
    monkeypatch.setattr(S,'load_refs',lambda *a,**kw:{})
    monkeypatch.setattr(S,'load_derivs',lambda *a,**kw:{})
    monkeypatch.setattr(S,'funding_series',lambda symbol,frame:np.full(len(frame),.0001))
    stamp=int(mp.ms(df['ts'])[-1]);obs=runtime.observation(SimpleNamespace(cached_ohlcv=cached),j,version,stamp)
    assert obs['signal_cut_ms']==stamp+step and len(reads)==2
    assert all(r[2]<6000 and r[3]==stamp+step for r in reads)
    assert obs['funding']['primary']==pytest.approx(.00005)
    # Ordinary REST delivery occurs after a candle closes. A prospective
    # decision may use that received candle, then wait for a later quote;
    # it cannot claim the already-passed candle open as a fill.
    df.loc[df.index[-1],'available_at_ms']=stamp+step+1000
    def late_cached(symbol,tf,limit,as_of_ms):
        reads.append((symbol,tf,limit,as_of_ms))
        return df[df['available_at_ms']<=as_of_ms].tail(limit).copy()
    with pytest.raises(ClosureRefused,match='closed_cut_unavailable'):
        runtime.observation(SimpleNamespace(cached_ohlcv=late_cached),j,version,stamp)
    late=runtime.observation(SimpleNamespace(cached_ohlcv=late_cached),j,version,stamp,decision_ms=stamp+step+1001)
    assert late['decision_basis']=='RECEIVED_INPUTS_BEFORE_QUOTE_DECISION'
    assert reads[-1][3]==stamp+step+1001


def test_input_gap_is_recorded_unknown_and_does_not_retry_one_cut_forever(tmp_path,packet):
    journal=Journal(tmp_path/'journal.db');cfg={'research':{'closure':{'enabled':True,'paper_intake':True}}}
    version=F.create_version(journal,cfg,dict(kind=T.KIND,packet=packet),at_ms=1)
    install=F.record_coordinated_paper_install(journal,version['version_id'],at_ms=1,timing='NEXT_BAR_OPEN')
    first=install['install']['first_bar_ms'];step=14_400_000
    def unavailable(*args):raise ClosureRefused('required_data_unavailable')
    result=runtime.run_once(journal,cfg,None,at_ms=first+step,reader=unavailable)
    assert result[0]['status']=='PAPER_INPUT_GAP'
    state=CoordinatedPaper(journal).state(install['book_id'])
    assert state['bar']==0 and not any(state['book']['pos'].values())
    result=runtime.run_once(journal,cfg,None,at_ms=first+2*step,reader=unavailable)
    assert result[0]['status']=='PAPER_INPUT_GAP'
    assert CoordinatedPaper(journal).state(install['book_id'])['bar']==1


def test_relative_value_uses_return_objective_not_risk_label(packet):
    p=deepcopy(packet);per=p['result']['hedge']['per_symbol']['A/USDT']
    hs=H.make_spec(**{k:v for k,v in per['effective_spec'].items() if k not in ('purpose','objective')},purpose='relative_value')
    per['effective_spec']=hs;per['objective']='net_return_pct'
    c=deepcopy(p['candidate']);c.pop('candidate_id');c.pop('graph_id',None)
    c['hedge']=hs;c['structure']='pair'
    c=graph.make_candidate(c);p['candidate']=c
    combo=graph.realize(c,p['envelope']['envelope_id'],entries_only=True)
    p['result'].update(candidate_id=c['candidate_id'],combo=combo.as_dict(),combo_hash=combo.hash)
    per['comparators']['hedged']['net_return_pct']=3.
    per['comparators']['unhedged']['net_return_pct']=2.
    per['comparators']['reduced']['net_return_pct']=1.
    per['folds']=[dict(hedged=3.,unhedged=2.,reduced=1.)]*4
    r=p['result'];r['result_id']=digest({k:v for k,v in r.items() if k!='result_id'})
    p['trial_id']=digest({k:v for k,v in p.items() if k!='trial_id'})
    assert T.verify(p)[1]['purpose']=='relative_value'
    per['comparators']['hedged']['net_return_pct']=-1.
    r['result_id']=digest({k:v for k,v in r.items() if k!='result_id'});p['trial_id']=digest({k:v for k,v in p.items() if k!='trial_id'})
    with pytest.raises(ClosureRefused):T.verify(p)


def test_paper_data_demand_is_separate_from_venue_exposure(tmp_path,packet):
    journal=Journal(tmp_path/'j.db')
    issued=F.create_version(journal,{},dict(kind=T.KIND,packet=packet),at_ms=1)
    candles,derivs=runtime.input_needs(journal)
    assert candles=={'A/USDT':{'4h'},'B/USDT':{'4h'}}
    assert {x[:2] for x in derivs}=={('A/USDT','funding'),('B/USDT','funding')}
    assert not journal.query('SELECT * FROM trades')


def test_coordinated_consumer_has_no_factory_approval_or_live_call():
    import ast,inspect
    tree=ast.parse(inspect.getsource(runtime))
    attributes={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=='F'}
    assert attributes=={'ensure','load_version','state_of','VALIDATED','SHADOW','record_coordinated_paper_install','create_version','HEDGE_HQ_KIND','APPROVAL_REQUIRED','APPROVED_FIRST_LIVE','evaluate_probation','HandoffRefused'}
    # Venue order capability is absent; current inputs arrive through cache.
    imports={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
    assert not imports.intersection({'ccxt','requests','executor','control_fence'})


def test_probation_failure_cannot_rewrite_a_committed_paper_cut(tmp_path,packet,monkeypatch):
    journal=Journal(tmp_path/'journal.db');cfg={'research':{'closure':{'enabled':True,'paper_intake':True}}}
    issued=F.create_version(journal,cfg,dict(kind=T.KIND,packet=packet),at_ms=1)
    installed=F.record_coordinated_paper_install(journal,issued['version_id'],at_ms=1,timing='NEXT_BAR_OPEN')
    first=installed['install']['first_bar_ms'];step=14_400_000
    def reader(feed,j,v,stamp):
        index=(stamp-first)//step
        return dict(p=100.,h=100.,po=100.,ho=100.,signal=1 if index==0 else 0,
            exit=index>=2,ratio=1.,corr=1.,funding={'primary':0.,'hedge':0.},signal_cut_ms=stamp+step,
            primary_exit_observation=dict(bar_ms=stamp,step_ms=step,open=100.,high=100.,low=100.,close=100.,atr=1.))
    def failed(*a,**kw):raise F.HandoffRefused('fixture_probation_unavailable')
    monkeypatch.setattr(F,'evaluate_probation',failed)
    for index in range(4):
        result=runtime.run_once(journal,cfg,None,at_ms=first+(index+1)*step,reader=reader,allow_entry=True)
    assert result[0]['status']=='PAPER_OBSERVATION' and result[0]['probation']=='UNAVAILABLE'
    state=CoordinatedPaper(journal).state(installed['book_id'])
    assert state['bar']==3 and not any(state['book']['pos'].values())
    assert len(journal.query('SELECT * FROM coordinated_paper_events'))==4
