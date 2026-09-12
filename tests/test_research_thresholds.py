"""A threshold is a percentile of the quantity's OWN distribution, and a
gauge that is NaN over the slice is not a gauge at all.

Two faults this closes. Absolute constants over distributions that never
reach them: `taker_buy/volume > 0.62` fires on 0.005% of 4h bars, so the
flow family appeared in the results table having never traded. And series
that do not exist over the window: open interest begins 2025-10 while the 4h
discovery cut lands ~2025-05, so every OI gauge is NaN over the whole slice —
which must read as "not measurable here", never as a threshold of NaN.
"""
import numpy as np
import pandas as pd

from trader.research import thresholds
from trader.strategy.features import FeatureCtx


def _ctx(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ts = pd.to_datetime(np.arange(n) * 14_400_000, unit="ms", utc=True)
    df = pd.DataFrame({"ts": ts, "open": close, "high": close * 1.01,
                       "low": close * 0.99, "close": close,
                       "volume": rng.uniform(1, 2, n),
                       "taker_buy": rng.uniform(0.4, 1.2, n)})
    return FeatureCtx(frames={"4h": df}, tf="4h", symbol="BT")


def test_percentiles_come_from_the_data():
    out = thresholds.measure(["ret(24)"], [_ctx()], min_samples=100)
    m = out["ret(24)"]
    assert m["p10"] < m["p25"] < m["p75"] < m["p90"]
    assert m["usable"] is True
    assert m["finite_frac"] > 0.9


def test_a_gauge_that_is_never_finite_is_not_usable():
    out = thresholds.measure(["funding_z(360)"], [_ctx()], min_samples=100)
    m = out["funding_z(360)"]
    assert m["finite_frac"] == 0.0
    assert m["usable"] is False
    assert m["p10"] is None and m["p90"] is None


def test_a_gauge_finite_on_a_minority_of_the_slice_is_not_usable():
    ctx = _ctx()
    # a quantity defined only after a long warmup: 500 of 6000 bars
    out = thresholds.measure(["zscore(close, 5500)"], [ctx],
                             min_samples=100, min_finite_frac=0.5)
    assert out["zscore(close, 5500)"]["usable"] is False


def test_too_few_samples_is_not_a_measurement():
    out = thresholds.measure(["ret(24)"], [_ctx(n=400)], min_samples=5000)
    assert out["ret(24)"]["usable"] is False
    assert out["ret(24)"]["n"] < 5000


def test_samples_pool_across_symbols():
    a, b = _ctx(seed=1), _ctx(seed=2)
    one = thresholds.measure(["ret(24)"], [a], min_samples=100)
    two = thresholds.measure(["ret(24)"], [a, b], min_samples=100)
    assert two["ret(24)"]["n"] > one["ret(24)"]["n"]


def test_an_expression_that_will_not_parse_is_reported_not_raised():
    out = thresholds.measure(["no_such_feature(3)"], [_ctx()],
                             min_samples=100)
    assert out["no_such_feature(3)"]["usable"] is False
    assert "error" in out["no_such_feature(3)"]


def test_a_boolean_expression_still_measures():
    """breadth(close > ema(50)) evaluates its inner boolean; the OUTER value
    is a float, and a measurement of it must not crash on dtype."""
    out = thresholds.measure(["close > ema(50)"], [_ctx()], min_samples=100)
    assert out["close > ema(50)"]["n"] > 0
