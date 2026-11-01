"""Unused timeframes cost nothing; required entry/model/exit inputs stay identical."""
import numpy as np
import pandas as pd
import pytest

from trader.research.closure import development as D
from trader.research.compose import graph, models
from trader.strategy import dsl, fitted, spec_evidence
from trader.strategy.features import FeatureCtx


def candidate(node=None, **kw):
    return graph.make_candidate(dict(question='q', rationale='r', expected='e', failure_conditions='f',
        structure='long_short', tf='15m', geo='fixed',
        long=node or {'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 50}},
        short={'cmp': 'lt', 'a': {'f': 'rsi14'}, 'b': {'lit': 30}}, **kw))


def higher():
    return {'op': 'htf', 'tf': '4h', 'a': {'f': 'rsi14'}}


@pytest.mark.parametrize('where', ['entry', 'fit', 'exit'])
def test_higher_context_retained_for_each_stage(where):
    if where == 'entry':
        c = candidate({'cmp': 'gt', 'a': higher(), 'b': {'lit': 50}})
    elif where == 'fit':
        c = candidate({'top': {'a': {'fit_spec': dict(model='ridge', side='long', horizon=6,
            inputs=[higher()], alpha=.01, max_depth=2, min_leaf=50)}, 'p': .9, 'dir': 'above'}})
    else:
        c = candidate(exit=dict(stop={'kind': 'atr', 'mult': 2}, time={'max_bars': 30},
            signal_exit={'cmp': 'gt', 'a': higher(), 'b': {'lit': 50}}))
    assert D._context_timeframes(c) == ('4h',)


def test_unused_context_is_not_aggregated_and_required_signals_match(monkeypatch):
    n = 2000
    close = 100 + np.sin(np.arange(n) / 20)
    df = pd.DataFrame(dict(ts=pd.date_range('2024-01-01', periods=n, freq='15min'),
        open=close, high=close + 1, low=close - 1, close=close, volume=np.ones(n)))
    eager = spec_evidence.frames_for(df, '15m')
    from trader.strategy import backtest
    original = backtest.resample
    calls = []

    def counted(frame, tf):
        calls.append(tf)
        return original(frame, tf)

    monkeypatch.setattr(backtest, 'resample', counted)
    selected = spec_evidence.frames_for(df, '15m', context_timeframes=D._context_timeframes(candidate()))
    assert calls == [] and set(selected) == {'15m'}
    selected = spec_evidence.frames_for(df, '15m', context_timeframes=('4h',))
    assert calls == ['4h'] and set(selected) == {'15m', '4h'}
    pd.testing.assert_frame_equal(selected['4h'], eager['4h'])
    for expr in ('rsi(14) > 50', 'htf("4h", rsi(14)) > 50'):
        a = dsl.evaluate(dsl.parse(expr), FeatureCtx(eager, tf='15m'))
        b = dsl.evaluate(dsl.parse(expr), FeatureCtx(selected, tf='15m'))
        pd.testing.assert_series_equal(a, b)


def test_retained_model_inputs_are_part_of_the_context_plan():
    x = np.arange(100, dtype=float).reshape(-1, 1)
    artifact = models.fit('ridge', ['htf("4h", rsi(14))'], x, x[:, 0],
        horizon=3, fit_window=[0, 1], alpha=.01)
    fitted.register_artifact(artifact)
    c = candidate({'cmp': 'gt', 'a': {'fit': artifact['artifact_id']}, 'b': {'lit': 0}})
    assert D._context_timeframes(c) == ('4h',)
