"""The feature registry — the Strategist's entire vocabulary.

Novelty scales directly with what is in this file: a strategy can only
express a mechanism whose observables are registered here. That is the real
reason 409 scraped ideas produced zero strategies — the old system had eight
hardcoded evaluators and six price-derived indicators, so anything that was
not a moving average had nowhere to land.

Every feature is a pure function returning a Series aligned to the base
frame's index. `arg_specs` and `domain` are not documentation: they drive
automatic parameter-range inference for the numeric literals in a DSL
expression, which is what lets an optimizer tune a spec without a
hand-written gene schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..agents import indicators as ind

#: marks an argument that is a nested expression rather than a literal
SERIES_ARG = "series"


@dataclass(frozen=True)
class Feature:
    name: str
    fn: Callable
    arg_specs: tuple = ()      # per positional arg: (type, lo, hi) or SERIES_ARG
    domain: tuple | None = None    # output range, drives threshold inference
    requires: tuple = ("ohlcv",)


FEATURES: dict[str, Feature] = {}


def register(name: str, arg_specs: tuple = (), domain: tuple | None = None,
             requires: tuple = ("ohlcv",)):
    def deco(fn):
        FEATURES[name] = Feature(name, fn, arg_specs, domain, requires)
        return fn
    return deco


@dataclass
class FeatureCtx:
    """Everything a feature may see at one evaluation.

    `frames` maps timeframe -> DataFrame for the symbol under test. `btc`
    holds the leader's frames. `derivs` holds RAW derivative observation
    frames; alignment onto the base index happens inside the feature (see
    features_deriv.align) so the point-in-time rule lives in one place.
    """
    frames: dict
    tf: str
    btc: dict | None = None
    derivs: dict | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def df(self) -> pd.DataFrame:
        return self.frames[self.tf]

    @property
    def index(self):
        return self.df.index

    def get(self, name: str, args: tuple = ()):
        """Memoised feature evaluation. `ema(20)` typically appears in both
        the entry expression and the filters; computing it twice per bar
        across a 5-symbol gauntlet is pure waste."""
        key = (name, self.tf, args)
        if key not in self._cache:
            self._cache[key] = FEATURES[name].fn(self, *args)
        return self._cache[key]

    def scoped(self, tf: str) -> "FeatureCtx":
        """A view on a different timeframe, sharing the cache."""
        return FeatureCtx(self.frames, tf, self.btc, self.derivs, self._cache)


def _s(ctx: FeatureCtx, col: str) -> pd.Series:
    return ctx.df[col]


def _series(ctx: FeatureCtx, v) -> pd.Series:
    """Broadcast a scalar argument to the base index."""
    if isinstance(v, pd.Series):
        return v
    return pd.Series(float(v), index=ctx.index)


# ── raw price/volume ─────────────────────────────────────────────────────
for _col in ("open", "high", "low", "close", "volume"):
    register(_col)(lambda ctx, _c=_col: _s(ctx, _c))


@register("taker_buy")
def _taker_buy(ctx):
    tb = ctx.df.get("taker_buy")
    return ctx.df["volume"] * 0.5 if tb is None else tb


# ── arithmetic helpers (needed to port the legacy families faithfully) ───
def _abs(x):
    return x.abs() if isinstance(x, pd.Series) else abs(x)


def _pair(a, b, op):
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        idx = a.index if isinstance(a, pd.Series) else b.index
        return pd.Series(op(np.asarray(a, dtype=float),
                            np.asarray(b, dtype=float)), index=idx)
    return float(op(a, b))


register("abs", arg_specs=(SERIES_ARG,))(lambda ctx, x: _abs(x))
register("max", arg_specs=(SERIES_ARG, SERIES_ARG))(
    lambda ctx, a, b: _pair(a, b, np.maximum))
register("min", arg_specs=(SERIES_ARG, SERIES_ARG))(
    lambda ctx, a, b: _pair(a, b, np.minimum))


# ── indicators ───────────────────────────────────────────────────────────
register("ema", arg_specs=((int, 3, 300),))(
    lambda ctx, n: ind.ema(ctx.df["close"], int(n)))
register("sma", arg_specs=((int, 3, 300),))(
    lambda ctx, n: ctx.df["close"].rolling(int(n)).mean())
register("rsi", arg_specs=((int, 3, 50),), domain=(0.0, 100.0))(
    lambda ctx, n: ind.rsi(ctx.df["close"], int(n)))
register("atr", arg_specs=((int, 3, 100),))(
    lambda ctx, n: ind.atr_series(ctx.df, int(n)))
register("adx", arg_specs=((int, 3, 60),), domain=(0.0, 100.0))(
    lambda ctx, n: ind.adx_series(ctx.df, int(n)))
register("vwap", arg_specs=((int, 12, 400),))(
    lambda ctx, n: ind.vwap_series(ctx.df, int(n)))
register("realized_vol", arg_specs=((int, 8, 400),))(
    lambda ctx, n: ind.realized_vol_series(ctx.df, int(n)))


@register("bb_upper", arg_specs=((int, 5, 100), (float, 0.5, 4.0)))
def _bb_upper(ctx, n, k):
    c = ctx.df["close"]
    n = int(n)
    return c.rolling(n).mean() + float(k) * c.rolling(n).std()


@register("bb_lower", arg_specs=((int, 5, 100), (float, 0.5, 4.0)))
def _bb_lower(ctx, n, k):
    c = ctx.df["close"]
    n = int(n)
    return c.rolling(n).mean() - float(k) * c.rolling(n).std()


@register("bb_pctb", arg_specs=((int, 5, 100), (float, 0.5, 4.0)),
          domain=(-1.0, 2.0))
def _bb_pctb(ctx, n, k):
    lo, hi = _bb_lower(ctx, n, k), _bb_upper(ctx, n, k)
    width = hi - lo
    return (ctx.df["close"] - lo) / width.where(width.abs() > 1e-12)


@register("donchian_hi", arg_specs=((int, 5, 300),))
def _don_hi(ctx, n):
    # shift(1): the current bar's own high must not define the level it breaks
    return ctx.df["high"].rolling(int(n)).max().shift(1)


@register("donchian_lo", arg_specs=((int, 5, 300),))
def _don_lo(ctx, n):
    return ctx.df["low"].rolling(int(n)).min().shift(1)


@register("ret", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0))
def _ret(ctx, n):
    return ctx.df["close"].pct_change(int(n))


# ── transforms ───────────────────────────────────────────────────────────
@register("zscore", arg_specs=(SERIES_ARG, (int, 12, 500)), domain=(-5.0, 5.0))
def _zscore(ctx, s, n):
    return ind.zscore_series(_series(ctx, s), int(n))


@register("pct_rank", arg_specs=(SERIES_ARG, (int, 12, 500)), domain=(0.0, 1.0))
def _pct_rank(ctx, s, n):
    return _series(ctx, s).rolling(int(n)).rank(pct=True)


@register("prev", arg_specs=(SERIES_ARG, (int, 1, 50)))
def _prev(ctx, s, n):
    """The value of an expression n bars ago.

    Crossing logic needs it. Approximating a cross
    (`prev(rsi(14),1) < 30 and rsi(14) >= 30`) with a plain level test
    (`rsi(14) >= 30`) is a DIFFERENT strategy — it fires on every bar of a
    recovery instead of once, at the moment the reclaim happens.
    """
    return _series(ctx, s).shift(int(n))


@register("sma_of", arg_specs=(SERIES_ARG, (int, 2, 500)))
def _sma_of(ctx, s, n):
    return _series(ctx, s).rolling(int(n)).mean()


@register("slope", arg_specs=(SERIES_ARG, (int, 2, 200)), domain=(-0.1, 0.1))
def _slope(ctx, s, n):
    """Per-bar change over n bars, normalised by the level — unit-free, so a
    threshold means the same thing on BTC and on a sub-dollar alt."""
    x = _series(ctx, s)
    return (x - x.shift(int(n))) / (x.abs() + 1e-12) / float(n)


# ── time ─────────────────────────────────────────────────────────────────
def _ts(ctx: FeatureCtx) -> pd.Series:
    return pd.to_datetime(ctx.df["ts"], utc=True)


register("hour_utc", domain=(0.0, 23.0))(
    lambda ctx: _ts(ctx).dt.hour.astype(float))
register("dow", domain=(0.0, 6.0))(
    lambda ctx: _ts(ctx).dt.dayofweek.astype(float))

_SESSIONS = {"asia": (0, 8), "eu": (7, 16), "us": (13, 22)}


@register("is_session", arg_specs=((str, None, None),))
def _is_session(ctx, name):
    lo, hi = _SESSIONS[str(name)]
    h = _ts(ctx).dt.hour
    return ((h >= lo) & (h < hi)).astype(bool)


# ── cross-asset (leader = BTC) ───────────────────────────────────────────
def _btc(ctx: FeatureCtx):
    # NEVER `a or b` on DataFrames — the truth value is ambiguous and raises.
    # Same trap evidence.py:82 documents.
    if not ctx.btc:
        return None
    b = ctx.btc.get(ctx.tf)
    if b is None:
        b = ctx.btc.get("15m")
    return b if b is not None and len(b) else None


def _btc_aligned(ctx: FeatureCtx, col: str = "close") -> pd.Series:
    """BTC's column reindexed onto the base frame's timestamps, taking only
    bars that had CLOSED by each base bar.

    Missing leader data is NaN, never a neutral default: a fabricated 0.0
    reads as 'BTC flat' and would fire rotation signals on absent data.
    """
    b = _btc(ctx)
    if b is None or "ts" not in b.columns:
        return pd.Series(np.nan, index=ctx.index)
    base_ts = pd.to_datetime(ctx.df["ts"], utc=True).values
    b_ts = pd.to_datetime(b["ts"], utc=True).values
    pos = np.searchsorted(b_ts, base_ts, side="right") - 1
    vals = b[col].to_numpy(float)
    out = np.where(pos >= 0, vals[np.clip(pos, 0, None)], np.nan)
    return pd.Series(out, index=ctx.index)


@register("btc_ret", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0))
def _btc_ret(ctx, n):
    c = _btc_aligned(ctx)
    return c / c.shift(int(n)) - 1.0


@register("btc_ema_dist", arg_specs=((int, 5, 300),), domain=(-1.0, 1.0))
def _btc_ema_dist(ctx, n):
    c = _btc_aligned(ctx)
    e = c.ewm(span=int(n), adjust=False).mean()
    return (c - e) / (e.abs() + 1e-12)


@register("corr_btc", arg_specs=((int, 12, 500),), domain=(-1.0, 1.0))
def _corr_btc(ctx, n):
    a = ctx.df["close"].pct_change()
    b = _btc_aligned(ctx).pct_change()
    return a.rolling(int(n)).corr(b)


@register("rel_strength_btc", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0))
def _rel_strength_btc(ctx, n):
    """This symbol's n-bar return minus BTC's. Positive = outperforming."""
    n = int(n)
    mine = ctx.df["close"] / ctx.df["close"].shift(n) - 1.0
    c = _btc_aligned(ctx)
    return mine - (c / c.shift(n) - 1.0)


# ── higher timeframe ─────────────────────────────────────────────────────
# Registered as a MARKER: dsl.evaluate() special-cases `htf(tf, expr)` because
# its second argument must be evaluated in a different timeframe scope and
# then reindexed point-in-time onto the base index. The fn here exists only so
# parse() recognises the name and its arity.
register("htf", arg_specs=((str, None, None), SERIES_ARG))(
    lambda ctx, tf, expr: expr)
