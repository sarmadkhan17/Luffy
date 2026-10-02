"""Immutable point-in-time composition of observations for one instrument.

A ``WorldState`` is what was captured for an instrument at an explicit cut
``as_of_ms`` (UTC epoch milliseconds). It holds observations exactly as
given: every quality state is preserved and duplicate coordinates are kept
as a multiset rather than silently overwritten. Interpretation (freshness,
conflict resolution, confidence) belongs to later consumers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable

from .observation import Observation, _text, _timestamp


SCHEMA_VERSION = "world.state.v1"

_ANY = object()


@dataclass(frozen=True, slots=True)
class WorldState:
    instrument: str
    as_of_ms: int
    observations: tuple[Observation, ...] = ()
    schema_version: str = SCHEMA_VERSION
    state_id: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.instrument, "instrument")
        _timestamp(self.as_of_ms, "as_of_ms")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported world state schema version")
        if isinstance(self.observations, (str, bytes)) or not isinstance(self.observations, Iterable):
            raise TypeError("observations must be an iterable of Observation")
        items = tuple(self.observations)
        for obs in items:
            if not isinstance(obs, Observation):
                raise TypeError(f"world state items must be Observation, got {type(obs).__name__}")
            if obs.instrument != self.instrument:
                raise ValueError("observation instrument does not match world state instrument")
            if obs.available_at_ms is not None and obs.available_at_ms > self.as_of_ms:
                raise ValueError("observation was not yet available at as_of_ms")
            if obs.observed_at_ms > self.as_of_ms:
                raise ValueError("observation was not yet captured at as_of_ms")
        # Canonical order by full serialized record: insertion order never
        # affects identity, even for equal IDs carrying different payloads.
        ordered = tuple(sorted(items, key=lambda obs: obs.to_json()))
        object.__setattr__(self, "observations", ordered)
        object.__setattr__(self, "state_id", self._compute_id())

    def _compute_id(self) -> str:
        record = {
            "schema_version": self.schema_version,
            "instrument": self.instrument,
            "as_of_ms": self.as_of_ms,
            "observations": [obs.to_dict() for obs in self.observations],
        }
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        return f"{SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"

    def get_observations(self, kind: str, *, timeframe: Any = _ANY,
                         horizon: Any = _ANY) -> tuple[Observation, ...]:
        """All matching observations in canonical order, duplicates included.

        Omitted ``timeframe``/``horizon`` match anything; an explicit
        ``horizon=None`` matches only observations without a horizon.
        """
        _text(kind, "kind")
        return tuple(
            obs for obs in self.observations
            if obs.kind == kind
            and (timeframe is _ANY or obs.timeframe == timeframe)
            and (horizon is _ANY or obs.horizon == horizon)
        )

    def get_one(self, kind: str, *, timeframe: Any = _ANY,
                horizon: Any = _ANY) -> Observation:
        """The single matching observation; raises LookupError on zero or several."""
        matches = self.get_observations(kind, timeframe=timeframe, horizon=horizon)
        if len(matches) != 1:
            raise LookupError(f"expected exactly one {kind!r} observation, found {len(matches)}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_id": self.state_id,
            "instrument": self.instrument,
            "as_of_ms": self.as_of_ms,
            "observations": [obs.to_dict() for obs in self.observations],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
