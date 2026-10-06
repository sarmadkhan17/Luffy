"""Synthetic tests never establish real economic or factor calibration."""
from dataclasses import replace, asdict
import json
from pathlib import Path
import ast
import pytest

from trader.portfolio import common_factor as C, candidate_bridge as B
from trader.portfolio.allocator import Source, Position, Portfolio, Evidence, Status, allocate, canonical, digest, inputs_from_payload
from tests.test_portfolio_allocator import candidate, inputs, test_only_models
from tests.test_candidate_bridge import exact
from tests.test_opportunity_live_integration import snapshot, IID
from tests.test_strategy_factory_handoff import T0
from trader.strategy import factory_handoff as F
from trader.strategy.spec import StrategySpec


def setup(*cs, positions=False, cap=8):
    raw = snapshot()
    if not positions:
        raw['positions'] = []
        raw['observation']['positions'] = []
        _rehash(raw)
    venue = Source.freeze('venue_position_snapshot', raw)
    known = Evidence(Status.ESTABLISHED, (venue.source_id,))
    portfolio = Portfolio(raw['snapshot_id'], 1000, 2000, known,
        tuple(Position(p['instrument_id'], 'futures', p['side'].upper(), str(p['quantity']), None) for p in raw['positions']), (venue.source_id,))
    i = inputs(*cs, portfolio=portfolio)
    i = replace(i, sources=(*i.sources, venue))
    policy = Source.freeze('owner-risk-policy', {'risk': {'max_open_positions': cap,
        'portfolio_heat_cap_pct': 15.0, 'per_symbol_risk_cap_pct': 8.0,
        'max_position_margin_pct': 20.0, 'max_total_margin_pct': 70.0}})
    return i, venue, policy


def _rehash(raw):
    o = raw['observation']
    o['observation_id'] = digest({k:v for k,v in o.items() if k != 'observation_id'})
    raw['source_identity']['observation_id'] = o['observation_id']
    raw['snapshot_id'] = digest({k:v for k,v in raw.items() if k != 'snapshot_id'})


def context(*cs, **kw):
    i, v, p = setup(*cs, **kw)
    return C.build(i,v,p)


def test_one_expression_and_opposite_conflict():
    r = json.loads(context(candidate(), candidate('b')).result_json)
    assert r['pairs'][0]['redundancy'] == 'EXACT_REDUNDANT'
    assert r['pairs'][0]['effect'] == 'ONE_EXPRESSION_NO_STACKING'
    r = json.loads(context(candidate(), candidate('b', direction='SHORT')).result_json)
    assert r['pairs'][0]['conflict']
    # Existing allocator enforces one position and opposite conflicts.
    assert len(json.loads(allocate(inputs(candidate(),candidate('b'))).result_json)['selected']) == 1
    assert not json.loads(allocate(inputs(candidate(),candidate('b',direction='SHORT'))).result_json)['selected']


def test_venue_projection_and_entry_notional():
    i,v,p = setup(positions=True)
    r = json.loads(C.build(i,v,p).result_json)['portfolio']
    assert r['positions'][0]['attribution'] == 'UNKNOWN'
    assert r['positions'][0]['notional'] is None
    assert r['positions'][0]['entry_basis_notional'] is not None
    assert r['current_risk_heat'] is None
    changed = json.loads(v.payload_json)
    changed['positions'][0]['quantity'] += 1
    changed['snapshot_id'] = digest({k:x for k,x in changed.items() if k != 'snapshot_id'})
    with pytest.raises(ValueError,match='PROJECTION'):
        C.build(i,Source.freeze(v.source_id,changed),p)


def test_current_position_support_conflict():
    for side in ('LONG','SHORT'):
        c = candidate(instrument=IID,direction=side)
        r = json.loads(context(c,positions=True).result_json)
        assert len(r['pairs']) == 1
        assert r['pairs'][0]['conflict'] == (side == 'SHORT')


def test_existing_policy_only_hard_count_boundary():
    i,v,p = setup(candidate(),positions=True,cap=1)
    r = json.loads(C.build(i,v,p).result_json)
    assert r['concentration']['checks'][0]['status'] == 'WITHIN_EXISTING_LIMITS'
    assert not r['constraints']['new_position_count_permitted']
    ai,_ = C.attach(i,v,p)
    out = json.loads(allocate(ai).result_json)
    assert 'EXISTING_OPEN_POSITION_LIMIT_NO_HEADROOM' in out['global_blockers']
    assert not out['selected']
    assert r['concentration']['status'] == 'UNAVAILABLE' # no invented heat or margin
    assert r['numeric_adjustments'] == 'NONE'


def test_missing_and_stale_book_fail_closed():
    i,v,p = setup()
    with pytest.raises(ValueError): C.build(i,Source.freeze(v.source_id,{}),p)
    with pytest.raises(ValueError,match='STALE'): C.build(replace(i,as_of_ms=1000000),v,p)
    with pytest.raises(ValueError,match='PROJECTION'): C.build(replace(i,portfolio=replace(i.portfolio,snapshot_id='journal')),v,p)


def test_identical_replay_current_sources_tamper():
    i,v,p = setup(candidate(),candidate('b'))
    r = C.build(i,v,p)
    assert r == C.build(replace(i,candidates=tuple(reversed(i.candidates))),v,p)
    assert C.verify(r,i,v,p)
    assert not C.verify(r,i,v,Source.freeze(p.source_id,{'max_open_positions':1}))
    ai,_ = C.attach(i,v,p)
    assert allocate(ai) == allocate(inputs_from_payload(json.loads(allocate(ai).inputs_json)))
    source = next(s for s in ai.sources if s.source_id == ai.exposure_source_id)
    fake = source.payload_json.replace('NOT_JUSTIFIED','JUSTIFIED')
    bad = Source.freeze(source.source_id,json.loads(fake))
    ai = replace(ai,sources=tuple(bad if s.source_id == source.source_id else s for s in ai.sources))
    assert 'PORTFOLIO_EXPOSURE_AUTHORITY_REFUSED' in json.loads(allocate(ai).result_json)['global_blockers']


def test_cash_without_economics():
    c = candidate()
    c = replace(c,economics=replace(c.economics,expected_net_value=None))
    i,v,p = setup(c)
    ai,_ = C.attach(i,v,p)
    r = json.loads(allocate(ai).result_json)
    assert r['cash_candidate']['selected']
    assert r['portfolio_exposure_context']['factor_model'] == 'NOT_JUSTIFIED'


def test_siblings_and_unrelated_proven_lineage(exact):
    j,cfg,v,args = exact
    siblings = []
    for index in range(3):
        spec = dict(v['spec']); spec['name'] = 'variant-' + str(index)
        # Material exact spec changes, never text-based similarity.
        spec['entry_long'] = f'close > sma({20+index})'
        rec = F.derive_version(j,v['version_id'],StrategySpec.from_dict(spec),at_ms=T0+10+index)
        loaded = F.load_version(j,rec['version_id'])
        c = replace(candidate(str(index)),strategy_id=loaded['strategy_id'],version_id=loaded['version_id'],spec_hash=loaded['spec_hash'])
        source = B.freeze_lineage(j,c.version_id,as_of_ms=T0+100,valid_until_ms=T0+1000)
        siblings.append((c,source))
    ls = C._lineages(tuple(s for c,s in siblings),T0+100)
    groups = C.evidence_groups([c for c,s in siblings],ls)
    assert groups['evidence_group_count'] == 1
    assert groups['shared_lineage_count'] == 2
    assert len(groups['representatives']) == 1
    assert groups['independent_evidence_count'] is None
    # Distinct root identity is distinct lineage; it does not prove independence.
    other = {'version_id':'other','strategy_id':'other','spec_hash':'other','root_version_id':'other'}
    oc = replace(candidate('other'),version_id='other',strategy_id='other',spec_hash='other')
    groups = C.evidence_groups([siblings[0][0],oc],{**ls,'other':other})
    assert groups['evidence_group_count'] == 2 and groups['shared_lineage_count'] == 0
    with pytest.raises(ValueError,match='STALE'): B.replay_lineage(siblings[0][1],T0+1001)


def test_no_mutation_imports():
    tree = ast.parse(Path(C.__file__).read_text())
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not calls & {'execute','kv_set','create_order','set_leverage','record_snapshot','record_margin'}


def measured_source(*, stale=False):
    from trader.world import (Observation, Quality, RelationshipCoordinate, RelationshipState,
        RelationshipCollection, Scope, ScopeLevel, Horizon, WorldModel, HierarchyNode)
    from trader.world.replay import WorldModelRecord
    a = Scope(ScopeLevel.INSTRUMENT,'binance_usdm:futures:BTCUSDT')
    b = Scope(ScopeLevel.INSTRUMENT,'binance_usdm:futures:ETHUSDT')
    g = Scope(ScopeLevel.GLOBAL,'world'); asset = Scope(ScopeLevel.ASSET_CLASS,'crypto')
    obs = tuple(Observation(instrument=s.identifier,timestamp_ms=1000,observed_at_ms=1000,
        available_at_ms=1000,timeframe='4h',kind='return-window',value={'window_start_ms':100,'window_end_ms':1000,'bars':30},
        source='test-measured',source_ref='window-v1',quality=Quality.VALID,
        max_age_ms=499 if stale else 1000,transform_version='rolling-pearson.v1') for s in (a,b))
    r = RelationshipState(RelationshipCoordinate(a,b,'rolling-correlation',Horizon.SWING),1000,
        {'correlation':0.99,'beta':1.2},Quality.VALID,
        'test-rolling-pearson','measured-v1',obs,
        measurement=dict(method='rolling-pearson.v1',window_start_ms=100,window_end_ms=1000,
            max_age_ms=1000,scope={'source':[a.identifier],'target':[b.identifier]},
            context={'source_cut_ms':1000,'regime':'TEST_ONLY'},
            assumptions=['aligned closed return windows','finite nonzero variance']),
        uncertainty={'instability':'unassessed outside measured window'})
    m = WorldModel(1000,(HierarchyNode(g),HierarchyNode(asset,g),HierarchyNode(a,asset),HierarchyNode(b,asset)),
        relationships=RelationshipCollection(1000,(r,)))
    return Source.freeze('relationship-test',dict(record_json=WorldModelRecord.from_model(m).to_json(),valid_until_ms=2000))


def test_measurement_preserved_without_threshold_or_penalty():
    a = candidate(instrument='binance_usdm:futures:BTCUSDT')
    b = candidate('b',instrument='binance_usdm:futures:ETHUSDT')
    i,v,p = setup(a,b)
    s = measured_source()
    r = json.loads(C.build(i,v,p,relationship_sources=(s,)).result_json)
    measurement = r['measured_relationships'][0]
    assert measurement['value']['correlation'] == 0.99
    assert measurement['horizon'] == 'swing'
    assert measurement['window_evidence'][0]['value']['bars'] == 30
    assert r['pairs'][0]['relationship'] == 'MEASURED_RELATIONSHIP'
    assert r['pairs'][0]['effect'] == 'CONTEXT_ONLY'
    assert r['numeric_adjustments'] == 'NONE'
    assert r['factor_model'] == 'NOT_JUSTIFIED'
    with pytest.raises(ValueError,match='STALE'):
        C.build(i,v,p,relationship_sources=(measured_source(stale=True),))
    with pytest.raises(ValueError,match='STALE'):
        C.build(i,v,p,relationship_sources=(Source.freeze('relationship-test',
            dict(json.loads(s.payload_json),valid_until_ms=1499)),))


def test_more_than_existing_limit_is_exceeded():
    i,v,p = setup(positions=True,cap=1)
    raw = json.loads(v.payload_json)
    other = dict(raw['positions'][0]); other['instrument_id'] = 'binance_usdm:futures:ETHUSDT'; other['symbol']='ETHUSDT'
    raw['positions'].append(other)
    obs = list(raw['observation']['positions'][0]); obs[0] = other['instrument_id']
    raw['observation']['positions'].append(obs)
    _rehash(raw)
    v = Source.freeze(v.source_id,raw)
    i = replace(i,portfolio=replace(i.portfolio,snapshot_id=raw['snapshot_id'],positions=tuple(
        Position(row['instrument_id'],'futures','LONG',str(row['quantity']),None) for row in raw['positions'])))
    r = C.build(i,v,p)
    assert json.loads(r.result_json)['concentration']['status'] == 'EXCEEDS_EXISTING_LIMIT'


def test_current_source_replacement_refuses_allocation():
    i,v,p = setup()
    ai,receipt = C.attach(i,v,p)
    newer = Source.freeze(p.source_id,{'risk':{'max_open_positions':9}})
    ai = replace(ai,sources=tuple(newer if s.source_id == p.source_id else s for s in ai.sources))
    r = json.loads(allocate(ai).result_json)
    assert 'PORTFOLIO_EXPOSURE_AUTHORITY_REFUSED' in r['global_blockers']


def test_existing_attention_correlation_is_basket_context(tmp_path):
    from tests._corr_synth import publish, NOW, SYM
    from trader.observability.investigation import source_snapshot
    path = publish(tmp_path)
    scan,bars,_ = source_snapshot(path)
    source = Source.freeze('attention-test',dict(schema='attention-relationship-input.v1',scan=scan,bars=bars,valid_until_ms=NOW+1000))
    rows = C.attention_measurements(source,NOW,{'positions':[{'instrument':'binance_usdm:futures:S1USDT'}]})
    assert len(rows) == 1
    assert rows[0]['authority'] == 'CONTEXT_ONLY'
    assert rows[0]['relationship_scope'] == 'INSTRUMENT_TO_LEAVE_ONE_OUT_PEER_BASKET'
    assert len(rows[0]['instruments']) == 1
    assert rows[0]['reference_symbols']
    assert rows[0]['value']['r_recent'] is not None
    changed = json.loads(source.payload_json)
    row = next(o for o in changed['scan']['observations'] if o['kind']=='correlation_change' and o['symbol']==SYM)
    row['value'] = 999
    with pytest.raises(ValueError):
        C.attention_measurements(Source.freeze(source.source_id,changed),NOW,{'positions':[{'instrument':'binance_usdm:futures:S1USDT'}]})


def test_full_context_deduplicates_exact_parent_variants(exact):
    j,cfg,v,args = exact
    cs,ls = [],[]
    for idx in range(3):
        spec = dict(v['spec']); spec['entry_long']=f'close > sma({20+idx})'
        rec=F.derive_version(j,v['version_id'],StrategySpec.from_dict(spec),at_ms=T0+idx+1)
        ver=F.load_version(j,rec['version_id'])
        cs.append(replace(candidate(str(idx)),strategy_id=ver['strategy_id'],version_id=ver['version_id'],spec_hash=ver['spec_hash']))
        ls.append(B.freeze_lineage(j,ver['version_id'],as_of_ms=T0+100,valid_until_ms=T0+1000))
    i,vs,pol=setup(*cs)
    raw=json.loads(vs.payload_json)
    raw['observed_at_ms']=raw['received_at_ms']=T0
    raw['request_start_ms']=T0-20
    raw['observation']['as_of_ms']=raw['observation']['response_received_ms']=T0
    raw['observation']['request_start_ms']=T0-20
    _rehash(raw)
    vs=Source.freeze(vs.source_id,raw)
    i=replace(i,as_of_ms=T0+100,portfolio=replace(i.portfolio,snapshot_id=raw['snapshot_id'],as_of_ms=T0,valid_until_ms=T0+1000),
              sources=tuple(vs if s.source_id==vs.source_id else s for s in i.sources))
    ai,receipt=C.attach(i,vs,pol,tuple(ls))
    out=json.loads(allocate(ai).result_json)['portfolio_exposure_context']
    assert out['duplicate_confidence']['shared_lineage_count']==2
    assert len(out['duplicate_confidence']['representatives'])==1
    assert all(p['redundancy']=='EXACT_REDUNDANT' and p['lineage_status']=='SHARED_LINEAGE' for p in out['pairs'])
    assert allocate(ai)==allocate(inputs_from_payload(json.loads(allocate(ai).inputs_json)))
    assert 'PORTFOLIO_EXPOSURE_AUTHORITY_UNAVAILABLE' in json.loads(allocate(replace(
        inputs(candidate()),candidates=(replace(candidate(),source_ids=(*candidate().source_ids,'candidate-bridge:test')),),
        sources=(*inputs(candidate()).sources,Source.freeze('candidate-bridge:test',{})))).result_json)['global_blockers']


def test_no_concentration_policy_is_invented():
    i,v,_=setup()
    p=Source.freeze('owner-risk-policy',{})
    r=json.loads(C.build(i,v,p).result_json)
    assert r['concentration']['status']=='UNAVAILABLE'
    assert r['constraints']['maximum_open_positions'] is None
    assert not r['constraints']['new_position_count_permitted']


def test_allocator_deduplicates_sibling_contributors_with_complete_test_economics(tmp_path):
    from tests.test_strategy_factory_handoff import _journal
    from trader.core.config import load_config
    from tests.economics_fixtures import binding,frozen
    from trader.portfolio.economics import build,to_allocator
    j,h=_journal(tmp_path); cfg=load_config()
    parent=F.create_version(j,cfg,{'kind':'research_candidate','hash':h},at_ms=100)
    parent=F.load_version(j,parent['version_id'])
    cs,ls=[],[]
    for idx in range(3):
        spec=dict(parent['spec']);spec['entry_long']=f'close > sma({20+idx})'
        rec=F.derive_version(j,parent['version_id'],StrategySpec.from_dict(spec),at_ms=200+idx)
        ver=F.load_version(j,rec['version_id'])
        c=candidate(str(idx),instrument=IID)
        b=binding(str(idx),IID,strategy_id=ver['strategy_id'],version_id=ver['version_id'],spec_hash=ver['spec_hash'])
        economic,_=to_allocator(build(frozen('2',b)))
        c=replace(c,strategy_id=ver['strategy_id'],version_id=ver['version_id'],spec_hash=ver['spec_hash'],economics=economic)
        cs.append(c)
        ls.append(B.freeze_lineage(j,c.version_id,as_of_ms=1000,valid_until_ms=2000))
    i,v,_=setup(*cs)
    # TEST-ONLY owner policy; only this configured constraint applies.
    policy=Source.freeze('owner-risk-policy',{'risk':{'max_open_positions':8}})
    ai,receipt=C.attach(i,v,policy,tuple(ls))
    r=json.loads(allocate(ai).result_json)
    assert len(r['selected'])==1
    assert len(r['selected'][0]['evidence_contributors'])==1
    assert r['selected'][0]['duplicate_confidence']['shared_lineage_count']==2
    assert r['selected'][0]['proposed_size']=='3'
    assert r['portfolio_exposure_context']['concentration']['status']=='WITHIN_EXISTING_LIMITS'
    assert r['selected'][0]['duplicate_confidence']['independent_evidence_count'] is None


def test_replay_in_new_process(tmp_path):
    import subprocess,sys
    i,v,p=setup()
    ai,receipt=C.attach(i,v,p)
    proposal=allocate(ai)
    path=tmp_path/'proposal.json';path.write_text(canonical(proposal.payload()))
    code="""import json,sys
from pathlib import Path
from trader.portfolio.allocator import allocate,inputs_from_payload
body=json.loads(Path(sys.argv[1]).read_text())
print(allocate(inputs_from_payload(body['inputs'])).proposal_id)
"""
    r=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True,check=True)
    assert r.stdout.strip()==proposal.proposal_id
