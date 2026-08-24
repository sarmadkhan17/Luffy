"""RiskManager — the ONLY hard-gate component in Luffy (REQUIREMENTS §10).

Everything else votes or suggests; this enforces capital survival rules:
- portfolio heat cap (total open risk ≤ 15%)
- per-symbol risk cap (≤ 8%)
- max open positions (10)
- daily loss breaker (−6% blocks NEW entries until UTC midnight)
- staged de-risk on rolling drawdown (×0.5 @ −8%, ×0.25 @ −12%)
- full halt at −20% (raises → kernel flips ControlState.HALTED)
- proving period: first N live trades at ×0.5 size

Position sizing = risk-based: notional sized so that stop-distance loss
equals allowed risk, then clamped by caps and min notional.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.types import ControlState, Position, RiskError

log = logging.getLogger(__name__)


@dataclass
class SizingResult:
    ok: bool
    reason: str
    size_usdt: float          # margin/notional to deploy
    amount: float             # base units
    risk_usdt: float
    size_mult: float          # de-risk/proving multiplier applied


class RiskManager:
    def __init__(self, cfg: dict, journal):
        r = cfg["risk"]
        self.risk_pct = float(r["risk_per_trade_pct"]) / 100.0
        self.proving_trades = int(r.get("proving_period_trades", 30))
        self.proving_mult = float(r.get("proving_size_mult", 0.5))
        self.heat_cap = float(r["portfolio_heat_cap_pct"]) / 100.0
        self.symbol_cap = float(r["per_symbol_risk_cap_pct"]) / 100.0
        self.max_positions = int(r["max_open_positions"])
        self.daily_loss_block = float(r["max_daily_loss_pct"]) / 100.0
        self.derisk_steps = sorted(
            ((float(s["dd_pct"]), float(s["size_mult"]))
             for s in r.get("drawdown_derisk_steps", [])))
        self.halt_dd = float(r["halt_drawdown_pct"]) / 100.0
        self.leverage = int(r["leverage"])
        self.sl_atr_mult = float(r["stop_loss_atr_mult"])
        self.min_notional = float(r["min_notional_usdt"])
        self.taker_fee = float(r.get("taker_fee_pct", 0.05)) / 100.0

        self.journal = journal
        self._day_key: str | None = None
        self._day_start_equity: float | None = None
        self._peak_equity: float | None = None
        self._lock = threading.Lock()

    # ── equity tracking ────────────────────────────────────────────────
    def update_equity(self, equity: float) -> dict:
        """Call once per cycle with marked equity. Returns status flags."""
        with self._lock:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._day_key:
                self._day_key = today
                self._day_start_equity = equity
            if self._peak_equity is None or equity > self._peak_equity:
                self._peak_equity = equity
            dd_pct = (self._peak_equity - equity) / self._peak_equity if self._peak_equity else 0.0
            day_pnl_pct = (
                (equity - self._day_start_equity) / self._day_start_equity
                if self._day_start_equity else 0.0)
            return {
                "equity": equity,
                "drawdown_pct": round(dd_pct * 100, 2),
                "daily_pnl_pct": round(day_pnl_pct * 100, 2),
                "halt_breached": dd_pct >= self.halt_dd,
            }

    def derisk_multiplier(self, dd_pct: float) -> float:
        m = 1.0
        for step_dd, mult in self.derisk_steps:
            if dd_pct >= step_dd:
                m = mult
        return m

    # ── entry permission + sizing ───────────────────────────────────────
    def check_entry(self, state: ControlState, symbol: str, price: float,
                    atr: float, side_risk_frac: float,
                    open_positions: list[Position], equity: float,
                    closed_trades_count: int, market_type: str) -> SizingResult:
        """side_risk_frac: stop distance as fraction of price (e.g. 0.02)."""
        if state == ControlState.FROZEN:
            return SizingResult(False, "state=FROZEN: entries blocked", 0, 0, 0, 0)
        if state == ControlState.HALTED:
            return SizingResult(False, "state=HALTED", 0, 0, 0, 0)

        st = self.update_equity(equity)
        if st["halt_breached"]:
            raise RiskError(f"drawdown {st['drawdown_pct']}% ≥ halt "
                            f"{self.halt_dd*100:.0f}% — flip HALTED")
        if st["daily_pnl_pct"] <= -self.daily_loss_block * 100:
            return SizingResult(False,
                                f"daily breaker {st['daily_pnl_pct']:.1f}%", 0, 0, 0, 0)
        if len(open_positions) >= self.max_positions:
            return SizingResult(False, "max positions reached", 0, 0, 0, 0)
        if any(p.symbol == symbol for p in open_positions):
            return SizingResult(False, "already exposed here", 0, 0, 0, 0)

        # heat accounting: open risk + this trade's intended risk
        open_risk = sum(self._position_risk(p, price) for p in open_positions)
        same_symbol_risk = sum(self._position_risk(p, price) for p in open_positions
                               if p.symbol == symbol)
        budget = equity * self.risk_pct
        headroom_heat = equity * self.heat_cap - open_risk
        headroom_sym = equity * self.symbol_cap - same_symbol_risk
        allowed = min(budget, headroom_heat, headroom_sym)
        if allowed <= 0:
            return SizingResult(
                False,
                f"risk budget exhausted (heat {open_risk/equity:.1%}/"
                f"{self.heat_cap:.0%})", 0, 0, 0, 0)

        mult = self.derisk_multiplier(st["drawdown_pct"])
        if closed_trades_count < self.proving_trades:
            mult *= self.proving_mult
        allowed *= mult

        # notional such that stop-loss hit ≈ `allowed` loss
        stop_dist = max(price * side_risk_frac, price * 0.004)   # ≥0.4% floor
        amount = allowed / stop_dist
        notional = amount * price
        if notional / self.leverage < self.min_notional:
            return SizingResult(False, "below min notional", 0, 0, 0, 0)
        return SizingResult(True, "ok",
                            size_usdt=round(notional / self.leverage, 2),
                            amount=round(amount, 8),
                            risk_usdt=round(allowed, 2),
                            size_mult=mult)

    def _position_risk(self, p: Position, mark: float) -> float:
        """Open risk ≈ distance to stop × amount × leverage."""
        if p.stop_loss <= 0:
            return p.notional_usdt * 0.05     # unprotected fallback estimate
        dist = abs(p.entry_price - p.stop_loss) / p.entry_price
        return p.notional_usdt * dist

    # ── protective levels ───────────────────────────────────────────────
    def protection_levels(self, price: float, atr: float, side: str,
                          tp_mult: float) -> tuple[float, float]:
        sl_dist = max(atr * self.sl_atr_mult, price * 0.004)
        if side == "long":
            return price - sl_dist, price + atr * tp_mult
        return price + sl_dist, price - atr * tp_mult
