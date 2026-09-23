"""Immutable observations of connected instrument capability, without trade authority.

An account eligibility value is a per-symbol claim. Venue listing, account-wide
permission and metadata coverage are separate observations and never imply it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from .types import MarketType


SCHEMA_VERSION = 1


class AssetClass(str, Enum):
    UNKNOWN = "UNKNOWN"


class Eligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class EligibilityBasisKind(str, Enum):
    """Only evidence with explicit per-symbol account semantics qualifies."""

    ACCOUNT_SYMBOL_PERMISSION = "ACCOUNT_SYMBOL_PERMISSION"
    ACCOUNT_SYMBOL_REFUSAL = "ACCOUNT_SYMBOL_REFUSAL"


class Presence(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class AccountTrading(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class Capability(str, Enum):
    PROVEN = "PROVEN"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InstrumentId:
    venue: str
    market_type: MarketType
    venue_symbol: str

    def __post_init__(self) -> None:
        if not self.venue or not self.venue_symbol or ":" in self.venue or ":" in self.venue_symbol:
            raise ValueError("venue and venue_symbol must be nonempty and colon-free")

    @property
    def value(self) -> str:
        return f"{self.venue}:{self.market_type.value}:{self.venue_symbol}"


@dataclass(frozen=True)
class EligibilityBasis:
    kind: EligibilityBasisKind
    instrument_id: InstrumentId
    evidence_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EligibilityBasisKind) or not isinstance(self.instrument_id, InstrumentId):
            raise ValueError("typed per-symbol eligibility basis required")
        if not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip():
            raise ValueError("authoritative eligibility evidence reference required")


@dataclass(frozen=True)
class OrderConstraints:
    price_tick: str | None = None
    quantity_step: str | None = None
    minimum_quantity: str | None = None
    minimum_notional: str | None = None
    price_precision: int | None = None
    amount_precision: int | None = None


@dataclass(frozen=True)
class InstrumentRecord:
    instrument_id: InstrumentId
    asset_class: AssetClass
    contract_type: str | None
    base_asset: str
    quote_asset: str
    settlement_asset: str | None
    contract_multiplier: str | None
    delivery_ms: int | None
    venue_listing: Presence
    venue_status: str
    onboard_ms: int | None
    constraints: OrderConstraints
    shortability: Capability
    account_eligibility: Eligibility
    account_state_ref: str
    symbol_config: Presence
    leverage_bracket: Presence
    data_availability: Presence
    observed_at_ms: int
    source: str
    evidence: tuple[str, ...] = ()
    venue_underlying_type: str | None = None
    eligibility_basis: EligibilityBasis | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.observed_at_ms < 0:
            raise ValueError("observed_at_ms must be nonnegative")
        if not self.source or not self.account_state_ref:
            raise ValueError("source and account_state_ref are required")
        if not isinstance(self.asset_class, AssetClass) or not isinstance(self.account_eligibility, Eligibility):
            raise ValueError("typed asset class and eligibility required")
        basis = self.eligibility_basis
        if self.account_eligibility is Eligibility.UNKNOWN:
            if basis is not None:
                raise ValueError("UNKNOWN eligibility cannot carry a permission basis")
        else:
            if not isinstance(basis, EligibilityBasis) or basis.instrument_id != self.instrument_id:
                raise ValueError("authoritative per-symbol eligibility basis required")
            required = (EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION
                        if self.account_eligibility is Eligibility.ELIGIBLE
                        else EligibilityBasisKind.ACCOUNT_SYMBOL_REFUSAL)
            if basis.kind is not required:
                raise ValueError("eligibility basis does not match state")
        object.__setattr__(self, "evidence", tuple(sorted(self.evidence)))


@dataclass(frozen=True)
class RegistrySnapshot:
    as_of_ms: int
    account_scope: str
    account_trading: AccountTrading
    records: tuple[InstrumentRecord, ...]
    source: str
    schema_version: int = SCHEMA_VERSION
    snapshot_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.as_of_ms < 0 or not self.account_scope or not self.source:
            raise ValueError("as_of_ms, account_scope and source are required")
        records = tuple(sorted(self.records, key=lambda r: r.instrument_id.value))
        ids = [r.instrument_id for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate canonical instrument identity")
        for record in records:
            if record.observed_at_ms > self.as_of_ms:
                raise ValueError("observation later than snapshot")
            if record.account_state_ref != self.account_scope:
                raise ValueError("account state reference mismatch")
            if self.account_trading is AccountTrading.DISABLED and record.account_eligibility is Eligibility.ELIGIBLE:
                raise ValueError("disabled account cannot have eligible instruments")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "snapshot_id", hashlib.sha256(self.canonical_json().encode()).hexdigest())

    def canonical_json(self) -> str:
        from dataclasses import asdict

        payload = {"schema_version": self.schema_version, "as_of_ms": self.as_of_ms,
                   "account_scope": self.account_scope, "account_trading": self.account_trading.value,
                   "source": self.source, "records": [asdict(r) for r in self.records]}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def all_instruments(self) -> tuple[InstrumentRecord, ...]:
        return self.records

    def venue_active(self) -> tuple[InstrumentRecord, ...]:
        return tuple(r for r in self.records if r.venue_status == "TRADING")

    def by_market_type(self, market_type: MarketType) -> tuple[InstrumentRecord, ...]:
        return tuple(r for r in self.records if r.instrument_id.market_type is market_type)

    def by_account_eligibility(self, state: Eligibility) -> tuple[InstrumentRecord, ...]:
        return tuple(r for r in self.records if r.account_eligibility is state)

    def get(self, instrument_id: InstrumentId) -> InstrumentRecord | None:
        return next((r for r in self.records if r.instrument_id == instrument_id), None)
