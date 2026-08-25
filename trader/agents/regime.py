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


def btc_context(df_exec: pd.DataFrame | None,
                df_htf: pd.DataFrame | None) -> dict:
    """Leader context attached to every alt snapshot.

    Alts are beta on BTC: scouts use this to boost signals aligned with
    the leader and suppress fades against an active BTC impulse.
    """
    out = {"trend": "FLAT", "ret_1h": 0.0, "ret_4h": 0.0}
    try:
        if df_exec is not None and len(df_exec) >= 17:
            c = df_exec["close"]
            ret = lambda bars: float(c.iloc[-1] / c.iloc[-bars] - 1)
            out["ret_1h"] = round(ret(4), 4)
            out["ret_4h"] = round(ret(min(16, len(c) - 1)), 4)
        if df_htf is not None and len(df_htf) >= 50:
            c = df_htf["close"]
            ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
            px = float(c.iloc[-1])
            out["trend"] = "UP" if px > ema50 * 1.001 else \
                "DOWN" if px < ema50 * 0.999 else "FLAT"
    except Exception:
        pass
    return out
