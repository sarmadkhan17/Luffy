"""Synthetic authority fixtures test infrastructure, never economic readiness."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys

import pytest
from trader.portfolio.allocator import (
    Bound, Candidate, Economics, Evidence, Inputs, Portfolio, Position, Proposal,
    Source, Status, allocate, canonical, digest, inputs_from_payload, persist, verify,
)

SOURCE = Source.freeze('fixture-authority', {'kind': 'synthetic-test-only', 'version': 1})
OK = Evidence(Status.ESTABLISHED, (SOURCE.source_id,))
MISSING = Evidence(Status.UNAVAILABLE, ())
UNKNOWN = Evidence(Status.UNKNOWN, ())


def economics(value='2', **kw):
    return Economics(**dict(evidence=OK, expected_net_value=value, currency='USDT',
                           horizon='4h', quantity_basis='net_return_per_unit_capital',
                           capital_basis='one_unit', estimator_contract='test-estimator.v1',
                           cost_basis='all_costs', **kw))


def candidate(name='a', instrument='venue:futures:BTCUSDT', direction='LONG', value='2'):
    return Candidate(name, instrument, 'futures', direction, 'strategy-' + name,
                     'version-' + name, digest({'spec': name}), 1000, '4h',
                     OK, OK, economics(value), OK, OK, OK, OK, OK, UNKNOWN,
                     UNKNOWN, UNKNOWN, OK, 2000,
                     tuple(Bound(authority, OK, maximum, 'base_quantity') for authority, maximum in
                           [('strategy_requested', '10'), ('risk_permitted', '3'),
                            ('account_funding', '8'), ('capacity', '6'), ('portfolio_constraints', '5')]),
                     (SOURCE.source_id,))


def inputs(*candidates, positions=(), **kw):
    base = Inputs(1500, tuple(candidates), Portfolio('snapshot', 1000, 2000, OK, positions,
                  (SOURCE.source_id,)), UNKNOWN, OK, 'ACTIVE', (SOURCE,))
    return replace(base, **kw)


def result(i):
    return json.loads(allocate(i).result_json)


def test_complete_deterministic_order():
    a, b = candidate(), candidate('b', 'venue:futures:ETHUSDT', value='4')
    i = inputs(a, b)
    assert allocate(i) == allocate(replace(i, candidates=(b, a)))
    r = result(i)
    assert r['economic_order'] == [list(b.identity), list(a.identity)]
    assert r['selected'][0]['primary_candidate'] == list(b.identity)


def test_incomplete_cannot_outrank():
    b = replace(candidate('b', 'venue:futures:ETHUSDT', value='999'), costs=MISSING)
    r = result(inputs(candidate(), b))
    assert r['selected'][0]['primary_candidate'][0] == 'a'
    assert r['candidates'][1]['feasibility'] == 'INCOMPLETE'
    assert 'COST_EVIDENCE_UNAVAILABLE' in r['candidates'][1]['refusal_reasons']


def test_all_incomplete_cash():
    c = replace(candidate(), economics=replace(economics(), evidence=MISSING, expected_net_value=None))
    r = result(inputs(c))
    assert r['decision'] == 'NO_ALLOCATION'
    assert r['cash_candidate']['selected']
    assert r['reason'] == 'INSUFFICIENT_COMPARABLE_ECONOMICS'


def test_same_asset_one_position_contributing_evidence():
    a, b = candidate(), candidate('b', value='4')
    r = result(inputs(a, b))
    assert len(r['selected']) == 1
    assert len(r['selected'][0]['evidence_contributors']) == 2
    assert r['selected'][0]['proposed_size'] == '3'  # never stack


def test_opposing_directions_conflict():
    r = result(inputs(candidate(), candidate('b', direction='SHORT')))
    assert r['decision'] == 'NO_ALLOCATION'
    assert r['conflicts']
    assert all(c['feasibility'] == 'CONFLICTED' for c in r['candidates'])


def holding(direction='LONG', value=None):
    return Position('venue:futures:BTCUSDT', 'futures', direction, '1', SOURCE.source_id,
                    economics(value) if value is not None else None)


def test_support_existing_never_resize():
    r = result(inputs(candidate(), positions=(holding(),)))
    assert r['selected'] == []
    assert r['candidates'][0]['interaction'] == 'SUPPORTS_EXISTING'
    assert r['candidates'][0]['expression'] == 'KEEP_EXISTING'
    assert r['candidates'][0]['proposed_size'] == 'UNAVAILABLE'


def test_opposes_existing():
    r = result(inputs(candidate(direction='SHORT'), positions=(holding(),)))
    assert r['candidates'][0]['interaction'] == 'CONFLICTS_EXISTING'
    assert r['candidates'][0]['feasibility'] == 'CONFLICTED'
    assert not r['selected']


def test_capacity_unavailable_no_number():
    r = result(inputs(replace(candidate(), capacity=MISSING)))
    assert not r['selected']
    assert r['candidates'][0]['proposed_size'] == 'UNAVAILABLE'


def test_stale_portfolio_fail_closed():
    i = inputs(candidate())
    r = result(replace(i, portfolio=replace(i.portfolio, valid_until_ms=1499)))
    assert r['candidates'][0]['feasibility'] == 'BLOCKED'
    assert not r['selected']


def test_smallest_authoritative_bound():
    c = candidate()
    for b in c.bounds:
        reduced = replace(c, bounds=tuple(replace(x, maximum='0.25') if x.authority == b.authority else x for x in c.bounds))
        assert result(inputs(reduced))['selected'][0]['proposed_size'] == '0.25'


def test_common_factor_retained_without_penalty():
    context = Evidence(Status.ESTABLISHED, (SOURCE.source_id,), canonical({'shared_factor': 'crypto', 'beta': None}))
    i = inputs(candidate(), relationships=context)
    r = result(i)
    assert r['common_exposure_context'] == json.loads(canonical({'status': context.status, 'source_ids': context.source_ids, 'detail_json': context.detail_json}))
    assert 'penalty' not in r
    assert r['selected'][0]['proposed_size'] == '3'


@pytest.mark.parametrize('change', ['economics', 'capacity', 'portfolio', 'relationship', 'risk', 'strategy', 'source', 'control'])
def test_tamper_or_changed_authority_refused(change):
    i = inputs(candidate())
    p = allocate(i)
    c = i.candidates[0]
    if change == 'economics':
        other = replace(i, candidates=(replace(c, economics=economics('999')),))
    elif change == 'capacity':
        other = replace(i, candidates=(replace(c, capacity=MISSING),))
    elif change == 'portfolio':
        other = replace(i, portfolio=replace(i.portfolio, positions=(holding(),)))
    elif change == 'relationship':
        other = replace(i, relationships=OK)
    elif change == 'risk':
        other = replace(i, risk_policy=MISSING)
    elif change == 'strategy':
        other = replace(i, candidates=(replace(c, spec_hash=digest('changed')),))
    elif change == 'source':
        other = replace(i, sources=(Source.freeze(SOURCE.source_id, {'changed': True}),))
    else:
        other = replace(i, control_state='FROZEN')
    assert not verify(p, other)
    assert verify(p, i)
    assert not verify(replace(p, result_json='{}'), i)


def test_restart_replay_identical_and_append_only(tmp_path):
    i = inputs(candidate())
    p = allocate(i)
    path = persist(p, tmp_path)
    assert persist(p, tmp_path) == path
    assert allocate(inputs_from_payload(json.loads(p.inputs_json))) == p
    code = ('import json,sys; from trader.portfolio.allocator import allocate,inputs_from_payload,canonical; '
            'b=json.load(open(sys.argv[1])); print(canonical(allocate(inputs_from_payload(b["inputs"])).payload()))')
    out = subprocess.check_output([sys.executable, '-c', code, str(path)], text=True)
    assert json.loads(out) == p.payload()
    path.write_text('{}')
    with pytest.raises(ValueError, match='IMMUTABLE_PROPOSAL_COLLISION'):
        persist(p, tmp_path)


def test_no_execution_control_or_llm_dependencies():
    import ast
    file = Path('trader/portfolio/allocator.py')
    tree = ast.parse(file.read_text())
    imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(any(bad in name for bad in ('trader.engine', 'journal', 'llm', 'ccxt', 'kernel')) for name in imports)
    i = inputs(candidate())
    before = canonical(allocate(i).payload())
    allocate(i)
    assert canonical(allocate(i).payload()) == before


@pytest.mark.parametrize('value', ['0', '-1'])
def test_no_positive_value_cash(value):
    assert result(inputs(candidate(value=value)))['cash_candidate']['selected']


def test_incomparable_economics_refuse_ranking():
    b = candidate('b', 'venue:futures:ETHUSDT', value='1000')
    b = replace(b, economics=replace(b.economics, capital_basis='different-size'))
    r = result(inputs(candidate(), b))
    assert r['reason'] == 'INSUFFICIENT_COMPARABLE_ECONOMICS'
    assert not r['economic_order']


def test_stale_candidate_invalid_and_missing_bounds():
    for c in [replace(candidate(), valid_until_ms=1499),
              replace(candidate(), validation=Evidence(Status.INVALID, (SOURCE.source_id,))),
              replace(candidate(), bounds=())]:
        assert not result(inputs(c))['selected']


def test_opportunity_cost_no_turnover():
    r = result(inputs(candidate('b', 'venue:futures:ETHUSDT', value='4'), positions=(holding(value='1'),)))
    assert r['opportunity_cost'][0]['status'] == 'BETTER_OPPORTUNITY_OBSERVED'
    assert r['opportunity_cost'][0]['switching'] == 'SWITCH_ECONOMICS_UNAVAILABLE'
    assert r['opportunity_cost'][0]['action'] == 'KEEP_EXISTING'


def test_unknown_switch_economics_no_claim():
    r = result(inputs(candidate('b', 'venue:futures:ETHUSDT'), positions=(holding(),)))
    assert not r['opportunity_cost']


def test_identity_and_provenance_integrity():
    with pytest.raises(ValueError):
        Source(SOURCE.source_id, SOURCE.sha256, '{}')
    with pytest.raises(ValueError):
        allocate(inputs(candidate(), sources=()))
    with pytest.raises(ValueError):
        allocate(inputs(candidate(), candidate()))
    with pytest.raises(FrozenInstanceError):
        candidate().direction = 'SHORT'
    with pytest.raises(ValueError):
        allocate(inputs(positions=(holding(), holding('SHORT'))))


def test_nested_fields_are_frozen_and_unknown_retained():
    mutable_refs = [SOURCE.source_id]
    e = Evidence(Status.ESTABLISHED, mutable_refs)
    mutable_refs.clear()
    assert e.source_ids == (SOURCE.source_id,)
    with pytest.raises(ValueError, match='MUTABLE_CONTRACT'):
        replace(candidate(), horizon={'guessed': '4h'})
    r = result(inputs(candidate()))
    assert r['candidates'][0]['relationship_context']['status'] == 'UNKNOWN'


def test_missing_size_units_no_numeric_proposal():
    c = candidate()
    c = replace(c, bounds=(replace(c.bounds[0], unit='quote_notional'), *c.bounds[1:]))
    r = result(inputs(c))
    assert not r['selected']
    assert 'SIZE_BOUND_UNITS_INCOMPARABLE' in r['candidates'][0]['unresolved_evidence']


def test_empty_book_stale_and_risk_frozen_reasons():
    i = inputs(control_state='FROZEN', risk_policy=MISSING)
    i = replace(i, portfolio=replace(i.portfolio, valid_until_ms=1499))
    r = result(i)
    assert r['cash_candidate']['selected']
    assert len(r['global_blockers']) == 3


def test_precision_independent_ordering():
    from decimal import localcontext
    a = candidate(value='2.000000000000000000001')
    b = candidate('b', 'venue:futures:ETHUSDT', value='2.000000000000000000002')
    with localcontext() as ctx:
        ctx.prec = 3
        small = allocate(inputs(a, b))
    with localcontext() as ctx:
        ctx.prec = 40
        large = allocate(inputs(a, b))
    assert small == large
    assert json.loads(small.result_json)['selected'][0]['primary_candidate'][0] == 'b'


def test_canonical_instrument_market_binding_required():
    with pytest.raises(ValueError, match='CANONICAL_INSTRUMENT'):
        candidate(instrument='BTCUSDT')
    with pytest.raises(ValueError, match='CANONICAL_INSTRUMENT'):
        replace(candidate(), market_type='spot')
