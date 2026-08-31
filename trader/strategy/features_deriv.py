"""Derivative and flow features.

Point-in-time alignment lives in `align()` and nowhere else. Every series
here is observed at a coarser and more irregular cadence than the base frame
— funding every 8 hours, open interest at whatever granularity was recorded,
with gaps — so the mapping onto bars is exactly where lookahead would creep
in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..agents import indicators as ind
from .features import SERIES_ARG, FeatureCtx, register


def align(obs: pd.DataFrame | None, base_ts) -> pd.Series:
    """Map an irregular observation series onto the base bar index.

    A bar closing at time t may only see observations STRICTLY BEFORE t. An
    observation stamped exactly t becomes knowable at t — i.e. at the close of
    that bar — so using it to decide an entry on that same bar is lookahead.
    `side='left'` gives that; `side='right'` would leak.

    Missing data is NaN, never a neutral default. A fabricated 0.0 funding
    rate reads as 'funding is flat' and would fire mean-reversion signals on
    data that does not exist.
    """
    idx = pd.RangeIndex(len(base_ts))
    if obs is None or len(obs) == 0:
        return pd.Series(np.nan, index=idx)
    o = obs.dropna(subset=["ts"]).sort_values("ts")
    if o.empty:
        return pd.Series(np.nan, index=idx)
    o_ts = pd.to_datetime(o["ts"], utc=True).values
    b_ts = pd.to_datetime(pd.Series(list(base_ts)), utc=True).values
    pos = np.searchsorted(o_ts, b_ts, side="left") - 1
    vals = o["value"].to_numpy(float)
    out = np.where(pos >= 0, vals[np.clip(pos, 0, None)], np.nan)
    return pd.Series(out, index=idx)


def _deriv(ctx: FeatureCtx, name: str) -> pd.Series:
    src = (ctx.derivs or {}).get(name)
    s = align(src, ctx.df["ts"])
    s.index = ctx.index
    return s


# ── funding (years of history — backtestable today) ──────────────────────
register("funding", domain=(-0.01, 0.01), requires=("funding",))(
    lambda ctx: _deriv(ctx, "funding"))


@register("funding_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("funding",))
def _funding_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "funding"), int(n))


@register("funding_cum", arg_specs=((int, 3, 500),), domain=(-0.5, 0.5),
          requires=("funding",))
def _funding_cum(ctx, n):
    """Cumulative funding paid over n bars — the running cost of holding the
    crowded side, which is what actually forces the unwind."""
    return _deriv(ctx, "funding").rolling(int(n)).sum()


# ── open interest (~30d window; recorded forward) ────────────────────────
register("oi", requires=("open_interest",))(lambda ctx: _deriv(ctx, "oi"))


@register("oi_ret", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0),
          requires=("open_interest",))
def _oi_ret(ctx, n):
    s = _deriv(ctx, "oi")
    return s / s.shift(int(n)) - 1.0


@register("oi_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("open_interest",))
def _oi_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "oi"), int(n))


# ── aggressor flow ───────────────────────────────────────────────────────
register("taker_ratio", domain=(0.0, 5.0), requires=("taker_ratio",))(
    lambda ctx: _deriv(ctx, "taker_ratio"))


@register("taker_ratio_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("taker_ratio",))
def _taker_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "taker_ratio"), int(n))


@register("taker_buy_frac", domain=(0.0, 1.0))
def _taker_buy_frac(ctx):
    """Aggressor-buy share estimated from candle position.

    Unlike `taker_ratio` this comes from the candle store's own `taker_buy`
    column (feed.py:99-104), so it has the FULL ~209 days of history rather
    than the exchange endpoint's 30-day wall. It is a proxy, not the real
    print — but it is the only flow measure that can be backtested deeply
    today.
    """
    v = ctx.df["volume"]
    tb = ctx.df.get("taker_buy")
    if tb is None:
        return pd.Series(np.nan, index=ctx.index)
    return tb / v.where(v.abs() > 1e-12)


register("ls_ratio", domain=(0.0, 5.0), requires=("ls_ratio",))(
    lambda ctx: _deriv(ctx, "ls_ratio"))


@register("ls_ratio_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("ls_ratio",))
def _ls_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "ls_ratio"), int(n))


# ── basis (years of history — backtestable today) ────────────────────────
register("basis", domain=(-0.1, 0.1), requires=("basis",))(
    lambda ctx: _deriv(ctx, "basis"))


@register("basis_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("basis",))
def _basis_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "basis"), int(n))
