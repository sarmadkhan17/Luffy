"""Exact evidence and immutable point-in-time world claim contracts."""

import json
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from trader.world import (ClaimCollection, ClaimCoordinate, ClaimEvidenceRef,
                          ClaimEvidenceType, HierarchyNode, Horizon, Observation,
                          Quality, RelationshipCollection, RelationshipCoordinate,
                          RelationshipState, Scope, ScopeLevel, WorldClaim,
                          WorldModel, WorldState)


GLOBAL = Scope(ScopeLevel.GLOBAL, "world")
ASSET = Scope(ScopeLevel.ASSET_CLASS, "digital-assets")
BTC = Scope(ScopeLevel.INSTRUMENT, "BTC")
ETH = Scope(ScopeLevel.INSTRUMENT, "ETH")
NODES = (HierarchyNode(GLOBAL), HierarchyNode(ASSET, GLOBAL),
         HierarchyNode(BTC, ASSET), HierarchyNode(ETH, ASSET))


def obs(instrument="BTC", value=1, *, captured=150, quality=Quality.VALID,
        available=120):
    return Observation(instrument, 100, captured, "1h", "price", value, "test",
                       "bar-1", quality, available_at_ms=available)


def edge(value=0.4, *, as_of=200):
    return RelationshipState(
        RelationshipCoordinate(BTC, ETH, "correlation", Horizon.INTRADAY),
        as_of, value, Quality.SUSPECT, "test", "edge-v1", (obs(), obs("ETH")))


def claim(*, scope=BTC, horizon=Horizon.INTRADAY, dimension="trend", value="weak",
          support=None, contradict=(), cut=200, confidence=0.6, uncertainty=None,
          quality=Quality.SUSPECT):
    return WorldClaim(ClaimCoordinate(scope, horizon, dimension), cut, value,
                      quality, confidence, {"reason": "tentative"} if uncertainty is None
                      else uncertainty,
                      (ClaimEvidenceRef.from_observation(obs()),) if support is None
                      else support, contradict, "test", "claim-v1")


def model(*, claims=None, observations=(), relationships=None, horizons=tuple(Horizon)):
    states = (WorldState("BTC", 200, observations),) if observations else ()
    return WorldModel(200, NODES, states, horizons,
                      relationships=relationships, claims=claims)


def test_immutable_explicit_polarity_and_canonical_evidence():
    a, b = ClaimEvidenceRef.from_observation(obs()), ClaimEvidenceRef.from_observation(obs("ETH"))
    first = claim(support=[a, b], contradict=[ClaimEvidenceRef.from_relationship(edge())])
    reverse = claim(support=[b, a], contradict=first.contradicting_evidence)
    assert first.to_json() == reverse.to_json() and first.claim_id == reverse.claim_id
    assert first.supporting_evidence == reverse.supporting_evidence
    assert first.to_dict()["contradicting_evidence"][0]["evidence_type"] == "RelationshipState"
    assert claim(support=[a], contradict=[b]).claim_id != claim(support=[b], contradict=[a]).claim_id
    with pytest.raises(FrozenInstanceError):
        first.value = "strong"
    with pytest.raises(FrozenInstanceError):
        a.record_id = "changed"
    with pytest.raises(FrozenInstanceError):
        first.coordinate.scope = ETH


def test_full_record_hash_identity_for_both_evidence_types():
    a, changed = obs(), obs(value=2)
    assert a.observation_id == changed.observation_id
    ref, changed_ref = ClaimEvidenceRef.from_observation(a), ClaimEvidenceRef.from_observation(changed)
    assert ref.record_id == a.observation_id
    assert ref.record_sha256 == sha256(a.to_json().encode()).hexdigest()
    assert ref.record_sha256 != changed_ref.record_sha256
    assert claim(support=[ref]).claim_id != claim(support=[changed_ref]).claim_id
    one, two = edge(), edge(0.5)
    edge_ref, changed_edge_ref = ClaimEvidenceRef.from_relationship(one), ClaimEvidenceRef.from_relationship(two)
    assert edge_ref.record_id == one.relationship_id
    assert edge_ref.record_sha256 == sha256(one.to_json().encode()).hexdigest()
    assert edge_ref.record_sha256 != changed_edge_ref.record_sha256
    assert claim(support=[edge_ref]).claim_id != claim(support=[changed_edge_ref]).claim_id


def test_material_claim_changes_change_identity():
    base = claim()
    variants = [claim(scope=ASSET), claim(horizon=Horizon.STRUCTURAL),
                claim(dimension="stress"), claim(value="strong"),
                claim(confidence=0.7), claim(uncertainty={"reason": "revised"}),
                claim(cut=201), replace(base, source_ref="claim-v2"),
                claim(quality=Quality.INVALID)]
    assert all(item.claim_id != base.claim_id for item in variants)
    assert base.coordinate.scope == BTC and "instrument" not in claim(scope=ASSET).to_dict()
    assert ClaimEvidenceType.OBSERVATION.value == "Observation"


def test_future_evidence_and_bad_confidence_refused():
    with pytest.raises(ValueError, match="future evidence"):
        claim(support=[ClaimEvidenceRef.from_observation(obs(captured=201, available=120))])
    with pytest.raises(ValueError, match="future evidence"):
        claim(support=[ClaimEvidenceRef.from_relationship(edge(as_of=201))])
    for confidence in (True, -0.1, 1.1, float("nan")):
        with pytest.raises(ValueError, match="confidence"):
            claim(confidence=confidence)


def test_low_quality_evidence_and_unknown_availability_remain_representable():
    low = obs(value=None, quality=Quality.MISSING, available=None)
    ref = ClaimEvidenceRef.from_observation(low)
    item = claim(support=[ref], quality=Quality.SUSPECT)
    assert item.supporting_evidence[0].available_at_ms is None
    with pytest.raises(ValueError, match="known evidence availability"):
        replace(item, quality=Quality.VALID)


def test_collection_order_horizons_ambiguity_and_mutation_safety():
    payload, uncertainty = {"labels": ["weak"]}, {"notes": ["tentative"]}
    evidence = [ClaimEvidenceRef.from_observation(obs())]
    weak = claim(value=payload, uncertainty=uncertainty, support=evidence)
    strong = claim(value="strong")
    structural = claim(horizon=Horizon.STRUCTURAL, value="strong")
    items = [weak, strong, structural]
    collection = ClaimCollection(200, items)
    assert collection.to_json() == ClaimCollection(200, reversed(items)).to_json()
    assert collection.query(horizon=Horizon.STRUCTURAL) == (structural,)
    assert len(collection.query(scope=BTC, horizon=Horizon.INTRADAY, dimension="trend")) == 2
    assert collection.get_one(horizon=Horizon.STRUCTURAL) is structural
    with pytest.raises(LookupError, match="found 2"):
        collection.get_one(scope=BTC, horizon=Horizon.INTRADAY, dimension="trend")
    with pytest.raises(LookupError, match="found 0"):
        collection.get_one(dimension="stress")
    with pytest.raises(ValueError, match="duplicate exact"):
        ClaimCollection(200, [weak, weak])
    before = (weak.to_json(), collection.to_json())
    payload["labels"].append("changed")
    uncertainty["notes"].append("changed")
    evidence.clear()
    items.clear()
    collection.to_dict()["claims"].clear()
    assert (weak.to_json(), collection.to_json()) == before
    with pytest.raises(TypeError):
        weak.value["labels"] = ("changed",)
    with pytest.raises(FrozenInstanceError):
        collection.claims = ()


def test_world_model_exact_attachment_and_backward_compatibility():
    original_obs = obs()
    original_state = WorldState("BTC", 200, [original_obs])
    rel = edge()
    rels = RelationshipCollection(200, [rel])
    baseline = model(observations=[original_obs], relationships=rels)
    before = (original_obs.to_json(), original_state.to_json(), rel.to_json(),
              baseline.to_json(), baseline.model_id)
    refs = [ClaimEvidenceRef.from_observation(original_obs),
            ClaimEvidenceRef.from_relationship(rel)]
    item = claim(scope=ASSET, support=refs)
    attached = model(observations=[original_obs], relationships=rels,
                     claims=ClaimCollection(200, [item]))
    assert attached.get_one_claim(ASSET, Horizon.INTRADAY, dimension="trend") is item
    assert attached.model_id != baseline.model_id
    changed = replace(item, confidence=0.8)
    assert model(observations=[original_obs], relationships=rels,
                 claims=ClaimCollection(200, [changed])).model_id != attached.model_id
    assert (original_obs.to_json(), original_state.to_json(), rel.to_json(),
            baseline.to_json(), baseline.model_id) == before
    assert "claims" not in baseline.to_dict()
    assert model(observations=[original_obs], relationships=rels).to_json() == baseline.to_json()
    with pytest.raises(ValueError, match="exact record"):
        model(observations=[obs(value=2)], relationships=rels,
              claims=ClaimCollection(200, [item]))
    with pytest.raises(ValueError, match="exact record"):
        model(observations=[original_obs], relationships=RelationshipCollection(200, [edge(0.5)]),
              claims=ClaimCollection(200, [item]))
    with pytest.raises(ValueError, match="exact record"):
        model(claims=ClaimCollection(200, [item]))
    forged_clock = replace(refs[0], observed_at_ms=149)
    with pytest.raises(ValueError, match="exact record"):
        model(observations=[original_obs], relationships=rels,
              claims=ClaimCollection(200, [claim(support=[forged_clock])]))
    with pytest.raises(ValueError, match="collection cut"):
        model(claims=ClaimCollection(201))
    with pytest.raises(ValueError, match="hierarchy node"):
        model(observations=[original_obs], claims=ClaimCollection(200, [claim(scope=Scope(ScopeLevel.INSTRUMENT, "SOL"))]))
    with pytest.raises(ValueError, match="horizon"):
        model(observations=[original_obs], horizons=[Horizon.STRUCTURAL],
              claims=ClaimCollection(200, [claim()]))


def test_absent_claims_keep_preexisting_model_wire_format_and_id():
    unchanged = model()
    legacy_record = {
        "schema_version": "world.model.v1", "as_of_ms": 200,
        "horizons": ["intraday", "short-term", "structural", "swing"],
        "nodes": [
            {"scope": {"level": "ASSET_CLASS", "identifier": "digital-assets"},
             "parent": {"level": "GLOBAL", "identifier": "world"}},
            {"scope": {"level": "GLOBAL", "identifier": "world"}, "parent": None},
            {"scope": {"level": "INSTRUMENT", "identifier": "BTC"},
             "parent": {"level": "ASSET_CLASS", "identifier": "digital-assets"}},
            {"scope": {"level": "INSTRUMENT", "identifier": "ETH"},
             "parent": {"level": "ASSET_CLASS", "identifier": "digital-assets"}},
        ],
        "states": [],
    }
    legacy_json = json.dumps(legacy_record, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
    assert unchanged.to_json() == legacy_json
    assert unchanged.model_id == "world.model.v1:" + sha256(legacy_json.encode()).hexdigest()


def test_model_queries_preserve_same_coordinate_ambiguity_and_horizons():
    records = ClaimCollection(200, [claim(), claim(value="strong"),
                                    claim(horizon=Horizon.STRUCTURAL, value="strong")])
    attached = model(observations=[obs()], claims=records)
    assert len(attached.get_claims(BTC, Horizon.INTRADAY, dimension="trend")) == 2
    assert attached.get_one_claim(BTC, Horizon.STRUCTURAL, dimension="trend").value == "strong"
    with pytest.raises(LookupError, match="found 2"):
        attached.get_one_claim(BTC, Horizon.INTRADAY, dimension="trend")
