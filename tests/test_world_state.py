"""Focused checks for the immutable point-in-time WorldState composition."""

import json
from dataclasses import FrozenInstanceError

import pytest

from trader.world import Observation, Quality, WorldState


def observation(**changes):
    fields = dict(
        instrument="BTC/USDT",
        timestamp_ms=1000,
        observed_at_ms=1100,
        available_at_ms=1050,
        timeframe="1h",
        horizon="intraday",
        kind="price",
        value={"close": 125.5, "flags": ["closed"]},
        unit="USDT",
        source="exchange_public",
        source_ref="candles:BTC/USDT:1h:1000:v1",
        quality=Quality.VALID,
        max_age_ms=500,
        uncertainty={"method": "direct"},
    )
    fields.update(changes)
    return Observation(**fields)


def mixed():
    return [
        observation(),
        observation(kind="funding", value=0.0001, timeframe="8h", horizon=None),
        observation(timeframe="4h", value={"close": 124.0}),
    ]


def test_order_invariance_gives_identical_state():
    obs = mixed()
    a = WorldState("BTC/USDT", 2000, obs)
    b = WorldState("BTC/USDT", 2000, list(reversed(obs)))
    assert a.observations == b.observations
    assert a.state_id == b.state_id
    assert a.to_json() == b.to_json()
    assert a.state_id.startswith("world.state.v1:")
    assert a.schema_version == "world.state.v1"


def test_same_id_different_payload_is_order_independent():
    x = observation(value={"close": 1.0})
    y = observation(value={"close": 2.0})
    assert x.observation_id == y.observation_id
    a = WorldState("BTC/USDT", 2000, [x, y])
    b = WorldState("BTC/USDT", 2000, [y, x])
    assert a.state_id == b.state_id
    assert [o.to_json() for o in a.observations] == [o.to_json() for o in b.observations]


def test_state_is_deeply_immutable_and_to_dict_is_fresh_plain_data():
    state = WorldState("BTC/USDT", 2000, mixed())
    assert isinstance(state.observations, tuple)
    with pytest.raises(FrozenInstanceError):
        state.as_of_ms = 3000
    with pytest.raises(FrozenInstanceError):
        state.observations = ()
    with pytest.raises(FrozenInstanceError):
        state.observations[0].value = None
    with pytest.raises(TypeError):
        state.observations[0].value["close"] = 0.0
    d = state.to_dict()
    assert type(d["observations"]) is list and type(d["observations"][0]["value"]) is dict
    d["observations"][0]["value"]["close"] = -1.0
    d["observations"].clear()
    assert state.to_dict() != d
    assert json.loads(state.to_json()) == state.to_dict()


def test_mutating_source_collection_does_not_change_state():
    source = mixed()
    state = WorldState("BTC/USDT", 2000, source)
    before = (state.state_id, state.to_json())
    source.append(observation(kind="oi", value=5.0))
    source.clear()
    assert (state.state_id, state.to_json()) == before
    assert len(state.observations) == 3


def test_rejects_future_capture_and_future_availability():
    with pytest.raises(ValueError, match="captured"):
        WorldState("BTC/USDT", 1099, [observation(available_at_ms=1000)])
    assert WorldState("BTC/USDT", 1100, [observation()]).as_of_ms == 1100
    future_available = observation(observed_at_ms=1200, available_at_ms=1150)
    with pytest.raises(ValueError, match="available"):
        WorldState("BTC/USDT", 1100, [future_available])
    unknown = observation(available_at_ms=None, quality=Quality.SUSPECT)
    assert WorldState("BTC/USDT", 1100, [unknown]).observations == (unknown,)


def test_rejects_instrument_mismatch_and_invalid_inputs():
    with pytest.raises(ValueError, match="instrument"):
        WorldState("ETH/USDT", 2000, [observation()])
    for bad in ("", "  ", None):
        with pytest.raises(ValueError):
            WorldState(bad, 2000, [])
    for bad in (-1, 1.5, True, "2000", None):
        with pytest.raises(ValueError):
            WorldState("BTC/USDT", bad, [])
    with pytest.raises(TypeError):
        WorldState("BTC/USDT", 2000, [observation().to_dict()])
    with pytest.raises(TypeError):
        WorldState("BTC/USDT", 2000, None)
    with pytest.raises(ValueError):
        WorldState("BTC/USDT", 2000, [], schema_version="world.state.v0")


def test_preserves_all_low_quality_states():
    low = [
        observation(quality=Quality.MISSING, value=None, available_at_ms=None, kind="k_missing"),
        observation(quality=Quality.STALE, kind="k_stale"),
        observation(quality=Quality.SUSPECT, available_at_ms=None, kind="k_suspect"),
        observation(quality=Quality.REPAIRED, kind="k_repaired"),
        observation(quality=Quality.UNSUPPORTED, value=None, available_at_ms=None, kind="k_unsupported"),
        observation(quality=Quality.INVALID, kind="k_invalid"),
        observation(kind="k_valid"),
    ]
    state = WorldState("BTC/USDT", 2000, low)
    assert {o.quality for o in state.observations} == set(Quality)
    assert state.get_one("k_missing").quality is Quality.MISSING
    assert state.get_one("k_missing").value is None


def test_duplicate_coordinates_are_preserved_as_multiset():
    x = observation(value={"close": 1.0})
    y = observation(value={"close": 2.0})
    state = WorldState("BTC/USDT", 2000, [x, y, x])
    assert len(state.observations) == 3
    matches = state.get_observations("price", timeframe="1h", horizon="intraday")
    assert [m.value["close"] for m in matches] == [1.0, 1.0, 2.0]
    with pytest.raises(LookupError, match="found 3"):
        state.get_one("price", timeframe="1h")
    single = WorldState("BTC/USDT", 2000, [x])
    double = WorldState("BTC/USDT", 2000, [x, x])
    assert single.state_id != double.state_id


def test_explicit_retrieval_is_deterministic():
    state = WorldState("BTC/USDT", 2000, mixed())
    assert isinstance(state.get_observations("price"), tuple)
    assert len(state.get_observations("price")) == 2
    assert state.get_one("price", timeframe="4h").value["close"] == 124.0
    assert state.get_one("funding", horizon=None).kind == "funding"
    assert state.get_observations("funding", horizon="intraday") == ()
    assert state.get_observations("price", timeframe="1d") == ()
    assert state.get_observations("absent") == ()
    with pytest.raises(LookupError, match="found 0"):
        state.get_one("absent")
    with pytest.raises(LookupError, match="found 2"):
        state.get_one("price")
    with pytest.raises(ValueError):
        state.get_observations("")
    again = WorldState("BTC/USDT", 2000, list(reversed(mixed())))
    assert again.get_observations("price") == state.get_observations("price")


def test_identity_changes_with_as_of_and_content():
    base = WorldState("BTC/USDT", 2000, [observation()])
    assert base.state_id != WorldState("BTC/USDT", 2001, [observation()]).state_id
    assert base.state_id != WorldState("BTC/USDT", 2000, [observation(value={"close": 125.6})]).state_id
    assert base.state_id != WorldState("BTC/USDT", 2000, [observation(confidence=0.5)]).state_id
    assert base.state_id != WorldState("BTC/USDT", 2000, []).state_id
    assert base.state_id == WorldState("BTC/USDT", 2000, [observation()]).state_id


def test_composition_leaves_observations_unchanged():
    obs = mixed()
    before = [(o.observation_id, o.to_json()) for o in obs]
    state = WorldState("BTC/USDT", 2000, obs)
    assert [(o.observation_id, o.to_json()) for o in obs] == before
    assert all(any(s is o for o in obs) for s in state.observations)
