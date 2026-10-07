"""Detached contract tests; every positive calibration/size is TEST-ONLY."""
from dataclasses import asdict, replace
from decimal import localcontext
import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from trader.portfolio import allocator as A, opportunity_cost as O, reoptimization as R, trade_intent as T
from trader.portfolio import candidate_bridge as B, opportunity_live as L, economics as E
from tests.test_portfolio_allocator import candidate, inputs, holding, OK, MISSING, SOURCE
from tests.test_candidate_bridge import exact  # noqa: F401
from tests.test_opportunity_live_integration import snapshot, IID
from tests.economics_fixtures import install_models, frozen


@pytest.fixture(autouse=True)
def models(monkeypatch):
    install_models(monkeypatch)


def switch_inputs(value='4', cost='1', impact='0', action='CLOSE'):
    i = inputs(candidate('b', 'venue:futures:ETHUSDT', value=value), positions=(holding(value='1'),))
    raw = dict(schema=O.SCHEMA, binding=json.loads(A.canonical(O.binding(i, i.candidates[0], i.portfolio.positions[0]))),
        method='TEST-ONLY-switch', version='1', switching_cost=cost, portfolio_impact=impact,
        requested_action=action, units='USDT', capital_constrained=True, captured_ms=1000,
        valid_until_ms=2000, provenance={'authority': 'TEST-ONLY'}, limitations=['SYNTHETIC'], test_only=True)
    s = A.Source.freeze('TEST-ONLY-switch', raw)
    return replace(i, sources=(*i.sources, s))


def switch_result(i):
    return json.loads(A.allocate(i).result_json)['opportunity_cost'][0]


@pytest.mark.parametrize('cost,impact,net,action', [('1', '0', '2', 'CLOSE'), ('3', '0', '0', 'NO_ACTION'),
    ('4', '0', '-1', 'NO_ACTION'), ('1', '-3', '-1', 'NO_ACTION'), ('1', '2', '4', 'CLOSE')])
def test_switch_net_includes_cost_and_portfolio_impact(monkeypatch, cost, impact, net, action):
    monkeypatch.setitem(O.SWITCH_MODELS, ('TEST-ONLY-switch', '1'), lambda raw: raw['test_only'] is True)
    i = switch_inputs(cost=cost, impact=impact)
    before = A.canonical(asdict(i))
    r = switch_result(i)
    assert A.number(r['incremental_net_benefit']) == A.number(net) and r['recommendation'] == action
    assert r['action'] == 'KEEP_EXISTING' and r['side_effects'] == 'NONE'
    assert A.canonical(asdict(i)) == before
    assert r['risk_final_gate_required']


def test_unregistered_switch_claim_stays_unavailable():
    r = switch_result(switch_inputs())
    assert r['status'] == 'SWITCH_ECONOMICS_UNAVAILABLE'
    assert r['switching'] == 'SWITCH_ECONOMICS_UNAVAILABLE' and r['incremental_net_benefit'] is None
    assert r['recommendation'] == 'NO_ACTION'


@pytest.mark.parametrize('field,value', [('valid_until_ms', 1499), ('captured_ms', 1501),
    ('units', 'USD'), ('switching_cost', '-1'), ('portfolio_impact', 'NaN'),
    ('capital_constrained', False), ('provenance', {}), ('requested_action', 'OPEN')])
def test_switch_refuses_invalid_scope(monkeypatch, field, value):
    monkeypatch.setitem(O.SWITCH_MODELS, ('TEST-ONLY-switch', '1'), lambda raw: True)
    i = switch_inputs()
    raw = json.loads(i.sources[-1].payload_json)
    raw[field] = value
    i = replace(i, sources=(*i.sources[:-1], A.Source.freeze(i.sources[-1].source_id, raw)))
    r = switch_result(i)
    assert r['switching'] == 'SWITCH_ECONOMICS_UNAVAILABLE' and r['recommendation'] == 'NO_ACTION'


def test_switch_holding_identity_and_changed_book(monkeypatch):
    monkeypatch.setitem(O.SWITCH_MODELS, ('TEST-ONLY-switch', '1'), lambda raw: True)
    i = switch_inputs()
    p = i.portfolio.positions[0]
    other = replace(i, portfolio=replace(i.portfolio, positions=(replace(p, instrument='venue:futures:SOLUSDT'),)))
    assert switch_result(other)['switching'] == 'SWITCH_ECONOMICS_UNAVAILABLE'
    other = replace(i, portfolio=replace(i.portfolio, snapshot_id='changed'))
    assert switch_result(other)['switching'] == 'SWITCH_ECONOMICS_UNAVAILABLE'
    assert not A.verify(A.allocate(i), other)


def test_frozen_switch_never_recommends(monkeypatch):
    monkeypatch.setitem(O.SWITCH_MODELS, ('TEST-ONLY-switch', '1'), lambda raw: True)
    i = switch_inputs()
    i = replace(i, control_state='FROZEN')
    raw = json.loads(i.sources[-1].payload_json)
    raw['binding'] = json.loads(A.canonical(O.binding(i, i.candidates[0], i.portfolio.positions[0])))
    i = replace(i, sources=(*i.sources[:-1], A.Source.freeze(i.sources[-1].source_id, raw)))
    assert switch_result(i)['recommendation'] == 'NO_ACTION'


def test_switch_precision_independent(monkeypatch):
    monkeypatch.setitem(O.SWITCH_MODELS, ('TEST-ONLY-switch', '1'), lambda raw: True)
    i = switch_inputs(cost='0.123456789123456789')
    expected = switch_result(i)
    with localcontext() as ctx:
        ctx.prec = 2
        assert switch_result(i) == expected


def events(receipt):
    return json.loads(receipt.result_json)['events']


def test_no_events_does_not_invoke_allocator(monkeypatch):
    i = inputs(candidate())
    monkeypatch.setattr(R, 'allocate', lambda _: pytest.fail('forced cycle optimization'))
    gate, proposal, intents = R.reoptimize(i, replace(i, as_of_ms=1501))
    assert events(gate) == [] and proposal is None and intents == ()
    assert not json.loads(gate.result_json)['trigger']


def test_exact_new_opportunity_and_processed_event():
    old, new = inputs(), inputs(candidate())
    gate, proposal, intents = R.reoptimize(old, new)
    assert events(gate)[0]['kind'] == 'NEW_OPPORTUNITY' and events(gate)[0]['trigger']
    assert proposal and intents and R.replay(gate) == gate
    processed = [e['event_id'] for e in events(gate)]
    gate, proposal, intents = R.reoptimize(old, new, processed_ids=processed)
    assert proposal is None and intents == ()
    assert not json.loads(gate.result_json)['trigger']


def test_exact_exit_and_risk_transition():
    old = inputs(positions=(holding(),))
    new = inputs(control_state='FROZEN')
    e = events(R.evaluate(old, new))
    triggered = {v['kind'] for v in e if v['trigger']}
    assert triggered == {'POSITION_EXIT', 'RISK_STATE_CHANGE'}
    assert all(v['materiality'] == 'EXACT_EVENT' for v in e if v['trigger'])


@pytest.mark.parametrize('kind', ['confidence', 'world', 'risk', 'capital', 'relationships'])
def test_uncalibrated_context_never_triggers(kind):
    old = inputs(candidate())
    c = old.candidates[0]
    context = A.Evidence(A.Status.ESTABLISHED, (SOURCE.source_id,), A.canonical({'TEST-ONLY-change': kind}))
    if kind == 'confidence': new = replace(old, candidates=(replace(c, evidence_quality=context),))
    elif kind == 'world': new = replace(old, candidates=(replace(c, regime_world=context),))
    elif kind == 'risk': new = replace(old, risk_policy=context)
    elif kind == 'relationships': new = replace(old, relationships=context)
    else:
        s = A.Source.freeze('TEST-ONLY-capital', {'equity': '101', 'test_only': True})
        new = replace(old, sources=(*old.sources, s), portfolio=replace(old.portfolio, source_ids=(s.source_id,)))
    gate = R.evaluate(old, new)
    assert events(gate) and not any(e['trigger'] for e in events(gate))
    assert all(e['materiality'] == 'UNAVAILABLE' for e in events(gate))


def test_registered_materiality_only(monkeypatch):
    old = inputs(candidate())
    new = replace(old, relationships=OK)
    gate = R.evaluate(old, new)
    e = next(e for e in events(gate) if e['kind'] == 'RELATIONSHIP_CHANGE')
    s = A.Source.freeze('TEST-ONLY-materiality', dict(binding=e['binding'], method='TEST-ONLY', version='1',
        material=True, captured_ms=1000, valid_until_ms=2000, provenance={'TEST-ONLY': True}))
    assert not json.loads(R.evaluate(old, new, materiality_sources=(s,)).result_json)['trigger']
    monkeypatch.setitem(R.MATERIALITY_MODELS, ('RELATIONSHIP_CHANGE', 'TEST-ONLY', '1'), lambda _: True)
    gate = R.evaluate(old, new, materiality_sources=(s,))
    assert json.loads(gate.result_json)['trigger'] and R.replay(gate) == gate
    raw = json.loads(s.payload_json); raw['valid_until_ms'] = 1499
    assert not json.loads(R.evaluate(old, new, materiality_sources=(A.Source.freeze(s.source_id, raw),)).result_json)['trigger']


def test_initial_context_and_unknown_risk_no_auto_trigger():
    assert not json.loads(R.evaluate(None, inputs()).result_json)['trigger']
    old = inputs(control_state='UNKNOWN')
    assert not json.loads(R.evaluate(old, inputs(control_state='ACTIVE')).result_json)['trigger']


def test_event_stale_reversed_duplicate_book_and_rehashed_tamper():
    i = inputs(candidate())
    with pytest.raises(ValueError, match='STALE'):
        R.evaluate(None, replace(i, as_of_ms=2001))
    with pytest.raises(ValueError, match='REVERSED'):
        R.evaluate(i, replace(i, as_of_ms=1499))
    with pytest.raises(ValueError, match='DUPLICATE_POSITION'):
        R.evaluate(None, inputs(positions=(holding(), holding())))
    e = R.evaluate(None, i)
    with pytest.raises(ValueError, match='REPLAY'):
        R.replay(replace(e, result_json='{}'))


def test_intent_identity_size_refusal_and_keep():
    i = inputs(candidate())
    p = A.allocate(i)
    intent = T.build(p, i)[0]
    raw = intent.payload()
    assert raw['allocation_proposal_id'] == p.proposal_id and raw['version_id'] == i.candidates[0].version_id
    assert raw['requested_action'] == 'OPEN' and raw['requested_size'] == '3'
    assert raw['status'] == 'INCOMPLETE' and 'OPPORTUNITY_CONTEXT_UNAVAILABLE' in raw['refusal_reasons']
    missing = inputs(replace(candidate(), bounds=()))
    raw = T.build(A.allocate(missing), missing)[0].payload()
    assert raw['requested_action'] == 'NO_ACTION' and raw['requested_size'] is None and raw['status'] == 'REFUSED'
    kept = inputs(candidate(), positions=(holding(),))
    raw = T.build(A.allocate(kept), kept)[0].payload()
    assert raw['requested_action'] == 'KEEP' and raw['requested_size'] is None
    assert raw['existing_position_interaction'] == 'SUPPORTS_EXISTING'
    assert raw['risk_approval'] == 'NOT_REQUESTED' and raw['execution_routed'] is False


def test_intents_current_sources_and_integrity_refused():
    i = inputs(candidate())
    p = A.allocate(i)
    intents = T.build(p, i)
    assert T.verify_intents(intents, p, i)
    other = replace(i, portfolio=replace(i.portfolio, snapshot_id='other'))
    assert not T.verify_intents(intents, p, other)
    with pytest.raises(ValueError, match='CURRENT_ALLOCATION'):
        T.build(p, other)
    with pytest.raises(ValueError, match='INTEGRITY'):
        T.TradeIntent(intents[0].intent_id, '{}')


def test_live_candidate_cannot_use_other_frozen_book(exact):
    j, cfg, _, args = exact
    cut = args['as_of_ms']
    venue = snapshot(None, cut)
    args = {**args, 'sources': (*args['sources'], L.source('portfolio', venue, cut, cut+1000))}
    c, er, sources = B.build(j, L.produce(**args), cfg, available_inputs=['ohlcv'],
                           dimensions=__import__('scripts.opportunity_context_shadow', fromlist=['DIMENSIONS']).DIMENSIONS)
    known = A.Evidence(A.Status.ESTABLISHED, (sources[0].source_id,))
    portfolio = A.Portfolio(venue['snapshot_id'], cut, cut+1000, known, (), (sources[0].source_id,))
    i = A.Inputs(cut, (c,), portfolio, known, known, 'ACTIVE', sources)
    assert A._portfolio_context_reasons(c, i) == []
    other = replace(i, portfolio=replace(portfolio, snapshot_id='different'))
    assert 'OPPORTUNITY_PORTFOLIO_CUT_UNAVAILABLE_OR_DIFFERS' in json.loads(A.allocate(other).result_json)['candidates'][0]['refusal_reasons']
    with pytest.raises(ValueError, match='CUT_DIFFERS'):
        R.evaluate(None, other)


def test_new_process_replay_and_authority_boundaries(tmp_path):
    from scripts.stage6_shadow import audit
    i = inputs(candidate(), positions=(holding(value='1'),))
    p = A.allocate(i)
    # Legacy candidate still produces a typed KEEP, without an execution call.
    body = audit(p, i)
    path = tmp_path / 'proposal.json'; path.write_text(A.canonical(p.payload()))
    code = ('import json,sys; from tests.economics_fixtures import install_models; install_models(); '
            'from trader.portfolio.allocator import *; from scripts.stage6_shadow import audit; '
            'b=json.load(open(sys.argv[1])); i=inputs_from_payload(b["inputs"]); '
            'print(canonical(audit(allocate(i),i)))')
    assert json.loads(subprocess.check_output([sys.executable, '-c', code, str(path)], text=True)) == body
    for name in ('opportunity_cost', 'trade_intent', 'reoptimization'):
        tree = ast.parse(Path('trader/portfolio', name+'.py').read_text())
        imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(any(bad in name for bad in ('engine.executor', 'journal', 'risk', 'ccxt', 'kernel', 'llm')) for name in imports)


def ready_candidate(exact, monkeypatch):
    """TEST-ONLY registered readiness adapter and calibrated economics.

    The later exposure reader is a TEST-ONLY double for marked/heat/margin
    authority, which does not exist in the retained real snapshot. The actual
    Factory/context/economics/book/TradeIntent replay machinery is used.
    """
    from tests.test_strategy_factory_handoff import INPUTS
    from scripts.opportunity_context_shadow import DIMENSIONS
    j, cfg, _, args = exact
    cut = args['as_of_ms']
    venue = snapshot(None, cut)
    args = {**args, 'sources': (*args['sources'], L.source('portfolio', venue, cut, cut+1000))}
    receipt = L.produce(**args)
    authority = B.freeze_authority(j, receipt, cfg, INPUTS)
    raw = dict(schema=B.ALLOCATION_SCHEMA, method='TEST-ONLY-ready', version='1', test_only=True,
        binding=B.allocation_binding(receipt, authority), captured_ms=cut, valid_until_ms=cut+1000,
        provenance={'TEST-ONLY': True}, bounds=[dict(authority=a, maximum='1', unit='base_quantity')
        for a in ('strategy_requested', 'risk_permitted', 'account_funding', 'capacity', 'portfolio_constraints')],
        established_dimensions=['probation', 'capacity', 'account_instrument', 'risk_compatibility', 'evidence_quality'])
    readiness = A.Source.freeze('TEST-ONLY-readiness', raw)
    monkeypatch.setitem(B.ALLOCATION_AUTHORITY_ADAPTERS, ('TEST-ONLY-ready', '1'), lambda r, *args: r.get('test_only') is True)
    # Existing TEST-ONLY model uses its own measured window/freshness. Shift
    # source clocks explicitly to this fixture's Factory cut.
    eb = L.economic_binding(receipt, **DIMENSIONS)
    ei = frozen('2', eb)
    def retime(s):
        r = json.loads(s.payload_json); r.update(captured_ms=cut, valid_until_ms=cut+1000)
        return A.Source.freeze(s.source_id, r)
    gross, cost = retime(ei.gross), retime(ei.costs)
    reserve_raw = json.loads(retime(ei.uncertainty).payload_json)
    reserve_raw.update(gross_source_sha256=gross.sha256, cost_source_sha256=cost.sha256)
    ei = replace(ei, gross=gross, costs=cost, uncertainty=A.Source.freeze(ei.uncertainty.source_id, reserve_raw),
                 context=(*ei.context, receipt.as_source(), authority, readiness))
    er = E.build(ei)
    c, sources = B.candidate(receipt, er, authority)
    assert c.economics.evidence.status == A.Status.ESTABLISHED
    known = A.Evidence(A.Status.ESTABLISHED, (readiness.source_id,))
    exposure_source = A.Source.freeze('TEST-ONLY-exposure', {'inputs': {'lineage_sources': []}})
    exposure = dict(concentration={'status': 'WITHIN_EXISTING_LIMITS'},
        constraints={'new_position_count_permitted': True}, pairs=[], measured_relationships=[], duplicate_confidence={})
    monkeypatch.setattr(__import__('trader.portfolio.common_factor', fromlist=['from_allocator']), 'from_allocator', lambda _: exposure)
    book = A.Portfolio(venue['snapshot_id'], cut, cut+1000, known, (), (readiness.source_id,))
    i = A.Inputs(cut, (c,), book, known, known, 'ACTIVE', (*sources, exposure_source), exposure_source.source_id)
    return i, receipt, authority, readiness


def test_registered_readiness_complete_typed_open(exact, monkeypatch):
    i, receipt, authority, source = ready_candidate(exact, monkeypatch)
    p = A.allocate(i)
    assert json.loads(p.result_json)['selected']
    intent = T.build(p, i)[0].payload()
    assert intent['status'] == 'COMPLETE' and intent['requested_action'] == 'OPEN'
    assert intent['context_id'] == receipt.context.context_id and intent['requested_size'] == '1'
    assert intent['risk_final_authority'] and not intent['execution_routed']
    assert T.verify_intents(T.build(p, i), p, i)
    c = i.candidates[0]
    # Unregistering the authority invalidates current replay; it cannot be
    # trusted merely because its previous receipt was content-addressed.
    monkeypatch.delitem(B.ALLOCATION_AUTHORITY_ADAPTERS, ('TEST-ONLY-ready', '1'))
    assert not A.verify(p, i)


@pytest.mark.parametrize('field,value', [('captured_ms', -1), ('valid_until_ms', 0),
    ('provenance', {}), ('binding', {}), ('established_dimensions', ['validation'])])
def test_registered_readiness_scope_refused(exact, monkeypatch, field, value):
    i, receipt, authority, source = ready_candidate(exact, monkeypatch)
    raw = json.loads(source.payload_json); raw[field] = value
    er = E.from_payload(json.loads(i.candidates[0].economics.receipt_json))
    ei = E.from_inputs(json.loads(er.inputs_json))
    ei = replace(ei, context=tuple(A.Source.freeze(s.source_id, raw) if s.source_id == source.source_id else s for s in ei.context))
    with pytest.raises(ValueError, match='ALLOCATION_'):
        B.candidate(receipt, E.build(ei), authority)


def test_stage6_shadow_read_only_sources(tmp_path, monkeypatch):
    from scripts.stage6_shadow import run
    from scripts import opportunity_context_shadow as shadow
    from tests.test_portfolio_common_factor_shadow import sources
    j, path = sources(tmp_path)
    before = j.query('SELECT * FROM state_kv')
    monkeypatch.setattr(shadow.time, 'time_ns', lambda: 1000 * 1000000)
    result = run(path, path.parent/'no-attention.db', path.parent/'no-investigation.db',
                 {'risk': {'max_open_positions': 8}}, tmp_path/'audit')
    assert result['status'] == 'PASS' and result['candidate_count'] == 0 and result['holdings_count'] == 1
    assert result['cash_selected'] and result['trade_intent_count'] == 0 and result['replay'] == 'PASS'
    assert j.query('SELECT * FROM state_kv') == before
    assert result['production_mutations'] == result['authenticated_requests'] == 0
    assert not (path.parent/'no-attention.db').exists()
    with pytest.raises(ValueError, match='OUTSIDE_PRODUCTION'):
        run(path, path, path, {}, path.parent/'output')


def test_previous_allocator_revision_cannot_be_current_authority():
    i = inputs(candidate())
    p = A.allocate(i)
    assert p.allocator_version == 'LUFFY-PORTFOLIO-ALLOCATOR-R3'
    assert not A.verify(replace(p, allocator_version='LUFFY-PORTFOLIO-ALLOCATOR-R1'), i)


def test_unregistered_readiness_never_supplies_bounds(exact, monkeypatch):
    i, receipt, authority, _ = ready_candidate(exact, monkeypatch)
    er = E.from_payload(json.loads(i.candidates[0].economics.receipt_json))
    monkeypatch.delitem(B.ALLOCATION_AUTHORITY_ADAPTERS, ('TEST-ONLY-ready', '1'))
    c, _ = B.candidate(receipt, er, authority)
    assert c.bounds == () and c.account_instrument.status == A.Status.UNKNOWN


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'unit', 'negative'])
def test_readiness_bound_shape_refused(exact, monkeypatch, kind):
    i, receipt, authority, source = ready_candidate(exact, monkeypatch)
    raw = json.loads(source.payload_json)
    if kind == 'missing': raw['bounds'] = raw['bounds'][:-1]
    elif kind == 'duplicate': raw['bounds'].append(raw['bounds'][0])
    elif kind == 'unit': raw['bounds'][0]['unit'] = None
    else: raw['bounds'][0]['maximum'] = '-1'
    er = E.from_payload(json.loads(i.candidates[0].economics.receipt_json))
    ei = E.from_inputs(json.loads(er.inputs_json))
    ei = replace(ei, context=tuple(A.Source.freeze(s.source_id, raw) if s.source_id == source.source_id else s for s in ei.context))
    with pytest.raises(ValueError, match='ALLOCATION_BOUND'):
        B.candidate(receipt, E.build(ei), authority)


def test_rehashed_complete_open_without_size_is_invalid():
    i = inputs(candidate())
    raw = json.loads(T.build(A.allocate(i), i)[0].payload_json)
    raw.update(status='COMPLETE', requested_size=None, context_id='TEST-ONLY-context')
    with pytest.raises(ValueError, match='OPEN_INCOMPLETE'):
        T.TradeIntent(A.digest(raw), A.canonical(raw))
    raw['requested_action'] = 'CLOSE'
    with pytest.raises(ValueError):
        T.TradeIntent(A.digest(raw), A.canonical(raw))
