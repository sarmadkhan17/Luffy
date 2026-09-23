"""Pure choice of one supplemental Attention observation symbol per cycle.

Reads an injected, immutable `RegistrySnapshot` and nothing else: no network,
no Kernel, no journal, no snapshot store. It chooses at most one venue-active
instrument that the full strategy scan does not already cover, maps it to the
supplemental fetch symbol, and returns a rotating cursor for the caller to
feed into the next call. The caller advances to `cursor_after` whether or not
the later fetch succeeds, so a failing symbol cannot starve the rest.

Observation is not permission. Account eligibility (UNKNOWN, ELIGIBLE or
INELIGIBLE), account-wide canTrade and metadata coverage are carried as
recorded state and never gate, rank or authorise anything.

Replay is limited to: the same snapshot, cycle cut, freshness limit, strategy
scan and cursor produce the same `Selection`. Durable replay needs a snapshot
store, which does not exist yet.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from trader.core.instrument_registry import (
    InstrumentId, InstrumentRecord, Presence, RegistrySnapshot,
)
from trader.core.types import MarketType

RULE_VERSION = "registry-attention-selector/1"

# snapshot-level outcomes
SNAPSHOT_FUTURE = "snapshot_future"
SNAPSHOT_STALE = "snapshot_stale"
SELECTED = "selected"
NOTHING_SELECTABLE = "nothing_selectable"

# observation predicate exclusions
VENUE_LISTING_ABSENT = "venue_listing_absent"
VENUE_LISTING_UNKNOWN = "venue_listing_unknown"
VENUE_STATUS_NOT_TRADING = "venue_status_not_trading"

# supplemental transport mapping refusals, checked in this order
VENUE_NOT_BINANCE_USDM = "venue_not_binance_usdm"
MARKET_TYPE_NOT_FUTURES = "market_type_not_futures"
CONTRACT_NOT_PERPETUAL = "contract_type_not_perpetual"
QUOTE_NOT_USDT = "quote_asset_not_usdt"
SETTLEMENT_NOT_USDT = "settlement_asset_not_usdt"
BASE_ASSET_INVALID = "base_asset_invalid"
VENUE_SYMBOL_BASE_MISMATCH = "venue_symbol_base_mismatch"

ALREADY_IN_STRATEGY_SCAN = "already_in_strategy_scan"

VENUE = "binance_usdm"
_BASE = re.compile(r"[A-Z0-9]{1,24}")
#: the only scan spelling that proves USD-M linear-perpetual coverage here:
#: ccxt's unified futures form 'BASE/USDT:USDT'. A bare 'BASE/USDT' may be
#: spot and this layer does not know the caller's market mode, so it stays
#: unmatched; a caller that knows its scan is USD-M must convert it first.
_SCAN_SYMBOL = re.compile(r"[A-Z0-9]{1,24}/USDT:USDT")


class SelectionRefused(ValueError):
    """The inputs cannot be judged at all; nothing is selected."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class Exclusion:
    instrument_id: str
    reason: str


@dataclass(frozen=True)
class Selection:
    rule_version: str
    outcome: str
    cycle_as_of_ms: int
    snapshot_id: str
    snapshot_as_of_ms: int
    registry_age_ms: int
    max_snapshot_age_ms: int
    #: sorted canonical IDs passing the observation predicate
    candidate_ids: tuple[str, ...]
    #: predicate exclusions (all records) then traversal exclusions in visit order
    exclusions: tuple[Exclusion, ...]
    selected_id: str | None
    selected_symbol: str | None
    #: recorded state of the selected record; never a permission
    selected_account_eligibility: str | None
    cursor_before: str | None
    cursor_after: str | None
    #: sorted strategy-scan symbols outside the unambiguous USD-M spelling
    #: 'BASE/USDT:USDT' (bare 'BASE/USDT' included); they cover no registry
    #: instrument
    unmatched_scan_symbols: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"rule_version": self.rule_version, "outcome": self.outcome,
                "cycle_as_of_ms": self.cycle_as_of_ms,
                "snapshot_id": self.snapshot_id,
                "snapshot_as_of_ms": self.snapshot_as_of_ms,
                "registry_age_ms": self.registry_age_ms,
                "max_snapshot_age_ms": self.max_snapshot_age_ms,
                "candidate_ids": list(self.candidate_ids),
                "exclusions": [[e.instrument_id, e.reason] for e in self.exclusions],
                "selected_id": self.selected_id,
                "selected_symbol": self.selected_symbol,
                "selected_account_eligibility": self.selected_account_eligibility,
                "cursor_before": self.cursor_before,
                "cursor_after": self.cursor_after,
                "unmatched_scan_symbols": list(self.unmatched_scan_symbols)}

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def selection_id(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def observation_exclusion(record: InstrumentRecord) -> str | None:
    """Why a record cannot be observed at all, or None. Listing and venue
    status only; account and metadata fields are deliberately not read."""
    if record.venue_listing is not Presence.PRESENT:
        return (VENUE_LISTING_ABSENT if record.venue_listing is Presence.ABSENT
                else VENUE_LISTING_UNKNOWN)
    if record.venue_status != "TRADING":
        return VENUE_STATUS_NOT_TRADING
    return None


def supplemental_symbol(record: InstrumentRecord) -> tuple[str | None, str | None]:
    """(mapped symbol, None) when every mapping fact is proven, else
    (None, refusal). The base comes from the record's own base_asset and is
    checked against the venue symbol; it is never derived from it."""
    iid = record.instrument_id
    if iid.venue != VENUE:
        return None, VENUE_NOT_BINANCE_USDM
    if iid.market_type is not MarketType.FUTURES:
        return None, MARKET_TYPE_NOT_FUTURES
    if record.contract_type != "PERPETUAL":
        return None, CONTRACT_NOT_PERPETUAL
    if record.quote_asset != "USDT":
        return None, QUOTE_NOT_USDT
    if record.settlement_asset != "USDT":
        return None, SETTLEMENT_NOT_USDT
    base = record.base_asset
    if not isinstance(base, str) or not _BASE.fullmatch(base):
        return None, BASE_ASSET_INVALID
    if iid.venue_symbol != base + "USDT":
        return None, VENUE_SYMBOL_BASE_MISMATCH
    return f"{base}/USDT:USDT", None


def scan_coverage(scan_symbols: Iterable[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """The comparison boundary between the strategy scan and the registry.

    Only the unambiguous USD-M futures spelling 'BASE/USDT:USDT' covers a
    mapped registry instrument; every other spelling, including the bare
    'BASE/USDT' that may denote spot, is reported as unmatched and covers
    nothing. Registry identity is untouched and this says nothing about
    trading eligibility.
    """
    covered, unmatched = set(), set()
    for sym in scan_symbols:
        if _SCAN_SYMBOL.fullmatch(sym):
            covered.add(sym)
        else:
            unmatched.add(sym)
    return frozenset(covered), tuple(sorted(unmatched))


def _is_canonical_id(value: str) -> bool:
    """True when `value` is exactly what some `InstrumentId.value` produces.
    The instrument need not exist in any snapshot."""
    parts = value.split(":")
    if len(parts) != 3:
        return False
    try:
        iid = InstrumentId(parts[0], MarketType(parts[1]), parts[2])
    except ValueError:
        return False
    return iid.value == value


def _check_int(name: str, value, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SelectionRefused("invalid_input", f"{name} must be an int >= {minimum}")


def select(snapshot: RegistrySnapshot, *, cycle_as_of_ms: int, max_snapshot_age_ms: int,
           strategy_scan: Iterable[str], cursor_before: str | None) -> Selection:
    """Choose at most one supplemental observation candidate. Pure."""
    if not isinstance(snapshot, RegistrySnapshot):
        raise SelectionRefused("invalid_input", "snapshot must be a RegistrySnapshot")
    _check_int("cycle_as_of_ms", cycle_as_of_ms)
    _check_int("max_snapshot_age_ms", max_snapshot_age_ms)
    if isinstance(strategy_scan, (str, bytes)) or not isinstance(strategy_scan, Iterable):
        raise SelectionRefused("invalid_input", "strategy_scan must be a collection of symbols")
    scan = tuple(strategy_scan)
    if any(not isinstance(s, str) or not s for s in scan):
        raise SelectionRefused("invalid_input", "strategy_scan symbols must be nonempty strings")
    if cursor_before is not None and (not isinstance(cursor_before, str)
                                      or not _is_canonical_id(cursor_before)):
        raise SelectionRefused("invalid_input", "cursor_before must be a canonical ID or None")

    age = cycle_as_of_ms - snapshot.as_of_ms
    if age < 0:
        raise SelectionRefused(SNAPSHOT_FUTURE, f"snapshot {age}ms ahead of the cycle cut")
    covered, unmatched = scan_coverage(scan)
    common = dict(rule_version=RULE_VERSION, cycle_as_of_ms=cycle_as_of_ms,
                  snapshot_id=snapshot.snapshot_id, snapshot_as_of_ms=snapshot.as_of_ms,
                  registry_age_ms=age, max_snapshot_age_ms=max_snapshot_age_ms,
                  cursor_before=cursor_before, unmatched_scan_symbols=unmatched)
    if age > max_snapshot_age_ms:
        return Selection(outcome=SNAPSHOT_STALE, candidate_ids=(), exclusions=(),
                         selected_id=None, selected_symbol=None,
                         selected_account_eligibility=None,
                         cursor_after=cursor_before, **common)

    exclusions: list[Exclusion] = []
    candidates: list[InstrumentRecord] = []
    for record in snapshot.records:  # already sorted by canonical ID
        reason = observation_exclusion(record)
        if reason is None:
            candidates.append(record)
        else:
            exclusions.append(Exclusion(record.instrument_id.value, reason))
    ids = tuple(r.instrument_id.value for r in candidates)

    start = 0 if cursor_before is None else bisect.bisect_left(ids, cursor_before) % max(len(ids), 1)
    for step in range(len(ids)):
        i = (start + step) % len(ids)
        record = candidates[i]
        symbol, refusal = supplemental_symbol(record)
        if refusal is None and symbol in covered:
            refusal = ALREADY_IN_STRATEGY_SCAN
        if refusal is not None:
            exclusions.append(Exclusion(ids[i], refusal))
            continue
        return Selection(outcome=SELECTED, candidate_ids=ids, exclusions=tuple(exclusions),
                         selected_id=ids[i], selected_symbol=symbol,
                         selected_account_eligibility=record.account_eligibility.value,
                         cursor_after=ids[(i + 1) % len(ids)], **common)
    return Selection(outcome=NOTHING_SELECTABLE, candidate_ids=ids, exclusions=tuple(exclusions),
                     selected_id=None, selected_symbol=None, selected_account_eligibility=None,
                     cursor_after=cursor_before, **common)
