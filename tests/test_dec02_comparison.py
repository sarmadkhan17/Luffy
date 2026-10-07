"""DEC-02: deterministic comparison against CASH. TEST-ONLY synthetic authorities."""
from dataclasses import replace
import itertools
import json
import subprocess
import sys

import pytest
from trader.portfolio import comparison as C, economics as E
from trader.portfolio.allocator import Evidence, Status, allocate, canonical, inputs_from_payload
from tests.test_portfolio_allocator import (candidate, inputs, economics, test_only_models,  # noqa: F401
                                            MISSING, OK, UNKNOWN)


def cmp(i):
    return C.decide(i)


def row(view, name):
    return next(r for r in view['candidates'] if r['identity'][0] == name)


def test_zero_candidates_is_explicit_cash():
    v = cmp(inputs())
    assert v['decision'] == 'NO_ALLOCATION' and v['cash']['selected'] and v['candidates'] == []
    assert v['cash']['expression'] == 'CASH' and 'NO_CANDIDATES' in v['cash']['reasons']


def test_unavailable_economics_rejected_cash_wins_and_is_not_zero():
    c = replace(candidate(), economics=replace(economics(), evidence=MISSING, expected_net_value=None))
    v = cmp(inputs(c))
    r = row(v, 'a')
    assert r['outcome'] == 'REJECTED' and 'EXPECTED_ECONOMICS_UNAVAILABLE' in r['reasons']
    assert r['expected_net_value'] is None and r['economic_rank'] is None
    assert v['cash']['selected'] and 'ALL_CANDIDATES_REJECTED' in v['cash']['reasons']
    assert r['confidence_components']['economics_confidence']['status'] == Status.UNAVAILABLE.value


@pytest.mark.parametrize('value', ['0', '-1'])
def test_non_positive_net_cannot_beat_cash(value):
    v = cmp(inputs(candidate(value=value)))
    r = row(v, 'a')
    assert v['decision'] == 'NO_ALLOCATION' and v['cash']['selected']
    assert r['outcome'] == 'REJECTED' and r['reasons'] == ['CAPITAL_PRIORITY_NOT_SELECTED']
    assert 'NO_POSITIVE_COMPARABLE_ECONOMIC_VALUE' in v['cash']['reasons']
    assert r['economic_rank'] == 1  # comparable, but cash outranks it


def test_positive_gross_with_missing_costs_or_negative_net_is_cash():
    c = replace(candidate(value='999'), costs=MISSING)
    v = cmp(inputs(c))
    assert row(v, 'a')['outcome'] == 'REJECTED' and 'COST_EVIDENCE_UNAVAILABLE' in row(v, 'a')['reasons']
    assert v['cash']['selected']
    neg = cmp(inputs(candidate(value='-1')))   # gross 2.1 > 0, costs + reserve exceed it
    assert neg['cash']['selected'] and row(neg, 'a')['outcome'] == 'REJECTED'


@pytest.mark.parametrize('field', ['validation', 'probation', 'costs'])
def test_missing_required_confidence_component_rejects_and_is_exposed(field):
    v = cmp(inputs(replace(candidate(), **{field: UNKNOWN})))
    r = row(v, 'a')
    comp = {'validation': 'evidence_strength', 'probation': 'strategy_reliability',
            'costs': 'execution_cost_confidence'}[field]
    assert r['outcome'] == 'REJECTED' and v['cash']['selected']
    assert r['confidence_components'][comp] == dict(status='UNKNOWN', source_ids=[], gating=True, score=None)
    assert any(x.endswith('_UNAVAILABLE') for x in r['reasons'])


def test_confidence_components_are_states_never_a_score_or_neutral_default():
    v = cmp(inputs(candidate()))
    comps = row(v, 'a')['confidence_components']
    assert set(comps) == {'evidence_strength', 'strategy_reliability', 'data_quality',
                          'regime_context_fit', 'execution_cost_confidence', 'economics_confidence'}
    assert all(c['score'] is None for c in comps.values())
    assert comps['regime_context_fit']['status'] == 'UNKNOWN' and not comps['regime_context_fit']['gating']
    assert v['confidence_policy'] == 'COMPONENT_STATES_NO_WEIGHTS_NO_COMPOSITE'
    assert not any('weight' in k or 'composite' in k or k == 'score' for k in v)


def test_proposed_winner_inferior_loser_and_cash_not_selected():
    a, b = candidate(), candidate('b', 'venue:futures:ETHUSDT', value='4')
    v = cmp(inputs(a, b))
    assert v['decision'] == 'ALLOCATION_PROPOSAL' and not v['cash']['selected']
    assert row(v, 'b')['outcome'] == 'PROPOSED' and row(v, 'a')['outcome'] == 'REJECTED'
    assert row(v, 'a')['reasons'] == ['CAPITAL_PRIORITY_NOT_SELECTED']
    assert v['ranking'] == [['b', 'version-b'], ['a', 'version-a']]
    assert v['cash']['reasons'] == ['OUTRANKED_BY_POSITIVE_COMPARABLE_ECONOMICS']
    assert all(r['outcome'] in ('PROPOSED', 'REJECTED') and r['reasons'] for r in v['candidates'])


def test_stale_or_mismatched_receipt_refuses_candidate():
    a = candidate()
    other = economics('9', 'zzz')
    v = cmp(inputs(replace(a, economics=other)))
    assert row(v, 'a')['outcome'] == 'REJECTED' and 'EXPECTED_ECONOMICS_RECEIPT_REFUSED' in row(v, 'a')['reasons']
    assert v['cash']['selected']
    stale = cmp(replace(inputs(a), as_of_ms=2500))
    assert stale['cash']['selected'] and row(stale, 'a')['outcome'] == 'REJECTED'


def test_opposing_strategy_directions_conflict_is_rejection_not_collapsed():
    a, b = candidate(), candidate('b', direction='SHORT', value='9')
    v = cmp(inputs(a, b))
    assert v['cash']['selected']
    assert all('OPPOSING_STRATEGY_DIRECTIONS' in r['reasons'] for r in v['candidates'])


TIE = 'EXACT_ECONOMIC_TIE_NO_DOMINANT_CANDIDATE'


def test_two_candidate_exact_positive_tie_selects_cash():
    a = candidate('a', 'venue:futures:BTCUSDT', value='3')
    b = candidate('b', 'venue:futures:ETHUSDT', value='3')
    i = inputs(a, b)
    r = json.loads(allocate(i).result_json)
    assert r['decision'] == 'NO_ALLOCATION' and r['selected'] == [] and r['reason'] == TIE
    assert r['cash_candidate']['selected'] and r['cash_candidate']['action'] == 'NO_TRADE'
    v = cmp(i)
    assert v['tie'] == dict(rule=C.TIE_CASH_RULE, tied=[['a', 'version-a'], ['b', 'version-b']], winner=None)
    assert all(x['outcome'] == 'REJECTED' and TIE in x['reasons'] for x in v['candidates'])
    assert v['cash']['selected'] and TIE in v['cash']['reasons']
    assert not any('score' in k for k in v)


def test_three_way_top_tie_cash_and_lower_candidates_do_not_matter():
    tied = [candidate(n, f'venue:futures:{s}USDT', value='5') for n, s in (('a', 'BTC'), ('b', 'ETH'), ('c', 'SOL'))]
    base = cmp(inputs(*tied))
    assert base['cash']['selected'] and base['tie']['tied'] == [[n, 'version-' + n] for n in 'abc']
    with_lower = cmp(inputs(*tied, candidate('d', 'venue:futures:XRPUSDT', value='1'),
                            candidate('e', 'venue:futures:ADAUSDT', value='-2')))
    assert with_lower['cash']['selected'] and with_lower['tie'] == base['tie']
    assert row(with_lower, 'd')['reasons'] == ['CAPITAL_PRIORITY_NOT_SELECTED']
    assert all(row(with_lower, n)['reasons'] == [TIE] for n in 'abc')


def test_tie_below_a_strictly_better_candidate_does_not_block_it():
    v = cmp(inputs(candidate('a', 'venue:futures:BTCUSDT', value='3'), candidate('b', 'venue:futures:ETHUSDT', value='3'),
                   candidate('c', 'venue:futures:SOLUSDT', value='4')))
    assert row(v, 'c')['outcome'] == 'PROPOSED' and v['tie'] is None and not v['cash']['selected']


def test_slight_real_difference_beats_tie():
    v = cmp(inputs(candidate('a', 'venue:futures:BTCUSDT', value='3.000001'),
                   candidate('b', 'venue:futures:ETHUSDT', value='3')))
    assert row(v, 'a')['outcome'] == 'PROPOSED' and row(v, 'b')['reasons'] == ['CAPITAL_PRIORITY_NOT_SELECTED']


def test_non_positive_tie_keeps_existing_cash_semantics():
    r = json.loads(allocate(inputs(candidate('a', value='-1'), candidate('b', 'venue:futures:ETHUSDT', value='-1'))).result_json)
    assert r['reason'] == 'NO_POSITIVE_COMPARABLE_ECONOMIC_VALUE' and r['exact_tie'] == []


def test_same_expression_duplicates_are_not_a_tie():
    a = candidate('a', value='3')
    b = candidate('b', value='3')       # same instrument + direction: evidence merge, not rivalry
    r = json.loads(allocate(inputs(a, b)).result_json)
    assert r['exact_tie'] == [] and r['decision'] == 'ALLOCATION_PROPOSAL'


def test_r2_history_replays_with_identity_order_winner_and_r3_is_distinct():
    from trader.portfolio import allocator as A
    a = candidate('a', 'venue:futures:BTCUSDT', value='3')
    b = candidate('b', 'venue:futures:ETHUSDT', value='3')
    i = inputs(b, a)
    old = A.allocate(i, A.VERSION_R2)
    new = allocate(i)
    assert old.allocator_version == A.VERSION_R2 != new.allocator_version == A.VERSION
    assert json.loads(old.result_json)['selected'][0]['primary_candidate'] == ['a', 'version-a']
    assert 'exact_tie' not in json.loads(old.result_json)
    assert old.proposal_id != new.proposal_id
    assert A.verify(old, i) and A.verify(new, i)
    assert not A.verify(replace(old, allocator_version=A.VERSION), i)       # versions are not interchangeable
    assert not A.verify(replace(new, allocator_version=A.VERSION_R2), i)
    # replay of a stored R2 proposal through its persisted inputs reproduces it exactly
    assert A.allocate(inputs_from_payload(json.loads(old.inputs_json)), A.VERSION_R2) == old
    assert C.compare(old)['tie']['rule'] == C.TIE_RULE and C.compare(old)['decision'] == 'ALLOCATION_PROPOSAL'
    assert C.compare(new)['decision'] == 'NO_ALLOCATION'
    with pytest.raises(ValueError, match='ALLOCATOR_VERSION_UNSUPPORTED'):
        A.allocate(i, 'LUFFY-PORTFOLIO-ALLOCATOR-R1')


def test_r3_tie_replays_identically_and_is_permutation_independent():
    cs = [candidate(n, f'venue:futures:{s}USDT', value='3') for n, s in (('a', 'BTC'), ('b', 'ETH'), ('c', 'SOL'))]
    first = allocate(inputs(*cs))
    assert allocate(inputs_from_payload(json.loads(first.inputs_json))) == first
    assert {allocate(inputs(*p)).proposal_id for p in itertools.permutations(cs)} == {first.proposal_id}


def test_candidate_input_permutation_never_changes_result():
    cs = [candidate('a', 'venue:futures:BTCUSDT', value='3'), candidate('b', 'venue:futures:ETHUSDT', value='3'),
          candidate('c', 'venue:futures:SOLUSDT', value='5'),
          replace(candidate('d', 'venue:futures:XRPUSDT', value='7'), costs=MISSING)]
    results = {canonical(cmp(inputs(*p))) for p in itertools.permutations(cs)}
    assert len(results) == 1


def test_comparison_is_replayable_after_restart_and_later_revision_does_not_rewrite():
    i = inputs(candidate(), candidate('b', 'venue:futures:ETHUSDT', value='4'))
    p = allocate(i)
    v = C.compare(p)
    assert v == C.compare(allocate(inputs_from_payload(json.loads(p.inputs_json))))
    script = ("import json,sys; from tests.economics_fixtures import install_models; install_models();"
              "from trader.portfolio import comparison as C; from trader.portfolio.allocator import Proposal;"
              "b=json.loads(sys.stdin.read());"
              "print(C.compare(Proposal(b['proposal_id'],json.dumps(b['inputs']),json.dumps(b['result']),b['allocator_version']))['comparison_id'])")
    out = subprocess.run([sys.executable, '-c', script], input=json.dumps(p.payload()), capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == v['comparison_id']
    # a later model revision changes later inputs, never the frozen historical comparison
    later = inputs(candidate(value='50'), candidate('b', 'venue:futures:ETHUSDT', value='4'))
    assert C.decide(later)['comparison_id'] != v['comparison_id']
    assert C.compare(p) == v


def test_comparison_grants_no_authority_and_reads_no_votes():
    v = cmp(inputs(candidate('a', value='9')))
    assert v['authority'] == dict(capital=False, risk_permission=False, owner_approval=False,
                                  order=False, risk_final_gate_required=True)
    assert v['side_effects'] == 'NONE'
    import inspect
    from trader.portfolio.allocator import Candidate
    assert not [f for f in Candidate.__dataclass_fields__ if 'vote' in f or 'analyst' in f]
    assert 'vote' not in inspect.getsource(C).lower().replace('votes', '').replace('vote ', '')


# ── DEC-01 context-bound: analyst votes stay visible and never decide ────────

from tests.test_dec01_opportunity_context import Ctx, packet, evidence  # noqa: E402,F401
from tests.test_market_investigation import prefix  # noqa: E402,F401
from trader.portfolio import opportunity_live as L  # noqa: E402


def _context_view(e, split):
    c0 = Ctx(e)
    c = Ctx(e, packets=[packet(n, x, e['symbol'], c0.A) for n, x in split])
    receipt = c.produce()
    from tests.test_opportunity_live_integration import economics as live_economics
    from trader.portfolio.allocator import Inputs, Portfolio
    er = E.build(live_economics(receipt))                       # real empty registry: UNAVAILABLE
    cand, sources = L.candidate(receipt, er)
    unknown = Evidence(Status.UNKNOWN, ())
    ai = Inputs(c.D, (cand,), Portfolio('missing', c.D, None, unknown, (), ()), unknown, unknown, 'FROZEN',
                tuple({s.source_id: s for s in sources}.values()))
    return receipt, C.decide(ai)


def test_analyst_majority_cannot_win_and_conflict_stays_visible(evidence):  # noqa: F811
    receipt, v = _context_view(evidence, (('structure', .8), ('momentum', .8), ('flow', -.8)))
    r = v['candidates'][0]
    assert v['decision'] == 'NO_ALLOCATION' and v['cash']['selected'] and r['outcome'] == 'REJECTED'
    assert r['context_id'] == receipt.context.context_id
    ev = r['analyst_evidence']
    assert ev['supporting'] == ['momentum', 'structure'] and ev['opposing'] == ['flow'] and ev['conflict'] is True
    assert ev['decides_nothing'] is True and 'EXPECTED_ECONOMICS_UNAVAILABLE' in r['reasons']


def test_unanimous_or_reversed_votes_give_the_same_decision(evidence):  # noqa: F811
    _, a = _context_view(evidence, (('structure', .8), ('momentum', .8), ('flow', .8)))
    _, b = _context_view(evidence, (('structure', -.8), ('momentum', -.8), ('flow', -.8)))
    for v in (a, b):
        assert v['decision'] == 'NO_ALLOCATION' and v['cash']['selected']
        assert v['candidates'][0]['outcome'] == 'REJECTED'
    assert a['candidates'][0]['analyst_evidence']['conflict'] is False
    assert a['candidates'][0]['analyst_evidence']['opposing'] == []
    assert b['candidates'][0]['analyst_evidence']['supporting'] == []
