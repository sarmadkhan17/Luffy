import numpy as np
import pandas as pd
import pytest

from trader.research.closure import development as D
from trader.research.compose import graph
from trader.strategy.vector_backtest import funding_for
from trader.data.market_provenance import ms


def test_cost_funding_uses_the_study_input_and_rejects_a_different_frame(monkeypatch):
    n, step = 30, 14_400_000
    ts = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    frame = pd.DataFrame(dict(ts=ts, close=np.ones(n), open=np.ones(n), high=np.ones(n), low=np.ones(n), volume=np.ones(n)))
    env = dict(manifest_id='a' * 64, dev_end_ms=int(ms(ts)[-1] + step),
               fit_window=[int(ms(ts)[0]), int(ms(ts)[-1] + step)], development_mode='RETROSPECTIVE',
               risk=dict(real_funding=True), tf='4h')
    candidate = graph.make_candidate(dict(question='q', rationale='r', expected='e', failure_conditions='f', tf='4h',
        geo='fixed', structure='long_only', long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 50}}, short=None))

    def supply(rate, identity):
        f = pd.DataFrame(dict(ts=ts[::2], event_time_ms=ms(ts[::2]), value=np.full(len(ts[::2]), rate),
                              quality='VALID', max_age_ms=3 * step))
        f.attrs['read_mode'] = 'retrospective'
        return dict(derivs={'A': {'funding': f}}, gaps=[], data_id=identity, provenance={})

    def deny_global(*a, **k):
        raise AssertionError('must not read a global funding source')
    from trader.strategy import spec_evidence
    monkeypatch.setattr(spec_evidence, 'funding_series', deny_global)
    monkeypatch.setattr(D.cdata, 'load_external', lambda *a, **k: supply(.001, 'rates1'))
    result = D._external(candidate, 'snapshot.db', 'derivatives.db', {'A': frame}, env)
    assert not result['gaps']
    assert np.all(funding_for('A', frame, env['risk']) == .001)
    with pytest.raises(ValueError, match='binding does not match'):
        funding_for('A', frame.iloc[1:].copy(), env['risk'])
    with pytest.raises(ValueError, match='binding does not match'):
        funding_for('B', frame, env['risk'])
    monkeypatch.setattr(D.cdata, 'load_external', lambda *a, **k: supply(.002, 'rates2'))
    D._external(candidate, 'snapshot.db', 'derivatives.db', {'A': frame}, env)
    assert np.all(funding_for('A', frame, env['risk']) == .002)
    assert frame.attrs['research_funding_binding']['source_id'] == 'rates2'


def test_missing_measured_funding_stays_blocked_instead_of_using_flat_or_zero(monkeypatch):
    frame = pd.DataFrame(dict(ts=pd.date_range('2024-01-01', periods=3, freq='4h', tz='UTC'), close=[1., 1., 1.]))
    env = dict(manifest_id='a' * 64, dev_end_ms=int(ms(frame.ts)[-1] + 14_400_000),
               fit_window=[int(ms(frame.ts)[0]), int(ms(frame.ts)[-1] + 14_400_000)],
               development_mode='RETROSPECTIVE', risk=dict(real_funding=True), tf='4h',
               policy=dict(min_discovery_symbols=1))
    monkeypatch.setattr(D.cdata, 'load_external', lambda *a, **k: dict(derivs={'A': {}}, gaps=[], data_id='missing', provenance={}))
    result = D._external({}, 'snapshot.db', 'derivatives.db', {'A': frame}, env)
    assert result['gaps'][0]['requirement'] == 'funding'
    assert 'research_funding_binding' not in frame.attrs
