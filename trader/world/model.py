"""Immutable hierarchy and horizon index around point-in-time WorldStates.

Higher scopes are identity nodes only. Measurements remain in the accepted
instrument-specific WorldState and Observation contracts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Iterable

from .observation import Observation, _text, _timestamp
from .state import WorldState

if TYPE_CHECKING:
    from .claim import ClaimCollection, WorldClaim
    from .relationship import RelationshipCollection


SCHEMA_VERSION = "world.model.v1"


class ScopeLevel(str, Enum):
    GLOBAL = "GLOBAL"
    ASSET_CLASS = "ASSET_CLASS"
    GROUP = "GROUP"
    INSTRUMENT = "INSTRUMENT"


class Horizon(str, Enum):
    SHORT_TERM = "short-term"
    INTRADAY = "intraday"
    SWING = "swing"
    STRUCTURAL = "structural"


@dataclass(frozen=True, slots=True)
class Scope:
    level: ScopeLevel
    identifier: str

    def __post_init__(self) -> None:
        if not isinstance(self.level, ScopeLevel):
            raise TypeError("level must be a ScopeLevel")
        _text(self.identifier, "identifier")

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level.value, "identifier": self.identifier}


@dataclass(frozen=True, slots=True)
class HierarchyNode:
    scope: Scope
    parent: Scope | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, Scope):
            raise TypeError("scope must be a Scope")
        if self.parent is not None and not isinstance(self.parent, Scope):
            raise TypeError("parent must be a Scope or None")
        allowed = {
            ScopeLevel.GLOBAL: None,
            ScopeLevel.ASSET_CLASS: (ScopeLevel.GLOBAL,),
            ScopeLevel.GROUP: (ScopeLevel.ASSET_CLASS,),
            ScopeLevel.INSTRUMENT: (ScopeLevel.GROUP, ScopeLevel.ASSET_CLASS),
        }
        expected = allowed[self.scope.level]
        if expected is None:
            if self.parent is not None:
                raise ValueError("GLOBAL cannot have a parent")
        elif self.parent is None or self.parent.level not in expected:
            raise ValueError(f"invalid parent level for {self.scope.level.value}")

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope.to_dict(),
                "parent": None if self.parent is None else self.parent.to_dict()}


@dataclass(frozen=True, slots=True)
class WorldModel:
    as_of_ms: int
    nodes: tuple[HierarchyNode, ...]
    states: tuple[WorldState, ...] = ()
    horizons: tuple[Horizon, ...] = tuple(Horizon)
    schema_version: str = SCHEMA_VERSION
    relationships: RelationshipCollection | None = None
    claims: ClaimCollection | None = None
    model_id: str = field(init=False)

    def __post_init__(self) -> None:
        _timestamp(self.as_of_ms, "as_of_ms")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported world model schema version")
        nodes = _items(self.nodes, HierarchyNode, "nodes")
        states = _items(self.states, WorldState, "states")
        horizons = _items(self.horizons, Horizon, "horizons")
        if self.relationships is not None:
            from .relationship import RelationshipCollection
            if not isinstance(self.relationships, RelationshipCollection):
                raise TypeError("relationships must be RelationshipCollection or None")
            if self.relationships.as_of_ms != self.as_of_ms:
                raise ValueError("relationship collection cut does not match model cut")
        if self.claims is not None:
            from .claim import ClaimCollection
            if not isinstance(self.claims, ClaimCollection):
                raise TypeError("claims must be ClaimCollection or None")
            if self.claims.as_of_ms != self.as_of_ms:
                raise ValueError("claim collection cut does not match model cut")
        if not horizons or len(set(horizons)) != len(horizons):
            raise ValueError("horizons must be nonempty and unique")
        scopes = [node.scope for node in nodes]
        if len(set(scopes)) != len(scopes):
            raise ValueError("duplicate scope identity")
        scope_set = set(scopes)
        if self.relationships is not None:
            for relationship in self.relationships.relationships:
                coordinate = relationship.coordinate
                if coordinate.source not in scope_set or coordinate.target not in scope_set:
                    raise ValueError("relationship endpoint has no hierarchy node")
                if coordinate.horizon not in horizons:
                    raise ValueError("relationship horizon is not declared by model")
        if sum(scope.level is ScopeLevel.GLOBAL for scope in scopes) != 1:
            raise ValueError("exactly one GLOBAL node is required")
        for node in nodes:
            if node.parent is not None and node.parent not in scope_set:
                raise ValueError("hierarchy parent is absent")
        instrument_scopes = {scope.identifier for scope in scopes
                             if scope.level is ScopeLevel.INSTRUMENT}
        instruments = [state.instrument for state in states]
        if len(set(instruments)) != len(instruments):
            raise ValueError("duplicate instrument WorldState")
        for state in states:
            if state.as_of_ms != self.as_of_ms:
                raise ValueError("WorldState as_of_ms does not match model cut")
            if state.instrument not in instrument_scopes:
                raise ValueError("WorldState instrument has no hierarchy node")
            for observation in state.observations:
                if observation.horizon is not None and observation.horizon not in horizons:
                    raise ValueError("observation horizon is not declared by model")
        if self.claims is not None:
            from .claim import ClaimEvidenceRef
            present = {ClaimEvidenceRef.from_observation(obs)
                       for state in states for obs in state.observations}
            if self.relationships is not None:
                present.update(ClaimEvidenceRef.from_relationship(rel)
                               for rel in self.relationships.relationships)
            for claim in self.claims.claims:
                if claim.coordinate.scope not in scope_set:
                    raise ValueError("claim scope has no hierarchy node")
                if claim.coordinate.horizon not in horizons:
                    raise ValueError("claim horizon is not declared by model")
                if any(ref not in present for ref in
                       claim.supporting_evidence + claim.contradicting_evidence):
                    raise ValueError("claim evidence is not an exact record in model")
        nodes = tuple(sorted(nodes, key=lambda node: (
            node.scope.level.value, node.scope.identifier)))
        states = tuple(sorted(states, key=lambda state: state.instrument))
        horizons = tuple(sorted(horizons, key=lambda horizon: horizon.value))
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "horizons", horizons)
        encoded = self.to_json().encode("utf-8")
        object.__setattr__(self, "model_id", f"{SCHEMA_VERSION}:{sha256(encoded).hexdigest()}")

    def _node(self, scope: Scope) -> HierarchyNode:
        if not isinstance(scope, Scope):
            raise TypeError("scope must be a Scope")
        for node in self.nodes:
            if node.scope == scope:
                return node
        raise LookupError(f"unknown scope: {scope!r}")

    def parent(self, scope: Scope) -> Scope | None:
        return self._node(scope).parent

    def ancestors(self, scope: Scope) -> tuple[Scope, ...]:
        """Return parents from immediate parent through the global root."""
        result: list[Scope] = []
        current = self.parent(scope)
        while current is not None:
            result.append(current)
            current = self.parent(current)
        return tuple(result)

    def children(self, scope: Scope) -> tuple[Scope, ...]:
        self._node(scope)
        return tuple(node.scope for node in self.nodes if node.parent == scope)

    def get_state(self, scope: Scope) -> WorldState | None:
        self._node(scope)
        if scope.level is ScopeLevel.INSTRUMENT:
            return next((state for state in self.states
                         if state.instrument == scope.identifier), None)
        return None

    def get_observations(self, scope: Scope, horizon: Horizon,
                         *, kind: str | None = None) -> tuple[Observation, ...]:
        """Return all exact-scope, exact-horizon observations in canonical order."""
        if not isinstance(horizon, Horizon) or horizon not in self.horizons:
            raise ValueError("horizon must be a declared Horizon")
        if kind is not None:
            _text(kind, "kind")
        state = self.get_state(scope)
        if state is None:
            return ()
        return tuple(obs for obs in state.observations
                     if obs.horizon == horizon.value and (kind is None or obs.kind == kind))

    def get_one(self, scope: Scope, horizon: Horizon, *, kind: str) -> Observation:
        matches = self.get_observations(scope, horizon, kind=kind)
        if len(matches) != 1:
            raise LookupError(f"expected one observation, found {len(matches)}")
        return matches[0]

    def get_claims(self, scope: Scope, horizon: Horizon,
                   *, dimension: str | None = None) -> tuple[WorldClaim, ...]:
        self._node(scope)
        if not isinstance(horizon, Horizon) or horizon not in self.horizons:
            raise ValueError("horizon must be a declared Horizon")
        if self.claims is None:
            return ()
        return self.claims.query(scope=scope, horizon=horizon, dimension=dimension)

    def get_one_claim(self, scope: Scope, horizon: Horizon, *, dimension: str) -> WorldClaim:
        matches = self.get_claims(scope, horizon, dimension=dimension)
        if len(matches) != 1:
            raise LookupError(f"expected exactly one claim, found {len(matches)}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        record = {"schema_version": self.schema_version, "as_of_ms": self.as_of_ms,
                "horizons": [horizon.value for horizon in self.horizons],
                "nodes": [node.to_dict() for node in self.nodes],
                "states": [state.to_dict() for state in self.states]}
        if self.relationships is not None:
            record["relationships"] = self.relationships.to_dict()
        if self.claims is not None:
            record["claims"] = self.claims.to_dict()
        return record

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)


def _items(value: Iterable[Any], item_type: type, name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"{name} must be an iterable of {item_type.__name__}")
    items = tuple(value)
    if any(not isinstance(item, item_type) for item in items):
        raise TypeError(f"{name} must contain only {item_type.__name__}")
    return items
