"""Offline economics presentation; stored evidence never becomes complete accounting."""
from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation

from ..engine.accounting import digest
from ..engine import booking

VERSION = "trade-economics-read.v2"


def booking_receipts(journal, trade_id):
    """Uncapped monetary history: an early UNKNOWN leg must not disappear."""
    return [r["payload"] for r in journal.query(
        "SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id", (trade_id,))]


def journal_pnl(trade, receipts=()):
    """A journal scalar is not authoritative over its retained booking evidence."""
    evidence, unknown, estimated = [], False, False
    for raw in receipts:
        try:
            receipt = json.loads(raw) if isinstance(raw, str) else raw
            booking.replay(receipt)
            if receipt.get("trade_id") != trade["id"] or receipt.get("after", {}).get("id") != trade["id"]:
                raise ValueError("trade_binding")
            if receipt.get("kind") == "entry":
                continue
            evidence.append(dict(receipt_id=receipt.get("sha256"), **receipt["evidence"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            unknown = True
            evidence.append({"integrity": "UNVERIFIED"})
    evidence.append({k: trade[k] for k in ("pnl_status", "pnl_value_class", "exit_price_source", "pnl_price_source", "basis") if k in trade})
    for ev in evidence:
        status, cls = ev.get("pnl_status"), ev.get("pnl_value_class")
        source = ev.get("pnl_price_source") or ev.get("exit_price_source")
        basis = str(ev.get("basis") or "")
        unknown |= status == "UNKNOWN" or cls == "UNKNOWN" or source == "journal_entry_not_a_fill" or "unpriced" in basis
        estimated |= status in ("ESTIMATE", "ESTIMATED") or cls == "DERIVED_ESTIMATE" or source == "ticker_last_not_a_fill" or basis in ("estimated_order_booking", "estimated", "panic_order_unconfirmed", "reconcile_ghost_mark_estimate")
    value = trade.get("realized_pnl")
    if type(value) not in (int, float) or not math.isfinite(value):
        unknown = True
    classification = "UNKNOWN" if unknown else "DERIVED_ESTIMATE" if estimated else "JOURNAL_BOOKED"
    return dict(value=None if unknown else value, status=classification,
                evidence=evidence, pnl_value_class=classification,
                coverage={"whole_trade_complete": False, "funding_attributed": False,
                          "booking_receipts": len(receipts),
                          "source_classification": classification,
                          "price_evidence": evidence,
                          "reason": "journal booking; not venue net accounting"})


def read(trade, provenance, receipts=()):
    """Keep venue fields, journal bookings and derived measurements separate.

    Versions identify the displayed source snapshot, not a reconstructed historical
    calculation version. Legacy calculation versions remain explicitly unknown.
    """
    def safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [safe(v) for v in value]
        return value

    def row(field, value, status, source, snapshot, coverage, calculation_version=None):
        return dict(field=field, value=value, status=status, source=source,
                    version=digest(safe(snapshot)), calculation_version=calculation_version,
                    coverage=safe(coverage))

    def finite(value):
        return value if type(value) in (int, float) and math.isfinite(value) else None

    def monetary(value):
        try:
            return value if not isinstance(value, bool) and Decimal(str(value)).is_finite() else None
        except InvalidOperation:
            return None

    legs = provenance.get("legs") or []
    venue = []
    seen = set()
    for leg in legs:
        for fill in leg.get("fills") or []:
            key = (fill.get("market_type"), fill.get("venue_fill_id"))
            if key in seen:
                continue
            seen.add(key)
            coverage = dict(trade_id=trade["id"], leg_id=leg["leg_id"],
                            fill_id=fill.get("venue_fill_id"),
                            order_id=fill.get("venue_order_id"),
                            market_type=fill.get("market_type"),
                            fill_coverage=leg.get("fill_coverage"),
                            attribution=fill.get("attribution"),
                            observed_ms=fill.get("observed_ms"),
                            whole_trade_complete=False)
            for field in ("realized_pnl", "commission"):
                value = monetary(fill.get(field))
                venue.append(row(field, value, "RECORDED_VENUE_FIELD" if value is not None
                                 else "UNAVAILABLE", "trade_fills:" + str(fill.get("source")),
                                 fill, dict(coverage, currency=fill.get("commission_asset")
                                            if field == "commission" else None)))
    if not venue:
        for field in ("realized_pnl", "commission"):
            venue.append(row(field, None, "UNAVAILABLE", "trade_fills", {},
                             dict(whole_trade_complete=False, reason="no attributed fills")))
    for field in ("funding", "net_economic_pnl"):
        venue.append(row(field, None, "UNAVAILABLE", "whole-trade cashflow receipts", {},
                         dict(whole_trade_complete=False,
                              reason="not available in this journal read; leg receipts do not establish funding")))

    pnl = journal_pnl(trade, receipts)
    journal = [row("realized_pnl", pnl["value"], pnl["status"],
                   "trades.realized_pnl + trade_accounting_bookings.evidence",
                   dict(trade={k: trade.get(k) for k in ("id", "realized_pnl", "status", "closed_at")},
                        evidence=pnl["evidence"]), pnl["coverage"])]
    excursion = trade.get("excursion")
    excursion = excursion if isinstance(excursion, dict) else {}
    derived = []
    for field, value in (("R", excursion.get("exit_r")),
                         ("MFE", trade.get("mfe_r")), ("MAE", trade.get("mae_r"))):
        value = finite(value)
        derived.append(row(field, value, "DERIVED_RECORDED" if value is not None else "UNAVAILABLE",
                           "trades.excursion_json / trader.engine.excursion",
                           dict(trade_id=trade["id"], field=field, value=value, excursion=excursion),
                           dict(excursion, whole_trade_complete=False,
                                interpretation="price excursion in frozen risk units; not monetary net return"),
                           excursion.get("schema_version")))
    reference = provenance.get("slippage_basis") or {}
    derived.append(row("slippage", None, "UNAVAILABLE", "trade_legs.reference_json",
                       reference, dict(reference=reference,
                                       reason="reference alone does not establish measured slippage")))
    attribution = [dict(leg_id=l["leg_id"], **l["exit_attribution"]) for l in legs]
    derived.append(row("attribution", attribution or None, "DERIVED_RECORDED" if legs else "UNAVAILABLE",
                       "trader.engine.trade_provenance.read", attribution,
                       dict(meaning=provenance.get("exit_attribution_meaning"),
                            missing=provenance.get("missing"), whole_trade_complete=False),
                       provenance.get("schema_version")))
    return dict(schema_version=VERSION, venue_monetary=venue, journal_booked=journal,
                derived=derived, whole_trade_complete=False)
