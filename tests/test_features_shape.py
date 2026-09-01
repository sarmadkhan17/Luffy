"""Bar shape and participation — what a candle says beyond its close."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(**cols):
    n = len(next(iter(cols.values())))
    base = {"ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.ones(n), "high": np.ones(n), "low": np.ones(n),
            "close": np.ones(n), "volume": np.ones(n)}
    base.update(cols)
    return FeatureCtx(frames={"15m": pd.DataFrame(base)}, tf="15m")


def _f(name, ctx, *args):
    return FEATURES[name].fn(ctx, *args)


def test_efficiency_ratio_is_one_for_a_straight_line():
    ctx = _ctx(close=np.arange(1.0, 21.0))
    assert _f("efficiency_ratio", ctx, 10).iloc[-1] == pytest.approx(1.0)


def test_efficiency_ratio_is_near_zero_for_a_round_trip():
    up = np.arange(1.0, 11.0)
    close = np.concatenate([up, up[::-1][1:]])
    ctx = _ctx(close=close)
    assert _f("efficiency_ratio", ctx, 18).iloc[-1] < 0.1


def test_body_frac_is_one_for_a_marubozu_and_zero_for_a_doji():
    ctx = _ctx(open=np.array([1.0, 2.0]), close=np.array([2.0, 2.0]),
               high=np.array([2.0, 3.0]), low=np.array([1.0, 1.0]))
    out = _f("body_frac", ctx)
    assert out.iloc[0] == pytest.approx(1.0)
    assert out.iloc[1] == pytest.approx(0.0)


def test_wicks_split_the_range():
    ctx = _ctx(open=np.array([2.0]), close=np.array([3.0]),
               high=np.array([4.0]), low=np.array([1.0]))
    assert _f("upper_wick", ctx).iloc[0] == pytest.approx(1 / 3)
    assert _f("lower_wick", ctx).iloc[0] == pytest.approx(1 / 3)


def test_rel_volume_reports_a_multiple_of_the_average():
    """Nine bars at 1.0 then one at 3.0: mean over the last ten is 1.2,
    so the multiple is exactly 3 / 1.2."""
    v = np.concatenate([np.ones(10), np.array([3.0])])
    ctx = _ctx(volume=v)
    assert _f("rel_volume", ctx, 10).iloc[-1] == pytest.approx(2.5)


def test_dd_from_high_is_zero_at_a_new_high_and_negative_below():
    h = np.array([1.0, 2.0, 3.0, 3.0])
    ctx = _ctx(high=h, close=np.array([1.0, 2.0, 3.0, 1.5]))
    out = _f("dd_from_high", ctx, 3)
    assert out.iloc[2] == pytest.approx(0.0)
    assert out.iloc[3] < -0.4


def test_a_flat_range_gives_nan_not_a_fabricated_zero():
    """Missing information is NaN. A fabricated 0.0 reads as a real reading."""
    ctx = _ctx(open=np.array([1.0]), close=np.array([1.0]),
               high=np.array([1.0]), low=np.array([1.0]))
    assert np.isnan(_f("body_frac", ctx).iloc[0])
