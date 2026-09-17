"""Seed strategy library — Luffy's starting playbook.

Five families, each a distinct, falsifiable inefficiency hypothesis.
Evaluators are pure: (snapshot) -> StrategySignal | None. No state.
The orchestrator treats these signals like analyst votes with receipts.

Every seed starts in PAPER probation; promotion is earned, not granted.
"""
from __future__ import annotations

import math
import logging

from dataclasses import replace

from ..agents.indicators import adx, anchored_vwap, ema, rsi, zscore, atr
from ..core.types import Action, Snapshot, StrategySignal
from .genome import Genome, spawn_seed

log = logging.getLogger(__name__)


# ── 1. EMA trend + pullback ─────────────────────────────────────────────
def eval_ema_trend(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df(g.params.get("trend_tf", "15m"))
    if df is None or len(df) < 210:
        return None
    c = df["close"]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    price, v20, v50 = float(c.iloc[-1]), float(e20.iloc[-1]), float(e50.iloc[-1])
    if adx(df) < g.params["adx_min"]:
        return None
    atr = max(abs(v20 - v50), price * 0.002)
    if price > v20 > v50:
        # long the stack on shallow pullback into EMA20
        if (v20 - price) <= g.params["pullback_atr"] * atr and price > v50:
            return StrategySignal(
                g.strategy_id, "ema_trend", snap.symbol, Action.BUY,
                confidence=min(0.6 + 0.02 * min(len(snap.dfs), 3), 0.85),
                rationale=f"bull stack p>v20>v50, ADX ok, pullback {((v20-price)/atr):.1f}·ATR",
                params=dict(g.params))
    elif price < v20 < v50:
        if (price - v20) <= g.params["pullback_atr"] * atr and price < v50:
            return StrategySignal(
                g.strategy_id, "ema_trend", snap.symbol, Action.SELL,
                confidence=0.62,
                rationale=f"bear stack p<v20<v50, ADX ok, pullback {((price-v20)/atr):.1f}·ATR",
                params=dict(g.params))
    return None


# ── 2. VWAP extension fade ──────────────────────────────────────────────
def eval_vwap_fade(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df("15m")
    if df is None or len(df) < g.params["anchor_bars"] + 10:
        return None
    anchor = int(g.params["anchor_bars"])
    vw = anchored_vwap(df, anchor)
    dev = (df["close"] - vw) / vw                       # %-distance series
    z = zscore(dev, anchor)
    if abs(z) < g.params["z_entry"]:
        return None
    side = Action.SELL if z > 0 else Action.BUY
    conf = min(0.35 + 0.12 * (abs(z) - g.params["z_entry"]), 0.75)
    return StrategySignal(
        g.strategy_id, "vwap_fade", snap.symbol, side, confidence=conf,
        rationale=f"VWAP deviation z={z:+.2f} (≥{g.params['z_entry']}) — "
                  f"fading liquidity-provider extreme",
        params=dict(g.params))


# ── 3. Breakout → retest continuation ───────────────────────────────────
def eval_breakout_retest(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df("15m")
    n = int(g.params["range_lookback"])
    if df is None or len(df) < n + 30:
        return None
    from ..agents.indicators import atr as _atr
    a = _atr(df)
    win = df.iloc[-n - 5:-2]                            # range BEFORE breakout bar
    r_hi, r_lo = float(win["high"].max()), float(win["low"].min())
    last, prev = df.iloc[-1], df.iloc[-2]
    broke_up = float(prev["close"]) > r_hi
    broke_dn = float(prev["close"]) < r_lo
    vol_ok = float(df["volume"].iloc[-3:].mean()) > \
        g.params["vol_mult"] * float(df["volume"].iloc[:-(n)].tail(48).mean() or 1)
    if not (broke_up or broke_dn):
        return None
    tol = g.params["retest_atr"] * a
    if broke_up:
        near = abs(float(last["low"]) - r_hi) <= tol or float(last["close"]) >= r_hi
        if near:
            return StrategySignal(
                g.strategy_id, "breakout_retest", snap.symbol, Action.BUY,
                confidence=0.66 if vol_ok else 0.5,
                rationale=f"broke range high {r_hi:.4g}"
                          f"{', vol-confirmed' if vol_ok else ', weak volume'}, holding retest",
                params=dict(g.params))
    else:
        near = abs(float(last["high"]) - r_lo) <= tol or float(last["close"]) <= r_lo
        if near:
            return StrategySignal(
                g.strategy_id, "breakout_retest", snap.symbol, Action.SELL,
                confidence=0.66 if vol_ok else 0.5,
                rationale=f"broke range low {r_lo:.4g}"
                          f"{', vol-confirmed' if vol_ok else ', weak volume'}, holding retest",
                params=dict(g.params))
    return None


# ── 4. Liquidity sweep reversal ──────────────────────────────────────────
def eval_sweep_reversal(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df("15m")
    if df is None or len(df) < 80:
        return None
    k = int(g.params["max_reclaim_bars"])
    body = df.iloc[:-k - 1:-1]                          # last k+1 bars, newest first
    prior = df.iloc[-60:-k - 1]
    swing_lo, swing_hi = float(prior["low"].min()), float(prior["high"].max())
    lo_pierce = float(body["low"].min()) < swing_lo * (1 - g.params["min_sweep_frac"])
    hi_pierce = float(body["high"].max()) > swing_hi * (1 + g.params["min_sweep_frac"])
    close_now = float(df["close"].iloc[-1])
    if lo_pierce and close_now > swing_lo:
        depth = (swing_lo - float(body["low"].min())) / swing_lo
        return StrategySignal(
            g.strategy_id, "sweep_reversal", snap.symbol, Action.BUY,
            confidence=min(0.55 + 40 * depth, 0.8),
            rationale=f"spring: pierced low {swing_lo:.4g}, reclaimed in ≤{k} bars "
                      f"(depth {depth:.3%})",
            params=dict(g.params))
    if hi_pierce and close_now < swing_hi:
        depth = (float(body["high"].max()) - swing_hi) / swing_hi
        return StrategySignal(
            g.strategy_id, "sweep_reversal", snap.symbol, Action.SELL,
            confidence=min(0.55 + 40 * depth, 0.8),
            rationale=f"upthrust: pierced high {swing_hi:.4g}, rejected in ≤{k} bars "
                      f"(depth {depth:.3%})",
            params=dict(g.params))
    return None


# ── 5. BTC rotation momentum ────────────────────────────────────────────
def eval_rotation_momo(g: Genome, snap: Snapshot) -> StrategySignal | None:
    btc = snap.dfs.get("BTC_1h")
    me = snap.df("1h")
    if btc is None or me is None or len(btc) < 24 or len(me) < 24:
        return None
    btc_ret = float(btc["close"].iloc[-1] / btc["close"].iloc[-2] - 1)
    if btc_ret < g.params["btc_ret_1h_min"]:
        return None
    lag_n = int(g.params["lag_lookback"])
    my_ret = float(me["close"].iloc[-1] / me["close"].iloc[-lag_n] - 1)
    btc_lag = float(btc["close"].iloc[-1] / btc["close"].iloc[-lag_n] - 1)
    if my_ret > 0 and my_ret < btc_lag * 0.7:           # positive but lagging BTC
        return StrategySignal(
            g.strategy_id, "rotation_momo", snap.symbol, Action.BUY,
            confidence=min(0.4 + 25 * (btc_ret - g.params["btc_ret_1h_min"]), 0.72),
            rationale=f"BTC 1h {btc_ret:+.2%}; {snap.symbol} up but lagging "
                      f"({my_ret:+.2%} vs {btc_lag:+.2%}) — catch-up bid expected",
            params=dict(g.params))
    return None


# ── 6. RSI extreme reclaim (fade momentum exhaustion) ───────────────────
def eval_rsi_extreme(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df("15m")
    if df is None or len(df) < int(g.params["rsi_len"]) + 30:
        return None
    r = rsi(df["close"], int(g.params["rsi_len"]))
    prev, now = float(r.iloc[-2]), float(r.iloc[-1])
    os_l, ob_l = g.params["os_level"], g.params["ob_level"]
    if prev < os_l <= now:                 # crossed up out of oversold
        return StrategySignal(
            g.strategy_id, "rsi_extreme", snap.symbol, Action.BUY,
            confidence=min(0.5 + (os_l - prev) / max(os_l, 1) * 0.3, 0.75),
            rationale=f"RSI({g.params['rsi_len']:.0f}) reclaimed {os_l:.0f} "
                      f"from {prev:.0f} — oversold exhaustion",
            params=dict(g.params))
    if prev > ob_l >= now:                 # crossed down out of overbought
        return StrategySignal(
            g.strategy_id, "rsi_extreme", snap.symbol, Action.SELL,
            confidence=min(0.5 + (prev - ob_l) / max(100 - ob_l, 1) * 0.3,
                           0.75),
            rationale=f"RSI({g.params['rsi_len']:.0f}) fell through {ob_l:.0f} "
                      f"from {prev:.0f} — overbought exhaustion",
            params=dict(g.params))
    return None


# ── 7. Moving-average crossover ─────────────────────────────────────────
def eval_ma_cross(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df("15m")
    fl, sl = int(g.params["fast_len"]), int(g.params["slow_len"])
    if df is None or len(df) < sl + 60 or fl >= sl:
        return None
    c = df["close"]
    spread = ema(c, fl) - ema(c, sl)
    now, p1, p2 = float(spread.iloc[-1]), float(spread.iloc[-2]), \
        float(spread.iloc[-3])
    from ..agents.indicators import atr as _atr
    a = _atr(df)
    sep = abs(now) / max(a, 1e-9)
    if p2 <= 0 < p1 and now > 0:           # fresh bullish cross ≤2 bars old
        return StrategySignal(
            g.strategy_id, "ma_cross", snap.symbol, Action.BUY,
            confidence=min(0.45 + sep * 0.12, 0.72),
            rationale=f"EMA{fl} crossed above EMA{sl} ({sep:.2f}·ATR apart)",
            params=dict(g.params))
    if p2 >= 0 > p1 and now < 0:
        return StrategySignal(
            g.strategy_id, "ma_cross", snap.symbol, Action.SELL,
            confidence=min(0.45 + sep * 0.12, 0.72),
            rationale=f"EMA{fl} crossed below EMA{sl} ({sep:.2f}·ATR apart)",
            params=dict(g.params))
    return None


# ── 8. Bollinger band pierce fade ───────────────────────────────────────
def eval_bb_fade(g: Genome, snap: Snapshot) -> StrategySignal | None:
    df = snap.df("15m")
    n, k = int(g.params["bb_len"]), float(g.params["bb_k"])
    if df is None or len(df) < n + 30:
        return None
    c = df["close"]
    mid = c.rolling(n).mean()
    sd = c.rolling(n).std()
    upper = mid + k * sd
    lower = mid - k * sd
    prev, now = df.iloc[-2], df.iloc[-1]
    if float(prev["close"]) > float(upper.iloc[-2]) and \
            float(now["close"]) < float(upper.iloc[-1]):
        z = (float(prev["close"]) - float(mid.iloc[-2])) / \
            max(float(sd.iloc[-2]) * k, 1e-9)
        return StrategySignal(
            g.strategy_id, "bb_fade", snap.symbol, Action.SELL,
            confidence=min(0.45 + 0.12 * (z - 1), 0.72),
            rationale=f"pierced upper BB({n},{k}) at {z:.1f}×band, "
                      f"closed back inside — fading overextension",
            params=dict(g.params))
    if float(prev["close"]) < float(lower.iloc[-2]) and \
            float(now["close"]) > float(lower.iloc[-1]):
        z = (float(mid.iloc[-2]) - float(prev["close"])) / \
            max(float(sd.iloc[-2]) * k, 1e-9)
        return StrategySignal(
            g.strategy_id, "bb_fade", snap.symbol, Action.BUY,
            confidence=min(0.45 + 0.12 * (z - 1), 0.72),
            rationale=f"pierced lower BB({n},{k}) at {z:.1f}×band, "
                      f"closed back inside — fading capitulation",
            params=dict(g.params))
    return None


EVALUATORS = {
    "ema_trend": eval_ema_trend,
    "vwap_fade": eval_vwap_fade,
    "breakout_retest": eval_breakout_retest,
    "sweep_reversal": eval_sweep_reversal,
    "rotation_momo": eval_rotation_momo,
    "rsi_extreme": eval_rsi_extreme,
    "ma_cross": eval_ma_cross,
    "bb_fade": eval_bb_fade,
}

_ALLOWED_BUILTINS = [
    "abs", "len", "min", "max", "range", "int", "float", "str", "bool",
    "round", "zip", "enumerate", "isinstance", "list", "dict", "tuple",
    "any", "all", "sum", "sorted", "reversed", "print",
]

# Restricted namespace for LLM-generated evaluator code.
# Provides indicators + types; blocks os/sys/network/file access.
SAFE_NS: dict = {
    "__builtins__": {k: __builtins__[k] for k in _ALLOWED_BUILTINS
                     if k in __builtins__},
    "math": math,
    "Action": Action,
    "StrategySignal": StrategySignal,
    "ema": ema,
    "rsi": rsi,
    "adx": adx,
    "atr": atr,
    "anchored_vwap": anchored_vwap,
    "zscore": zscore,
}


def register_evaluator(family: str, fn) -> None:
    """Register a runtime-invented evaluator into the dispatch table."""
    EVALUATORS[family] = fn


def evaluate(genome: Genome, snap: Snapshot, diagnostic=None) -> StrategySignal | None:
    if diagnostic is not None:
        callback = diagnostic
        def diagnostic(reason, exc=None):
            try:
                callback(reason, exc)
            except Exception as error:
                log.warning("strategy diagnostic failed: %s", type(error).__name__)
    fn = EVALUATORS.get(genome.family)
    if not fn:
        if diagnostic:
            diagnostic("missing_evaluator")
        return None
    try:
        # mined genomes may arrive with missing params — fill from the
        # family defaults so evaluators can index g.params safely
        from .genome import PARAM_DEFAULTS
        defaults = PARAM_DEFAULTS.get(genome.family, {})
        if any(k not in genome.params for k in defaults):
            merged = {**defaults, **genome.params}
            genome = replace(genome, params=merged)
        reported = False
        def report(reason, exc=None):
            nonlocal reported
            reported = True
            diagnostic(reason, exc)
        if diagnostic and getattr(fn, "_diagnostic_capable", False):
            result = fn(genome, snap, diagnostic=report)
        else:
            result = fn(genome, snap)
    except Exception as e:
        if diagnostic:
            diagnostic("evaluation_failed", e)
        log.warning(f"strategy {genome.strategy_id} ({genome.family}) error: {e}")
        return None
    if diagnostic and not reported:
        diagnostic("emitted_signal" if result is not None else "returned_none")
    return result


# ── The seeds themselves ────────────────────────────────────────────────
def build_seed_population(market_type: str = "futures") -> list[tuple]:
    """Returns [(Strategy, Genome)] — Luffy's founding playbook."""
    mk = {"futures"} if market_type == "futures" else {"spot"}
    seeds = [
        spawn_seed(
            "ematrend", "ema_trend", "EMA Stack Pullback",
            "Trade aligned EMA stacks, enter on shallow pullbacks.",
            hypothesis="Trends persist short-term due to anchoring and delayed "
                       "discretionary entry; buying shallow pullbacks within an "
                       "aligned EMA stack captures continuation at reduced risk.",
            invalidation="Demote after 6 consecutive losses or PF<0.8 over 20 trades.",
            regimes={"TRENDING_UP", "TRENDING_DOWN"}, markets=mk,
            adx_min=22.0, pullback_atr=0.8),
        spawn_seed(
            "vwapfade", "vwap_fade", "VWAP Extreme Fade",
            "Fade statistically extreme deviations from anchored VWAP.",
            hypothesis="Without a trend regime, extensions beyond ~2.5σ from the "
                       "anchored VWAP revert as liquidity providers are paid the "
                       "panic premium of overreaction traders (De Bondt–Thaler).",
            invalidation="Demote if winrate<40% over 15 trades or PF<1.0 over 20.",
            regimes={"RANGING"}, markets=mk,
            z_entry=2.5, anchor_bars=96),
        spawn_seed(
            "bkrt", "breakout_retest", "Breakout-Retest",
            "Buy/sell confirmed range breaks on the first retest hold.",
            hypothesis="Range breakouts with volume confirmation trap the other "
                       "side; the retest that holds converts trapped traders into "
                       "fuel, paying continuation to the breakout direction.",
            invalidation="Demote after PF<0.9 over 20 trades or DD>8%.",
            regimes={"TRENDING_UP", "TRENDING_DOWN", "RANGING"}, markets=mk,
            range_lookback=48, vol_mult=1.4, retest_atr=0.5),
        spawn_seed(
            "sweeprev", "sweep_reversal", "Liquidity Sweep Reversal",
            "Trade stop-hunt reversals when swept levels reclaim quickly.",
            hypothesis="Resting stops beyond obvious swings are liquidity; their "
                       "harvest by larger players marks exhaustion, and fast "
                       "reclaim of the level signals the real direction (Wyckoff spring).",
            invalidation="Demote after 6 consecutive losses or PF<0.85/20 trades.",
            regimes={"RANGING", "VOLATILE"}, markets=mk,
            max_reclaim_bars=3, min_sweep_frac=0.002),
        spawn_seed(
            "rotmomo", "rotation_momo", "BTC Rotation Momentum",
            "Ride BTC-led catch-up flows into lagging majors/alts.",
            hypothesis="Crypto capital rotates BTC→ETH→LCaps→SCaps; alts that are "
                       "positive yet lagging a fresh BTC impulse get chased by "
                       "late rotation buyers within hours.",
            invalidation="Demote if PF<1.0 over 20 trades or BTC-correlation collapses.",
            regimes={"TRENDING_UP"}, markets={"futures"},
            btc_ret_1h_min=0.008, lag_lookback=4),
    ]
    return seeds
