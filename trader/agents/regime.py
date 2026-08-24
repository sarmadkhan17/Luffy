"""Regime layer — decides WHICH mechanisms are currently paid.

Theory: volatility clusters (Mandelbrot) and trend persistence is
state-dependent. Momentum edges pay in trends; mean-reversion/liquidity
edges pay in ranges; both get dangerous in violent vol expansions.
This is context, not a vote: it scales other agents' convictions.
"""
from __future__ import annotations

import logging

import pandas as pd

from .indicators import adx, atr, realized_vol

log = logging.getLogger(__name__)


def classify(df_exec: pd.DataFrame, df_htf: pd.DataFrame | None) -> dict:
    """Return regime classification for one symbol."""
    if df_exec is None or len(df_exec) < 60:
        return {"regime": "UNKNOWN", "adx": 0.0, "vol_ratio": 1.0}

    adx_v = adx(df_exec)
    rv_now = realized_vol(df_exec, 24)
    rv_base = realized_vol(df_exec, 96) + 1e-9
    vol_ratio = rv_now / rv_base          # >1.4 = vol expansion in progress

    if adx_v >= 25:
        close = float(df_exec["close"].iloc[-1])
        ema50 = float(df_exec["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        drift = "TRENDING_UP" if close > ema50 else "TRENDING_DOWN"
    else:
        drift = "RANGING"

    if vol_ratio > 1.6 and adx_v < 30:
        drift = "VOLATILE"

    return {"regime": drift, "adx": round(adx_v, 1),
            "vol_ratio": round(vol_ratio, 2)}


def fit_multiplier(agent_regimes: tuple, regime: str) -> float:
    """Scale conviction by how well the agent's mechanism fits the regime."""
    if regime not in agent_regimes:
        return 0.55          # working outside its edge — still heard, discounted
    return 1.0
