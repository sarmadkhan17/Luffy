"""Pure parsing, filtering and identity for forced-liquidation-participation.v1.

A ``!forceOrder@arr`` item is only an exchange-published, throttled snapshot.
Nothing here counts liquidations, aggregates quantity/notional, or maps order
side BUY/SELL to a long/short liquidation. Raw bytes are the evidence; every
decision below is recomputable from them plus the archived venue metadata.

No network, credentials, exchange objects, Attention, Risk or Execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..core.instrument_registry import InstrumentId
from ..core.types import MarketType

PROTOCOL_ID = "forced-liquidation-participation.v1"
PARSER_VERSION = "forced-liquidation-parser.v1"
STREAM_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
ENVIRONMENT = "production"
VENUE = "binance_usdm"
METADATA_SOURCE_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

SIDES = ("BUY", "SELL")
_DECIMAL_FIELDS = ("q", "p", "ap", "z")

# Item dispositions. Only ACCEPTED can contribute an observed side.
ACCEPTED = "ACCEPTED"
EXCLUDED_ST2 = "EXCLUDED_ST2"
UNCLASSIFIABLE_ST = "UNCLASSIFIABLE_ST"
INVALID_PAYLOAD = "INVALID_PAYLOAD"
NOT_FORCE_ORDER = "NOT_FORCE_ORDER"
MALFORMED_FRAME = "MALFORMED_FRAME"
IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
UNMAPPED = "UNMAPPED"

# Mapping statuses.
EXACT = "EXACT"
AMBIGUOUS = "AMBIGUOUS"
NOT_LISTED = "NOT_LISTED"
NO_SYMBOL = "NO_SYMBOL"


def encode(value) -> str:
    """Canonical JSON: sorted keys, compact separators, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    return sha256(encode(value).encode())


class _Duplicate(ValueError):
    pass


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _Duplicate(key)
        out[key] = value
    return out


def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name}")


def strict_json(raw: bytes):
    """Decode UTF-8 JSON; duplicate keys and NaN/Infinity are malformed."""
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                      parse_constant=_reject_constant)


# ── identity ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SymbolMap:
    """Exact ``o.s`` → InstrumentId through archived production exchangeInfo bytes.

    No stripping, case folding, base-asset matching or spot/COIN-M inference.
    A symbol listed more than once is AMBIGUOUS for every case it could affect.
    """

    raw_sha256: str
    source_url: str
    environment: str
    received_utc_ms: int
    counts: tuple[tuple[str, int], ...]

    @classmethod
    def from_exchange_info(cls, raw: bytes, *, source_url: str, environment: str,
                           received_utc_ms: int) -> "SymbolMap":
        if source_url != METADATA_SOURCE_URL or environment != ENVIRONMENT:
            raise ValueError("production USD-M exchangeInfo metadata required")
        if type(received_utc_ms) is not int or received_utc_ms <= 0:
            raise ValueError("metadata receipt time required")
        body = strict_json(raw)
        rows = body.get("symbols") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise ValueError("exchangeInfo symbols are required")
        counts: dict[str, int] = {}
        for row in rows:
            symbol = row.get("symbol") if isinstance(row, dict) else None
            if not isinstance(symbol, str) or not symbol:
                raise ValueError("exchangeInfo row without a symbol")
            counts[symbol] = counts.get(symbol, 0) + 1
        return cls(sha256(raw), source_url, environment, received_utc_ms,
                   tuple(sorted(counts.items())))

    def resolve(self, symbol) -> dict:
        if not isinstance(symbol, str) or not symbol:
            return {"status": NO_SYMBOL, "instrument_id": None}
        n = dict(self.counts).get(symbol, 0)
        if n == 0:
            return {"status": NOT_LISTED, "instrument_id": None}
        if n > 1:
            return {"status": AMBIGUOUS, "instrument_id": None}
        try:
            iid = InstrumentId(VENUE, MarketType.FUTURES, symbol)
        except ValueError:
            return {"status": AMBIGUOUS, "instrument_id": None}
        return {"status": EXACT, "instrument_id": iid.value}

    def envelope(self) -> dict:
        return {"raw_sha256": self.raw_sha256, "source_url": self.source_url,
                "environment": self.environment, "received_utc_ms": self.received_utc_ms}


# ── frame classification ────────────────────────────────────────────────────

def _timestamp(value) -> bool:
    return type(value) is int and value > 0


def _decimal(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        number = Decimal(value)
    except InvalidOperation:
        return False
    return number.is_finite() and number >= 0


def _raw_field(mapping, key):
    return mapping.get(key) if isinstance(mapping, dict) else None


def classify_item(item, index: int, symbols: SymbolMap) -> dict:
    """One event object → provenance fields, mapping and disposition/reason."""
    order = _raw_field(item, "o")
    out = {"index": index,
           "e": _raw_field(item, "e"), "E": _raw_field(item, "E"),
           "st": _raw_field(item, "st"),
           "T": _raw_field(order, "T"), "s": _raw_field(order, "s"), "S": _raw_field(order, "S"),
           **{k: _raw_field(order, k) for k in _DECIMAL_FIELDS}}
    out["mapping"] = symbols.resolve(out["s"])

    def done(disposition, reason):
        out["disposition"], out["reason"] = disposition, reason
        return out

    if not isinstance(item, dict):
        return done(MALFORMED_FRAME, "item_not_object")
    if item.get("e") != "forceOrder":
        return done(NOT_FORCE_ORDER, "event_type_not_forceOrder")
    st = item.get("st", None)
    if "st" not in item:
        return done(UNCLASSIFIABLE_ST, "st_missing")
    if type(st) is not int:
        return done(UNCLASSIFIABLE_ST, "st_not_integer")
    if st == 2:
        return done(EXCLUDED_ST2, "st_2_excluded")
    if st != 1:
        return done(UNCLASSIFIABLE_ST, "st_unknown_value")
    if not isinstance(order, dict):
        return done(INVALID_PAYLOAD, "order_not_object")
    if not _timestamp(item.get("E")) or not _timestamp(order.get("T")):
        return done(INVALID_PAYLOAD, "invalid_timestamp")
    if not isinstance(order.get("s"), str) or not order["s"]:
        return done(INVALID_PAYLOAD, "invalid_symbol")
    if order.get("S") not in SIDES:
        return done(INVALID_PAYLOAD, "invalid_side")
    if not all(_decimal(order.get(k)) for k in _DECIMAL_FIELDS):
        return done(INVALID_PAYLOAD, "invalid_decimal_field")
    status = out["mapping"]["status"]
    if status == AMBIGUOUS:
        return done(IDENTITY_AMBIGUOUS, "symbol_mapping_ambiguous")
    if status != EXACT:
        return done(UNMAPPED, "symbol_not_in_metadata")
    return done(ACCEPTED, "st_1_valid")


def classify_frame(raw: bytes, symbols: SymbolMap) -> dict:
    """Frame body stored beside the raw bytes. Deterministic in (raw, symbols)."""
    base = {"parser_version": PARSER_VERSION, "mapping_sha256": symbols.raw_sha256}
    try:
        payload = strict_json(raw)
    except (UnicodeDecodeError, ValueError):
        return {**base, "payload_kind": "malformed", "items": [
            {"index": 0, "disposition": MALFORMED_FRAME, "reason": "not_strict_json",
             "s": None, "mapping": symbols.resolve(None)}]}
    if isinstance(payload, list):
        if not payload:
            return {**base, "payload_kind": "array", "items": [
                {"index": 0, "disposition": MALFORMED_FRAME, "reason": "empty_array",
                 "s": None, "mapping": symbols.resolve(None)}]}
        return {**base, "payload_kind": "array",
                "items": [classify_item(x, i, symbols) for i, x in enumerate(payload)]}
    return {**base, "payload_kind": "object", "items": [classify_item(payload, 0, symbols)]}
