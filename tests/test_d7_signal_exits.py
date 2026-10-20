"""Approved signal decision priority, with no invented same-close execution."""
import json
import numpy as np
import pytest
from trader.strategy import exit_policy as E
from trader.strategy.spec import ExitSpec
from trader.strategy.compile import compile_spec,compile_version
from tests.test_d7_strategy_parity import STEP,frame
from tests.test_compile import _spec


def test_optional_blank_signal_exit_matches_compiler():
    ex=ExitSpec(signal_exit='  ',time={'max_bars':1})
    p,s=E.initialize(ex,100,1,'long',0,STEP)
    assert not p.signal_exit
    assert compile_spec(_spec(exit=ex)).exit_signal({'15m':frame(20)}) is None
    assert E.advance(p,s,E.Observation(STEP,100,101,99,100),1).reason=='time'


@pytest.mark.parametrize('side',['long','short'])
def test_signal_decision_precedes_time_and_trail_but_never_claims_a_fill(side):
    ex=ExitSpec(signal_exit='close < ema(5)',time={'max_bars':1},
        trail={'kind':'atr','mult':4.,'arm_at_r':1.})
    p,s=E.initialize(ex,100,1,side,0,STEP)
    r=E.advance(p,s,E.Observation(STEP,100,101,99,100,signal_exit=True),1)
    assert r.reason=='signal' and r.due and r.state.reference_exit is None
    assert r.state.trail=='dormant' and r.stop==p.stop
    p,s2=E.decode(E.encode(p,r.state))
    retry=E.advance(p,s2,E.Observation(2*STEP,100,101,99,100),.5)
    assert retry.state==s2 and retry.quantity==.5 and retry.state.reference_exit is None
    r=E.advance(p,s,E.Observation(STEP,100,101,99,100,signal_exit=False),1)
    assert r.reason=='time'
    with pytest.raises(ValueError,match='exit_signal_evidence_unavailable'):
        E.advance(p,s,E.Observation(STEP,100,101,99,100),1)


@pytest.mark.parametrize('side',['long','short'])
def test_prior_protection_wins_even_with_unavailable_or_true_signal(side):
    ex=ExitSpec(signal_exit='close < ema(5)')
    p,s=E.initialize(ex,100,1,side,0,STEP)
    for value in (None,True):
        r=E.advance(p,s,E.Observation(STEP,100,110,90,100,signal_exit=value),1)
        assert r.reason=='stop' and r.state.ambiguous and r.state.reference_exit==p.stop


def test_signal_compiler_preserves_unknown_and_exact_multi_condition():
    df=frame(40)
    c=compile_spec(_spec(exit=ExitSpec(signal_exit='not (close > donchian_hi(100))')))
    signal,available=c.exit_signal_detail({'15m':df})
    assert not signal.any() and not available.any()
    df.loc[25,'close']=102
    df.loc[25,'high']=103
    c=compile_spec(_spec(exit=ExitSpec(signal_exit='prev(close, 1) > 101 and close < 101')))
    signal,available=c.exit_signal_detail({'15m':df})
    assert np.flatnonzero(signal).tolist()==[26]
    assert available[26]
    assert np.array_equal(signal,c.exit_signal({'15m':df}))


@pytest.mark.parametrize('side',['long','short'])
def test_factory_paper_and_live_adapter_persist_intent_until_D6_fill_contract(tmp_path,side):
    from trader.core.config import load_config
    from trader.core.types import Decision
    from trader.engine.paper import PaperExecutor
    from trader.engine.versioned_exits import LiveExitAdapter
    from trader.strategy import factory_handoff as F
    from tests.test_sol1_paper_cost import installed,bars,snapshot,H4
    from tests.test_sol1_strategy_lifecycle import _spec as version_spec
    from tests.authority_factory_fixtures import T0
    spec=version_spec(direction=side,entry_long='close > 0' if side=='long' else '',
        entry_short='close > 0' if side=='short' else '',
        exit=ExitSpec(stop={'kind':'pct','v':.04},target={'kind':'rr','v':2.},
            trail={'kind':'atr','mult':4.,'arm_at_r':1.},time={'max_bars':3},
            signal_exit='close < 109'))
    cfg=load_config()
    j,out,_=installed(tmp_path,cfg,spec)
    compiled=compile_version(j,out['version_id'])
    start=T0//H4*H4
    snap=snapshot(bars(60,start))
    sig=compiled.to_evaluator()(None,snap)
    dec=Decision('signal','signal','BTC/USDT',sig.action,1.,.5,.8,[],[])
    dec.strategy_signals=[dict(strategy_id=spec.id,action=sig.action.value,params=sig.params)]
    paper=PaperExecutor(j,cfg)
    paper.capture=None
    tid=paper.enter(dec,snap,spec.id,reference_equity=10000,state='ACTIVE')
    initial=j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    final_snap=snapshot(bars(60,start,[(110,110.2,108.5,108.8)]))
    assert paper.manage(final_snap)==[]
    pending=j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    p,s=E.decode(pending['exit_state_json'])
    from trader.engine import paper_exit_evidence as X
    evidence=json.loads(j.query(f'SELECT canonical_json FROM {X.TABLE} WHERE trade_id=?',(tid,))[0]['canonical_json'])
    assert s.reason is None and s.reference_exit is None
    assert evidence['signal_decision']['bar_ms']==s.last_bar_ms
    assert 'signal_fill' not in evidence
    assert pending['status']=='open' and pending['exit_price'] is None
    assert pending['stop_loss']==initial['stop_loss']==p.stop
    restarted=PaperExecutor(j,cfg)
    restarted.capture=None
    assert restarted.manage(final_snap)==[]
    assert j.query('SELECT status FROM versioned_paper_trades WHERE id=?',(tid,))[0]['status']=='open'
    class NoVenue:
        def close(self,*a,**kw):
            pytest.fail('signal-close fill was invented before D6 reconciliation')
    adapter=LiveExitAdapter(j,NoVenue())
    with pytest.raises(ValueError,match='signal_exit_execution_timing_requires_D6'):
        adapter.manage(dict(initial),final_snap)
    state=j.query('SELECT state_json FROM versioned_live_exit_states WHERE trade_id=?',(tid,))[0]['state_json']
    assert E.decode(state)[1].reason=='signal'
