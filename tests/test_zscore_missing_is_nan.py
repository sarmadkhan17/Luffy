"""A z-score of data that does not exist is NaN, never zero.

`zscore_series` ended `.fillna(0.0)`, so funding_z, oi_z, taker_ratio_z,
ls_ratio_z and basis_z all returned 0.0 — "exactly average" — for a symbol
with no series at all. A spec testing `funding_z(96) < -2.0` was safe by
accident, but `funding_z(96) > -1.0` fired TRUE on fabricated data, and the
warmup window at the head of every frame read as average rather than unknown.

This is the registry's first rule: missing information is NaN, never a
fabricated default, because a fabricated value reads as a real measurement.

The 0.0-on-degenerate behaviour is deliberate for the analyst path, which
scores a float and cannot carry NaN, so that stays — the strict variant is
what the feature layer uses.
"""
import numpy as np
import pandas as pd
import pytest

from trader.agents import indicators as ind


def test_the_analyst_helper_keeps_its_zero_fill():
    """Analyst scoring cannot carry NaN; do not change it underneath them."""
    s = pd.Series([1.0] * 5)
    assert ind.zscore_series(s, 96).notna().all()


def test_the_strict_variant_leaves_an_empty_series_unknown():
    s = pd.Series([np.nan] * 200)
    assert ind.zscore_series(s, 96, fill=None).isna().all()


def test_the_strict_variant_leaves_the_warmup_window_unknown():
    s = pd.Series(np.linspace(1, 200, 200))
    z = ind.zscore_series(s, 96, fill=None)
    assert z.iloc[:95].isna().all(), "a window that has not filled is unknown"
    assert z.iloc[120:].notna().all()


def test_a_flat_series_has_no_defined_z_score():
    """Zero variance means undefined, not average."""
    z = ind.zscore_series(pd.Series([5.0] * 200), 96, fill=None)
    assert z.iloc[-1] != z.iloc[-1]


def test_real_values_are_unchanged_by_the_strict_variant():
    rng = np.random.default_rng(0)
    s = pd.Series(rng.normal(0, 1, 400))
    a = ind.zscore_series(s, 96)
    b = ind.zscore_series(s, 96, fill=None)
    both = a.notna() & b.notna()
    assert np.allclose(a[both][100:], b[both][100:])


def test_funding_z_on_a_symbol_with_no_funding_is_nan():
    """The whole point: no data must not read as 'funding is average'."""
    from trader.strategy.compile import compile_spec
    from trader.strategy.features import FeatureCtx
    from trader.strategy import dsl
    n = 300
    c = np.linspace(100, 120, n)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
        "volume": 1.0})
    ctx = FeatureCtx(frames={"15m": df}, tf="15m", btc=None, derivs=None,
                     universe=None, market=None, symbol="BTC/USDT")
    z = dsl.evaluate(dsl.parse("funding_z(96)"), ctx) \
        if hasattr(dsl, "evaluate") else dsl._eval(dsl.parse("funding_z(96)").body, ctx)
    assert pd.Series(z).isna().all(), \
        "absent funding must be unknown, not 'exactly average'"


# ── the same rule, one layer up ──────────────────────────────────────────
# features.py registered taker_buy as `volume * 0.5 if tb is None else tb`,
# so a frame with no taker_buy column invented "half the volume was aggressive
# buying" — the exact fabrication that was removed from the feed layer, where
# the real published figure disagreed with the candle-shape proxy on the
# DIRECTION of the imbalance 33% of the time.

def test_taker_buy_is_nan_when_the_column_is_absent():
    from trader.strategy.features import FEATURES, FeatureCtx
    n = 50
    c = np.linspace(100, 110, n)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": c, "high": c, "low": c, "close": c, "volume": 100.0})
    ctx = FeatureCtx(frames={"15m": df}, tf="15m")
    out = FEATURES["taker_buy"].fn(ctx)
    assert pd.Series(out).isna().all(), \
        "absent aggressor volume must be unknown, not half of turnover"


def test_taker_buy_passes_a_real_column_through_untouched():
    from trader.strategy.features import FEATURES, FeatureCtx
    n = 50
    c = np.linspace(100, 110, n)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": c, "high": c, "low": c, "close": c, "volume": 100.0,
        "taker_buy": 30.0})
    ctx = FeatureCtx(frames={"15m": df}, tf="15m")
    assert (FEATURES["taker_buy"].fn(ctx) == 30.0).all()
