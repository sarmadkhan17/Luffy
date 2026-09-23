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
import math
from dataclasses import dataclass

log = logging.getLogger(__name__)


class ProtectiveSnapshotIncomplete(RuntimeError):
    """Global protective-order truth cannot be established."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class StopSnapshot(list):
    """List of observed stops with an explicit global-completeness marker."""

    def __init__(self, rows=(), *, complete: bool, reason: str | None = None):
        super().__init__(rows)
        self.complete = complete
        self.reason = reason


@dataclass(frozen=True)
class ProtectionMatch:
    matches: bool
    reason: str
    expected_normalized_stop: float | None
    actual_stop: float | None
    required_amount: float | None
    actual_amount: float | None


def protection_match(ex, symbol: str, position_side: str,
                     position_amount: float, expected_stop_price: float,
                     stop: dict) -> ProtectionMatch:
    """Decide if one normalized venue stop protects the current venue position.

    Price comparison uses the venue's submission precision. Only floating
    representation slack is allowed; quantity may cover more than the position.
    """
    def number(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    amount = number(stop.get("amount"))
    actual = number(stop.get("stop_price"))
    required = number(position_amount)
    expected = number(expected_stop_price)
    normalized = None
    if expected is not None and expected > 0:
        try:
            normalized = number(ex.price_to_precision(symbol, expected)) if callable(
                getattr(ex, "price_to_precision", None)) else number(round(expected, 6))
        except Exception:
            normalized = None
    reason = "matched"
    if venue_key(str(stop.get("symbol") or "")) != venue_key(symbol):
        reason = "symbol_mismatch"
    elif not str(stop.get("id") or ""):
        reason = "missing_order_id"
    elif position_side not in ("long", "short"):
        reason = "invalid_position_side"
    elif str(stop.get("side") or "").lower() != ("sell" if position_side == "long" else "buy"):
        reason = "wrong_closing_side"
    elif stop.get("reduce_only") is not True:
        reason = "not_reduce_only"
    elif stop.get("order_type") != "STOP_MARKET":
        reason = "wrong_order_type"
    elif required is None or required <= 0:
        reason = "invalid_position_amount"
    elif amount is None or amount <= 0:
        reason = "invalid_stop_amount"
    elif actual is None or actual <= 0:
        reason = "invalid_stop_trigger"
    elif normalized is None or normalized <= 0:
        reason = "invalid_expected_stop"
    elif amount < required and not math.isclose(amount, required, rel_tol=1e-12, abs_tol=1e-12):
        reason = "insufficient_amount"
    elif position_side == "long" and actual < normalized and not math.isclose(
            actual, normalized, rel_tol=1e-12, abs_tol=1e-12):
        reason = "trigger_below_expected"
    elif position_side == "short" and actual > normalized and not math.isclose(
            actual, normalized, rel_tol=1e-12, abs_tol=1e-12):
        reason = "trigger_above_expected"
    return ProtectionMatch(reason == "matched", reason, normalized, actual,
                           required, amount)


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


def open_stops(ex, symbol: str | None = None, *, strict: bool = False,
               known_symbols: set[str] | None = None) -> StopSnapshot:
    """Every live protective order, algo and ordinary, as one flat list.

    Each entry: {id, symbol, side, amount, stop_price, kind}. `symbol` is the
    venue's own string (BTCUSDT for algo rows), so callers compare on a
    normalised form rather than trusting either spelling.
    """
    out: list[dict] = []
    global_algo = callable(getattr(ex, "fapiPrivateGetOpenAlgoOrders", None))
    complete = global_algo or symbol is not None
    reason = None if complete else "global_stop_listing_unsupported"
    if not global_algo and symbol is None and strict:
        raise ProtectiveSnapshotIncomplete(reason)
    if global_algo:
        try:
            r = ex.fapiPrivateGetOpenAlgoOrders()
            if not (isinstance(r, list) or
                    isinstance(r, dict) and isinstance(r.get("orders"), list)):
                if symbol is None:
                    raise ProtectiveSnapshotIncomplete("algo_listing_invalid")
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
                if symbol is not None or isinstance(e, ProtectiveSnapshotIncomplete):
                    raise
                raise ProtectiveSnapshotIncomplete("algo_listing_failed") from e
            complete, reason = False, "algo_listing_failed"
            log.warning("algo order listing failed: %s", e)
    # Binance USDM's global conditional endpoint is authoritative for LUFFY
    # stops. ccxt rejects symbol-less fetchOpenOrders on that venue. Ordinary
    # fallback is therefore always scoped to explicitly named symbols.
    symbols = [symbol] if symbol is not None else sorted(known_symbols or ())
    for known_symbol in symbols:
        try:
            rows = ex.fetch_open_orders(known_symbol)
            if strict and not isinstance(rows, list):
                raise ValueError("invalid ordinary order snapshot")
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
            complete, reason = False, "order_listing_failed"
            log.warning("plain order listing failed for %s: %s", known_symbol, e)
    if symbol:
        key = venue_key(symbol)
        out = [o for o in out if venue_key(o["symbol"]) == key]
    if not complete:
        log.warning("protective stop snapshot incomplete: %s", reason)
    return StopSnapshot(out, complete=complete, reason=reason)


def venue_key(symbol: str) -> str:
    """BTC/USDT, BTC/USDT:USDT and BTCUSDT are one market."""
    return symbol.split(":")[0].replace("/", "").upper()


def sweep_orphans(ex, keep_ids: set[str], live_symbols: set[str],
                  protected: set[str] | None = None, *,
                  strict: bool = False,
                  known_symbols: set[str] | None = None) -> tuple[int, int]:
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
    snapshot = open_stops(ex, strict=strict, known_symbols=known_symbols)
    if not snapshot.complete:
        # An incomplete global read cannot justify destructive orphan cleanup.
        raise ProtectiveSnapshotIncomplete(snapshot.reason or "global_stop_listing_incomplete")
    for o in snapshot:
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
