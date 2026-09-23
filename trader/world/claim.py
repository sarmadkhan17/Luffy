"""Immutable world-state claims with exact, point-in-time evidence references."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Iterable, Mapping

from .model import Horizon, Scope
from .observation import Observation, Quality, _freeze, _plain, _text, _timestamp
from .relationship import RelationshipState


SCHEMA_VERSION = "world.claim.v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


class ClaimEvidenceType(str, Enum):
    OBSERVATION = "Observation"
    RELATIONSHIP_STATE = "RelationshipState"


@dataclass(frozen=True, slots=True)
class ClaimEvidenceRef:
    evidence_type: ClaimEvidenceType
    record_id: str
    record_sha256: str
    observed_at_ms: int
    available_at_ms: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_type, ClaimEvidenceType):
            raise TypeError("evidence_type must be ClaimEvidenceType")
        _text(self.record_id, "record_id")
        if (not isinstance(self.record_sha256, str) or len(self.record_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.record_sha256)):
            raise ValueError("record_sha256 must be a lowercase SHA-256 hex digest")
        _timestamp(self.observed_at_ms, "observed_at_ms")
        _timestamp(self.available_at_ms, "available_at_ms", optional=True)
        if self.available_at_ms is not None and self.available_at_ms > self.observed_at_ms:
            raise ValueError("availability cannot follow capture")

    @classmethod
    def from_observation(cls, observation: Observation) -> ClaimEvidenceRef:
        if not isinstance(observation, Observation):
            raise TypeError("expected Observation")
        return cls(ClaimEvidenceType.OBSERVATION, observation.observation_id,
                   sha256(observation.to_json().encode("utf-8")).hexdigest(),
                   observation.observed_at_ms, observation.available_at_ms)

    @classmethod
    def from_relationship(cls, relationship: RelationshipState) -> ClaimEvidenceRef:
        if not isinstance(relationship, RelationshipState):
            raise TypeError("expected RelationshipState")
        return cls(ClaimEvidenceType.RELATIONSHIP_STATE, relationship.relationship_id,
                   sha256(relationship.to_json().encode("utf-8")).hexdigest(),
                   relationship.as_of_ms, relationship.as_of_ms)

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_type": self.evidence_type.value, "record_id": self.record_id,
                "record_sha256": self.record_sha256,
                "observed_at_ms": self.observed_at_ms,
                "available_at_ms": self.available_at_ms}

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True, slots=True)
class ClaimCoordinate:
    scope: Scope
    horizon: Horizon
    dimension: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, Scope):
            raise TypeError("scope must be Scope")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        _text(self.dimension, "dimension")

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope.to_dict(), "horizon": self.horizon.value,
                "dimension": self.dimension}


def _evidence(value: Iterable[ClaimEvidenceRef], name: str) -> tuple[ClaimEvidenceRef, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"{name} must be an iterable of ClaimEvidenceRef")
    items = tuple(value)
    if any(not isinstance(item, ClaimEvidenceRef) for item in items):
        raise TypeError(f"{name} must contain only ClaimEvidenceRef")
    return tuple(sorted(items, key=lambda item: item.to_json()))


@dataclass(frozen=True, slots=True)
class WorldClaim:
    coordinate: ClaimCoordinate
    as_of_ms: int
    value: Any
    quality: Quality
    confidence: float | None
    uncertainty: Mapping[str, Any]
    supporting_evidence: tuple[ClaimEvidenceRef, ...]
    contradicting_evidence: tuple[ClaimEvidenceRef, ...]
    source: str
    source_ref: str
    schema_version: str = SCHEMA_VERSION
    claim_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.coordinate, ClaimCoordinate):
            raise TypeError("coordinate must be ClaimCoordinate")
        _timestamp(self.as_of_ms, "as_of_ms")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported claim schema version")
        if not isinstance(self.quality, Quality):
            raise TypeError("quality must be Quality")
        if self.confidence is not None and (type(self.confidence) not in (int, float)
                                            or not math.isfinite(self.confidence)
                                            or not 0 <= self.confidence <= 1):
            raise ValueError("confidence must be finite and between 0 and 1")
        if not isinstance(self.uncertainty, Mapping):
            raise TypeError("uncertainty must be a JSON object")
        _text(self.source, "source")
        _text(self.source_ref, "source_ref")
        supporting = _evidence(self.supporting_evidence, "supporting_evidence")
        contradicting = _evidence(self.contradicting_evidence, "contradicting_evidence")
        refs = supporting + contradicting
        if not refs:
            raise ValueError("claim requires evidence")
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate exact claim evidence")
        for ref in refs:
            if ref.observed_at_ms > self.as_of_ms or (ref.available_at_ms is not None
                                                       and ref.available_at_ms > self.as_of_ms):
                raise ValueError("future evidence at claim cut")
        if self.quality is Quality.VALID and any(ref.available_at_ms is None for ref in refs):
            raise ValueError("VALID claim requires known evidence availability")
        if self.quality is Quality.VALID and self.value is None:
            raise ValueError("VALID claim requires a value")
        if self.quality is Quality.MISSING and self.value is not None:
            raise ValueError("MISSING claim cannot carry a value")
        object.__setattr__(self, "supporting_evidence", supporting)
        object.__setattr__(self, "contradicting_evidence", contradicting)
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "uncertainty", _freeze(self.uncertainty))
        digest = sha256(_canonical(self._identity_record()).encode("utf-8")).hexdigest()
        object.__setattr__(self, "claim_id", f"{SCHEMA_VERSION}:{digest}")

    def _identity_record(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "coordinate": self.coordinate.to_dict(),
                "as_of_ms": self.as_of_ms, "value": _plain(self.value),
                "quality": self.quality.value, "confidence": self.confidence,
                "uncertainty": _plain(self.uncertainty), "source": self.source,
                "source_ref": self.source_ref,
                "supporting_evidence": [ref.to_dict() for ref in self.supporting_evidence],
                "contradicting_evidence": [ref.to_dict() for ref in self.contradicting_evidence]}

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity_record(), "claim_id": self.claim_id}

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True, slots=True)
class ClaimCollection:
    as_of_ms: int
    claims: tuple[WorldClaim, ...] = ()

    def __post_init__(self) -> None:
        _timestamp(self.as_of_ms, "as_of_ms")
        if isinstance(self.claims, (str, bytes)) or not isinstance(self.claims, Iterable):
            raise TypeError("claims must be an iterable of WorldClaim")
        items = tuple(self.claims)
        if any(not isinstance(item, WorldClaim) for item in items):
            raise TypeError("claims must contain only WorldClaim")
        if any(item.as_of_ms != self.as_of_ms for item in items):
            raise ValueError("claim cut does not match collection cut")
        if len({item.claim_id for item in items}) != len(items):
            raise ValueError("duplicate exact claim identity")
        object.__setattr__(self, "claims", tuple(sorted(items, key=lambda item: item.to_json())))

    def query(self, *, scope: Scope | None = None, horizon: Horizon | None = None,
              dimension: str | None = None) -> tuple[WorldClaim, ...]:
        if scope is not None and not isinstance(scope, Scope):
            raise TypeError("scope must be Scope")
        if horizon is not None and not isinstance(horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        if dimension is not None:
            _text(dimension, "dimension")
        return tuple(item for item in self.claims
                     if (scope is None or item.coordinate.scope == scope)
                     and (horizon is None or item.coordinate.horizon == horizon)
                     and (dimension is None or item.coordinate.dimension == dimension))

    def get_one(self, *, scope: Scope | None = None, horizon: Horizon | None = None,
                dimension: str | None = None) -> WorldClaim:
        matches = self.query(scope=scope, horizon=horizon, dimension=dimension)
        if len(matches) != 1:
            raise LookupError(f"expected exactly one claim, found {len(matches)}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {"as_of_ms": self.as_of_ms, "claims": [claim.to_dict() for claim in self.claims]}

    def to_json(self) -> str:
        return _canonical(self.to_dict())
