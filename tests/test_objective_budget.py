"""No venue calls. Exercise shared ledger reservation, failure and pure proof."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research.ledger import Ledger
from trader.research.closure import ClosureRefused,digest,hedge_qualification as Q,objective_admission as O
from trader.research.closure.store import Store
from tests.test_objective_admission import evidence
from tests.test_hedge_qualification import result


def run(tmp_path,worker):
    store=Store(tmp_path/'closure.db')
    env=dict(envelope_id='fixture',policy_class='TEST_ONLY',exposure_proofs={},protected_fingerprint='fixture',
        policy=dict(fdr_target=.1,lord_w0=.05),tf='4h',split=dict(start_ms=0,end_ms=64*40*14400000,cut_ms=0))
    freeze=dict(freeze_id='frozen-fixture',hedge_spec=dict(purpose='protective_beta',objective=dict(metric='max_drawdown_pct')),
        admission_protocol=dict(O.PROTOCOL),ratio_frozen=1.,combo_hash='fixture')
    packet=Q._reserved_assessment(store,freeze,env,SimpleNamespace(hash='fixture',tf='4h',geo='fixed'),{},
        dict(method_id=O.METHOD_ID,version='1',calibration_id='fixture'),lambda x:x,worker,30,5,None,{},tmp_path/'journal.db')
    return packet,store,env,freeze


def test_canonical_budget_precedes_read_and_completed_proof_rederives(tmp_path):
    def worker(fn,payload):
        ledger=Ledger(Journal(tmp_path/'journal.db'))
        spent=ledger.tests()
        assert len(spent)==1 and spent[0]['p']==1. and not spent[0]['rejected']
        assert json.loads(spent[0]['detail'])['look_state']=='pending'
        r=result();r.update(evidence(count=40))
        return ChildResult(ok=True,value=r)
    packet,store,env,freeze=run(tmp_path,worker)
    assert packet['status']=='QUALIFIED',packet['reason']
    proof=packet['protected_result']['error_budget'];ev=packet['statistical_admission']['evidence']
    Q._verify_budget(proof,env,freeze,ev)
    again=O.evaluate(dict(result=packet['protected_result'],alpha=ev['alpha']))
    assert again['admitted'] and again['evidence']==ev
    for edit in ('alpha','duplicate','cut','freeze'):
        p=deepcopy(proof)
        if edit=='alpha':p['sequence'][0]['alpha_t']*=10
        elif edit=='duplicate':p['sequence'].append(deepcopy(p['sequence'][0]))
        else:
            detail=json.loads(p['sequence'][0]['detail'])
            if edit=='cut':detail['cut_ms']=1
            else:detail['evaluated']['freeze_id']='other'
            p['sequence'][0]['detail']=json.dumps(detail)
        with pytest.raises(ClosureRefused):Q._verify_budget(p,env,freeze,ev)
    store.close()


def test_crash_is_spent_and_never_retried(tmp_path):
    calls=[]
    def failed(fn,payload):
        calls.append(1);return ChildResult(ok=False,timed_out=True,error='fixture timeout')
    packet,store,env,freeze=run(tmp_path,failed)
    assert packet['status']=='INCONCLUSIVE' and 'no retry' in packet['reason']
    ledger=Ledger(Journal(tmp_path/'journal.db'))
    assert len(ledger.tests())==1 and ledger.tests()[0]['p']==1.
    packet2=Q._reserved_assessment(store,freeze,env,SimpleNamespace(hash='fixture',tf='4h',geo='fixed'),{},
        dict(method_id=O.METHOD_ID),lambda x:x,failed,30,5,None,{},tmp_path/'journal.db')
    assert packet2['status']=='INCONCLUSIVE' and calls==[1]
    store.close()


def test_economic_failure_cannot_turn_statistical_reliability_into_admission(tmp_path):
    def worker(fn,payload):
        r=result(hedged=10.);r.update(evidence(count=40))
        return ChildResult(ok=True,value=r)
    packet,store,_,_=run(tmp_path,worker)
    assert packet['status']=='ASSESSED_NOT_SUPPORTED'
    ledger=Ledger(Journal(tmp_path/'journal.db'))
    assert ledger.tests()[0]['p']==1. and not ledger.tests()[0]['rejected']
    store.close()
