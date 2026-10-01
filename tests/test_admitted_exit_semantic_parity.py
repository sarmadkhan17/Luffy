"""Factory exit semantic matrix and production-adapter causal replay.

All observations/fills are deterministic fixtures; no real venue capability.
"""
import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

from trader.core.types import ControlState, MarketType, Position, Side, TF_MS
from trader.engine.exits import ExitEngine, SpecExit
from trader.engine.executor import Executor
from trader.engine.paper import PaperExecutor, TABLE
from trader.strategy import exit_policy as E, factory_handoff as F
from trader.strategy.compile import compile_spec
from trader.strategy.geometries import GEOS
from trader.strategy.spec import ExitSpec, StrategySpec
from trader.strategy.vector_backtest import _trade, simulate, trade_table, walk_table
from tests.test_trade_provenance import Venue
from tests.test_versioned_paper_execution import setup, frames, snap, append, decision
from tests.test_strategy_factory_handoff import T0, DAY
from tests.test_cross_stage_authority import _candidate
from trader.core.journal import Journal

MODES = {
    'ATR stop': ExitSpec(stop={'kind':'atr','mult':3.0},target={'kind':'rr','v':3.0},time={'max_bars':2}),
    '% stop': ExitSpec(stop={'kind':'pct','v':0.02},time={'max_bars':2}),
    'RR target': ExitSpec(stop={'kind':'atr','mult':3.0},target={'kind':'rr','v':3.0},time={'max_bars':2}),
    'ATR target': ExitSpec(target={'kind':'atr','mult':3.0},time={'max_bars':2}),
    '% target': ExitSpec(target={'kind':'pct','v':0.05},time={'max_bars':2}),
    'target none': ExitSpec(target={'kind':'none'},time={'max_bars':2}),
    'trail none': ExitSpec(trail={'kind':'none'},time={'max_bars':2}),
    'max_bars': ExitSpec(time={'max_bars':2}),
    'ATR trailing': ExitSpec(trail={'kind':'atr','mult':4.0,'arm_at_r':1.0}),
    'signal_exit': ExitSpec(signal_exit='ret(1) > 0'),
    'swing stop': ExitSpec(stop={'kind':'swing','lookback':20}),
    'partial exit': ExitSpec(target={'kind':'rr','v':2.0,'partial':0.5}),
    'flip exit': ExitSpec(time={'max_bars':2,'flip':True}),
    'regime exit': ExitSpec(time={'max_bars':2,'regime':'RANGING'}),
}
SUPPORTED = [name for name, ex in MODES.items() if E.unsupported(ex) is None]
REFUSED = [name for name in MODES if name not in SUPPORTED]
MATRIX_PATH = Path('docs/superpowers/reports/2026-10-01-admitted-exit-semantic-parity-r1-matrix.json')


def acceptance_matrix():
    return {'exit_semantics_id':E.EXIT_SEMANTICS_ID, 'observation_contract':E.CONTRACT,
            'rows':[{'behavior':name, 'research':'SUPPORTED' if name in SUPPORTED else 'REFUSED_ADMISSION',
                     'paper':'SUPPORTED' if name in SUPPORTED else 'REFUSED',
                     'live':'SUPPORTED' if name in SUPPORTED else 'REFUSED',
                     'parity':'PASS' if name in SUPPORTED else 'REFUSED',
                     'status':'SUPPORTED' if name in SUPPORTED else 'REFUSED'} for name in MODES]}


def test_machine_matrix_is_current():
    assert json.loads(MATRIX_PATH.read_text()) == acceptance_matrix()


@pytest.mark.parametrize('name', REFUSED)
def test_unsupported_modes_refuse_admission_install_and_probation(tmp_path, monkeypatch, name):
    bad = MODES[name]
    assert E.unsupported(bad)
    with pytest.raises(ValueError,match='unsupported_versioned_exit'):
        E.initialize(bad,100,1,'long',0,1000)
    monkeypatch.setitem(GEOS, 'fixed', copy.deepcopy(bad))
    j=Journal(tmp_path/'j.db')
    _candidate(j,'fixed',state='referee_passed')
    h=j.query('SELECT hash FROM research_candidates')[0]['hash']
    with pytest.raises(F.HandoffRefused,match='unsupported_versioned_exit'):
        F.create_version(j,{}, {'kind':'research_candidate','hash':h},at_ms=T0)
    assert not j.query('SELECT * FROM strategy_versions')
    # Install/probation are independently defensive for prior-era versions.
    from trader.engine.paper import unsupported
    assert unsupported(type('Spec',(),{'exit':bad})()) == E.unsupported(bad)
    assessment=F._assess(j,{}, {'spec':{**_minimal_spec_dict(), 'exit':asdict(bad)}},T0,T0+DAY)
    assert assessment['status']=='UNSUPPORTED_PAPER_EXIT'


@pytest.mark.parametrize('side', ['long','short'])
@pytest.mark.parametrize('name', SUPPORTED)
@pytest.mark.parametrize('event', ['stop','target','both','gap_stop','gap_target','time','hold'])
def test_cross_adapter_replay(tmp_path, monkeypatch, name, side, event):
    ex=copy.deepcopy(MODES[name])
    monkeypatch.setitem(GEOS,'fixed',ex)
    j,cfg,v=setup(tmp_path)
    # Frozen version must ignore all global trail/TP1/target defaults.
    cfg['risk'].update(trailing_atr_mult=0.0001,tp1_fraction=0.99,tp1_r_mult=0.01,take_profit_atr_mult=0.01)
    paper=PaperExecutor(j,cfg)
    df=frames(v);df.ts+=pd.Timedelta(hours=4)
    if side=='short':
        for i in range(7): df=append(df,120+20*i)
    initial=snap(df)
    initial.price+=0.1  # execution movement cannot shift reference geometry
    d=decision(v,initial)
    assert d.action.value==('BUY' if side=='long' else 'SELL')
    pid=paper.enter(d,initial,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=paper.open_positions()[0]
    identity=json.loads(p['entry_identity_json'])
    policy,state=E.decode(p['exit_state_json'])
    assert policy.reference==float(df.close.iloc[-1]) and p['entry_price']!=policy.reference
    identity.update(exec_mode='live',initial_risk=policy.initial_r)
    # An already-open fixture tests live exits; no entry authority is activated.
    j.add_trade(Position(id='live',symbol=p['symbol'],side=Side(side),amount=p['amount'],
        entry_price=p['entry_price']+0.2,stop_loss=p['stop_loss'],take_profit=p['take_profit'],
        strategy_id=v['strategy_id'],market_type='futures',exec_mode='live',
        notional_usdt=p['notional_usdt'],entry_identity=identity))
    assert j.query("SELECT initial_risk FROM trades WHERE id='live'")[0]['initial_risk']==policy.initial_r
    venue=Venue()
    executor=Executor(venue,j,cfg,MarketType.FUTURES)
    live=ExitEngine(venue,j,executor,cfg,spec_exits={v['strategy_id']:SpecExit.from_spec(StrategySpec.from_dict(v['spec']),versioned=True)})
    entry_i=len(df)-1
    sign=1 if side=='long' else -1
    if not policy.target and event in ('target','both','gap_target'):
        event='hold'  # No target: favourable movement must never invent TP1.
    if event in ('time','hold'):
        # Profit exceeds .5R while strictly inside the fixed target.
        close=policy.reference+sign*policy.initial_r*0.7
        count=2 if event=='time' else 1
        for _ in range(count):
            df=append(df,close,high=max(policy.reference,close)+0.01,low=min(policy.reference,close)-0.01)
            df.loc[df.index[-1],'open']=policy.reference
    else:
        close=policy.stop if event in ('stop','gap_stop','both') else policy.target
        if event.startswith('gap'):
            close+=(-sign if event=='gap_stop' else sign)*policy.initial_r*0.1
        high=max(policy.reference,close)+0.01;low=min(policy.reference,close)-0.01
        if event=='both':
            high=max(policy.stop,policy.target)+0.01;low=min(policy.stop,policy.target)-0.01
        df=append(df,close,high=high,low=low)
        if not event.startswith('gap'):
            df.loc[df.index[-1],'open']=policy.reference
    compiled=compile_spec(StrategySpec.from_dict(v['spec']),exit_semantics_id=E.EXIT_SEMANTICS_ID)
    from trader.agents.indicators import atr_series
    transitions=[]; research=[]
    _trade(entry_i,side,df,df.close.to_numpy(),df.high.to_numpy(),df.low.to_numpy(),
        atr_series(df).to_numpy(),compiled.spec.exit,0.4,0.001,ex.time['max_bars'],0,1,
        None,None,0.0001,240,research,p['amount'],transitions)
    # Consume each observation separately through actual production adapters.
    for i,expected in enumerate(transitions,entry_i+1):
        observation_snap=snap(df.iloc[:i+1].copy())
        venue.px=observation_snap.price+0.3  # live fill differs, intent does not
        closed=paper.manage(observation_snap)
        trade=j.query("SELECT * FROM trades WHERE id='live'")[0]
        reason=live.manage(trade,observation_snap.price,999,-sign, snapshot=observation_snap)
        paper_row=j.query(f'SELECT * FROM {TABLE} WHERE id=?',(pid,))[0]
        live_state=j.query("SELECT state_json FROM versioned_live_exit_states WHERE trade_id='live'")[0]['state_json']
        assert E.decode(paper_row['exit_state_json'])==(policy,expected.state)
        assert E.decode(live_state)==(policy,expected.state)
        assert bool(closed)==expected.due and reason==expected.reason
        assert paper_row['stop_loss']==expected.stop and paper_row['take_profit']==expected.target
        assert expected.quantity==(p['amount'] if expected.due else 0)
        if expected.due:
            assert len(venue.sent)==1 and venue.sent[0][3]==p['amount']
            assert venue.sent[0][4]=={'reduceOnly':True}
            booking=json.loads(j.query("SELECT payload FROM trade_accounting_bookings WHERE trade_id='live' ORDER BY id DESC")[0]['payload'])
            assert booking['evidence']['exit_authority']=='STRATEGY'
            assert booking['evidence']['strategy_exit']['intended_quantity']==expected.quantity
            assert booking['evidence']['strategy_exit']['version_id']==v['version_id']
        # Duplicate observation and adapter reconstruction are idempotent.
        assert PaperExecutor(j,cfg).manage(observation_snap)==[]
        assert live.manage(trade,observation_snap.price,999,sign,snapshot=observation_snap) is None
        live=ExitEngine(venue,j,executor,cfg,spec_exits=live.spec_exits)
    if event=='both':
        assert research[0]['state']['state']['ambiguous'] is True
        assert research[0]['state']['state']['reason']=='stop'
    if event.startswith('gap'):
        assert research[0]['state']['state']['gap'] is True
    assert F.live_entry_block(j,v['strategy_id']).startswith('version_not_live_authorized')


def test_floor_symmetry_duplicate_and_closed_evidence():
    ex=ExitSpec(stop={'kind':'pct','v':0.001},time={'max_bars':2})
    for side in ('long','short'):
        policy,state=E.initialize(ex,100,0.001,side,0,1000)
        assert policy.initial_r==0.4
        result=E.advance(policy,state,E.Observation(1000,100,100.1,99.9,100),2)
        assert E.advance(policy,result.state,E.Observation(1000,100,100.1,99.9,100),2)==result
        with pytest.raises(ValueError,match='consecutive_closed_bar'):
            E.advance(policy,state,E.Observation(1000,100,100.1,99.9,100,False),2)
        with pytest.raises(ValueError,match='consecutive_closed_bar'):
            E.advance(policy,state,E.Observation(2000,100,100.1,99.9,100),2)


def test_old_semantics_evidence_refused_without_version_identity_change(tmp_path,monkeypatch):
    j,cfg,v=setup(tmp_path)
    initial_version=v['version_id']
    changed=E.EXIT_SEMANTICS_ID+'.material-change'
    monkeypatch.setattr(E,'EXIT_SEMANTICS_ID',changed)
    assert F.load_version(j,initial_version)['version_id']==initial_version
    with pytest.raises(F.HandoffRefused,match='exit_semantics_mismatch'):
        F.verify_validation(j,v)
    with pytest.raises(F.HandoffRefused,match='exit_semantics_mismatch'):
        F.verify_install(j,v)
    with pytest.raises(F.HandoffRefused,match='exit_semantics_mismatch'):
        F.evaluate_probation(j,cfg,v['version_id'],at_ms=T0+DAY)


@pytest.mark.parametrize('reason,authority',[('panic','RISK'),('sl_fill','PROTECTION'),('tp_fill','PROTECTION'),('reconciled_ghost','RECOVERY')])
def test_external_authority_not_strategy(tmp_path,reason,authority):
    j=Journal(tmp_path/'j.db')
    j.add_trade(Position(id='p',symbol='BTC/USDT',side=Side.LONG,amount=1,entry_price=100,stop_loss=95,notional_usdt=100))
    j.close_trade('p',94,-6,reason)
    receipt=json.loads(j.query('SELECT payload FROM trade_accounting_bookings ORDER BY id DESC')[0]['payload'])
    assert receipt['evidence']['exit_authority']==authority
    assert not receipt['evidence'].get('strategy_exit')


def test_research_consumers_use_canonical_trade_definition(tmp_path):
    j,cfg,v=setup(tmp_path)
    compiled=compile_spec(StrategySpec.from_dict(v['spec']),exit_semantics_id=E.EXIT_SEMANTICS_ID)
    assert compiled.spec.exit._exit_semantics_id==E.EXIT_SEMANTICS_ID
    # The same compiled ExitSpec goes through rolling, null, referee table and vector.
    df=frames(v)
    import numpy as np
    lo=np.zeros(len(df),bool);lo[210]=True
    sh=np.zeros(len(df),bool)
    risk={'risk_per_trade_pct':0.5,'bar_minutes':240,'slippage_atr_frac':0.2}
    table=trade_table(df,compiled.spec.exit,risk)
    walk=walk_table(table,lo,sh,risk)
    evidence=[]; result=simulate(lo,sh,df,compiled.spec.exit,risk,exit_evidence_out=evidence)
    assert result.trades==len(walk)
    assert result.pnl_usdt==pytest.approx(sum(r*10 for _,_,r in walk))
    assert evidence[0]['exit_semantics_id']==E.EXIT_SEMANTICS_ID
    # Candidate construction must never mutate the legacy geometry singleton.
    assert not hasattr(GEOS['fixed'],'_exit_semantics_id')


def test_censored_research_position_reserves_occupancy():
    import numpy as np
    n=214
    px=np.full(n,100.0);px[211]=105;px[212:]=94
    df=pd.DataFrame({'ts':pd.date_range('2026-01-01',periods=n,freq='15min',tz='UTC'),
                     'open':px,'high':px+.1,'low':px-.1,'close':px,'volume':1})
    spec=type('Spec',(),{'timeframe':'15m', 'exit':ExitSpec(stop={'kind':'pct','v':.1},target={'kind':'rr','v':1})})()
    E.bind_research(spec)
    lo=np.zeros(n,bool);lo[210:212]=True;sh=np.zeros(n,bool)
    risk={'risk_per_trade_pct':.5,'bar_minutes':15,'slippage_atr_frac':0}
    evidence=[]
    result=simulate(lo,sh,df,spec.exit,risk,exit_evidence_out=evidence)
    assert result.trades==0 and evidence[0]['censored'] is True
    assert walk_table(trade_table(df,spec.exit,risk),lo,sh,risk)==[]


def test_retirement_and_partial_fill_retry_preserve_frozen_intent(tmp_path):
    j,cfg,v=setup(tmp_path)
    paper=PaperExecutor(j,cfg);df=frames(v);df.ts+=pd.Timedelta(hours=4)
    initial=snap(df)
    pid=paper.enter(decision(v,initial),initial,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=paper.open_positions()[0];identity=json.loads(p['entry_identity_json'])
    policy,state=E.decode(p['exit_state_json'])
    identity.update(exec_mode='live',initial_risk=policy.initial_r)
    j.add_trade(Position(id='live',symbol=p['symbol'],side=Side(p['side']),amount=p['amount'],
        entry_price=p['entry_price'],stop_loss=p['stop_loss'],take_profit=p['take_profit'],
        strategy_id=v['strategy_id'],market_type='futures',exec_mode='live',
        notional_usdt=p['notional_usdt'],entry_identity=identity))
    class PartialVenue(Venue):
        def create_order(self,*args,**kwargs):
            order=super().create_order(*args,**kwargs)
            if self.n==1:
                order['filled']*=0.4
                for fill in self.fills: fill['amount']*=0.4
            return order
    venue=PartialVenue();venue.px=p['take_profit']
    executor=Executor(venue,j,cfg,MarketType.FUTURES)
    def engine():
        # Missing population cache must not send an exact version down legacy exits.
        return ExitEngine(venue,j,executor,cfg)
    df=append(df,p['take_profit']);observation=snap(df)
    trade=j.query("SELECT * FROM trades WHERE id='live'")[0]
    assert engine().manage(trade,observation.price,1,-1,snapshot=observation) is None
    remaining=j.query("SELECT * FROM trades WHERE id='live'")[0]['amount']
    assert remaining==pytest.approx(p['amount']*.6)
    pending=j.query("SELECT state_json FROM versioned_live_exit_states")[0]['state_json']
    # Retire both the strategy row and mutable candidate admission state.
    with j._tx() as c:
        c.execute("UPDATE strategies SET state='retired' WHERE id=?",(v['strategy_id'],))
        c.execute("UPDATE research_candidates SET state='refused'")
    assert PaperExecutor(j,cfg).manage(observation)==[pid]
    assert engine().manage(trade,observation.price,1,-1,snapshot=observation)=='target'
    assert len(venue.sent)==2 and venue.sent[-1][3]==pytest.approx(remaining)
    assert j.query("SELECT state_json FROM versioned_live_exit_states")[0]['state_json']==pending
    payload=json.loads(j.query("SELECT payload FROM trade_accounting_bookings WHERE trade_id='live' ORDER BY id DESC")[0]['payload'])
    assert payload['evidence']['strategy_exit']['intended_quantity']==pytest.approx(remaining)
    assert payload['evidence']['exit_authority']=='STRATEGY'
    assert engine().manage(trade,observation.price,1,-1,snapshot=observation) is None
    assert len(venue.sent)==2


def _minimal_spec_dict():
    from tests.test_spec import _valid
    return _valid().to_dict()


def test_historical_nonversioned_research_spec_keeps_legacy_semantics(tmp_path):
    spec=StrategySpec.from_dict(_minimal_spec_dict())
    spec.provenance={'source_kind':'research'}
    compiled=compile_spec(spec)
    assert not hasattr(compiled.spec.exit,'_exit_semantics_id')
    j=Journal(tmp_path/'j.db');j.upsert_spec(spec,state='paper')
    loaded=j.list_specs(['paper'])[0][1]
    assert not hasattr(compile_spec(loaded).spec.exit,'_exit_semantics_id')


def test_installed_version_reload_research_binding_and_kernel_retry_without_frame(tmp_path):
    from types import SimpleNamespace
    from trader.kernel import Kernel
    j,cfg,v=setup(tmp_path)
    loaded=j.list_specs(['paper'])[0][1]
    assert compile_spec(loaded).spec.exit._exit_semantics_id==E.EXIT_SEMANTICS_ID
    k=Kernel.__new__(Kernel);k.journal=j;k.cfg=cfg
    reached=[]
    k.exits=SimpleNamespace(manage=lambda trade,mark,atr,score,**kw: reached.append(kw['snapshot']) or 'target')
    k.notifier=SimpleNamespace(send=lambda message:None)
    observation=SimpleNamespace(price=100,dfs={},df=lambda tf:None)
    assert k._manage_one({'strategy_id':v['strategy_id'],'symbol':'BTC/USDT'},observation,0)=='target'
    assert reached==[observation]


def test_referee_worker_preserves_exact_book_exit_binding(tmp_path):
    from trader.research.referee import bound_book
    j,cfg,v=setup(tmp_path)
    spec=j.list_specs(['paper'])[0][1]
    binding=spec.exit._exit_binding
    payload={'book':[spec.to_dict()], 'book_exit_bindings':{spec.id:binding}}
    restored=bound_book(json.loads(json.dumps(payload)))[0]
    assert restored.to_dict()==spec.to_dict()
    assert restored.exit._exit_semantics_id==E.EXIT_SEMANTICS_ID
    assert restored.exit._exit_binding==binding
    legacy=bound_book({'book':[spec.to_dict()]})[0]
    assert not hasattr(legacy.exit,'_exit_semantics_id')
    altered=copy.deepcopy(payload);altered['book'][0]['exit']['stop']['mult']=4
    with pytest.raises(ValueError,match='book_exit_version_differs'):
        bound_book(altered)
    altered=copy.deepcopy(payload);altered['book_exit_bindings'][spec.id]['exit_semantics_id']='old'
    with pytest.raises(ValueError,match='book_exit_semantics_unbound'):
        bound_book(altered)


def test_vector_backtest_exposes_ohlc_ambiguity_and_exit_reason():
    n=212
    close=np.full(n,100.0)
    high=np.full(n,100.2);low=np.full(n,99.8)
    high[211],low[211]=105,95
    df=pd.DataFrame({'ts':pd.date_range('2026-01-01',periods=n,freq='4h',tz='UTC'),
        'open':close,'high':high,'low':low,'close':close,'volume':1.0})
    ex=copy.deepcopy(GEOS['fixed'])
    spec=type('S',(),{'timeframe':'4h','exit':ex})()
    E.bind_research(spec)
    ex=spec.exit
    lo=np.zeros(n,bool);lo[210]=True;sh=np.zeros(n,bool)
    result=simulate(lo,sh,df,ex,{'risk_per_trade_pct':.5,'bar_minutes':15,'slippage_atr_frac':0})
    assert result.exit_semantics_id==E.EXIT_SEMANTICS_ID
    assert result.exit_reasons=={'stop':1}
    assert result.ambiguous_exit_count==1
    assert result.censored_position_count==0
