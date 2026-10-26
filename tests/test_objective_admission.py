"""Numerical inference and frozen-comparator regressions; no venue authority."""
from copy import deepcopy
import math
import numpy as np
import pytest
from trader.research.closure import objective_admission as O
from trader.research.closure.calibration import clopper_pearson
from tests.test_compose_hedge import market, signals, spec
from trader.research.compose import hedge as H


def evidence(count=20, *, objective='max_drawdown_pct'):
    relative=objective=='net_return_pct'
    return dict(objective=objective,admission_protocol_id=O.protocol_id(),objective_episodes=[
        dict(start=i*64,end=(i+1)*64,hedged=3. if relative else 1.,unhedged=2.,reduced=2.,cash=0.,active=True)
        for i in range(count)])


def test_all_required_comparisons_have_separate_wealth_and_cash_is_required_for_pairs():
    r=evidence();out=O.evaluate(dict(result=r,alpha=.01))
    assert out['admitted'] and out['evidence']['p']==pytest.approx(1.5**-20)
    r['objective_episodes'][0]['reduced']=0.
    out=O.evaluate(dict(result=r,alpha=.0005))
    assert not out['admitted']
    assert out['evidence']['p_by_comparator']['unhedged']<out['evidence']['p_by_comparator']['reduced']
    pair=evidence(objective='net_return_pct')
    assert O.evaluate(dict(result=pair,alpha=.01))['admitted']
    for episode in pair['objective_episodes']:
        episode.update(hedged=-1.,unhedged=-2.,reduced=-2.)
    out=O.evaluate(dict(result=pair,alpha=.01))
    assert not out['admitted'] and out['evidence']['p_by_comparator']['cash']==1.


def test_ties_inactive_and_later_losses_never_invent_evidence():
    r=evidence();r['objective_episodes'][0]['active']=False
    r['objective_episodes'][1]['hedged']=2.
    out=O.evaluate(dict(result=r,alpha=.01))
    assert out['evidence']['counts']['unhedged']==dict(win=18,loss=0,tie=2)
    assert out['evidence']['p']==pytest.approx(1.5**-18)
    r=evidence()
    for ep in r['objective_episodes'][10:]:ep['hedged']=3.
    assert O.evaluate(dict(result=r,alpha=.01))['evidence']['p']==pytest.approx(1.5**-10)


@pytest.mark.parametrize('alpha',[None,0,1,float('nan'),True])
def test_unknown_or_invalid_registered_alpha_refused(alpha):
    assert not O.evaluate(dict(result=evidence(),alpha=alpha))['admitted']


def test_protocol_and_episode_integrity():
    r=evidence();r['admission_protocol_id']='edited'
    assert not O.evaluate(dict(result=r,alpha=.01))['admitted']
    for field,value in [('start',1),('hedged',float('nan')),('active','false')]:
        r=evidence();r['objective_episodes'][0][field]=value
        with pytest.raises(ValueError):O.evaluate(dict(result=r,alpha=.01))


def test_continuous_episode_keeps_boundary_move_and_unknowns_are_not_zero():
    eq=np.full(128,100.);eq[64:]=90.
    out=O.episode_values(eq,start_equity=100.,gross=np.ones(128),metric='worst_bar_pct')
    assert out[0]['value']==0. and out[1]['value']==pytest.approx(10.)
    eq[70]=float('nan')
    with pytest.raises(ValueError,match='marks_unknown'):
        O.episode_values(eq,start_equity=100.,gross=np.ones(128),metric='worst_bar_pct')


@pytest.mark.parametrize('metric',['max_drawdown_pct','cvar5_pct','worst_bar_pct','stress_loss_pct','net_return_pct'])
def test_real_evaluator_emits_frozen_episode_clock_without_fitting_reduced_control_on_test(metric):
    p,h=market(512);s,x=signals(len(p))
    sp=H.make_spec(primary=dict(symbol='ALT/USDT'),hedge=dict(symbol='BTC/USDT'),purpose='relative_value' if metric=='net_return_pct' else 'protective_beta',
        objective=dict(metric=metric),ratio=dict(method='fixed',value=.5))
    out=H.evaluate(sp,p,h,s,x,assessment_episode_bars=64,reduced_fraction=.4)
    assert out['comparators']['reduced']['fraction']==.4
    assert len(out['objective_episodes'])==8
    assert out['objective_episodes'][1]['start']==64
    assert out['admission_protocol_id']==O.protocol_id()
    assert O.evaluate(dict(result=out,alpha=.01))['evidence']['episodes']==8
    altered=p.copy();altered[400:]*=1.2
    again=H.evaluate(sp,altered,h,s,x,assessment_episode_bars=64,reduced_fraction=.4)
    assert again['comparators']['reduced']['fraction']==.4
    assert out['objective_episodes'][:6]==again['objective_episodes'][:6]


def test_large_and_small_exact_binomial_intervals():
    lo,hi=clopper_pearson(0,10)
    assert lo==0. and hi==pytest.approx(1-.025**.1)
    assert clopper_pearson(10,10)[0]==pytest.approx(.025**.1)
    lo,hi=clopper_pearson(2300,1_000_000)
    assert 0<lo<.0023<hi<.0025
    with pytest.raises(ValueError):O.calibrate(alpha=0)
