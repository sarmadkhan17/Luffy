"""Detached truth from one completed Binance futures position response.

The receipt time is a local knowledge time, not an atomic venue-side cut.
This module performs no exchange requests and grants no trading authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime

from ..core.instrument_registry import InstrumentId
from ..core.types import MarketType

SCHEMA = "portfolio.observation.v1"
VENUE = "binance_usdm"
SOURCE = "ccxt.binanceusdm.fetch_positions"
_TRADING_SOURCES = {
    "https://demo-fapi.binance.com/fapi/v3": ("demo", "https://demo-fapi.binance.com"),
    "https://fapi.binance.com/fapi/v3": ("production", "https://fapi.binance.com"),
}
_COIN_TRADING_SOURCES = {
    'https://demo-dapi.binance.com/dapi/v1': ('demo', 'https://demo-dapi.binance.com'),
    'https://dapi.binance.com/dapi/v1': ('production', 'https://dapi.binance.com'),
}
_VENUE_SYMBOL = re.compile(r"[A-Z0-9]+USDT")
_COIN_SYMBOL = re.compile(r'([A-Z0-9]+)USD_(PERP|[0-9]{6})')


def coin_position_identity(row):
    """Exact CCXT/native symbol join; contracts are not base-coin amounts."""
    info=row.get('info')
    symbol=info.get('symbol') if isinstance(info,dict) else None
    match=_COIN_SYMBOL.fullmatch(symbol) if isinstance(symbol,str) else None
    if match is None:raise ValueError('invalid Binance COIN-M venue symbol')
    base,expiry=match.groups()
    expected=f'{base}/USD:{base}'
    if expiry!='PERP':
        try:datetime.strptime(expiry,'%y%m%d')
        except ValueError:raise ValueError('invalid COIN-M delivery date') from None
        expected+='-'+expiry
    if row.get('symbol')!=expected:
        raise ValueError('ambiguous position symbol identity')
    if info.get('positionSide')!='BOTH' or row.get('hedged') is not False:
        raise ValueError('COIN-M position mode unsupported')
    try:
        if isinstance(row.get('contractSize'),bool):raise ValueError
        size=Decimal(str(row.get('contractSize')))
        if not size.is_finite() or size<=0:raise ValueError
    except (InvalidOperation,ValueError):
        raise ValueError('COIN-M contract size unavailable') from None
    return symbol,base


def trading_source(exchange: object) -> tuple[str, str]:
    """Identify the already-resolved REST venue; this grants no capability."""
    urls = getattr(exchange, "urls", None)
    api = urls.get("api") if isinstance(urls, dict) else None
    coin = getattr(exchange, 'id', None) == 'binancecoinm'
    private_url = api.get('dapiPrivate' if coin else 'fapiPrivateV3') if isinstance(api, dict) else None
    try:
        if coin:
            result = _COIN_TRADING_SOURCES[private_url]
            for name, suffix in (('dapiPublic', '/dapi/v1'), ('dapiPrivateV2', '/dapi/v2')):
                if name in api and api[name] != result[1] + suffix:
                    raise ValueError('mixed COIN-M trading REST venues')
            return result
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
    CCXT's unified symbol; ``norm_symbol`` is not identity evidence.
    """
    coin=exchange_id=='binancecoinm'
    if exchange_id not in ('binanceusdm','binancecoinm') or market_type is not MarketType.FUTURES:
        raise ValueError("unsupported venue or market type")
    sources=_COIN_TRADING_SOURCES if coin else _TRADING_SOURCES
    if (environment, source_ref) not in sources.values():
        raise ValueError("unsupported or ambiguous trading REST venue")
    if (type(request_start_ms) is not int or type(response_received_ms) is not int
            or request_start_ms < 0 or response_received_ms < request_start_ms):
        raise ValueError("invalid position observation time")
    if not isinstance(rows, list):
        raise ValueError("position response is not a complete list")

    records: list[PositionRecord] = []
    venue='binancecoinm' if coin else VENUE
    source='ccxt.binancecoinm.fetch_positions' if coin else SOURCE
    seen: set[InstrumentId] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("info"), dict):
            raise ValueError("invalid position row")
        if coin:
            venue_symbol,_=coin_position_identity(row)
        else:
            venue_symbol = row["info"].get("symbol")
            if not isinstance(venue_symbol, str) or not _VENUE_SYMBOL.fullmatch(venue_symbol):
                raise ValueError("invalid Binance USD-M venue symbol")
            base = venue_symbol[:-4]
            if row.get("symbol") != f"{base}/USDT:USDT":
                raise ValueError("ambiguous position symbol identity")
        instrument_id = InstrumentId(venue, MarketType.FUTURES, venue_symbol)
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
        if coin:
            try:
                raw=row['info']['positionAmt']
                if isinstance(raw,bool):raise ValueError
                signed=Decimal(str(raw));contracts=Decimal(str(raw_quantity))
                if (not signed.is_finite() or abs(signed)!=contracts
                        or contracts!=contracts.to_integral_value()
                        or Decimal.from_float(quantity)!=contracts
                        or (signed and side!=('long' if signed>0 else 'short'))):raise ValueError
            except (KeyError,InvalidOperation,ValueError):
                raise ValueError('COIN-M raw position quantity differs') from None
        records.append(PositionRecord(instrument_id, quantity > 0,
                                      side if quantity > 0 else None, quantity))

    positions = tuple(sorted(records, key=lambda record: record.instrument_id.value))
    payload = {
        "schema": SCHEMA, "venue": venue, "source": source,
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
    return PortfolioObservation(SCHEMA, venue, source, environment, source_ref,
                                MarketType.FUTURES,
                                request_start_ms, response_received_ms,
                                response_received_ms, True, positions,
                                observation_id)
