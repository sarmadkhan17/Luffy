"""Declared-source diversity and evaluation fairness. No market-success or trading-authority claims."""
from trader.research.compose import graph,search
from trader.research.closure import study
from trader.research.closure.store import Store

FACTORS=['ret24','rsi14','relvol50','fundz360','ctx_cg_total_ret30','world_volume_anomaly']


def config():
    return dict(search.DEFAULT_BUDGET,tf='4h',factors=FACTORS,symbols=10,dataset_reads=2000)


def test_learned_inputs_include_available_external_derivative_and_world_sources():
    budget=config();stream=search.candidate_stream(budget,{k:'EXECUTABLE' for k in FACTORS})
    learned=stream['learned']
    first=[p['provenance']['track'] for p in learned[:3]]
    assert first==['learned-ridge','learned-lasso','equal-weight']
    assert all(p['structure']=='long_short' for p in learned[:3])
    factor_sets=[]
    for p in learned[:3]:
        candidate=graph.make_candidate(p)
        assert search._nodes(candidate)<=budget['max_nodes']
        assert {'funding','ref:cg_total_daily','world'}<=set(graph.requires_of(candidate))
        factor_sets.append({n['f'] for n in search._walk_factors(candidate)})
    assert factor_sets[0]==factor_sets[1]==factor_sets[2]
    assert len(factor_sets[0])>=4


def test_declared_factor_subset_cannot_bring_in_unrequested_sources():
    budget=dict(config(),factors=['rsi14','relvol50','ret24'])
    stream=search.candidate_stream(budget,{k:'EXECUTABLE' for k in FACTORS})
    assert stream['learned']
    assert all({n['f'] for n in search._walk_factors(p)}<=set(budget['factors']) for p in stream['learned'])


def test_default_fit_budget_reaches_tree_regularized_models_and_matched_baseline(tmp_path):
    store=Store(tmp_path/'plan.db')
    try:
        budget=search.register_budget(store,'s',config())
        result=search.plan(store,'s',budget,{k:'EXECUTABLE' for k in FACTORS})
        candidates=[store.get_once('candidates',cid) for cid in result['admitted']]
        types={spec['model'] for c in candidates for spec in search._fit_specs(c)}
        tracks={c['provenance']['track'] for c in candidates}
        assert {'tree','ridge','lasso'}<=types
        assert 'equal-weight' in tracks
        assert result['spent']['fits']<=budget['max_fits']
    finally:store.close()


def test_retained_question_is_not_starved_and_cap_waits_for_both_producers(tmp_path):
    store=Store(tmp_path/'fair.db');sid='study'
    try:
        own=graph.make_candidate(search._spec('Retained synthetic question',{'top':{'a':{'f':'relvol50'},'p':.91,'dir':'above'}},None,track='retained',structure='long_only'))
        store.put_once('candidates',own['candidate_id'],own)
        store.claim_item('question','BRAIN','q',{'study_id':sid},0)
        store.set_item('question','COMPILED','test-only',0,candidate_id=own['candidate_id'])
        for i in range(20):
            c=graph.make_candidate(search._spec('Generated synthetic question',{'top':{'a':{'f':'ret24'},'p':.5+i/100,'dir':'above'}},None,track='single',structure='long_only'))
            store.put_once('candidates',c['candidate_id'],c)
            store.put_once('attempts',str(i),dict(study_id=sid,candidate_id=c['candidate_id'],domain='momentum',track='single'))
        spec=dict(bank_path='private-bank',seed_families=[],seed_graphs=[],search={},max_candidates=8,journal_path=None)
        study._ensure_unit(store,sid,'intake','intake');study._set_unit(store,sid,'intake','DONE',0,result={})
        study._plan(store,sid,spec,{},0)
        assert not [u for u in store.units(sid) if u['kind']=='develop']
        study._set_unit(store,sid,'search','DONE',1,result={})
        study._plan(store,sid,spec,{},1)
        units=[u for u in store.units(sid) if u['kind']=='develop']
        assert len(units)==8 and 'develop:'+own['candidate_id'] in {u['unit_id'] for u in units}
        study._plan(store,sid,spec,{},2)
        assert len([u for u in store.units(sid) if u['kind']=='develop'])==8
    finally:store.close()


def test_evaluation_cap_includes_retained_tree_models_and_matched_baseline(tmp_path):
    store=Store(tmp_path/'integrated-plan.db');sid='s'
    try:
        budget=search.register_budget(store,sid,config())
        search.plan(store,sid,budget,{k:'EXECUTABLE' for k in FACTORS})
        own=graph.make_candidate(search._spec('Retained synthetic independent question',{'top':{'a':{'f':'relvol50'},'p':.91,'dir':'above'}},None,track='retained',structure='long_only'))
        store.put_once('candidates',own['candidate_id'],own)
        store.claim_item('question','BRAIN','q',{'study_id':sid},0)
        store.set_item('question','COMPILED','test-only',0,candidate_id=own['candidate_id'])
        study._ensure_unit(store,sid,'intake','intake');study._set_unit(store,sid,'intake','DONE',0,result={})
        study._ensure_unit(store,sid,'search','search');study._set_unit(store,sid,'search','DONE',0,result={})
        spec=dict(bank_path='private-bank',seed_families=[],seed_graphs=[],search={},max_candidates=8,journal_path=None)
        study._plan(store,sid,spec,{},0)
        candidates=[store.get_once('candidates',u['unit_id'].split(':',1)[1]) for u in store.units(sid) if u['kind']=='develop']
        assert len(candidates)==8 and own['candidate_id'] in {c['candidate_id'] for c in candidates}
        assert {'ridge','lasso','tree'}<={s['model'] for c in candidates for s in search._fit_specs(c)}
        assert 'equal-weight' in {c['provenance']['track'] for c in candidates}
    finally:store.close()
