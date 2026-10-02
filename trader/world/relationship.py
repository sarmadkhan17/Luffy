"""Point-in-time claims about ordered edges between WorldModel scopes."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Mapping

from .model import Horizon, Scope
from .observation import Observation, Quality, _freeze, _plain, _text, _timestamp


SCHEMA_VERSION = "world.relationship.v1"


def _canonical(record: Any) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class RelationshipCoordinate:
    source: Scope
    target: Scope
    kind: str
    horizon: Horizon

    def __post_init__(self) -> None:
        if not isinstance(self.source, Scope) or not isinstance(self.target, Scope):
            raise TypeError("relationship endpoints must be Scope")
        _text(self.kind, "kind")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source.to_dict(), "target": self.target.to_dict(),
                "kind": self.kind, "horizon": self.horizon.value}


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    observation_id: str
    record_sha256: str
    observed_at_ms: int
    available_at_ms: int | None

    @classmethod
    def from_observation(cls, observation: Observation) -> EvidenceRef:
        if not isinstance(observation, Observation):
            raise TypeError("evidence must contain only Observation")
        return cls(observation.observation_id,
                   sha256(observation.to_json().encode("utf-8")).hexdigest(),
                   observation.observed_at_ms, observation.available_at_ms)

    def to_dict(self) -> dict[str, Any]:
        return {"observation_id": self.observation_id,
                "record_sha256": self.record_sha256,
                "observed_at_ms": self.observed_at_ms,
                "available_at_ms": self.available_at_ms}


@dataclass(frozen=True, slots=True)
class RelationshipState:
    coordinate: RelationshipCoordinate
    as_of_ms: int
    value: Any
    quality: Quality
    source: str
    source_ref: str
    evidence: tuple[Observation, ...]
    confidence: float | None = None
    uncertainty: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    evidence_refs: tuple[EvidenceRef, ...] = field(init=False)
    relationship_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.coordinate, RelationshipCoordinate):
            raise TypeError("coordinate must be RelationshipCoordinate")
        _timestamp(self.as_of_ms, "as_of_ms")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported relationship schema version")
        if not isinstance(self.quality, Quality):
            raise TypeError("quality must be Quality")
        _text(self.source, "source")
        _text(self.source_ref, "source_ref")
        if self.confidence is not None and (type(self.confidence) not in (int, float)
                                            or not math.isfinite(self.confidence)
                                            or not 0 <= self.confidence <= 1):
            raise ValueError("confidence must be finite and between 0 and 1")
        if not isinstance(self.uncertainty, Mapping):
            raise TypeError("uncertainty must be a JSON object")
        if isinstance(self.evidence, (str, bytes)) or not isinstance(self.evidence, Iterable):
            raise TypeError("evidence must be an iterable of Observation")
        observations = tuple(self.evidence)
        if not observations:
            raise ValueError("relationship requires evidence")
        for observation in observations:
            if not isinstance(observation, Observation):
                raise TypeError("evidence must contain only Observation")
            if observation.observed_at_ms > self.as_of_ms or observation.timestamp_ms > self.as_of_ms:
                raise ValueError("future evidence was not captured at relationship cut")
            if observation.available_at_ms is not None and observation.available_at_ms > self.as_of_ms:
                raise ValueError("future evidence was not available at relationship cut")
        observations = tuple(sorted(observations, key=lambda item: item.to_json()))
        refs = tuple(EvidenceRef.from_observation(item) for item in observations)
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate exact evidence record")
        if self.quality is Quality.VALID and any(ref.available_at_ms is None for ref in refs):
            raise ValueError("VALID relationship requires known evidence availability")
        if self.quality is Quality.VALID and self.value is None:
            raise ValueError("VALID relationship requires a value")
        if self.quality is Quality.MISSING and self.value is not None:
            raise ValueError("MISSING relationship cannot carry a value")
        object.__setattr__(self, "evidence", observations)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "uncertainty", _freeze(self.uncertainty))
        digest = sha256(_canonical(self._identity_record()).encode("utf-8")).hexdigest()
        object.__setattr__(self, "relationship_id", f"{SCHEMA_VERSION}:{digest}")

    def _identity_record(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version,
                "coordinate": self.coordinate.to_dict(), "as_of_ms": self.as_of_ms,
                "value": _plain(self.value), "quality": self.quality.value,
                "confidence": self.confidence, "uncertainty": _plain(self.uncertainty),
                "source": self.source, "source_ref": self.source_ref,
                "evidence": [ref.to_dict() for ref in self.evidence_refs]}

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity_record(), "relationship_id": self.relationship_id}

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True, slots=True)
class RelationshipCollection:
    as_of_ms: int
    relationships: tuple[RelationshipState, ...] = ()

    def __post_init__(self) -> None:
        _timestamp(self.as_of_ms, "as_of_ms")
        if isinstance(self.relationships, (str, bytes)) or not isinstance(self.relationships, Iterable):
            raise TypeError("relationships must be an iterable of RelationshipState")
        items = tuple(self.relationships)
        if any(not isinstance(item, RelationshipState) for item in items):
            raise TypeError("relationships must contain only RelationshipState")
        if any(item.as_of_ms != self.as_of_ms for item in items):
            raise ValueError("relationship as_of_ms does not match collection cut")
        if len({item.relationship_id for item in items}) != len(items):
            raise ValueError("duplicate exact relationship identity")
        object.__setattr__(self, "relationships",
                           tuple(sorted(items, key=lambda item: item.to_json())))

    def query(self, *, source: Scope | None = None, target: Scope | None = None,
              kind: str | None = None, horizon: Horizon | None = None) -> tuple[RelationshipState, ...]:
        if source is not None and not isinstance(source, Scope):
            raise TypeError("source must be Scope")
        if target is not None and not isinstance(target, Scope):
            raise TypeError("target must be Scope")
        if kind is not None:
            _text(kind, "kind")
        if horizon is not None and not isinstance(horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        return tuple(item for item in self.relationships
                     if (source is None or item.coordinate.source == source)
                     and (target is None or item.coordinate.target == target)
                     and (kind is None or item.coordinate.kind == kind)
                     and (horizon is None or item.coordinate.horizon == horizon))

    def get_one(self, *, source: Scope | None = None, target: Scope | None = None,
                kind: str | None = None, horizon: Horizon | None = None) -> RelationshipState:
        matches = self.query(source=source, target=target, kind=kind, horizon=horizon)
        if len(matches) != 1:
            raise LookupError(f"expected exactly one relationship, found {len(matches)}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {"as_of_ms": self.as_of_ms,
                "relationships": [item.to_dict() for item in self.relationships]}

    def to_json(self) -> str:
        return _canonical(self.to_dict())
