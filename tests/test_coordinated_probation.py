"""Prospective synthetic paper lifecycle; never claims a profitable real strategy."""
from copy import deepcopy
import numpy as np
import pytest
from tests.test_coordinated_trial_lifecycle import packet
from trader.core.journal import Journal
from trader.research.compose import hedge as H,graph
from trader.research.closure import digest
from trader.research.closure import coordinated_trial as T
from trader.engine.coordinated_paper import CoordinatedPaper
from trader.strategy import factory_handoff as F


def setup(tmp_path,packet,*,funding=True,partial=False):
    packet=deepcopy(packet)
    per=packet['result']['hedge']['per_symbol']['A/USDT']
    hs=H.make_spec(**{k:v for k,v in per['effective_spec'].items() if k not in ('ratio','spec_id')},
        ratio=dict(method='fixed',value=.5),primary_exposure_constraint=dict(min_net_exposure=.4))
    cand=deepcopy(packet['candidate']);cand.pop('candidate_id');cand.pop('graph_id',None);cand['hedge']=hs
    cand=graph.make_candidate(cand);packet['candidate']=cand
    combo=graph.realize(cand,packet['envelope']['envelope_id'],entries_only=True)
    per['effective_spec']=hs;per['comparators']['reduced']['fraction']=.8
    packet['result'].update(candidate_id=cand['candidate_id'],combo=combo.as_dict(),combo_hash=combo.hash)
    packet['result']['result_id']=digest({k:v for k,v in packet['result'].items() if k!='result_id'})
    packet['trial_id']=digest({k:v for k,v in packet.items() if k!='trial_id'})
    j=Journal(tmp_path/'journal.db')
    cfg=dict(strategies=dict(paper_probation_trades=8,paper_min_winrate=.6,paper_min_profit_factor=1.2))
    issued=F.create_version(j,cfg,dict(kind=T.KIND,packet=packet),at_ms=1)
    vid=issued['version_id'];installed=F.record_coordinated_paper_install(j,vid,at_ms=1)
    runner=CoordinatedPaper(j);step=14400000;first=installed['install']['first_bar_ms']
    # Fixed causal planted protection case: main loses first, hedge opens on
    # the frozen trigger, then the correlated hedge falls AFTER its fill.
    # Both controls lose over each full episode. Not a real-data edge claim.
    phase=np.arange(512)%40
    p=np.where(phase<2,100.,np.where(phase<4,97.,np.where(phase==4,94.,np.where(phase<=21,96.,100.))))
    h=np.where(phase<4,100.,np.where(phase==4,90.,np.where(phase<=21,88.,100.)))
    for i in range(len(p)):
        state=runner.state(installed['book_id'])
        obs=dict(p=float(p[i]),h=float(h[i]),po=float(p[i]),ho=float(h[i]),
            signal=1 if i%40==0 else 0,exit=i%40==20,ratio=.5,corr=1.,
            funding=dict(primary=0.,hedge=0.) if funding else None,signal_cut_ms=first+(i+1)*step,
            primary_exit_observation=dict(bar_ms=first+i*step,step_ms=step,open=float(p[i]),high=float(p[i]*1.004),
                low=float(p[i]*.996),close=float(p[i]),atr=1.))
        fills=None
        if partial and state['book']['pending']:
            fills={oid:qty*.8 for oid,(leg,side,qty,_) in state['book']['pending'].items()}
        runner.advance(installed['book_id'],i,obs,allow_entry=True,fills=fills)
    return j,cfg,vid,first+len(p)*step


def test_objective_paper_result_reaches_existing_approval_request_and_reverifies(tmp_path,packet):
    j,cfg,vid,at=setup(tmp_path,packet)
    result=F.evaluate_probation(j,cfg,vid,at_ms=at)
    assessment=F._assess(j,cfg,F.load_version(j,vid),1,at)
    assert result['status']==F.P_SATISFIED,(result,assessment.get('reason'),assessment.get('stats'))
    assert F.state_of(j,vid)==F.APPROVAL_REQUIRED
    request=F.approval_request(j,vid)
    proof=F._verify_probation(j,cfg,F.load_version(j,vid),request['probation_receipt_id'])
    assert proof['stats']['objective']=='max_drawdown_pct'
    assert proof['stats']['venue_execution']=='NOT_ESTABLISHED'
    assert not j.query('SELECT * FROM trades') and not j.query('SELECT * FROM strategies')
    assert F.multi_leg_block(F.load_version(j,vid))=='multi_leg_requires_coordinated_execution'
    assert not F.eligible_for_first_live(j,vid,cfg=cfg,now_ms=at).eligible
    # Exact retry creates no second request and no live authority.
    assert F.evaluate_probation(j,cfg,vid,at_ms=at+1)['result']=='duplicate'


def test_missing_funding_cannot_graduate_even_with_good_risk_curve(tmp_path,packet):
    j,cfg,vid,at=setup(tmp_path,packet,funding=False)
    result=F.evaluate_probation(j,cfg,vid,at_ms=at)
    assert result['status']==F.P_COST_INCOMPLETE
    assert F.state_of(j,vid)==F.SHADOW and F.approval_request(j,vid) is None


def test_partial_fills_replay_exactly_and_do_not_become_full_fills(tmp_path,packet):
    j,cfg,vid,at=setup(tmp_path,packet,partial=True)
    v=F.load_version(j,vid);result=F._assess(j,cfg,v,1,at)
    assert result['status'] in (F.P_INSUFFICIENT,F.P_NOT_SATISFIED,F.P_SATISFIED),result
    assert result.get('reason')!='paper_replay_differs_from_recorded_execution'
