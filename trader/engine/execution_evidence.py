"""Execution / slippage evidence: one exact measurement per executed order.

A read model over what trade provenance already recorded (`trade_legs`,
`trade_fills`, `trades.entry_identity_json`); it writes nothing and makes no
request. One row per Luffy order with a venue order id in a known market
scope:

- strategy / version exactly as frozen at entry (UNKNOWN stays UNKNOWN)
- instrument, side, requested quantity
- the pre-submission reference price the order recorded, with its basis;
  a missing reference is never filled in, so slippage is UNAVAILABLE
- fills attributed to that order by exact venue order id, their quantities,
  the volume-weighted average price, commissions per asset (never summed
  across assets and never folded into price)
- measured slippage = side-signed (VWAP - reference) / reference, in basis
  points; positive is adverse (paid more on a buy, received less on a sell)

- the exact action lineage: logical id (from the entry's frozen execution
  binding), decision id, client order id and venue order id. An entry is
  VERIFIED only when the client order id is the one derived from that logical
  id and, where the reservation row exists, it agrees on all three; any
  disagreement is MISMATCH, never repaired. Exit orders carry no logical
  action id and stay bound to the trade only
- timing: signal bar, decision, submission and fill times as recorded; the
  acknowledgement time, spread and book depth are not persisted at order time
  and are reported UNAVAILABLE, never inferred
- commission VERIFIED only when every attributed fill carries the venue's own
  figure AND the fills cover the booked quantity; a booked estimate with no
  venue figure is ESTIMATED; anything else is UNAVAILABLE. Never zero
- `rejections()`: refused/rejected reservations, which never have fills

No aggregation, no size -> impact relation, no capacity figure: these are
exact historical measurements for a later, separately validated model.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation

from .trade_provenance import ATTRIBUTED, LUFFY_ORDER_PURPOSES, MARKET_TYPES

SCHEMA = "execution-evidence.v1"
MEASURED, UNAVAILABLE, UNKNOWN = "MEASURED", "UNAVAILABLE", "UNKNOWN"
VERIFIED, ESTIMATED = "VERIFIED", "ESTIMATED"


def _pos(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) and v > 0 else None


def _dec(text):
    try:
        d = Decimal(str(text))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def _json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _has(query, table: str) -> bool:
    return bool(query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (table,)))


def _strategy(trade: dict | None) -> dict:
    ident = _json((trade or {}).get("entry_identity_json"))
    if not isinstance(ident, dict):
        return {"status": UNKNOWN, "strategy_id": (trade or {}).get("strategy_id"),
                "reason": "no strategy version was recorded when this trade opened"}
    return {"status": ident.get("status") or UNKNOWN,
            "strategy_id": ident.get("strategy_id"),
            "spec_sha256": ident.get("spec_sha256"),
            "signal_spec_sha256": ident.get("signal_spec_sha256"),
            "params_sha256": ident.get("params_sha256"),
            "reason": ident.get("reason")}


def _reference(leg: dict) -> dict:
    ref = _json(leg.get("reference_json"))
    if not isinstance(ref, dict):
        return {"status": UNAVAILABLE, "price": None,
                "reason": "no pre-submission reference recorded"}
    price = _pos(ref.get("price"))
    if price is None or ref.get("basis") in (None, "unavailable"):
        return {"status": UNAVAILABLE, "price": None, "basis": ref.get("basis"),
                "reason": ref.get("reason") or "reference price not recorded",
                "submitted_ms": ref.get("submitted_ms")}
    # entries record the decision snapshot time as `snapshot_at`; exits as `observed_at`
    observed = ref.get("observed_at")
    source = "observed_at"
    if observed is None and ref.get("snapshot_at") is not None:
        observed, source = ref.get("snapshot_at"), "snapshot_at"
    return {"status": "RECORDED", "price": price, "basis": ref.get("basis"),
            "observed_at": observed, "observed_at_source": source if observed is not None else None,
            "bar_ts": ref.get("bar_ts"),
            "submitted_ms": ref.get("submitted_ms")}


def _instrument(market: str, symbol: str) -> str | None:
    base, _, quote = symbol.partition("/")
    if market == "futures" and base and quote:
        return f"binance_usdm:futures:{base}{quote.split(':')[0]}"
    return None


def _action(leg: dict, trade: dict | None, request: dict | None) -> dict:
    """The exact action lineage of one order, verified or explicitly not."""
    out = {"trade_id": leg["trade_id"], "decision_id": (trade or {}).get("decision_id") or None,
           "client_order_id": leg.get("client_order_id"),
           "venue_order_id": leg.get("venue_order_id"), "logical_id": None}
    if leg.get("purpose") != "entry":
        return dict(out, status="TRADE_BOUND_ONLY",
                    reason="exit orders carry no logical action id; bound to the trade by exact venue order id")
    ident = _json((trade or {}).get("entry_identity_json"))
    binding = ident.get("execution_binding") if isinstance(ident, dict) else None
    logical = binding.get("logical_id") if isinstance(binding, dict) else None
    if not logical or not isinstance(logical, str):
        return dict(out, status=UNKNOWN, reason="no execution binding recorded for this entry")
    out["logical_id"] = logical
    cid = leg.get("client_order_id")
    expected = "lr_" + hashlib.sha256(logical.encode()).hexdigest()[:28]
    recorded_decision = ((ident.get("decision") or {}).get("decision_id")
                         if isinstance(ident.get("decision"), dict) else None)
    if not cid:
        return dict(out, status=UNKNOWN, reason="no client order id recorded on the order")
    if cid != expected:
        return dict(out, status="MISMATCH", expected_client_order_id=expected,
                    reason="client order id is not the one derived from the bound logical id")
    if out["decision_id"] and recorded_decision and out["decision_id"] != recorded_decision:
        return dict(out, status="MISMATCH", reason="trade decision id differs from the decision in the entry identity")
    if request is not None and (request.get("logical_id") != logical
            or request.get("client_order_id") != cid
            or (out["decision_id"] and request.get("decision_id") != out["decision_id"])):
        return dict(out, status="MISMATCH", reason="reservation row disagrees on logical/client/decision id")
    return dict(out, status=VERIFIED, reservation="RECORDED" if request is not None else "NOT_FOUND")


def _timing(leg: dict, ref: dict, trade: dict | None, fill_ts: list, submitted) -> dict:
    ident = _json((trade or {}).get("entry_identity_json"))
    decided = ((ident.get("decision") or {}).get("decided_at")
               if isinstance(ident, dict) and isinstance(ident.get("decision"), dict) else None)
    fields = {"signal_bar_ts": ref.get("bar_ts"),
              "decision_at": decided,
              "reference_observed_at": ref.get("observed_at"),
              "submitted_ms": submitted if isinstance(submitted, int) else None,
              "acknowledged_ms": None,
              "first_fill_ms": min(fill_ts) if fill_ts else None,
              "last_fill_ms": max(fill_ts) if fill_ts else None}
    out = {k: v for k, v in fields.items()}
    out["unavailable"] = sorted(k for k, v in fields.items() if v is None)
    out["note"] = "acknowledgement time is not persisted; a missing field is never filled from another clock"
    return out


def measure(leg: dict, fills: list, trade: dict | None, request: dict | None = None) -> dict:
    """One order's measurement from its recorded leg, its fills attributed by
    exact venue order id, and its trade row (and, if known, its reservation)."""
    market = leg["market_type"]
    good = [f for f in fills if _pos(f.get("qty")) and _pos(f.get("price"))]
    qty = sum(f["qty"] for f in good)
    vwap = sum(f["qty"] * f["price"] for f in good) / qty if good else None
    booked = leg.get("booked_qty")
    if not good:
        coverage = UNAVAILABLE
    elif booked and math.isclose(qty, booked, rel_tol=1e-9, abs_tol=1e-12) \
            and len(good) == len(fills):
        coverage = "COMPLETE"
    else:
        coverage = "PARTIAL"
    commissions: dict = {}
    fee_gaps = 0
    for f in fills:
        v = _dec(f.get("commission"))
        if v is None or not f.get("commission_asset"):
            fee_gaps += 1
            continue
        commissions[f["commission_asset"]] = commissions.get(
            f["commission_asset"], Decimal(0)) + v
    if not fills:
        commission_status = ESTIMATED if leg.get("fee_basis") in (
            "estimated_order_booking", "estimated") else UNAVAILABLE
    elif fee_gaps == 0 and coverage == "COMPLETE":
        commission_status = VERIFIED
    elif commissions:
        commission_status = "PARTIAL"
    else:
        commission_status = ESTIMATED if leg.get("fee_basis") in (
            "estimated_order_booking", "estimated") else UNAVAILABLE
    ref = _reference(leg)
    side = leg.get("side")
    if ref["status"] != "RECORDED":
        slip = {"status": UNAVAILABLE, "reason": "NO_REFERENCE_PRICE"}
    elif vwap is None:
        slip = {"status": UNAVAILABLE, "reason": "NO_ATTRIBUTED_FILLS"}
    elif side not in ("buy", "sell"):
        slip = {"status": UNAVAILABLE, "reason": "ORDER_SIDE_UNKNOWN"}
    else:
        sign = 1.0 if side == "buy" else -1.0
        bps = sign * (vwap - ref["price"]) / ref["price"] * 1e4
        slip = {"status": MEASURED if coverage == "COMPLETE"
                else "MEASURED_ON_PARTIAL_FILLS",
                "adverse_bps": bps, "reference_price": ref["price"],
                "reference_basis": ref["basis"], "vwap": vwap,
                "convention": "side-signed (VWAP - reference) / reference x 1e4; "
                              "positive = adverse"}
    ts = [f["venue_ts_ms"] for f in good if isinstance(f.get("venue_ts_ms"), int)]
    out = {"schema": SCHEMA, "trade_id": leg["trade_id"], "leg_id": leg["id"],
           "purpose": leg["purpose"], "market_type": market,
           "symbol": leg["symbol"], "instrument_id": _instrument(market, leg["symbol"]),
           "venue_order_id": leg["venue_order_id"], "side": side,
           "requested_qty": leg.get("requested_qty"), "booked_qty": booked,
           "strategy": _strategy(trade), "reference": ref,
           "fills": [{"venue_fill_id": f.get("venue_fill_id"), "qty": f.get("qty"),
                      "price": f.get("price"), "commission": f.get("commission"),
                      "commission_asset": f.get("commission_asset"),
                      "venue_ts_ms": f.get("venue_ts_ms")} for f in fills],
           "fill_qty": qty if good else None, "fill_coverage": coverage,
           "vwap": vwap,
           "commissions_by_asset": {a: str(v) for a, v in sorted(commissions.items())},
           "commission_status": commission_status,
           "action": _action(leg, trade, request),
           "timing": _timing(leg, ref, trade, ts, submitted=ref.get("submitted_ms")),
           "market_context": {"spread": {"status": UNAVAILABLE, "reason": "not captured at order time"},
                              "depth": {"status": UNAVAILABLE, "reason": "not captured at order time"},
                              "size": {"requested_qty": leg.get("requested_qty"),
                                       "booked_qty": booked}},
           "slippage": slip,
           "first_fill_ms": min(ts) if ts else None,
           "last_fill_ms": max(ts) if ts else None,
           "submitted_ms": ref.get("submitted_ms"),
           "recorded_ms": leg.get("recorded_ms")}
    out["measurement_id"] = hashlib.sha256(json.dumps(
        out, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return out


def executed_orders(query, *, trade_id: str | None = None) -> list[dict]:
    """Every executed Luffy order's exact measurement, oldest first.
    `query(sql, params) -> list[dict]` (Journal.query). Read-only."""
    if not (_has(query, "trade_legs") and _has(query, "trade_fills")):
        return []
    marks = ",".join("?" * len(LUFFY_ORDER_PURPOSES))
    sql = (f"SELECT * FROM trade_legs WHERE origin='luffy_order' AND purpose IN ({marks}) "
           "AND venue_order_id IS NOT NULL AND market_type IN ('spot','futures')")
    args = tuple(sorted(LUFFY_ORDER_PURPOSES))
    if trade_id is not None:
        sql += " AND trade_id=?"
        args += (trade_id,)
    out = []
    has_requests = _has(query, "execution_requests")
    trades: dict = {}
    for leg in query(sql + " ORDER BY id", args):
        if leg["market_type"] not in MARKET_TYPES:
            continue
        fills = query("SELECT * FROM trade_fills WHERE leg_id=? AND attribution=? AND "
                      "market_type=? ORDER BY venue_ts_ms, id",
                      (leg["id"], ATTRIBUTED, leg["market_type"]))
        tid = leg["trade_id"]
        if tid not in trades:
            rows = query("SELECT * FROM trades WHERE id=?", (tid,))
            trades[tid] = rows[0] if rows else None
        request = None
        if has_requests and leg.get("client_order_id"):
            rows = query("SELECT logical_id,decision_id,client_order_id,state FROM execution_requests "
                         "WHERE client_order_id=?", (leg["client_order_id"],))
            request = rows[0] if len(rows) == 1 else None
        out.append(measure(leg, fills, trades[tid], request))
    return out


def rejections(query) -> list[dict]:
    """Entry reservations the venue or Luffy refused. They have no fills and
    no venue order id; the reason is as recorded, UNAVAILABLE when it was not.
    Read-only."""
    if not _has(query, "execution_requests"):
        return []
    out = []
    for r in query("SELECT logical_id,decision_id,client_order_id,state,result_json "
                   "FROM execution_requests WHERE state='REFUSED' ORDER BY rowid", ()):
        res = _json(r.get("result_json"))
        res = res if isinstance(res, dict) else {}
        out.append({"schema": SCHEMA, "kind": "REJECTION", "logical_id": r["logical_id"],
                    "decision_id": r["decision_id"], "client_order_id": r["client_order_id"],
                    "reason": res.get("reason") or UNAVAILABLE,
                    "submitted": res.get("submitted") if isinstance(res.get("submitted"), bool) else None,
                    "submitted_ms": res.get("submitted_ms") if isinstance(res.get("submitted_ms"), int) else None,
                    "fills": 0})
    return out
