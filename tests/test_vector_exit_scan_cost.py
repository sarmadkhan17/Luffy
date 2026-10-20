"""The canonical exit scan reads scalars, not whole pandas rows, per bar.

`df.iloc[j]` builds a row Series and deep-copies `df.attrs` on every bar of
every trade, which made the null baseline's thousands of simulated trades
dominate research runtime. The scan must not do that, and quality evidence in
the frame must still gate each observation exactly as before.
"""
import numpy as np
import pandas as pd
import pytest

from trader.strategy import exit_policy as E
from trader.strategy import vector_backtest as V
from tests.test_d6_d7_vector_handoff import RISK, signal_case


def _row_extractions(monkeypatch, max_bars):
    df, ex, lo, sh, signal, available = signal_case('long', trail=True, max_bars=max_bars)
    signal[:] = False
    calls = []
    original = pd.core.indexing._iLocIndexer.__getitem__

    def spy(self, key):
        if isinstance(self.obj, pd.DataFrame):
            calls.append(key)
        return original(self, key)

    with monkeypatch.context() as mp:
        mp.setattr(pd.core.indexing._iLocIndexer, '__getitem__', spy)
        V.simulate(lo, sh, df, ex, RISK, exit_sig=signal, exit_sig_available=available)
    return len(calls)


def test_row_extraction_does_not_grow_with_bars_held(monkeypatch):
    # A per-bar `df.iloc[j]` would make the longer hold cost many more rows.
    assert _row_extractions(monkeypatch, 3) == _row_extractions(monkeypatch, 25)


@pytest.mark.parametrize('column,value', [('quality', 'INVALID'), ('bar_state', 'PARTIAL')])
def test_quality_evidence_still_gates_each_observation(column, value):
    df, ex, lo, sh, signal, available = signal_case('long', max_bars=6)
    df[column] = 'VALID' if column == 'quality' else 'FINAL'
    df.loc[211, column] = value
    with pytest.raises(ValueError, match='exit_observation_not_consecutive_closed_bar'):
        V.simulate(lo, sh, df, ex, RISK, exit_sig=signal, exit_sig_available=available)


def test_retrospective_frames_read_observation_quality_not_quality():
    df, ex, lo, sh, signal, available = signal_case('long', max_bars=6)
    df.attrs['read_mode'] = 'retrospective'
    df['quality'] = 'INVALID'              # ignored in this read mode
    df['observation_quality'] = 'VALID'
    assert V.simulate(lo, sh, df, ex, RISK, exit_sig=signal,
                      exit_sig_available=available).trades == 1
    df.loc[211, 'observation_quality'] = 'INVALID'
    with pytest.raises(ValueError, match='exit_observation_not_consecutive_closed_bar'):
        V.simulate(lo, sh, df, ex, RISK, exit_sig=signal, exit_sig_available=available)


def test_scan_stops_at_the_policy_clock_exit_not_the_end_of_data():
    df, ex, lo, sh, signal, available = signal_case('long', max_bars=3)
    signal[:] = False
    seen = []
    original = E.advance
    E_advance = lambda *a, **k: (seen.append(1), original(*a, **k))[1]
    import unittest.mock as m
    with m.patch.object(E, 'advance', E_advance):
        fills = []
        V.simulate(lo, sh, df, ex, RISK, exit_sig=signal, exit_sig_available=available,
                   fills_out=fills)
    assert fills[0].exit_i - 211 < 3 and len(seen) == 3
