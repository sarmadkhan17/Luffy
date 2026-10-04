"""Stage8 offline proof. Temporary journals, fake retrieval, no venue calls."""
import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trader.core.journal import Journal
from trader.engine.state import ControlStateMachine
from trader.owner import approvals as A, queries as Q
from trader.owner.contract import OwnerRequest
from trader.owner.service import OwnerService
from tests.test_historical_outcome_capture import prospective, finish
from tests.test_strategy_factory_handoff import cfg, _journal, _to_approval, T0, DAY
from tests.test_external_research_router import registered, FakeChild
from tests.owner_frontend_fixture import make_app, TOKEN
from tests.test_portfolio_allocator import test_only_models


def proposal(j, cfg=None, action='paid_spend', ms=None):
    ms = int(time.time()*1000) if ms is None else ms
    context = dict(scope='fixture-only exact scope', cost_usd=7, safety='no execution')
    key = 'owner_proposal_context:' + action
    j.kv_set(key, json.dumps(context))
    return A.propose(j, cfg or {}, action_type=action, affected_object=action,
                     reason='TEST_ONLY', required_action='approve or reject exact context',
                     bindings=[A.binding(j, 'context', key)], context=context,
                     created_at_ms=ms, valid_until_ms=ms+60000)


def service(j, cfg=None):
    return OwnerService(j, ControlStateMachine(j), resume=lambda *a, **k: pytest.fail('resume called'),
                        approval_cfg=cfg)


def request(item, decision='APPROVED', rid='dashboard-stage8-request-1'):
    return OwnerRequest(rid, 'approval_decision', 'dashboard', 'session', time.time(),
                        args=dict(item_id=item['item_id'], binding_hash=item['binding_hash'], decision=decision))


def test_grounded_no_trade_and_entire_retained_chain(prospective):
    j,d,_ = prospective
    finish(j,d)
    before = j._conn().total_changes
    out = Q.query(j,'decision',d.id)
    assert out['status']=='AVAILABLE'
    kinds = {r['kind'] for r in out['records']}
    assert {'decision','world','context','portfolio','risk_config','outcome','action'} <= kinds
    assert any(r['value'].get('action')=='NO_ALLOCATION' for r in out['records'] if isinstance(r['value'],dict))
    assert all(r['record_id'] and r['sha256'] and r['source'] for r in out['records'])
    assert any(r['available_at'] is not None and r['verification']=='VERIFIED' for r in out['records'])
    assert j._conn().total_changes == before


def test_enter_record_is_not_venue_or_invented_sizing(tmp_path, monkeypatch):
    app,j,_=make_app(tmp_path,monkeypatch)
    c=TestClient(app)
    from trader.core.types import Decision, Action
    j.log_decision(Decision('stage8-entry','c1','BTC/USDT',Action.BUY,.8,.2,.9,[],[],
                            executed=True,size_usdt=100))
    ids=j.query('SELECT id FROM decisions WHERE executed=1')
    assert ids
    out=c.get('/owner-api/v1/query/decision',params={'identity':ids[0]['id']},headers={'x-luffy-token':TOKEN}).json()
    assert out['status']=='AVAILABLE'
    assert out['records'][0]['verification']=='RECORDED'
    assert 'venue verification' in out['semantics']
    assert out['unavailable']


def test_research_exact_source_trace_empty_and_inconclusive(tmp_path):
    from trader.research.external_research import research_pass
    j=registered(tmp_path)
    bank=research_pass(j,child=FakeChild([]))['results'][0]['bank']
    out=Q.query(j,'research',bank['bank_object_id'])
    r=out['records'][0]
    assert r['verification']=='VERIFIED'
    assert r['value']['question']['question_id']==bank['question']['question_id']
    assert r['value']['sources'] and r['value']['searches']
    assert r['value']['result_status']=='INCONCLUSIVE'
    assert r['value']['experiments']==[]
    assert r['value']['contradictory_evidence']['status']=='NOT_CLASSIFIED'
    assert r['value']['limitations'] and r['value']['cost']['paid_cost_usd']==0
    assert 'not predictive edge' in str(out['unavailable'])


def test_standard_research_view_exposes_exact_linked_evidence_values(tmp_path):
    from tests.test_strategy_decay_research_bank import _filed
    j,_=_filed(tmp_path)
    bank=j.research_bank_objects()[0]
    before=j._conn().total_changes
    out=Q.query(j,'research',bank['bank_object_id'])
    assert {r['kind'] for r in out['records']}=={'research','research_evidence'}
    evidence=next(r for r in out['records'] if r['kind']=='research_evidence')
    stored=j.query('SELECT canonical_json FROM research_evidence WHERE evidence_id=?',(bank['evidence_id'],))[0]
    assert evidence['value']==json.loads(stored['canonical_json'])
    assert evidence['verification']=='VERIFIED' and j._conn().total_changes==before


def test_portfolio_exact_cut_candidates_rejection_intent_risk(tmp_path,monkeypatch,test_only_models):
    from tests.paper_cost_evidence_fixture import register_test_cost_evidence
    register_test_cost_evidence(monkeypatch)
    from trader.portfolio.runtime import Consumer
    from tests.test_portfolio_allocator import inputs, candidate
    root=tmp_path; (root/'data').mkdir()
    j=Journal(root/'data/j.db')
    body=Consumer(root/'data/runtime-portfolio.db').consume(inputs(candidate()),processed_at=2000)
    out=Q.query(j,'portfolio',body['portfolio_cut_id'],root=root)
    r=out['records'][0]
    assert r['value']['proposal']==body['proposal']
    assert r['value']['trade_intents']==body['trade_intents']
    assert r['value']['risk_decisions']==body['risk_decisions']
    assert r['value']['event_receipt']['inputs']['current']['candidates']
    assert 'not current book' in str(out['unavailable'])


@pytest.mark.parametrize('kind',Q.KINDS)
def test_missing_is_unavailable_not_zero(tmp_path,kind):
    j=Journal(tmp_path/'j.db')
    out=Q.query(j,kind,'absent-record')
    assert out['status']=='UNAVAILABLE' and out['records']==[]
    assert out['unavailable']


@pytest.mark.parametrize('action',A.CLASSES[1:])
def test_all_approval_classes_have_exact_binding(tmp_path,action):
    j=Journal(tmp_path/'j.db'); p=proposal(j,action=action)
    item=A.items(j,{})['items'][0]
    assert item['item_id']==p['item_id'] and len(item['binding_hash'])==64
    assert item['bindings'][0]['sha256'] and item['config_sha256']
    assert item['validity']=='VALID' and item['available_at_ms']==item['created_at_ms']
    r=service(j).execute(request(item))
    assert r.status==('REFUSED' if action=='serious_recovery' else 'ACCEPTED')
    assert j.kv_get('control_state') is None


def test_approval_replay_actor_audit_and_hash_refusal(tmp_path):
    j=Journal(tmp_path/'j.db'); proposal(j); s=service(j)
    item=A.items(j,{})['items'][0]; req=request(item)
    a=s.execute(req); b=s.execute(req)
    assert a.status=='ACCEPTED' and b.replayed
    assert a.data==b.data and a.principal==b.principal=='owner'
    assert a.audit_event_ids and b.audit_event_ids
    assert len(j.query('SELECT * FROM owner_approval_receipts'))==1
    forged=request(dict(item,binding_hash='0'*64),rid='dashboard-stage8-forged-1')
    assert s.execute(forged).status=='REFUSED'
    same=request(item,decision='REJECTED')
    assert s.execute(same).status=='REFUSED'
    unauthorized=OwnerRequest('stage8-unknown-actor','approval_decision','dashboard','stranger',time.time(),args=req.args)
    assert s.execute(unauthorized).status=='REFUSED'


@pytest.mark.parametrize('change', ['config','scope','cost','safety','expiry','superseded'])
def test_prior_approval_is_stale_when_relevant_context_changes(tmp_path,change):
    j=Journal(tmp_path/'j.db'); p=proposal(j); s=service(j)
    item=A.items(j,{})['items'][0]; assert s.execute(request(item)).status=='ACCEPTED'
    cfg={}; at=int(time.time()*1000)
    if change=='config': cfg={'risk':{'limit':.02}}
    elif change=='expiry': at=p['valid_until_ms']
    elif change=='superseded': proposal(j,ms=p['created_at_ms']+1)
    else:
        v=dict(p['context']); v[change]='changed'
        j.kv_set('owner_proposal_context:paid_spend',json.dumps(v))
    item=next(i for i in A.items(j,cfg,now_ms=at)['items'] if i['item_id']==p['item_id'])
    assert item['validity']=='STALE' and item['status']=='STALE' and item['receipt']


def test_factory_request_reuses_exact_version_and_policy_staleness(tmp_path,cfg):
    j,_=_journal(tmp_path); v,p=_to_approval(j,cfg)
    at=T0+31*DAY
    item=next(i for i in A.items(j,cfg,now_ms=at)['items'] if i['action_type']=='first_live')
    assert item['affected_object']==v['version_id']
    assert item['bindings']['spec_hash']==v['spec_hash'] and item['validity']=='VALID'
    from copy import deepcopy
    changed=deepcopy(cfg)
    changed['strategies']['paper_probation_trades']=999999
    stale=next(i for i in A.items(j,changed,now_ms=at)['items'] if i['action_type']=='first_live')
    assert stale['validity']=='STALE'


def test_first_live_owner_gateway_records_approval_without_activation(tmp_path,cfg):
    from trader.strategy import factory_handoff as F
    j,_=_journal(tmp_path); v,_=_to_approval(j,cfg)
    at=T0+31*DAY
    s=OwnerService(j,ControlStateMachine(j),resume=lambda *a,**k: pytest.fail('recovery'),
                   approval_cfg=cfg,clock=lambda:at/1000)
    item=next(i for i in A.items(j,cfg,now_ms=at)['items'] if i['action_type']=='first_live')
    req=OwnerRequest('stage8-firstlive-exact-owner','approval_decision','dashboard','session',at/1000,
                     args=dict(item_id=item['item_id'],binding_hash=item['binding_hash'],decision='APPROVED'))
    assert s.execute(req).status=='ACCEPTED' and s.execute(req).replayed
    assert F.state_of(j,v['version_id'])==F.APPROVED_FIRST_LIVE
    assert j.kv_get('control_state') is None
    assert len(j.query('SELECT * FROM strategy_approval_decisions'))==1
    changed=dict(cfg,stage8_config_revision='changed')
    stale=next(i for i in A.items(j,changed,now_ms=at)['items'] if i['action_type']=='first_live')
    assert stale['validity']=='STALE' and 'approved_configuration_changed' in stale['invalid_reasons']


def test_production_code_artifact_change_invalidates_approval(tmp_path):
    root=tmp_path; (root/'data').mkdir(); (root/'trader').mkdir()
    j=Journal(root/'data/j.db'); code=root/'trader/example.py'; code.write_text('old=1\n')
    context=dict(architecture='TEST_ONLY',version='1',code_path='trader/example.py')
    key='owner_proposal_context:production_code'; j.kv_set(key,json.dumps(context))
    now=int(time.time()*1000)
    A.propose(j,{},action_type='production_code',affected_object='example',reason='TEST_ONLY',required_action='review',
              bindings=[A.binding(j,'context',key),A.binding(j,'artifact','trader/example.py')],context=context,
              created_at_ms=now,valid_until_ms=now+60000)
    item=A.items(j,{})['items'][0]; assert service(j).execute(request(item)).status=='ACCEPTED'
    code.write_text('changed=2\n')
    assert A.items(j,{})['items'][0]['validity']=='STALE'


def test_world_query_retains_revision_identity_and_rejects_corrupt_hash(tmp_path):
    j=Journal(tmp_path/'j.db')
    body=dict(target='WORLD_MODEL_PROBABILITIES',context={'claim_id':'TEST_ONLY'},revision=1,
              value={'confidence':.4},previous_hash='0'*64)
    with j._tx() as db:
        db.execute('CREATE TABLE learning_target_revisions(target TEXT, context_id TEXT, revision INTEGER, payload TEXT, sha256 TEXT, application_id TEXT)')
        db.execute('INSERT INTO learning_target_revisions VALUES(?,?,?,?,?,?)',
                   (body['target'],'TEST_ONLY',1,A.canonical(body),A.digest(body),'app-TEST_ONLY'))
    before=j._conn().total_changes
    out=Q.query(j,'world','TEST_ONLY')
    assert out['records'][0]['value']['application_id']=='app-TEST_ONLY'
    assert out['records'][0]['value']['previous_hash']=='0'*64
    assert out['records'][0]['verification']=='RECORDED' and j._conn().total_changes==before
    with j._tx() as db: db.execute("UPDATE learning_target_revisions SET sha256='corrupt'")
    with pytest.raises(ValueError,match='learned_revision_integrity'): Q.query(j,'world','TEST_ONLY')


def test_chat_uses_only_typed_queries_and_refuses_ungrounded_facts(tmp_path):
    from trader.chat.agent import AnalystAgent
    from trader.chat.tools import GROUNDED_SCHEMAS
    j=Journal(tmp_path/'j.db')
    class Fake:
        def chat_tools(self,messages,tools,**kwargs):
            assert tools==GROUNDED_SCHEMAS
            return SimpleNamespace(content='Invented equity 9000',tool_calls=None)
    agent=AnalystAgent(j,Fake())
    assert 'UNAVAILABLE' in agent.run('what is equity?')
    assert agent._exec('execute', '{}')['error']
    assert agent._exec('shell', '{"cmd":"true"}')['error']
    assert agent._exec('owner_query', '{"kind":"sql","identity":"SELECT *"}')['error']
    assert j.kv_get('control_state') is None


def test_knowledge_lenses_event_vs_mtime_code_without_graphify(tmp_path,monkeypatch):
    app,j,_=make_app(tmp_path,monkeypatch)
    p=tmp_path/'knowledge/code.md'; p.write_text('---\nsource_paths:\n - trader/example.py\n - ../secret.py\n---\n# Code reference\n')
    (tmp_path/'trader').mkdir(); (tmp_path/'trader/example.py').write_text('value=1\n')
    c=TestClient(app); out=c.get('/owner-api/v1/knowledge',headers={'x-luffy-token':TOKEN}).json()
    assert all(out['lenses'][k]['available'] for k in ('Knowledge','Evidence','Timeline','Code'))
    assert any(n['lenses']==['Code'] and 'SHA256' in n['provenance']['summary'] for n in out['nodes'])
    assert any(n['provenance']['time_basis']=='event' for n in out['nodes'])
    assert any(n['provenance']['time_basis']=='file_modified' for n in out['nodes'])
    assert not any('secret' in n['id'] for n in out['nodes'])
    tree=ast.parse(Path('trader/owner/knowledge.py').read_text())
    assert not any('graphify' in (getattr(n,'module','') or '') for n in ast.walk(tree) if isinstance(n,ast.ImportFrom))


def test_owner_query_auth_and_decision_uses_same_gateway(tmp_path,monkeypatch):
    app,j,g=make_app(tmp_path,monkeypatch); c=TestClient(app)
    for path in ('query/decision','needs-you'):
        assert c.get('/owner-api/v1/'+path).status_code==401
    body=dict(item_id='a'*64,binding_hash='b'*64,decision='APPROVED',request_id='stage8-browser-request-001',issued_at_ms=time.time()*1000)
    assert c.post('/owner-api/v1/needs-you/decision',json=body,headers={'origin':'http://testserver'}).status_code==401
    before=j.kv_get('control_state')
    c.post('/auth/login',json={'password':TOKEN},headers={'origin':'http://testserver'})
    r=c.post('/owner-api/v1/needs-you/decision',json=body,headers={'origin':'http://testserver'})
    assert r.status_code==200 and g.calls[-1]['operation']=='approval_decision'
    assert j.kv_get('control_state')==before


def test_normal_risk_execution_authorities_preserved():
    import subprocess
    root=Path(__file__).resolve().parents[1]
    baseline='53e7c8567c259b2326315a0acdd4b838af030998'
    for name in ('risk.py','risk_intent.py','executor.py'):
        path='trader/engine/'+name
        before=subprocess.check_output(['git','show',baseline+':'+path],cwd=root,text=True)
        after=(root/path).read_text()
        if name!='executor.py':
            assert after==before
            continue
        # This audit authorizes durable partial-exit/recovery repair. Every
        # other method, including all entry authority/reservation gates, must
        # remain exactly equal to the audited starting implementation.
        allowed={'close_partial','recover_entries','recovery_pending'}
        def authorities(source):
            tree=ast.parse(source)
            executor=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Executor')
            return {n.name:ast.dump(n,include_attributes=False) for n in executor.body
                    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name not in allowed}
        assert authorities(after)==authorities(before)


def stored_questions(j, n=51):
    """Offline identity/window fixture, not predictive research evidence."""
    for k in range(n):
        value = dict(question_id=f'q{k}', status='INCONCLUSIVE', question=f'fixture question {k}')
        assert j.record_research_question(dict(question_id=f'q{k}', schema='research-question.v1',
            question_kind='strategy_decay', trigger='TEST_ONLY', scope_kind='strategy', scope_id='fixture',
            source_kind='spec', source_event_id=k, canonical_json=json.dumps(value)), recorded_at_ms=k+1)=='inserted'


def test_oldest_stored_question_is_reachable_by_api_and_chat(tmp_path, monkeypatch):
    from trader.chat.agent import AnalystAgent
    app,j,_=make_app(tmp_path,monkeypatch); stored_questions(j)
    c=TestClient(app); headers={'x-luffy-token':TOKEN}
    first=c.get('/owner-api/v1/query/research',headers=headers).json()
    assert first['questions_page']['next_offset']==50
    second=c.get('/owner-api/v1/query/research?offset=50',headers=headers).json()
    assert [r['identity'] for r in second['records'] if r['kind']=='question']==['q0']
    exact=c.get('/owner-api/v1/query/research?identity=q0',headers=headers).json()
    assert exact['status']=='AVAILABLE' and exact['records'][0]['identity']=='q0'
    # The same advertised typed contract is consumed by the actual chat dispatcher.
    agent=AnalystAgent.__new__(AnalystAgent); agent.journal=j; agent.query_cfg={}
    assert agent._exec('owner_query',json.dumps({'kind':'research','identity':'q0'}))['status']=='AVAILABLE'
    assert agent._exec('owner_query',json.dumps({'kind':'research','offset':50}))['records'][0]['identity']=='q0'


def test_research_route_reads_questions_even_without_quantitative_results(tmp_path):
    from trader.dashboard.owner_reads import research
    j=Journal(tmp_path/'j.db'); stored_questions(j)
    before=j._conn().total_changes
    first=research(j); second=research(j,offset=50)
    assert first['available'] and len(first['questions'])==50
    assert first['questions_page']['has_more']
    assert second['questions'][0]['identity']=='q0'
    assert all(u['field']!='questions' for u in first['unavailable'])
    assert j._conn().total_changes==before


def test_completed_history_cannot_hide_pending_first_live_and_all_pending_are_pageable(tmp_path,cfg):
    from trader.strategy import factory_handoff as F
    j,_=_journal(tmp_path); _,p=_to_approval(j,cfg); at=T0+31*DAY
    for k in range(101):
        key=f'owner_proposal_context:history{k}'; context={'scope':str(k),'cost_usd':k}
        j.kv_set(key,json.dumps(context))
        item=A.propose(j,cfg,action_type='paid_spend',affected_object=f'history{k}',reason='TEST_ONLY',
            required_action='review', bindings=[A.binding(j,'context',key)], context=context,
            created_at_ms=at, valid_until_ms=at+60000)
        feed=A.items(j,cfg,now_ms=at,identity=item['item_id'])['items'][0]
        A.decide(j,cfg,dict(item_id=feed['item_id'],binding_hash=feed['binding_hash'],decision='APPROVED'),
                 actor='dashboard',request_id=f'fixture{k}',now_ms=at)
    feed=A.items(j,cfg,now_ms=at)
    assert feed['items'][0]['item_id']==p['request_id'] and feed['pending_total']==1
    assert feed['page']['next_offset']==100 and len(A.items(j,cfg,now_ms=at,offset=100)['items'])==2
    # Invalidated completed approvals also remain visible as mandatory stale contexts.
    changed=dict(cfg,review_revision='changed')
    all_items=A.items(j,changed,now_ms=at)['items']+A.items(j,changed,now_ms=at,offset=100)['items']
    assert len(all_items)==102 and len({i['item_id'] for i in all_items})==102


def test_configuration_invalidation_is_consumed_by_factory(tmp_path,cfg):
    from copy import deepcopy
    from trader.strategy import factory_handoff as F
    j,_=_journal(tmp_path); v,_=_to_approval(j,cfg); at=T0+31*DAY
    item=A.items(j,cfg,now_ms=at)['items'][0]
    A.decide(j,cfg,dict(item_id=item['item_id'],binding_hash=item['binding_hash'],decision='APPROVED'),
             actor='dashboard',request_id='test-consumer-binding',now_ms=at)
    before=F.eligible_for_first_live(j,v['version_id'],cfg=cfg,available_inputs=[],now_ms=at)
    assert 'approved_configuration_changed' not in before.reasons
    changed=deepcopy(cfg); changed.setdefault('risk',{})['risk_per_trade_pct']=2*changed.get('risk',{}).get('risk_per_trade_pct',1)
    after=F.eligible_for_first_live(j,v['version_id'],cfg=changed,available_inputs=[],now_ms=at)
    assert 'approved_configuration_changed' in after.reasons and not after.eligible
    assert F.live_entry_block(j,v['strategy_id']) is not None


def test_native_factory_decision_configuration_is_bound_and_legacy_missing_refuses(tmp_path,cfg):
    from trader.strategy import factory_handoff as F
    j,_=_journal(tmp_path); v,p=_to_approval(j,cfg); at=T0+31*DAY
    F.record_owner_decision(j,cfg,p['request_id'],'APPROVED',actor='dashboard',decided_at_ms=at)
    d=F._load(j.query('SELECT * FROM strategy_approval_decisions')[0]); req=F.approval_request(j,v['version_id'])
    F.verify_owner_configuration(j,cfg,req,d)
    with pytest.raises(F.HandoffRefused,match='approved_configuration_changed'):
        F.verify_owner_configuration(j,dict(cfg,changed=True),req,d)
    with pytest.raises(F.HandoffRefused,match='owner_approval_configuration_not_recorded'):
        F.verify_owner_configuration(j,cfg,req,{k:v for k,v in d.items() if k!='config_sha256'})
