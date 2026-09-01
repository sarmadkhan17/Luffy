"""Sequence features — a setup is an event followed by a condition."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy import dsl
from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(close):
    n = len(close)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.ones(n)})
    return FeatureCtx(frames={"15m": df}, tf="15m")


def test_bars_since_counts_from_the_last_true_bar():
    ctx = _ctx(np.array([1.0, 5.0, 1.0, 1.0, 1.0]))
    out = dsl.evaluate(dsl.parse("bars_since(close > 3)"), ctx)
    assert list(out.iloc[1:]) == [0.0, 1.0, 2.0, 3.0]


def test_bars_since_is_nan_before_the_condition_has_ever_been_true():
    """Never-true is unknown, not zero."""
    ctx = _ctx(np.array([1.0, 1.0, 1.0]))
    out = dsl.evaluate(dsl.parse("bars_since(close > 99)"), ctx)
    assert out.isna().all()


def test_streak_counts_consecutive_true_bars_and_resets():
    ctx = _ctx(np.array([5.0, 5.0, 1.0, 5.0]))
    out = dsl.evaluate(dsl.parse("streak(close > 3)"), ctx)
    assert list(out) == [1.0, 2.0, 0.0, 1.0]


def test_swing_high_only_reports_a_pivot_after_it_is_confirmed():
    """A pivot at bar i needs n later bars to confirm, so it cannot be
    visible at i — that would be lookahead."""
    close = np.array([1.0, 2.0, 9.0, 2.0, 1.0, 1.0, 1.0])
    ctx = _ctx(close)
    out = FEATURES["swing_high"].fn(ctx, 2)
    assert np.isnan(out.iloc[2]), "pivot visible on its own bar = lookahead"
    assert out.iloc[4] == 10.0        # high = close + 1


def test_bars_since_with_nan_input_remains_nan():
    """NaN input means unknown condition, so output must be NaN."""
    close = np.arange(1.0, 11.0)
    n = len(close)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.ones(n)})
    ctx = FeatureCtx(frames={"15m": df}, tf="15m")

    # Create a Series with leading NaNs and trailing True/False values
    nan_series = pd.Series([np.nan] * 5 + [False, False, True, False, False],
                          index=df.index)
    # Call _bars_since directly with NaN input
    out = FEATURES["bars_since"].fn(ctx, nan_series)
    # First 5 bars have NaN input, should stay NaN
    assert out.iloc[:5].isna().all(), "bars_since should be NaN where input is NaN"
    # After bar 7 (where condition becomes True), should count up
    assert out.iloc[7] == 0.0  # True on bar 7
    assert out.iloc[8] == 1.0  # 1 bar since bar 7 was true
    assert out.iloc[9] == 2.0  # 2 bars since bar 7 was true


def test_streak_with_nan_input_remains_nan():
    """NaN input means unknown condition, so output must be NaN."""
    close = np.arange(1.0, 11.0)
    n = len(close)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.ones(n)})
    ctx = FeatureCtx(frames={"15m": df}, tf="15m")

    # Create a Series with leading NaNs then True values
    nan_series = pd.Series([np.nan] * 5 + [True, True, False, True, True],
                          index=df.index)
    # Call _streak directly with NaN input
    out = FEATURES["streak"].fn(ctx, nan_series)
    # First 5 bars have NaN input, should stay NaN
    assert out.iloc[:5].isna().all(), "streak should be NaN where input is NaN"
    # After NaN warmup
    assert out.iloc[5] == 1.0   # first True
    assert out.iloc[6] == 2.0   # streak continues
    assert out.iloc[7] == 0.0   # False breaks streak
