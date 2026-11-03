"""Array kernels preserve the policy oracle and Pandas swing boundaries."""
import json
from dataclasses import asdict
import numpy as np
import pandas as pd
import pytest

from trader.strategy import vector_backtest as V, exit_policy as E
from trader.strategy.spec import ExitSpec


@pytest.mark.parametrize('lookback', [-2, 0, 1, 5, 50])
@pytest.mark.parametrize('side', ['long', 'short'])
def test_swing_extrema_match_pandas_slices(lookback, side):
    values = np.array([np.nan, np.nan, 3, -np.inf, 5, np.nan, np.inf, 2, np.nan])
    df = pd.DataFrame({'low': values, 'high': values + 10})
    ex = ExitSpec(stop={'kind': 'swing', 'lookback': lookback})
    columns = {}
    for i in range(len(df)):
        w = df.iloc[max(0, i-lookback):i+1]
        distance = 100-float(w.low.min()) if side == 'long' else float(w.high.max())-100
        expected = max(distance, .4)
        for cache in (None, columns):
            actual = V._stop_distance(ex, 100., 1., df, i, side, cache)
            np.testing.assert_equal(actual, expected)


def candles(seed, tf):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .003, 280)))
    opens = close * np.exp(rng.normal(0, .002, len(close)))
    df = pd.DataFrame({'ts': pd.date_range('2026-01-01', periods=len(close), freq=tf, tz='UTC'),
        'open': opens, 'high': np.maximum(opens, close)*1.004,
        'low': np.minimum(opens, close)*.996, 'close': close, 'volume': 100.})
    df.attrs['timeframe'] = tf
    return df, rng


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('tf', ['1h', '4h'])
def test_numeric_canonical_table_equals_policy_oracle(seed, tf, monkeypatch):
    df, rng = candles(seed, tf)
    ex = ExitSpec(stop={'kind': 'atr', 'mult': 2.},
        target={'kind': 'none'} if seed % 2 else {'kind': 'rr', 'v': 2.},
        trail={'kind': 'atr', 'mult': 1.5, 'arm_at_r': .5} if seed % 3 else {'kind': 'none'},
        time={'max_bars': 3 + seed*2}, signal_exit='close < 100' if seed % 2 else '')
    ex._exit_semantics_id, ex._exit_timeframe = E.EXIT_SEMANTICS_ID, tf
    funding = rng.normal(0, .0003, len(df))
    funding[::7] = np.nan
    signals = rng.random(len(df)) < .2
    available = np.ones(len(df), dtype=bool)
    risk = dict(risk_per_trade_pct=1., bar_minutes=60 if tf == '1h' else 240)
    entries = rng.random(len(df)) < .3
    def simulate():
        evidence, trace, fills = [], [], []
        result = V.simulate(entries, entries, df, ex, risk, funding=funding,
            exit_sig=signals, exit_sig_available=available, exit_evidence_out=evidence,
            trace_out=trace, fills_out=fills)
        return json.dumps([asdict(result), evidence, trace, [asdict(f) for f in fills]], sort_keys=True)
    got_simulation = simulate()
    got = V.trade_table(df, ex, risk, funding=funding, exit_sig=signals, exit_sig_available=available)
    original = V._try_trade

    def policy_oracle(*args, **kwargs):
        kwargs['transitions_out'] = []  # shared canonical policy, including its exact state objects
        return original(*args, **kwargs)

    monkeypatch.setattr(V, '_try_trade', policy_oracle)
    assert simulate() == got_simulation
    expected = V.trade_table(df, ex, risk, funding=funding, exit_sig=signals, exit_sig_available=available)
    for side in got:
        for actual, reference in zip(got[side], expected[side]):
            np.testing.assert_array_equal(actual, reference)


@pytest.mark.parametrize('seed', range(12))
def test_compiled_walk_preserves_sequential_equity_and_precedence(seed):
    rng = np.random.default_rng(seed)
    n = 400
    table = {}
    for side in ('long', 'short'):
        ex = np.minimum(np.arange(n) + rng.integers(1, 10, n), n-1)
        ex[rng.random(n) < .1] = -1
        ex[rng.random(n) < .01] = -2
        table[side] = ex, rng.normal(0, 1, n), rng.uniform(0, 100, n)
    long, short = rng.random(n) < .3, rng.random(n) < .3
    eq, cursor, expected = 2000., V.WARMUP, []
    for i in np.flatnonzero(long | short):
        if i < cursor or i >= n-1:
            continue
        ex, r, pos = table['long' if long[i] else 'short']
        if ex[i] == -1 or eq*.01*pos[i] < 10:
            continue
        if ex[i] == -2:
            break
        expected.append((int(i), int(ex[i]), float(r[i])))
        eq += r[i]*(eq*.01)
        cursor = ex[i] + 1
    assert V.walk_table(table, long, short, dict(risk_per_trade_pct=1.)) == expected
