"""Reconciled D6 fill clock and D7 shared-policy handoff; offline fixtures."""
import json
import numpy as np
import pytest
from trader.strategy import exit_policy as E
from trader.strategy import vector_backtest as V
from trader.strategy.spec import ExitSpec
from tests.test_d7_strategy_parity import frame, STEP
from tests.test_d7_trailing_exits import geometry
from tests.test_compile import _spec


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('target',[False,True])
def test_shared_candidate_trade_table_simulate_and_policy_intents_agree(side,target):
    df=frame(240)
    tail=[(100,104,99,103),(103,105,100.5,104),(100.8,102,100,101)]
    if side=='short':
        tail=[(200-o,200-l,200-h,200-c) for o,h,l,c in tail]
    for j,row in enumerate(tail,211):
        df.loc[j,['open','high','low','close']]=row
    ex=E.bind_research(_spec(exit=geometry(target))).exit
    # Actual base ATR is 2, so 2 ATR stop = 4 and arm_at_r 1 needs high 104.
    risk=dict(risk_per_trade_pct=1.,taker_fee_pct=.05,slippage_atr_frac=.06,
        funding_rate_8h=.0001,bar_minutes=15)
    lo,sh=np.zeros(len(df),bool),np.zeros(len(df),bool)
    (lo if side=='long' else sh)[210]=True
    funding=np.full(len(df),.0002)
    fills,evidence=[],[]
    res=V.simulate(lo,sh,df,ex,risk,funding=funding,fills_out=fills,exit_evidence_out=evidence)
    table=V.trade_table(df,ex,risk,funding=funding)
    walked=V.walk_table(table,lo,sh,risk)
    assert res.trades==len(fills)==len(walked)==1
    assert fills[0].r_multiple==pytest.approx(walked[0][2])
    atr=E.trail_atr_series(df,STEP)
    entry_ms=int(df.ts.iloc[210].timestamp()*1000)
    p,s=E.initialize(ex,100,float(atr.iloc[210]),side,entry_ms,STEP)
    for j in range(211,len(df)):
        row=df.iloc[j]
        r=E.advance(p,s,E.Observation(int(row.ts.timestamp()*1000),row.open,row.high,row.low,row.close,
            atr=float(atr.iloc[j])),1)
        s=r.state
        if r.due:
            break
    assert evidence[0]['state']==json.loads(E.encode(p,s))
    assert fills[0].exit_i==j
    assert evidence[0]['exit_semantics_id']==E.EXIT_SEMANTICS_ID


def test_research_trail_declared_geometry_routes_to_current_policy():
    # Use the established research vocab contract without evaluating market history.
    from trader.strategy.geometries import GEOS
    ex=E.bind_research(_spec(exit=GEOS['trail'])).exit
    assert ex._exit_semantics_id==E.EXIT_SEMANTICS_ID
    assert ex.stop=={'kind':'atr','mult':2.}
    assert ex.trail=={'kind':'atr','mult':4.,'arm_at_r':1.}
    assert ex.target=={'kind':'none'} and ex.time=={'max_bars':500}


def test_signal_decision_cannot_be_scored_as_same_close_execution():
    df=frame(240)
    spec=E.bind_research(_spec(filters=[],entry_long='close > 0',
        exit=ExitSpec(signal_exit='close < 101',time={'max_bars':4})))
    lo,sh=np.zeros(len(df),bool),np.zeros(len(df),bool)
    lo[210]=True
    risk=dict(risk_per_trade_pct=1.,taker_fee_pct=.05,slippage_atr_frac=.06)
    signal=np.ones(len(df),bool)
    available=np.ones(len(df),bool)
    fills, evidence = [], []
    res = V.simulate(lo,sh,df,spec.exit,risk,exit_sig=signal,exit_sig_available=available,
                     fills_out=fills,exit_evidence_out=evidence)
    assert res.trades == 1 and fills[0].exit_i == 212
    assert evidence[0]['state']['state']['reference_exit'] is None
    assert evidence[0]['fill_price'] == df.open.iloc[212] and evidence[0]['at_open']
    with pytest.raises(ValueError,match='exit_signal_evidence_unavailable'):
        V.simulate(lo,sh,df,spec.exit,risk,exit_sig=signal)
    signal[:]=False
    res=V.simulate(lo,sh,df,spec.exit,risk,exit_sig=signal,exit_sig_available=available)
    table=V.trade_table(df,spec.exit,risk,exit_sig=signal,exit_sig_available=available)
    assert res.trades==len(V.walk_table(table,lo,sh,risk))==1


RISK = dict(risk_per_trade_pct=1., taker_fee_pct=.05, slippage_atr_frac=.06,
            funding_rate_8h=.0001, bar_minutes=15)


def signal_case(side, trail=False, target=False, max_bars=6):
    df = frame(240)
    ex = geometry(target) if trail else ExitSpec(target={'kind':'none'})
    ex.signal_exit = 'close < 101'
    ex.time = {'max_bars': max_bars}
    ex = E.bind_research(_spec(exit=ex)).exit
    lo, sh = np.zeros(len(df), bool), np.zeros(len(df), bool)
    (lo if side == 'long' else sh)[210] = True
    signal = np.zeros(len(df), bool)
    signal[212] = True
    return df, ex, lo, sh, signal, np.ones(len(df), bool)


@pytest.mark.parametrize('side', ['long', 'short'])
@pytest.mark.parametrize('trail,target', [(False,False), (True,False), (True,True)])
@pytest.mark.parametrize('delay', [0, STEP//3])
def test_signal_fills_first_available_open_with_identical_actual_null_costs(side,trail,target,delay):
    from tests.test_d6_replay_timing import retained
    df, ex, lo, sh, signal, available = signal_case(side,trail,target)
    df = retained(df, delay)
    # Signals at 212 cannot fill until 213 (prompt) / 214 (late).
    f = 213 if delay == 0 else 214
    df.loc[f, ['open','high','low','close']] = [100.5,101.5,99.5,100.5]
    receipts = df.available_at_ms.copy()
    fills, evidence = [], []
    funding = np.full(len(df), .0002)
    res = V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available,
                     fills_out=fills,exit_evidence_out=evidence,funding=funding)
    table = V.trade_table(df,ex,RISK,exit_sig=signal,exit_sig_available=available,funding=funding)
    walked = V.walk_table(table,lo,sh,RISK)
    assert res.trades == len(walked) == 1 and fills[0].exit_i == f
    assert fills[0].r_multiple == pytest.approx(walked[0][2])
    assert evidence[0]['state']['state']['reason'] == 'signal'
    assert evidence[0]['state']['state']['reference_exit'] is None
    assert evidence[0]['fill_price'] == 100.5 and evidence[0]['at_open']
    # Hand-computed open-to-open fee, spread, signed carry contract.
    sign = 1 if side == 'long' else -1
    entry, exit_px = 100 + sign*.12, 100.5 - sign*.12
    entry_f = 211 if delay == 0 else 212
    carry = .0002*sign*(f-entry_f)*.25/8
    unit = (exit_px-entry)*sign - .0005*(entry+exit_px) - carry*exit_px
    assert fills[0].r_multiple == pytest.approx(unit/4)
    assert receipts.equals(df.available_at_ms)


@pytest.mark.parametrize('side', ['long', 'short'])
def test_signal_on_last_held_bar_fills_after_close_instead_of_time_exit(side):
    df,ex,lo,sh,signal,available = signal_case(side,max_bars=2)
    fills=[]
    result=V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available,fills_out=fills)
    assert result.trades == 1 and fills[0].exit_i == 213 and result.exit_reasons == {'signal':1}


@pytest.mark.parametrize('side', ['long', 'short'])
def test_protection_remains_active_while_signal_waits_for_available_open(side):
    from tests.test_d6_replay_timing import retained
    df,ex,lo,sh,signal,available = signal_case(side,trail=True)
    signal[:] = False; signal[211] = True
    df = retained(df, 0)
    df.loc[211,'available_at_ms'] += STEP//3  # decision fills at 213
    # Protective hit at 212 precedes the intended 213 signal fill.
    df.loc[212,['open','high','low','close']] = [100,106,94,100]
    result=V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available)
    assert result.trades == 1 and result.exit_reasons == {'stop':1}


@pytest.mark.parametrize('side', ['long', 'short'])
def test_unexecutable_signal_keeps_real_protection_and_time_exit(side):
    df,ex,lo,sh,signal,available = signal_case(side,max_bars=4)
    signal[:] = False; signal[211] = True
    df.loc[212,'open'] = np.nan
    # The unavailable next open cannot become a signal trade; its invalid
    # completed OHLC also cannot be passed off as trustworthy evidence.
    with pytest.raises(ValueError,match='exit_observation_invalid_ohlc'):
        V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available)
    df=frame(213)
    lo,sh,signal,available=lo[:213],sh[:213],signal[:213],available[:213]
    signal[:]=False; signal[-1]=True
    result=V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available)
    assert result.trades == 0 and result.censored_position_count == 1


@pytest.mark.parametrize('bad', ['PARTIAL','missing_signal','missing_atr'])
def test_unknown_observation_evidence_refuses_instead_of_fabricating_trade(bad):
    df,ex,lo,sh,signal,available=signal_case('long',trail=True)
    if bad=='PARTIAL':
        df['bar_state']='FINAL';df.loc[211,'bar_state']='PARTIAL'
        message='exit_observation_not_consecutive_closed_bar'
    elif bad=='missing_signal':
        available[211]=False; message='exit_signal_evidence_unavailable'
    else:
        # Invalid prior ATR input leaves entry 210 intact but invalidates 211.
        from unittest.mock import patch
        original=E.trail_atr_series
        def unavailable(frame,step):
            values=original(frame,step);values.iloc[211]=np.nan;return values
        with patch.object(E,'trail_atr_series',unavailable):
            with pytest.raises(ValueError,match='exit_trail_atr_unavailable'):
                V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available)
        return
    with pytest.raises(ValueError,match=message):
        V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available)


@pytest.mark.parametrize('side', ['long', 'short'])
def test_stale_signal_refuses_its_fill_and_preserves_clock_exit(side):
    from tests.test_d6_replay_timing import retained
    df,ex,lo,sh,signal,available=signal_case(side,max_bars=4)
    df=retained(df,0)
    df.loc[212,'available_at_ms'] += 2*STEP
    evidence=[]
    result=V.simulate(lo,sh,df,ex,RISK,exit_sig=signal,exit_sig_available=available,
                      exit_evidence_out=evidence)
    assert result.trades == 1 and result.exit_reasons == {'time':1}
    assert evidence[0]['fill_i'] == 214 and not evidence[0]['at_open']


@pytest.mark.parametrize('side', ['long', 'short'])
def test_compiled_vector_and_walk_forward_use_declared_signal_evidence(side):
    from trader.strategy.compile import compile_spec
    from trader.strategy.null_baseline import assess
    df=frame(700)
    spec=_spec(direction=side,filters=[],entry_long='close > 0' if side=='long' else '',
               entry_short='close > 0' if side=='short' else '',
               exit=ExitSpec(signal_exit='close < 101',time={'max_bars':4}))
    compiled=compile_spec(E.bind_research(spec))
    result=V.vector_backtest(compiled,{'15m':df},RISK)
    walk=V.vector_walk_forward(compiled,{'15m':df},RISK)
    train,test=walk['train'],walk['test']
    assert result.trades > 0 and result.exit_reasons == {'signal':result.trades}
    assert train.exit_reasons == {'signal':train.trades} and train.trades > 0
    assert test.exit_reasons == {'signal':test.trades} and test.trades > 0
    # Same market signals stay aligned under entry rotation, including slicing.
    out=assess(compiled,{'15m':df},RISK,result.profit_factor,draws=20,split=.5)
    assert out['draws']==20 and out['percentile'] is not None


@pytest.mark.parametrize('side',['long','short'])
def test_research_portfolio_legs_retain_signal_evidence_for_actual_and_rotated_walks(side):
    from trader.strategy.compile import compile_spec
    from trader.research.evaluate import Bundle
    from trader.research.portfolio_null import legs_for, fills_for
    df=frame(240)
    spec=_spec(direction=side,filters=[],entry_long='close > 0' if side=='long' else '',
               entry_short='close > 0' if side=='short' else '',
               exit=ExitSpec(signal_exit='close < 101',time={'max_bars':4}))
    compiled=compile_spec(E.bind_research(spec))
    risk={**RISK,'real_funding':False}
    bundle=Bundle('15m',{'S':df},{'S':{'15m':df}},{'S':{'15m':df}},None,None,{},risk)
    legs=legs_for(compiled,bundle)
    actual,count=fills_for(legs,compiled.spec.exit,risk,'15m')
    rotated,rotated_count=fills_for(legs,compiled.spec.exit,risk,'15m',offset=7)
    assert count == rotated_count > 0
    assert all(f.exit_i-f.entry_i == 2*900 for f in actual+rotated)
    result=V.vector_backtest(compiled,{'15m':df},risk)
    assert result.trades == count and result.exit_reasons == {'signal':count}
