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


def atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def adx(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean() + 1e-12
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
    return float(dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def anchored_vwap(df: pd.DataFrame, anchor_bars: int = 96) -> float:
    """VWAP over the last `anchor_bars` bars ≈ session/market cost basis."""
    w = df.tail(anchor_bars)
    tp = (w["high"] + w["low"] + w["close"]) / 3
    denom = w["volume"].sum() + 1e-12
    return float((tp * w["volume"]).sum() / denom)


def zscore(s: pd.Series, lookback: int = 96) -> float:
    w = s.tail(lookback)
    sd = float(w.std())
    if sd < 1e-12:
        return 0.0
    return float((s.iloc[-1] - w.mean()) / sd)


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
