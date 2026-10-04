"""Future learned revisions through real application, WorldModel and replay."""
from dataclasses import replace
import pytest
from trader.core.journal import Journal
from trader.learning import targets as T, foundation as L, consumers as C
from tests.test_learning_foundation import chain  # noqa: F401
from tests.test_real_learning_integration import isolated, apply  # noqa: F401
from tests.test_attention_telemetry import frames
from trader.observability import attention, world_producer
from trader.world import WorldClaim, ClaimCoordinate, Scope, ScopeLevel, Horizon, Quality, ClaimEvidenceRef, ClaimCollection


def claimed(cut):
    event = attention.capture(frames(1,cut), ['S0/USDT'], 'temporal-world',
        attention.settings({'world_model': True}), cut)
    model, _ = world_producer.produce(event)
    sym = model.states[0].instrument
    obs = next(o for o in model.states[0].observations if o.kind == world_producer.OUTPUT_KIND)
    claim = WorldClaim(ClaimCoordinate(Scope(ScopeLevel.INSTRUMENT,sym), Horizon.INTRADAY,
        world_producer.OUTPUT_KIND), cut, 'descriptive', Quality.SUSPECT, .6,
        {'reason':'TEST_ONLY'}, (ClaimEvidenceRef.from_observation(obs),), (),
        'attention.capture', 'TEST_ONLY')
    model = replace(model, claims=ClaimCollection(cut,(claim,)))
    context = C.claim_context(claim, regime='NOT_APPLICABLE', direction='NOT_APPLICABLE', family='NOT_APPLICABLE')
    return model, context, event


def view(j, model):
    claim = model.claims.claims[0]
    return C.world_claims(j,model,claim.coordinate.scope,claim.coordinate.horizon,
        regime='NOT_APPLICABLE', direction='NOT_APPLICABLE', family='NOT_APPLICABLE')[0]


def test_future_applied_revision_cannot_change_historical_world(isolated):
    j,cfg,ev,o,_ = isolated
    cut = max(1800000000000,o.observed_ms)+1000
    model,ctx,event = claimed(cut)
    assert view(j,model).effective_confidence == .6
    apply(j,cfg,ev,cut+86400000,L.Target.WORLD,ctx,{'confidence':.9})
    assert T.read(j,L.Target.WORLD,ctx)['value']['confidence'] == .9  # latest remains adaptive
    assert view(j,model).effective_confidence == .6
    reopened = Journal(j.db_path)
    assert view(reopened,model).effective_confidence == .6
    got = world_producer.evaluate(event,model=model,learning_journal=reopened)
    assert got['rows'][0]['world_model']['claims'][0]['confidence'] == .6


def test_exact_cut_multiple_revisions_capture_replay_and_reopen(isolated):
    j,cfg,ev,o,_ = isolated
    cut = max(1800000000000,o.observed_ms)+1000
    model,ctx,event = claimed(cut)
    apply(j,cfg,ev,cut-1,L.Target.WORLD,ctx,{'confidence':.7})
    apply(j,cfg,ev,cut,L.Target.WORLD,ctx,{'confidence':.8})
    exact = view(j,model)
    assert exact.effective_confidence == .8 and exact.learned['revision'] == 2
    captured = world_producer.evaluate(event,model=model,learning_journal=j)
    frozen = captured['governed_world_claim_state']
    apply(j,cfg,ev,cut+1,L.Target.WORLD,ctx,{'confidence':.9})
    assert view(Journal(j.db_path),model).effective_confidence == .8
    assert view(j,claimed(cut-1)[0]).effective_confidence == .7
    assert view(j,claimed(cut+1)[0]).effective_confidence == .9
    replay = attention.evaluate_snapshot(dict(event,governed_world_claim_state=frozen),model)
    assert replay['rows'] == captured['rows']
    future = world_producer.evaluate(claimed(cut+1)[2],model=claimed(cut+1)[0],learning_journal=j)
    with pytest.raises(ValueError, match='future|temporal'):
        attention.evaluate_snapshot(dict(event,governed_world_claim_state=future['governed_world_claim_state']),model)
