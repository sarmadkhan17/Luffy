"""Derivative structure beyond a z-score."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(close, series_name=None, values=None):
    n = len(close)
    ts = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"ts": ts, "open": close, "high": close + 0.5,
                       "low": close - 0.5, "close": close,
                       "volume": np.ones(n)})
    derivs = None
    if series_name is not None:
        derivs = {series_name: pd.DataFrame({"ts": ts, "value": values})}
    return FeatureCtx(frames={"1h": df}, tf="1h", derivs=derivs)


def test_funding_pct_is_high_when_funding_is_at_its_extreme():
    """The spike sits in the last few observations, not only on the final
    timestamp, so the percentile has something recent to rank against.
    (This fixture does not exercise point-in-time compliance — neighbouring
    observations share the spike value, so a leaked implementation would
    give the same answer here. Real PIT coverage lives in
    tests/test_pit_alignment.py.)"""
    n = 80
    vals = np.concatenate([np.zeros(n - 5), np.full(5, 0.01)])
    ctx = _ctx(np.ones(n) * 100, "funding", vals)
    assert FEATURES["funding_pct"].fn(ctx, 60).iloc[-1] > 0.9


def test_missing_series_is_nan_not_zero():
    ctx = _ctx(np.ones(50) * 100)          # no derivs at all
    assert FEATURES["funding_pct"].fn(ctx, 20).isna().all()


def test_oi_price_div_is_positive_when_oi_rises_as_price_falls():
    n = 60
    price = np.linspace(100.0, 90.0, n)
    oi = np.linspace(1000.0, 2000.0, n)
    ctx = _ctx(price, "oi", oi)
    assert FEATURES["oi_price_div"].fn(ctx, 24).iloc[-1] > 0


def test_oi_price_div_is_nan_when_lagged_oi_is_zero():
    """When OI was zero n bars ago, division by zero produces inf unless
    masked. Assert that NaN, not inf, results from this degenerate case.

    `~np.isfinite(x) | x.isna()` is always True for both NaN AND inf (inf
    is, correctly, "not finite") — so it cannot distinguish the masked
    result this test exists to require from the bug it exists to catch.
    Check the zero-denominator bars are NaN explicitly, and separately that
    no inf sneaks through anywhere in the result."""
    n = 60
    price = np.ones(n) * 100.0
    oi = np.concatenate([np.zeros(10), np.linspace(1000.0, 2000.0, n - 10)])
    ctx = _ctx(price, "oi", oi)
    result = FEATURES["oi_price_div"].fn(ctx, 10)
    # align() itself is point-in-time (a bar sees only observations strictly
    # before its own close), adding one more bar of lag on top of shift(n) —
    # so the zero-denominator window is bars 0-20, not 0-19.
    assert result[:21].isna().all()
    # After warmup, result should be finite
    assert np.all(np.isfinite(result[21:]))
    assert not np.isinf(result).any()
