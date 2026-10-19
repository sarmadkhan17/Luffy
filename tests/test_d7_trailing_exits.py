"""Owner-approved next-bar ATR trail; synthetic observations only."""
import json

import numpy as np
import pytest

from trader.strategy import exit_policy as E
from trader.strategy.spec import ExitSpec
from tests.test_d7_strategy_parity import frame, STEP


def geometry(target=False, arm=1., mult=4.):
    return ExitSpec(stop={'kind':'atr','mult':2.},
        target={'kind':'rr','v':10.} if target else {'kind':'none'},
        trail={'kind':'atr','mult':mult,'arm_at_r':arm},time={'max_bars':20})


def observation(k, side, o,h,l,c, atr=1.):
    if side == 'short':
        o,h,l,c = 200-o,200-l,200-h,200-c
    return E.Observation(k*STEP,o,h,l,c,atr=atr)


@pytest.mark.parametrize('target',[False,True])
@pytest.mark.parametrize('side',['long','short'])
def test_next_bar_arming_tightening_gap_restart_and_target_retention(side,target):
    p,s = E.initialize(geometry(target),100,1,side,0,STEP)
    assert s.trail == 'dormant' and s.active_stop == p.stop
    r = E.advance(p,s,observation(1,side,100,104,99,103),2.)
    assert not r.due  # low 99 crosses newly calculated 100 but not original 98
    assert r.state.trail == 'armed' and r.state.active_stop == 100
    assert r.target == p.target
    p,s = E.decode(E.encode(p,r.state))
    r = E.advance(p,s,observation(2,side,103,105,100.5,104),2.)
    assert not r.due
    assert r.stop == (101 if side=='long' else 99)
    assert r.target == p.target
    # A duplicate cannot ratchet again using different observations.
    assert E.advance(p,r.state,observation(2,side,103,150,95,104),2.).state == r.state
    p,s = E.decode(E.encode(p,r.state))
    r = E.advance(p,s,observation(3,side,100.8,102,100,101),1.25)
    assert r.due and r.reason == 'stop' and r.state.gap
    assert r.state.reference_exit == pytest.approx(100.8 if side=='long' else 99.2)
    assert r.quantity == 1.25
    retry = E.advance(p,r.state,observation(4,side,100,101,99,100),.5)
    assert retry.state == r.state and retry.quantity == .5


@pytest.mark.parametrize('side',['long','short'])
def test_initial_stop_and_target_precede_current_extreme(side):
    p,s = E.initialize(geometry(True),100,1,side,0,STEP)
    r = E.advance(p,s,observation(1,side,100,125,97,101),1)
    assert r.reason == 'stop' and r.state.ambiguous
    assert r.state.trail == 'dormant' and r.state.reference_exit == p.stop
    # After arming, the original fixed target still closes the position.
    s = E.advance(p,s,observation(1,side,100,104,99,103),1).state
    r = E.advance(p,s,observation(2,side,103,121,102,120),1)
    assert r.reason == 'target' and r.state.reference_exit == p.target


@pytest.mark.parametrize('side',['long','short'])
def test_trail_cannot_loosen_and_initial_r_never_changes(side):
    p,s = E.initialize(geometry(),100,1,side,0,STEP)
    s = E.advance(p,s,observation(1,side,100,104,99,103),1).state
    r = E.advance(p,s,observation(2,side,103,105,101,104,atr=10.),1)
    assert not r.due and r.stop == s.active_stop == 100
    assert r.state.trail == 'armed' and p.initial_r == 2.
    assert r.state.best == (105 if side=='long' else 95)


@pytest.mark.parametrize('side',['long','short'])
def test_arming_requires_exact_declared_threshold_and_zero_is_explicit(side):
    p,s = E.initialize(geometry(arm=3.),100,1,side,0,STEP)
    s = E.advance(p,s,observation(1,side,100,105.9,99,104),1).state
    assert s.trail == 'dormant' and s.active_stop == p.stop
    s = E.advance(p,s,observation(2,side,104,106,103,105),1).state
    assert s.trail == 'armed' and s.active_stop == (102 if side=='long' else 98)
    p,s = E.initialize(geometry(arm=0.),100,1,side,0,STEP)
    s = E.advance(p,s,observation(1,side,100,100.1,99.9,100),1).state
    assert s.trail == 'armed'


@pytest.mark.parametrize('atr',[None,np.nan,np.inf,0.,-1.,True])
def test_missing_or_invalid_atr_refuses_surviving_transition_but_not_protection(atr):
    p,s = E.initialize(geometry(),100,1,'long',0,STEP)
    with pytest.raises(ValueError,match='exit_trail_atr_unavailable'):
        E.advance(p,s,E.Observation(STEP,100,101,99,100,atr=atr),1)
    assert s.bars_held == 0 and s.active_stop == 98
    stopped = E.advance(p,s,E.Observation(STEP,97,101,96,100,atr=atr),1)
    assert stopped.reason == 'stop' and stopped.state.reference_exit == 97


@pytest.mark.parametrize('change',[
    {'mult':0.}, {'mult':True}, {'mult':np.inf}, {'arm_at_r':None},
    {'arm_at_r':-1.}, {'arm_at_r':True}, {'arm_at_r':np.nan}, {'extra':1}])
def test_no_default_or_silent_trail_parameter_substitution(change):
    ex = geometry()
    ex.trail.update(change)
    assert E.unsupported(ex)
    with pytest.raises(ValueError,match='unsupported_versioned_exit'):
        E.initialize(ex,100,1,'long',0,STEP)


def test_trail_atr_requires_intact_declared_timeframe_inputs():
    df = frame(40)
    baseline = E.trail_atr_series(df,STEP)
    assert baseline.iloc[:14].isna().all() and baseline.iloc[14:].eq(2).all()
    missing = df.drop(index=20)
    assert E.trail_atr_series(missing,STEP).loc[21:34].isna().all()
    for key,value in [('quality','INVALID'),('bar_state','PARTIAL')]:
        bad = df.copy()
        bad[key] = 'VALID' if key=='quality' else 'FINAL'
        bad.loc[20,key] = value
        assert E.trail_atr_series(bad,STEP).loc[20:34].isna().all()
    assert E.trail_atr_series(df,4*STEP).isna().all()


def test_new_semantics_never_relabels_old_receipts():
    p,s = E.initialize(geometry(),100,1,'long',0,STEP)
    body = json.loads(E.encode(p,s))
    body['policy']['semantics_id'] = 'factory-exit.closed-bars.v1:historical'
    with pytest.raises(ValueError,match='exit_semantics_mismatch'):
        E.decode(json.dumps(body))


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('target',[False,True])
@pytest.mark.parametrize('outcome',['stop','target'])
def test_versioned_paper_trail_combination_and_restart(tmp_path,side,target,outcome):
    if outcome == 'target' and not target:
        pytest.skip('no target was declared')
    from trader.core.config import load_config
    from trader.core.types import Decision
    from trader.engine.paper import PaperExecutor
    from trader.engine import paper_exit_evidence as X
    from trader.engine.versioned_exits import observations
    from trader.strategy import factory_handoff as F
    from trader.strategy.compile import compile_version
    from tests.test_sol1_paper_cost import installed,bars,snapshot,H4
    from tests.test_sol1_strategy_lifecycle import _spec
    from tests.authority_factory_fixtures import T0

    ex = geometry(target)
    ex.stop = {'kind':'pct','v':.04}
    if target:
        ex.target = {'kind':'rr','v':2.}
    spec = _spec(direction=side,entry_long='close > 0' if side=='long' else '',
        entry_short='close > 0' if side=='short' else '',exit=ex)
    cfg = load_config()
    j,out,_ = installed(tmp_path,cfg,spec)
    version = F.load_version(j,out['version_id'])
    compiled = compile_version(j,version['version_id'])
    start = T0//H4*H4
    df = bars(220,start)
    snap = snapshot(df)
    signal = compiled.to_evaluator()(None,snap)
    dec = Decision('trail','trail','BTC/USDT',signal.action,1.,.5,.8,[],[])
    dec.strategy_signals = [dict(strategy_id=spec.id,action=signal.action.value,params=signal.params)]
    paper = PaperExecutor(j,cfg)
    paper.capture = None
    tid = paper.enter(dec,snap,spec.id,reference_equity=10000,state='ACTIVE')
    trade = j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    p,s = E.decode(trade['exit_state_json'])
    tail = [(110,116,109,115),(115,117,113,116),
        (110,111,109,110) if outcome=='stop' else (116,121,114,120)]
    if side=='short':
        tail = [(220-o,220-l,220-h,220-c) for o,h,l,c in tail]
    first = snapshot(bars(220,start,tail[:1]))
    assert paper.manage(first) == []
    actual = j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    _,s1 = E.decode(actual['exit_state_json'])
    assert s1.trail == 'armed' and s1.active_stop != p.stop
    assert actual['take_profit'] == p.target
    paper = PaperExecutor(j,cfg)  # exact persisted state survives restart
    paper.capture = None
    final_snap = snapshot(bars(220,start,tail))
    assert paper.manage(final_snap) == [tid]
    for obs in observations(compiled.spec,final_snap,s):
        s = E.advance(p,s,obs,trade['amount']).state
        if s.reason:
            break
    closed = j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    assert closed['exit_state_json'] == E.encode(p,s)
    assert closed['close_reason'] == s.reason == outcome
    assert X.verify(j,closed,version,F.verify_install(j,version,current=False))
    assert closed['commission_basis'] == 'UNAVAILABLE' or closed['commission_basis'].startswith('ESTIMATED')
    # Applicable live adapter: intent/retry evidence only, with no venue capability.
    from trader.engine.versioned_exits import LiveExitAdapter
    class SubmissionProbe:
        ok = False
        calls = []
        def close(self,position,**kw):
            self.calls.append((dict(position['_strategy_exit_evidence']),kw))
            return self.ok
    probe = SubmissionProbe()
    adapter = LiveExitAdapter(j,probe)
    assert adapter.manage(dict(trade),final_snap) is None
    assert probe.calls[-1][0]['state'] == json.loads(closed['exit_state_json'])
    assert probe.calls[-1][0]['intended_quantity'] == trade['amount']
    # A failed submission stays pending; a caller reporting a reduced remainder
    # receives that remainder's intent without another price-state transition.
    probe.ok = True
    remaining = dict(trade,amount=trade['amount']/2)
    assert adapter.manage(remaining,final_snap) == outcome
    assert probe.calls[-1][0]['intended_quantity'] == trade['amount']/2
    assert probe.calls[-1][1]['exit_price_hint'] == final_snap.price


@pytest.mark.parametrize('column,value',[('quality','INVALID'),('quality','STALE'),('bar_state','PARTIAL')])
def test_execution_observation_refuses_unusable_metadata(column,value):
    from types import SimpleNamespace
    from trader.engine.versioned_exits import observations
    from tests.test_compile import _spec
    df = frame(30)
    df[column] = 'VALID' if column=='quality' else 'FINAL'
    df.loc[29,column] = value
    at = df.ts.iloc[-1] + __import__('pandas').Timedelta(minutes=15)
    snap = SimpleNamespace(ts=at.isoformat(),df=lambda tf:df)
    ms = int(df.ts.iloc[-2].timestamp()*1000)
    p,s = E.initialize(ExitSpec(),100,1,'long',ms,STEP)
    obs = list(observations(_spec(),snap,s))
    assert len(obs)==1 and not obs[0].completed
    with pytest.raises(ValueError,match='exit_observation_not_consecutive_closed_bar'):
        E.advance(p,s,obs[0],1)
    df.attrs['read_mode']='retrospective'
    with pytest.raises(ValueError,match='retrospective_observations_have_no_execution_authority'):
        list(observations(_spec(),snap,s))


@pytest.mark.parametrize('key',['available_at_ms','observed_at_ms'])
def test_exit_adapter_cannot_consume_a_receipt_after_its_existing_cut(key):
    from types import SimpleNamespace
    from trader.engine.versioned_exits import observations
    from tests.test_compile import _spec
    df=frame(30)
    at=int(df.ts.iloc[-1].timestamp()*1000)+STEP
    df[key]=at
    df.loc[29,key]=at+1
    snap=SimpleNamespace(ts=__import__('pandas').Timestamp(at,unit='ms',tz='UTC').isoformat(),df=lambda tf:df)
    p,s=E.initialize(ExitSpec(),100,1,'long',at-2*STEP,STEP)
    obs=list(observations(_spec(),snap,s))
    assert len(obs)==1 and not obs[0].completed
    with pytest.raises(ValueError,match='exit_observation_not_consecutive_closed_bar'):
        E.advance(p,s,obs[0],1)
