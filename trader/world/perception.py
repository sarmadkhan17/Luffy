"""Versioned perception transforms from a WorldState into one derived Observation.

A ``PerceptionSpec`` names exact input coordinates (kind, timeframe, horizon)
under unique aliases and declares the output coordinates. ``perceive``
resolves each input with ``WorldState.get_one``, hands the transform a
read-only alias mapping, and wraps the returned ``Measurement`` in an ordinary
``Observation`` whose ``source_ref`` hashes the spec, cut and full input
records. Time is conservative: event time is the latest input event, capture
and availability are the state cut, and availability is only asserted when
every input's availability is known.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .observation import Observation, Quality, _text, _timestamp
from .state import WorldState


SCHEMA_VERSION = "world.perception.v1"


class PerceptionInputError(LookupError):
    """An input requirement did not resolve to exactly one observation."""

    def __init__(self, alias: str, requirement: InputRequirement, found: int) -> None:
        self.alias = alias
        self.requirement = requirement
        self.found = found
        super().__init__(
            f"input {alias!r} ({requirement.kind}/{requirement.timeframe}/{requirement.horizon}) "
            f"expected exactly one observation, found {found}")


class MissingInputError(PerceptionInputError):
    """No observation matched the requirement's exact coordinates."""


class AmbiguousInputError(PerceptionInputError):
    """Several observations matched the requirement's exact coordinates."""


@dataclass(frozen=True, slots=True)
class InputRequirement:
    """Exact coordinates of one input; ``horizon=None`` means no horizon."""

    alias: str
    kind: str
    timeframe: str
    horizon: str | None = None

    def __post_init__(self) -> None:
        for name in ("alias", "kind", "timeframe"):
            _text(getattr(self, name), name)
        if self.horizon is not None:
            _text(self.horizon, "horizon")

    def to_dict(self) -> dict[str, Any]:
        return {"alias": self.alias, "kind": self.kind,
                "timeframe": self.timeframe, "horizon": self.horizon}


@dataclass(frozen=True, slots=True)
class Measurement:
    """The small payload a transform returns; wrapped into an Observation by ``perceive``."""

    value: Any
    quality: Quality = Quality.VALID
    confidence: float | None = None
    uncertainty: Mapping[str, Any] = field(default_factory=dict)


# Implementations must be pure: use only the supplied immutable inputs, with
# no clock, mutable external state, random source, or additional market data.
Transform = Callable[[Mapping[str, Observation]], Measurement]


@dataclass(frozen=True, slots=True)
class PerceptionSpec:
    name: str
    version: str
    inputs: tuple[InputRequirement, ...]
    output_kind: str
    output_timeframe: str
    output_unit: str | None
    transform: Transform = field(compare=False, repr=False)
    output_horizon: str | None = None
    output_max_age_ms: int | None = None

    def __post_init__(self) -> None:
        for name in ("name", "version", "output_kind", "output_timeframe"):
            _text(getattr(self, name), name)
        for name in ("output_unit", "output_horizon"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        _timestamp(self.output_max_age_ms, "output_max_age_ms", optional=True)
        if not isinstance(self.inputs, tuple) or not self.inputs:
            raise ValueError("inputs must be a nonempty tuple of InputRequirement")
        for req in self.inputs:
            if not isinstance(req, InputRequirement):
                raise TypeError(f"inputs must be InputRequirement, got {type(req).__name__}")
        aliases = [req.alias for req in self.inputs]
        if len(set(aliases)) != len(aliases):
            raise ValueError("input aliases must be unique")
        if not callable(self.transform):
            raise TypeError("transform must be callable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "inputs": [req.to_dict() for req in self.inputs],
            "output_kind": self.output_kind,
            "output_timeframe": self.output_timeframe,
            "output_horizon": self.output_horizon,
            "output_unit": self.output_unit,
            "output_max_age_ms": self.output_max_age_ms,
        }


def _canonical(record: Any) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def resolve_inputs(spec: PerceptionSpec, state: WorldState) -> Mapping[str, Observation]:
    """Resolve every requirement to exactly one observation, as a read-only alias map."""
    resolved: dict[str, Observation] = {}
    for req in spec.inputs:
        try:
            resolved[req.alias] = state.get_one(req.kind, timeframe=req.timeframe,
                                                horizon=req.horizon)
        except LookupError:
            found = len(state.get_observations(req.kind, timeframe=req.timeframe,
                                               horizon=req.horizon))
            error = MissingInputError if found == 0 else AmbiguousInputError
            raise error(req.alias, req, found) from None
    return MappingProxyType(resolved)


def provenance_ref(spec: PerceptionSpec, state: WorldState,
                   inputs: Mapping[str, Observation]) -> str:
    """Canonical, inspectable lineage with hashes of exact input records."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "spec": spec.to_dict(),
        "instrument": state.instrument,
        "as_of_ms": state.as_of_ms,
        "inputs": [
            {"alias": alias, "observation_id": inputs[alias].observation_id,
             "record_sha256": sha256(inputs[alias].to_json().encode("utf-8")).hexdigest()}
            for alias in sorted(inputs)
        ],
    }
    return f"{SCHEMA_VERSION}:{_canonical(record)}"


def perceive(spec: PerceptionSpec, state: WorldState) -> Observation:
    """Apply ``spec`` to ``state`` at its cut and return one derived Observation."""
    if not isinstance(spec, PerceptionSpec):
        raise TypeError("spec must be a PerceptionSpec")
    if not isinstance(state, WorldState):
        raise TypeError("state must be a WorldState")
    inputs = resolve_inputs(spec, state)
    measurement = spec.transform(inputs)
    if not isinstance(measurement, Measurement):
        raise TypeError(f"transform must return Measurement, got {type(measurement).__name__}")
    if not isinstance(measurement.quality, Quality):
        raise ValueError("measurement quality must be a Quality state")

    availability_known = all(obs.available_at_ms is not None for obs in inputs.values())
    if measurement.quality is Quality.VALID and not availability_known:
        raise ValueError("VALID perception requires known availability for every input")

    return Observation(
        instrument=state.instrument,
        timestamp_ms=max(obs.timestamp_ms for obs in inputs.values()),
        observed_at_ms=state.as_of_ms,
        available_at_ms=state.as_of_ms if availability_known else None,
        timeframe=spec.output_timeframe,
        horizon=spec.output_horizon,
        kind=spec.output_kind,
        value=measurement.value,
        unit=spec.output_unit,
        max_age_ms=spec.output_max_age_ms,
        source=f"perception:{spec.name}",
        source_ref=provenance_ref(spec, state, inputs),
        quality=measurement.quality,
        confidence=measurement.confidence,
        uncertainty=measurement.uncertainty,
        transform_version=spec.version,
    )
