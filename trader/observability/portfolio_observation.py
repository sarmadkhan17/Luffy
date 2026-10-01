"""Detached truth from one completed Binance USD-M position response.

The receipt time is a local knowledge time, not an atomic venue-side cut.
This module performs no exchange requests and grants no trading authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal

from ..core.instrument_registry import InstrumentId
from ..core.types import MarketType

SCHEMA = "portfolio.observation.v1"
VENUE = "binance_usdm"
SOURCE = "ccxt.binanceusdm.fetch_positions"
_TRADING_SOURCES = {
    "https://demo-fapi.binance.com/fapi/v3": ("demo", "https://demo-fapi.binance.com"),
    "https://fapi.binance.com/fapi/v3": ("production", "https://fapi.binance.com"),
}
_VENUE_SYMBOL = re.compile(r"[A-Z0-9]+USDT")


def trading_source(exchange: object) -> tuple[str, str]:
    """Read the private USD-M REST venue already resolved by make_exchange()."""
    urls = getattr(exchange, "urls", None)
    api = urls.get("api") if isinstance(urls, dict) else None
    private_url = api.get("fapiPrivateV3") if isinstance(api, dict) else None
    try:
        return _TRADING_SOURCES[private_url]
    except (KeyError, TypeError) as exc:
        raise ValueError("unsupported or ambiguous trading REST venue") from exc


@dataclass(frozen=True)
class PositionRecord:
    instrument_id: InstrumentId
    position_present: bool
    side: str | None
    absolute_quantity: float


@dataclass(frozen=True)
class PortfolioObservation:
    schema: str
    venue: str
    source: str
    environment: str
    source_ref: str
    market_type: MarketType
    request_start_ms: int
    response_received_ms: int
    as_of_ms: int
    complete: bool
    positions: tuple[PositionRecord, ...]
    observation_id: str

    def get(self, instrument_id: InstrumentId) -> PositionRecord | None:
        """None means absent from this complete response, not a venue row."""
        return next((p for p in self.positions if p.instrument_id == instrument_id), None)


def observe_positions(
    rows: object, *, exchange_id: str, market_type: MarketType,
    environment: str, source_ref: str,
    request_start_ms: int, response_received_ms: int,
) -> PortfolioObservation:
    """Validate every row before publishing any state.

    CCXT's unfiltered ``fetch_positions()`` is the full response supplied by
    the caller. A failed call, non-list result, or invalid row has no result.
    The raw Binance symbol in ``info.symbol`` is required and checked against
    CCXT's unified USD-M symbol; ``norm_symbol`` is not identity evidence.
    """
    if exchange_id != "binanceusdm" or market_type is not MarketType.FUTURES:
        raise ValueError("unsupported venue or market type")
    if (environment, source_ref) not in _TRADING_SOURCES.values():
        raise ValueError("unsupported or ambiguous trading REST venue")
    if (type(request_start_ms) is not int or type(response_received_ms) is not int
            or request_start_ms < 0 or response_received_ms < request_start_ms):
        raise ValueError("invalid position observation time")
    if not isinstance(rows, list):
        raise ValueError("position response is not a complete list")

    records: list[PositionRecord] = []
    seen: set[InstrumentId] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("info"), dict):
            raise ValueError("invalid position row")
        venue_symbol = row["info"].get("symbol")
        if not isinstance(venue_symbol, str) or not _VENUE_SYMBOL.fullmatch(venue_symbol):
            raise ValueError("invalid Binance USD-M venue symbol")
        base = venue_symbol[:-4]
        if row.get("symbol") != f"{base}/USDT:USDT":
            raise ValueError("ambiguous position symbol identity")
        instrument_id = InstrumentId(VENUE, MarketType.FUTURES, venue_symbol)
        if instrument_id in seen:
            raise ValueError("duplicate position instrument")
        seen.add(instrument_id)

        raw_quantity = row.get("contracts")
        if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, (int, float, Decimal)):
            raise ValueError("invalid position quantity")
        try:
            quantity = float(raw_quantity)
        except (OverflowError, ValueError) as exc:
            raise ValueError("invalid position quantity") from exc
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError("invalid position quantity")
        side = row.get("side")
        if quantity > 0:
            if side not in ("long", "short"):
                raise ValueError("ambiguous position side")
        elif side not in (None, "long", "short"):
            raise ValueError("ambiguous zero-position side")
        records.append(PositionRecord(instrument_id, quantity > 0,
                                      side if quantity > 0 else None, quantity))

    positions = tuple(sorted(records, key=lambda record: record.instrument_id.value))
    payload = {
        "schema": SCHEMA, "venue": VENUE, "source": SOURCE,
        "environment": environment, "source_ref": source_ref,
        "market_type": MarketType.FUTURES.value,
        "request_start_ms": request_start_ms,
        "response_received_ms": response_received_ms,
        "as_of_ms": response_received_ms, "complete": True,
        "positions": [
            [p.instrument_id.value, p.position_present, p.side, p.absolute_quantity]
            for p in positions
        ],
    }
    observation_id = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    return PortfolioObservation(SCHEMA, VENUE, SOURCE, environment, source_ref,
                                MarketType.FUTURES,
                                request_start_ms, response_received_ms,
                                response_received_ms, True, positions,
                                observation_id)
