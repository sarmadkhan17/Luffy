"""Shared indicator math — small, dependency-light, pandas-based."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / (dn + 1e-12)
    return 100 - 100 / (1 + rs)


def _true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    return pd.concat([h - l, (h - c.shift()).abs(),
                      (l - c.shift()).abs()], axis=1).max(axis=1)


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).rolling(period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> float:
    return float(atr_series(df, period).iloc[-1])


def adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l = df["high"], df["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = _true_range(df).ewm(alpha=1 / period, adjust=False).mean() + 1e-12
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> float:
    return float(adx_series(df, period).iloc[-1])


def vwap_series(df: pd.DataFrame, n: int = 96) -> pd.Series:
    """Rolling n-bar VWAP. At the last bar this equals anchored_vwap(df, n)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(n).sum()
    vol = df["volume"].rolling(n).sum()
    return pv / (vol + 1e-12)


def anchored_vwap(df: pd.DataFrame, anchor_bars: int = 96) -> float:
    """VWAP over the last `anchor_bars` bars ≈ session/market cost basis."""
    return float(vwap_series(df, anchor_bars).iloc[-1])


def zscore_series(s: pd.Series, n: int = 96,
                  fill: float | None = 0.0) -> pd.Series:
    """Rolling z-score, sample std (ddof=1).

    `fill=0.0` keeps the analyst path's 0.0-on-degenerate-window behaviour:
    an analyst scores a float and cannot carry NaN.

    `fill=None` leaves an undefined z-score UNDEFINED, which is what the
    feature registry needs. With the zero fill, funding_z / oi_z /
    taker_ratio_z / ls_ratio_z / basis_z all returned 0.0 — "exactly
    average" — for a symbol with no series at all, so a spec testing
    `funding_z(96) > -1.0` fired true on data that does not exist, and every
    frame's warmup window read as average rather than unknown.

    Missing information is NaN, never a fabricated default: a fabricated
    value reads as a real measurement.
    """
    m = s.rolling(n).mean()
    sd = s.rolling(n).std()
    z = (s - m) / sd.where(sd > 1e-12)
    z = z.replace([np.inf, -np.inf], np.nan)
    return z if fill is None else z.fillna(fill)


def zscore(s: pd.Series, lookback: int = 96) -> float:
    w = s.tail(lookback)
    if w.isna().any() or len(w.dropna()) < 10:
        return 0.0
    return float(zscore_series(s, lookback).iloc[-1])


def realized_vol_series(df: pd.DataFrame, n: int = 48) -> pd.Series:
    """Rolling std of log returns. Matches realized_vol() on a full window:
    that function computes r.std()*sqrt(len(r))/sqrt(bars), and len(r) == bars
    once warmed up. The scalar keeps its own body because agents/regime.py
    depends on its short-frame scaling."""
    r = np.log(df["close"]).diff()
    return r.rolling(n).std()


def realized_vol(df: pd.DataFrame, bars: int = 48) -> float:
    """Annualized-ish realized volatility from log returns of closes."""
    r = np.log(df["close"]).diff().tail(bars)
    return float(r.std() * np.sqrt(len(r)) / max(np.sqrt(bars), 1e-9))


def swing_highs_lows(df: pd.DataFrame, left: int = 3, right: int = 3):
    """Pivot detection for liquidity mapping."""
    h, l = df["high"].values, df["low"].values
    ph, pl = [], []
    for i in range(left, len(df) - right):
        win_h = h[i - left:i + right + 1]
        win_l = l[i - left:i + right + 1]
        if h[i] == max(win_h):
            ph.append(i)
        if l[i] == min(win_l):
            pl.append(i)
    return ph, pl
