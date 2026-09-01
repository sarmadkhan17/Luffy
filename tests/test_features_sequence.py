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
