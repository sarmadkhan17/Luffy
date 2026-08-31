import numpy as np
import pandas as pd
import pytest

from trader.agents import regime


def _frame(n=600, drift=0.0, vol=0.005, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, vol, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]


@pytest.mark.parametrize("drift,vol,seed", [
    (0.0, 0.005, 1), (0.004, 0.004, 2), (-0.004, 0.004, 3),
    (0.0, 0.012, 4), (0.001, 0.008, 5),
])
def test_series_last_value_matches_the_live_classifier(drift, vol, seed):
    """The live gate and the measured regime_filter must mean the same thing,
    or a strategy is admitted for one regime and traded in another."""
    df = _frame(600, drift, vol, seed)
    assert regime.regime_series(df).iloc[-1] == \
        regime.classify(df, None)["regime"]


def test_series_covers_the_whole_frame():
    df = _frame(400)
    s = regime.regime_series(df)
    assert len(s) == len(df) and s.index.equals(df.index)


def test_only_known_labels_are_emitted():
    s = regime.regime_series(_frame(800, 0.002, 0.007, 11))
    assert set(s.unique()) <= {"TRENDING_UP", "TRENDING_DOWN", "RANGING",
                               "VOLATILE", "UNKNOWN"}


def test_warmup_bars_are_unknown_not_guessed():
    s = regime.regime_series(_frame(400))
    assert s.iloc[0] == "UNKNOWN"


def test_short_frame_returns_empty():
    assert len(regime.regime_series(_frame(10))) == 0


def test_trending_frame_is_mostly_trending():
    s = regime.regime_series(_frame(1200, drift=0.006, vol=0.003, seed=7))
    known = s[s != "UNKNOWN"]
    assert (known == "TRENDING_UP").mean() > 0.4
