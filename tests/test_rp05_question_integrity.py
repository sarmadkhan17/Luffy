"""RP-05: unsupported predictive templates are not generated; identical
experiments never acquire a fresh statistical identity. Offline, synthetic."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from trader.cognition import predictive as P
from trader.research import predictive_bridge as B, predictive_experiment as E
from tests.test_predictive_research_bridge import (
    population, consume, saved, NumericalFixture, CFG)  # noqa: F401
from tests.test_investigation_state_feedback import paths  # noqa: F401

UNSUPPORTED = ('exact_volume_path.v1', 'volume_breakout_no_benefit.v1')


def test_only_supported_shapes_are_generatable():
    assert set(P.generatable()) == {'volume_breakout_context.v1'}
    assert set(UNSUPPORTED) <= set(P.TRANSFORMS)          # registry still replays history
    assert all(P.TRANSFORMS[k]['shape'] in P.SUPPORTED_SHAPES for k in P.generatable())


@pytest.mark.parametrize('transformation', UNSUPPORTED)
def test_unsupported_template_is_refused_at_generation(population, transformation):
    *_, s = population
    with pytest.raises(ValueError, match='unsupported_experiment_shape'):
        P.propose(s, transformation, s.available_ms)


@pytest.mark.parametrize('transformation,schema', [
    ('exact_volume_path.v1', P.SCHEMA), ('exact_volume_path.v1', P.LEGACY_SCHEMA),
    ('volume_breakout_no_benefit.v1', P.SCHEMA)])
def test_retained_unsupported_hypotheses_still_replay(population, transformation, schema):
    *_, s = population
    h = P._propose(s, transformation, s.available_ms, schema)
    assert P.hypothesis_from_dict(json.loads(P.canonical(asdict(h)))) == h


def test_cycle_registers_no_unsupported_question_and_spends_nothing(population):
    source, bank, candles, created, later, _ = population
    fake = NumericalFixture()
    first = consume(population, fake, created)
    assert first['counts']['hypotheses_proposed'] == 1
    assert first['counts']['not_testable'] == 0
    for _ in range(6):
        consume(population, fake, later)
    assert {r['hypothesis']['transformation'] for r in saved(bank, 'bridge_hypotheses')} \
        == {'volume_breakout_context.v1'}
    assert all(r['translation']['status'] != 'UNSUPPORTED_EXPERIMENT_SHAPE'
               for r in saved(bank, 'bridge_translations'))


def test_retained_unsupported_hypothesis_is_refused_without_a_look(population):
    """A pre-RP-05 store holding an unsupported hypothesis keeps its refusal path."""
    source, bank, candles, created, later, s = population
    old = P._propose(s, 'exact_volume_path.v1', created, P.SCHEMA)
    with B.store(bank) as db:
        with db:
            B.append(db, 'bridge_hypotheses', old.hypothesis_id,
                     dict(hypothesis=asdict(old), novelty='NEW', related_prior=[]))
    fake = NumericalFixture()
    out = consume(population, fake, later)
    assert out['counts']['not_testable'] == 1
    assert all(e['source_hypothesis_id'] != old.hypothesis_id for e in saved(bank, 'bridge_experiments'))
    res = next(r for r in saved(bank, 'bridge_bank_results') if r['hypothesis_id'] == old.hypothesis_id)
    assert res['classification'] == 'NOT_TESTABLE' and res['experiment_id'] is None


def _twin(population, now):
    """Same expression/data/policy, different source version and name."""
    *_, s = population
    twin_source = P.replace_source_prior(s, '[{"classification":"UNSUPPORTED","note":"rp05"}]')
    h = P.propose(twin_source, 'volume_breakout_context.v1', now)
    return h


def test_identical_experiment_does_not_acquire_a_second_statistical_identity(population):
    source, bank, candles, created, later, s = population
    fake = NumericalFixture('UNSUPPORTED')
    consume(population, fake, created, submit=False)
    h2 = _twin(population, created)
    first = P.hypothesis_from_dict(saved(bank, 'bridge_hypotheses')[0]['hypothesis'])
    assert h2.hypothesis_id != first.hypothesis_id and h2.source_hash != first.source_hash
    with B.store(bank) as db:
        with db:
            B.append(db, 'bridge_hypotheses', h2.hypothesis_id,
                     dict(hypothesis=asdict(h2), novelty='RELATED_PRIOR', related_prior=[]))
    for _ in range(8):
        consume(population, fake, later, max_experiments=4)
    experiments = saved(bank, 'bridge_experiments')
    assert len(experiments) == 1
    assert experiments[0]['source_hypothesis_id'] == first.hypothesis_id
    assert [n for n, _ in fake.calls].count('referee_job') == 1
    assert len(saved(bank, 'bridge_tests')) == 1
    dup = next(r for r in saved(bank, 'bridge_bank_results') if r['hypothesis_id'] == h2.hypothesis_id)
    assert dup['classification'] == 'DATA_INSUFFICIENT' and dup['experiment_id'] is None
    assert dup['referee_disposition']['state'] == 'DUPLICATE_EXPERIMENT_IDENTITY'
    assert dup['referee_disposition']['identical_experiment_id'] == experiments[0]['experiment_id']
    # restart: nothing re-runs, nothing new is written
    snapshot = {t: saved(bank, t) for t in ('bridge_experiments', 'bridge_tests', 'bridge_bank_results')}
    calls = len(fake.calls)
    consume(population, fake, later + 1, max_experiments=4)
    assert len(fake.calls) == calls
    assert {t: saved(bank, t) for t in snapshot} == snapshot


def test_statistical_identity_ignores_naming_but_not_the_question(population):
    source, bank, candles, created, later, s = population
    a = E.translate(P.propose(s, 'volume_breakout_context.v1', created), candles, CFG, later)
    other = P.replace_source_prior(s, '[{"classification":"UNSUPPORTED"}]')
    b = E.translate(P.propose(other, 'volume_breakout_context.v1', created), candles, CFG, later)
    assert a['status'] == b['status'] == 'READY' and a['experiment_id'] != b['experiment_id']
    assert E.statistical_identity(a) == E.statistical_identity(b)
    for field, value in (('predictor', ['volz96>p90']), ('geometry', 'trail'),
                         ('timeframe', '1h'), ('target', 'other')):
        c = deepcopy(a)
        c[field] = value
        assert E.statistical_identity(c) != E.statistical_identity(a)
    c = deepcopy(a)
    c['split']['cut_ms'] += 14_400_000
    assert E.statistical_identity(c) != E.statistical_identity(a)
    c = deepcopy(a)
    c['minimum_evidence']['min_discovery_symbols'] += 1
    assert E.statistical_identity(c) != E.statistical_identity(a)
