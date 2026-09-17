"""Protective stops: place, cancel and enumerate them on the venue.

Binance USDM books a reduceOnly STOP_MARKET as an **algo (conditional)**
order — it comes back with an `algoId` and `algoType: CONDITIONAL`, and it
lives behind `/fapi/v1/algo/*`, not `/fapi/v1/order`. Three consequences,
all of which were live:

  * `cancel_order(id, symbol)` answers "Unknown order sent" every time. The
    trail ratchet cancels the incumbent stop after placing its replacement,
    so EVERY ratchet leaked one live stop. SUI carried nine, from a short
    that had closed days earlier.
  * `fetch_open_orders()` returns an empty list, so reconcile and the
    dashboard could not see a single protective order. Two positions read
    as naked while they were in fact stopped.
  * `flatten_all()` — /panic — closes the position and leaks its stop too.

A leaked stop is reduceOnly, so it is inert while the symbol is flat. It
stops being inert the moment a NEW position opens on the opposite side: a
stale BUY stop is exactly the closing side of a fresh short, and it fires at
a trigger from a trade that no longer exists.

Everything here degrades to the plain ccxt call, so a venue that books
stops as ordinary orders keeps working unchanged.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def place_stop(ex, symbol: str, close_side: str, amount: float,
               stop_price: float) -> str:
    """Arm a reduceOnly stop. Returns the order/algo id, or "" on failure."""
    o = ex.create_order(symbol, "market", close_side, amount,
                        params={"stopLossPrice": round(stop_price, 6),
                                "reduceOnly": True})
    return str(o.get("id") or (o.get("info") or {}).get("algoId") or "")


def cancel_stop(ex, order_id: str, symbol: str) -> bool:
    """Cancel a protective stop however the venue books it.

    Algo first: that is where Binance USDM actually puts it, and the plain
    path fails there with -2011. Returns True when the stop is gone —
    including when it was already gone, which is the same outcome.
    """
    if not order_id:
        return True
    if hasattr(ex, "fapiPrivateDeleteAlgoOrder"):
        try:
            ex.fapiPrivateDeleteAlgoOrder({"algoId": int(order_id)})
            return True
        except Exception as e:
            msg = str(e)
            # already gone / never an algo order — fall through, don't shout
            if "-2011" not in msg and "not exist" not in msg.lower():
                log.debug(f"algo cancel {order_id} {symbol}: {msg[:120]}")
    try:
        ex.cancel_order(order_id, symbol)
        return True
    except Exception as e:
        log.warning(f"stop {order_id} on {symbol} not cancelled: "
                    f"{str(e)[:140]}")
        return False


def open_stops(ex, symbol: str | None = None, *, strict: bool = False) -> list[dict]:
    """Every live protective order, algo and ordinary, as one flat list.

    Each entry: {id, symbol, side, amount, stop_price, kind}. `symbol` is the
    venue's own string (BTCUSDT for algo rows), so callers compare on a
    normalised form rather than trusting either spelling.
    """
    out: list[dict] = []
    if hasattr(ex, "fapiPrivateGetOpenAlgoOrders"):
        try:
            r = ex.fapiPrivateGetOpenAlgoOrders()
            if strict and not (isinstance(r, list) or
                               isinstance(r, dict) and isinstance(r.get("orders"), list)):
                raise ValueError("invalid protective order snapshot")
            rows = r.get("orders", []) if isinstance(r, dict) else (r or [])
            for o in rows:
                out.append({
                    "id": str(o.get("algoId") or ""),
                    "symbol": str(o.get("symbol") or ""),
                    "side": str(o.get("side") or "").lower(),
                    "amount": float(o.get("quantity") or 0),
                    "stop_price": float(o.get("triggerPrice") or 0),
                    "reduce_only": str(o.get("reduceOnly", "")).lower() == "true",
                    "order_type": str(o.get("orderType") or o.get("type") or "").upper(),
                    "kind": "algo"})
        except Exception as e:
            if strict:
                raise
            log.debug(f"algo order listing failed: {e}")
    try:
        rows = ex.fetch_open_orders(symbol) if symbol else []
        for o in rows:
            info = o.get("info") or {}
            sp = info.get("stopPrice") or o.get("stopPrice")
            if not sp:
                continue
            out.append({
                "id": str(o.get("id") or ""),
                "symbol": str(info.get("symbol") or o.get("symbol") or ""),
                "side": str(o.get("side") or "").lower(),
                "amount": float(o.get("amount") or 0),
                "stop_price": float(sp),
                "reduce_only": str(o.get("reduceOnly", info.get("reduceOnly", ""))).lower() == "true",
                "order_type": str(info.get("origType") or info.get("type") or o.get("type") or "").upper(),
                "kind": "order"})
    except Exception as e:
        if strict:
            raise
        log.debug(f"plain order listing failed: {e}")
    if symbol:
        key = venue_key(symbol)
        out = [o for o in out if venue_key(o["symbol"]) == key]
    return out


def venue_key(symbol: str) -> str:
    """BTC/USDT, BTC/USDT:USDT and BTCUSDT are one market."""
    return symbol.split(":")[0].replace("/", "").upper()


def sweep_orphans(ex, keep_ids: set[str], live_symbols: set[str],
                  protected: set[str] | None = None) -> tuple[int, int]:
    """Cancel every protective stop that no open position accounts for.

    `keep_ids`   — the stop ids the journal still owns.
    `live_symbols` — symbols with an actual position on the venue.
    `protected`  — symbols the journal knows a stop id for. A live symbol
                   NOT in this set has a position whose stop we cannot
                   identify, so every stop on it is left alone: stripping it
                   would leave a real position naked on nothing better than a
                   journal gap.

    What gets cancelled is the residue of a trail ratchet that could not
    cancel its predecessor, and the stops of positions that have since
    closed. Returns (cancelled, failed).
    """
    live = {venue_key(s) for s in live_symbols}
    prot = {venue_key(s) for s in (protected or set())}
    cancelled = failed = 0
    for o in open_stops(ex):
        if o["id"] in keep_ids:
            continue
        key = venue_key(o["symbol"])
        if key in live and key not in prot:
            log.warning(f"stop {o['id']} on {o['symbol']} is unrecognised but "
                        f"the position is live and has no known stop — kept")
            continue
        if cancel_stop(ex, o["id"], o["symbol"]):
            cancelled += 1
            log.info(f"orphan stop cancelled: {o['symbol']} {o['side']} "
                     f"{o['amount']} @ {o['stop_price']} (id {o['id']})")
        else:
            failed += 1
    return cancelled, failed
