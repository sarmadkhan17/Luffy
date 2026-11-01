"""Fixed-horizon futility skips only computations that cannot produce a pass."""
from copy import deepcopy
import json

import pytest

from trader.research.closure import calibration as C


def protocol(*, enabled=True, kind='FINAL'):
    return dict(deepcopy(C.PROTOCOL), stop_on_futility=enabled, kind=kind,
                retain_generated_snapshots=True)


@pytest.mark.parametrize('truth', ['NO_EDGE', 'EDGE', 'UNAVAILABLE'])
def test_certificate_excludes_every_possible_fixed_horizon_completion(truth, monkeypatch):
    p = protocol()
    case = dict(name='case', truth=truth)
    n = 8
    # Compute real intervals once per possible final count, then exhaustively
    # enumerate every observed state and every possible future result count.
    judged = {k: C.judge(case, k, n, p) for k in range(n + 1)}
    monkeypatch.setattr(C, 'judge', lambda case, k, total, protocol: judged[k])
    for done in range(n + 1):
        for wins in range(done + 1):
            certificate = C.fixed_horizon_futility(case, wins, done, n, p)
            possible = [judged[k]['verdict'] for k in range(wins, wins + n - done + 1)]
            assert (certificate is None) == ('OK' in possible)
            if certificate:
                assert certificate['planned'] == n
                assert certificate['inference'].startswith('COMPUTATION_FUTILITY_ONLY')


def fake_runner(monkeypatch, outcome):
    calls = []

    def run(p, case, i, seed, directory, run_id=''):
        calls.append((case['name'], i, seed))
        status = outcome(case, i)
        return dict(run_id=run_id, case=case['name'], index=i, seed=seed,
                    outcome=status, trials=1, process_status='COMPLETED',
                    failed_units=0, development_outcomes={})

    monkeypatch.setattr(C, '_replicate', run)
    return calls


def test_futile_power_case_stops_skips_later_cases_and_resumes_without_new_calls(tmp_path, monkeypatch):
    p = protocol()
    p['cases'] = [p['cases'][3], p['cases'][0]]  # unchanged declared seeds in THIS protocol
    calls = fake_runner(monkeypatch, lambda case, i: 'NOT_QUALIFIED')
    result = C.run(p, tmp_path)
    assert result['completion_status'] == 'STOPPED_FUTILITY'
    assert result['calibration_status'] == 'INCONCLUSIVE' and not result['authority_granted']
    assert result['completed_replicates'] < p['replicates']
    assert result['skipped_replicates'] == 120 - len(calls)
    assert result['cases'][0]['verdict'] == 'NOT_COMPLETED'
    assert result['cases'][0]['interval_status'] == 'INTERIM_DESCRIPTIVE_NOT_FINAL'
    assert result['cases'][1]['verdict'] == 'NOT_RUN'
    assert result['futility_stop']['best_possible_final_verdict']['verdict'] != 'OK'
    observed = list(calls)
    assert C.run(p, tmp_path) == result and calls == observed
    assert len(list(tmp_path.glob('rep-*.json'))) == len(calls)


def test_null_with_two_false_admissions_cannot_pass_original_sixty_run_rule(tmp_path, monkeypatch):
    p = protocol(); p['cases'] = [p['cases'][0]]
    calls = fake_runner(monkeypatch, lambda case, i: 'QUALIFIED')
    result = C.run(p, tmp_path)
    assert len(calls) == 2 and result['skipped_replicates'] == 58
    assert result['futility_stop']['best_possible_final_qualified'] == 2
    # This does not claim a failed population-rate test from an interim peek.
    assert result['calibration_status'] == 'INCONCLUSIVE'


def test_successful_run_still_needs_all_original_cases_and_replicates(tmp_path, monkeypatch):
    p = protocol()
    calls = fake_runner(monkeypatch, lambda case, i: 'QUALIFIED' if case['truth'] == 'EDGE' else 'NOT_QUALIFIED')
    result = C.run(p, tmp_path)
    assert len(calls) == 300 and result['skipped_replicates'] == 0
    assert result['calibration_status'] == 'CALIBRATED'
    assert result['completion_status'] == 'COMPLETED' and result['futility_stop'] is None
    assert all(n == 60 for n in result['completed_replicates_per_case'].values())
    for ci, case in enumerate(p['cases']):
        assert calls[ci * 60] == (case['name'], 0, p['final_seed_base'] + ci * 10_000)


@pytest.mark.parametrize('kind,enabled', [('FINAL', False), ('DEVELOPMENT_DIAGNOSTIC', True)])
def test_explicit_off_or_diagnostic_preserves_complete_workflow(tmp_path, monkeypatch, kind, enabled):
    p = protocol(kind=kind, enabled=enabled); p['cases'] = [p['cases'][3]]
    calls = fake_runner(monkeypatch, lambda case, i: 'NOT_QUALIFIED')
    result = C.run(p, tmp_path)
    assert len(calls) == 60 and result['completion_status'] == 'COMPLETED'


def test_partial_report_cannot_authorize_even_with_a_forged_success_label(tmp_path, monkeypatch):
    p = protocol(); p['cases'] = [p['cases'][3]]
    fake_runner(monkeypatch, lambda case, i: 'NOT_QUALIFIED')
    result = C.run(p, tmp_path)
    result['calibration_status'] = 'CALIBRATED'
    result['report_id'] = C.digest({k: v for k, v in result.items() if k != 'report_id'})
    env = {k: result[k] for k in ('search_contract_id', 'numeric_id', 'evaluator_id', 'cost_id')}
    assert not C.report_authorizes(result, {'report_id': result['report_id']}, env)


def test_stop_policy_is_bound_and_cannot_be_toggled_on_resume(tmp_path, monkeypatch):
    p = protocol(); p['cases'] = [p['cases'][3]]
    fake_runner(monkeypatch, lambda case, i: 'NOT_QUALIFIED')
    C.run(p, tmp_path)
    with pytest.raises(ValueError, match='calibration_run_identity_changed'):
        C.run(dict(p, stop_on_futility=False), tmp_path)
    saved = json.loads((tmp_path / 'protocol.json').read_text())
    assert saved['protocol']['stop_on_futility'] is True


@pytest.mark.parametrize('value', ['true', 1, None])
def test_invalid_stop_policy_does_not_launch(value, tmp_path, monkeypatch):
    fake_runner(monkeypatch, lambda *args: pytest.fail('must not launch'))
    with pytest.raises(ValueError, match='stop_on_futility_must_be_boolean'):
        C.run(dict(protocol(), stop_on_futility=value), tmp_path)
