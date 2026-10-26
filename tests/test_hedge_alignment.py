"""compose.hedge.align_pair: a full UTC clock, validated, bounded, and causal (receipts, quality, consumed windows) for BOTH legs."""
import numpy as np
import pandas as pd
import pytest

from trader.core.types import TF_MS
from trader.research.compose import hedge as H

STEP = TF_MS['4h']
T0 = pd.Timestamp('2024-01-01', tz='UTC')


def frame(slots, delay=0, quality=None):
    slots = list(slots)
    ts = [T0 + pd.Timedelta(milliseconds=STEP * k) for k in slots]
    px = 100 + np.arange(len(slots), dtype=float)
    df = pd.DataFrame(dict(ts=ts, open=px, high=px + 1, low=px - 1, close=px, volume=1.0))
    close_ms = (T0.value // 10 ** 6) + np.array(slots, dtype='int64') * STEP + STEP
    df['available_at_ms'] = close_ms + (np.broadcast_to(np.asarray(delay), len(slots)) if np.ndim(delay) else delay)
    df['quality'] = 'VALID' if quality is None else quality
    df['bar_state'] = 'FINAL'
    df.attrs.update(read_mode='replay', timeframe='4h')
    return df


def sig(n, long_at=(), short_at=()):
    lo, sh = np.zeros(n, bool), np.zeros(n, bool)
    lo[list(long_at)] = True
    sh[list(short_at)] = True
    return lo, sh


def run(df, hdf, lo, sh, **kw):
    return H.align_pair(df, hdf, lo, sh, STEP, **kw)


def test_known_before_decision_inputs_are_executable_and_a_late_hedge_close_is_not():
    n = 40
    lo, sh = sig(n, long_at=[20])
    good = run(frame(range(n)), frame(range(n)), lo, sh, p_window=10, h_window=1)
    assert good['sig'][20] == 1 and good['refused']['hedge_not_executable'] == 0
    late = frame(range(n), delay=np.where(np.arange(n) == 20, 2 * STEP, 0))          # hedge close for slot 20 observed 2 slots late
    bad = run(frame(range(n)), late, lo, sh, p_window=10, h_window=1)
    assert bad['sig'][20] == 0 and bad['refused']['hedge_not_executable'] == 1
    assert bad['refused']['hedge_reasons']                                            # a named reason, not a silent drop


def test_a_primary_input_received_after_the_next_open_inside_the_real_window_refuses_only_with_the_real_window():
    n = 80
    lo, sh = sig(n, long_at=[60])
    delay = np.where(np.arange(n) == 30, 200 * STEP, 0)                               # slot 30 arrives long after slot 60's next open
    prim = frame(range(n), delay=delay)
    real = run(prim, frame(range(n)), lo, sh, p_window=50, h_window=1)                # 60-30 < 50: consumed
    assert real['sig'][60] == 0 and real['refused']['primary_not_executable'] == 1
    short = run(prim, frame(range(n)), lo, sh, p_window=20, h_window=1)               # the old, too-short window never looks back 30
    assert short['sig'][60] == 1


def test_a_hedge_leg_is_not_given_the_15_bar_indicator_default_but_its_ratio_lookback_when_dynamic():
    n = 60
    lo, sh = sig(n, long_at=[8, 40])
    hedge = frame([k for k in range(n) if k != 5])                                    # a hedge source hole at slot 5
    price_only = run(frame(range(n)), hedge, lo, sh, p_window=3, h_window=1)
    assert price_only['sig'][8] == 1                                                  # a price-only hedge does not need 15 clean bars
    rolling = run(frame(range(n)), hedge, lo, sh, p_window=3, h_window=21)            # rolling beta consumes lookback+1 = 21 bars
    assert rolling['sig'][8] == 0 and rolling['refused']['hedge_reasons'].get('history_gap') == 1
    assert rolling['sig'][40] == 1                                                    # the hole has left its window


def test_invalid_or_partial_leg_rows_are_not_executable():
    n = 30
    lo, sh = sig(n, long_at=[15])
    q = np.array(['VALID'] * n, dtype=object)
    q[15] = 'INVALID'
    out = run(frame(range(n)), frame(range(n), quality=q), lo, sh, p_window=5, h_window=1)
    assert out['sig'][15] == 0


def test_signal_edges_keep_flips_and_alternation_but_not_same_direction_repeats_or_delayed_replacements():
    n = 40
    lo, sh = sig(n, long_at=[10, 11, 20], short_at=[12, 30])
    out = run(frame(range(n)), frame(range(n)), lo, sh, p_window=3, h_window=1)
    s = out['sig']
    assert s[10] == 1 and s[11] == 0                                                  # a same-direction repeat does not re-enter
    assert s[12] == -1                                                                # the true opposite signal is NOT deleted
    assert s[20] == 1 and s[30] == -1                                                 # alternating episodes both survive
    # a refused edge is not replaced by a later bar of the same run (no delayed fill)
    hdf = frame([k for k in range(n) if k != 11])                                     # hedge has no open for slot 11 (fill of a slot-10 signal)
    gap = run(frame(range(n)), hdf, *sig(n, long_at=[10, 11]), p_window=3, h_window=1)
    assert gap['sig'][10] == 0 and gap['sig'][11] == 0 and gap['refused']['leg_open_missing_at_fill'] >= 1


def test_malformed_timestamps_and_unbounded_grids_are_refused_explicitly():
    n = 20
    lo, sh = sig(n)
    dup = frame(range(n))
    dup.loc[5, 'ts'] = dup.loc[4, 'ts']
    with pytest.raises(ValueError, match='duplicate_or_unordered'):
        run(dup, frame(range(n)), lo, sh)
    off = frame(range(n))
    off['ts'] = off['ts'] + pd.Timedelta(milliseconds=1)
    with pytest.raises(ValueError, match='off_grid'):
        run(frame(range(n)), off.assign(ts=[T0 + pd.Timedelta(milliseconds=STEP * k + (1 if k == 7 else 0)) for k in range(n)]), lo, sh)
    with pytest.raises(ValueError, match='empty'):
        run(frame([]), frame(range(n)), lo, sh)
    far = frame([0, 5000])
    with pytest.raises(ValueError, match='grid_exceeds_bound'):
        run(far, frame([0, 1]), np.zeros(2, bool), np.zeros(2, bool), max_slots=1000)


def test_unknown_ratio_is_never_a_fabricated_zero_hedge():
    r = np.r_[np.nan, np.random.default_rng(1).normal(0, .01, 10)]
    spec = dict(ratio=dict(method='rolling_beta', lookback=96, max_ratio=2.0))
    assert np.isnan(H.estimate_ratio(spec, r, r, 10))                                  # < 20 observations: unknown, not 0.0


def test_declared_exit_and_unknown_exit_evidence_share_pair_clock():
    from types import SimpleNamespace
    from trader.research.compose import hedge as H
    # The missing middle row and the unknown final exit both request unwind.
    compiled=SimpleNamespace(spec=SimpleNamespace(exit=SimpleNamespace(signal_exit='rsi(14) > 70')),
        exit_signal_detail=lambda frames,**kw:(np.array([False,True,False]),np.array([True,True,False])))
    aligned=dict(bars=4,idx_p=np.array([0,2,3]))
    assert H.aligned_exits(compiled,{},aligned).tolist()==[False,True,True,True]
    compiled.spec.exit.signal_exit=''
    assert not H.aligned_exits(compiled,{},aligned).any()
