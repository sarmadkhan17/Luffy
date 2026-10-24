"""Real combined-book return assessment; fixtures never establish statistical or live authority."""
import copy
import numpy as np
import pytest
from trader.research.compose import hedge as H
from trader.research.closure import ClosureRefused, hedge_qualification as Q


def evaluate(hedge_drift=-0.001, *, fee=4., missing=False):
    n=640
    p=100*np.exp(np.arange(n)*0.0003)
    h=100*np.exp(np.arange(n)*hedge_drift)
    if missing:h[250]=np.nan
    signal=np.zeros(n,bool);signal[5::40]=True
    spec=H.make_spec(primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},purpose='relative_value',account_mode='netting',
        ratio={'method':'fixed','value':1.},fee_bps=fee,unwind={'max_hold_bars':15})
    result=H.evaluate(spec,p,h,signal,np.zeros(n,bool),opens=(np.r_[p[0],p[:-1]],np.r_[h[0],h[:-1]]))
    return spec,result


def test_return_pair_is_assessed_using_real_two_leg_accounting():
    spec,res=evaluate()
    assert spec['objective']['metric']=='net_return_pct'
    assert res['comparators']['hedged']['entries']>=3
    assert res['verdict']=='HEDGE_SUPPORTED',res['reason']
    assert Q.rederive_verdict(res,spec)==res['verdict']
    assert Q.assess(res,spec)[0]=='ASSESSED_SUPPORTED'
    assert res['comparators']['hedged']['net_return_pct']>max(0.,res['comparators']['unhedged']['net_return_pct'],res['comparators']['reduced']['net_return_pct'])
    assert Q.ADMISSION_METHODS=={} # independent statistical qualification remains unavailable


@pytest.mark.parametrize('drift,fee',[(0.0003,4.),(-0.001,500.)])
def test_no_relative_edge_or_costs_remove_support(drift,fee):
    spec,res=evaluate(drift,fee=fee)
    assert res['verdict']=='ECONOMIC_UNSUPPORTED'
    assert Q.assess(res,spec)[0]=='ASSESSED_NOT_SUPPORTED'


def test_positive_return_does_not_hide_better_simple_alternative():
    spec,res=evaluate(0.0001)
    assert res['comparators']['hedged']['net_return_pct']>0
    assert res['comparators']['unhedged']['net_return_pct']>res['comparators']['hedged']['net_return_pct']
    assert Q.assess(res,spec)[0]=='ASSESSED_NOT_SUPPORTED'


def test_unknown_marks_are_inconclusive_and_objective_cannot_be_relabelled():
    spec,res=evaluate(missing=True)
    assert res['verdict']=='INSUFFICIENT_EVIDENCE'
    assert Q.assess(res,spec)[0]=='INCONCLUSIVE'
    spec,res=evaluate()
    bad=copy.deepcopy(res);bad['objective']='max_drawdown_pct'
    with pytest.raises(ClosureRefused,match='objective_mismatch'):Q.assess(bad,spec)
    with pytest.raises(ClosureRefused,match='risk_objective'):
        H.make_spec(primary={'symbol':'A'},hedge={'symbol':'B'},purpose='relative_value',objective={'metric':'max_drawdown_pct'})


@pytest.mark.parametrize('bad',[{'fee_bps':-1},{'slip_bps':float('nan')},{'leverage':0},
    {'maint_margin':1.},{'ratio':{'value':float('inf')}},{'ratio':{'lookback':-1}},
    {'sizing':{'primary_notional_pct':-100}},{'unwind':{'min_corr':2}},
    {'rebalance':{'trigger':'made_up'}},{'unwind':{'max_hold_bars':0}}])
def test_nonphysical_generated_accounting_is_refused(bad):
    with pytest.raises(ClosureRefused):
        H.make_spec(primary={'symbol':'A'},hedge={'symbol':'B'},purpose='relative_value',**bad)
