"""Shared capital feasibility, causal gaps and paper/replay parity; synthetic only."""
from copy import deepcopy
import numpy as np
import pytest
from trader.research.compose import capital as K,hedge as H
from trader.engine.coordinated_paper import CoordinatedPaper
from trader.core.journal import Journal
from tests.test_strategy_hedge_stops import current,observed
from tests.test_coordinated_trial_lifecycle import packet  # noqa: F401


def spec(**changes):
    return H.make_spec(purpose='protective_beta',account_mode='netting',leverage=1.,
        primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},management=current(),
        ratio={'method':'fixed','value':1.},sizing={'primary_notional_pct':60.},
        fee_bps=4.,slip_bps=2.,**changes)


def test_headroom_counts_primary_hedge_cost_and_leverage_together():
    result=K.capacity(equity=970.,gross=600.,leverage=1.,unit_notional=50.,requested=12.,cost_rate=.001)
    assert 0<result['quantity']<12.
    assert result['initial_margin_after']+result['incremental_cost']<=970.
    assert result['initial_margin_after']+result['incremental_cost']==pytest.approx(970.)
    assert K.capacity(equity=970.,gross=980.,leverage=1.,unit_notional=50.,requested=12.,cost_rate=0.)['quantity']==0.


@pytest.mark.parametrize('field',['equity','gross','leverage','unit_notional','requested','cost_rate'])
def test_unknown_or_nonfinite_capital_is_not_zero(field):
    args=dict(equity=1000.,gross=600.,leverage=2.,unit_notional=50.,requested=12.,cost_rate=.001)
    args[field]=float('nan')
    with pytest.raises(ValueError,match='capital_inputs_unavailable'):K.capacity(**args)


def test_many_budgets_never_enlarge_intent_or_spend_other_leg_capital_twice():
    rng=np.random.default_rng(20261030)
    for _ in range(200):
        equity=float(rng.uniform(1.,10000.));lev=float(rng.uniform(1.,20.))
        gross=float(rng.uniform(0.,equity*lev));unit=float(rng.uniform(.1,100000.))
        requested=float(rng.uniform(.01,1000.));fee=float(rng.uniform(0.,.02))
        r=K.capacity(equity=equity,gross=gross,leverage=lev,unit_notional=unit,requested=requested,cost_rate=fee)
        assert 0<=r['quantity']<=requested
        assert r['initial_margin_after']+r['incremental_cost']<=equity+1e-10


def replay_and_paper(tmp_path,sp,p,h,po,ho,funding):
    n=len(p);signal=np.r_[1,np.zeros(n-1,int)];exit_=np.zeros(n,bool)
    bars=[observed(i,p=float(p[i]),h=float(h[i]))['primary_exit_observation'] for i in range(n)]
    replay=H.replay(sp,p,h,signal,exit_,equity=1000.,opens=(po,ho),decision_bars=bars,
                    ratios=np.ones(n),correlations=np.ones(n),funding=funding)
    paper=CoordinatedPaper(Journal(tmp_path/'paper.db'))
    key=paper.start(spec=sp,equity=1000.,identity=dict(exec_mode='paper',version_id='v',spec_hash='s',install_id='i',admission_id='a'))
    events=[]
    for i in range(n):
        events.append(paper.advance(key,i,dict(p=float(p[i]),h=float(h[i]),po=float(po[i]),ho=float(ho[i]),
            signal=int(signal[i]),exit=False,ratio=1.,corr=1.,primary_exit_observation=bars[i],
            funding={leg:float(funding[j][i]) for j,leg in enumerate(('primary','hedge'))}),allow_entry=True))
    assert np.allclose(replay['equity'],[r['equity'] for r in events],atol=1e-10)
    assert np.allclose(replay['gross'],[r['gross_notional'] for r in events],atol=1e-10)
    assert replay['capital_policy_id']==paper.state(key)['capital_policy_id']==K.POLICY_ID
    return replay,events,paper,key


def test_gap_funding_and_both_leg_costs_agree_between_replay_and_durable_paper(tmp_path):
    p=np.array([100.,100.,95.,94.,94.,94.]);h=np.full(6,50.)
    po=np.array([100.,110.,100.,95.,94.,94.]);ho=np.array([50.,50.,50.,70.,50.,50.])
    funding=(np.full(6,.001),np.full(6,-.001))
    out,events,paper,key=replay_and_paper(tmp_path,spec(),p,h,po,ho,funding)
    assert any(r['positions']['hedge']<0 for r in events)
    # After each actual increase, total margin must fit post-cost equity.
    for r in events:
        if any(r['fills'].values()):assert r['gross_notional']<=r['equity']+1e-10
    assert out['book'].pos==paper.state(key)['book']['pos']


def test_opening_gap_reprices_the_initial_order_instead_of_borrowing_new_capital(tmp_path):
    p=np.full(3,200.);p[0]=100.;h=np.full(3,50.)
    sp=spec();sp['sizing']['primary_notional_pct']=100.
    _,events,_,_=replay_and_paper(tmp_path,sp,p,h,p,h,(np.zeros(3),np.zeros(3)))
    # Signal requested ten units at100; fill occurs at200 with only1000 capital.
    quantity=next(iter(events[1]['fills'].values()))
    assert 0<quantity<5.
    assert events[1]['gross_notional']+events[1]['costs']<=1000.+1e-10


def test_reductions_do_not_require_spare_margin_or_known_other_mark():
    sp=spec()
    assert H.affordable_fill(sp,{'primary':6.,'hedge':-4.},{'primary':50.,'hedge':float('nan')},-10.,'primary',-1,3.,.01)==3.
    assert H.affordable_fill(sp,{'primary':6.,'hedge':-4.},{'primary':50.,'hedge':float('nan')},1000.,'primary',1,3.,.01)==0.


def test_old_paper_state_cannot_silently_gain_new_capital_semantics(tmp_path):
    paper=CoordinatedPaper(Journal(tmp_path/'old.db'))
    key=paper.start(spec=spec(),identity=dict(exec_mode='paper',version_id='v',spec_hash='s',install_id='i',admission_id='a'))
    state=deepcopy(paper.state(key));state.pop('capital_policy_id')
    from trader.research.closure import canonical,digest
    with paper.journal._tx() as db:db.execute('UPDATE coordinated_paper_books SET payload=?,sha256=? WHERE book_id=?',
        (canonical(state),digest(state),key))
    with pytest.raises(ValueError,match='capital_protocol_differs'):paper.advance(key,0,{},allow_entry=True)


def test_paper_can_reduce_main_at_known_price_when_unheld_hedge_data_is_missing(tmp_path):
    paper=CoordinatedPaper(Journal(tmp_path/'reduce.db'))
    key=paper.start(spec=spec(),identity=dict(exec_mode='paper',version_id='v',spec_hash='s',install_id='i',admission_id='a'))
    def bar(i):
        return dict(p=100.,h=50.,po=100.,ho=50.,signal=1 if i==0 else 0,exit=i>=2,ratio=1.,corr=1.,
            funding={'primary':0.,'hedge':0.},primary_exit_observation=observed(i,p=100.,h=50.)['primary_exit_observation'])
    for i in range(3):paper.advance(key,i,bar(i),allow_entry=i<2)
    missing=dict(bar(3),h=None,ho=None,funding=None)
    result=paper.advance(key,3,missing,allow_entry=False)
    assert result['positions']['primary']==0. and any(result['fills'].values())
    assert result['incomplete_marks'] and result['equity'] is None and result['net_economics']=='UNAVAILABLE'


def test_old_numerical_evidence_cannot_be_relabelled_as_current_budget_model():
    from trader.research.closure import hedge_qualification as HQ,ClosureRefused
    from tests.test_hedge_qualification import result
    with pytest.raises(ClosureRefused,match='capital_policy_differs'):HQ.assess(result(),spec())


def test_cache_key_changes_with_capital_model_without_invalidating_legacy_candidates(monkeypatch):
    from trader.research.closure import development as D
    candidate=dict(candidate_id='fixed',hedge=spec());legacy=dict(candidate_id='legacy')
    env=dict(envelope_id='e',evaluator_id='v',cost_id='c',split_id='s',policy={'draws':30})
    before=D.cache_key(candidate,env,'data');unchanged=D.cache_key(legacy,env,'data')
    monkeypatch.setattr(K,'POLICY_ID','different-protocol')
    assert D.cache_key(candidate,env,'data')!=before
    assert D.cache_key(legacy,env,'data')==unchanged


def test_resigned_old_paper_proposal_cannot_claim_current_capital_model(packet):
    from tests.test_strategy_hedge_stops import packet_with_stop
    from trader.research.closure import coordinated_trial as T,digest,ClosureRefused
    old=packet_with_stop(packet,'close_group')
    next(iter(old['result']['hedge']['per_symbol'].values())).pop('capital_policy_id')
    old['result']['result_id']=digest({k:v for k,v in old['result'].items() if k!='result_id'})
    old['trial_id']=digest({k:v for k,v in old.items() if k!='trial_id'})
    with pytest.raises(ClosureRefused,match='capital_policy_differs'):T.verify(old)
