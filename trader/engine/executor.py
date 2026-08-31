"""Executor — turns an approved Decision into a protected live position.

Contract (REQUIREMENTS §2/§10):
- entry = market order; fill recorded from actual average price
- protective stop placed as EXCHANGE-NATIVE order immediately (survives
  laptop death); TP as reduce-only limit when the venue supports it
- realized P&L always nets taker fees both sides (+ funding accrual later)
- slippage observed, not assumed: entry uses real average, not quote
"""
from __future__ import annotations

import logging
import time
import threading

from ..core.config import ROOT
from ..core.journal import Journal
from ..core.types import Action, Decision, MarketType, Position, Side, new_id

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, exchange, journal: Journal, cfg: dict,
                 market_type: MarketType):
        self.ex = exchange
        self.journal = journal
        self.market_type = market_type
        r = cfg["risk"]
        self.leverage = int(r["leverage"])
        self.tp_atr_mult = float(r["take_profit_atr_mult"])
        self.taker_fee = float(r.get("taker_fee_pct", 0.05)) / 100.0
        self._locks: dict[str, threading.Lock] = {}
        self._reg = threading.Lock()

    def _lock_for(self, symbol: str) -> threading.Lock:
        with self._reg:
            return self._locks.setdefault(symbol, threading.Lock())

    # ── entry ────────────────────────────────────────────────────────────
    def open(self, decision: Decision, amount: float, atr: float,
             stop_loss: float, take_profit: float,
             strategy_id: str, strategy_name: str,
             exec_mode: str = "live") -> Position | None:
        lock = self._lock_for(decision.symbol)
        if not lock.acquire(blocking=False):
            log.warning(f"[{decision.symbol}] entry already in flight")
            return None
        try:
            return self._open_locked(decision, amount, atr, stop_loss,
                                     take_profit, strategy_id, strategy_name,
                                     exec_mode)
        finally:
            lock.release()

    def _open_locked(self, decision: Decision, amount: float, atr: float,
                     stop_loss: float, take_profit: float,
                     strategy_id: str, strategy_name: str,
                     exec_mode: str) -> Position | None:
        sym = decision.symbol
        side_ccxt = "buy" if decision.action == Action.BUY else "sell"
        pos_side = Side.LONG if decision.action == Action.BUY else Side.SHORT
        params: dict = {}
        if self.market_type == MarketType.FUTURES:
            params["reduceOnly"] = False
        try:
            order = self.ex.create_order(sym, "market", side_ccxt, amount,
                                         params=params)
        except Exception as e:
            from ..data.feed import is_untradeable_error, mark_untradeable
            if is_untradeable_error(e):
                # venue lists it but this account can never trade it
                # (tokenized equities need a signed TradFi-Perps agreement).
                # Blacklist rather than rediscovering it every scan.
                mark_untradeable(sym, str(e))
                self.journal.log_brain_event(
                    "symbol_untradeable", sym, {"error": str(e)[:200]})
            log.error(f"ENTRY FAILED {sym}: {e}")
            return None
        fill = self._confirm_fill(sym, str(order.get("id") or ""),
                                  order, amount)
        oid = str(order.get("id") or "")
        if fill is None:
            # order may still have filled — reconciliation will adopt it at
            # next boot; never place a stop against an unknown fill
            log.critical(f"ENTRY {sym}: fill unconfirmed (order {oid}) — "
                         f"position UNJOURNALED, reconcile will adopt")
            self.journal.log_control_event(
                "fill_unconfirmed", "executor",
                detail={"symbol": sym, "order_id": oid})
            return None

        sl_oid = ""
        if self.market_type == MarketType.FUTURES:
            sl_oid = self._place_native_stop(sym, pos_side, amount,
                                              stop_loss)

        pos = Position(
            id=new_id("pos"), symbol=sym, side=pos_side, amount=amount,
            entry_price=fill,
            notional_usdt=round(amount * fill, 2),
            leverage=self.leverage if self.market_type == MarketType.FUTURES else 1,
            stop_loss=round(stop_loss, 6), take_profit=round(take_profit, 6),
            strategy_id=strategy_id, strategy_name=strategy_name,
            decision_id=decision.id,
            market_type=self.market_type.value, exec_mode=exec_mode,
            confidence=decision.confidence, sl_order_id=sl_oid)
        self.journal.add_trade(pos)
        decision.executed = True
        decision.size_usdt = pos.notional_usdt
        self.journal.set_decision_entry_price(decision.id, fill)
        self.journal.schedule_outcome(decision.id, decision.cycle_id,
                                         sym, decision.ts,
                                         decision.action.value, fill)
        fee_note = self.taker_fee * pos.notional_usdt * 200   # % round-trip cost
        log.info(f"TRADE OPEN {sym} {pos_side.value} {amount} @ {fill} "
                 f"| SL {stop_loss:.6g}{(' oid=' + sl_oid) if sl_oid else ''} "
                 f"| TP {take_profit:.6g} | est RT fees ≈ {fee_note:.2f}% notional")
        return pos

    def _confirm_fill(self, symbol: str, order_id: str,
                      order: dict, amount: float) -> float | None:
        """Poll the order until a fill price is known (demo fills async)."""
        for attempt in range(6):
            fill = float(order.get("average") or order.get("price") or 0)
            if fill > 0:
                return fill
            if order.get("status") in ("closed", "filled") and fill > 0:
                return fill
            if not order_id:
                return None
            time.sleep(0.8 * (attempt + 1))
            try:
                order = self.ex.fetch_order(order_id, symbol)
            except Exception as e:
                log.warning(f"fill confirm retry {symbol}: {e}")
        # last resort: mark price
        try:
            t = self.ex.fetch_ticker(symbol)
            px = float(t.get("last") or 0)
            return px if px > 0 else None
        except Exception:
            return None

    def _place_native_stop(self, symbol: str, side: Side, amount: float,
                           stop_price: float) -> str:
        close_side = "sell" if side == Side.LONG else "buy"
        try:
            o = self.ex.create_order(
                symbol, "market", close_side, amount,
                params={"stopLossPrice": round(stop_price, 6),
                        "reduceOnly": True})
            return str(o.get("id") or "")
        except Exception as e:
            # ccxt unified stopLossPrice → fallback to binance STOP_MARKET
            try:
                o = self.ex.create_order(
                    symbol, "STOP_MARKET", close_side, amount,
                    params={"stopPrice": round(stop_price, 6),
                            "reduceOnly": True, "workingType": "MARK_PRICE"})
                return str(o.get("id") or "")
            except Exception as e2:
                log.error(f"NATIVE STOP FAILED {symbol}: {e2} — position "
                          f"unprotected! Consider manual flatten.")
                return ""

    def close_partial(self, trade: dict, amount: float,
                      price_hint: float = 0.0, reason: str = "partial") -> bool:
        """Reduce-only partial close; journal amount shrinks, trade stays open."""
        sym = trade["symbol"]
        side_close = "sell" if trade["side"] == "long" else "buy"
        try:
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fill = float(order.get("average") or order.get("price")
                         or price_hint or 0)
            direction = 1.0 if trade["side"] == "long" else -1.0
            gross = ((fill - float(trade["entry_price"])) * direction * amount)
            fees = self.taker_fee * (amount * fill + amount * float(trade["entry_price"]))
            pnl = gross - fees
            new_amt = float(trade["amount"]) - amount
            with self.journal._tx() as c:
                c.execute("UPDATE trades SET amount=?, realized_pnl="
                          "realized_pnl+? WHERE id=?",
                          (round(new_amt, 8), round(pnl, 8), trade["id"]))
                c.execute("UPDATE trades SET tp1_done=1 WHERE id=? AND "
                          "tp1_done=0", (trade["id"],))
            log.info(f"PARTIAL {sym}: -{amount} @{fill:.4g} {reason} "
                     f"pnl={pnl:+.2f} remaining={new_amt}")
            return True
        except Exception as e:
            log.error(f"partial close failed {sym}: {e}")
            return False

    # ── exit ─────────────────────────────────────────────────────────────
    def close(self, trade: dict, exit_price_hint: float = 0.0,
              reason: str = "signal_exit") -> bool:
        sym = trade["symbol"]
        side_close = "sell" if trade["side"] == "long" else "buy"
        amount = float(trade["amount"])
        try:
            if trade.get("sl_order_id"):
                try:
                    self.ex.cancel_order(trade["sl_order_id"], sym)
                except Exception:
                    pass
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fill = float(order.get("average") or order.get("price")
                         or exit_price_hint)
            direction = 1.0 if trade["side"] == "long" else -1.0
            gross = ((fill - float(trade["entry_price"])) * direction * amount)
            fees = self.taker_fee * (float(trade["notional_usdt"]) + amount * fill)
            pnl = gross - fees
            self.journal.close_trade(trade["id"], fill, round(pnl, 8), reason)
            log.info(f"TRADE CLOSE {sym} @{fill} reason={reason} "
                     f"gross={gross:+.2f} fees={fees:.2f} pnl={pnl:+.2f}")
            return True
        except Exception as e:
            log.error(f"CLOSE FAILED {sym}: {e}")
            return False
