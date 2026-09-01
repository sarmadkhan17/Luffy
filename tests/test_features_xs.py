"""Where this coin sits against the rest of the book."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy import dsl
from trader.strategy.features import FeatureCtx


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def _ctx(mine, others):
    uni = {"ME": {"15m": _df(mine)}}
    for name, v in others.items():
        uni[name] = {"15m": _df(v)}
    return FeatureCtx(frames={"15m": _df(mine)}, tf="15m", universe=uni)


def test_xs_rank_is_one_for_the_strongest_member():
    ctx = _ctx(np.array([1.0, 4.0]), {"A": np.array([1.0, 2.0]),
                                      "B": np.array([1.0, 3.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == 1.0


def test_xs_rank_is_zero_for_the_weakest_member():
    ctx = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 2.0]),
                                      "B": np.array([1.0, 3.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == 0.0


def test_xs_rank_is_nan_without_a_universe():
    """No peers means no cross-sectional answer — not a fabricated 0.5."""
    ctx = FeatureCtx(frames={"15m": _df(np.ones(4))}, tf="15m")
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.isna().all()


def test_breadth_is_the_fraction_of_the_book_satisfying_the_condition():
    ctx = _ctx(np.array([5.0, 5.0]), {"A": np.array([5.0, 5.0]),
                                      "B": np.array([1.0, 1.0])})
    out = dsl.evaluate(dsl.parse("breadth(close > 3)"), ctx)
    assert out.iloc[-1] == pytest.approx(2 / 3)


def test_dispersion_rises_when_members_diverge():
    tight = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 1.0])})
    wide = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 50.0])})
    t = dsl.evaluate(dsl.parse("dispersion(close)"), tight).iloc[-1]
    w = dsl.evaluate(dsl.parse("dispersion(close)"), wide).iloc[-1]
    assert w > t
