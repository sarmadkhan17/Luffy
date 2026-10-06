"""Offline economics presentation; stored evidence never becomes complete accounting."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from ..engine.accounting import digest

VERSION = "trade-economics-read.v1"


def read(trade, provenance):
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

    booked = finite(trade.get("realized_pnl"))
    journal = [row("realized_pnl", booked, "JOURNAL_BOOKED" if booked is not None else "UNAVAILABLE",
                   "trades.realized_pnl", {k: trade.get(k) for k in
                   ("id", "realized_pnl", "status", "closed_at")},
                   dict(whole_trade_complete=False, funding_attributed=False,
                        reason="may include estimates; not venue net accounting"))]
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
