"""Focused contracts for point-in-time cross-scope relationship claims."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from trader.world import (EvidenceRef, HierarchyNode, Horizon, Observation, Quality,
                          RelationshipCollection, RelationshipCoordinate,
                          RelationshipState, Scope, ScopeLevel, WorldModel, WorldState)


GLOBAL = Scope(ScopeLevel.GLOBAL, "world")
ASSET = Scope(ScopeLevel.ASSET_CLASS, "digital assets")
BTC = Scope(ScopeLevel.INSTRUMENT, "BTC")
ETH = Scope(ScopeLevel.INSTRUMENT, "ETH")


def observation(instrument="BTC", value=1, *, observed_at_ms=150,
                available_at_ms=120, quality=Quality.VALID):
    return Observation(instrument=instrument, timestamp_ms=100,
                       observed_at_ms=observed_at_ms, available_at_ms=available_at_ms,
                       timeframe="1h", kind="price", value=value, source="test",
                       source_ref="bar-1", quality=quality)


def relationship(*, source=BTC, target=ETH, kind="correlation",
                 horizon=Horizon.INTRADAY, as_of_ms=200, value=None,
                 evidence=None, confidence=0.7, uncertainty=None):
    return RelationshipState(
        RelationshipCoordinate(source, target, kind, horizon), as_of_ms,
        {"estimate": 0.4} if value is None else value, Quality.SUSPECT,
        "test", "calculation-v1", [observation("BTC"), observation("ETH")]
        if evidence is None else evidence, confidence,
        {"reason": "small sample"} if uncertainty is None else uncertainty)


def model(relationships=None):
    return WorldModel(200, [HierarchyNode(GLOBAL), HierarchyNode(ASSET, GLOBAL),
                            HierarchyNode(BTC, ASSET), HierarchyNode(ETH, ASSET)],
                      relationships=relationships)


def test_immutable_coordinates_ordered_endpoints_and_no_fake_instrument():
    coordinate = RelationshipCoordinate(BTC, ETH, "correlation", Horizon.INTRADAY)
    claim = relationship()
    with pytest.raises(FrozenInstanceError):
        coordinate.source = ETH
    with pytest.raises(FrozenInstanceError):
        claim.as_of_ms = 201
    assert coordinate.source == BTC and coordinate.target == ETH
    assert relationship(source=ETH, target=BTC).relationship_id != claim.relationship_id
    assert "instrument" not in claim.to_dict()


def test_canonical_evidence_order_and_full_record_hash():
    a, b = observation("BTC"), observation("ETH")
    first = relationship(evidence=[a, b])
    second = relationship(evidence=[b, a])
    assert first.to_json() == second.to_json()
    assert first.relationship_id == second.relationship_id
    assert first.evidence_refs == second.evidence_refs
    for ref, obs in zip(first.evidence_refs, first.evidence):
        assert ref.observation_id == obs.observation_id
        assert ref.record_sha256 == sha256(obs.to_json().encode()).hexdigest()
    changed = observation("BTC", value=2)
    assert changed.observation_id == a.observation_id
    assert EvidenceRef.from_observation(changed).record_sha256 != EvidenceRef.from_observation(a).record_sha256
    assert relationship(evidence=[changed, b]).relationship_id != first.relationship_id


def test_material_coordinates_cut_payload_metadata_and_evidence_change_identity():
    base = relationship()
    variants = [
        relationship(source=ETH, target=BTC),
        relationship(kind="lead-lag"),
        relationship(horizon=Horizon.SWING),
        relationship(as_of_ms=201),
        relationship(value={"estimate": 0.5}),
        relationship(confidence=0.8),
        relationship(uncertainty={"reason": "revised"}),
        replace(base, source_ref="calculation-v2"),
        relationship(evidence=[observation("BTC", value=2), observation("ETH")]),
    ]
    assert all(item.relationship_id != base.relationship_id for item in variants)


def test_future_evidence_refused_and_unknown_availability_preserved():
    with pytest.raises(ValueError, match="future evidence"):
        relationship(evidence=[observation(observed_at_ms=201)])
    # Observation enforces availability <= capture, so a future known
    # availability also entails a future capture and must be refused.
    with pytest.raises(ValueError, match="future evidence"):
        relationship(evidence=[observation(observed_at_ms=220, available_at_ms=210)])
    unknown = observation(available_at_ms=None, quality=Quality.SUSPECT)
    claim = relationship(evidence=[unknown])
    assert claim.evidence_refs[0].available_at_ms is None
    assert claim.to_dict()["evidence"][0]["available_at_ms"] is None
    with pytest.raises(ValueError, match="known evidence availability"):
        replace(claim, quality=Quality.VALID)


def test_low_quality_evidence_is_retained():
    suspect = observation(quality=Quality.SUSPECT)
    missing = Observation(instrument="ETH", timestamp_ms=100, observed_at_ms=150,
                          available_at_ms=None, timeframe="1h", kind="price",
                          value=None, source="test", source_ref="missing",
                          quality=Quality.MISSING)
    claim = relationship(evidence=[suspect, missing])
    assert len(claim.evidence) == 2
    assert {item.quality for item in claim.evidence} == {Quality.SUSPECT, Quality.MISSING}


def test_collection_coexistence_query_ambiguity_and_duplicate_rejection():
    corr = relationship()
    beta = relationship(kind="beta")
    swing = relationship(horizon=Horizon.SWING)
    contradictory = relationship(value={"estimate": -0.4})
    collection = RelationshipCollection(200, [swing, contradictory, beta, corr])
    reverse = RelationshipCollection(200, [corr, beta, contradictory, swing])
    assert collection.to_json() == reverse.to_json()
    assert len(collection.query(source=BTC, target=ETH)) == 4
    assert collection.query(kind="beta") == (beta,)
    assert collection.query(horizon=Horizon.SWING) == (swing,)
    assert len(collection.query(kind="correlation", horizon=Horizon.INTRADAY)) == 2
    assert collection.get_one(kind="beta", source=BTC, target=ETH,
                              horizon=Horizon.INTRADAY) is beta
    with pytest.raises(LookupError, match="found 2"):
        collection.get_one(kind="correlation", horizon=Horizon.INTRADAY)
    with pytest.raises(LookupError, match="found 0"):
        collection.get_one(kind="cointegration")
    with pytest.raises(ValueError, match="duplicate exact"):
        RelationshipCollection(200, [corr, corr])


def test_caller_mutations_cannot_change_relationship_or_index():
    value = {"window": [1, 2]}
    uncertainty = {"notes": ["tentative"]}
    evidence = [observation("BTC"), observation("ETH")]
    claim = relationship(value=value, uncertainty=uncertainty, evidence=evidence)
    claims = [claim]
    index = RelationshipCollection(200, claims)
    before = (claim.to_json(), index.to_json())
    value["window"].append(3)
    uncertainty["notes"].append("changed")
    evidence.clear()
    claims.clear()
    claim.to_dict()["value"]["window"].append(4)
    index.to_dict()["relationships"].clear()
    assert (claim.to_json(), index.to_json()) == before
    with pytest.raises(TypeError):
        claim.value["window"] = (9,)


def test_world_model_attachment_cut_endpoints_horizon_and_underlying_unchanged():
    btc_observation = observation("BTC")
    btc_state = WorldState("BTC", 200, [btc_observation])
    baseline = model()
    original = (BTC.to_dict(), btc_observation.to_json(), btc_state.to_json(),
                baseline.to_json(), baseline.model_id)
    edge = relationship()
    attached = model(RelationshipCollection(200, [edge]))
    assert attached.relationships.get_one(kind="correlation") is edge
    assert attached.model_id != baseline.model_id
    assert (BTC.to_dict(), btc_observation.to_json(), btc_state.to_json(),
            baseline.to_json(), baseline.model_id) == original
    with pytest.raises(ValueError, match="collection cut"):
        model(RelationshipCollection(201))
    with pytest.raises(ValueError, match="collection cut|as_of_ms"):
        RelationshipCollection(200, [relationship(as_of_ms=201)])
    with pytest.raises(ValueError, match="endpoint"):
        model(RelationshipCollection(200, [relationship(target=Scope(ScopeLevel.INSTRUMENT, "SOL"))]))
    with pytest.raises(ValueError, match="horizon"):
        WorldModel(200, baseline.nodes, horizons=[Horizon.SHORT_TERM],
                   relationships=RelationshipCollection(200, [edge]))
