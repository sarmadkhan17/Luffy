"""Return screening skips work, never manufactures statistical evidence."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.test_research_evaluate import DONCH, _bundle, _combo
from trader.research import evaluate as E
from trader.research.closure import development as D, calibration as C
from trader.research.vocab import Part
from trader.strategy import null_baseline


def test_loser_preserves_actual_trades_and_skips_timing_controls(monkeypatch):
    combo=_combo([DONCH]);bundle=_bundle(n_symbols=4,cfg_risk={'taker_fee_pct':10.})
    before={};full=E.evaluate(combo,bundle,draws=20,raw_runs=before)
    assert full['portfolio']['total_pct']<=0
    assessor=Mock(side_effect=AssertionError('screened loser must not run timing controls'))
    monkeypatch.setattr(null_baseline,'assess',assessor)
    after={};screened=E.evaluate(combo,bundle,draws=20,raw_runs=after,economic_screen=True)
    assert screened['verdict']=='economic_screened'
    assert screened['portfolio']==full['portfolio'] and screened['trades']==full['trades']
    assert screened['consistency_p'] is None and screened['scored_symbols']==0
    assert all(v['null_pctile'] is None for v in screened['symbols'].values())
    assert screened['economic_screen']['timing_assessment']=='NOT_COMPUTED_ECONOMIC_SCREEN'
    for symbol in before:
        assert before[symbol]['result']==after[symbol]['result']
        assert before[symbol]['fills']==after[symbol]['fills']
    assessor.assert_not_called()
    outcome,reason=D.classify(screened,{},development_only=True)
    assert outcome=='NEGATIVE_ECONOMICS' and 'not evidence of no edge' in reason


def test_positive_survivor_keeps_identical_full_result_without_repeating_actuals(monkeypatch):
    combo=_combo([DONCH]);bundle=_bundle(n_symbols=4)
    full=E.evaluate(combo,bundle,draws=20)
    assert full['portfolio']['total_pct']>0 and full['verdict']=='scored'
    simulate=Mock(wraps=E.simulate);assess=Mock(wraps=null_baseline.assess)
    monkeypatch.setattr(E,'simulate',simulate);monkeypatch.setattr(null_baseline,'assess',assess)
    screened=E.evaluate(combo,bundle,draws=20,economic_screen=True)
    assert screened==full
    assert simulate.call_count==len(bundle.frames)
    assert assess.call_count==full['scored_symbols']


def test_small_sample_remains_untestable_instead_of_becoming_economic_rejection():
    combo=_combo([DONCH]);bundle=_bundle(n_symbols=3,cfg_risk={'taker_fee_pct':2.})
    full=E.evaluate(combo,bundle,draws=20)
    screened=E.evaluate(combo,bundle,draws=20,economic_screen=True)
    assert screened==full and screened['verdict']=='untestable'
    assert D.classify(screened,{})[0]=='INCONCLUSIVE_POWER'


def test_required_missing_input_is_untested_before_any_economic_screen(monkeypatch):
    bundle=_bundle(n_symbols=4);bundle.evidence={'scope':'retrospective'}
    funding=Part('funding_test','gauge','funding_test','funding_z(64)>0','funding_z(64)<0')
    simulate=Mock(side_effect=AssertionError('missing inputs cannot be simulated'))
    monkeypatch.setattr(E,'simulate',simulate)
    result=E.evaluate(_combo([funding]),bundle,economic_screen=True)
    assert result['verdict']=='untested' and result['context_gaps']
    assert 'economic_screen' not in result
    simulate.assert_not_called()
    assert D.classify(result,{})[0]=='BLOCKED_DATA'


def test_empty_strategy_is_not_misclassified_as_a_losing_strategy():
    never=Part('never','gauge','never','rsi(14)>1000','rsi(14)>1000')
    result=E.evaluate(_combo([never]),_bundle(n_symbols=4),economic_screen=True)
    assert result['verdict']=='empty' and 'economic_screen' not in result
    assert D.classify(result,{})[0]=='INCONCLUSIVE_POWER'


@pytest.mark.parametrize('bad',['positive','fabricated_p','sample','policy'])
def test_screen_cannot_accept_positive_return_fabricated_statistics_or_wrong_policy(bad):
    result=dict(verdict='economic_screened',trades=100,consistency_p=None,portfolio=dict(total_pct=-1.),
                economic_screen=dict(policy=E.ECONOMIC_SCREEN_POLICY,observed_trade_symbols=4))
    result=deepcopy(result)
    if bad=='positive':result['portfolio']['total_pct']=1.
    if bad=='fabricated_p':result['consistency_p']=.001
    if bad=='sample':result['economic_screen']['observed_trade_symbols']=3
    if bad=='policy':result['economic_screen']['policy']='old'
    assert D.classify(result,{})[0]=='CODE_FAILURE'


def test_calibration_search_contract_names_the_new_operational_order():
    contract=C.search_contract(search={},max_candidates=8,max_revisions=4)
    assert contract['development_order']==E.ECONOMIC_SCREEN_POLICY


def test_screened_graph_keeps_cost_evidence_and_its_revision_reaches_real_evaluation(tmp_path,monkeypatch):
    import json
    from trader.research.compose import graph
    from trader.research.closure import consumer,digest
    from trader.research.closure.store import Store
    bundle=_bundle(n_symbols=4,cfg_risk={'taker_fee_pct':10.})
    env=dict(envelope_id='FIXTURE',evaluator_id='FIXTURE',cost_id='FIXTURE',
        development_mode='REPLAY',policy_class='TEST_ONLY',tf='4h',fit_window=[0,bundle.cut],
        protected_status='UNAVAILABLE',protected_reasons=['FIXTURE_NO_INDEPENDENT_AUTHORITY'],
        policy=dict(min_discovery_symbols=4,draws=20,seed=17,min_symbol_trades=8,grow_max_p=.25))
    parent=graph.make_candidate(dict(question='fixture',rationale='fixture',expected='fixture',failure_conditions='fixture',
        structure='long_short',tf='4h',geo='fixed',
        long={'cmp':'gt','a':{'f':'ret24'},'b':{'lit':0}},
        short={'cmp':'lt','a':{'f':'ret24'},'b':{'lit':0}}))
    monkeypatch.setattr(D,'build_bundle',lambda *a,**kw:bundle)
    assessor=Mock(wraps=null_baseline.assess);controls=Mock(wraps=D._graph_baselines)
    monkeypatch.setattr(null_baseline,'assess',assessor);monkeypatch.setattr(D,'_graph_baselines',controls)
    result=D._develop(parent,bundle.frames,'FIXTURE_DATA',env,20,{'gaps':[]})
    assert result['outcome']=='NEGATIVE_ECONOMICS'
    assert result['metrics']['consistency_p'] is None
    assert result['baseline_assessment']=='NOT_COMPUTED_ECONOMIC_SCREEN'
    assert result['validation_readiness']['status']=='UNAVAILABLE'
    assert result['diagnostics']['mtm'] and result['diagnostics']['economic_screen']['per_symbol']
    assert any(v['fees']>0 for v in result['diagnostics']['economic_screen']['per_symbol'].values())
    assessor.assert_not_called();controls.assert_not_called()
    result['result_id']=digest(result)
    store=Store(tmp_path/'revisions.db')
    try:
        store.put_once('candidates',parent['candidate_id'],parent)
        item=consumer.revise(store,parent['candidate_id'],result,now_ms=1,max_revisions=1)
        assert item['status']=='COMPILED'
        assert json.loads(item['payload'])['provenance']['change']['kind']=='holding_period'
        child=store.get_once('candidates',item['candidate_id'])
        revised=D._develop(child,bundle.frames,'FIXTURE_DATA',env,20,{'gaps':[]})
        assert revised['candidate_id']==child['candidate_id']!=parent['candidate_id']
        assert revised['outcome'] in D.OUTCOMES and revised['outcome']!='CODE_FAILURE'
    finally:
        store.close()


def test_hedge_objective_branches_before_directional_screen(monkeypatch):
    env=dict(envelope_id='FIXTURE',evaluator_id='FIXTURE',cost_id='FIXTURE',
        development_mode='REPLAY',policy_class='TEST_ONLY',tf='4h',
        policy=dict(min_discovery_symbols=1,draws=20,seed=17,min_symbol_trades=8))
    candidate=dict(candidate_id='hedge-fixture',structure='hedged')
    marker={'hedge_objective':'risk_reduction_unchanged'}
    hedge=Mock(return_value=marker)
    monkeypatch.setattr(D,'build_bundle',Mock(return_value=object()))
    monkeypatch.setattr(D.graph,'is_graph',lambda _:True)
    monkeypatch.setattr(D,'_develop_hedge',hedge)
    evaluate=Mock(side_effect=AssertionError('hedges cannot use a directional profit screen'))
    monkeypatch.setattr(E,'evaluate',evaluate)
    assert D._develop(candidate,{'fixture':object()},'FIXTURE_DATA',env,20)==marker
    hedge.assert_called_once();evaluate.assert_not_called()
