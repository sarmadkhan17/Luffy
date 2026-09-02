"""ExitEngine — manages open positions to their conclusion.

Exits ladder (checked in order each cycle, per position):
  1. HARD SL/TP     — already native on-exchange; here we detect fills
  2. TP1 partial    — close tp1_fraction at tp1_r_mult × initial risk,
                      then move stop to breakeven (+fee buffer)
  3. Trailing stop  — after TP1 (or trail_after_r), trail at trail_atr_mult
                      × current ATR; ratchets, never loosens
  4. Time stop      — position older than max_hold_hours with pnl ≤ 0 →
                      recycle capital
  5. Flip exit      — orchestrator score flips hard against the position
                      (|score| ≥ flip_threshold in opposite direction)

All exchange operations are reduce-only; journal is updated atomically
with each partial close.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.journal import Journal
from ..core.types import Action, Position

log = logging.getLogger(__name__)


#: minutes per bar, so a spec's max_bars converts with ITS timeframe
_TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


@dataclass
class SpecExit:
    """The exit geometry a spec was actually validated with.

    Without this the engine ran ExitConfig's defaults against every spec:
    a 36-hour time stop on a strategy whose edge is an 83-day hold, a 2.2 ATR
    trail where the spec chose 4.0, and a 50% partial at 1.5R on a spec with
    no target at all. A backtest that validates one geometry while the engine
    runs another is not evidence about anything.
    """
    max_bars: int
    timeframe: str
    trail_atr_mult: float
    has_target: bool

    @classmethod
    def from_spec(cls, spec) -> "SpecExit":
        ex = spec.exit
        trail = ex.trail or {}
        return cls(
            max_bars=int((ex.time or {}).get("max_bars", 32)),
            timeframe=spec.timeframe,
            trail_atr_mult=float(trail.get("mult", 0.0))
            if trail.get("kind") == "atr" else 0.0,
            has_target=(ex.target or {}).get("kind", "rr") != "none")

    @property
    def max_hold_hours(self) -> float:
        return self.max_bars * _TF_MINUTES.get(self.timeframe, 15) / 60.0


@dataclass
class ExitConfig:
    tp1_fraction: float = 0.5       # fraction closed at TP1
    tp1_r_mult: float = 1.5         # TP1 at N × initial risk (stop distance)
    trail_atr_mult: float = 2.2     # trailing distance in ATR
    trail_after_r: float = 1.0      # trailing activates at this R multiple
    breakeven_buffer_pct: float = 0.0006   # cover round-trip fees
    max_hold_hours: float = 36.0    # time stop
    flip_threshold: float = 0.18    # opposite score magnitude that exits


class ExitEngine:
    def __init__(self, exchange, journal: Journal, executor,
                 cfg: dict, genomes: dict[str, dict] | None = None,
                 spec_exits: dict[str, SpecExit] | None = None):
        r = cfg["risk"]
        self.ex = exchange
        self.journal = journal
        self.executor = executor
        self.c = ExitConfig(
            tp1_fraction=float(r.get("tp1_fraction", 0.5)),
            tp1_r_mult=float(r.get("tp1_r_mult", 1.5)),
            trail_atr_mult=float(r.get("trailing_atr_mult", 2.2)),
            max_hold_hours=float(r.get("max_hold_hours", 36)),
        )
        self.genomes = genomes or {}       # strategy_id -> params (max_hold_bars etc.)
        #: strategy_id -> the geometry that spec was validated with
        self.spec_exits = spec_exits or {}

    # ── main entry, called once per cycle per open trade ────────────────
    def manage(self, trade: dict, mark: float, atr: float,
               current_score: float | None) -> str | None:
        """Returns reason string if a closing action fired, else None."""
        # journal is the source of truth — the caller's dict may be stale
        fresh = self.journal.query(
            "SELECT * FROM trades WHERE id=?", (trade["id"],))
        if not fresh or fresh[0]["status"] != "open":
            return None
        trade.update(fresh[0])
        entry = float(trade["entry_price"])
        side = trade["side"]
        direction = 1.0 if side == "long" else -1.0
        sl = float(trade.get("stop_loss") or 0)
        amount = float(trade["amount"])
        if not sl or not amount:
            return None

        initial_risk = abs(entry - sl)          # per base unit
        if initial_risk <= 0:
            return None
        r_now = (mark - entry) * direction / initial_risk    # R multiple
        opened = datetime_from(trade["opened_at"])
        hours_held = hours_since(opened)

        # ── 4. time stop ────────────────────────────────────────────────
        genome_h = self._max_hold(trade)
        max_h = genome_h if genome_h else self.c.max_hold_hours
        if hours_held >= max_h and r_now < 0.5:
            return self._close(trade, mark, f"time_stop_{max_h:.0f}h")

        # ── 5. flip exit ────────────────────────────────────────────────
        if current_score is not None:
            against = (current_score * direction) < -self.c.flip_threshold
            if against and r_now < 1.0:
                return self._close(trade, mark,
                                   f"flip_exit score={current_score:+.2f}")

        # ── 2. TP1 partial + breakeven ──────────────────────────────────
        tp1_done = bool(trade.get("tp1_done"))
        if not tp1_done and r_now >= self.c.tp1_r_mult \
                and self._takes_partial(trade):
            frac = self.c.tp1_fraction
            part = round(amount * frac, 8)
            if part > 0 and amount - part > 0:
                ok = self.executor.close_partial(trade, part, mark,
                                                 f"tp1_{self.c.tp1_r_mult}R")
                if ok:
                    be = entry + direction * \
                        entry * self.c.breakeven_buffer_pct
                    self._move_stop(trade, be)
                    log.info(f"TP1 {trade['symbol']}: closed {frac:.0%} @ {mark:.4g}, "
                             f"stop → breakeven {be:.4g}")
                    return "tp1"

        # ── 3. trailing stop (after activation R) ───────────────────────
        if tp1_done or r_now >= self.c.trail_after_r:
            trail = mark - direction * atr * self._trail_mult(trade)
            cur_sl = float(trade.get("stop_loss") or 0)
            better = trail > cur_sl if side == "long" else trail < cur_sl
            if better and abs(trail - cur_sl) > atr * 0.1:   # hysteresis
                self._move_stop(trade, trail)
                log.info(f"TRAIL {trade['symbol']}: stop → {trail:.4g} "
                         f"(R={r_now:.2f})")
        return None

    # ── helpers ─────────────────────────────────────────────────────────
    def _max_hold(self, trade: dict) -> float | None:
        sid = trade.get("strategy_id") or ""
        se = self.spec_exits.get(sid)
        if se is not None:
            return se.max_hold_hours
        g = self.genomes.get(sid)
        if g and "max_hold_bars" in g:
            # legacy genomes are 15m bars by construction
            return float(g["max_hold_bars"]) * 15 / 60
        return None

    def _trail_mult(self, trade: dict) -> float:
        se = self.spec_exits.get(trade.get("strategy_id") or "")
        if se is not None and se.trail_atr_mult > 0:
            return se.trail_atr_mult
        return self.c.trail_atr_mult

    def _takes_partial(self, trade: dict) -> bool:
        """A spec with no target keeps its whole position.

        Closing half at 1.5R on a mechanism whose edge is the fat right tail
        removes exactly the trades that pay for all the losers.
        """
        se = self.spec_exits.get(trade.get("strategy_id") or "")
        return True if se is None else se.has_target

    def _close(self, trade: dict, mark: float, reason: str) -> str:
        if self.executor.close(trade, exit_price_hint=mark, reason=reason):
            return reason
        return None

    def _move_stop(self, trade: dict, new_sl: float) -> None:
        sym, side = trade["symbol"], trade["side"]
        close_side = "sell" if side == "long" else "buy"
        amount = float(trade["amount"])
        old_oid = trade.get("sl_order_id") or ""
        # Place the replacement BEFORE cancelling the incumbent. Cancelling
        # first leaves the position naked for the width of one API round trip,
        # and if the create then fails it stays naked — the old code logged
        # "old stop may still stand" for a stop it had already cancelled.
        # This way the worst case is two reduceOnly stops of the same size,
        # and the loser is cancelled immediately below.
        try:
            o = self.ex.create_order(
                sym, "market", close_side, amount,
                params={"stopLossPrice": round(new_sl, 6), "reduceOnly": True})
            new_oid = str(o.get("id") or "")
        except Exception as e:
            log.error(f"stop move failed {sym}: {e} — keeping the existing "
                      f"stop at {trade.get('stop_loss')}")
            return
        if old_oid:
            try:
                self.ex.cancel_order(old_oid, sym)
            except Exception as e:
                # A duplicate stop is survivable; no stop is not. Reconcile
                # picks up the stale order.
                log.warning(f"stale stop {old_oid} on {sym} not cancelled: "
                            f"{e} — new stop {new_oid} is in force")
        try:
            self.journal.update_position_protection(
                trade["id"], round(new_sl, 6), trade.get("take_profit"))
            self.journal.query("UPDATE trades SET sl_order_id=? WHERE id=?",
                               (new_oid, trade["id"]))
        except Exception as e:
            log.warning(f"stop move journalling failed {sym}: {e}")
        trade["stop_loss"] = new_sl
        trade["sl_order_id"] = new_oid


def datetime_from(iso: str):
    from datetime import datetime
    return datetime.fromisoformat(iso)


def hours_since(dt) -> float:
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
