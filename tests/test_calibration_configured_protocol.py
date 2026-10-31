"""Calibrate the scheduler's actual windows; preparing a protocol runs nothing."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from trader.core.config import load_config
from trader.research.closure import ClosureRefused, digest
from trader.research.closure import calibration as C, auto
from trader.research.closure import __main__ as cli


@pytest.mark.parametrize('tf,bars', [('4h',6570),('1h',2000),('15m',1600)])
def test_search_identity_matches_configured_study_and_preserves_protected_budget(tf,bars):
    cfg=load_config();before=deepcopy(cfg)
    p=C.configured_protocol(cfg,tf,intake_bank_path='/explicit/retained-bank.db')
    a=auto.settings(cfg)
    expected=C.search_contract(search=a['search'],max_candidates=a['max_candidates'],
        max_revisions=a.get('max_revisions',4),tf=tf,intake=True,development_bars=bars)
    actual=C.search_contract(search=p['search'],max_candidates=p['max_candidates'],
        max_revisions=p['max_revisions'],tf=p['tf'],intake=bool(p.get('intake_bank_path')),
        development_bars=p['dev_bars'])
    assert digest(expected)==digest(actual)
    assert p['n_bars']-p['dev_bars']-101>=C.PROTOCOL['n_bars']-C.PROTOCOL['dev_bars']-101
    assert C.feasibility(p)==[]
    assert p['cases']==C.PROTOCOL['cases'] and p['kind']=='FINAL'
    assert cfg==before
    p['search']['max_fits']=1
    assert cfg==before


def test_intake_cannot_be_silently_dropped_or_added():
    cfg=load_config()
    with pytest.raises(ClosureRefused,match='calibration_intake_contract_mismatch'):
        C.configured_protocol(cfg,'4h')
    cfg['research']['closure']['auto'].pop('bank_path',None)
    with pytest.raises(ClosureRefused,match='calibration_intake_contract_mismatch'):
        C.configured_protocol(cfg,'4h',intake_bank_path='unexpected.db')
    assert 'intake_bank_path' not in C.configured_protocol(cfg,'4h')


def test_unknown_and_ambiguous_horizon_cannot_select_a_different_search():
    cfg=load_config();a=cfg['research']['closure']['auto']
    a['horizons']=[dict(tf='4h',fit_bars=6570)]
    with pytest.raises(ClosureRefused,match='calibration_horizon_not_unique'):
        C.configured_protocol(cfg,'1h',intake_bank_path='bank.db')
    a['horizons']*=2
    with pytest.raises(ClosureRefused,match='calibration_horizon_not_unique'):
        C.configured_protocol(cfg,'4h',intake_bank_path='bank.db')


def test_impossible_configured_floor_is_reported_without_weakening_it():
    cfg=load_config();cfg['research']['min_threshold_samples']=10**9
    with pytest.raises(ClosureRefused,match='threshold_samples_unreachable'):
        C.configured_protocol(cfg,'4h',intake_bank_path='bank.db')


def test_cli_preparation_never_runs_calibration_or_opens_a_bank(monkeypatch,capsys):
    run=Mock(side_effect=AssertionError('must not run'))
    freeze=Mock(side_effect=AssertionError('must not open bank'))
    monkeypatch.setattr(C,'run',run);monkeypatch.setattr(C,'_freeze_intake',freeze)
    assert cli.main(['calibration-protocol','--horizon','4h','--intake-bank','/absent/bank.db'])==0
    p=json.loads(capsys.readouterr().out)
    assert p['dev_bars']==6570 and p['intake_bank_path']=='/absent/bank.db'
    assert 'authority_granted' not in p # A protocol is not a result or admission.
    run.assert_not_called();freeze.assert_not_called()
