"""The null must be charged the same costs as the strategy it is testing.

`null_pfs` rotates the ENTRY array and leaves the market untouched, so every
per-bar cost series stays aligned to its own bars. Funding was the exception:
the actual profit factor came from `vector_walk_forward`, which charges the
venue's real signed series, while every null draw fell back to the flat
`abs(funding_8h)` charge applied to both sides. A short was therefore billed
carry in the null that it was paid in the actual, so the strategy was compared
against a control that had been made artificially expensive — which inflates
every percentile the selection framework rests on.
"""
import numpy as np
import pandas as pd

from trader.strategy import null_baseline
from trader.strategy.spec import ExitSpec

RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.05,
        "slippage_atr_frac": 0.06, "funding_rate_8h": 0.001,
        "bar_minutes": 240}

GEO = ExitSpec(stop={"kind": "atr", "mult": 2.0}, target={"kind": "none"},
               trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
               time={"max_bars": 60})


def _frame(n=900):
    rng = np.random.default_rng(4)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.004, n))
    return pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
        "open": close, "high": close * 1.004, "low": close * 0.996,
        "close": close, "volume": np.full(n, 1000.0)})


def _signals(n, every=40):
    lo = np.zeros(n, dtype=bool)
    sh = np.zeros(n, dtype=bool)
    lo[np.arange(60, n - 80, every)] = True
    sh[np.arange(80, n - 80, every)] = True
    return lo, sh


def test_null_pfs_accepts_and_uses_a_funding_series():
    df = _frame()
    lo, sh = _signals(len(df))
    flat = null_baseline.null_pfs(lo, sh, df, GEO, RISK, draws=25, seed=3)
    # a strongly positive rate: longs pay it, shorts are paid it
    real = null_baseline.null_pfs(lo, sh, df, GEO, RISK, draws=25, seed=3,
                                  funding=np.full(len(df), 0.002))
    assert len(flat) == len(real) > 0
    assert flat != real, "funding was accepted and then ignored"


def test_a_short_is_not_billed_carry_the_venue_pays_it():
    """Shorts only, under a positive funding rate. The flat model charges
    abs() and the signed model credits it, so the signed run must score
    higher — the exact asymmetry that made every null too expensive."""
    df = _frame()
    n = len(df)
    lo = np.zeros(n, dtype=bool)
    _, sh = _signals(n)
    flat = null_baseline.null_pfs(lo, sh, df, GEO, RISK, draws=25, seed=3)
    real = null_baseline.null_pfs(lo, sh, df, GEO, RISK, draws=25, seed=3,
                                  funding=np.full(n, 0.002))
    assert np.median(real) > np.median(flat)


def test_nan_funding_bars_keep_the_conservative_flat_charge():
    """Missing information is not zero. An all-NaN series must reproduce the
    flat-charge result exactly, not a free ride."""
    df = _frame()
    lo, sh = _signals(len(df))
    flat = null_baseline.null_pfs(lo, sh, df, GEO, RISK, draws=25, seed=3)
    nan = null_baseline.null_pfs(lo, sh, df, GEO, RISK, draws=25, seed=3,
                                 funding=np.full(len(df), np.nan))
    assert flat == nan
