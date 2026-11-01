"""Per-evaluation column reuse preserves the shared trade's complete evidence."""
from dataclasses import asdict
import json

import numpy as np
import pytest

from trader.strategy import vector_backtest as V
from tests.test_d6_d7_vector_handoff import RISK, signal_case


@pytest.mark.parametrize('side', ['long', 'short'])
@pytest.mark.parametrize('trail,target', [(False, False), (True, False), (True, True)])
def test_reuse_matches_uncached_trades_and_evidence(monkeypatch, side, trail, target):
    df, ex, lo, sh, signal, available = signal_case(side, trail, target)
    lo[220] = sh[230] = True
    funding = np.linspace(-.0002, .0003, len(df))
    funding[215] = np.nan

    def run():
        fills, evidence, trace = [], [], []
        result = V.simulate(lo, sh, df, ex, RISK, funding=funding,
                            exit_sig=signal, exit_sig_available=available,
                            fills_out=fills, exit_evidence_out=evidence, trace_out=trace)
        table = V.trade_table(df, ex, RISK, funding=funding,
                              exit_sig=signal, exit_sig_available=available)
        return json.dumps([asdict(result), [asdict(f) for f in fills], evidence, trace],
                          sort_keys=True), table

    cached, table = run()
    original = V._try_trade

    def uncached(*args, **kwargs):
        kwargs.pop('_columns', None)
        return original(*args, **kwargs)

    monkeypatch.setattr(V, '_try_trade', uncached)
    expected, expected_table = run()
    assert cached == expected
    for side in table:
        for got, want in zip(table[side], expected_table[side]):
            np.testing.assert_array_equal(got, want)


def test_column_cache_is_local_to_each_evaluation(monkeypatch):
    df, ex, lo, sh, signal, available = signal_case('long')
    original = V._try_trade
    caches = []

    def spy(*args, **kwargs):
        caches.append(kwargs['_columns'])
        return original(*args, **kwargs)

    monkeypatch.setattr(V, '_try_trade', spy)
    V.trade_table(df, ex, RISK, exit_sig=signal, exit_sig_available=available)
    first = caches[:]
    assert first and all(cache is first[0] for cache in first)
    caches.clear()
    # A changed frame must never reuse an earlier evaluation's quality values.
    df['quality'] = 'INVALID'
    with pytest.raises(ValueError, match='exit_observation_not_consecutive_closed_bar'):
        V.trade_table(df, ex, RISK, exit_sig=signal, exit_sig_available=available)
    assert caches[0] is not first[0]
