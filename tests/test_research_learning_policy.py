"""Production research policy is investigation priority, not performance authority."""
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from trader.learning import foundation as L, research_policy as P, targets as T, dispatch as D


def view(**changes):
    body=dict(kind=L.Kind.EXECUTED,boundary=L.Boundary.REALIZED,
              lineage=dict(strategy_id='s',version_id='v',spec_hash='h'),
              observation=dict(monetary_status='VERIFIED',net_pnl=-2.5))
    body.update(changes)
    return SimpleNamespace(**body)


@pytest.mark.parametrize('changes',[
    dict(kind=L.Kind.MISSED), dict(boundary=L.Boundary.COUNTERFACTUAL),
    dict(lineage={'strategy_id':'s'}),dict(observation={'net_pnl':-2.5}),
    dict(observation={'monetary_status':'VERIFIED','net_pnl':float('nan')}),
    dict(observation={'monetary_status':'VERIFIED','net_pnl':5}),
])
def test_no_investigation_priority_from_unknown_or_counterfactual_money(changes):
    assert not P.sufficient(view(**changes))[0]


def test_registered_policy_requests_investigation_only(monkeypatch):
    facts=view()
    monkeypatch.setattr(D,'view',lambda _:facts)
    current=dict(context=asdict(P.context(facts)),value=None)
    status,value,reason=P.evaluate(object(),current)
    assert status==L.Status.APPLICABLE and value=={'priority':'HIGH'}
    assert P.rule().target==L.Target.RESEARCH
    current['value']=value
    assert P.evaluate(object(),current)[0]==L.Status.INSUFFICIENT
    current['context']=dict(current['context'],subject_id='other')
    assert P.evaluate(object(),current)[0]==L.Status.CONFLICTING


def test_forged_evidence_cannot_reach_policy():
    class Bad:
        quality='VERIFIED_REPLAY'
        historical_json='{}'
    rows,proposals=D.evaluate(None,{},Bad(),T.PRODUCTION_RULES)
    assert not proposals and rows[0]['reason']=='REPLAY_INCOMPLETE'

from tests.test_decision_sources import producer, chain, exact  # noqa: F401
def test_verified_loss_changes_later_research_priority(producer,tmp_path,monkeypatch):  # noqa: F811
    from tests.test_whole_trade_accounting import source,Venue,seal
    from trader.engine import trade_accounting as A
    from trader.learning import capture as C, producers, foundation as L
    import json
    from trader.observability import accounting as W
    from datetime import datetime,timezone
    j,d,_,cfg,_,_,ai,_,v=producer
    cut=ai.as_of_ms
    src=source();venue=Venue()
    # Genuine losing fills with matching booking snapshots, not an edited P&L label.
    venue.fills[1].update(price=90, realizedPnl=-10)
    venue.fills[2].update(price=80, realizedPnl=-20)
    src['trade'].update(exit_price=80,realized_pnl=-32)
    src['receipts'][1]['after']['realized_pnl']=-11
    src['receipts'][2]['before']['realized_pnl']=-11
    src['receipts'][2]['after'].update(exit_price=80,realized_pnl=-32)
    for t in [src['trade']]+[t for r in src['receipts'] for t in (r['before'],r['after']) if t]:
        t['decision_id']=d.id;t['strategy_id']=v['strategy_id']
        for key in ('opened_at','closed_at'):
            if t[key]:
                # Each snapshot is a separate dict, so clocks shift exactly once.
                from trader.cognition.outcomes import timestamp
                t[key]=datetime.fromtimestamp((timestamp(t[key])+cut)/1000,timezone.utc).isoformat()
    for r in src['receipts']:r['observed_ms']+=cut;seal(r)
    src['observed_ms']+=cut;seal(src)
    for rows,key in ((venue.fills,'time'),(venue.funding,'time'),(venue.events,'fundingTime')):
        for row in rows:row[key]+=cut
    identity=dict(status='VERIFIED',strategy_id=v['strategy_id'],version_id=v['version_id'],spec_sha256=v['spec_hash'],
                  decision=dict(decision_id=d.id,cycle_id=d.cycle_id))
    with j._tx() as db:
        t=src['trade'];columns=list(t)+['entry_identity_json']
        db.execute('INSERT INTO trades('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(t.values())+[json.dumps(identity)])
        for receipt in src['receipts']:
            db.execute('INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES (?,?)',('trade',json.dumps(receipt)))
        C.record_action(db,'decision:'+d.id,dict(action='EXECUTED'),cut+1000)
        C.record_action(db,'decision:'+d.id,dict(ok=True,config_risk_sha256=L.digest(cfg['risk'])),cut+1,risk=True)
    before={table:j.query('SELECT * FROM '+table) for table in ('trades','decisions','state_kv','control_events','strategy_versions')}
    monkeypatch.setattr(A.time,'time',lambda:(cut+5000)/1000)
    directory=tmp_path/'worker'
    # Simulate crash after journal commit but before queue delivery marker.
    original=W.deliver_completed
    monkeypatch.setattr(W,'deliver_completed',lambda *args:dict(delivered=0,retry=1))
    first=W.step(j.db_path,directory,lambda:venue,now_ms=cut+5000)
    assert first['jobs']=={'complete':1}
    monkeypatch.setattr(W,'deliver_completed',original)
    report=W.step(j.db_path,directory,lambda:pytest.fail('complete receipt fetched after restart'),now_ms=cut+6000)
    assert report['learning_delivery']['delivered']==1
    rows=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")
    assert len(rows)==1
    oid=rows[0]['outcome_id'];raw=producers.materialize(j._conn(),oid)
    assert raw['authoritative'],[(x['role'],x['reason']) for x in C.manifest(j._conn(),oid)['dependencies'] if x['required'] and x['status']!='AVAILABLE']
    obs=json.loads(raw['outcome']['observation_json'])
    assert (obs['gross_pnl'],obs['fees'],obs['funding'],obs['net_pnl'])==(-30,3,-2,-35)
    assert raw['outcome']['boundary']=='REALIZED' and raw['proposal']['proposed_json'] is None
    assert before=={table:j.query('SELECT * FROM '+table) for table in before}
    assert W.step(j.db_path,directory,lambda:pytest.fail('duplicate fetched'),now_ms=cut+7000)['learning_delivery']['delivered']==0
    assert j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")==rows
    # Delivery itself is idempotent, even across a fresh process / new import clock.
    artifact=json.loads(next(directory.glob('*.json')).read_text())
    assert producers.deliver_accounting(j.db_path,artifact)==oid
    assert j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")==rows

    from trader.learning import runtime
    _,_,_,evidence=chain(j,oid)
    rows = D.dispatch_evidence(j,cfg,evidence,at_ms=cut+8000)
    assert any(r['result']=='PROPOSED' for r in rows['results']), rows
    receipts=runtime.checkpoint(j,cfg,at_ms=cut+9000)
    assert any(r['result']=='APPLIED' and r['rule_id']==P.RULE_ID for r in receipts), receipts
    tasks=P.tasks(j,at_ms=cut+10000)
    assert len(tasks)==1 and tasks[0]['subject']=='strategy:'+v['strategy_id']+':'+v['version_id']
    assert P.tasks(j,at_ms=cut+8500)==[]  # the later application cannot leak backwards
    assert before=={table:j.query('SELECT * FROM '+table) for table in before}

    # The same governed task becomes a frozen input of the numerical research
    # proposer, not merely a log entry. A later HIGH cannot alter an old plan.
    from trader.research.compose.proposer import GraphProposer
    from trader.research.closure.store import Store
    from tests.test_compose_core import spec
    from unittest.mock import Mock
    llm=SimpleNamespace(available=True,chat_json=Mock(return_value={'proposal':spec()}))
    store=Store(tmp_path/'later-research.db')
    proposer=GraphProposer(llm,store,tf='4h',study_id='later',learning_tasks=tasks)
    status,reason,candidate,_=proposer({'question_id':'after-loss','passage':'Investigate the retained loss'},'BRAIN',{})
    assert status=='COMPILED',(status,reason)
    assert tasks[0]['application_id'] in llm.chat_json.call_args.args[0]
    frozen=store.all_once('proposal_contexts')[0]
    assert frozen['verified_outcome_tasks']==tasks
    store.close()
