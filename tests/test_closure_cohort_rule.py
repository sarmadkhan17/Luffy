"""Cohort rule data-availability-cohort.v1: exclusion by missing input only, never below the fixed floors."""
import inspect

import pandas as pd
import pytest

from trader.research import control
from trader.research.closure import cohort, development as D
from trader.strategy import spec_evidence

SERIES = spec_evidence._SERIES_FOR
ENV = {'policy': {'min_discovery_symbols': 16}}
TEST_ENV = {'policy': {'min_discovery_symbols': 6}}


def _syms(n):
    return [f'S{i:02d}/USDT' for i in range(n)]


def _frames(syms):
    return {s: pd.DataFrame({'ts': pd.to_datetime([0, 14_400_000], unit='ms', utc=True)}) for s in syms}


def _funding(rows=2):
    return pd.DataFrame({'ts': pd.to_datetime(range(rows), unit='ms', utc=True), 'value': [0.0001] * rows})


def _result(syms, missing, extra_gaps=()):
    derivs = {s: ({} if s in missing else {'funding': _funding()}) for s in syms}
    gaps = [dict(requirement='funding', reason=f'{len(syms) - len(missing)}/{len(syms)} symbols have eligible rows')] if missing else []
    return dict(derivs=derivs, gaps=gaps + list(extra_gaps), data_id='D0', provenance={})


def _apply(syms, missing, env=ENV, extra_gaps=()):
    frames, result = _frames(syms), _result(syms, set(missing), extra_gaps)
    cohort.apply(frames, result, {'funding'}, env, SERIES, stage='external')
    return frames, result


def test_floors_are_the_fixed_policy_values():
    assert cohort.floor_for(ENV) == dict(discovery=16, control=12, effective=16)
    assert control.MIN_CONTROL_SYMBOLS == 12
    assert cohort.floor_for(TEST_ENV)['effective'] == 12, 'a lower policy minimum can never push the floor under the control minimum'


def test_symbols_missing_an_input_are_excluded_and_every_exclusion_is_recorded():
    syms = _syms(20)
    frames, result = _apply(syms, [syms[3], syms[7]])
    assert sorted(frames) == sorted(s for s in syms if s not in (syms[3], syms[7]))
    assert result['gaps'] == [] and syms[3] not in result['derivs']
    rec = result['cohort']
    assert rec['rule'] == cohort.RULE_ID and rec['status'] == 'EXCLUDE'
    assert rec['considered'] == 20 and rec['kept'] == 18 and len(rec['excluded']) == 2
    entry = {e['symbol']: e for e in rec['excluded']}[syms[3]]
    assert entry['missing'] == ['funding']
    assert entry['code'] == f'symbol_excluded_missing_input:{syms[3]}:funding'
    assert entry['coverage'] == dict(bars=2, series={'funding': dict(rows=0)})       # measured, not asserted
    assert result['data_id'] != 'D0'                                                  # the cohort is part of the data identity


def test_nothing_missing_changes_nothing():
    syms = _syms(20)
    frames, result = _apply(syms, [])
    assert sorted(frames) == sorted(syms) and 'cohort' not in result and result['data_id'] == 'D0'


def test_excluding_below_the_discovery_floor_is_refused_and_the_study_stays_blocked():
    syms = _syms(18)
    frames, result = _apply(syms, syms[:3])                 # 15 would remain: below 16
    assert sorted(frames) == sorted(syms), 'no symbol may be dropped when the floor would be breached'
    assert result['gaps'], 'the original gap must still block'
    assert result['cohort']['status'] == 'FLOOR_BREACHED' and result['cohort']['kept'] == 15
    assert len(result['cohort']['excluded']) == 3 and result['data_id'] == 'D0'


def test_exact_floor_is_allowed():
    syms = _syms(18)
    frames, result = _apply(syms, syms[:2])                 # exactly 16 remain
    assert len(frames) == 16 and result['cohort']['status'] == 'EXCLUDE'


def test_control_minimum_binds_even_when_the_policy_minimum_is_lower():
    syms = _syms(14)
    frames, result = _apply(syms, syms[:3], env=TEST_ENV)   # 11 would remain: above policy 6, below control 12
    assert len(frames) == 14 and result['cohort']['status'] == 'FLOOR_BREACHED'


def test_an_unattributable_gap_keeps_blocking_the_whole_study():
    syms = _syms(20)
    btc = dict(requirement='btc', reason='BTC reference unavailable in development era')
    frames, result = _apply(syms, [syms[0]], extra_gaps=[btc])
    assert len(frames) == 20 and 'cohort' not in result and len(result['gaps']) == 2


def test_decision_is_deterministic_and_takes_no_outcome():
    assert set(inspect.signature(cohort.decide).parameters) == {'frames', 'derivs', 'gaps', 'required', 'series_for', 'floor'}
    syms = _syms(20)
    a = _apply(syms, [syms[1]])[1]['cohort']
    b = _apply(syms, [syms[1]])[1]['cohort']
    assert a == b and a['cohort_id'] == b['cohort_id']


def test_funding_stage_accumulates_on_top_of_the_external_stage():
    syms = _syms(22)
    frames, result = _frames(syms), _result(syms, {syms[0]})
    cohort.apply(frames, result, {'funding'}, ENV, SERIES, stage='external')
    result['gaps'].append(dict(requirement='funding', reason='no retained funding for the symbol', symbols=[syms[5]]))
    cohort.apply(frames, result, {'funding'}, ENV, SERIES, stage='funding')
    rec = result['cohort']
    assert rec['stage'] == 'funding' and rec['considered'] == 22 and rec['kept'] == 20
    assert [e['symbol'] for e in rec['excluded']] == [syms[0], syms[5]]
    assert syms[5] not in frames


# ── wiring: _external applies the rule before and after the funding bind; a blocked result keeps the evidence ──
def _env(policy_min=16):
    return {'development_mode': 'REPLAY', 'manifest_id': 'm' * 32, 'dev_end_ms': 1, 'fit_window': [0, 1],
            'risk': {'real_funding': True}, 'tf': '4h', 'policy': {'min_discovery_symbols': policy_min}}


def test_external_excludes_before_binding_funding_and_again_for_funding_gaps(monkeypatch):
    syms = _syms(22)
    monkeypatch.setattr(D.cdata, 'load_external', lambda *a, **k: _result(syms, {syms[0]}) | dict(market=None, first_obs={}))
    bound = []

    def fake_bind(frames, result, env, basis):
        bound.append(sorted(frames))
        result['gaps'].append(dict(requirement='funding', reason='no retained funding for the symbol', symbols=[syms[9]]))
    monkeypatch.setattr(D, 'bind_funding', fake_bind)
    frames = _frames(syms)
    out = D._external({'candidate_id': 'c'}, 'snap', 'derivs', frames, _env())
    assert bound == [sorted(s for s in syms if s != syms[0])], 'funding is bound only for symbols that survived the first pass'
    assert out['gaps'] == []
    assert sorted(frames) == sorted(s for s in syms if s not in (syms[0], syms[9]))
    assert [e['symbol'] for e in out['cohort']['excluded']] == [syms[0], syms[9]]


def test_blocked_result_carries_the_would_be_exclusions(monkeypatch):
    syms = _syms(17)
    monkeypatch.setattr(D.cdata, 'load_external', lambda *a, **k: _result(syms, set(syms[:4])) | dict(market=None, first_obs={}))
    frames = _frames(syms)
    ext = D._external({'candidate_id': 'c'}, 'snap', 'derivs', frames, _env())
    assert len(frames) == 17 and ext['gaps'] and ext['cohort']['status'] == 'FLOOR_BREACHED'
    env = dict(_env(), envelope_id='e', evaluator_id='v', cost_id='c', policy_class='CONFIGURED')
    res = D._develop({'candidate_id': 'c'}, frames, 'data', env, 10, ext)
    assert res['outcome'] == 'BLOCKED_DATA' and res['cohort']['status'] == 'FLOOR_BREACHED'
    assert len(res['cohort']['excluded']) == 4
