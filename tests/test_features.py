import numpy as np
import pandas as pd
import pytest

from trader.agents import indicators as ind


@pytest.fixture
def df():
    rng = np.random.default_rng(11)
    n = 500
    close = 50000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    spread = close * 0.0015
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close,
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


def test_atr_series_last_matches_scalar(df):
    assert ind.atr_series(df, 14).iloc[-1] == pytest.approx(ind.atr(df, 14))


def test_adx_series_last_matches_scalar(df):
    assert ind.adx_series(df, 14).iloc[-1] == pytest.approx(ind.adx(df, 14))


def test_vwap_series_last_matches_scalar(df):
    assert ind.vwap_series(df, 96).iloc[-1] == pytest.approx(
        ind.anchored_vwap(df, 96))


def test_zscore_series_last_matches_scalar(df):
    s = df["close"]
    assert ind.zscore_series(s, 96).iloc[-1] == pytest.approx(
        ind.zscore(s, 96), abs=1e-9)


def test_realized_vol_series_last_matches_scalar(df):
    assert ind.realized_vol_series(df, 48).iloc[-1] == pytest.approx(
        ind.realized_vol(df, 48), rel=1e-6)


def test_series_are_full_length_and_aligned(df):
    for s in (ind.atr_series(df, 14), ind.adx_series(df, 14),
              ind.vwap_series(df, 96), ind.realized_vol_series(df, 48)):
        assert len(s) == len(df)
        assert s.index.equals(df.index)
