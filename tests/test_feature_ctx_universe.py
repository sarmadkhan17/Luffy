"""The context can see the rest of the book, not just this symbol."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FeatureCtx


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def test_ctx_carries_a_universe_and_market():
    uni = {"ETH/USDT": {"15m": _df(np.arange(1.0, 6.0))}}
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5))}, tf="15m", universe=uni)
    assert "ETH/USDT" in ctx.universe
    assert ctx.market is None


def test_for_symbol_returns_a_context_scoped_to_another_member():
    uni = {"ETH/USDT": {"15m": _df(np.arange(1.0, 6.0))}}
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5))}, tf="15m", universe=uni)
    sub = ctx.for_symbol("ETH/USDT")
    assert sub is not None
    assert float(sub.df["close"].iloc[-1]) == 5.0
    assert sub.tf == ctx.tf


def test_for_symbol_returns_none_when_the_member_is_absent():
    """Absent is None, so callers produce NaN rather than a fabricated value."""
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5))}, tf="15m", universe={})
    assert ctx.for_symbol("SOL/USDT") is None


def test_scoped_preserves_universe_and_market():
    uni = {"ETH/USDT": {"15m": _df(np.ones(5)), "1h": _df(np.ones(5))}}
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5)), "1h": _df(np.ones(5))},
                     tf="15m", universe=uni, market={"btc_dominance": _df(np.ones(5))})
    assert ctx.scoped("1h").universe is ctx.universe
    assert ctx.scoped("1h").market is ctx.market
