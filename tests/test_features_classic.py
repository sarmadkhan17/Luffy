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


def test_volume_z_is_nan_during_warmup():
    """volume_z should be NaN during warmup, not 0.0 (fabricated 'average')."""
    close = np.ones(10) * 100
    ctx = _ctx(close)
    out = FEATURES["volume_z"].fn(ctx, 20)
    # All 10 bars have insufficient history for a 20-bar window
    assert out.isna().all(), "volume_z should be NaN when window > available bars"


def test_vol_of_vol_is_higher_when_volatility_changes():
    """vol_of_vol should be higher for changing volatility than steady."""
    # Steady volatility: constant returns
    steady = np.ones(60) * 100
    ctx_steady = _ctx(steady)
    steady_vov = FEATURES["vol_of_vol"].fn(ctx_steady, 40).iloc[-1]

    # Changing volatility: calm then volatile
    calm = np.ones(30) * 100
    volatile = 100 + np.arange(1, 31) * 0.5
    ctx_changing = _ctx(np.concatenate([calm, volatile]))
    changing_vov = FEATURES["vol_of_vol"].fn(ctx_changing, 40).iloc[-1]

    # Changing volatility should have higher vol_of_vol
    assert changing_vov > steady_vov, "vol_of_vol should increase with volatility changes"
