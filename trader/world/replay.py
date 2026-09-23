"""Exact, fail-closed reconstruction of the v1 historical WorldModel wire format.

The v1 model wire omits model_id, and relationship wires contain evidence
references rather than observations. A historical receipt must therefore supply
its stored model_id and any relationship evidence absent from model states.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping

from .claim import (ClaimCollection, ClaimCoordinate, ClaimEvidenceRef,
                    ClaimEvidenceType, WorldClaim, SCHEMA_VERSION as CLAIM_VERSION)
from .model import (HierarchyNode, Horizon, Scope, ScopeLevel, WorldModel,
                    SCHEMA_VERSION as MODEL_VERSION)
from .observation import Observation, Quality, _timestamp
from .relationship import (EvidenceRef, RelationshipCollection,
                           RelationshipCoordinate, RelationshipState,
                           SCHEMA_VERSION as RELATIONSHIP_VERSION)
from .state import WorldState, SCHEMA_VERSION as STATE_VERSION


RECORD_VERSION = "world.replay.record.v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _object(value: Any, fields: set[str], name: str, *, optional: set[str] = frozenset()) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not fields <= value.keys() or value.keys() - fields - optional:
        raise ValueError(f"invalid {name} fields")
    return value


def _version(record: Mapping[str, Any], expected: str) -> None:
    if record.get("schema_version") != expected:
        raise ValueError(f"unsupported historical schema version: {record.get('schema_version')!r}; expected {expected!r}")


def _identity(stored: str, derived: str, name: str) -> None:
    if stored != derived:
        raise ValueError(f"stored {name} does not match reconstructed content")


def _scope(value: Any) -> Scope:
    record = _object(value, {"level", "identifier"}, "scope")
    return Scope(ScopeLevel(record["level"]), record["identifier"])


def _node(value: Any) -> HierarchyNode:
    record = _object(value, {"scope", "parent"}, "hierarchy node")
    return HierarchyNode(_scope(record["scope"]),
                         None if record["parent"] is None else _scope(record["parent"]))


def _observation(value: Any) -> Observation:
    record = _object(value, set(Observation.__dataclass_fields__) - {"observation_id"}
                     | {"observation_id"}, "observation")
    return Observation.from_dict(record)


def _state(value: Any) -> WorldState:
    record = _object(value, {"schema_version", "state_id", "instrument", "as_of_ms", "observations"}, "world state")
    _version(record, STATE_VERSION)
    state = WorldState(record["instrument"], record["as_of_ms"],
                       tuple(_observation(item) for item in record["observations"]),
                       schema_version=record["schema_version"])
    _identity(record["state_id"], state.state_id, "state_id")
    return state


def _evidence_ref(value: Any) -> EvidenceRef:
    record = _object(value, {"observation_id", "record_sha256", "observed_at_ms", "available_at_ms"}, "relationship evidence ref")
    return EvidenceRef(**record)


def _relationship(value: Any, evidence: Mapping[EvidenceRef, Observation]) -> RelationshipState:
    record = _object(value, {"schema_version", "relationship_id", "coordinate", "as_of_ms", "value",
                             "quality", "confidence", "uncertainty", "source", "source_ref", "evidence"}, "relationship")
    _version(record, RELATIONSHIP_VERSION)
    coordinate = _object(record["coordinate"], {"source", "target", "kind", "horizon"}, "relationship coordinate")
    refs = tuple(_evidence_ref(item) for item in record["evidence"])
    try:
        observations = tuple(evidence[ref] for ref in refs)
    except KeyError as exc:
        raise ValueError("exact historical relationship evidence record is missing") from exc
    relationship = RelationshipState(
        RelationshipCoordinate(_scope(coordinate["source"]), _scope(coordinate["target"]),
                               coordinate["kind"], Horizon(coordinate["horizon"])),
        record["as_of_ms"], record["value"], Quality(record["quality"]),
        record["source"], record["source_ref"], observations,
        confidence=record["confidence"], uncertainty=record["uncertainty"],
        schema_version=record["schema_version"])
    if [ref.to_dict() for ref in relationship.evidence_refs] != record["evidence"]:
        raise ValueError("relationship evidence refs do not match exact records")
    _identity(record["relationship_id"], relationship.relationship_id, "relationship_id")
    return relationship


def _claim_ref(value: Any) -> ClaimEvidenceRef:
    record = _object(value, {"evidence_type", "record_id", "record_sha256", "observed_at_ms", "available_at_ms"}, "claim evidence ref")
    return ClaimEvidenceRef(ClaimEvidenceType(record["evidence_type"]), record["record_id"],
                            record["record_sha256"], record["observed_at_ms"], record["available_at_ms"])


def _claim(value: Any) -> WorldClaim:
    record = _object(value, {"schema_version", "claim_id", "coordinate", "as_of_ms", "value", "quality",
                             "confidence", "uncertainty", "supporting_evidence", "contradicting_evidence",
                             "source", "source_ref"}, "claim")
    _version(record, CLAIM_VERSION)
    coordinate = _object(record["coordinate"], {"scope", "horizon", "dimension"}, "claim coordinate")
    claim = WorldClaim(
        ClaimCoordinate(_scope(coordinate["scope"]), Horizon(coordinate["horizon"]),
                        coordinate["dimension"]),
        record["as_of_ms"], record["value"], Quality(record["quality"]),
        record["confidence"], record["uncertainty"],
        tuple(_claim_ref(item) for item in record["supporting_evidence"]),
        tuple(_claim_ref(item) for item in record["contradicting_evidence"]),
        record["source"], record["source_ref"], schema_version=record["schema_version"])
    _identity(record["claim_id"], claim.claim_id, "claim_id")
    return claim


def reconstruct_world_model(historical_json: str, *, model_id: str,
                            relationship_evidence: Iterable[Observation | Mapping[str, Any]] = ()) -> WorldModel:
    """Rebuild a v1 model from its exact canonical JSON and stored ID.

    Relationship evidence not present in instrument states must be supplied as
    exact historical Observation records. No clock or version is inferred.
    """
    if not isinstance(historical_json, str):
        raise TypeError("historical_json must be a string")
    record = json.loads(historical_json)
    record = _object(record, {"schema_version", "as_of_ms", "nodes", "states", "horizons"},
                     "world model", optional={"relationships", "claims"})
    _version(record, MODEL_VERSION)
    states = tuple(_state(item) for item in record["states"])
    evidence_items = [obs for state in states for obs in state.observations]
    evidence_items.extend(item if isinstance(item, Observation) else _observation(item)
                          for item in relationship_evidence)
    evidence: dict[EvidenceRef, Observation] = {}
    for observation in evidence_items:
        if not isinstance(observation, Observation):
            raise TypeError("relationship_evidence must contain Observation or serialized Observation")
        ref = EvidenceRef.from_observation(observation)
        evidence[ref] = observation
    relationships = None
    if "relationships" in record:
        rels = _object(record["relationships"], {"as_of_ms", "relationships"}, "relationship collection")
        relationships = RelationshipCollection(rels["as_of_ms"],
            tuple(_relationship(item, evidence) for item in rels["relationships"]))
    claims = None
    if "claims" in record:
        items = _object(record["claims"], {"as_of_ms", "claims"}, "claim collection")
        claims = ClaimCollection(items["as_of_ms"], tuple(_claim(item) for item in items["claims"]))
    model = WorldModel(record["as_of_ms"], tuple(_node(item) for item in record["nodes"]),
                       states, tuple(Horizon(item) for item in record["horizons"]),
                       schema_version=record["schema_version"], relationships=relationships,
                       claims=claims)
    _identity(model_id, model.model_id, "model_id")
    if model.to_json() != historical_json:
        raise ValueError("historical WorldModel JSON is not byte-for-byte canonical")
    return model


@dataclass(frozen=True, slots=True)
class WorldModelRecord:
    """Self-contained, exact v1 WorldModel archival receipt."""

    schema_version: str
    model_json: str
    model_id: str
    relationship_evidence: tuple[str, ...]
    record_id: str

    @classmethod
    def from_model(cls, model: WorldModel) -> WorldModelRecord:
        if not isinstance(model, WorldModel):
            raise TypeError("model must be WorldModel")
        evidence = (observation.to_json()
                    for relationship in (() if model.relationships is None else
                                         model.relationships.relationships)
                    for observation in relationship.evidence)
        records = tuple(sorted(set(evidence)))
        payload = {"schema_version": RECORD_VERSION, "model_json": model.to_json(),
                   "model_id": model.model_id,
                   "relationship_evidence": [json.loads(item) for item in records]}
        record_id = f"{RECORD_VERSION}:{sha256(_canonical(payload).encode('utf-8')).hexdigest()}"
        return cls(RECORD_VERSION, model.to_json(), model.model_id, records, record_id)

    @classmethod
    def from_json(cls, historical_json: str) -> WorldModelRecord:
        if not isinstance(historical_json, str):
            raise TypeError("historical_json must be a string")
        payload = json.loads(historical_json)
        record = _object(payload, {"schema_version", "model_json", "model_id",
                                   "relationship_evidence", "record_id"}, "world model record")
        _version(record, RECORD_VERSION)
        if _canonical(record) != historical_json:
            raise ValueError("WorldModelRecord JSON is not byte-for-byte canonical")
        if not isinstance(record["model_json"], str) or not isinstance(record["model_id"], str):
            raise ValueError("invalid archived model JSON or model_id")
        if not isinstance(record["relationship_evidence"], list):
            raise ValueError("relationship_evidence must be a list")
        evidence = tuple(_observation(item) for item in record["relationship_evidence"])
        without_id = {key: value for key, value in record.items() if key != "record_id"}
        expected_id = f"{RECORD_VERSION}:{sha256(_canonical(without_id).encode('utf-8')).hexdigest()}"
        _identity(record["record_id"], expected_id, "record_id")
        model = reconstruct_world_model(record["model_json"], model_id=record["model_id"],
                                        relationship_evidence=evidence)
        canonical = cls.from_model(model)
        if canonical.to_json() != historical_json:
            raise ValueError("archived relationship evidence does not match exact model evidence")
        return canonical

    def to_json(self) -> str:
        return _canonical({"schema_version": self.schema_version, "model_json": self.model_json,
                           "model_id": self.model_id,
                           "relationship_evidence": [json.loads(item) for item in self.relationship_evidence],
                           "record_id": self.record_id})

    def reconstruct(self) -> WorldModel:
        return self.from_json(self.to_json())._reconstruct_verified()

    def _reconstruct_verified(self) -> WorldModel:
        return reconstruct_world_model(self.model_json, model_id=self.model_id,
                                       relationship_evidence=(_observation(json.loads(item))
                                                              for item in self.relationship_evidence))


@dataclass(frozen=True, slots=True, init=False, eq=False)
class WorldHistory:
    """Immutable index of self-contained records by exact historical cut."""

    _records: tuple[WorldModelRecord, ...]

    def __init__(self, models: Iterable[WorldModel | WorldModelRecord]):
        by_cut: dict[int, WorldModelRecord] = {}
        for item in models:
            if isinstance(item, WorldModel):
                record = WorldModelRecord.from_model(item)
            elif isinstance(item, WorldModelRecord):
                record = WorldModelRecord.from_json(item.to_json())
            else:
                raise TypeError("history items must be WorldModel or WorldModelRecord")
            cut = record.reconstruct().as_of_ms
            prior = by_cut.get(cut)
            if prior is not None and prior.record_id != record.record_id:
                raise ValueError(f"ambiguous WorldModel at exact cut {cut}")
            by_cut[cut] = record
        object.__setattr__(self, "_records", tuple(by_cut[cut] for cut in sorted(by_cut)))

    @classmethod
    def from_json(cls, historical_json: str) -> WorldHistory:
        if not isinstance(historical_json, str):
            raise TypeError("historical_json must be a string")
        items = json.loads(historical_json)
        if not isinstance(items, list):
            raise ValueError("history must be a list")
        history = cls(WorldModelRecord.from_json(_canonical(item)) for item in items)
        if history.to_json() != historical_json:
            raise ValueError("history JSON is not byte-for-byte canonical")
        return history

    def get_exact(self, as_of_ms: int) -> WorldModel:
        _timestamp(as_of_ms, "as_of_ms")
        for record in self._records:
            if record.reconstruct().as_of_ms == as_of_ms:
                return record.reconstruct()
        raise LookupError(f"no historical WorldModel at exact cut {as_of_ms}")

    def to_json(self) -> str:
        return _canonical([json.loads(record.to_json()) for record in self._records])

    @property
    def history_id(self) -> str:
        return sha256(self.to_json().encode("utf-8")).hexdigest()
