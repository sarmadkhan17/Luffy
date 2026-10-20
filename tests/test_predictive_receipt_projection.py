"""Synthetic protocol regressions; numerical profitability is not asserted."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from trader.cognition import predictive as P
from trader.research import predictive_bridge as B, predictive_receipt as M
from trader.research.ledger import _slim
from tests.test_predictive_research_bridge import population, NumericalFixture
from tests.test_investigation_state_feedback import paths


@pytest.fixture(scope='module')
def development(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('receipt-projection')
    with pytest.MonkeyPatch.context() as mp:
        case = population.__wrapped__(paths.__wrapped__(tmp, mp), tmp, mp)
        source, bank, candles, created, later, _ = case
        cfg = yaml.safe_load((Path(__file__).parents[1]/'config.yaml').read_text())
        assert cfg['research']['referee'] is False and cfg['research']['handoff'] is False
        fixture = NumericalFixture()

        def run(fn, payload, **kw):
            assert fn.__name__ in ('measure_job', 'evaluate_job')
            result = fixture(fn, payload, **kw)
            for row in result.value.get('results', []):
                row['portfolio']['order'] = ['BTC/USDT', 'ETH/USDT']
            return result

        def cycle(now):
            return B.cycle(source, bank, candles, cfg, now_ms=now, run=run,
                           quantitative_source_path=tmp/'luffy.db')

        cycle(created)
        for _ in range(5):
            cycle(later)
        with B.store(bank) as db:
            results = B.records(db, 'bridge_bank_results')
            body = next(r for r in reversed(results) if
                        (r.get('measured_result') or {}).get('round') == 'predictive')
            ex = next(e for e in B.records(db, 'bridge_experiments')
                      if e['experiment_id'] == body['experiment_id'])
        yield bank, cycle, later, fixture, ex, body


def validate(ex, body):
    M.validate_quantitative(ex, body['classification'], body['measured_result'],
                            body['referee_disposition'], body['quantitative_evidence'],
                            body['measurement_receipts'])


def test_full_worker_receipt_matches_slim_stored_candidate(development):
    *_, ex, body = development
    measured = body['measured_result']
    raw = next(r for receipt in body['measurement_receipts']
               for r in (receipt.get('value') or {}).get('results', [])
               if r['hash'] == measured['hash'])
    assert raw['portfolio']['order']
    assert 'order' not in measured['portfolio']
    assert _slim(raw)['portfolio'] == measured['portfolio']
    M.validate(P.hypothesis_from_dict(ex['hypothesis']), ex, body)


@pytest.mark.parametrize('mutation,error', [
    ('missing', 'quantitative_measurement_missing'),
    ('identity', 'quantitative_measurement_identity'),
    ('candidate', 'quantitative_measurement_expression'),
    ('numeric', 'quantitative_measurement_missing'),
    ('stale', 'quantitative_receipt_binding'),
    ('receipt_order', 'quantitative_receipt_binding'),
])
def test_invalid_measurement_refused(development, mutation, error):
    *_, ex, original = development
    body = deepcopy(original)
    measured = body['measured_result']
    if mutation == 'missing':
        # Keep valid controls/ablations; remove only the candidate's receipt.
        body['measurement_receipts'] = [r for r in body['measurement_receipts'] if not any(
            x['hash'] == measured['hash'] for x in (r.get('value') or {}).get('results', []))]
    elif mutation == 'identity':
        measured['experiment_id'] = 'another-experiment'
    elif mutation == 'candidate':
        measured['hash'] = 'another-candidate'
    elif mutation == 'numeric':
        measured['portfolio']['total_pct'] += 1
    elif mutation == 'stale':
        r = body['measurement_receipts'][0]
        r['binding']['experiment_id'] = 'stale-experiment'
        r['measurement_id'] = P.digest({k:v for k,v in r.items() if k != 'measurement_id'})
    elif mutation == 'receipt_order':
        r = next(r for r in body['measurement_receipts'] if r['job'] == 'evaluate_job')
        r['value']['results'][0]['portfolio']['order'].append('TAMPERED')
    with pytest.raises(ValueError, match=error):
        validate(ex, body)


def test_unchanged_feedback_continuation_is_idempotent(development):
    bank, cycle, later, fixture, ex, body = development
    tables = ('bridge_bank_results', 'bridge_measurements', 'bridge_questions', 'bridge_tests')
    def snapshot():
        with B.store(bank) as db:
            return {t:B.records(db,t) for t in tables}
    before = snapshot()
    calls = len(fixture.calls)
    cycle(later)
    assert snapshot() == before
    assert len(fixture.calls) == calls
    assert before['bridge_tests'] == []
    assert sum(r['result_id'] == body['result_id'] for r in before['bridge_bank_results']) == 1
