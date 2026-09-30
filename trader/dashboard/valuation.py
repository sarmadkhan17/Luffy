"""Unrealized P&L estimate for journal-open positions from ticker valuations.

Read-only and pure: callers supply the open journal trades and the ticker
quotes they already fetched (DataFeed.ticker_quote: last, else close — NOT an
exchange mark price). Each position carries its quote's identity, its own
source/receipt time and age; the total is published only when every open
position has a finite, well-formed, fresh, USDT-settled valuation. Any gap
makes the total UNAVAILABLE — never a partial sum. Commissions and funding
are excluded (not proven per position), so this is an estimate.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..core import truth

QUOTE_STALE_S = 60.0
BASIS = ("venue ticker last/close via DataFeed.ticker_quote (futures; follows "
         "BINANCE_DEMO); not the exchange mark price")
EXCLUSIONS = ("commissions and funding are excluded (not proven per position); "
              "estimate = (ticker price - journal entry) x signed journal quantity")


def _settle(symbol: str) -> str | None:
    s = str(symbol or "").upper()
    if ":" in s:
        return s.split(":", 1)[1] or None
    if "/" in s:
        return s.split("/", 1)[1] or None
    return "USDT" if s.endswith("USDT") else None


def _quote_view(q, now: datetime) -> tuple[dict | None, list[str]]:
    """The quote's identity and time. A local receipt time (taken after the
    request returned) is always required; it may not precede a recorded attempt
    start. A ticker timestamp, when present, is kept independently and must not
    be later than that receipt; nothing may be in the future."""
    if not isinstance(q, dict):
        return None, ["quote_missing"]
    reasons = []
    px = truth.finite(q.get("price"))
    if px is None or px <= 0:
        reasons.append("quote_price_missing_or_malformed" if q.get("error") is None
                       else str(q.get("error")))
    field = q.get("field")
    if field not in ("last", "close"):
        reasons.append("quote_field_unrecognized")
    rec = truth.freshness_of(q.get("received_at"), now, QUOTE_STALE_S)
    if rec["freshness"] == truth.UNAVAILABLE:
        reasons.append("quote_receipt_missing")
    elif rec["freshness"] == truth.INVALID:
        reasons.append("quote_receipt_" + ("in_future" if rec["reason"] ==
                                           "source_time_in_future" else "malformed"))
    if q.get("attempt_started_at") is not None:
        # receipt is taken after the request returned: it cannot precede the attempt
        att_t, rec_t = truth.parse_time(q.get("attempt_started_at"))[0], \
            truth.parse_time(q.get("received_at"))[0]
        if att_t is None:
            reasons.append("quote_attempt_malformed")
        elif rec_t is not None and truth.is_after(att_t, rec_t):
            reasons.append("quote_receipt_before_attempt")
    view, time_basis = rec, "local_receipt"
    if q.get("source_ms") is not None:
        time_basis = "ticker_timestamp"
        src_s = truth.finite(q.get("source_ms"))
        view = truth.freshness_of(src_s / 1000.0 if src_s is not None else "malformed",
                                  now, QUOTE_STALE_S)
        if view["freshness"] == truth.INVALID:
            reasons.append("quote_source_" + ("in_future" if view["reason"] ==
                                              "source_time_in_future" else "malformed"))
        else:
            src_t, rec_t = truth.parse_time(src_s / 1000.0)[0], truth.parse_time(
                q.get("received_at"))[0]
            if rec_t is None:
                reasons.append("quote_source_receipt_relation_unproven")
            elif truth.is_after(src_t, rec_t):
                reasons.append("quote_time_after_receipt")
    if view["freshness"] == truth.STALE or (time_basis == "ticker_timestamp"
                                            and rec["freshness"] == truth.STALE):
        reasons.append("quote_stale")
    ok = not reasons
    return {"price": px, "field": field,
            "basis": f"ticker_{field}" if field in ("last", "close") else None,
            "source_time": view["observed_at"] if time_basis == "ticker_timestamp" else None,
            "received_at": rec["observed_at"], "time_basis": time_basis,
            "age_s": view["age_s"], "freshness": view["freshness"] if ok else (
                view["freshness"] if view["freshness"] != truth.FRESH else truth.INVALID),
            "stale_after_s": QUOTE_STALE_S}, list(dict.fromkeys(reasons))


def estimate(trades: list[dict], quotes: dict, now: datetime | None = None) -> dict:
    """{status: COMPLETE|NO_POSITIONS|UNAVAILABLE, total_upnl, coverage, positions}.
    Positions are per trade (two trades on one symbol are two positions)."""
    now = now or datetime.now(timezone.utc)
    positions, parts = [], []
    for t in trades:
        reasons: list[str] = []
        side = t.get("side")
        amount, entry = truth.finite(t.get("amount")), truth.finite(t.get("entry_price"))
        if side not in ("long", "short"):
            reasons.append("side_malformed")
        if amount is None or amount < 0 or isinstance(t.get("amount"), bool):
            reasons.append("quantity_malformed")
        if entry is None or entry <= 0 or isinstance(t.get("entry_price"), bool):
            reasons.append("entry_price_malformed")
        settle = _settle(t.get("symbol"))
        if settle != "USDT":
            reasons.append("settlement_currency_incompatible")
        signed = (amount if side == "long" else -amount) \
            if amount is not None and not reasons and side in ("long", "short") else None
        q, q_reasons = _quote_view(quotes.get(t.get("symbol")) if isinstance(quotes, dict)
                                   else None, now)
        reasons += q_reasons
        upnl = None
        if not reasons:
            upnl = (q["price"] - entry) * signed          # zero quantity → exactly 0.0
            if not math.isfinite(upnl):
                reasons.append("upnl_not_finite")
                upnl = None
        positions.append({"trade_id": str(t.get("id")), "symbol": t.get("symbol"),
                          "side": side, "signed_quantity": signed, "entry_price": entry,
                          "zero_quantity": amount == 0, "quote": q,
                          "upnl_estimate": upnl,
                          "status": "OK" if not reasons else "UNAVAILABLE",
                          "reasons": reasons})
        if upnl is not None:
            parts.append(upnl)
    covered = sum(1 for p in positions if p["status"] == "OK")
    agg_reasons = []
    if not positions:
        status, value = "NO_POSITIONS", 0.0
    elif covered == len(positions):
        try:
            total = math.fsum(parts)
        except OverflowError:                         # finite parts, infinite sum
            total = math.inf
        if math.isfinite(total):
            status, value = "COMPLETE", total
        else:
            status, value = "UNAVAILABLE", None
            agg_reasons.append("aggregate_not_finite")
    else:
        status, value = "UNAVAILABLE", None
        agg_reasons.append("incomplete_coverage")
    oldest = [p["quote"]["source_time"] or p["quote"]["received_at"]
              for p in positions if p["status"] == "OK"]
    return {"status": status, "total_upnl": value, "currency": "USDT",
            "reasons": agg_reasons,
            "coverage": {"positions": len(positions), "valued": covered,
                         "complete": covered == len(positions)},
            "oldest_quote_at": min(oldest) if oldest and status == "COMPLETE" else None,
            "positions": positions, "basis": BASIS, "exclusions": EXCLUSIONS,
            "fees_funding": "EXCLUDED_UNPROVEN",
            "position_source": "journal open trades (booked quantity/entry; not venue-confirmed)",
            "computed_at": truth.iso(now)}
