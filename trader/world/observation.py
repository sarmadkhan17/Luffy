"""Immutable, JSON-safe observations for future world-state consumers.

All times are UTC epoch milliseconds. ``timestamp_ms`` is the event time;
``available_at_ms`` is when the value first became knowable, if established;
``observed_at_ms`` is when LUFFY captured it. Consumers must check both
availability and freshness at their own decision time.
"""
from __future__ import annotations

import json
import math
from hashlib import sha256
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = "world.observation.v1"


class Quality(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    MISSING = "MISSING"
    SUSPECT = "SUSPECT"
    REPAIRED = "REPAIRED"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"


def _timestamp(value: int | None, name: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative UTC epoch millisecond integer")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _freeze(value: Any) -> Any:
    """Copy JSON data into recursively immutable containers; reject frames/NaN."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError("observation data must be finite")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("observation object keys must be strings")
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    raise TypeError(f"observation data must be JSON-compatible, got {type(value).__name__}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class ObservationIdentity:
    """Stable event and provenance coordinates, independent of measured payload."""

    instrument: str
    timestamp_ms: int
    timeframe: str
    horizon: str | None
    kind: str
    source: str
    source_ref: str
    transform_version: str | None

    def __post_init__(self) -> None:
        for name in ("instrument", "timeframe", "kind", "source", "source_ref"):
            _text(getattr(self, name), name)
        _timestamp(self.timestamp_ms, "timestamp_ms")
        for name in ("horizon", "transform_version"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ObservationIdentity:
        """Recreate identity from serialized Observation fields, including replay loads."""
        return cls(**{name: record[name] for name in (
            "instrument", "timestamp_ms", "timeframe", "horizon", "kind",
            "source", "source_ref", "transform_version",
        )})

    def to_id(self) -> str:
        fields = (
            self.instrument, self.timestamp_ms, self.timeframe, self.horizon,
            self.kind, self.source, self.source_ref, self.transform_version,
        )
        encoded = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return f"{SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class Observation:
    instrument: str
    timestamp_ms: int
    observed_at_ms: int
    timeframe: str
    kind: str
    value: Any
    source: str
    source_ref: str
    quality: Quality
    available_at_ms: int | None = None
    source_timestamp_ms: int | None = None
    horizon: str | None = None
    unit: str | None = None
    max_age_ms: int | None = None
    confidence: float | None = None
    uncertainty: Mapping[str, Any] = field(default_factory=dict)
    transform_version: str | None = None
    schema_version: str = SCHEMA_VERSION
    observation_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("instrument", "timeframe", "kind", "source", "source_ref"):
            _text(getattr(self, name), name)
        for name in ("timestamp_ms", "observed_at_ms"):
            _timestamp(getattr(self, name), name)
        for name in ("available_at_ms", "source_timestamp_ms", "max_age_ms"):
            _timestamp(getattr(self, name), name, optional=True)
        for name in ("horizon", "unit", "transform_version"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported observation schema version")
        if not isinstance(self.quality, Quality):
            raise ValueError("quality must be a Quality state")
        if self.timestamp_ms > self.observed_at_ms:
            raise ValueError("event time cannot follow observation time")
        if self.source_timestamp_ms is not None and self.source_timestamp_ms > self.observed_at_ms:
            raise ValueError("source time cannot follow observation time")
        if self.available_at_ms is not None and self.available_at_ms < self.timestamp_ms:
            raise ValueError("availability cannot precede event time")
        if self.available_at_ms is not None and self.available_at_ms > self.observed_at_ms:
            raise ValueError("availability cannot follow observation time")
        if self.quality is Quality.VALID and self.available_at_ms is None:
            raise ValueError("VALID requires established availability")
        if self.quality is Quality.MISSING and self.value is not None:
            raise ValueError("MISSING cannot carry a value")
        if self.quality is Quality.VALID and self.value is None:
            raise ValueError("VALID requires a value")
        if self.confidence is not None:
            if type(self.confidence) not in (int, float) or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
                raise ValueError("confidence must be finite and between 0 and 1")
        if not isinstance(self.uncertainty, Mapping):
            raise TypeError("uncertainty must be a JSON object")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "uncertainty", _freeze(self.uncertainty))
        identity_fields = {
            name: getattr(self, name) for name in ObservationIdentity.__dataclass_fields__
        }
        object.__setattr__(self, "observation_id", ObservationIdentity.from_record(identity_fields).to_id())

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Observation:
        """Reload a serialized observation, refusing contradictory stored identity."""
        fields = dict(record)
        stored_id = fields.pop("observation_id")
        fields["quality"] = Quality(fields["quality"])
        observation = cls(**fields)
        if stored_id != observation.observation_id:
            raise ValueError("stored observation_id does not match observation identity")
        return observation

    def age_ms(self, as_of_ms: int) -> int:
        """Age of the underlying event at a specified replay/decision cut."""
        _timestamp(as_of_ms, "as_of_ms")
        if as_of_ms < self.observed_at_ms:
            raise ValueError("observation was not yet captured at this cut")
        return as_of_ms - self.timestamp_ms

    def is_fresh_at(self, as_of_ms: int) -> bool:
        """Conservative eligibility: known, valid, and within the age limit."""
        _timestamp(as_of_ms, "as_of_ms")
        return (self.quality is Quality.VALID
                and self.available_at_ms is not None
                and self.available_at_ms <= as_of_ms
                and self.observed_at_ms <= as_of_ms
                and self.max_age_ms is not None
                and as_of_ms - self.timestamp_ms <= self.max_age_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "instrument": self.instrument,
            "timestamp_ms": self.timestamp_ms,
            "observed_at_ms": self.observed_at_ms,
            "available_at_ms": self.available_at_ms,
            "source_timestamp_ms": self.source_timestamp_ms,
            "timeframe": self.timeframe,
            "horizon": self.horizon,
            "kind": self.kind,
            "value": _plain(self.value),
            "unit": self.unit,
            "source": self.source,
            "source_ref": self.source_ref,
            "quality": self.quality.value,
            "max_age_ms": self.max_age_ms,
            "confidence": self.confidence,
            "uncertainty": _plain(self.uncertainty),
            "transform_version": self.transform_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class SnapshotObservation:
    """A narrow, read-only bridge for the legacy in-memory Snapshot price."""

    @staticmethod
    def price(snapshot: Any, *, timeframe: str, source_ref: str) -> Observation:
        from datetime import datetime, timezone

        captured = datetime.fromisoformat(snapshot.ts.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            raise ValueError("Snapshot.ts must include a timezone")
        observed_ms = int(captured.astimezone(timezone.utc).timestamp() * 1000)
        return Observation(
            instrument=snapshot.symbol,
            timestamp_ms=observed_ms,
            observed_at_ms=observed_ms,
            timeframe=timeframe,
            kind="price",
            value=snapshot.price,
            unit="quote_currency",
            source="legacy_snapshot",
            source_ref=source_ref,
            quality=Quality.SUSPECT,
            uncertainty={"reason": "source event and availability timestamps absent from Snapshot"},
        )
