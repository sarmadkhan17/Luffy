"""Vectorized backtester.

The old engine called a Python evaluator once per bar: ~1.9 ms/bar/symbol, so
a 5-symbol x 8000-bar gauntlet cost ~76 s and had to run inside a 300 s brain
tick — which is why `gauntlet_max_full_runs` is 2. Here the entry signal is a
boolean array computed once, and the simulation touches only the sparse bars
where a trade actually opens.

The fill and accounting model is deliberately identical to backtest.py:137-208
— same pessimism (stop before target when both are touched intrabar), same
fee/slippage/funding arithmetic — so any difference the equivalence harness
finds is attributable to the new exit geometry, not to an accounting change.

One intentional divergence: the old engine initialises its drawdown peak to
0.0 rather than to starting equity, so it cannot see a drawdown from the
opening balance. This one starts the peak at `equity`. It affects max_dd_pct
only, which is why the equivalence criterion compares trades/wins/pnl.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..agents.indicators import atr_series
from .backtest import BacktestResult
from .spec import ExitSpec

WARMUP = 210


def _stop_distance(exit_spec: ExitSpec, ref_px: float, atr: float,
                   df: pd.DataFrame, i: int, side: str) -> float:
    kind = exit_spec.stop.get("kind", "atr")
    if kind == "atr":
        d = atr * float(exit_spec.stop.get("mult", 2.0))
    elif kind == "pct":
        d = ref_px * float(exit_spec.stop.get("v", 0.01))
    else:                                   # swing
        n = int(exit_spec.stop.get("lookback", 20))
        w = df.iloc[max(0, i - n):i + 1]
        d = (ref_px - float(w["low"].min())) if side == "long" \
            else (float(w["high"].max()) - ref_px)
    # same floor as the old engine, computed from the same reference price
    return max(d, ref_px * 0.004)


def _target_distance(exit_spec: ExitSpec, ref_px: float, atr: float,
                     stop_dist: float) -> float | None:
    t = exit_spec.target
    kind = t.get("kind", "rr")
    if kind == "none":
        return None
    if kind == "atr":
        return atr * float(t.get("mult", 3.0))
    if kind == "rr":
        return stop_dist * float(t.get("v", 2.0))
    return ref_px * float(t.get("v", 0.02))          # pct


def simulate(long: np.ndarray, short: np.ndarray, df: pd.DataFrame,
             exit_spec: ExitSpec, risk_cfg: dict, equity: float = 2000.0,
             genome_id: str = "", symbol: str = "BT",
             exit_sig: np.ndarray | None = None) -> BacktestResult:
    res = BacktestResult(genome_id=genome_id, symbol=symbol, bars=len(df))
    fee = float(risk_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip_frac = float(risk_cfg.get("slippage_atr_frac", 0.06))
    risk_frac = float(risk_cfg["risk_per_trade_pct"]) / 100.0
    funding_8h = float(risk_cfg.get("funding_rate_8h", 0.0001))
    bar_minutes = float(risk_cfg.get("bar_minutes", 15))

    n = len(df)
    if n <= WARMUP + 1:
        return res
    closes = df["close"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    atr = atr_series(df, 14).to_numpy(float)

    max_bars = int(exit_spec.time.get("max_bars", 32) or 32)
    trail = exit_spec.trail or {"kind": "none"}
    trail_mult = float(trail.get("mult", 0.0)) \
        if trail.get("kind") == "atr" else 0.0
    arm_at_r = float(trail.get("arm_at_r", 1.0))

    equity_curve = [equity]
    peak = equity
    candidates = np.flatnonzero(long | short)
    cursor = WARMUP

    for raw_i in candidates:
        i = int(raw_i)
        if i < cursor or i >= n - 1:
            continue
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        side = "long" if long[i] else "short"
        sign = 1.0 if side == "long" else -1.0
        px = closes[i]
        slip = atr[i] * slip_frac
        entry = px + sign * slip
        # stop distance is measured from the CLOSE (as the old engine does),
        # then applied from the slipped entry
        sl_dist = _stop_distance(exit_spec, px, atr[i], df, i, side)
        tp_dist = _target_distance(exit_spec, px, atr[i], sl_dist)
        sl = entry - sign * sl_dist
        tp = entry + sign * tp_dist if tp_dist is not None else None
        amount = (equity_curve[-1] * risk_frac) / sl_dist
        if amount * px < 10:                          # dust guard
            continue

        end = min(i + max_bars, n - 1)
        exit_i, exit_px = end, closes[end]
        best = entry
        for j in range(i + 1, end + 1):
            if trail_mult:
                best = max(best, highs[j]) if side == "long" \
                    else min(best, lows[j])
                if abs(best - entry) >= arm_at_r * sl_dist:
                    trailed = best - sign * trail_mult * atr[j]
                    sl = max(sl, trailed) if side == "long" \
                        else min(sl, trailed)
            hit_sl = lows[j] <= sl if side == "long" else highs[j] >= sl
            hit_tp = tp is not None and (
                highs[j] >= tp if side == "long" else lows[j] <= tp)
            if hit_sl:                                # pessimistic: stop first
                exit_i, exit_px = j, sl
                break
            if hit_tp:
                exit_i, exit_px = j, tp
                break
            if exit_sig is not None and exit_sig[j]:
                exit_i, exit_px = j, closes[j]
                break

        exit_px = exit_px - sign * slip               # exits pay the spread too
        gross = (exit_px - entry) * sign * amount
        fees = fee * (entry + exit_px) * amount
        hours = (exit_i - i) * bar_minutes / 60.0
        funding = abs(funding_8h) * (hours / 8.0) * exit_px * amount
        pnl = gross - fees - funding

        res.trades += 1
        res.pnl_usdt += pnl
        if pnl > 0:
            res.wins += 1
            res.gross_win += pnl
        else:
            res.losses += 1
            res.gross_loss += abs(pnl)
        equity_curve.append(equity_curve[-1] + pnl)
        peak = max(peak, equity_curve[-1])
        if peak > 0:
            res.max_dd_pct = max(res.max_dd_pct,
                                 (peak - equity_curve[-1]) / peak * 100)
        cursor = exit_i + 1                           # one position at a time

    return res


def vector_backtest(compiled, frames: dict, risk_cfg: dict, btc=None,
                    derivs=None, equity: float = 2000.0,
                    symbol: str = "BT") -> BacktestResult:
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs)
    ex = compiled.exit_signal(frames, btc=btc, derivs=derivs)
    return simulate(lo, sh, frames[compiled.spec.timeframe], compiled.spec.exit,
                    risk_cfg, equity=equity, genome_id=compiled.spec.id,
                    symbol=symbol, exit_sig=ex)


def vector_walk_forward(compiled, frames: dict, risk_cfg: dict,
                        split: float = 0.7, btc=None, derivs=None,
                        symbol: str = "BT") -> dict:
    """Same contract as backtest.walk_forward so evidence.py can call either.

    Signals are computed ONCE over the full frame and then sliced, so an
    indicator near the split boundary is warmed up exactly as it would be
    live — the old engine re-ran the evaluator on a truncated frame, which
    cost the test half its first 210 bars.
    """
    tf = compiled.spec.timeframe
    df = frames[tf]
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs)
    ex = compiled.exit_signal(frames, btc=btc, derivs=derivs)
    cut = int(len(df) * split)

    def run(a, b):
        return simulate(lo[a:b], sh[a:b], df.iloc[a:b].reset_index(drop=True),
                        compiled.spec.exit, risk_cfg,
                        genome_id=compiled.spec.id, symbol=symbol,
                        exit_sig=None if ex is None else ex[a:b])

    train, test = run(0, cut), run(cut, len(df))
    ok_train, f_train = train.passes()
    ok_test, f_test = test.passes(min_trades=max(3, int(train.trades * 0.2)),
                                  min_pf=1.0)
    return {"train": train, "test": test, "robust": ok_train and ok_test,
            "train_fails": f_train, "test_fails": f_test}
