"""ATT-03 source-cut warm-up and existing-policy ranking; synthetic offline only."""
from copy import deepcopy
import json
import socket
from unittest.mock import Mock

import pytest

from trader import attention_admission as A
from trader.core.journal import Journal
from trader.observability.attention import capture, settings, evaluate_snapshot
from tests.test_attention_telemetry import frames
from tests.test_hierarchical_admission import CUT, observation

TF = 14_400_000


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('network forbidden')))


def inputs(short=False):
    data = frames(4, CUT)
    return {f'S{i:02}/USDT': {'4h': f['4h'].iloc[-10:].copy() if short else f['4h'].copy()}
            for i, f in enumerate(data.values())}


def evaluate(data, cut=CUT):
    ev = capture(data, list(data), 'att03', settings(), cut)
    ev['capture_ms'] = None
    return ev, evaluate_snapshot(ev)


def frozen_observation(data, cut=CUT):
    ev, result = evaluate(data, cut)
    obs = observation(4, relevant=True)
    obs.update(source_cut_ms=cut, attention_event=ev,
               salience_rows={r['symbol']: r for r in result['rows']},
               salience_config=result['config'], salience_anchor_ms=cut // TF * TF,
               market=result['market'])
    for row in obs['rows'].values():
        row['available_at_ms'] = cut
    return obs


def test_warmup_exposes_null_components_and_exact_required_history():
    _, result = evaluate(inputs(short=True))
    for row in result['rows']:
        assert row['status'] == 'warmup' and not row['eligible']
        assert row['salience'] is None
        assert all(v is None for v in row['components'].values())
        h = row['history']
        assert h['required_bars'] == result['config']['window'] + result['config']['short'] + 1 == 26
        assert h['eligible_bars'] == 10 and h['timeframe'] == '4h'
        assert h['source_cut_ms'] == CUT and h['available_at_ms'] == CUT
        assert h['eligible_at_ms'] is None and len(h['missing_open_ms']) == 16
        assert h['anchor_open_ms'] + TF == CUT // TF * TF


def test_late_leading_history_eligible_only_at_actual_receipt_cut():
    data = inputs()
    for f in data.values():
        # Exactly the first five required bars arrive one millisecond later.
        df = f['4h']
        df.loc[df.index[-26:-21], ['available_at_ms', 'observed_at_ms']] = CUT + 1
    original = deepcopy(data)
    _, before = evaluate(data)
    _, after = evaluate(data, CUT + 1)
    assert all(r['status'] == 'warmup' and not r['eligible'] for r in before['rows'])
    assert all(r['eligible'] and r['history']['eligible_at_ms'] == CUT + 1 for r in after['rows'])
    # Rewind selects the old availability, not the latest evaluated cut.
    assert evaluate(data)[1]['rows'] == before['rows']
    for s in data:
        assert data[s]['4h'].equals(original[s]['4h'])


def test_date_advance_cannot_invent_history_or_finalize_partial_bar():
    data = inputs(short=True)
    for f in data.values():
        f['4h'].loc[f['4h'].index[-1], 'bar_state'] = 'PARTIAL'
    _, before = evaluate(data)
    _, later = evaluate(data, CUT + TF)
    assert all(not r['eligible'] and r['salience'] is None for r in before['rows'] + later['rows'])
    assert all(r['history']['eligible_at_ms'] is None for r in later['rows'])


def test_invalid_missing_inputs_are_distinct_from_warmup():
    data = inputs(short=True)
    data['S00/USDT'] = {}
    data['S01/USDT']['4h'].loc[:, 'close'] = float('nan')
    _, r = evaluate(data)
    rows = {x['symbol']: x for x in r['rows']}
    assert rows['S00/USDT']['status'] == rows['S01/USDT']['status'] == 'missing'
    assert rows['S02/USDT']['status'] == 'warmup'
    assert r['issues'] and r['rejected_inputs']
    assert all(x['salience'] is None and not x['eligible'] for x in rows.values())


def test_missing_optional_components_remain_none_existing_max_only():
    data = inputs()
    for f in data.values():
        f['4h'].loc[:, 'volume'] = 100.
    _, r = evaluate(data)
    for row in r['rows']:
        assert row['components']['volume_anomaly'] is None
        assert row['components']['correlation_change'] is None  # only 30 of 151 closes
        assert set(row['components']) == {'volume_anomaly', 'volatility_transition',
                                         'relative_return_divergence', 'correlation_change'}
        assert row['salience'] == max(abs(v) for v in row['components'].values() if v is not None)
    _, small = evaluate({'S00/USDT': data['S00/USDT']})
    assert small['rows'][0]['components']['relative_return_divergence'] is None


def test_history_reaches_receipt_candidates_and_exact_restart(tmp_path):
    obs = frozen_observation(inputs(short=True))
    policy = A.AdmissionPolicy(12, 2, 2, 0)
    j = Journal(tmp_path / 'attention.db')
    receipt = A.persist(j, obs, policy)
    assert not receipt['roles']['event'] and not receipt['tie_break_order']
    for row in receipt['rows']:
        assert row['score'] is None and row['history']['eligible_at_ms'] is None
    candidates = A.candidates(receipt)
    assert candidates and all(c['score_status'] == 'warmup' and c['history']['required_bars'] == 26 for c in candidates)
    assert {c['category'] for c in candidates} <= {'exploration', 'warmup_relevant'}
    reopened = Journal(j.db_path)
    assert A.canonical(A.latest(reopened)) == A.canonical(receipt)
    assert A.canonical(A.candidates(A.latest(reopened))) == A.canonical(candidates)
    assert A.canonical(A.admit(json.loads(A.canonical(obs)), policy, receipt['prior_state'])) == A.canonical(receipt)


def test_existing_ranking_ties_no_forced_selection_and_no_learned_override():
    obs = frozen_observation(inputs())
    policy = A.AdmissionPolicy(12, 2, 2, 0)
    receipt = A.admit(obs, policy, A.initial_state())
    expected = sorted((s for s, r in obs['salience_rows'].items() if r['eligible'] and r['salience'] >= obs['salience_config']['min_salience']),
                      key=lambda s: (-float(format(obs['salience_rows'][s]['salience'], '.12g')), s))
    assert receipt['tie_break_order'] == expected
    assert receipt['learned_priority_influence'].startswith('NONE')
    assert receipt == A.admit(json.loads(A.canonical(obs)), policy, A.initial_state())
    empty = deepcopy(obs)
    empty['rows'] = {}; empty['peer_cohort'] = []; empty['salience_rows'] = {}
    assert A.admit(empty, policy, A.initial_state())['admitted_symbols'] == []
    with pytest.raises(ValueError, match='forced_admission'):
        A.AdmissionPolicy(12, 2, 2, 1)


def test_normal_broad_producer_carries_history_into_candidate_without_deep_fetch(tmp_path, monkeypatch):
    from pathlib import Path
    import yaml
    from trader.data.broad_crypto import observe
    from tests.test_hierarchical_admission import broad_kernel
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    k = broad_kernel(tmp_path, cfg, n=4)
    data = inputs(short=True)
    k.feed.cached_ohlcv = Mock(side_effect=lambda s, tf, **kw: data[s][tf])
    monkeypatch.setattr('trader.data.broad_crypto.time.time', lambda: CUT / 1000)
    obs, _, _ = observe(k, [])
    r = A.persist(k.journal, obs, A.AdmissionPolicy.from_config(cfg))
    assert all(x['score_status'] == 'warmup' and x['score'] is None and
               x['history']['eligible_bars'] == 10 for x in r['rows'])
    c = A.candidates(r)
    assert c and all(x['history']['required_bars'] == 26 for x in c)
    assert all(x['category'] == 'exploration' for x in c)
    k.feed.fetch_ohlcv.assert_not_called()
