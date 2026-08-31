import numpy as np
import pandas as pd
import pytest

from trader.strategy.features import FEATURES, FeatureCtx
from trader.strategy.features_deriv import align


@pytest.fixture
def base():
    n = 200
    close = 100 + np.arange(n) * 0.1
    return pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


@pytest.fixture
def funding_df():
    """8-hourly funding — far coarser than the 15m base frame."""
    return pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=8, freq="8h", tz="UTC"),
        "value": [0.0001 * i for i in range(8)]})


def test_align_forward_fills_onto_base_index(base, funding_df):
    out = align(funding_df, base["ts"])
    assert len(out) == len(base)
    assert out.notna().sum() > 0


def test_align_has_no_lookahead(base, funding_df):
    """A bar may only see observations that had already settled. The
    observation stamped exactly t is knowable at the close of bar t, so bars
    up to and including t must not use it."""
    out = align(funding_df, base["ts"])
    first_obs = funding_df["ts"].iloc[0]
    before = (base["ts"] <= first_obs).values
    assert out[before].isna().all()


def test_align_never_fabricates_values(base):
    empty = pd.DataFrame({"ts": pd.to_datetime([], utc=True), "value": []})
    assert align(empty, base["ts"]).isna().all()


def test_align_with_none_is_all_nan(base):
    assert align(None, base["ts"]).isna().all()


def test_shifting_the_series_changes_the_signal(base, funding_df):
    """The lookahead guard that matters: if shifting the input does not
    change the output, alignment is not actually time-aware."""
    a = align(funding_df, base["ts"])
    shifted = funding_df.copy()
    shifted["ts"] = shifted["ts"] + pd.Timedelta("4h")
    b = align(shifted, base["ts"])
    assert not a.equals(b)


def test_align_uses_the_most_recent_prior_observation(base, funding_df):
    out = align(funding_df, base["ts"])
    # bar at 09:00 sees the 08:00 observation (index 1), not the 16:00 one
    i = int((base["ts"] == pd.Timestamp("2026-08-01 09:00", tz="UTC")).idxmax())
    assert out.iloc[i] == pytest.approx(funding_df["value"].iloc[1])


def test_funding_feature_reads_ctx_derivs(base, funding_df):
    ctx = FeatureCtx(frames={"15m": base}, tf="15m",
                     derivs={"funding": funding_df})
    out = FEATURES["funding"].fn(ctx)
    assert len(out) == len(base) and out.notna().any()


def test_funding_feature_without_data_is_nan(base):
    ctx = FeatureCtx(frames={"15m": base}, tf="15m", derivs=None)
    assert FEATURES["funding"].fn(ctx).isna().all()


def test_deriv_features_declare_their_requirement():
    assert FEATURES["funding"].requires == ("funding",)
    assert FEATURES["oi"].requires == ("open_interest",)
    assert FEATURES["taker_ratio"].requires == ("taker_ratio",)
    assert FEATURES["basis"].requires == ("basis",)


def test_taker_buy_frac_uses_the_candle_store_column(base):
    b = base.copy()
    b["taker_buy"] = b["volume"] * 0.7
    ctx = FeatureCtx(frames={"15m": b}, tf="15m")
    assert FEATURES["taker_buy_frac"].fn(ctx).iloc[-1] == pytest.approx(0.7)


def test_taker_buy_frac_needs_no_external_data():
    """It is the only flow feature backtestable over the full candle
    history, so it must not declare a derivative requirement."""
    assert FEATURES["taker_buy_frac"].requires == ("ohlcv",)


def test_deriv_expression_compiles_and_derives_its_data_requirement(base,
                                                                    funding_df):
    from trader.strategy.dsl import data_requires, evaluate_bool, parse
    tree = parse("funding_z(96) > 2.0 and close > ema(20)")
    assert data_requires(tree) == ("funding", "ohlcv")
    ctx = FeatureCtx(frames={"15m": base}, tf="15m",
                     derivs={"funding": funding_df})
    assert len(evaluate_bool(tree, ctx)) == len(base)


def test_missing_deriv_data_yields_no_signals(base):
    """A spec that needs funding must be silent, not wrong, without it."""
    from trader.strategy.dsl import evaluate_bool, parse
    ctx = FeatureCtx(frames={"15m": base}, tf="15m", derivs=None)
    assert not evaluate_bool(parse("funding_z(96) > 2.0"), ctx).any()
