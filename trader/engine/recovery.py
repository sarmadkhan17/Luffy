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


class OrderEvidenceMismatch(ValueError):
    pass


class EntryRecovery:
    def __init__(self, executor):
        self.executor = executor
        self.journal = executor.journal
        self.ex = executor.ex
        self.code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def pending(self):
        # Read failures propagate: callers cannot enter with an unreadable ledger.
        return read(self.journal)

    def save(self, intent, reason, *, conn=None):
        intent.update(reason=reason, updated_ms=int(time.time()*1000))
        if conn is None:
            with self.journal._tx() as db:
                return self.save(intent, reason, conn=db)
        conn.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)", (KEY, json.dumps(intent, allow_nan=False)))
        if intent.get('logical_id'):
            state = {'entry_submission_ambiguous':'UNKNOWN_OUTCOME', 'entry_submitted':'ACKNOWLEDGED',
                     'entry_fill_unconfirmed':'UNKNOWN_OUTCOME', 'entry_order_not_terminal':'PARTIAL',
                     'entry_intent_persisted':'SUBMISSION_ATTEMPTED'}.get(reason, 'UNKNOWN_OUTCOME')
            conn.execute("UPDATE execution_requests SET state=?,recovery_json=?,order_id=? WHERE logical_id=?",
                         (state,json.dumps(intent,allow_nan=False),intent.get('order_id'),intent['logical_id']))
        detail={
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
            "attempts": intent.get("attempts", 0)}
        conn.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES (?, 'execution_recovery','executor',?)",
                     (time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),json.dumps(detail,allow_nan=False)))

    def finish(self, intent, reason):
        # Audit before release; an audit failure must retain the entry barrier.
        self.save(intent, reason)
        # Archive before release, atomically. Accounting failures cannot erase
        # the intent; no network accounting call delays the safety path.
        with self.journal._tx() as db:
            if intent.get('logical_id'):
                db.execute("UPDATE execution_requests SET state='TERMINAL',result_json=?,recovery_json=? WHERE logical_id=?",
                           (json.dumps({'reason':reason,'position_id':intent['position']['id']}),json.dumps(intent,allow_nan=False),intent['logical_id']))
            if reason == "terminal_orders_and_flat_venue":
                from .accounting import archive_flat
                archive_flat(db, intent)
            db.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                       (KEY, "null"))

    def begin(self, position, *, logical_id=None, conn=None):
        intent = {"id": uuid4().hex, "symbol": position.symbol,
                  "schema_version": 1, "code_hash": self.code_hash,
                  "position": position.as_dict(), "phase": "entry",
                  "client_order_id": "lr_"+uuid4().hex,
                  "created_ms": int(time.time()*1000)}
        if logical_id:
            intent['logical_id'] = logical_id
            intent['client_order_id'] = 'lr_' + hashlib.sha256(logical_id.encode()).hexdigest()[:28]
        self.save(intent, "entry_intent_persisted", conn=conn)
        return intent

    def order(self, intent, close=False):
        prefix = "close_" if close else ""
        oid = intent.get(prefix+"order_id")
        if oid:
            order = self.ex.fetch_order(oid, intent["symbol"])
        else:
            order = self.ex.fetch_order(None, intent["symbol"],
                                       {"origClientOrderId": intent[prefix+"client_order_id"]})
        self._require_exact(intent, order, close, oid)
        return order

    def _require_exact(self, intent, order, close, oid):
        """Only the order this action submitted may resolve it. The venue echo
        must positively carry our client id and must not contradict the
        persisted order id, symbol or side; absent identity is not a match."""
        want_cid = intent[("close_" if close else "")+"client_order_id"]
        side = intent["position"]["side"]
        want_side = ("buy" if side == "long" else "sell") if not close else ("sell" if side == "long" else "buy")
        if (not isinstance(order, dict) or order.get("clientOrderId") != want_cid
                or (oid and str(order.get("id")) != str(oid))
                or norm_symbol(str(order.get("symbol") or "")) != intent["symbol"]
                or str(order.get("side") or "").lower() != want_side):
            raise OrderEvidenceMismatch("order evidence does not match submitted action")

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
        except OrderEvidenceMismatch as exc:
            intent["error_type"] = type(exc).__name__
            self.save(intent, "order_evidence_mismatch")  # stays UNKNOWN; never resolves
        except Exception as exc:
            # Store type, not venue exception payloads that may contain request data.
            intent["error_type"] = type(exc).__name__
            self.save(intent, "venue_or_recovery_unavailable")

    def _tick(self, intent):
        binding = (intent['position'].get('entry_identity') or {}).get('execution_binding')
        if binding:
            market = self.ex.market(intent['symbol'])
            from ..data.feed import execution_account_scope
            cap = binding['capability']
            iid = cap['record']['instrument_id']
            if (getattr(self.ex, 'id', None) != 'binanceusdm'
                    or execution_account_scope(self.ex) != cap['account_scope']
                    or market.get('id') != iid['venue_symbol']):
                raise ValueError('recovery_canonical_identity_mismatch')
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
            if binding:
                # Loaded canonical market identity is the authority. Aliases
                # may differ; a raw symbol comparison cannot own a position.
                venue_market = self.ex.market(p['symbol'])
                venue_id = (p.get('info') or {}).get('symbol', venue_market.get('id'))
                if venue_id != venue_market.get('id'):
                    raise ValueError('recovery_venue_position_identity_mismatch')
                if venue_id != iid['venue_symbol']:
                    continue
            elif norm_symbol(p["symbol"]) != intent["symbol"]:
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
                "confirmed_quantity": amount, "confirmed_price": entry, "recovery_intent_id": intent["id"],
                "purpose": "recovered_entry", "client_order_id": intent.get("client_order_id"),
                "side": "buy" if p["side"] == "long" else "sell",
                "reference": {"price": None, "basis": "unavailable",
                              "reason": "recovered entry: pre-submission reference not persisted in intent"}})
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
