"""Read-only implementation audit; synthetic journals expose approved pre-ACTIVE binding."""
from pathlib import Path
import json, tempfile
import pytest
from trader.strategy import factory_handoff as F
from tests.authority_factory_fixtures import cfg
from tests.test_universal_strategy_authority import NOW, approved_world

EP = Path(__file__).resolve().parent
out=[]
for target in ['PAUSED', F.DEGRADED, F.RETIRED]:
    with tempfile.TemporaryDirectory(prefix='str04-binding-',dir='/mnt/luffy-data/tmp') as td, pytest.MonkeyPatch.context() as mp:
        config=cfg.__wrapped__(mp)
        j,v,_,_=approved_world(Path(td),config,mp)
        before=F.state_of(j,v['version_id'])
        decision=json.loads(j.query('SELECT canonical_json FROM strategy_approval_decisions')[0]['canonical_json'])
        result=F.govern_version(j,config,v['version_id'],target,actor='strategy_governor',reason_code='TEST-ONLY audit before first activation',at_ms=NOW)
        ev=result['event']; replay=F.governor_events(j,v['version_id'])
        assert before==F.APPROVED_FIRST_LIVE
        assert decision['decision_id'] and ev['binding']['approval_request_id']==decision['request_id']
        assert ev['owner_decision_id'] is None
        assert replay==[ev]
        out.append(dict(from_state=before,to_state=target,status=result['status'],actual_owner_decision_id=decision['decision_id'],event_owner_decision_id=ev['owner_decision_id'],replay_accepted=True,event=ev))
proof=dict(required_proof_satisfied=False,blocker='Approved first Governor PAUSED/DEGRADED/RETIRED events omit the existing owner decision identity; replay accepts them.',ordinary_canonical_writer_only=True,privileged_flag_or_corruption_used=False,provider_calls=0,cases=out)
(EP/'binding-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps({k:v for k,v in proof.items() if k!='cases'},indent=2))
