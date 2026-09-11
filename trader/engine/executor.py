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
from datetime import datetime, timedelta

from ..core.config import ROOT
from ..core.journal import Journal
from ..core.types import Action, Decision, MarketType, Position, Side, new_id
from . import protective
from .reconcile import venue_realized_pnl

log = logging.getLogger(__name__)


def quantize(ex, symbol: str, amount: float) -> float:
    """Round an order size down to the venue's lot step.

    Binance truncates silently: UNI/USDT steps in whole coins, so a request
    for 607.73 fills 607. Journalling the request rather than the fill let
    the error compound across legs — the entry lost 0.73 lots, and each exit
    was then sized from a remainder the venue never held and lost 0.87 more,
    stranding 1 UNI of dust that no exit could ever clear.

    A venue without the helper (spot fakes, backtest doubles) keeps the
    amount it was given.
    """
    fn = getattr(ex, "amount_to_precision", None)
    if fn is None:
        return float(amount)
    try:
        return float(fn(symbol, amount))
    except Exception as e:
        log.warning(f"amount precision unavailable for {symbol}: {e}")
        return float(amount)


class Executor:
    def __init__(self, exchange, journal: Journal, cfg: dict,
                 market_type: MarketType):
        self.ex = exchange
        self.journal = journal
        self.market_type = market_type
        r = cfg["risk"]
        self.leverage = int(r["leverage"])
        #: symbols whose venue leverage already matches self.leverage
        self._leverage_set: set = set()
        self.tp_atr_mult = float(r["take_profit_atr_mult"])
        self.taker_fee = float(r.get("taker_fee_pct", 0.05)) / 100.0
        self._locks: dict[str, threading.Lock] = {}
        self._reg = threading.Lock()
        #: backoff between fill-history reads after an exit; tests set 0
        self.fill_retry_s = 1.0

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

    def _ensure_leverage(self, symbol: str) -> None:
        """Apply the configured leverage to the venue, once per symbol.

        risk.leverage drove the margin figure (notional / leverage) and was
        journalled on every trade, while set_leverage was never called — so
        the venue traded at whatever the account was set to and the sizer
        budgeted for something else. At 1x on the venue, every position needs
        five times the margin the risk manager believes it does.

        Cached because Binance rejects a leverage change while a position is
        open on that symbol; a rejection is expected, never a reason to skip
        the trade, and is not cached so it is retried once the symbol is flat.
        """
        if self.market_type != MarketType.FUTURES:
            return
        if symbol in self._leverage_set:
            return
        fn = getattr(self.ex, "set_leverage", None)
        if fn is None:
            return
        try:
            fn(self.leverage, symbol)
            self._leverage_set.add(symbol)
            log.info(f"leverage {self.leverage}x applied to {symbol}")
        except Exception as e:
            log.warning(f"leverage {self.leverage}x not applied to {symbol}: "
                        f"{e} — venue keeps its current setting")

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
        self._ensure_leverage(sym)
        amount = quantize(self.ex, sym, amount)
        if amount <= 0:
            log.warning(f"ENTRY SKIPPED {sym}: size rounds to zero lots")
            return None
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
        fill, filled = self._confirm_fill(sym, str(order.get("id") or ""),
                                          order, amount)
        oid = str(order.get("id") or "")
        if filled and filled > 0:
            amount = filled          # the venue's number is the only truth
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
        # 0.0 is the schema's "no target": the spec rides its trail out.
        tp_note = f"{take_profit:.6g}" if take_profit else "none (trail)"
        fee_note = self.taker_fee * 200        # round-trip cost, % of notional
        log.info(f"TRADE OPEN {sym} {pos_side.value} {amount} @ {fill} "
                 f"| SL {stop_loss:.6g}{(' oid=' + sl_oid) if sl_oid else ''} "
                 f"| TP {tp_note} | est RT fees ≈ {fee_note:.2f}% notional")
        return pos

    def _confirm_fill(self, symbol: str, order_id: str, order: dict,
                      amount: float) -> tuple[float | None, float]:
        """Poll the order until a fill is known (demo fills async).

        Returns (average price, filled quantity). The quantity matters as
        much as the price: the venue rounds the request down to its lot step
        and a journal that keeps the request instead accumulates a residue
        that later legs can never close.
        """
        for attempt in range(6):
            fill = float(order.get("average") or order.get("price") or 0)
            got = float(order.get("filled") or 0)
            if fill > 0:
                return fill, got
            if not order_id:
                return None, got
            time.sleep(0.8 * (attempt + 1))
            try:
                order = self.ex.fetch_order(order_id, symbol)
            except Exception as e:
                log.warning(f"fill confirm retry {symbol}: {e}")
        # last resort: mark price
        try:
            t = self.ex.fetch_ticker(symbol)
            px = float(t.get("last") or 0)
            return (px if px > 0 else None), float(order.get("filled") or 0)
        except Exception:
            return None, float(order.get("filled") or 0)

    def _place_native_stop(self, symbol: str, side: Side, amount: float,
                           stop_price: float) -> str:
        close_side = "sell" if side == Side.LONG else "buy"
        amount = quantize(self.ex, symbol, amount)
        try:
            return protective.place_stop(self.ex, symbol, close_side,
                                         amount, stop_price)
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

    # ── venue truth for exits ────────────────────────────────────────────
    def _order_fills(self, symbol: str, order_id: str,
                     since_ms: int) -> list[dict]:
        """This order's own fills as the venue reports them; [] if unavailable.

        A demo market order returns neither `average` nor `price`, so booking
        `order["average"] or order["price"] or hint` booked the hint: 8 of 8
        exits on 2026-09-11 were journalled better than they filled.
        """
        fetch = getattr(self.ex, "fetch_my_trades", None)
        if fetch is None or not order_id:
            return []
        for attempt in range(3):
            try:
                fills = [
                    f for f in (fetch(symbol, since=since_ms, limit=100) or [])
                    if str(f.get("order")
                           or (f.get("info") or {}).get("orderId")
                           or "") == str(order_id)]
                if fills:
                    return fills
            except Exception as e:
                log.warning(f"fill history {symbol}: {e}")
            if attempt < 2:
                time.sleep(self.fill_retry_s)
        return []

    @staticmethod
    def _vwap(fills: list[dict]) -> tuple[float, float]:
        qty = sum(float(f["amount"]) for f in fills)
        px = sum(float(f["price"]) * float(f["amount"]) for f in fills) / qty
        return px, qty

    @staticmethod
    def _fills_net(fills: list[dict]) -> float:
        """The venue's realized P&L on these fills, net of their commission."""
        return sum(float((f.get("info") or {}).get("realizedPnl") or 0)
                   - float((f.get("info") or {}).get("commission") or 0)
                   for f in fills)

    def _pnl_window_start(self, trade: dict) -> str:
        """Where this trade's fills begin, as an ISO timestamp.

        The entry fill lands seconds before `opened_at` is stamped, so reading
        from `opened_at` drops the entry commission. Look back 30s, but never
        into the previous trade on the same symbol.
        """
        start = datetime.fromisoformat(trade["opened_at"]) - timedelta(seconds=30)
        prev = self.journal.query(
            "SELECT MAX(closed_at) t FROM trades WHERE symbol=? AND id<>? "
            "AND status='closed' AND closed_at <= ?",
            (trade["symbol"], trade["id"], trade["opened_at"]))
        if prev and prev[0]["t"]:
            start = max(start, datetime.fromisoformat(prev[0]["t"])
                        + timedelta(milliseconds=1))
        return start.isoformat()

    def _book_estimate(self, trade: dict, why: str) -> None:
        log.warning(f"P&L ESTIMATED {trade['symbol']} {trade['id']}: {why}")
        self.journal.log_brain_event("pnl_estimated", trade["id"],
                                     {"symbol": trade["symbol"], "why": why})

    def close_partial(self, trade: dict, amount: float,
                      price_hint: float = 0.0, reason: str = "partial") -> bool:
        """Reduce-only partial close; journal amount shrinks, trade stays open."""
        sym = trade["symbol"]
        side_close = "sell" if trade["side"] == "long" else "buy"
        amount = quantize(self.ex, sym, amount)
        if amount <= 0:
            # a "partial" the venue would reject, or worse accept as nothing
            # while the journal booked a fill against it
            log.info(f"partial skipped {sym}: below one lot")
            return False
        try:
            sent_ms = int(time.time() * 1000) - 60_000
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fills = self._order_fills(sym, str(order.get("id") or ""), sent_ms)
            if fills:
                # this leg's own fills; the entry commission is settled when
                # the final close re-bases the trade to the venue's total
                fill, amount = self._vwap(fills)
                pnl = self._fills_net(fills)
            else:
                fill = float(order.get("average") or order.get("price")
                             or price_hint or 0)
                direction = 1.0 if trade["side"] == "long" else -1.0
                gross = ((fill - float(trade["entry_price"])) * direction * amount)
                fees = self.taker_fee * (amount * fill + amount * float(trade["entry_price"]))
                pnl = gross - fees
                self._book_estimate(trade, "venue fills unavailable at partial")
            new_amt = float(trade["amount"]) - amount
            # notional must shrink with the position. It did not, so a
            # half-closed trade still charged its FULL size against the 15%
            # heat cap — starving a book whose return comes from breadth —
            # and close() then billed entry fees on twice the size that was
            # actually left, understating the PnL of every trade that took
            # a partial.
            new_notional = round(new_amt * float(trade["entry_price"]), 2)
            with self.journal._tx() as c:
                c.execute("UPDATE trades SET amount=?, notional_usdt=?, "
                          "realized_pnl=realized_pnl+? WHERE id=?",
                          (round(new_amt, 8), new_notional,
                           round(pnl, 8), trade["id"]))
                c.execute("UPDATE trades SET tp1_done=1 WHERE id=? AND "
                          "tp1_done=0", (trade["id"],))
            trade["amount"] = round(new_amt, 8)
            trade["notional_usdt"] = new_notional
            log.info(f"PARTIAL {sym}: -{amount} @{fill:.4g} {reason} "
                     f"pnl={pnl:+.2f} remaining={new_amt} "
                     f"notional={new_notional}")
            return True
        except Exception as e:
            log.error(f"partial close failed {sym}: {e}")
            return False

    # ── exit ─────────────────────────────────────────────────────────────
    def close(self, trade: dict, exit_price_hint: float = 0.0,
              reason: str = "signal_exit") -> bool:
        sym = trade["symbol"]
        side_close = "sell" if trade["side"] == "long" else "buy"
        amount = quantize(self.ex, sym, float(trade["amount"]))
        if amount <= 0:
            log.warning(f"CLOSE {sym}: {trade['amount']} is below one lot — "
                        f"nothing the venue will accept")
            return False
        try:
            if trade.get("sl_order_id"):
                protective.cancel_stop(self.ex, trade["sl_order_id"], sym)
            sent_ms = int(time.time() * 1000) - 60_000
            order = self.ex.create_order(sym, "market", side_close, amount,
                                         params={"reduceOnly": True})
            fills = self._order_fills(sym, str(order.get("id") or ""), sent_ms)
            if fills:
                fill, amount = self._vwap(fills)
            else:
                fill = float(order.get("average") or order.get("price")
                             or exit_price_hint)
                # reduce-only fills only what the venue still holds. Sending 169
                # against a 1-coin residue closes 1, and pricing the exit over
                # the journalled 169 invents 168 coins of P&L out of rounding.
                amount = float(order.get("filled") or 0) or amount
            entry = float(trade["entry_price"])
            direction = 1.0 if trade["side"] == "long" else -1.0
            gross = ((fill - entry) * direction * amount)
            fees = self.taker_fee * (amount * entry + amount * fill)
            pnl = gross - fees
            venue = (venue_realized_pnl(self.ex, sym, self._pnl_window_start(trade))
                     if fills else None)
            if venue is not None:
                # close_trade ADDS this leg, so pass venue total minus what
                # the partials already banked: the trade then totals the venue
                banked = self.journal.query(
                    "SELECT realized_pnl FROM trades WHERE id=?", (trade["id"],))
                pnl = venue - float((banked[0]["realized_pnl"] if banked else 0) or 0)
            else:
                self._book_estimate(trade, "venue fills unavailable at close"
                                    if not fills else "venue P&L unavailable")
            self.journal.close_trade(trade["id"], fill, round(pnl, 8), reason)
            log.info(f"TRADE CLOSE {sym} @{fill} reason={reason} "
                     f"gross={gross:+.2f} fees={fees:.2f} pnl={pnl:+.2f} "
                     f"({'venue' if venue is not None else 'estimate'})")
            return True
        except Exception as e:
            log.error(f"CLOSE FAILED {sym}: {e}")
            return False
