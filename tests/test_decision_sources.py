"""Prospective source binding through actual producers; no orders/network."""
from dataclasses import replace, asdict
from datetime import datetime, timezone
import json
import sqlite3
import subprocess
import sys
import pytest

from trader.learning import capture as C, capture_runtime as R, decision_sources as D, foundation as F
from trader.portfolio import opportunity_live as L, candidate_bridge as B, allocator as A
from trader.core.types import Snapshot, Decision, Action
from tests.test_candidate_bridge import exact  # noqa: F401
from tests.test_opportunity_live_integration import snapshot, world, SYMBOL
from tests.test_strategy_factory_handoff import INPUTS
from scripts.opportunity_context_shadow import DIMENSIONS


@pytest.fixture
def producer(exact):
    j,cfg,v,args=exact
    cut=args['as_of_ms']; book=snapshot(None,cut)
    from tests.test_attention_telemetry import frames
    from trader.observability import attention, world_producer
    from trader.world.replay import WorldModelRecord
    market={SYMBOL:frames(1,cut)['S0/USDT']}
    event=attention.capture(market,[SYMBOL],'prospective-market-cut',attention.settings({'world_model':True}),cut)
    model,world_receipt=world_producer.produce(event)
    assert model is not None and world_receipt['status']=='ok'
    args=dict(args,sources=(*args['sources'],L.source('world_model',world_receipt['record_json'],cut,cut+1000),
                           L.source('portfolio',book,cut,cut+1000)))
    receipt=L.produce(**args)
    c,er,sources=B.build(j,receipt,cfg,available_inputs=INPUTS,dimensions=DIMENSIONS)
    known=A.Evidence(A.Status.ESTABLISHED,(sources[0].source_id,))
    portfolio=A.Portfolio(book['snapshot_id'],cut,cut+1000,known,(),(sources[0].source_id,))
    ai=A.Inputs(cut,(c,),portfolio,known,known,'ACTIVE',sources)
    proposal=A.allocate(ai)
    ts=datetime.fromtimestamp(cut/1000,timezone.utc).isoformat()
    snap=Snapshot(SYMBOL,ts,100,market[SYMBOL])
    d=Decision('prospective','test-cycle',SYMBOL,Action.HOLD,0,.2,.5,[],[],ts=ts)
    original_state=j.query('SELECT * FROM state_kv')
    intent=R.journalize_allocation(j,snap,d,cfg,receipt,proposal,ai)
    assert j.query('SELECT * FROM state_kv')==original_state
    assert not j.query('SELECT * FROM learning_capture_failures')
    return j,d,snap,cfg,receipt,proposal,ai,intent,v


def resolve(producer,kind=F.Kind.CASH):
    j,d,_,_,receipt,_,ai,_,_=producer
    cut=ai.as_of_ms
    with j._tx() as db:
        obs={'measurement':'TEST_ONLY_FROZEN_FUTURE_RETURN','value':.1}
        out=R.freeze(db,'outcome',{'observation':obs},cut+100,'TEST_ONLY_TERMINAL_OBSERVATION')
        action=C.record_action(db,'decision:'+d.id,{'action':'NO_ALLOCATION'},cut)
        deps=[action,out]
        if kind==F.Kind.RISK_BLOCKED:
            risk=R.freeze(db,'risk_decision',{'ok':False,'config_risk_sha256':F.digest(producer[3]['risk'])},cut+1,'TEST_ONLY_EXISTING_RISK_RESULT')
            deps.append(risk)
        oid=C.attach(db,'decision:'+d.id,'later:'+d.id,kind.value,F.Boundary.COUNTERFACTUAL.value,obs,cut+100,deps)
    return oid


def chain(j,oid):
    o,s=C.learning_outcome(j._conn(),oid)
    r=F.replay(o,s)
    return o,s,r,F.evidence(o,F.attribute(o),r)


def test_direct_prospective_producers_to_manifest_outcome_learning(producer):
    j,d,snap,cfg,receipt,p,ai,intent,v=producer
    assert json.loads(p.result_json)['decision']=='NO_ALLOCATION'
    oid=resolve(producer)
    m=C.manifest(j._conn(),oid)
    assert m['status']=='REPLAY_COMPLETE',m.get('decision_source_faults')
    reg=m['capture']['registration']; dm=m['capture']['decision_source_manifest']
    assert dm['schema']==D.SCHEMA and dm['manifest_id']==reg['decision_source_manifest_id']
    sources=m['sources']; pbody=json.loads(receipt.payload_json)
    original={s['source_id']:json.loads(s['payload_json'])['data'] for s in pbody['sources']}
    assert sources['world']['record_json']==original['world_model']
    assert sources['context']['context_json']==receipt.context.canonical_json
    assert sources['portfolio']==original['portfolio']
    assert sources['risk_config']['risk']==cfg['risk']
    assert sources['risk_config']['risk_version']==F.digest(cfg['risk'])
    assert sources['risk_config']['config_version']==F.digest(sources['risk_config']['config'])
    assert sources['strategy']==v
    assert sources['strategy']['spec_hash']==ai.candidates[0].spec_hash
    assert sources['proposal']==p.payload()
    assert sources['intent']==dict(intent_id=intent.intent_id,payload_json=intent.payload_json)
    assert sources['economics']==json.loads(ai.candidates[0].economics.receipt_json)
    assert sources['control']['state']==ai.control_state
    o,s,r,e=chain(j,oid)
    assert r.status=='COMPLETE' and e.quality=='VERIFIED_REPLAY'
    assert F.verified_history(e)
    assert F.propose(e,F.Target.ALLOCATION,{},rule='profit').status==F.Status.UNREGISTERED
    assert j.query('SELECT * FROM trades')==[]


@pytest.mark.parametrize('kind',[F.Kind.CASH,F.Kind.REJECTED,F.Kind.RISK_BLOCKED,F.Kind.MISSED])
def test_nontrade_replay_needs_no_executed_trade(producer,kind):
    j=producer[0];oid=resolve(producer,kind)
    o,s,r,e=chain(j,oid)
    if kind==F.Kind.MISSED:
        assert r.status=='INCOMPLETE' and 'missing:opportunity' in r.faults
        assert e.quality=='NON_AUTHORITATIVE'
    else:
        assert r.status=='COMPLETE',r.faults
        assert e.quality=='VERIFIED_REPLAY'
    assert o.boundary==F.Boundary.COUNTERFACTUAL
    assert j.query('SELECT * FROM trades')==[]


@pytest.mark.parametrize('role',['world','context','portfolio','risk_config','control','strategy','exit_semantics','economics','proposal','intent'])
def test_missing_exact_source_cannot_be_replaced_with_latest(producer,role):
    j=producer[0];oid=resolve(producer)
    o,s,r,e=chain(j,oid)
    m=next(iter(s.values()))
    original=m['sources'].pop(role)
    m['sources']['latest_'+role]=original
    # Even a new outer digest cannot waive the mandatory embedded dependency.
    m['manifest_id']=F.digest({k:v for k,v in m.items() if k!='manifest_id'})
    ref=replace(o.sources[0],source_id=m['manifest_id'],sha256=F.digest(m))
    o=replace(o,sources=(ref,));s={(ref.source_id,ref.version):m}
    replay=F.replay(o,s)
    assert replay.status=='INCOMPLETE'
    assert F.evidence(o,F.attribute(o),replay).quality=='NON_AUTHORITATIVE'
    forged=replace(e,historical_json=replay.reconstructed_json,quality='VERIFIED_REPLAY')
    assert F.propose(forged,F.Target.LIFECYCLE,{},rule=F.DECAY_RULE).status==F.Status.INCOMPLETE


@pytest.mark.parametrize('role',['world','context','portfolio','risk_config','strategy','economics','proposal','intent','control'])
def test_tampered_source_or_manifest_refused(producer,role):
    j=producer[0];oid=resolve(producer)
    o,s,_,_=chain(j,oid)
    m=next(iter(s.values()));m['sources'][role]={'tampered':True}
    assert F.replay(o,s).status=='INCOMPLETE'
    with j._tx() as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute(f"UPDATE {D.TABLE} SET payload='{{}}'")


def test_current_state_does_not_change_history_and_restart_is_deterministic(producer):
    j=producer[0];oid=resolve(producer)
    before=C.manifest(j._conn(),oid)
    j.kv_set('world_model','LATEST');j.kv_set('control_state','HALTED');j.kv_set('venue_position_snapshot','LATEST')
    assert C.manifest(j._conn(),oid)==before
    code='import sqlite3,json,sys;from trader.learning.capture import manifest;db=sqlite3.connect(sys.argv[1]+"?mode=ro",uri=True);print(json.dumps(manifest(db,sys.argv[2]),sort_keys=True))'
    from pathlib import Path
    result=subprocess.check_output([sys.executable,'-c',code,Path(j.db_path).resolve().as_uri(),oid],text=True)
    assert json.loads(result)==before


def test_journal_portfolio_never_satisfies_venue_truth(producer):
    j,d,snap,cfg,_,_,ai,_,_=producer
    second=replace(d,id='journal-book')
    inputs=R.runtime_inputs(j,snap,cfg,cut_ms=ai.as_of_ms,control_state='ACTIVE')
    inputs['portfolio']={'positions':[],'basis':'journal','observed_ms':ai.as_of_ms}
    j.log_decision(second,capture_inputs=inputs)
    _,reg=C.registration(j._conn(),'decision:journal-book')
    dm=D.load(j._conn(),reg['decision_source_manifest_id'])
    sources={dep['role']:C.resolve(j._conn(),dep) for dep in reg['dependencies'] if dep['status']=='AVAILABLE'}
    faults=D.verify(dm,reg,sources)
    assert 'missing:portfolio' in faults
    dep=next(dep for dep in dm['dependencies'] if dep['role']=='portfolio')
    assert dep['status']=='UNAVAILABLE' and 'JOURNAL_NOT_TRUTH' in dep['reason']
    # A scan never consulted the later world/opportunity stages. Supplying an
    # invalid portfolio cannot waive that genuinely supplied dependency.
    assert reg['stage_contract']['stage']=='SCAN'
    assert 'missing:world' not in faults and 'missing:context' not in faults


def test_legacy_registration_is_not_repaired(producer):
    j=producer[0];oid=resolve(producer)
    o,s,_,_=chain(j,oid)
    m=next(iter(s.values()))
    del m['capture']['decision_source_manifest']
    assert F.replay(o,s).status=='INCOMPLETE'
    from scripts.decision_source_shadow import run
    before=list(j._conn().iterdump())
    report=run(j.db_path)
    assert report['counts']['complete']==1 and report['counts']['manifests']==1
    assert list(j._conn().iterdump())==before
    assert report['production_mutations']==0


@pytest.mark.parametrize('action',[Action.HOLD,Action.BUY])
def test_direct_existing_forward_outcome_resolver(producer,monkeypatch,action):
    """Final acceptance path: no manual source-link insertion at any stage."""
    import pandas as pd
    from trader.engine.outcomes import resolve_pending
    import trader.core.journal as JM
    j,d,snap,cfg,receipt,proposal,ai,_,_=producer
    if action==Action.BUY:
        d=replace(d,id='rejected-prospective',action=action,skip_reason='allocation refused')
        R.journalize_allocation(j,snap,d,cfg,receipt,proposal,ai)
    cut=ai.as_of_ms
    monkeypatch.setattr(JM,'now_utc',lambda:datetime.fromtimestamp(cut/1000,timezone.utc))
    j.schedule_outcome(d.id,d.cycle_id,d.symbol,d.ts,'BUY',100)
    class Feed:
        def fetch_ohlcv(self,*args,**kwargs):
            return pd.DataFrame({'ts':pd.to_datetime([cut+3600000,cut+14400000],unit='ms',utc=True),'close':[110,120]})
    assert resolve_pending(j,Feed(),now_ms=cut+14400000+300000)==1
    oid=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'forward:%'")[0]['outcome_id']
    o,s,r,e=chain(j,oid)
    assert r.status=='COMPLETE',r.faults
    assert e.quality=='VERIFIED_REPLAY' and F.verified_history(e)
    assert C.manifest(j._conn(),oid)['capture']['decision_source_manifest']['schema']==D.SCHEMA
    assert not j.query('SELECT * FROM learning_capture_failures')


def test_registered_rule_and_all_targets_refuse_forged_quality(producer,monkeypatch):
    j=producer[0];oid=resolve(producer)
    o,s,r,e=chain(j,oid)
    h=json.loads(e.historical_json)
    del h['capture_manifest']['capture']['decision_source_manifest']
    forged=replace(e,quality='VERIFIED_REPLAY',historical_json=F.canonical(h),eligible_targets=(F.Target.LIFECYCLE,))
    monkeypatch.setattr(F,'evaluate_decay',lambda *a:pytest.fail('manifest gate bypassed'))
    for target in F.Target:
        assert F.propose(forged,target,{},rule=F.DECAY_RULE).status==F.Status.INCOMPLETE


def test_old_live_decisions_are_only_observed_never_backfilled(tmp_path):
    from trader.core.journal import Journal
    from scripts.decision_source_shadow import run
    j=Journal(tmp_path/'legacy.db')
    with j._tx() as db:
        db.execute("INSERT INTO cycles(id,ts,symbol) VALUES('old-cycle','2026-01-01T00:00:00+00:00','BTCUSDT')")
        db.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,executed) VALUES('old','old-cycle','2026-01-01T00:00:00+00:00','BTCUSDT','HOLD',0,0,0,0)")
    before=list(j._conn().iterdump())
    result=run(j.db_path)
    assert result['counts']==dict(decisions=1,manifests=0,complete=0,incomplete=1)
    assert 'original_registration_missing_no_backfill' in result['missing_dependencies']
    assert before==list(j._conn().iterdump())
