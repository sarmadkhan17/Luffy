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


from trader.strategy.features import FEATURES, FeatureCtx


@pytest.fixture
def ctx(df):
    return FeatureCtx(frames={"15m": df}, tf="15m")


def test_registry_has_core_features():
    for name in ("close", "ema", "rsi", "atr", "adx", "vwap", "volume",
                 "abs", "max", "min"):
        assert name in FEATURES, f"{name} missing from FEATURES"


def test_zero_arity_feature_returns_column(ctx, df):
    assert FEATURES["close"].fn(ctx).equals(df["close"])


def test_ema_matches_indicator(ctx, df):
    assert FEATURES["ema"].fn(ctx, 20).iloc[-1] == pytest.approx(
        ind.ema(df["close"], 20).iloc[-1])


def test_atr_feature_matches_indicator(ctx, df):
    assert FEATURES["atr"].fn(ctx, 14).iloc[-1] == pytest.approx(ind.atr(df, 14))


def test_ctx_caches_repeated_calls(ctx):
    a = ctx.get("ema", (20,))
    b = ctx.get("ema", (20,))
    assert a is b, "FeatureCtx must memoise; ema(20) appears in entry and filters"


def test_every_feature_domain_is_well_formed():
    for name, f in FEATURES.items():
        assert f.domain is None or (len(f.domain) == 2 and f.domain[0] < f.domain[1]), name


def test_bounded_features_stay_in_domain(ctx):
    for name in ("rsi", "adx"):
        s = ctx.get(name, (14,)).dropna()
        lo, hi = FEATURES[name].domain
        assert s.min() >= lo - 1e-6 and s.max() <= hi + 1e-6, name


def test_donchian_excludes_the_current_bar(ctx, df):
    hi = FEATURES["donchian_hi"].fn(ctx, 10)
    assert hi.iloc[20] == pytest.approx(df["high"].iloc[10:20].max())


def test_zscore_feature_matches_indicator(ctx, df):
    out = FEATURES["zscore"].fn(ctx, df["close"], 96)
    assert out.iloc[-1] == pytest.approx(ind.zscore(df["close"], 96), abs=1e-9)


def test_pct_rank_is_bounded(ctx, df):
    out = FEATURES["pct_rank"].fn(ctx, df["close"], 96).dropna()
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_prev_shifts_by_n_bars(ctx, df):
    out = FEATURES["prev"].fn(ctx, df["close"], 1)
    assert out.iloc[5] == pytest.approx(df["close"].iloc[4])
    assert pd.isna(out.iloc[0])


def test_hour_utc_matches_timestamps(ctx, df):
    assert int(FEATURES["hour_utc"].fn(ctx).iloc[0]) == int(
        df["ts"].dt.hour.iloc[0])


def test_is_session_is_boolean(ctx):
    out = FEATURES["is_session"].fn(ctx, "us")
    assert out.dtype == bool
    assert out.any() and not out.all()


def test_btc_features_align_to_base_index(ctx, df):
    ctx.btc = {"15m": df.copy()}
    out = FEATURES["btc_ret"].fn(ctx, 4)
    assert len(out) == len(df) and out.index.equals(df.index)


def test_btc_feature_without_btc_frame_is_nan(ctx):
    ctx.btc = None
    out = FEATURES["btc_ret"].fn(ctx, 4)
    assert out.isna().all(), "missing leader data must be NaN, never 0.0 — a " \
                             "zero would read as 'BTC flat' and fire signals"


def test_btc_lookup_does_not_raise_on_dataframe_truthiness(ctx, df):
    """`ctx.btc.get(tf) or ctx.btc.get('15m')` raises ValueError on a
    DataFrame. The lookup must use an explicit `is None` check."""
    ctx.btc = {"1h": df.copy()}          # base tf is 15m, so the get() misses
    out = FEATURES["btc_ret"].fn(ctx, 4)
    assert len(out) == len(df)


def test_rel_strength_against_itself_is_zero(ctx, df):
    ctx.btc = {"15m": df.copy()}
    out = FEATURES["rel_strength_btc"].fn(ctx, 4).dropna()
    assert abs(out).max() < 1e-9
