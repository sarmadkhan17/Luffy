"""Focused checks for versioned perception transforms over a WorldState."""

import json
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pandas as pd
import pytest

from trader.world import Observation, Quality, WorldState
from trader.world.perception import (
    AmbiguousInputError, InputRequirement, Measurement, MissingInputError,
    PerceptionInputError, PerceptionSpec, perceive, resolve_inputs,
)


def observation(**changes):
    fields = dict(
        instrument="BTC/USDT",
        timestamp_ms=1000,
        observed_at_ms=1100,
        available_at_ms=1050,
        timeframe="1h",
        horizon=None,
        kind="price",
        value={"close": 100.0},
        unit="USDT",
        source="exchange_public",
        source_ref="candles:BTC/USDT:1h:1000:v1",
        quality=Quality.VALID,
        max_age_ms=500,
    )
    fields.update(changes)
    return Observation(**fields)


def funding(**changes):
    fields = dict(kind="funding", timeframe="8h", timestamp_ms=900, available_at_ms=950,
                  value=0.0001, unit="rate", source_ref="funding:BTC/USDT:900")
    fields.update(changes)
    return observation(**fields)


def state(*observations, as_of_ms=2000):
    return WorldState(instrument="BTC/USDT", as_of_ms=as_of_ms, observations=observations)


def carry(inputs):
    return Measurement(value={"carry": inputs["rate"].value * inputs["px"].value["close"]},
                       confidence=0.5, uncertainty={"method": "product"})


def spec(**changes):
    fields = dict(
        name="funding_carry",
        version="v1",
        inputs=(InputRequirement("px", "price", "1h"),
                InputRequirement("rate", "funding", "8h")),
        output_kind="carry",
        output_timeframe="8h",
        output_unit="USDT",
        transform=carry,
    )
    fields.update(changes)
    return PerceptionSpec(**fields)


# 1. Spec is immutable and validated.
def test_spec_is_immutable_and_validates_fields():
    s = spec()
    with pytest.raises(FrozenInstanceError):
        s.version = "v2"
    with pytest.raises(FrozenInstanceError):
        s.inputs[0].kind = "funding"
    for bad in (dict(name=""), dict(version=" "), dict(output_kind=""),
                dict(output_timeframe=""), dict(output_unit=""), dict(output_horizon="")):
        with pytest.raises(ValueError):
            spec(**bad)
    with pytest.raises(ValueError):
        spec(inputs=())
    with pytest.raises(ValueError):
        spec(inputs=[InputRequirement("px", "price", "1h")])
    with pytest.raises(TypeError):
        spec(inputs=(("px", "price", "1h"),))
    with pytest.raises(TypeError):
        spec(transform="not callable")
    with pytest.raises(ValueError):
        InputRequirement("px", "price", "")


# 2. Aliases must be unique.
def test_duplicate_aliases_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        spec(inputs=(InputRequirement("x", "price", "1h"),
                     InputRequirement("x", "funding", "8h")))


# 3. Resolution uses exact kind/timeframe/horizon coordinates.
def test_resolution_is_exact_on_all_coordinates():
    px = observation()
    decoys = (observation(timeframe="4h"), observation(horizon="intraday"),
              funding(), funding(timeframe="1h", kind="funding_alt"))
    resolved = resolve_inputs(spec(), state(px, *decoys))
    assert resolved["px"] is not None and resolved["px"].to_json() == px.to_json()
    assert resolved["rate"].to_json() == funding().to_json()

    horizoned = spec(inputs=(InputRequirement("px", "price", "1h", "intraday"),
                             InputRequirement("rate", "funding", "8h")))
    got = resolve_inputs(horizoned, state(px, *decoys))
    assert got["px"].horizon == "intraday"


# 4. Zero matches is a distinct, explicit error.
def test_missing_input_is_explicit():
    with pytest.raises(MissingInputError) as err:
        perceive(spec(), state(observation(), funding(timeframe="4h")))
    assert isinstance(err.value, PerceptionInputError) and isinstance(err.value, LookupError)
    assert err.value.alias == "rate" and err.value.found == 0


# 5. Several matches is a distinct, explicit error; no silent pick.
def test_ambiguous_input_is_explicit():
    dup = funding(value=0.0002, source_ref="funding:alt")
    with pytest.raises(AmbiguousInputError) as err:
        perceive(spec(), state(observation(), funding(), dup))
    assert err.value.alias == "rate" and err.value.found == 2
    assert not isinstance(err.value, MissingInputError)


# 6. The transform sees read-only inputs and cannot mutate them or the state.
def test_inputs_are_not_mutated():
    s = state(observation(), funding())
    before_id, before_json = s.state_id, s.to_json()
    seen = {}

    def hostile(inputs):
        with pytest.raises(TypeError):
            inputs["px"] = None
        with pytest.raises(TypeError):
            inputs["px"].value["close"] = 0.0
        with pytest.raises(FrozenInstanceError):
            inputs["rate"].value = 1.0
        seen.update(inputs)
        return Measurement(value=1.0)

    perceive(spec(transform=hostile), s)
    assert set(seen) == {"px", "rate"}
    assert s.state_id == before_id and s.to_json() == before_json


# 7. Output is an ordinary Observation carrying spec coordinates and provenance.
def test_output_is_ordinary_observation_with_spec_coordinates():
    out = perceive(spec(output_horizon="swing"), state(observation(), funding()))
    assert type(out) is Observation
    assert (out.instrument, out.kind, out.timeframe, out.horizon, out.unit) == (
        "BTC/USDT", "carry", "8h", "swing", "USDT")
    assert out.value["carry"] == pytest.approx(0.01)
    assert out.quality is Quality.VALID and out.confidence == 0.5
    assert out.source == "perception:funding_carry" and out.transform_version == "v1"
    assert out.source_ref.startswith("world.perception.v1:")
    lineage = json.loads(out.source_ref.removeprefix("world.perception.v1:"))
    assert (lineage["spec"]["name"], lineage["spec"]["version"], lineage["as_of_ms"]) == (
        "funding_carry", "v1", 2000)
    assert Observation.from_dict(json.loads(out.to_json())).observation_id == out.observation_id


def test_declared_output_freshness_has_exact_boundary():
    out = perceive(spec(output_max_age_ms=1000), state(observation(), funding()))
    assert out.max_age_ms == 1000
    assert out.is_fresh_at(2000)
    assert not out.is_fresh_at(2001)


def test_output_without_freshness_claim():
    out = perceive(spec(output_max_age_ms=None), state(observation(), funding()))
    assert out.max_age_ms is None
    assert not out.is_fresh_at(2000)


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "1000"])
def test_invalid_output_max_age_is_refused(invalid):
    with pytest.raises(ValueError, match="output_max_age_ms"):
        spec(output_max_age_ms=invalid)


def test_output_max_age_changes_provenance_and_identity():
    inputs = state(observation(), funding())
    one = perceive(spec(output_max_age_ms=1000), inputs)
    two = perceive(spec(output_max_age_ms=1001), inputs)
    assert one.value == two.value
    assert json.loads(one.source_ref.removeprefix("world.perception.v1:"))["spec"]["output_max_age_ms"] == 1000
    assert one.source_ref != two.source_ref
    assert one.observation_id != two.observation_id


# 8. Deterministic: equal inputs give equal output regardless of insertion order.
def test_deterministic_across_insertion_order_and_repeats():
    a = perceive(spec(), state(observation(), funding(), observation(timeframe="4h")))
    b = perceive(spec(), state(observation(timeframe="4h"), funding(), observation()))
    assert a.to_json() == b.to_json() and a.observation_id == b.observation_id


# 9. Provenance hashes the full canonical input records; any payload change moves it.
def test_input_payload_change_changes_source_ref_and_identity():
    base = perceive(spec(), state(observation(), funding()))
    lineage = json.loads(base.source_ref.removeprefix("world.perception.v1:"))
    assert lineage["spec"] == spec().to_dict()
    assert lineage["instrument"] == "BTC/USDT" and lineage["as_of_ms"] == 2000
    assert lineage["inputs"] == [
        {"alias": alias, "observation_id": obs.observation_id,
         "record_sha256": sha256(obs.to_json().encode("utf-8")).hexdigest()}
        for alias, obs in (("px", observation()), ("rate", funding()))
    ]

    # A non-identity field (uncertainty) still changes provenance.
    const = spec(transform=lambda inputs: Measurement(value=1.0))
    one = perceive(const, state(observation(), funding()))
    two = perceive(const, state(observation(uncertainty={"note": "x"}), funding()))
    assert one.value == two.value
    assert one.source_ref != two.source_ref and one.observation_id != two.observation_id

    changed = perceive(spec(), state(observation(value={"close": 101.0}), funding()))
    assert changed.source_ref != base.source_ref
    assert changed.observation_id != base.observation_id


# 10. Spec version and cut are bound into provenance and identity.
def test_version_and_cut_change_identity():
    s = state(observation(), funding())
    v1, v2 = perceive(spec(), s), perceive(spec(version="v2"), s)
    assert v1.value == v2.value
    assert v1.source_ref != v2.source_ref and v1.observation_id != v2.observation_id
    assert v2.transform_version == "v2"
    later = perceive(spec(), state(observation(), funding(), as_of_ms=3000))
    assert later.source_ref != v1.source_ref and later.observation_id != v1.observation_id


# 11. Conservative time: max input event, captured and available at the cut.
def test_conservative_timestamps():
    out = perceive(spec(), state(observation(timestamp_ms=1000), funding(timestamp_ms=900)))
    assert out.timestamp_ms == 1000
    assert out.observed_at_ms == 2000 and out.available_at_ms == 2000
    assert out.source_timestamp_ms is None
    with pytest.raises(ValueError, match="captured"):
        state(observation(observed_at_ms=2001, available_at_ms=1900), funding())
    with pytest.raises(ValueError, match="available"):
        state(observation(observed_at_ms=2001, available_at_ms=2001), funding())


# 12. Unknown input availability: availability stays unknown and VALID is refused.
def test_unknown_availability_blocks_valid():
    unknown = observation(available_at_ms=None, quality=Quality.SUSPECT)
    with pytest.raises(ValueError, match="availability"):
        perceive(spec(), state(unknown, funding()))

    suspect = spec(transform=lambda inputs: Measurement(value=1.0, quality=Quality.SUSPECT))
    out = perceive(suspect, state(unknown, funding()))
    assert out.available_at_ms is None and out.quality is Quality.SUSPECT
    assert not out.is_fresh_at(2000)


def test_transform_must_return_measurement():
    with pytest.raises(TypeError, match="Measurement"):
        perceive(spec(transform=lambda inputs: 1.0), state(observation(), funding()))
    bad_quality = spec(transform=lambda inputs: Measurement(value=1.0, quality="VALID"))
    with pytest.raises(ValueError):
        perceive(bad_quality, state(observation(), funding()))
    missing_value = spec(transform=lambda inputs: Measurement(value=None))
    with pytest.raises(ValueError):
        perceive(missing_value, state(observation(), funding()))


def test_dataframe_cannot_enter_durable_output():
    frame = pd.DataFrame({"close": [1.0]})
    with pytest.raises(TypeError, match="JSON-compatible"):
        perceive(spec(transform=lambda inputs: Measurement(value=frame)),
                 state(observation(), funding()))
