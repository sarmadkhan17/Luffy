"""Focused checks for the shared point-in-time observation contract."""

import json
from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from trader.core.types import Snapshot
from trader.world import Observation, ObservationIdentity, Quality, SnapshotObservation


def observation(**changes):
    fields = dict(
        instrument="BTC/USDT",
        timestamp_ms=1000,
        observed_at_ms=1100,
        available_at_ms=1050,
        source_timestamp_ms=1000,
        timeframe="1h",
        horizon="intraday",
        kind="price",
        value={"close": 125.5, "flags": ["closed"]},
        unit="USDT",
        source="exchange_public",
        source_ref="candles:BTC/USDT:1h:1000:v1",
        quality=Quality.VALID,
        max_age_ms=500,
        confidence=0.8,
        uncertainty={"method": "direct", "bounds": [125.0, 126.0]},
        transform_version="raw.v1",
    )
    fields.update(changes)
    return Observation(**fields)


def test_same_input_has_deterministic_serialization_and_provenance():
    a = observation(value={"close": 125.5, "flags": ["closed"]})
    b = observation(value={"flags": ["closed"], "close": 125.5})
    assert a.to_json() == b.to_json()
    assert a.to_dict() == b.to_dict()
    assert a.to_dict()["source_ref"] == "candles:BTC/USDT:1h:1000:v1"
    assert a.to_dict()["source_timestamp_ms"] == 1000
    assert a.to_dict()["available_at_ms"] == 1050
    assert a.to_dict()["transform_version"] == "raw.v1"


def test_identity_is_stable_across_payload_changes_and_replay_loads():
    a = observation()
    b = observation(value={"close": 130.0}, confidence=0.4, observed_at_ms=1200,
                    available_at_ms=1150)
    identity = ObservationIdentity.from_record(a.to_dict())
    expected = identity.to_id()
    assert a.observation_id == expected
    assert observation().observation_id == expected
    assert b.observation_id == expected
    assert expected == ObservationIdentity.from_record(b.to_dict()).to_id()
    assert expected == ObservationIdentity.from_record(json.loads(a.to_json())).to_id()
    assert expected == ObservationIdentity.from_record(dict(reversed(list(a.to_dict().items())))).to_id()
    assert Observation.from_dict(json.loads(a.to_json())).observation_id == expected
    assert Observation.from_dict(b.to_dict()).observation_id == expected


def test_caller_and_replay_cannot_supply_conflicting_id():
    with pytest.raises(TypeError, match="observation_id"):
        observation(observation_id="arbitrary")
    record = json.loads(observation().to_json())
    record["observation_id"] = "arbitrary"
    with pytest.raises(ValueError, match="stored observation_id does not match"):
        Observation.from_dict(record)


@pytest.mark.parametrize("field,changed", [
    ("instrument", "ETH/USDT"),
    ("timestamp_ms", 1001),
    ("timeframe", "4h"),
    ("horizon", "swing"),
    ("kind", "volume"),
    ("source", "other_feed"),
    ("source_ref", "candles:other-record"),
    ("transform_version", "raw.v2"),
])
def test_material_identity_field_change_changes_id(field, changed):
    original = observation()
    assert observation(**{field: changed}).observation_id != original.observation_id


def test_input_and_observation_are_deeply_isolated_and_immutable():
    payload = {"close": 125.5, "flags": ["closed"]}
    uncertainty = {"bounds": [125.0, 126.0]}
    o = observation(value=payload, uncertainty=uncertainty)
    payload["flags"].append("changed")
    uncertainty["bounds"][0] = 0
    assert o.to_dict()["value"]["flags"] == ["closed"]
    assert o.to_dict()["uncertainty"]["bounds"] == [125.0, 126.0]
    with pytest.raises(FrozenInstanceError):
        o.instrument = "ETH/USDT"
    with pytest.raises(TypeError):
        o.value["close"] = 0
    with pytest.raises(TypeError):
        o.uncertainty["bounds"][0] = 0
    exported = o.to_dict()
    exported["value"]["flags"].append("changed")
    assert o.to_dict()["value"]["flags"] == ["closed"]


def test_freshness_is_evaluated_at_explicit_cut():
    o = observation()
    assert o.age_ms(1100) == 100
    assert o.is_fresh_at(1500)
    assert not o.is_fresh_at(1501)
    with pytest.raises(ValueError, match="not yet captured"):
        o.age_ms(1099)
    assert not o.is_fresh_at(1099)
    assert not observation(max_age_ms=None).is_fresh_at(1100)


@pytest.mark.parametrize("quality,value", [
    (Quality.MISSING, None),
    (Quality.STALE, 12.0),
    (Quality.INVALID, 12.0),
    (Quality.SUSPECT, 12.0),
])
def test_nonvalid_quality_is_not_fresh(quality, value):
    o = observation(quality=quality, value=value)
    assert o.to_dict()["quality"] == quality.value
    assert not o.is_fresh_at(1100)


def test_bad_or_future_data_is_refused():
    with pytest.raises(ValueError, match="availability"):
        observation(available_at_ms=1101)
    with pytest.raises(ValueError, match="VALID requires established availability"):
        observation(available_at_ms=None)
    with pytest.raises(ValueError, match="MISSING cannot carry a value"):
        observation(quality=Quality.MISSING)
    with pytest.raises(TypeError, match="JSON-compatible"):
        observation(value=pd.DataFrame({"close": [1.0]}))
    with pytest.raises(ValueError, match="finite"):
        observation(value=float("nan"))


def test_availability_event_and_capture_boundaries():
    assert observation(available_at_ms=1000).available_at_ms == 1000
    assert observation(available_at_ms=1100).available_at_ms == 1100
    with pytest.raises(ValueError, match="availability cannot precede event time"):
        observation(available_at_ms=999)
    with pytest.raises(ValueError, match="availability cannot follow observation time"):
        observation(available_at_ms=1101)


def test_snapshot_adapter_does_not_mutate_snapshot_or_embed_frames():
    frame = pd.DataFrame({"close": [125.5]})
    snapshot = Snapshot(symbol="BTC/USDT", ts="2026-09-23T10:00:00+00:00",
                        price=125.5, dfs={"1h": frame})
    before = snapshot.__dict__.copy()
    o = SnapshotObservation.price(snapshot, timeframe="1h",
                                  source_ref="capture:cycle-7")
    assert snapshot.__dict__ == before
    assert snapshot.dfs["1h"] is frame
    assert o.value == 125.5
    assert o.source_ref == "capture:cycle-7"
    assert o.observation_id == ObservationIdentity.from_record(o.to_dict()).to_id()
    assert SnapshotObservation.price(snapshot, timeframe="1h", source_ref="capture:cycle-7").observation_id == o.observation_id
    assert o.available_at_ms is None
    assert o.quality is Quality.SUSPECT
    assert not o.is_fresh_at(o.observed_at_ms)
    assert "125.5" in o.to_json()
    assert "DataFrame" not in o.to_json()
