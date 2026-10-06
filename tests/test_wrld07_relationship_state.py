"""WRLD-07 bounded relationship versions and negative proofs (offline)."""
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from trader.world import (HierarchyNode, Horizon, Observation, Quality, Scope, ScopeLevel,
    RelationshipState, RelationshipCoordinate, RelationshipCollection, WorldModel, WorldHistory)
from trader.world.replay import WorldModelRecord
from trader.portfolio.common_factor import world_measurements
from trader.portfolio.allocator import Source

A = Scope(ScopeLevel.INSTRUMENT, 'binance_usdm:futures:BTCUSDT')
B = Scope(ScopeLevel.INSTRUMENT, 'binance_usdm:futures:ETHUSDT')
G = Scope(ScopeLevel.GROUP, 'crypto-peers')
GLOBAL = Scope(ScopeLevel.GLOBAL, 'world')
ASSET = Scope(ScopeLevel.ASSET_CLASS, 'crypto')
COORD = RelationshipCoordinate(A, B, 'rolling-correlation', Horizon.SWING)


def evidence():
    return tuple(Observation(s.identifier, 1000, 1000, '4h', 'return-window',
        {'window_start_ms': 100, 'window_end_ms': 1000, 'bars': 30}, 'test-measured',
        'window-v1', Quality.VALID, available_at_ms=1000, max_age_ms=1000,
        transform_version='rolling-pearson.v1') for s in (A, B))


def measurement():
    return dict(method='rolling-pearson.v1', window_start_ms=100, window_end_ms=1000,
        max_age_ms=500, scope={'source': [A.identifier], 'target': [B.identifier]},
        context={'source_cut_ms': 1000, 'regime': 'TEST_ONLY'},
        assumptions=['aligned closed return windows', 'finite nonzero variance'])


def relationship(**kw):
    args = dict(coordinate=COORD, as_of_ms=1000, value={'correlation': .99, 'beta': 1.2},
        quality=Quality.VALID, source='test-rolling-pearson', source_ref='measured-v1',
        evidence=evidence(), measurement=measurement(),
        uncertainty={'instability': 'unassessed outside this measured window'})
    args.update(kw)
    return RelationshipState(**args)


def model(r):
    return WorldModel(r.as_of_ms, (HierarchyNode(GLOBAL), HierarchyNode(ASSET, GLOBAL),
        HierarchyNode(G, ASSET), HierarchyNode(A, ASSET), HierarchyNode(B, ASSET)),
        relationships=RelationshipCollection(r.as_of_ms, (r,)))


def test_complete_record_and_bounded_current_state():
    r = relationship()
    wire = r.to_dict()
    assert wire['coordinate'] == COORD.to_dict()
    assert wire['measurement'] == measurement()
    assert wire['quality'] == 'VALID' and wire['uncertainty']['instability']
    assert all(ref.record_sha256 for ref in r.evidence_refs)
    assert not r.is_current_at(999)
    assert r.is_current_at(1500) and not r.is_current_at(1501)
    assert r.is_current_at(1000)  # historical query remains relative to its own cut
    with pytest.raises(FrozenInstanceError):
        r.as_of_ms = 2000
    with pytest.raises(TypeError):
        r.measurement['context']['source_cut_ms'] = 2000


@pytest.mark.parametrize('field', list(measurement()))
def test_missing_measurement_contract_refused(field):
    m = measurement(); m.pop(field)
    with pytest.raises(ValueError, match='explicit measurement'):
        relationship(measurement=m)


@pytest.mark.parametrize('quality', [Quality.STALE, Quality.MISSING, Quality.SUSPECT, Quality.UNKNOWN])
def test_bad_evidence_cannot_become_valid_by_high_confidence(quality):
    obs = evidence()
    bad = replace(obs[0], quality=quality, value=None if quality is Quality.MISSING else obs[0].value)
    with pytest.raises(ValueError, match='fresh VALID'):
        relationship(evidence=(bad, obs[1]), confidence=1)


def test_old_evidence_cannot_be_recut_or_rewrapped_as_current():
    with pytest.raises(ValueError, match='stale|fresh VALID'):
        relationship(as_of_ms=2001)
    stale = relationship(quality=Quality.STALE)
    assert not stale.is_current_at(1000)
    src = Source.freeze('relationship-test', dict(record_json=WorldModelRecord.from_model(model(stale)).to_json(), valid_until_ms=9999))
    with pytest.raises(ValueError, match='STALE'):
        world_measurements(src, 1000)
    src = Source.freeze('relationship-test', dict(record_json=WorldModelRecord.from_model(model(relationship())).to_json(), valid_until_ms=9999))
    assert world_measurements(src, 1501)[0]['freshness'] == 'UNKNOWN'


def test_guessed_endpoint_and_group_attribution_refused():
    with pytest.raises(ValueError, match='scope|attribution'):
        relationship(evidence=(evidence()[0],))
    m = measurement(); m['scope']['target'] = [A.identifier]
    with pytest.raises(ValueError, match='attribution'):
        relationship(measurement=m)
    coord = replace(COORD, target=G)
    with pytest.raises(ValueError, match='membership'):
        relationship(coordinate=coord)
    m = measurement(); m['context']['membership_source_refs'] = {G.identifier: 'TEST_ONLY:membership-cut-1000'}
    grouped = relationship(coordinate=coord, measurement=m)
    assert grouped.to_dict()['measurement']['scope']['target'] == [B.identifier]
    src = Source.freeze('relationship-test', dict(record_json=WorldModelRecord.from_model(model(grouped)).to_json(), valid_until_ms=1500))
    assert world_measurements(src, 1000)[0]['instruments'] is None


@pytest.mark.parametrize('kind', ['cointegration', 'attribution', 'permanent-beta', 'permanent-correlation'])
@pytest.mark.parametrize('quality', [Quality.VALID, Quality.SUSPECT, Quality.UNKNOWN, Quality.STALE, Quality.REPAIRED])
def test_unsupported_claims_refused(kind, quality):
    with pytest.raises(ValueError, match='unsupported relationship method'):
        relationship(coordinate=replace(COORD, kind=kind), quality=quality)


@pytest.mark.parametrize('change', ['method', 'window', 'instability', 'context'])
def test_contract_mismatch_refused(change):
    m = measurement(); uncertainty = {'instability': 'unassessed'}
    if change == 'method': m['method'] = 'guessed.v1'
    if change == 'window': m['window_start_ms'] = 200
    if change == 'instability': uncertainty = {}
    if change == 'context': m['context'] = {}
    with pytest.raises(ValueError):
        relationship(measurement=m, uncertainty=uncertainty)


def test_new_version_and_persisted_exact_history(tmp_path):
    first = relationship()
    later = replace(first, as_of_ms=1100, value={'correlation': -.3},
        uncertainty={'instability': 'sign reversal measured at new cut'})
    assert first.relationship_id != later.relationship_id
    history = WorldHistory((model(first), model(later)))
    path = tmp_path / 'history.json'; path.write_text(history.to_json())
    restored = WorldHistory.from_json(path.read_text())
    assert restored.get_exact(1000).relationships.relationships[0].to_json() == first.to_json()
    assert restored.get_exact(1100).relationships.relationships[0].to_json() == later.to_json()
    with pytest.raises(LookupError): restored.get_exact(1050)
    with pytest.raises(ValueError, match='ambiguous'):
        WorldHistory((model(first), model(replace(first, value={'correlation': .3}))))


def test_legacy_archive_exact_replay_does_not_grant_current_status():
    legacy = relationship(schema_version='world.relationship.v1', measurement={})
    raw = WorldModelRecord.from_model(model(legacy)).to_json()
    restored = WorldModelRecord.from_json(raw).reconstruct().relationships.relationships[0]
    assert restored.to_json() == legacy.to_json() and not restored.is_current_at(1000)
    assert 'measurement' not in json.loads(restored.to_json())
    src = Source.freeze('relationship-test', dict(record_json=raw, valid_until_ms=1500))
    assert world_measurements(src, 1000)[0]['freshness'] == 'UNKNOWN'


@pytest.mark.parametrize('value', [{'correlation': 1.01}, {'correlation': True},
    {'correlation': .5, 'cointegration': True}, {'beta': 1}])
def test_invalid_or_unverified_estimate_payload_refused(value):
    with pytest.raises(ValueError): relationship(value=value)


def test_beta_is_bounded_and_method_bound():
    obs = tuple(replace(o, transform_version='ols-beta.v1') for o in evidence())
    m = measurement(); m['method'] = 'ols-beta.v1'
    beta = relationship(coordinate=replace(COORD, kind='beta'), value={'beta': 1.2}, evidence=obs, measurement=m)
    assert beta.is_current_at(1500) and not beta.is_current_at(1501)
    assert WorldModelRecord.from_model(model(beta)).reconstruct().relationships.relationships[0] == beta


@pytest.mark.parametrize('cut', [999, 1001, None])
def test_context_source_cut_must_bind_available_evidence(cut):
    m = measurement(); m['context']['source_cut_ms'] = cut
    with pytest.raises(ValueError): relationship(measurement=m)


def test_pre_fix_v1_wire_replays_byte_for_byte():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs/tracker/evidence/wrld07-relationships-sol1/legacy-v1-record.json'
    wire = path.read_text()
    record = WorldModelRecord.from_json(wire)
    assert record.to_json() == wire
    assert record.reconstruct().relationships.relationships[0].schema_version == 'world.relationship.v1'
    assert not record.reconstruct().relationships.relationships[0].is_current_at(10)


@pytest.mark.parametrize('field', ['context', 'assumptions', 'max_age_ms', 'window_start_ms', 'instability'])
def test_measurement_metadata_changes_create_new_versions(field):
    first = relationship(); m = measurement(); obs = evidence()
    uncertainty = {'instability': 'unassessed outside this measured window'}
    if field == 'context': m['context']['regime'] = 'TEST_ONLY_NEW_REGIME'
    if field == 'assumptions': m['assumptions'].append('TEST_ONLY revised assumption')
    if field == 'max_age_ms': m['max_age_ms'] = 400
    if field == 'window_start_ms':
        m[field] = 200
        obs = tuple(replace(o, value={**o.value, field: 200}) for o in obs)
    if field == 'instability': uncertainty[field] = 'TEST_ONLY observed instability'
    revised = relationship(measurement=m, evidence=obs, uncertainty=uncertainty)
    assert revised.relationship_id != first.relationship_id
    assert first.to_dict()['measurement'] == measurement()
