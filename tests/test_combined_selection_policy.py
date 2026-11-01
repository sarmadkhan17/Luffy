"""Combination preference changes development selection, never admission."""
from copy import deepcopy

import pytest

from trader.research.closure import calibration as C, development as D, digest


def scored(pct=8., p=.01):
    return dict(verdict='scored', testable=True, consistency_p=p,
                portfolio=dict(total_pct=pct), trades=100, scored_symbols=16)


def test_profitable_combination_can_advance_with_a_stronger_component():
    own, baselines = scored(), {'simpler': scored(20., .001)}
    before = deepcopy((own, baselines))
    assert D.classify(own, baselines)[0] == 'NEGATIVE_ECONOMICS'  # legacy path
    outcome, reason = D.classify(own, baselines, component_comparison_only=True)
    assert outcome == 'DEVELOPMENT_POSITIVE' and 'comparisons retained' in reason
    assert (own, baselines) == before


@pytest.mark.parametrize('own,want', [
    (scored(-1.), 'NEGATIVE_ECONOMICS'),
    (scored(0.), 'NEGATIVE_ECONOMICS'),
    (scored(p=.9), 'NEGATIVE_ECONOMICS'),
    (scored(float('nan')), 'INCONCLUSIVE_POWER'),
    (dict(verdict='untestable', testable=False), 'INCONCLUSIVE_POWER'),
    (dict(verdict='untested', context_gaps={'funding': 'missing'}), 'BLOCKED_DATA'),
])
def test_combination_preference_cannot_override_own_evidence(own, want):
    assert D.classify(own, {}, component_comparison_only=True)[0] == want


@pytest.mark.parametrize('baseline,want', [
    (dict(verdict='error', error='fixture'), 'CODE_FAILURE'),
    (dict(verdict='malformed'), 'CODE_FAILURE'),
])
def test_required_comparison_failure_stays_explicit(baseline, want):
    assert D.classify(scored(), {'simple': baseline}, component_comparison_only=True)[0] == want


@pytest.mark.parametrize('verdict', ['untestable', 'untested'])
def test_unavailable_component_evidence_does_not_erase_measured_combined_economics(verdict):
    baseline = {'simple': dict(verdict=verdict, consistency_p=None)}
    assert D.classify(scored(), baseline)[0] == 'INCONCLUSIVE_POWER'
    assert D.classify(scored(), baseline, component_comparison_only=True)[0] == 'DEVELOPMENT_POSITIVE'
    assert baseline['simple']['consistency_p'] is None


def test_actual_graph_keeps_its_comparison_numbers_and_independent_boundary(monkeypatch):
    from tests.test_research_evaluate import _bundle
    from trader.research.compose import graph
    bundle = _bundle(n_symbols=4)
    env = dict(envelope_id='FIXTURE', evaluator_id='FIXTURE', cost_id='FIXTURE',
        development_mode='REPLAY', policy_class='TEST_ONLY', tf='4h', fit_window=[0, bundle.cut],
        protected_status='UNAVAILABLE', protected_reasons=['FIXTURE_NO_INDEPENDENT_AUTHORITY'],
        policy=dict(min_discovery_symbols=4, draws=20, seed=17, min_symbol_trades=8, grow_max_p=.25))
    participation = {'cmp': 'gt', 'a': {'f': 'relvol50'}, 'b': {'lit': -1}}
    candidate = graph.make_candidate(dict(question='fixture combination', rationale='fixture',
        expected='fixture', failure_conditions='fixture', structure='long_short', tf='4h', geo='fixed',
        long={'all': [{'cmp': 'gt', 'a': {'f': 'ret24'}, 'b': {'lit': 0}}, participation]},
        short={'all': [{'cmp': 'lt', 'a': {'f': 'ret24'}, 'b': {'lit': 0}}, participation]}))
    monkeypatch.setattr(D, 'build_bundle', lambda *a, **kw: bundle)
    result = D._develop(candidate, bundle.frames, 'FIXTURE_DATA', env, 20, {'gaps': []})
    assert result['metrics']['total_pct'] > 0 and result['metrics']['consistency_p'] <= .25
    assert result['outcome'] == 'DEVELOPMENT_POSITIVE', (result['reason'], result['baselines'])
    assert result['selection_policy'] == D.COMBINED_SELECTION_POLICY
    assert result['baselines'] and any(not b['candidate_earns_place'] for b in result['baselines'].values())
    assert any(b['verdict'] == 'untestable' for b in result['baselines'].values())
    assert result['validation_readiness']['status'] == 'UNAVAILABLE'
    assert result['validation_readiness']['authority'].startswith('NO_ADMISSION')


def test_search_contract_binds_selection_before_new_calibration():
    assert C.search_contract(search={}, max_candidates=8, max_revisions=4)[
        'directional_selection'] == D.COMBINED_SELECTION_POLICY


def test_older_selection_report_cannot_authorize_the_new_procedure():
    # Hand-made verifier fixture only, not a measured calibration approval.
    contract = C.search_contract(search={}, max_candidates=8, max_revisions=4)
    old_contract = {k: v for k, v in contract.items() if k != 'directional_selection'}
    env = dict(search_contract_id=digest(contract), numeric_id='numbers', cost_id='cost', evaluator_id='code')
    report = dict(schema='closure-calibration-report.v3', search_scope='COMPOSITIONAL_SEARCH', **env,
        calibration_status='CALIBRATED', kind='FINAL', numerics='EXACT_CONFIGURED', authority_granted=False)
    report['search_contract_id'] = digest(old_contract)
    report['report_id'] = digest(report)
    assert not C.report_authorizes(report, {'report_id': report['report_id']}, env)
