"""Test-only calibrated actions live exclusively here; no runtime registry entry."""
from dataclasses import replace
from copy import deepcopy
import json
import socket
import sqlite3
import time
from types import SimpleNamespace

import pytest
from trader.core.journal import Journal
from trader.learning import foundation as L, targets as T, application as A, runtime as R, consumers as C
from trader.strategy import factory_handoff as F
from tests.test_learning_foundation import chain, get_evidence
from tests.test_decision_sources import producer
from tests.test_candidate_bridge import exact

@pytest.fixture(autouse=True)
def no_venue(monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('venue/network forbidden'))

@pytest.fixture
def isolated(chain,tmp_path):
    outcome,retained,current,version=chain
    journal=Journal(tmp_path/'luffy.db')
    journal.learning_test_only=True
    F.ensure(journal)
    with journal._tx() as db:
        F._insert_version(db,version,outcome.decision_ms-1)
        for state in (F.VALIDATED,F.SHADOW,F.APPROVAL_REQUIRED,F.APPROVED_FIRST_LIVE):
            F._transition(db,version['version_id'],state,'TEST_ONLY',None,'factory',outcome.decision_ms)
    ev=get_evidence(outcome,retained)
    cfg=json.loads(ev.historical_json)['risk_config']
    return journal,cfg,ev,outcome,version

def rule(target,value):
    def evaluate(ev,current):
        return L.Status.APPLICABLE,value,'TEST_ONLY_DETERMINISTIC_ACTION'
    r=T.Rule('test-only.'+target.value,target,L.digest(dict(target=target.value,value=value)),evaluate)
    return {r.rule_id:r},r.rule_id

def apply(j,cfg,ev,ms,target,context,value):
    rules,rid=rule(target,value)
    state=T.read(j,target,context)
    p=T.propose(ev,target,state,rid,rules)
    req=A.request(ev,p,state,A.source_versions(cfg))
    receipt=A.apply(j,cfg,ev,p,req,at_ms=ms,test_registry=rules)
    assert receipt['result']=='APPLIED'
    return receipt,p,req,rules

def consumer_case(target,j,monkeypatch,tmp_path):
    if target==L.Target.ALLOCATION:
        from tests.economics_fixtures import install_models
        from tests.test_portfolio_allocator import candidate,inputs
        from trader.portfolio.allocator import allocate,attach_learning
        install_models(monkeypatch)
        c=candidate()
        i=inputs(c)
        def consume():
            # This case exercises a current adaptive consumer, while the
            # economic fixture keeps its independent original validity cut.
            # Historical-cut rejection has its own dedicated regression test.
            result=json.loads(allocate(attach_learning(replace(i,as_of_ms=int(time.time()*1000)),j)).result_json)
            return result['candidates'][0].get('governed_allocation_context')
        return C.allocation_context(c),{'max_share':0},consume
    if target==L.Target.ATTENTION:
        from tests.test_attention_telemetry import frames,event
        from trader.observability.attention import evaluate_snapshot
        now=int(time.time()*1000)
        data=frames(6,now)
        for item in data.values():item['4h'].loc[29,'volume']=100000
        snapshot=event(now=now,data=data)
        def consume():
            rows=evaluate_snapshot(snapshot,learning_journal=j)['rows']
            return [r['symbol'] for r in sorted(rows,key=lambda r:r.get('rank',100)) if r.get('selected')]
        return C.attention_context('S5/USDT','14400000'),{'priority':'HIGH'},consume
    if target==L.Target.RESEARCH:
        from trader.observability import investigation_research as IR
        path=tmp_path/'investigation.db'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE cases(id TEXT,created_ms INTEGER,payload TEXT); CREATE TABLE updates(id TEXT,case_id TEXT,observed_ms INTEGER);')
            for n,name in enumerate(('old','new')):
                db.execute('INSERT INTO cases VALUES(?,?,?)',(name,n,json.dumps({'measurement':{'family':IR.SOURCE_FAMILY}})))
                db.execute('INSERT INTO updates VALUES(?,?,?)',(name+'-update',name,1))
        seen=[]
        def run(path,iid,**kw):
            seen.append(iid)
            return {'status':'OK'}
        monkeypatch.setattr(IR,'run',run)
        def consume():
            seen.clear()
            IR.research_pass(path,recorded_at_ms=2,max_cases=1)
            return seen[:]
        return C.research_context('new'),{'priority':'HIGH'},consume
    if target==L.Target.WORLD:
        from trader.world import WorldModel,HierarchyNode,Scope,ScopeLevel
        world=WorldModel(1,(HierarchyNode(Scope(ScopeLevel.GLOBAL,'world')),),())
        context=T.Context('BTC','4h','ranging','verified-claim','LONG','family','claim-exact')
        original=world.to_json()
        def consume():
            result=world.learned_claim_confidence(j,context)
            assert world.to_json()==original
            return result['value']
        return context,{'confidence':.75},consume
    from trader.engine.orchestrator import net_score
    from trader.core.types import Side
    context=C.evidence_context('BTC','4h','ranging','signal','BUY','family','expert')
    def consume():
        vote=SimpleNamespace(agent='expert',side=Side.LONG,conviction=.8,confidence=.6,meta={})
        C.aggregate_vote(j,vote,context)
        assert vote.confidence==.6 # Raw evidence remains intact.
        return net_score([vote],[],{'expert':1},{})
    return context,{'reliability':.75},consume

@pytest.mark.parametrize('target',list(T.ADAPTIVE))
def test_actual_owner_application_consumer_restart_and_invariants(isolated,target,monkeypatch,tmp_path):
    j,cfg,ev,o,v=isolated
    context,value,consume=consumer_case(target,j,monkeypatch,tmp_path)
    before=consume()
    risk=deepcopy(cfg);spec=F.load_version(j,v['version_id'])
    receipt,p,req,rules=apply(j,cfg,ev,o.observed_ms,target,context,value)
    after=consume()
    assert before!=after
    assert T.read(j,target,context)['revision']==1
    assert A.apply(j,cfg,ev,p,req,at_ms=o.observed_ms+1,test_registry=rules)==receipt
    reopened=Journal(j.db_path);reopened.learning_test_only=True
    assert A.apply(reopened,cfg,ev,p,req,at_ms=o.observed_ms+2,test_registry=rules)==receipt
    assert T.read(reopened,target,context)==T.read(j,target,context)
    assert cfg==risk and F.load_version(j,v['version_id'])==spec
    assert not j.query('SELECT * FROM trades')
    assert p.rule not in L.REGISTERED_RULES and p.rule not in T.PRODUCTION_RULES
    assert A.apply(j,cfg,ev,p,req,at_ms=o.observed_ms,shadow=True)['result']=='UNREGISTERED_RULE'
    # Exact mismatch in ANY contextual dimension gets no learned adjustment.
    for field in context.__dataclass_fields__:
        assert T.read(j,target,replace(context,**{field:'different-exact-context'}))['value'] is None
    with pytest.raises(sqlite3.IntegrityError):
        with j._tx() as db: db.execute('DELETE FROM learning_target_revisions')
    assert receipt['resulting']['previous_hash']==receipt['previous']['state_hash']

@pytest.mark.parametrize('target',list(T.ADAPTIVE))
def test_unregistered_stale_insufficient_and_shadow(isolated,target):
    j,cfg,ev,o,v=isolated
    ctx=T.Context('A','4h','regime','signal','LONG','family','exact')
    value={'priority':'HIGH'} if target in (L.Target.ATTENTION,L.Target.RESEARCH) else (
        {'max_share':.5} if target==L.Target.ALLOCATION else {'confidence':.5} if target==L.Target.WORLD else {'reliability':.5})
    state=T.read(j,target,ctx)
    p=T.propose(ev,target,state,'unknown',T.PRODUCTION_RULES)
    req=A.request(ev,p,state,A.source_versions(cfg))
    assert A.apply(j,cfg,ev,p,req,at_ms=o.observed_ms)['result']=='UNREGISTERED_RULE'
    assert T.read(j,target,ctx)==state
    rules,rid=rule(target,value);p=T.propose(ev,target,state,rid,rules)
    req=A.request(ev,p,state,A.source_versions(cfg))
    dump='\n'.join(j._conn().iterdump())
    assert A.apply(j,cfg,ev,p,req,at_ms=o.observed_ms,shadow=True,test_registry=rules)['result']=='APPLIED'
    assert '\n'.join(j._conn().iterdump())==dump
    apply(j,cfg,ev,o.observed_ms,target,ctx,value)
    # A different evidence binding is not needed to test CAS: make another
    # explicitly versioned rule proposing against the retained old revision.
    rules2,rid2=rule(target,value);r=rules2[rid2]
    rules2={rid2:replace(r,version='new-test-version')}
    stale=T.propose(ev,target,state,rid2,rules2)
    request=A.request(ev,stale,state,A.source_versions(cfg))
    assert A.apply(j,cfg,ev,stale,request,at_ms=o.observed_ms,test_registry=rules2)['result']=='CONFLICT'
    insufficient_rule=T.Rule('test-insufficient',target,'test',lambda e,s:(L.Status.INSUFFICIENT,None,'TEST_ONLY_INSUFFICIENT'))
    rules3={'test-insufficient':insufficient_rule}
    current=T.read(j,target,ctx)
    p=T.propose(ev,target,current,'test-insufficient',rules3)
    req=A.request(ev,p,current,A.source_versions(cfg))
    assert A.apply(j,cfg,ev,p,req,at_ms=o.observed_ms,test_registry=rules3)['result']=='INSUFFICIENT_EVIDENCE'
    assert T.read(j,target,ctx)==current

def test_normal_decay_producer_automatic_checkpoint_retirement_restart(isolated):
    j,cfg,ev,o,v=isolated
    import pandas as pd
    data=json.loads(ev.historical_json)['data']
    frames={s:pd.DataFrame(rows) for s,rows in data['frames'].items()}
    for frame in frames.values():frame['ts']=pd.to_datetime(frame['ts'],utc=True)
    evidence,proposal,req=R.recent_decay(j,cfg,v['version_id'],frames,
        cutoff_ms=o.decision_ms,observed_ms=o.observed_ms)
    assert L.verified_history(evidence) and proposal.status==L.Status.APPLICABLE,proposal.reason
    risk=deepcopy(cfg);spec=F.load_version(j,v['version_id'])
    results=R.checkpoint(j,cfg,at_ms=o.observed_ms,max_work=1)
    assert len(results)==1 and results[0]['result']=='APPLIED'
    assert F.state_of(j,v['version_id'])==F.RETIRED
    assert not R.checkpoint(Journal(j.db_path),cfg,at_ms=o.observed_ms+1,max_work=1)
    assert len(F.governor_events(j,v['version_id']))==1
    assert cfg==risk and F.load_version(j,v['version_id'])==spec
    assert not j.query('SELECT * FROM trades')

def test_normal_portfolio_checkpoint_refuses_an_origin_that_is_not_a_journalled_decision(producer,monkeypatch,tmp_path):
    """Stage 7 closure: the Portfolio stage binds to the REAL decision identity.

    This fixture's candidate origin ('test-setup') is not a Kernel decision, so
    nothing is fabricated: no allocation:<digest> identity, no HOLD row, only an
    explicit capture failure. The positive path is in
    tests/test_normal_learning_loop_closure.py.
    """
    from trader.portfolio import current
    j,d,snap,cfg,receipt,p,ai,intent,v=producer
    monkeypatch.setattr(current,'freeze',lambda *a,**kw:(ai,{}))
    registrations=j.query('SELECT event_key FROM learning_registrations')
    decisions=j.query('SELECT id FROM decisions')
    result,detail=current.checkpoint(j.db_path,cfg,ledger=tmp_path/'portfolio.db',
        market_snapshots={json.loads(receipt.payload_json)['candidate_id'].removesuffix(':'+v['version_id']):snap})
    assert detail['learning_source_delivery']==[]
    assert j.query('SELECT event_key FROM learning_registrations')==registrations
    assert j.query('SELECT id FROM decisions')==decisions
    assert not getattr(snap,'learning_sources',None)
    assert not j.query('SELECT * FROM trades')

def test_legacy_no_outcome_queries_or_updates(isolated):
    j,cfg,ev,o,v=isolated
    from trader.strategy.blend import strategy_weights
    from trader.engine.orchestrator import Orchestrator
    # Changing recorded outcomes cannot trigger recalculation on these reads.
    j.kv_set('legacy_frozen_strategy_weights',json.dumps({'ranging':{'s':.8}}))
    assert strategy_weights(j,[SimpleNamespace(id='s')],'ranging')=={'s':.8}
    obj=Orchestrator.__new__(Orchestrator);obj.journal=j;obj.base_threshold=.22
    j.kv_set('legacy_frozen_accuracy_multipliers',json.dumps({'agent':.9}))
    j.kv_set('legacy_frozen_adaptive_base',json.dumps({'ranging':.25}))
    j.agent_accuracy=lambda **k:pytest.fail('legacy outcome query')
    assert obj._accuracy_multipliers()=={'agent':.9}
    assert obj._adaptive_base('ranging',2)==.25

def test_owner_refuses_raw_writes_and_test_rule_has_no_runtime_entry(isolated):
    j,cfg,ev,o,v=isolated
    A.ensure(j); T.ensure(j)
    ctx=T.Context('A','4h','ranging','signal','LONG','family','id')
    current=T.read(j,L.Target.CONFIDENCE,ctx)
    with pytest.raises(ValueError,match='application_authority_required'):
        with j._tx() as db:
            T.compare_and_apply(db,j,L.Target.CONFIDENCE,current,{'reliability':.5},'raw')
    with pytest.raises(sqlite3.IntegrityError,match='target authority required'):
        with j._tx() as db:
            db.execute('INSERT INTO learning_target_revisions VALUES(?,?,?,?,?,?)',
                (L.Target.CONFIDENCE.value,ctx.identity,1,'{}','0'*64,'raw'))
    unflagged=Journal(j.db_path)
    rules,rid=rule(L.Target.CONFIDENCE,{'reliability':.5})
    p=T.propose(ev,L.Target.CONFIDENCE,current,rid,rules)
    req=A.request(ev,p,current,A.source_versions(cfg))
    with pytest.raises(ValueError,match='isolated_test_registry_required'):
        A.apply(unflagged,cfg,ev,p,req,at_ms=o.observed_ms,test_registry=rules)
    assert not T.PRODUCTION_RULES and not T.read(j,L.Target.CONFIDENCE,ctx)['value']

def test_attention_retains_exact_priority_for_later_replay(isolated):
    j,cfg,ev,o,v=isolated
    from trader.observability.store import Store
    from trader.observability.attention import settings,evaluate_snapshot
    from tests.test_attention_telemetry import frames,event
    now=int(time.time()*1000)
    market=frames(6,now)
    for item in market.values():item['4h'].loc[29,'volume']=100000
    scan=event('before',now,market)
    store=Store(j.db_path.parent/'attention.db',settings())
    store.write(scan)
    before=json.loads(store.db.execute("SELECT payload FROM scans WHERE scan_id='before'").fetchone()[0])
    ctx=C.attention_context('S5/USDT','14400000')
    apply(j,cfg,ev,max(now,o.observed_ms),L.Target.ATTENTION,ctx,{'priority':'HIGH'})
    scan=event('after',now,market)
    store.write(scan)
    after=json.loads(store.db.execute("SELECT payload FROM scans WHERE scan_id='after'").fetchone()[0])
    assert before['rows']!=after['rows']
    assert 'governed_attention_state' in after
    # A subsequent adaptive change cannot alter this scan's replay.
    apply(j,cfg,ev,max(now,o.observed_ms),L.Target.ATTENTION,ctx,{'priority':'LOW'})
    replayed=evaluate_snapshot(dict(scan,governed_attention_state=after['governed_attention_state']))
    assert replayed['rows']==after['rows']
    assert [r['salience'] for r in before['rows']]==[r['salience'] for r in after['rows']]
    store.close()
