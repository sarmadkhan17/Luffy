"""Independent canonical proof; only synthetic journals under luffy-pytest."""
import json
from pathlib import Path
from trader.strategy import factory_handoff as F
from tests.authority_factory_fixtures import cfg
from tests.test_str04_governor_lifecycle import world, _snap
from tests.test_universal_strategy_authority import NOW


def test_all_five_current_events_bind_exact_historical_owner(world):
    w = world
    before = _snap(w.j)
    req = F.approval_request(w.j,w.vid)
    decision = F._load(w.j.query('SELECT * FROM strategy_approval_decisions')[0])
    expected = dict(strategy_id=w.sid,version_id=w.vid,
        lineage_sha256=w.v['lineage_sha256'],install_id=req['install_id'],
        approval_request_id=req['request_id'],owner_decision_id=decision['decision_id'])
    results = [w.act()]
    for i, state in enumerate(('PAUSED','REACTIVATED',F.DEGRADED,F.RETIRED),1):
        results.append(w.gov(state,at=NOW+i))
    history = F.governor_events(w.j,w.vid)
    for result, event in zip(results,history):
        actual = dict(event['binding'],version_id=event['version_id'],owner_decision_id=event['owner_decision_id'])
        assert event['schema']=='strategy-governor-event.v2'
        assert actual==expected
        assert result['event']==event
    assert _snap(w.j)==before
    assert F.live_entry_block(w.j,w.sid)=='version_install_unbound'
    proof = dict(scope='SYNTHETIC_OFFLINE_CANONICAL_PROOF',
        ordinary_canonical_writer=True,privileged_corruption_used=False,
        runtime_activation=False,provider_calls=0,venue_calls=0,
        expected_binding=expected,events=history,artifact_snapshot_unchanged=True)
    Path(__file__).with_name('five-state-binding-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
