"""Historical v1 reconstruction must preserve exact records and cuts."""
import json
from dataclasses import FrozenInstanceError

import pytest

from trader.world import (
    ClaimCollection, ClaimCoordinate, ClaimEvidenceRef, HierarchyNode, Horizon,
    Observation, Quality, RelationshipCollection, RelationshipCoordinate,
    RelationshipState, Scope, ScopeLevel, WorldClaim, WorldHistory, WorldModel,
    WorldModelRecord, WorldState, reconstruct_world_model,
)

ROOT = Scope(ScopeLevel.GLOBAL, "world")
ASSET = Scope(ScopeLevel.ASSET_CLASS, "crypto")
BTC = Scope(ScopeLevel.INSTRUMENT, "BTC")
ETH = Scope(ScopeLevel.INSTRUMENT, "ETH")
NODES = (HierarchyNode(ROOT), HierarchyNode(ASSET, ROOT),
         HierarchyNode(BTC, ASSET), HierarchyNode(ETH, ASSET))


def obs(instrument="BTC", *, captured=150, value=1):
    return Observation(instrument, 100, captured, "1h", "price", value,
                       "test", "bar", Quality.VALID, available_at_ms=120,
                       horizon="intraday", uncertainty={"sources": ["bar"]})


def make_model(cut=200, *, with_relationship=False, with_claim=False,
               observation=None):
    observation = observation or obs()
    states = (WorldState("BTC", cut, (observation,)),)
    relationship = None
    relationships = None
    if with_relationship:
        relationship = RelationshipState(
            RelationshipCoordinate(BTC, ETH, "correlation", Horizon.INTRADAY),
            cut, {"strength": [0.4]}, Quality.SUSPECT, "test", "edge",
            (observation, obs("ETH")), uncertainty={"method": "test"})
        relationships = RelationshipCollection(cut, (relationship,))
    claims = None
    if with_claim:
        refs = [ClaimEvidenceRef.from_observation(observation)]
        if relationship:
            refs.append(ClaimEvidenceRef.from_relationship(relationship))
        claim = WorldClaim(ClaimCoordinate(ASSET, Horizon.INTRADAY, "trend"),
                           cut, "weak", Quality.SUSPECT, 0.6, {"reason": ["sample"]},
                           tuple(refs), (), "test", "claim")
        claims = ClaimCollection(cut, (claim,))
    return WorldModel(cut, NODES, states, (Horizon.INTRADAY, Horizon.STRUCTURAL),
                      relationships=relationships, claims=claims)


def replay(model, **kwargs):
    return reconstruct_world_model(model.to_json(), model_id=model.model_id, **kwargs)


def edit(model, mutate, **kwargs):
    record = json.loads(model.to_json())
    mutate(record)
    return reconstruct_world_model(json.dumps(record, sort_keys=True, separators=(",", ":")),
                                   model_id=model.model_id, **kwargs)


@pytest.mark.parametrize("relationships,claims", [(False, False), (True, False),
                                                    (False, True), (True, True)])
def test_exact_round_trip_all_supported_shapes(relationships, claims):
    model = make_model(with_relationship=relationships, with_claim=claims)
    evidence = (obs("ETH"),) if relationships else ()
    rebuilt = replay(model, relationship_evidence=evidence)
    assert type(rebuilt) is WorldModel
    assert rebuilt.to_json() == model.to_json()
    assert rebuilt.model_id == model.model_id
    assert rebuilt.get_state(BTC).get_one("price").to_json() == obs().to_json()
    assert rebuilt.get_observations(BTC, Horizon.INTRADAY, kind="price")
    assert rebuilt.get_state(ASSET) is None
    assert rebuilt.parent(BTC) == ASSET
    if relationships:
        assert rebuilt.relationships.get_one(source=BTC, target=ETH).evidence_refs == \
            model.relationships.get_one().evidence_refs
    if claims:
        assert rebuilt.get_one_claim(ASSET, Horizon.INTRADAY, dimension="trend").to_json() == \
            model.claims.get_one().to_json()


def test_nested_and_outer_ids_fail_closed():
    model = make_model(with_relationship=True, with_claim=True)
    evidence = {"relationship_evidence": (obs("ETH"),)}
    changes = [
        (lambda r: r["states"][0]["observations"][0].update(observation_id="bad"), "observation_id"),
        (lambda r: r["states"][0].update(state_id="bad"), "state_id"),
        (lambda r: r["relationships"]["relationships"][0].update(relationship_id="bad"), "relationship_id"),
        (lambda r: r["claims"]["claims"][0].update(claim_id="bad"), "claim_id"),
    ]
    for mutation, name in changes:
        with pytest.raises(ValueError, match=name):
            edit(model, mutation, **evidence)
    with pytest.raises(ValueError, match="model_id"):
        reconstruct_world_model(model.to_json(), model_id="bad", **evidence)
    # An observation's stable ID excludes its value; its enclosing state ID
    # must still bind the complete historical record.
    with pytest.raises(ValueError, match="state_id"):
        edit(model, lambda r: r["states"][0]["observations"][0].update(value=99), **evidence)


def test_version_binding_and_missing_evidence():
    model = make_model(with_relationship=True)
    with pytest.raises(ValueError, match="unsupported historical schema version"):
        edit(model, lambda r: r.update(schema_version="world.model.v0"))
    with pytest.raises(ValueError, match="unsupported observation schema version"):
        edit(model, lambda r: r["states"][0]["observations"][0].update(schema_version="world.observation.v0"))
    with pytest.raises(ValueError, match="exact historical relationship evidence record is missing"):
        replay(model)
    # A valid-looking but different payload cannot satisfy its evidence hash.
    with pytest.raises(ValueError, match="exact historical relationship evidence record is missing"):
        replay(model, relationship_evidence=(obs("ETH", value=99),))


def test_future_records_cannot_enter_earlier_replay():
    early = make_model(200)
    late = make_model(300, observation=obs(captured=250, value=2),
                      with_relationship=True, with_claim=True)
    history = WorldHistory((late, early))
    assert history.get_exact(200).to_json() == early.to_json()
    assert history.get_exact(200).get_state(BTC).get_one("price").value == 1
    assert history.get_exact(200).relationships is None
    assert history.get_exact(200).claims is None
    with pytest.raises(ValueError, match="not yet captured"):
        edit(early, lambda r: r["states"][0]["observations"][0].update(observed_at_ms=250))
    with pytest.raises(ValueError, match="relationship collection cut"):
        edit(early, lambda r: r.update(relationships=late.relationships.to_dict()),
             relationship_evidence=(obs("ETH"), obs(captured=250, value=2)))
    with pytest.raises(ValueError, match="claim collection cut"):
        edit(early, lambda r: r.update(claims=late.claims.to_dict()))
    with pytest.raises(LookupError, match="exact cut 250"):
        history.get_exact(250)


def test_history_ambiguity_order_and_mutation_safety():
    first = make_model()
    later = make_model(300)
    assert WorldHistory((first, later)).to_json() == WorldHistory((later, first, first)).to_json()
    assert WorldHistory((first, later)).history_id == WorldHistory((later, first)).history_id
    with pytest.raises(ValueError, match="ambiguous"):
        WorldHistory((first, make_model(observation=obs(value=2))))
    incoming = [first, later]
    history = WorldHistory(incoming)
    before = history.to_json()
    incoming.clear()
    payload = history.get_exact(200).to_dict()
    payload["states"][0]["observations"][0]["value"] = 99
    with pytest.raises(FrozenInstanceError):
        history.get_exact(200).as_of_ms = 1
    assert history.to_json() == before


def test_history_records_cannot_be_reassigned():
    history = WorldHistory((make_model(),))
    before = history.to_json()
    with pytest.raises(FrozenInstanceError):
        history._records = ()
    assert history.to_json() == before


def test_absent_optional_collections_keep_exact_v1_wire():
    model = make_model()
    record = json.loads(model.to_json())
    assert "relationships" not in record and "claims" not in record
    assert "model_id" not in record  # v1 receipt binds it separately.
    assert replay(model).to_json() == model.to_json()


@pytest.mark.parametrize("relationships,claims", [(False, False), (True, False),
                                                    (False, True), (True, True)])
def test_self_contained_record_round_trip(relationships, claims):
    model = make_model(with_relationship=relationships, with_claim=claims)
    before_json, before_id = model.to_json(), model.model_id
    record = WorldModelRecord.from_model(model)
    loaded = WorldModelRecord.from_json(record.to_json())
    rebuilt = loaded.reconstruct()
    assert type(rebuilt) is WorldModel
    assert rebuilt.to_json() == before_json
    assert rebuilt.model_id == before_id == loaded.model_id
    assert WorldModelRecord.from_model(rebuilt).to_json() == record.to_json()
    assert WorldModelRecord.from_model(model).record_id == record.record_id
    if relationships:
        assert len(loaded.relationship_evidence) == 2
    else:
        assert loaded.relationship_evidence == ()
    assert "model_id" not in json.loads(before_json)


def archive_edit(record, mutate, *, rehash=False):
    payload = json.loads(record.to_json())
    mutate(payload)
    if rehash:
        from hashlib import sha256
        from trader.world.replay import RECORD_VERSION
        identity = {key: value for key, value in payload.items() if key != "record_id"}
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
        payload["record_id"] = f"{RECORD_VERSION}:{sha256(encoded).hexdigest()}"
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_archive_tampering_fails_closed():
    record = WorldModelRecord.from_model(make_model(with_relationship=True, with_claim=True))
    with pytest.raises(ValueError, match="record_id"):
        WorldModelRecord.from_json(archive_edit(record, lambda r: r.update(record_id="bad")))
    with pytest.raises(ValueError, match="model_id"):
        WorldModelRecord.from_json(archive_edit(record, lambda r: r.update(model_id="bad"), rehash=True))
    with pytest.raises(ValueError, match="byte-for-byte canonical"):
        WorldModelRecord.from_json(archive_edit(
            record, lambda r: r.update(model_json=r["model_json"] + " "), rehash=True))
    with pytest.raises(ValueError, match="unsupported historical schema version"):
        WorldModelRecord.from_json(archive_edit(record, lambda r: r.update(schema_version="world.replay.record.v0"), rehash=True))
    with pytest.raises(ValueError, match="exact historical relationship evidence record is missing"):
        WorldModelRecord.from_json(archive_edit(record, lambda r: r["relationship_evidence"].pop(), rehash=True))
    with pytest.raises(ValueError, match="archived relationship evidence does not match"):
        WorldModelRecord.from_json(archive_edit(record, lambda r: r["relationship_evidence"][0].update(value=99), rehash=True))
    for path, label in [
        (("states", 0, "observations", 0, "observation_id"), "observation_id"),
        (("states", 0, "state_id"), "state_id"),
        (("relationships", "relationships", 0, "relationship_id"), "relationship_id"),
        (("claims", "claims", 0, "claim_id"), "claim_id"),
    ]:
        def change(payload):
            model = json.loads(payload["model_json"])
            target = model
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = "bad"
            payload["model_json"] = json.dumps(model, sort_keys=True, separators=(",", ":"))
        with pytest.raises(ValueError, match=label):
            WorldModelRecord.from_json(archive_edit(record, change, rehash=True))
    def nested_version(payload):
        model = json.loads(payload["model_json"])
        model["states"][0]["observations"][0]["schema_version"] = "world.observation.v0"
        payload["model_json"] = json.dumps(model, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="unsupported observation schema version"):
        WorldModelRecord.from_json(archive_edit(record, nested_version, rehash=True))
    def model_version(payload):
        model = json.loads(payload["model_json"])
        model["schema_version"] = "world.model.v0"
        payload["model_json"] = json.dumps(model, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="unsupported historical schema version"):
        WorldModelRecord.from_json(archive_edit(record, model_version, rehash=True))
    with pytest.raises(ValueError, match="archived relationship evidence does not match"):
        WorldModelRecord.from_json(archive_edit(record, lambda r: r["relationship_evidence"].reverse(), rehash=True))


def test_record_full_evidence_order_and_history_persistence():
    first = make_model(with_relationship=True)
    rel = first.relationships.get_one()
    reversed_rel = RelationshipState(rel.coordinate, rel.as_of_ms, rel.value, rel.quality,
                                     rel.source, rel.source_ref, tuple(reversed(rel.evidence)),
                                     uncertainty=rel.uncertainty)
    same = WorldModel(first.as_of_ms, tuple(reversed(first.nodes)), first.states,
                      tuple(reversed(first.horizons)),
                      relationships=RelationshipCollection(first.as_of_ms, (reversed_rel,)))
    assert WorldModelRecord.from_model(first).to_json() == WorldModelRecord.from_model(same).to_json()
    two_values = RelationshipState(rel.coordinate, rel.as_of_ms, rel.value, rel.quality,
                                   rel.source, rel.source_ref,
                                   (obs("ETH", value=2), obs("ETH", value=1), obs()),
                                   uncertainty=rel.uncertainty)
    full_record_model = WorldModel(first.as_of_ms, first.nodes, first.states, first.horizons,
                                   relationships=RelationshipCollection(first.as_of_ms, (two_values,)))
    full_record = WorldModelRecord.from_model(full_record_model)
    assert len(full_record.relationship_evidence) == 3
    assert full_record.reconstruct().to_json() == full_record_model.to_json()
    # The exact evidence record matters even when stable observation_id is equal.
    different = make_model(with_relationship=True, observation=obs(value=2))
    assert WorldModelRecord.from_model(first).record_id != WorldModelRecord.from_model(different).record_id
    late = make_model(300, observation=obs(captured=250, value=2),
                      with_relationship=True, with_claim=True)
    history = WorldHistory((WorldModelRecord.from_model(late), WorldModelRecord.from_model(first)))
    restored = WorldHistory.from_json(history.to_json())
    assert restored.to_json() == history.to_json()
    assert restored.history_id == history.history_id
    assert type(restored.get_exact(200)) is WorldModel
    assert restored.get_exact(200).to_json() == first.to_json()
    assert restored.get_exact(200).claims is None
    with pytest.raises(LookupError, match="exact cut"):
        restored.get_exact(250)
    with pytest.raises(ValueError, match="ambiguous"):
        WorldHistory((WorldModelRecord.from_model(first), WorldModelRecord.from_model(different)))
