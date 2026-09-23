"""Optional WorldModel volume_anomaly input to attention: exact acceptance,
explicit no-contribution statuses, and unchanged output without a model."""
import json
from hashlib import sha256

import pytest

from tests.test_cognition_contracts import SYMBOLS, at, make_fixture, no_network  # noqa: F401
from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import load_input
from trader.observability.attention import evaluate_snapshot
from trader.world import (HierarchyNode, Horizon, Observation, Quality, Scope,
                          ScopeLevel, WorldModel, WorldState)

AS_OF = at(45) + 60_000         # one minute into the next bar
ANCHOR = at(45)                     # close of the newest closed 1h bar
GLOBAL = Scope(ScopeLevel.GLOBAL, "world")
ASSET = Scope(ScopeLevel.ASSET_CLASS, "crypto")


def obs(sym="AAA", value=9.0, **kw):
    base = dict(instrument=sym, timestamp_ms=ANCHOR, observed_at_ms=AS_OF,
                available_at_ms=ANCHOR, timeframe="1h", horizon="intraday",
                kind="volume_anomaly", value=value, unit="z_score", source="test",
                source_ref="fixture", quality=Quality.VALID)
    base.update(kw)
    return Observation(**base)


def model(*observations, cut=AS_OF, symbols=SYMBOLS, horizons=tuple(Horizon)):
    by_sym = {}
    for o in observations:
        by_sym.setdefault(o.instrument, []).append(o)
    nodes = [HierarchyNode(GLOBAL), HierarchyNode(ASSET, GLOBAL),
             *(HierarchyNode(Scope(ScopeLevel.INSTRUMENT, s), ASSET) for s in symbols)]
    return WorldModel(cut, nodes, [WorldState(s, cut, os) for s, os in by_sym.items()],
                      horizons)


def attend(world_model=None, **kw):
    ds = load_input(make_fixture())
    return evaluate(ds, AS_OF, CognitionConfig(**kw), "d1", {}, world_model)


def row(world_model, sym="AAA"):
    return attend(world_model)["rows"][sym]


def test_no_model_output_is_unchanged_and_has_no_world_fields():
    a, b = attend(), attend(None)
    dump = lambda r: json.dumps({k: v for k, v in r.items() if k != "index"},
                                default=repr, sort_keys=True)
    assert dump(a) == dump(b)
    assert "world" not in dump(a)
    event = {"input": make_fixture(), "as_of_ms": AS_OF, "scan_id": "s", "scope": {},
             "capture_ms": 0.0, "issues": [], "capture_settings": {}}
    assert json.dumps(evaluate_snapshot(event), sort_keys=True) == \
        json.dumps(evaluate_snapshot(event, None), sort_keys=True)


def test_type_and_exact_cut_are_enforced():
    with pytest.raises(TypeError):
        attend(object())
    with pytest.raises(ValueError, match="cut"):
        attend(model(cut=AS_OF + 1))
    event = {"input": make_fixture(), "as_of_ms": AS_OF, "scan_id": "s", "scope": {},
             "capture_ms": 0.0, "issues": [], "capture_settings": {}}
    with pytest.raises(TypeError):
        evaluate_snapshot(event, {"as_of_ms": AS_OF})
    with pytest.raises(ValueError, match="cut"):
        evaluate_snapshot(event, model(cut=AS_OF - 1))


def test_accepted_observation_competes_by_magnitude_with_provenance():
    o = obs(value=-9.0)
    m = model(o)
    r = row(m)
    assert r["world_model"] == {
        "model_id": m.model_id, "observation_id": o.observation_id,
        "record_sha256": sha256(o.to_json().encode("utf-8")).hexdigest(),
        "value": -9.0, "status": "ok", "reason": "accepted"}
    assert r["components"]["world_volume_anomaly"] == -9.0
    assert r["salience"] == 9.0 and r["dominant"] == "world_volume_anomaly"
    up = row(model(obs(value=9.0)))
    assert up["salience"] == r["salience"] and up["rank"] == r["rank"]
    # A small world value competes but does not displace a larger candle component.
    small = row(model(obs(value=0.0)))
    base = row(None)
    assert small["salience"] == base["salience"] and small["dominant"] == base["dominant"]
    assert small["world_model"]["status"] == "ok"


def test_one_world_observation_changes_rank_deterministically_without_mutation():
    raw = make_fixture()
    raw_before = json.dumps(raw, sort_keys=True)
    o = obs(sym="FFF", value=50.0)
    m = model(o)
    model_before, observation_before = m.to_json(), o.to_json()
    ds = load_input(raw)
    cfg = CognitionConfig()
    baseline = evaluate(ds, AS_OF, cfg, "d1", {})
    first = evaluate(ds, AS_OF, cfg, "d1", {}, m)
    second = evaluate(ds, AS_OF, cfg, "d1", {}, m)
    assert baseline["rows"]["FFF"]["rank"] == 6
    assert first["rows"]["FFF"]["rank"] == 1
    assert first["ranked"][0] == "FFF"
    assert first["rows"]["FFF"]["world_model"]["record_sha256"] == \
        sha256(observation_before.encode("utf-8")).hexdigest()
    assert json.dumps(first["universe"], sort_keys=True) == \
        json.dumps(second["universe"], sort_keys=True)
    assert (json.dumps(raw, sort_keys=True), m.to_json(), o.to_json()) == \
        (raw_before, model_before, observation_before)
    assert not any(key in first["rows"]["FFF"] for key in
                   ("direction", "side", "size", "order", "risk_override"))


def test_other_symbols_without_observation_are_explicitly_absent():
    r = row(model(obs()), "BBB")
    assert (r["world_model"]["status"], r["world_model"]["reason"]) == ("absent", "no_observation")
    assert r["components"]["world_volume_anomaly"] is None
    assert r["salience"] == row(None, "BBB")["salience"]


@pytest.mark.parametrize("m,status,reason", [
    (lambda: model(obs(), obs(source_ref="other")), "ambiguous", "2_observations"),
    (lambda: model(obs(timestamp_ms=at(44), available_at_ms=at(44))), "stale",
     "not_anchor_timestamp"),
    (lambda: model(obs(max_age_ms=60_000)), "ok", "accepted"),
    (lambda: model(obs(max_age_ms=59_999)), "stale", "max_age_exceeded"),
    (lambda: model(obs(quality=Quality.STALE)), "stale", "quality_stale"),
    (lambda: model(obs(quality=Quality.SUSPECT)), "invalid", "quality_suspect"),
    (lambda: model(obs(quality=Quality.UNSUPPORTED)), "unsupported", "quality_unsupported"),
    (lambda: model(obs(quality=Quality.MISSING, value=None)), "absent", "quality_missing"),
    (lambda: model(obs(quality=Quality.SUSPECT, available_at_ms=None)), "invalid",
     "quality_suspect"),
    (lambda: model(obs(value="high")), "invalid", "non_numeric_value"),
    (lambda: model(obs(value=True)), "invalid", "non_numeric_value"),
    (lambda: model(obs(unit="percent")), "unsupported", "unit_not_z_score"),
    (lambda: model(obs(unit=None)), "unsupported", "unit_not_z_score"),
    (lambda: model(obs(timeframe="4h")), "unsupported", "timeframe_mismatch"),
    (lambda: model(obs(horizon="swing")), "absent", "no_observation"),
    (lambda: model(obs(kind="volume")), "absent", "no_observation"),
    (lambda: model(symbols=["BBB"]), "absent", "unknown_scope"),
    (lambda: model(horizons=(Horizon.SWING,)), "unsupported", "horizon_not_declared"),
])
def test_non_accepted_inputs_contribute_nothing(m, status, reason):
    r = row(m())
    assert (r["world_model"]["status"], r["world_model"]["reason"]) == (status, reason)
    if status == "ok":
        return
    base = row(None)
    assert r["components"]["world_volume_anomaly"] is None
    assert (r["salience"], r["dominant"], r["rank"]) == (
        base["salience"], base["dominant"], base["rank"])


def test_unknown_availability_is_refused():
    o = obs(quality=Quality.SUSPECT, available_at_ms=None)
    # VALID cannot be constructed without availability; refusal keeps provenance.
    r = row(model(o))
    assert r["world_model"]["observation_id"] == o.observation_id
    assert r["world_model"]["record_sha256"] == sha256(o.to_json().encode()).hexdigest()


def test_snapshot_rows_carry_world_record():
    event = {"input": make_fixture(), "as_of_ms": AS_OF, "scan_id": "s", "scope": {},
             "capture_ms": 0.0, "issues": [], "capture_settings": {}}
    m = model(obs(value=12.5))
    out = evaluate_snapshot(event, m)
    r = next(x for x in out["rows"] if x["symbol"] == "AAA")
    assert r["world_model"]["model_id"] == m.model_id and r["salience"] == 12.5
    json.dumps(out, allow_nan=False)
