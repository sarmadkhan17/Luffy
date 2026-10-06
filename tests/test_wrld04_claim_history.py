"""WRLD-04: exact lineage, immutable confidence/validity and missing evidence."""
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
import sqlite3

import pytest

from trader.core.journal import Journal
from trader.learning import consumers as C, foundation as L, targets as T
from trader.world import (ClaimCollection, ClaimCoordinate, ClaimEvidenceRef,
                          HierarchyNode, Horizon, Observation, Quality, Scope,
                          ScopeLevel, WorldClaim, WorldHistory, WorldModel,
                          WorldModelRecord, WorldState, RelationshipCollection,
                          RelationshipCoordinate, RelationshipState)
from trader.world.context import WorldContext
from tests.test_learning_foundation import chain  # noqa: F401
from tests.test_real_learning_integration import isolated, apply  # noqa: F401

GLOBAL = Scope(ScopeLevel.GLOBAL, 'world')
ASSET = Scope(ScopeLevel.ASSET_CLASS, 'crypto')
SCOPE = Scope(ScopeLevel.INSTRUMENT, 'BTCUSDT')
HORIZON = Horizon.INTRADAY
NODES = (HierarchyNode(GLOBAL), HierarchyNode(ASSET, GLOBAL), HierarchyNode(SCOPE, ASSET))


def observation(kind='support', *, quality=Quality.VALID, value=10):
    return Observation('BTCUSDT', 10, 20, '1h', kind, value, 'feed', 'retained:' + kind,
                       quality, available_at_ms=15, horizon=HORIZON.value, max_age_ms=100000)


def claim(cut=100, *, support=None, contradiction=None, quality=Quality.VALID,
          confidence=.6, value='trend'):
    support = observation() if support is None else support
    contradiction = observation('contradiction', value=-10) if contradiction is None else contradiction
    return WorldClaim(ClaimCoordinate(SCOPE, HORIZON, 'trend'), cut, value, quality,
                      confidence, {'reason': 'bounded test evidence'},
                      (ClaimEvidenceRef.from_observation(support),),
                      (ClaimEvidenceRef.from_observation(contradiction),), 'feed', 'test:trend')


def model(item, observations=None, relationships=None):
    records = (observation(), observation('contradiction', value=-10)) if observations is None else observations
    return WorldModel(item.as_of_ms, NODES, (WorldState('BTCUSDT', item.as_of_ms, records),),
                      claims=ClaimCollection(item.as_of_ms, (item,)), relationships=relationships)


def test_exact_support_and_contradiction_lineage_survives_archive(tmp_path):
    original = model(claim())
    path = tmp_path / 'world.json'
    path.write_text(WorldModelRecord.from_model(original).to_json())
    restored = WorldModelRecord.from_json(path.read_text()).reconstruct()
    assert restored.to_json() == original.to_json() and restored.model_id == original.model_id
    item = restored.get_one_claim(SCOPE, HORIZON, dimension='trend')
    records = {ClaimEvidenceRef.from_observation(o): o for o in restored.states[0].observations}
    for ref in item.supporting_evidence + item.contradicting_evidence:
        exact = records[ref]
        assert sha256(exact.to_json().encode()).hexdigest() == ref.record_sha256
        assert exact.source == 'feed' and exact.source_ref.startswith('retained:')
    changed = replace(observation(), value=99)  # same event ID, different exact record
    assert changed.observation_id == observation().observation_id
    with pytest.raises(ValueError, match='exact record'):
        model(item, (changed, observation('contradiction', value=-10)))
    with pytest.raises(ValueError, match='exact record'):
        model(item, (observation(),))  # contradictions cannot be pruned from a receipt


def test_confidence_and_validity_versions_keep_old_contradictions_and_facts(tmp_path):
    base = claim()
    revised = replace(base, as_of_ms=200, confidence=.9, quality=Quality.SUSPECT)
    assert revised.claim_id != base.claim_id
    assert revised.supporting_evidence == base.supporting_evidence
    assert revised.contradicting_evidence == base.contradicting_evidence
    original, newer = model(base), model(revised)
    assert original.states[0].observations == newer.states[0].observations
    with pytest.raises(FrozenInstanceError):
        base.confidence = .99
    with pytest.raises(TypeError):
        base.uncertainty['reason'] = 'overwrite'
    # A later snapshot may omit a contradiction; the earlier record remains retained.
    later = model(replace(revised, as_of_ms=300, contradicting_evidence=()))
    history = WorldHistory((original, newer, later))
    path = tmp_path / 'history.json'
    path.write_text(history.to_json())
    restarted = WorldHistory.from_json(path.read_text())
    for expected in (original, newer, later):
        exact = restarted.get_exact(expected.as_of_ms)
        assert exact.to_json() == expected.to_json() and exact.model_id == expected.model_id
    assert restarted.get_exact(100).claims.claims[0].contradicting_evidence == base.contradicting_evidence
    assert restarted.get_exact(200).claims.claims[0].quality is Quality.SUSPECT
    with pytest.raises(LookupError):
        restarted.get_exact(199)
    with pytest.raises(ValueError, match='ambiguous'):
        WorldHistory((original, model(replace(base, confidence=.8))))


@pytest.mark.parametrize('through_relationship', [False, True])
def test_missing_support_cannot_become_valid_by_confidence(through_relationship):
    missing = observation(quality=Quality.MISSING, value=None)
    item = claim(support=missing, confidence=1)
    relationships = None
    if through_relationship:
        rel = RelationshipState(RelationshipCoordinate(SCOPE, GLOBAL, 'test', HORIZON),
                                100, None, Quality.MISSING, 'feed', 'test:missing', (missing,))
        relationships = RelationshipCollection(100, (rel,))
        item = replace(item, supporting_evidence=(ClaimEvidenceRef.from_relationship(rel),))
    with pytest.raises(ValueError, match='VALID claim requires nonmissing supporting evidence'):
        model(item, (missing, observation('contradiction', value=-10)), relationships)
    unknown = replace(item, quality=Quality.UNKNOWN, value=None, confidence=None)
    retained = model(unknown, (missing, observation('contradiction', value=-10)), relationships)
    exact = WorldModelRecord.from_model(retained).reconstruct()
    assert exact.claims.claims[0].quality is Quality.UNKNOWN
    assert exact.claims.claims[0].value is None
    query = WorldContext(WorldHistory((exact,))).observations(100, SCOPE, HORIZON, kind='support')
    assert query.quality is Quality.UNKNOWN and query.observations[0].value is None


def test_contradictions_alone_cannot_establish_valid_claim():
    with pytest.raises(ValueError, match='VALID claim requires nonmissing supporting evidence'):
        model(replace(claim(), supporting_evidence=()))


def test_missing_cut_or_dimension_never_falls_back_to_confident_claim():
    retained = model(claim(confidence=1))
    assert retained.get_claims(SCOPE, HORIZON, dimension='absent') == ()
    with pytest.raises(LookupError):
        retained.get_one_claim(SCOPE, HORIZON, dimension='absent')
    result = WorldContext(WorldHistory((retained,))).observations(99, SCOPE, HORIZON, kind='support')
    assert result.quality is Quality.UNKNOWN and result.observations == ()


@pytest.mark.parametrize('missing_support', [False, True])
def test_learned_confidence_appends_linked_history_without_rewriting_claim(isolated, missing_support):
    j, cfg, ev, outcome, _ = isolated
    cut = outcome.observed_ms + 100
    if missing_support:
        missing = observation(quality=Quality.MISSING, value=None)
        retained = model(claim(cut, support=missing, quality=Quality.UNKNOWN,
                               value=None, confidence=None),
                         (missing, observation('contradiction', value=-10)))
    else:
        retained = model(claim(cut))
    item = retained.claims.claims[0]
    context = C.claim_context(item, regime='NOT_APPLICABLE', direction='NOT_APPLICABLE', family='NOT_APPLICABLE')
    frozen = retained.to_json()
    apply(j, cfg, ev, cut - 1, L.Target.WORLD, context, {'confidence': .7})
    first = T.read(j, L.Target.WORLD, context)
    apply(j, cfg, ev, cut, L.Target.WORLD, context, {'confidence': .9})
    second = T.read(j, L.Target.WORLD, context)
    assert first['revision'] == 1 and second['revision'] == 2
    assert second['previous_hash'] == first['state_hash']
    rows = j.query('SELECT * FROM learning_target_revisions ORDER BY revision')
    assert len(rows) == 2 and json.loads(rows[0]['payload'])['value']['confidence'] == .7
    for operation in ('UPDATE learning_target_revisions SET revision=9', 'DELETE FROM learning_target_revisions'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with j._tx() as db:
                db.execute(operation)
    reopened = Journal(j.db_path)
    assert T.read(reopened, L.Target.WORLD, context, as_of_ms=cut - 1)['value']['confidence'] == .7
    effective = retained.effective_claim(SCOPE, HORIZON, dimension='trend', learning_journal=reopened,
                                         context_for=lambda c: context)
    assert effective.effective_confidence == .9 and effective.learned['revision'] == 2
    assert effective.claim == item and effective.claim.contradicting_evidence == item.contradicting_evidence
    assert effective.claim.value == (None if missing_support else 'trend')
    assert effective.claim.quality is (Quality.UNKNOWN if missing_support else Quality.VALID)
    assert retained.to_json() == frozen
