"""Vectorized-enough backtester — replays a genome bar-by-bar through its own
evaluator, simulating fills pessimistically (SL before TP when both hit
intrabar), netting taker fees both sides.

This is the gauntlet every proposed strategy must survive before paper
probation, and the tool the brain uses to test mutations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..agents.indicators import atr as _atr
from ..core.types import Action, Snapshot
from . import library as strat_lib
from .genome import Genome

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    genome_id: str
    symbol: str
    bars: int
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl_usdt: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    max_dd_pct: float = 0.0
    errors: list = field(default_factory=list)

    @property
    def profit_factor(self) -> float:
        return self.gross_win / self.gross_loss if self.gross_loss > 0 else \
            (99.0 if self.gross_win > 0 else 0.0)

    @property
    def winrate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    def passes(self, min_trades=20, min_pf=1.15, max_dd=12.0,
               min_winrate=0.40) -> tuple[bool, list[str]]:
        fails = []
        if self.trades < min_trades:
            fails.append(f"trades {self.trades}<{min_trades}")
        if self.trades and self.profit_factor < min_pf:
            fails.append(f"PF {self.profit_factor:.2f}<{min_pf}")
        if self.trades and self.winrate < min_winrate:
            fails.append(f"winrate {self.winrate:.0%}<{min_winrate:.0%}")
        if self.max_dd_pct > max_dd:
            fails.append(f"DD {self.max_dd_pct:.1f}%>{max_dd}%")
        return (not fails), fails


def _snap(df: pd.DataFrame, price: float) -> Snapshot:
    return Snapshot(symbol="BT", ts="", price=price, dfs={"15m": df},
                    market_type="futures")


def backtest(genome: Genome, df: pd.DataFrame, risk_cfg: dict,
             equity: float = 2000.0) -> BacktestResult:
    res = BacktestResult(genome_id=genome.strategy_id, symbol="BT",
                         bars=len(df))
    sl_mult = float(risk_cfg["stop_loss_atr_mult"])
    tp_mult = float(risk_cfg["take_profit_atr_mult"])
    fee = float(risk_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip_frac = float(risk_cfg.get("slippage_atr_frac", 0.06))
    risk_frac = float(risk_cfg["risk_per_trade_pct"]) / 100.0

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    warmup = 210
    step = 2                       # evaluate every 2nd bar (speed/quality tradeoff)

    pos = None                     # dict(side, entry, amount, sl, tp, entry_i)
    peak, trough = 0.0, 0.0
    equity_curve = [equity]

    def close_pos(exit_px: float, i: int):
        nonlocal pos, res, peak, trough
        direction = 1.0 if pos["side"] == "long" else -1.0
        gross = (exit_px - pos["entry"]) * direction * pos["amount"]
        fees = fee * (pos["entry"] + exit_px) * pos["amount"]
        pnl = gross - fees
        res.trades += 1
        res.pnl_usdt += pnl
        if pnl > 0:
            res.wins += 1
            res.gross_win += pnl
        else:
            res.losses += 1
            res.gross_loss += abs(pnl)
        equity_curve.append(equity_curve[-1] + pnl)
        cur = equity_curve[-1]
        peak = max(peak, cur)
        trough = min(trough, cur)
        dd = (peak - cur) / peak * 100 if peak > 0 else 0.0
        res.max_dd_pct = max(res.max_dd_pct, dd)
        pos = None

    i = warmup
    while i < n - 1:
        window = df.iloc[max(0, i - 400):i + 1]
        px = float(closes[i])

        if pos is not None:
            # ── manage open position: pessimistic SL-first ──────────────
            hit_sl = (lows[i] <= pos["sl"]) if pos["side"] == "long" \
                else (highs[i] >= pos["sl"])
            hit_tp = (highs[i] >= pos["tp"]) if pos["side"] == "long" \
                else (lows[i] <= pos["tp"])
            if hit_sl:
                close_pos(pos["sl"], i)
            elif hit_tp:
                close_pos(pos["tp"], i)
            elif i - pos["entry_i"] >= int(
                    genome.params.get("max_hold_bars", 32)):
                close_pos(px, i)
            i += 1
            continue

        try:
            sig = strat_lib.evaluate(genome, _snap(window, px))
        except Exception as e:
            res.errors.append(str(e)[:80])
            sig = None
        if sig is None or sig.action not in (Action.BUY, Action.SELL):
            i += step
            continue

        a = _atr(window)
        side = "long" if sig.action == Action.BUY else "short"
        sl_dist = max(a * sl_mult, px * 0.004)
        slip = a * slip_frac
        entry = px + (slip if side == "long" else -slip)   # pay spread
        sl = entry - sl_dist if side == "long" else entry + sl_dist
        tp = entry + a * tp_mult if side == "long" else entry - a * tp_mult
        amount = (equity_curve[-1] * risk_frac) / sl_dist
        if amount * px < 10:                                # dust guard
            i += step
            continue
        pos = {"side": side, "entry": entry, "amount": amount,
               "sl": sl, "tp": tp, "entry_i": i}
        i += 1

    if pos is not None:
        close_pos(float(closes[-1]), n - 1)
    return res


def walk_forward(genome: Genome, df: pd.DataFrame, risk_cfg: dict,
                 split: float = 0.7) -> dict:
    """Train/test discipline: fit nothing (genes fixed), but require BOTH
    halves to behave — catches luck masquerading as edge."""
    cut = int(len(df) * split)
    train = backtest(genome, df.iloc[:cut], risk_cfg)
    test = backtest(genome, df.iloc[cut:], risk_cfg)
    ok_train, f_train = train.passes()
    ok_test, f_test = test.passes(min_trades=max(3, int(train.trades * 0.2)),
                                  min_pf=1.0)
    return {"train": train, "test": test,
            "robust": ok_train and ok_test,
            "train_fails": f_train, "test_fails": f_test}
