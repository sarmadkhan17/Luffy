"""Standard vocabulary that was simply absent."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(close):
    n = len(close)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.ones(n)})
    return FeatureCtx(frames={"15m": df}, tf="15m")


def test_macd_histogram_is_positive_in_an_uptrend():
    ctx = _ctx(np.arange(1.0, 121.0))
    assert FEATURES["macd"].fn(ctx, 12, 26, 9).iloc[-1] > 0


def test_macd_histogram_is_negative_in_a_downtrend():
    ctx = _ctx(np.arange(120.0, 0.0, -1.0))
    assert FEATURES["macd"].fn(ctx, 12, 26, 9).iloc[-1] < 0


def test_keltner_brackets_the_price():
    ctx = _ctx(np.concatenate([np.ones(60) * 100, np.array([101.0])]))
    up = FEATURES["keltner_upper"].fn(ctx, 20, 2.0).iloc[-1]
    dn = FEATURES["keltner_lower"].fn(ctx, 20, 2.0).iloc[-1]
    assert dn < 100.0 < up


def test_atr_pct_rank_is_high_when_volatility_expands():
    calm = np.ones(80) * 100
    wild = 100 + np.arange(1, 21) * 3.0
    ctx = _ctx(np.concatenate([calm, wild]))
    assert FEATURES["atr_pct_rank"].fn(ctx, 60).iloc[-1] > 0.8
