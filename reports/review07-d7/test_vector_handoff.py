"""Only run via verify_shared_vector.py until the D6 interface is reconciled."""
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
    with pytest.raises(ValueError,match='signal_exit_execution_timing_requires_D6'):
        V.simulate(lo,sh,df,spec.exit,risk,exit_sig=signal,exit_sig_available=available)
    with pytest.raises(ValueError,match='exit_signal_evidence_unavailable'):
        V.simulate(lo,sh,df,spec.exit,risk,exit_sig=signal)
    signal[:]=False
    res=V.simulate(lo,sh,df,spec.exit,risk,exit_sig=signal,exit_sig_available=available)
    table=V.trade_table(df,spec.exit,risk,exit_sig=signal,exit_sig_available=available)
    assert res.trades==len(V.walk_table(table,lo,sh,risk))==1
