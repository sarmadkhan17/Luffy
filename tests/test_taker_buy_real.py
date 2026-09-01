"""taker_buy must be a measurement, not a candle-shape proxy.

`taker_buy` was computed as volume * (close - low) / (high - low), which is a
pure function of OHLCV and carries no order-flow information at all. Measured
against Binance's published takerBuyBaseAssetVolume over 3000 bars it
correlated only +0.42, had 3x the dispersion, and disagreed on the DIRECTION
of the aggressor imbalance 33% of the time.

The flow analyst and both "Aggressor" specs read this column believing it
reports who was paying the spread.
"""
import numpy as np
import pandas as pd

from trader.data.feed import DataFeed


def _rows(n=5, width=12):
    """ccxt-style kline rows. Width 12 = raw Binance (taker-buy at index 9)."""
    out = []
    for i in range(n):
        ts = 1_700_000_000_000 + i * 900_000
        o, h, l, c, v = 100.0, 110.0, 90.0, 108.0, 1000.0
        row = [ts, o, h, l, c, v]
        if width == 12:
            # real aggressor buy = 30% of volume, while the candle-shape proxy
            # would say (108-90)/(110-90) = 90%. The two must not agree.
            row += [0, 0, 0, 300.0, 0, 0]
        out.append(row)
    return out


def test_real_taker_buy_is_used_when_the_venue_publishes_it():
    df = DataFeed()._frame(_rows(width=12))
    assert np.allclose(df["taker_buy"], 300.0), (
        "must use the venue's published takerBuyBaseAssetVolume, not "
        "volume*(close-low)/(high-low) which would give 900")


def test_taker_buy_is_nan_when_the_venue_does_not_publish_it():
    """Missing information is NaN, never a fabricated default. A synthesised
    value reads as a real measurement and fires flow trades on data that does
    not exist."""
    df = DataFeed()._frame(_rows(width=6))
    assert df["taker_buy"].isna().all()


def test_frame_still_yields_the_ohlcv_contract():
    df = DataFeed()._frame(_rows(width=12))
    assert list(df.columns)[:6] == ["ts", "open", "high", "low", "close", "volume"]
    assert len(df) == 5 and pd.api.types.is_datetime64_any_dtype(df["ts"])
