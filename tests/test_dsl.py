import numpy as np
import pandas as pd
import pytest

from trader.agents import indicators as ind
from trader.strategy.dsl import (SpecError, data_requires, evaluate,
                                 evaluate_bool, features_used, parse)
from trader.strategy.features import FeatureCtx

GOOD = [
    "close > ema(20)",
    "adx(14) > 25 and rsi(14) < 30",
    "not (close > vwap(96))",
    "zscore(close, 96) < -2.0",
    "close > ema(20) and (rsi(14) < 30 or bb_pctb(20, 2.0) < 0)",
    "abs(close - ema(50)) > 2.0 * atr(14)",
    "htf('1h', close > ema(10)) and is_session('us')",
    "-1.5 > zscore(volume, 96)",
    "prev(rsi(14), 1) < 30 and rsi(14) >= 30",
    "sma_of(volume, 3) > 1.4 * prev(sma_of(volume, 48), 48)",
]

BAD = [
    "__import__('os').system('ls')",
    "close.__class__",
    "().__class__.__bases__[0].__subclasses__()",
    "close[0]",
    "[x for x in close]",
    "(lambda: 1)()",
    "open('secrets.txt')",
    "unknown_feature(3)",
    "ema(n=20)",
    "close > ema(20) if True else 0",
    "close := 5",
    "ema(20) ** 2",
    "close; volume",
    "ema()",
    "ema(20, 30)",
    "close",          # bare non-boolean is fine to parse, but see below
]


@pytest.mark.parametrize("expr", GOOD)
def test_valid_expressions_parse(expr):
    assert parse(expr) is not None


@pytest.mark.parametrize("expr", BAD[:-1])
def test_invalid_expressions_rejected(expr):
    with pytest.raises(SpecError):
        parse(expr)


def test_bare_feature_name_parses():
    """`close` alone is a valid expression; it is the compiler's job to treat
    a non-boolean entry as always-true, not the parser's."""
    assert parse("close") is not None


def test_features_used_is_complete():
    assert features_used(parse("close > ema(20) and adx(14) > 25")) == \
        {"close", "ema", "adx"}


def test_data_requires_is_derived_not_declared():
    assert data_requires(parse("close > ema(20)")) == ("ohlcv",)


def test_empty_expression_rejected():
    with pytest.raises(SpecError):
        parse("")


@pytest.fixture
def frame():
    rng = np.random.default_rng(3)
    n = 400
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": rng.uniform(50, 500, n)})


@pytest.fixture
def ctx(frame):
    return FeatureCtx(frames={"15m": frame}, tf="15m")


def test_comparison_returns_boolean_series(ctx, frame):
    out = evaluate(parse("close > ema(20)"), ctx)
    assert isinstance(out, pd.Series) and out.dtype == bool
    assert out.equals(frame["close"] > ind.ema(frame["close"], 20))


def test_and_is_elementwise(ctx):
    both = evaluate_bool(parse("close > ema(20) and adx(14) > 10"), ctx)
    a = evaluate_bool(parse("close > ema(20)"), ctx)
    b = evaluate_bool(parse("adx(14) > 10"), ctx)
    assert np.array_equal(both, a & b)


def test_or_is_elementwise(ctx):
    both = evaluate_bool(parse("close > ema(20) or adx(14) > 90"), ctx)
    a = evaluate_bool(parse("close > ema(20)"), ctx)
    b = evaluate_bool(parse("adx(14) > 90"), ctx)
    assert np.array_equal(both, a | b)


def test_not_inverts(ctx):
    a = evaluate_bool(parse("close > ema(20)"), ctx)
    n = evaluate_bool(parse("not (close > ema(20))"), ctx)
    assert np.array_equal(n, ~a)


def test_arithmetic_on_series(ctx, frame):
    out = evaluate(parse("close - ema(20)"), ctx)
    assert out.iloc[-1] == pytest.approx(
        frame["close"].iloc[-1] - ind.ema(frame["close"], 20).iloc[-1])


def test_nan_warmup_is_false_not_true(ctx):
    """A NaN comparison must never be treated as a firing signal."""
    out = evaluate_bool(parse("close > sma(200)"), ctx)
    assert not out[:199].any()


def test_htf_evaluates_in_other_timeframe(ctx, frame):
    from trader.strategy.backtest import resample
    ctx.frames["1h"] = resample(frame, "1h")
    out = evaluate_bool(parse("htf('1h', close > ema(10))"), ctx)
    assert len(out) == len(frame)
    assert out.any()


def test_htf_has_no_lookahead(ctx, frame):
    from trader.strategy.backtest import resample
    ctx.frames["1h"] = resample(frame, "1h")
    out = evaluate_bool(parse("htf('1h', close > ema(10))"), ctx)
    assert not out[0], "the first base bar precedes any closed 1h bar"


def test_htf_missing_timeframe_is_false(ctx):
    out = evaluate_bool(parse("htf('4h', close > ema(10))"), ctx)
    assert not out.any()


def test_unary_minus_literal(ctx):
    assert evaluate(parse("zscore(close, 96) < -1.0"), ctx).dtype == bool


def test_prev_enables_crossing_logic(ctx):
    cross = evaluate_bool(parse("prev(rsi(14), 1) < 30 and rsi(14) >= 30"), ctx)
    level = evaluate_bool(parse("rsi(14) >= 30"), ctx)
    assert cross.sum() < level.sum(), "a cross must be rarer than a level"
