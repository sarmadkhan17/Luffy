"""Frozen price geometry decides a joint exit; it never fabricates an intrabar fill."""
import numpy as np
from trader.research.compose import hedge as H
from tests.test_compose_hedge import spec


def bars(prices):
    return [dict(bar_ms=i*14400000,step_ms=14400000,open=float(p),high=float(p+1),
                 low=float(p-1),close=float(p),atr=1.) for i,p in enumerate(prices)]


def exit_(**changes):
    out=dict(stop=dict(kind='pct',v=.05),target=dict(kind='none'),trail=dict(kind='none'),
             time=dict(max_bars=40),signal_exit='')
    out.update(changes);return out


def test_primary_price_stop_requests_both_legs_next_open_without_booking_stop_reference():
    p=np.array([100.,100.,80.,80.,80.]);h=np.full(len(p),100.)
    result=H.replay(spec(ratio=dict(method='fixed',value=.5),fee_bps=0.,slip_bps=0.,unwind=dict(max_hold_bars=100)),
        p,h,np.array([1,0,0,0,0]),np.zeros(5,bool),opens=(p,h),primary_exit=exit_(),decision_bars=bars(p))
    due=[i for i in result['instructions'] if i.kind=='UNWIND']
    assert len(due)==2 and {i.bar for i in due}=={2}
    assert result['book'].pos=={'primary':0.,'hedge':0.}
    assert result['final_equity']==8000. # next open at80, never a fabricated fill at95
    assert result['exit_input_gaps']==0


def test_trail_and_declared_time_change_the_actual_joint_exit():
    p=np.array([100.,110.,108.,100.,100.]);h=np.full(len(p),100.)
    kwargs=dict(opens=(p,h),decision_bars=bars(p))
    sp=spec(ratio=dict(method='fixed',value=.5),fee_bps=0.,slip_bps=0.,unwind=dict(max_hold_bars=100))
    signals=np.array([1,0,0,0,0]);x=np.zeros(5,bool)
    trail=H.replay(sp,p,h,signals,x,primary_exit=exit_(trail=dict(kind='atr',mult=2.,arm_at_r=0.)),**kwargs)
    assert [i.bar for i in trail['instructions'] if i.kind=='UNWIND' and i.leg=='primary']==[2]
    timed=H.replay(sp,p,h,signals,x,primary_exit=exit_(time=dict(max_bars=1)),**kwargs)
    assert [i.bar for i in timed['instructions'] if i.kind=='UNWIND' and i.leg=='primary']==[1]


def test_missing_declared_exit_inputs_refuse_entry_and_cannot_support_economics():
    p=np.full(5,100.)
    result=H.replay(spec(),p,p,np.array([1,0,0,0,0]),np.zeros(5,bool),primary_exit=exit_())
    assert result['entries']==0 and result['exit_input_gaps']>0
    assert not H.risk_metrics(result)['risk_complete']
