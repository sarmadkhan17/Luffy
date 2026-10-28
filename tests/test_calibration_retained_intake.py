"""Calibration must exercise retained intake or explicitly remain incomplete."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tests.test_brain_research_intake import setup  # noqa: F401
from tests.test_closure_question_consumer import _bank, VOLUME
from trader.research.closure import calibration as C


def test_provider_contract_changes_with_model_but_never_contains_credentials():
    from trader.core.config import load_config
    cfg = load_config()
    first = C.intake_provider_id(cfg)
    changed = deepcopy(cfg)
    changed['brain']['api_key'] = 'must-never-be-retained'
    assert C.intake_provider_id(changed) == first
    changed['brain']['model_fast'] = 'different-model'
    assert C.intake_provider_id(changed) != first
    without = C.search_contract(search={}, max_candidates=8, max_revisions=4)
    with_intake = C.search_contract(search={}, max_candidates=8, max_revisions=4, intake=True)
    assert 'intake_provider_id' not in without and with_intake['intake_provider_id'] == first


def test_freezes_verified_corpus_and_does_not_refresh_after_source_change(setup, tmp_path):
    bank = _bank(setup, [('one', VOLUME)])
    work = tmp_path / 'calibration'; work.mkdir()
    protocol = dict(intake_bank_path=str(bank))
    C._freeze_intake(protocol, work)
    identity = protocol['intake_corpus_id']
    frozen = (work / 'intake-bank.db').read_bytes()
    bank.unlink()
    C._freeze_intake(protocol, work)
    assert protocol['intake_corpus_id'] == identity
    assert (work / 'intake-bank.db').read_bytes() == frozen
    protocol['intake_corpus_id'] = 'changed'
    with pytest.raises(ValueError, match='corpus_changed'):
        C._freeze_intake(protocol, work)


def test_empty_corpus_cannot_claim_intake_coverage(tmp_path):
    from trader.research import predictive_bridge as B
    bank = tmp_path / 'empty.db'; B.store(bank).close()
    work = tmp_path / 'calibration'; work.mkdir()
    with pytest.raises(ValueError, match='one_to_eight'):
        C._freeze_intake(dict(intake_bank_path=str(bank)), work)


@pytest.mark.parametrize('error,status,complete', [
    (None, 'COMPILED', True),
    ('invalid_graph', 'REFUSED', True),
    ('PROVIDER_RESPONSE_UNAVAILABLE', 'DEFERRED', False),
    ('PROPOSAL_INTERRUPTED_AFTER_RESERVATION', 'REFUSED', False),
])
def test_intake_coverage_tracks_actual_provider_results(error, status, complete):
    store = SimpleNamespace(
        units=lambda sid: [dict(kind='intake', status='DONE', result=json.dumps(dict(status='OK', seen=1, deferred=0)))],
        all_once=lambda table: [dict(error=error, status=status)])
    assert C._intake_observation(store, 'sid')['complete'] is complete


def test_failed_intake_prevents_calibration_even_with_passing_case_counts(tmp_path, monkeypatch):
    proto = dict(C.PROTOCOL, **C.SMOKE)
    proto.update(kind='FINAL', numerics='EXACT_CONFIGURED', cases=[dict(name='null', truth='NO_EDGE')],
                 intake_bank_path='detached-bank')
    monkeypatch.setattr(C, '_freeze_intake', lambda p, d: p.update(intake_corpus_id='frozen'))
    monkeypatch.setattr(C, 'feasibility', lambda p: [])
    monkeypatch.setattr(C, '_replicate', lambda p, case, i, seed, d, run_id='': dict(
        run_id=run_id, case=case['name'], index=i, seed=seed, outcome='NOT_REACHED', trials=0,
        intake=dict(complete=False)))
    report = C.run(proto, tmp_path, replicates=40)
    assert report['cases'][0]['verdict'] == 'OK'
    assert report['calibration_status'] == 'INCONCLUSIVE'
    assert not report['intake_complete']
    assert report['omitted_paths'] == ['live multi-leg execution']
    assert report['intake_corpus_id'] == 'frozen'
