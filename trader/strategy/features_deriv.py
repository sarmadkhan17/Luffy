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

    Retained observations require availability AND receipt <= the explicit
    decision cut, with event ancestry and registered staleness checked.
    Unqualified numerical fixtures retain their conservative strict-event
    alignment; production/historical loaders supply retained receipts.

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
    if 'available_at_ms' in o:
        # Select the newest EVENT, then its exact revision at each cut. A
        # late correction of an older event must not displace a newer event.
        times = pd.to_datetime(pd.Series(list(base_ts)), utc=True).to_numpy(dtype='datetime64[ms]').astype('int64')
        rows = o.sort_values(['available_at_ms','observed_at_ms']).to_dict('records')
        out = np.full(len(times),np.nan)
        by_event = {}
        cursor = 0
        for index in np.argsort(times,kind='stable'):
            at = int(times[index])
            while cursor<len(rows) and rows[cursor]['available_at_ms'] <= at:
                row=rows[cursor];cursor+=1
                if row['observed_at_ms'] <= at and row['event_time_ms'] <= at:
                    by_event[row['event_time_ms']]=row
            if by_event:
                newest=by_event[max(by_event)]
                age_limit = newest.get('max_age_ms')
                fresh = age_limit is None or pd.isna(age_limit) or at-newest['event_time_ms'] <= age_limit
                if newest['quality']=='VALID' and fresh:
                    out[index]=newest['value']
        return pd.Series(out,index=idx)
    o_ts = pd.to_datetime(o['ts'],utc=True).values
    b_ts = pd.to_datetime(pd.Series(list(base_ts)),utc=True).values
    pos = np.searchsorted(o_ts,b_ts,side='left')-1
    vals=o['value'].to_numpy(float)
    return pd.Series(np.where(pos>=0,vals[np.clip(pos,0,None)],np.nan),index=idx)



def _deriv(ctx: FeatureCtx, name: str) -> pd.Series:
    src = (ctx.derivs or {}).get(name)
    cuts = ctx.df['ts']
    if src is not None and 'available_at_ms' in src:
        from ..core.types import TF_MS
        from ..data.market_provenance import ms
        points = ms(ctx.df['ts'])+TF_MS[ctx.tf]
        if ctx.as_of_ms is not None:
            points = np.minimum(points,ctx.as_of_ms)
            if ctx.df.attrs.get('read_mode')!='replay' and len(points):
                points[-1]=ctx.as_of_ms
        cuts=pd.to_datetime(points,unit='ms',utc=True)
    s = align(src,cuts)
    s.index = ctx.index
    return s


# ── funding (years of history — backtestable today) ──────────────────────
register("funding", domain=(-0.01, 0.01), requires=("funding",))(
    lambda ctx: _deriv(ctx, "funding"))


@register("funding_z", arg_specs=((int, 64, 4000),), domain=(-4.0, 4.0),
          requires=("funding",))
def _funding_z(ctx, n):
    """Window is in BARS, not funding periods. Funding settles every 8h =
    32 bars on a 15m frame, so n=96 spans only 3 observations; a meaningful
    z-score needs n in the low thousands."""
    return ind.zscore_series(_deriv(ctx, "funding"), int(n), fill=None)


@register("funding_cum", arg_specs=((int, 32, 4000),), domain=(-0.5, 0.5),
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


@register("oi_z", arg_specs=((int, 12, 2000),), domain=(-4.0, 4.0),
          requires=("open_interest",))
def _oi_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "oi"), int(n), fill=None)


# ── aggressor flow ───────────────────────────────────────────────────────
register("taker_ratio", domain=(0.0, 5.0), requires=("taker_ratio",))(
    lambda ctx: _deriv(ctx, "taker_ratio"))


@register("taker_ratio_z", arg_specs=((int, 12, 2000),), domain=(-4.0, 4.0),
          requires=("taker_ratio",))
def _taker_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "taker_ratio"), int(n), fill=None)


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


@register("ls_ratio_z", arg_specs=((int, 12, 2000),), domain=(-4.0, 4.0),
          requires=("ls_ratio",))
def _ls_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "ls_ratio"), int(n), fill=None)


# `ls_ratio` above is Binance's topLongShortPositionRatio — how much notional
# the top traders hold each way, ~33 days deep. The two below are
# globalLongShortAccountRatio: how many ACCOUNTS lean each way, 334 days deep
# via Coinalyze. Measured corr between them is -0.64, so they answer different
# questions and a mechanism must name the one it means.
register("ls_account_ratio", domain=(0.0, 5.0), requires=("ls_account_ratio",))(
    lambda ctx: _deriv(ctx, "ls_account_ratio"))


@register("ls_account_ratio_z", arg_specs=((int, 12, 2000),),
          domain=(-4.0, 4.0), requires=("ls_account_ratio",))
def _ls_acct_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "ls_account_ratio"), int(n),
                             fill=None)


# ── basis (years of history — backtestable today) ────────────────────────
register("basis", domain=(-0.1, 0.1), requires=("basis",))(
    lambda ctx: _deriv(ctx, "basis"))


@register("basis_z", arg_specs=((int, 12, 2000),), domain=(-4.0, 4.0),
          requires=("basis",))
def _basis_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "basis"), int(n), fill=None)


@register("funding_pct", arg_specs=((int, 24, 2000),), domain=(0.0, 1.0),
          requires=("funding",))
def _funding_pct(ctx, n):
    """Where funding sits in its own recent distribution. A percentile
    survives regime shifts in the level that a z-score does not."""
    return _deriv(ctx, "funding").rolling(int(n)).rank(pct=True)


@register("basis_slope", arg_specs=((int, 6, 500),), domain=(-1.0, 1.0),
          requires=("basis",))
def _basis_slope(ctx, n):
    b = _deriv(ctx, "basis")
    return b - b.shift(int(n))


@register("oi_price_div", arg_specs=((int, 6, 500),), domain=(-10.0, 10.0),
          requires=("open_interest",))
def _oi_price_div(ctx, n):
    """Open interest building against the price move — positioning growing
    into a decline is a different animal from one growing into a rally.
    Domain is a search range for the optimizer, not a claim about data bounds."""
    n = int(n)
    oi = _deriv(ctx, "oi")
    oi_shifted = oi.shift(n)
    oi_ret = (oi / oi_shifted - 1.0).mask(oi_shifted == 0)
    px_ret = ctx.df["close"] / ctx.df["close"].shift(n) - 1.0
    return oi_ret - px_ret
