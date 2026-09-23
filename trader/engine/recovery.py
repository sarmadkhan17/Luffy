"""Durable futures entry recovery. Unknown venue state never permits entries.

One outstanding intent, serialized by Executor's entry lock. Never resubmits an
ambiguous entry. Recovery is bounded to one order/position/protection pass per
cycle; it uses no LLM and never changes operator control state.
"""
from __future__ import annotations

import json
import hashlib
import math
import time
from pathlib import Path
from uuid import uuid4
from ccxt import InvalidOrder, InsufficientFunds

from ..core.types import Position, Side, norm_symbol
from . import protective

KEY = "execution_recovery"
TERMINAL = {"closed", "canceled", "expired", "rejected"}


def read(journal):
    value = json.loads(journal.kv_get(KEY, "null"))
    if value is not None and (not isinstance(value, dict) or not value.get("symbol")):
        raise ValueError("invalid recovery record")
    return value


class EntryRecovery:
    def __init__(self, executor):
        self.executor = executor
        self.journal = executor.journal
        self.ex = executor.ex
        self.code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def pending(self):
        # Read failures propagate: callers cannot enter with an unreadable ledger.
        return read(self.journal)

    def save(self, intent, reason):
        intent.update(reason=reason, updated_ms=int(time.time()*1000))
        self.journal.kv_set(KEY, json.dumps(intent, allow_nan=False))
        self.journal.log_control_event("execution_recovery", "executor", detail={
            "intent_id": intent["id"], "decision_id": intent["position"]["decision_id"],
            "symbol": intent["symbol"], "reason": reason,
            "phase": intent["phase"], "order_id": intent.get("order_id"),
            "close_order_id": intent.get("close_order_id"),
            "client_order_id": intent["client_order_id"],
            "close_client_order_id": intent.get("close_client_order_id"),
            "created_ms": intent["created_ms"], "updated_ms": intent["updated_ms"],
            "schema_version": intent.get("schema_version"), "code_hash": intent.get("code_hash"),
            "position_intent": intent["position"],
            "entry_observation": intent.get("entry_observation"),
            "close_observation": intent.get("close_observation"),
            "accounting": "recovery observations; not realized P&L",
            "attempts": intent.get("attempts", 0)})

    def finish(self, intent, reason):
        # Audit before release; an audit failure must retain the entry barrier.
        self.save(intent, reason)
        # Archive before release, atomically. Accounting failures cannot erase
        # the intent; no network accounting call delays the safety path.
        with self.journal._tx() as db:
            if reason == "terminal_orders_and_flat_venue":
                from .accounting import archive_flat
                archive_flat(db, intent)
            db.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                       (KEY, "null"))

    def begin(self, position):
        intent = {"id": uuid4().hex, "symbol": position.symbol,
                  "schema_version": 1, "code_hash": self.code_hash,
                  "position": position.as_dict(), "phase": "entry",
                  "client_order_id": "lr_"+uuid4().hex,
                  "created_ms": int(time.time()*1000)}
        self.save(intent, "entry_intent_persisted")
        return intent

    def order(self, intent, close=False):
        prefix = "close_" if close else ""
        oid = intent.get(prefix+"order_id")
        if oid:
            return self.ex.fetch_order(oid, intent["symbol"])
        return self.ex.fetch_order(None, intent["symbol"],
                                  {"origClientOrderId": intent[prefix+"client_order_id"]})

    def submit_close(self, intent, amount):
        # Persist BEFORE the call: timeout or crash must not cause a blind retry.
        intent.update(phase="closing", close_client_order_id="lr_"+uuid4().hex)
        intent.pop("close_order_id", None)
        self.save(intent, "emergency_close_intent_persisted")
        side = "sell" if intent["position"]["side"] == "long" else "buy"
        try:
            order = self.ex.create_order(intent["symbol"], "market", side, amount,
                                        params={"reduceOnly": True,
                                                "newClientOrderId": intent["close_client_order_id"]})
        except (InvalidOrder, InsufficientFunds):
            intent.update(phase="entry", force_close=True)
            self.save(intent, "emergency_close_rejected_retry_pending")
            return
        intent["close_order_id"] = str(order.get("id") or "")
        self.save(intent, "emergency_close_submitted_unconfirmed")

    def tick(self):
        intent = self.pending()
        if not intent:
            return
        intent["attempts"] = intent.get("attempts", 0)+1
        try:
            self._tick(intent)
        except Exception as exc:
            # Store type, not venue exception payloads that may contain request data.
            intent["error_type"] = type(exc).__name__
            self.save(intent, "venue_or_recovery_unavailable")

    def _tick(self, intent):
        order = self.order(intent)
        if str(order.get("status", "")).lower() not in TERMINAL:
            # Stop a partial entry accumulating more exposure before adoption.
            # Cancellation can race with a fill; re-read instead of trusting its ack.
            if order.get("id"):
                self.ex.cancel_order(order["id"], intent["symbol"])
                order = self.order(intent)
            if str(order.get("status", "")).lower() not in TERMINAL:
                self.save(intent, "entry_order_not_terminal")
                return
        intent["entry_observation"] = {k: order.get(k) for k in ("id", "status", "filled", "average")}
        if intent["phase"] == "closing":
            close = self.order(intent, close=True)
            intent["close_observation"] = {k: close.get(k) for k in ("id", "status", "filled", "average")}
            if str(close.get("status", "")).lower() not in TERMINAL:
                self.save(intent, "emergency_close_not_terminal")
                return
            # Preserve every terminal close before a remaining-size retry can
            # replace close_order_id. Persisting the next intent saves this too.
            closes = intent.setdefault("close_orders", [])
            observation = intent["close_observation"]
            if not any(str(c.get("id")) == str(observation.get("id")) for c in closes):
                closes.append(observation)
        positions = self.ex.fetch_positions()
        if not isinstance(positions, list):
            raise ValueError("position snapshot unavailable")
        matches = []
        for p in positions:
            if norm_symbol(p["symbol"]) != intent["symbol"]:
                continue
            amount = float(p["contracts"])
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("invalid venue quantity")
            if amount > 0:
                matches.append(p)
        if not matches:
            # Terminal orders plus a fresh position read, never a momentary flat
            # snapshot while an accepted entry could still fill later.
            if protective.open_stops(self.ex, intent["symbol"], strict=True):
                # An ambiguous stop may have been accepted before emergency
                # flattening. Do not let it accidentally close the next entry.
                self.save(intent, "flat_with_remaining_protection")
                return
            self.finish(intent, "terminal_orders_and_flat_venue")
            return
        if len(matches) != 1 or matches[0].get("side") != intent["position"]["side"]:
            self.save(intent, "venue_side_or_hedge_conflict")
            return
        p = matches[0]
        amount = float(p["contracts"])
        if intent["phase"] == "closing" or intent.get("force_close"):
            self.submit_close(intent, amount)  # confirmed partial close: remaining size only
            return
        filled = float(order.get("filled") or 0)
        if not math.isfinite(filled) or not math.isclose(amount, filled, rel_tol=1e-8):
            # A different position or an intervening exit needs separate fill
            # accounting. Never adopt it as an untouched entry with zero P&L.
            self.save(intent, "entry_fill_position_mismatch")
            return
        template = intent["position"]
        # Detect conflicting journal ownership before placing any new orders.
        existing = [t for t in self.journal.open_trades() if t["symbol"] == intent["symbol"]]
        if any(t["id"] != template["id"] for t in existing):
            self.save(intent, "journal_ownership_conflict")
            return
        stops = protective.open_stops(self.ex, intent["symbol"], strict=True)
        sl = float(template["stop_loss"])
        if not math.isfinite(sl) or sl <= 0:
            raise ValueError("invalid persisted stop")
        candidates = [s for s in stops if protective.protection_match(
            self.ex, intent["symbol"], p["side"], amount, sl, s).matches]
        if not candidates:
            # Existing mismatched protection is preserved for inspection. Do not
            # stack replacement stops against an ambiguous placement attempt.
            if stops or intent.get("stop_attempted"):
                self.save(intent, "protection_requires_verification")
                return
            mark = float(p.get("markPrice") or 0)
            if not math.isfinite(mark) or mark <= 0 or sl <= 0:
                raise ValueError("missing stop geometry")
            if (sl >= mark if p["side"] == "long" else sl <= mark):
                self.submit_close(intent, amount)
                return
            intent["stop_attempted"] = True
            self.save(intent, "recovery_stop_intent_persisted")
            try:
                close_side = "sell" if p["side"] == "long" else "buy"
                protective.place_stop(self.ex, intent["symbol"], close_side, amount, sl)
            except (InvalidOrder, InsufficientFunds):
                self.submit_close(intent, amount)
                return
            self.save(intent, "recovery_stop_submitted_unconfirmed")
            return  # enumerate on next cycle; submission is not confirmation
        entry = float(p.get("entryPrice") or 0)
        if not math.isfinite(entry) or entry <= 0:
            raise ValueError("missing actual entry price")
        if not existing:
            values = dict(template)
            values.update(side=Side(p["side"]), amount=amount, entry_price=entry,
                          notional_usdt=amount*entry, sl_order_id=candidates[0]["id"],
                          stop_loss=candidates[0]["stop_price"])
            self.journal.add_trade(Position(**values), accounting={
                "basis": "recovered_entry_order_confirmation", "order_id": str(order.get("id") or ""),
                "confirmed_quantity": amount, "confirmed_price": entry, "recovery_intent_id": intent["id"]})
        else:
            # Do not silently rewrite realized accounting if size changed during recovery.
            if not math.isclose(float(existing[0]["amount"]), amount, rel_tol=1e-8):
                self.save(intent, "journal_size_conflict")
                return
            self.journal.record_stop_order(template["id"], candidates[0]["id"], candidates[0]["stop_price"])
        self.journal.update_decision_outcome(template["decision_id"], True, amount*entry,
                                             "recovered_confirmed_exposure")
        self.journal.set_decision_entry_price(template["decision_id"], entry)
        self.finish(intent, "confirmed_protected_and_journalled")
