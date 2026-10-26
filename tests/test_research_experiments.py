"""Executable experiment allocation and diagnostic revisions, isolated stores only."""

from tests.test_compose_core import spec, _ctx
from trader.research.compose import experiments, graph, search
from trader.research.closure import consumer, study
from trader.research.closure.store import Store


def test_plans_bind_hypothesis_inputs_objective_and_falsifier():
    a = graph.make_candidate(spec())
    b = graph.make_candidate(spec(long={'cmp': 'gt', 'a': {'f': 'fundz360'}, 'b': {'lit': 1}}))
    pa, pb = experiments.plan(a), experiments.plan(b)
    assert pa['plan_id'] != pb['plan_id']
    assert 'funding' in pb['required_inputs'] and 'funding' not in pa['required_inputs']
    assert pa['hypothesis']['falsifier'] == a['failure_conditions']
    assert pa['authority'] == pb['authority'] == 'DEVELOPMENT_ONLY'


def test_same_role_indicator_comparison_is_executed_on_matched_rows():
    c = graph.make_candidate(spec())
    result = experiments.comparisons(c, [_ctx()])
    assert result['matrix']['pairs'] and result['matrix']['n_matched'] > 100
    assert result['authority'] == 'NO_QUALIFICATION'


def test_pair_and_exit_alternatives_are_reachable_with_small_budget(tmp_path):
    store = Store(tmp_path/'research.db')
    budget = search.register_budget(store,'s',dict(max_attempts=8,max_fits=1,interaction_share=0,
                factors=['rsi14','ret24','relvol50'],structures=['long_only'], dataset_reads=100))
    status = {k:'EXECUTABLE' for k in ('rsi14','ret24','relvol50')}
    result = search.plan(store,'s',budget,status,now_ms=1)
    candidates = [store.get_once('candidates',i) for i in result['admitted']]
    assert {c['geo'] for c in candidates} == {'fixed','trail'}
    assert 'pair' in {c['provenance']['track'] for c in candidates}
    assert len(store.all_once('experiment_plans')) == len(candidates)
    assert search.plan(store,'s',budget,status,now_ms=2)['admitted'] == []
    store.close()


def test_different_observed_failures_choose_different_experiments():
    body = spec(long={'all':[{'cmp':'gt','a':{'f':'rsi14'},'b':{'lit':60}},
                              {'cmp':'gt','a':{'f':'relvol50'},'b':{'lit':1}}]})
    c = graph.make_candidate(body)
    loss = dict(metrics={'total_pct':-4},baselines={})
    redundant = dict(metrics={'total_pct':4},baselines={'without_volume':{'candidate_earns_place':False}})
    cost_choices,_ = search.diagnosed_mutations(c,loss)
    redundancy_choices,_ = search.diagnosed_mutations(c,redundant)
    assert cost_choices[0]['kind'] == 'exit_geometry'
    assert redundancy_choices[0]['kind'] == 'remove_condition'
    for change in (cost_choices[0],redundancy_choices[0]):
        child = search.apply_change(c,change)
        assert child['candidate_id'] != c['candidate_id']
        graph.verify(child)


def test_custom_exit_is_preserved_in_indicator_revision():
    from dataclasses import asdict
    from trader.strategy.geometries import GEOS
    c = graph.make_candidate(spec(exit=asdict(GEOS['trail'])))
    change = next(x for x in search.mutations(c) if x['kind']=='substitute')
    child = search.apply_change(c,change)
    assert child['exit'] == c['exit']


def test_btc_reference_is_explicit_even_when_not_a_target():
    c = graph.make_candidate(spec(long={'cmp':'gt','a':{'f':'relbtc30'},'b':{'lit':0}}))
    assert experiments.needs_btc(c)
    assert 'btc' in experiments.plan(c)['required_inputs']
    assert search.reads_of(c,3) == 4


def test_revision_gets_evaluated_after_root_candidate_cap(tmp_path):
    store=Store(tmp_path/'research.db')
    root=graph.make_candidate(spec())
    cfg=dict(bank_path=None,seed_families=[],seed_graphs=[spec()],search=None,max_candidates=1)
    study._plan(store,'s',cfg,{},1)
    result=dict(candidate_id=root['candidate_id'],result_id='development-r',stage='DEVELOPMENT',
                outcome='NEGATIVE_ECONOMICS',metrics={'total_pct':-2})
    study._set_unit(store,'s','develop:'+root['candidate_id'],'DONE',2,result=result)
    child=consumer.revise(store,root['candidate_id'],result,study_id='s',now_ms=3)
    assert child['status']=='COMPILED'
    study._plan(store,'s',cfg,{},4)
    units={u['unit_id'] for u in store.units('s')}
    assert 'develop:'+child['candidate_id'] in units
    assert len([u for u in units if u.startswith('develop:')])==2
    store.close()


def test_btc_reference_stays_in_development_era_without_becoming_a_target(tmp_path,monkeypatch):
    from tests import compose_fixtures as C
    from trader.research.closure import manifest, synthetic
    monkeypatch.setattr(synthetic,'symbols',lambda n:[f'S{i}/USDT' if i!=6 else 'BTC/USDT' for i in range(n)])
    bundle=C.build_ctx(tmp_path,name='btc-reference',n_bars=1500)
    frames,_=manifest.development_frames(bundle.snap,bundle.env)
    assert 'BTC/USDT' not in frames and 'BTC/USDT' in bundle.env['heldout_symbols']
    ref,identity=manifest.development_reference(bundle.snap,bundle.env)
    assert identity and 'BTC/USDT' in ref
    assert int(manifest._ms(ref['BTC/USDT']['ts']).max())+synthetic.TF_MS <= bundle.env['dev_end_ms']
    assert 'BTC/USDT' not in frames
    bundle.store.close()


def test_horizon_rotation_is_predeclared_and_resumeable():
    from trader.research.closure.auto import horizon_for
    auto=dict(tf='4h',fit_bars=1620,horizons=[dict(tf='15m',fit_bars=1600),
              dict(tf='1h',fit_bars=2000),dict(tf='4h',fit_bars=1620)])
    assert [horizon_for(auto,i)['tf'] for i in range(4)]==['15m','1h','4h','15m']
    assert horizon_for(auto,1)['fit_bars']==2000
    import pytest
    with pytest.raises(ValueError):horizon_for(dict(auto,horizons=[dict(tf='1m',fit_bars=400)]),0)


def test_rotation_controls_obey_the_same_entry_eligibility(monkeypatch):
    import numpy as np
    import pandas as pd
    from types import SimpleNamespace
    from trader.strategy import null_baseline as N
    allow=np.zeros(700,dtype=bool);allow[300:350]=True
    raw=np.ones(700,dtype=bool)
    seen=[]
    monkeypatch.setattr(N,'simulate',lambda long,short,*a,**kw:seen.append((long,short)) or SimpleNamespace(trades=1,profit_factor=1.0))
    N.null_pfs(raw,raw,pd.DataFrame({'close':np.ones(700)}),None,{},draws=20,entry_eligibility=allow)
    assert len(seen)==20
    assert all(not lo[~allow].any() and not sh[~allow].any() for lo,sh in seen)


def test_falsifier_can_be_structured_without_accepting_arbitrary_prose_objects():
    import pytest
    candidate=graph.make_candidate(spec(failure_conditions=['fails after costs','does not beat control']))
    graph.verify(candidate)
    for bad in ({'untrusted':'object'},42,['valid',{}]):
        with pytest.raises(ValueError):graph.make_candidate(spec(failure_conditions=bad))
    with pytest.raises(ValueError):graph.make_candidate(spec(question={'bad':'schema'}))


def test_analysis_roles_require_real_inputs_too():
    import pytest
    c=graph.make_candidate(spec(roles={'relbtc30':'exposure'}))
    assert experiments.needs_btc(c) and 'btc' in experiments.plan(c)['required_inputs']
    with pytest.raises(ValueError,match='GAP_PROVIDER'):graph.make_candidate(spec(roles={'asset_size':'exposure'}))


def test_declared_simple_comparator_is_executable_and_keeps_exit_identity():
    from trader.research.closure import development as D
    candidate=graph.make_candidate(spec(long={'cmp':'gt','a':{'f':'rsi14'},'b':{'lit':60}},short=None,structure='long_only'))
    control=experiments.simple_control(candidate)
    assert control['exit']==candidate['exit'] and control['geo']==candidate['geo']
    assert control['short'] is None and control['long']!=candidate['long']
    graph.realize(control,'fixture')
    assert 'simple_direction_control' in dict(D._graph_baselines(candidate,{'envelope_id':'fixture'}))
    same=graph.make_candidate(spec(long={'cmp':'gt','a':{'f':'ret24'},'b':{'lit':0}},short=None,structure='long_only'))
    assert experiments.simple_control(same) is None


def test_cost_or_delay_failure_selects_a_bounded_holding_experiment(tmp_path):
    store=Store(tmp_path/'holding.db');parent=graph.make_candidate(spec())
    store.put_once('candidates',parent['candidate_id'],parent)
    result=dict(stage='DEVELOPMENT',outcome='DEVELOPMENT_POSITIVE',candidate_id=parent['candidate_id'],
        result_id='execution-stress',metrics={'total_pct':3},diagnostics={'robustness':{'edge_disappears_at':{'delay_bars':1,'cost_mult':2}}})
    item=consumer.revise(store,parent['candidate_id'],result,now_ms=2,max_revisions=1)
    assert item['status']=='COMPILED'
    child=store.get_once('candidates',item['candidate_id'])
    assert child['provenance']['change']['kind']=='holding_period'
    assert child['exit']['time']['max_bars']>0
    assert 'execution_stress' in item['reason']
    store.close()
