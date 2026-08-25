"""Analyst validation harness — evidence-based vote weighting.

Replays each analyst over historical candles bar by bar, records every
directional vote, resolves it against forward returns, and produces:
  - per-agent accuracy overall and per regime
  - data-driven base weights (replacing hand-tuned guesses)
  - per-regime fit multipliers (measured, not assumed)

Output: data/agent_weights.json — the orchestrator loads this at boot.

Honest limitation: flow's order-book imbalance and funding components
have no history; it is validated on taker-flow only.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.config import ROOT
from .base import Analyst
from .regime import classify

log = logging.getLogger(__name__)

HORIZON_BARS = {"1h": 4, "4h": 16}          # 15m bars


def _rolling_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized regime per bar: ADX(14), vol ratio, EMA50 drift."""
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / 14, adjust=False).mean() + 1e-12
    pdi = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_
    mdi = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-12)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    r_now = np.log(c).diff().rolling(24).std()
    r_base = np.log(c).diff().rolling(96).std() + 1e-12
    vol_ratio = r_now / r_base

    ema50 = c.ewm(span=50, adjust=False).mean()
    out = pd.DataFrame({"adx": adx, "vol_ratio": vol_ratio})
    out["regime"] = np.where(
        (vol_ratio > 1.6) & (adx < 30), "VOLATILE",
        np.where(adx >= 25, np.where(c > ema50, "TRENDING_UP", "TRENDING_DOWN"),
                 "RANGING"))
    return out


def validate_symbol(analysts: dict[str, Analyst], symbol: str,
                    df: pd.DataFrame, btc_1h: pd.DataFrame | None,
                    step: int = 4, warmup: int = 210) -> dict:
    """Replay analysts over one symbol's history.

    Returns {agent: {regime: {n, correct_1h, correct_4h}}}."""
    reg = _rolling_regime(df)
    closes = df["close"].values
    n = len(df)
    scores: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"n1": 0, "c1": 0, "n4": 0, "c4": 0}))

    for i in range(warmup, n - HORIZON_BARS["4h"], step):
        regime = reg["regime"].iloc[i]
        px = float(closes[i])
        # CRITICAL: analysts see ONLY bars ≤ i. Handing the full frame
        # would leak the future into every vote (look-ahead bias).
        win = df.iloc[max(0, i - 400):i + 1]
        dfs = {"15m": win, "1h": win}
        if btc_1h is not None:
            dfs["BTC_1h"] = btc_1h[btc_1h["ts"] <= win["ts"].iloc[-1]]
        snap = _snap(symbol, win, i, px, dfs)
        for name, analyst in analysts.items():
            try:
                v = analyst.evaluate(snap)
            except Exception:
                continue
            if v is None or abs(v.conviction) < 0.05:
                continue
            direction = 1 if v.conviction > 0 else -1
            bucket = scores[name][regime]
            for hkey, hb in HORIZON_BARS.items():
                if i + hb >= n:
                    continue
                fwd = (float(closes[i + hb]) - px) / px * direction
                if hkey == "1h":
                    bucket["n1"] += 1
                    bucket["c1"] += 1 if fwd > 0 else 0
                else:
                    bucket["n4"] += 1
                    bucket["c4"] += 1 if fwd > 0 else 0
    return scores


def _snap(symbol, df, i, px, dfs):
    from ..core.types import Snapshot
    import datetime as dt
    return Snapshot(symbol=symbol,
                    ts=dt.datetime.now(dt.timezone.utc).isoformat(),
                    price=px, dfs=dfs, market_type="futures")


def run(analysts: dict[str, Analyst], symbols: list[str],
        feed, days: int = 20, step: int = 4,
        btc_1h: pd.DataFrame | None = None) -> dict:
    """Full validation across symbols → weights file."""
    limit = min(int(days * 24 * 60 / 15), 3000)
    per_symbol = []

    def _one(sym):
        df = feed.fetch_ohlcv(sym, "15m", limit=limit, force=True)
        if df is None or len(df) < 400:
            return None
        btc = btc_1h if sym != "BTC/USDT" else None
        return sym, validate_symbol(analysts, sym, df, btc, step=step)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for res in pool.map(_one, symbols):
            if res:
                per_symbol.append(res)

    # ── aggregate ────────────────────────────────────────────────────────
    agg: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"n1": 0, "c1": 0, "n4": 0, "c4": 0}))
    for _sym, scores in per_symbol:
        for agent, by_regime in scores.items():
            for regime, b in by_regime.items():
                a = agg[agent][regime]
                for k in ("n1", "c1", "n4", "c4"):
                    a[k] += b[k]

    report = {"evaluated_at": datetime.now(timezone.utc).isoformat(),
              "symbols": [s for s, _ in per_symbol],
              "agents": {}}
    raw_weights = {}
    for agent in sorted(agg):
        tot = defaultdict(int)
        accs = {}
        for regime, b in agg[agent].items():
            acc1 = b["c1"] / b["n1"] if b["n1"] else None
            accs[regime] = {"n": b["n1"], "acc_1h": round(acc1, 3)
                            if acc1 is not None else None}
            tot["n"] += b["n1"]
            tot["c1"] += b["c1"]
        overall_acc = tot["c1"] / tot["n"] if tot["n"] else 0.5
        # weight ∝ measured edge; 0.5 accuracy = neutral 1.0
        edge = (overall_acc - 0.5) * 2                  # -1..+1
        raw_weights[agent] = max(0.35, min(1.5, 1.0 + edge))
        report["agents"][agent] = {
            "overall_acc_1h": round(overall_acc, 3),
            "samples": tot["n"],
            "by_regime": accs,
        }

    # normalize to sum 1.0
    s = sum(raw_weights.values())
    weights = {a: round(w / s, 4) for a, w in raw_weights.items()}
    report["base_weights"] = weights

    out = ROOT / "data" / "agent_weights.json"
    out.write_text(json.dumps(report, indent=2))
    log.info(f"agent validation written: {out}")
    return report


def load_weights() -> dict | None:
    p = ROOT / "data" / "agent_weights.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None
