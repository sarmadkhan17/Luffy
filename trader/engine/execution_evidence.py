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
                "reason": ref.get("reason") or "reference price not recorded"}
    return {"status": "RECORDED", "price": price, "basis": ref.get("basis"),
            "observed_at": ref.get("observed_at"),
            "submitted_ms": ref.get("submitted_ms")}


def _instrument(market: str, symbol: str) -> str | None:
    base, _, quote = symbol.partition("/")
    if market == "futures" and base and quote:
        return f"binance_usdm:futures:{base}{quote.split(':')[0]}"
    return None


def measure(leg: dict, fills: list, trade: dict | None) -> dict:
    """One order's measurement from its recorded leg, its fills attributed by
    exact venue order id, and its trade row."""
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
           "commission_status": (UNAVAILABLE if not fills else
                                 "VERIFIED" if fee_gaps == 0 else "PARTIAL"),
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
        out.append(measure(leg, fills, trades[tid]))
    return out
