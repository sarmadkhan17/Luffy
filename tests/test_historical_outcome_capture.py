"""Synthetic prospective integration only; no network or production writes."""
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from trader.core.journal import Journal
from trader.core.types import Snapshot,Decision,Action
from trader.learning import capture as C, capture_runtime as R, foundation as L
from trader.cognition import opportunity_context as OC
from trader.world import WorldModel,HierarchyNode,Scope,ScopeLevel
from trader.world.replay import WorldModelRecord
from tests.test_rolling import _frame,_spec
from trader.strategy import factory_handoff as F
from trader.strategy.exit_policy import EXIT_SEMANTICS_ID
from tests.test_investigation_state_feedback import paths

CUT=1767225600000


@pytest.fixture
def prospective(tmp_path):
    j=Journal(tmp_path/'capture.db')
    world=WorldModel(CUT,[HierarchyNode(Scope(ScopeLevel.GLOBAL,'world'))])
    world_record=WorldModelRecord.from_model(world)
    context=OC.build(as_of_ms=CUT,symbol='BTCUSDT',world_model=world_record)
    frame=_frame(20)
    snap=Snapshot('BTCUSDT','2026-01-01T00:00:00+00:00',100,{'1h':frame})
    d=Decision('d','c','BTCUSDT',Action.HOLD,0,.2,.5,[],[],ts=snap.ts)
    inputs=dict(snapshot=snap,config={'risk':{'limit':.01},'strategies':{}},
        world={'record_json':world_record.to_json()},context={'context_json':context.canonical_json},
        derivative_identity={'venue':'binanceusdm','market_type':'futures','environment':'demo'},
        portfolio=__import__('tests.test_opportunity_live_integration',fromlist=['snapshot']).snapshot(None,CUT),
        control=dict(state='ACTIVE',record={'state':'ACTIVE'},version=L.digest({'state':'ACTIVE'})))
    j.log_cycle(snap,'c','TEST_ONLY')
    j.log_decision(d,capture_inputs=inputs)
    assert not j.query('SELECT * FROM learning_capture_failures')
    return j,d,inputs


def finish(j,d,*,kind=L.Kind.CASH,observation=None):
    with j._tx() as db:
        obs=observation or {'measurement':'TEST_ONLY_REGISTERED_OBSERVATION','value':1}
        out=R.freeze(db,'outcome',{'observation':obs},CUT+100,'TEST_ONLY_MEASUREMENT')
        action=C.record_action(db,'decision:'+d.id,{'action':'NO_ALLOCATION'},CUT)
        oid=C.attach(db,'decision:'+d.id,'result:'+d.id,kind.value,L.Boundary.COUNTERFACTUAL.value,
                     obs,CUT+100,[action,out])
    return oid


def chain(j,oid):
    with j._conn() as db:
        o,sources=C.learning_outcome(db,oid)
    r=L.replay(o,sources)
    e=L.evidence(o,L.attribute(o),r)
    return o,sources,r,e


def test_complete_future_decision_manifest_learning_evidence_proposal(prospective):
    j,d,inputs=prospective
    oid=finish(j,d)
    m=C.manifest(j._conn(),oid)
    assert m['status']=='REPLAY_COMPLETE'
    assert all(dep['status']=='AVAILABLE' for dep in m['dependencies'] if dep['required'])
    o,s,r,e=chain(j,oid)
    assert r.status=='COMPLETE' and e.quality=='VERIFIED_REPLAY'
    assert o.label=='SIMULATED / UNREALIZED'
    p=L.propose(e,L.Target.CONFIDENCE,.5)
    assert p.status==L.Status.UNREGISTERED and p.proposed_json is None
    assert p==L.propose(chain(j,oid)[3],L.Target.CONFIDENCE,.5)
    assert j.query('SELECT * FROM state_kv')==[]


@pytest.mark.parametrize('role',['world','context','risk_config'])
def test_missing_required_source_never_authoritative(prospective,role):
    j,d,_=prospective
    # A new prospective decision with an explicitly absent original source.
    with j._tx() as db:
        _,reg=C.registration(db,'decision:d')
        reg['lineage']['decision_id']='missing'
        deps=[]
        for dep in reg['dependencies']:
            if dep['role']==role:
                deps.append(C.unavailable(role,'NOT_PRODUCED'))
            elif dep['role']=='decision':
                raw=C.resolve(db,dep);raw['id']='missing'
                deps.append(R.freeze(db,'decision',raw,CUT,'test'))
            else: deps.append(dep)
        C.register(db,'decision:missing',L.Kind.CASH.value,reg['lineage'],CUT,deps)
        out=R.freeze(db,'outcome',{'observation':{'value':1}},CUT+100,'test')
        action=C.record_action(db,'decision:missing',{'action':'CASH'},CUT)
        oid=C.attach(db,'decision:missing','missing:'+role,L.Kind.CASH.value,L.Boundary.COUNTERFACTUAL.value,{'value':1},CUT+100,[action,out])
    o,s,r,e=chain(j,oid)
    assert r.status=='INCOMPLETE' and e.quality=='NON_AUTHORITATIVE'
    assert L.propose(e,L.Target.CONFIDENCE,.5).status==L.Status.INCOMPLETE


def test_future_measurement_cannot_rewrite_context_or_original_lineage(prospective):
    j,d,_=prospective
    before=j.query('SELECT * FROM learning_registrations')
    with j._tx() as db:
        changed=R.freeze(db,'context',prospective[2]['context'],CUT+1,'test')
        with pytest.raises(ValueError,match='cannot_rewrite'):
            C.attach(db,'decision:d','bad',L.Kind.CASH.value,L.Boundary.COUNTERFACTUAL.value,{},CUT+1,[changed])
        with pytest.raises(ValueError,match='cannot_rewrite'):
            C.attach(db,'decision:d','bad2',L.Kind.CASH.value,L.Boundary.COUNTERFACTUAL.value,{},CUT+1,post_lineage={'spec_hash':'latest'})
    finish(j,d)
    assert before==j.query('SELECT * FROM learning_registrations')


def test_latest_config_and_version_never_resolved(prospective):
    j,d,_=prospective
    oid=finish(j,d)
    old=chain(j,oid)[2]
    j.kv_set('config','LATEST_DIFFERENT_CONFIG')
    j.kv_set('strategy_version','LATEST_DIFFERENT_VERSION')
    assert chain(j,oid)[2]==old
    with j._tx() as db:
        with pytest.raises(ValueError,match='identity'):
            C.snapshot(db,'risk_config',{'risk':'latest'},source_id='risk',version='latest',available_ms=CUT,producer='test')


def test_snapshots_and_data_chunks_are_immutable_deduplicated(prospective):
    j,d,_=prospective
    oid=finish(j,d)
    before=j.query('SELECT COUNT(*) AS n FROM learning_source_blobs')[0]['n']
    with j._tx() as db:
        _,reg=C.registration(db,'decision:d')
        data=next(s for s in reg['dependencies'] if s['role']=='data')
        C.snapshot(db,'data',C.resolve(db,data),source_id=data['source_id'],version=data['version'],available_ms=data['available_ms'],producer=data['producer'])
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE learning_source_blobs SET payload='{}'")
    assert before==j.query('SELECT COUNT(*) AS n FROM learning_source_blobs')[0]['n']
    assert C.manifest(j._conn(),oid)['data_blobs']


def test_tampered_export_hash_or_chunk_refused(prospective):
    j,d,_=prospective
    oid=finish(j,d)
    o,sources,r,_=chain(j,oid)
    m=next(iter(sources.values()))
    m['sources']['risk_config']['risk']={'limit':99}
    assert L.replay(o,sources).status=='INCOMPLETE'
    fresh=chain(j,oid)[1]
    manifest=next(iter(fresh.values()))
    sha=next(iter(manifest['data_blobs']))
    manifest['data_blobs'][sha]={'tampered':True}
    assert L.replay(o,fresh).status=='INCOMPLETE'


@pytest.mark.parametrize('kind',[L.Kind.REJECTED,L.Kind.MISSED,L.Kind.RISK_BLOCKED,L.Kind.CASH])
def test_rejected_missed_skipped_counterfactual_never_realized(prospective,kind):
    j,d,_=prospective
    with j._tx() as db:
        with pytest.raises(ValueError,match='unrealized'):
            C.attach(db,'decision:d','bad',kind.value,L.Boundary.REALIZED.value,{'net_pnl':1},CUT+1)
    oid=finish(j,d,kind=kind)
    assert chain(j,oid)[0].boundary==L.Boundary.COUNTERFACTUAL
    if kind==L.Kind.RISK_BLOCKED:
        assert C.manifest(j._conn(),oid)['status']=='REPLAY_PARTIAL'


def test_restart_manifest_is_deterministic(prospective):
    j,d,_=prospective
    oid=finish(j,d)
    expected=C.manifest(j._conn(),oid)
    script='import sqlite3,json,sys; from trader.learning.capture import manifest; db=sqlite3.connect(sys.argv[1]+"?mode=ro",uri=True); print(json.dumps(manifest(db,sys.argv[2]),sort_keys=True))'
    uri=Path(j.db_path).resolve().as_uri()
    actual=json.loads(subprocess.check_output([sys.executable,'-c',script,uri,oid],text=True))
    assert actual==expected
    j2=Journal(j.db_path)
    assert C.manifest(j2._conn(),oid)==expected


def test_automatic_research_bank_chain_capture(tmp_path):
    from tests.test_strategy_decay_research_bank import _filed
    j,_=_filed(tmp_path)
    failures=j.query('SELECT * FROM learning_capture_failures')
    assert not failures,failures
    rows=j.query('SELECT outcome_id FROM learning_outcome_captures')
    assert rows
    m=C.manifest(j._conn(),rows[0]['outcome_id'])
    assert m['status']=='REPLAY_COMPLETE',m['dependencies']
    assert m['sources']['bank']['result_status']=='INCONCLUSIVE'
    assert m['sources']['question']['question_id']==m['sources']['bank']['question']['question_id']
    assert chain(j,rows[0]['outcome_id'])[3].quality=='VERIFIED_REPLAY'


def test_missing_original_registration_no_legacy_backfill(tmp_path):
    j=Journal(tmp_path/'legacy.db')
    with j._tx() as db:
        C.ensure(db)
        with pytest.raises(ValueError,match='no_backfill'):
            C.attach(db,'decision:old','old',L.Kind.REJECTED.value,L.Boundary.COUNTERFACTUAL.value,{},CUT+1)


def test_exact_old_strategy_survives_new_version_and_missing_old_refuses(prospective):
    j,d,_=prospective
    spec=_spec().to_dict()
    version=F._version_record(spec['id'],spec,F._jsha(spec),None,{'kind':'TEST_ONLY'},[])
    with j._tx() as db:
        _,reg=C.registration(db,'decision:d')
        lin=dict(reg['lineage'],strategy_id=version['strategy_id'],version_id=version['version_id'],spec_hash=version['spec_hash'])
        deps=reg['dependencies']+[R.freeze(db,'strategy',version,CUT,'TEST_ONLY_VERSION'),
            R.freeze(db,'exit_semantics',{'exit_semantics_id':EXIT_SEMANTICS_ID},CUT,'TEST_ONLY_EXIT')]
        C.register(db,'old-version',L.Kind.REJECTED.value,lin,CUT,deps)
        out=R.freeze(db,'outcome',{'observation':{'value':1}},CUT+1,'test')
        action=C.record_action(db,'old-version',{'action':'REJECTED'},CUT)
        oid=C.attach(db,'old-version','old-version-final',L.Kind.REJECTED.value,L.Boundary.COUNTERFACTUAL.value,{'value':1},CUT+1,[out,action])
    original=chain(j,oid)[2]
    assert original.status=='COMPLETE'
    new_spec=dict(spec,name='New material version')
    new=F._version_record(spec['id'],new_spec,F._jsha(new_spec),version['version_id'],{'kind':'TEST_ONLY'},[])
    with j._tx() as db:
        R.freeze(db,'strategy',new,CUT+1,'TEST_ONLY_NEW_VERSION')
    assert chain(j,oid)[2]==original
    o,s,_,_=chain(j,oid)
    next(iter(s.values()))['sources'].pop('strategy')
    assert L.replay(o,s).status=='INCOMPLETE'


def test_realized_exact_accounting_cost_funding_and_risk_path(prospective,monkeypatch):
    from tests.test_whole_trade_accounting import source,Venue,seal
    from trader.engine import trade_accounting as A
    from datetime import datetime,timezone
    j,d,_=prospective
    src=source()
    def shift_trade(t):
        if t is None:return
        t['decision_id']='d'
        t['strategy_id']='r1'
        for key in ('opened_at','closed_at'):
            if t[key]: t[key]=datetime.fromtimestamp((timestamp(t[key])+CUT)/1000,timezone.utc).isoformat()
    from trader.cognition.outcomes import timestamp
    shift_trade(src['trade'])
    for r in src['receipts']:
        r['observed_ms']+=CUT;shift_trade(r['before']);shift_trade(r['after']);seal(r)
    src['observed_ms']+=CUT;seal(src)
    venue=Venue()
    for rows,key in ((venue.fills,'time'),(venue.funding,'time'),(venue.events,'fundingTime')):
        for row in rows:row[key]+=CUT
    monkeypatch.setattr(A.time,'time',lambda:(CUT+5000)/1000)
    whole=A.capture(src,venue)
    receipt=A.verified_outcome(whole,CUT+6000)
    spec=_spec().to_dict()
    version=F._version_record(spec['id'],spec,F._jsha(spec),None,{'kind':'TEST_ONLY'},[])
    with j._tx() as db:
        _,reg=C.registration(db,'decision:d')
        # A separate original event with an exact strategy and venue identity.
        # Its data/world/context are the fixture's original frozen inputs.
        reg['lineage'].update(strategy_id=version['strategy_id'],version_id=version['version_id'],spec_hash=version['spec_hash'])
        deps=reg['dependencies']+[R.freeze(db,'strategy',version,CUT,'TEST_ONLY_VERSION'),
            R.freeze(db,'exit_semantics',{'exit_semantics_id':EXIT_SEMANTICS_ID},CUT,'TEST_ONLY_EXIT')]
        # Test a fresh prospective event instead of rewriting d's registration.
    new=Journal(Path(j.db_path).parent/'executed.db')
    with new._tx() as db:
        C.ensure(db)
        for row in j.query('SELECT * FROM learning_source_blobs'):
            C.insert(db,'learning_source_blobs','sha256',row['sha256'],(row['sha256'],row['payload']))
        C.register(db,'decision:d',L.Kind.REJECTED.value,reg['lineage'],CUT,deps)
        C.record_action(db,'decision:d',{'action':'EXECUTED'},CUT+1000)
        C.record_action(db,'decision:d',{'ok':True,'config_risk_sha256':L.digest({'limit':.01})},CUT+10,risk=True)
        oid=R.verified_trade(db,receipt)
        assert R.verified_trade(db,receipt)==oid
        assert R.verified_trade(db,A.verified_outcome(whole,CUT+7000))==oid
    o,s,r,e=chain(new,oid)
    assert r.status=='COMPLETE',r
    assert o.boundary==L.Boundary.REALIZED and json.loads(o.observation_json)['net_pnl']==25
    assert e.quality=='VERIFIED_REPLAY'
    m=C.manifest(new._conn(),oid)
    assert m['sources']['funding']==whole['assessment']['accounting']
    assert new.query('SELECT * FROM state_kv')==[]


def test_original_runtime_inputs_detach_mutable_snapshot(prospective):
    j,_,inputs=prospective
    snap=inputs['snapshot']
    original=R.runtime_inputs(j,snap,inputs['config'],cut_ms=CUT)
    snap.dfs['1h'].loc[0,'close']=9999
    inputs['config']['risk']['limit']=99
    assert original['snapshot'].dfs['1h'].iloc[0]['close']!=9999
    assert original['config']['risk']['limit']==.01


def test_observed_missing_snapshot_stays_unassessable(prospective):
    j,_,_=prospective
    with j._tx() as db:
        oid=R.missed_snapshot(db,'scan','BTCUSDT',CUT)
        assert R.missed_snapshot(db,'scan','BTCUSDT',CUT)==oid
    m=C.manifest(j._conn(),oid)
    assert m['status']=='UNASSESSABLE'
    data=next(d for d in m['dependencies'] if d['role']=='data')
    assert data['status']=='UNAVAILABLE' and 'RETURNED_NONE' in data['reason']
    assert chain(j,oid)[3].quality=='NON_AUTHORITATIVE'


def test_automatic_forward_resolver_keeps_closed_postdecision_bars(prospective,monkeypatch):
    from datetime import datetime,timezone
    import pandas as pd
    from trader.engine.outcomes import resolve_pending
    import trader.core.journal as JM
    j,d,_=prospective
    monkeypatch.setattr(JM,'now_utc',lambda:datetime.fromtimestamp(CUT/1000,timezone.utc))
    j.schedule_outcome(d.id,d.cycle_id,d.symbol,d.ts,'BUY',100)
    class Feed:
        def fetch_ohlcv(self,*args,**kwargs):
            return pd.DataFrame({'ts':pd.to_datetime([CUT+3600000,CUT+14400000],unit='ms',utc=True),
                'close':[110,120]})
    assert resolve_pending(j,Feed(),now_ms=CUT+14400000+300000)==1
    assert not j.query('SELECT * FROM learning_capture_failures')
    oid=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'forward:%'")[0]['outcome_id']
    o,s,r,e=chain(j,oid)
    assert o.boundary==L.Boundary.COUNTERFACTUAL and r.status=='COMPLETE',r
    assert e.quality=='VERIFIED_REPLAY'
    assert json.loads(o.observation_json)['returns']['fwd_ret_4h']==.2
    assert L.propose(e,L.Target.CONFIDENCE,.5).status==L.Status.UNREGISTERED
    before=j.query('SELECT * FROM learning_outcome_captures')
    with j._tx() as db:
        m=C.manifest(db,oid);raw=m['sources']['outcome']
        R.forward(db,raw['declaration'],raw['observation']['returns'],raw['target_bars'],CUT+14400000+600000)
    assert before==j.query('SELECT * FROM learning_outcome_captures')


def test_public_funding_reference_is_exact_and_missing_not_zero(prospective):
    import hashlib
    from trader.observability import funding_events as F
    j,_,_=prospective
    raw='[]'
    receipt=dict(schema=F.SCHEMA,environment='production',symbol='BTCUSDT',open_ms=CUT,close_ms=CUT+1,
        pages=[dict(url=F.url('BTCUSDT',CUT,CUT+1),request_start_ms=CUT+2,received_ms=CUT+3,
            http_status=200,response_body=raw,response_sha256=hashlib.sha256(raw.encode()).hexdigest())])
    with j._tx() as db:
        dep=R.freeze(db,'funding',receipt,CUT+3,'TEST_ONLY_PUBLIC_RECEIPT')
        C.semantic('funding',C.resolve(db,dep),{})
        receipt['pages'][0]['response_body']='[{}]'
        with pytest.raises(ValueError,match='hash'):
            C.semantic('funding',receipt,{})
    absent=C.unavailable('funding','EVENT_POSITION_NOTIONAL_BASIS_UNAVAILABLE')
    assert absent['status']=='UNAVAILABLE' and absent['sha256'] is None


def test_unreadable_research_bank_exact_chain_captured(tmp_path):
    from tests.test_strategy_health_unreadable_bank import _filed
    j,_=_filed(tmp_path)
    assert not j.query('SELECT * FROM learning_capture_failures')
    rows=j.query('SELECT outcome_id FROM learning_outcome_captures')
    assert rows
    for row in rows:
        m=C.manifest(j._conn(),row['outcome_id'])
        assert m['status']=='REPLAY_COMPLETE',m['dependencies']
        assert m['sources']['bank']['bank_kind']=='strategy_health_unreadable'
        assert chain(j,row['outcome_id'])[3].quality=='VERIFIED_REPLAY'


def test_investigation_registered_research_chain_captured(paths):
    from tests.test_investigation_research_family import measured
    from trader.observability import investigation_research as I
    _,dest,_=paths
    iid,end=measured(paths,'same_direction')
    assert I.run(dest,iid,recorded_at_ms=end+2)['status']=='OK'
    db=sqlite3.connect(dest)
    assert db.execute('SELECT * FROM learning_capture_failures').fetchall()==[]
    oid=db.execute('SELECT outcome_id FROM learning_outcome_captures').fetchone()[0]
    assert C.manifest(db,oid)['status']=='REPLAY_COMPLETE'
    o,s=C.learning_outcome(db,oid)
    assert L.replay(o,s).status=='COMPLETE'
    assert I.run(dest,iid,recorded_at_ms=end+3)['status']=='OK'
    assert db.execute('SELECT COUNT(*) FROM learning_outcome_captures').fetchone()[0]==1
    db.close()


def test_inventory_reads_only_and_keeps_pending_separate(prospective):
    from scripts.learning_capture_inventory import run
    j,d,_=prospective
    finish(j,d)
    with j._conn() as db:
        before=list(db.iterdump())
    report=run(j.db_path,CUT+100,10)
    assert report['counts']['complete']==1
    assert report['counts']['incomplete']>=1
    assert report['applicable_updates']==report['production_mutations']==0
    with j._conn() as db:
        assert before==list(db.iterdump())


def test_capture_failure_does_not_change_original_decision(prospective):
    j,d,_=prospective
    before=j.query('SELECT * FROM decisions')
    with j._tx() as db:
        def fail(db):
            R.freeze(db,'data',{'rolled_back':True},CUT,'test')
            raise ValueError('TEST_ONLY_CAPTURE_FAILURE')
        assert C.safely(db,'test',fail) is None
    assert before==j.query('SELECT * FROM decisions')
    assert j.query('SELECT reason FROM learning_capture_failures')==[{'reason':'ValueError:TEST_ONLY_CAPTURE_FAILURE'}]
    assert not j.query("SELECT * FROM learning_source_blobs WHERE payload LIKE '%rolled_back%'")


def test_source_frame_preserves_full_float_and_nanosecond_precision():
    import pandas as pd
    frame=pd.DataFrame({'ts':[pd.Timestamp('2026-01-01T00:00:00.123456789Z')],
        'close':[1.2345678901234567],'volume':[float('nan')]})
    frozen=json.loads(L.canonical(R.exact_frame(frame)))
    assert frozen['data'][0][0]['timestamp']=='2026-01-01T00:00:00.123456789+00:00'
    assert frozen['data'][0][1].hex()==frame['close'].iloc[0].item().hex()
    assert frozen['data'][0][2]=={'float_hex':'nan'}
    pd.testing.assert_frame_equal(R.restore_frame(frozen),frame,check_exact=True)


def test_actual_risk_block_receipt_can_complete_without_confidence_change(prospective):
    j,d,_=prospective
    observation={'measurement':'TEST_ONLY_COUNTERFACTUAL_RETURN','return':-.1}
    with j._tx() as db:
        risk=C.record_action(db,'decision:d',{'ok':False,'reason':'TEST_ONLY_RISK_DENIAL',
            'config_risk_sha256':L.digest({'limit':.01})},CUT+1,risk=True)
        action=C.record_action(db,'decision:d',{'action':'RISK_BLOCKED'},CUT+1)
        outcome=R.freeze(db,'outcome',{'observation':observation},CUT+100,'TEST_ONLY_FUTURE_MEASUREMENT')
        oid=C.attach(db,'decision:d','blocked',L.Kind.RISK_BLOCKED.value,L.Boundary.COUNTERFACTUAL.value,
            observation,CUT+100,[risk,action,outcome])
    o,s,r,e=chain(j,oid)
    assert r.status=='COMPLETE' and o.lineage.risk_decision_id==risk['source_id']
    assert o.boundary==L.Boundary.COUNTERFACTUAL
    assert L.propose(e,L.Target.CONFIDENCE,.5).status==L.Status.UNREGISTERED


def test_capture_does_not_mutate_strategy_risk_trading_or_control(prospective):
    import ast
    j,d,_=prospective
    tables=('trades','strategies','strategy_specs','control_events','state_kv')
    existing=[t for t in tables if j.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,))]
    before={t:j.query('SELECT * FROM '+t) for t in existing}
    finish(j,d)
    assert before=={t:j.query('SELECT * FROM '+t) for t in existing}
    banned={'create_order','set_control','upsert_spec','apply','check_entry','refit','activate'}
    for path in ('trader/learning/capture.py','trader/learning/capture_runtime.py'):
        tree=ast.parse(Path(path).read_text())
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in banned
                       for n in ast.walk(tree)),path


def test_valid_context_for_wrong_instrument_cannot_certify_replay(prospective):
    j,d,inputs=prospective
    world=WorldModelRecord.from_json(inputs['world']['record_json'])
    other=OC.build(as_of_ms=CUT,symbol='ETHUSDT',world_model=world)
    with j._tx() as db:
        _,reg=C.registration(db,'decision:d')
        deps=[dep for dep in reg['dependencies'] if dep['role']!='context']
        deps.append(R.freeze(db,'context',{'context_json':other.canonical_json},CUT,'TEST_ONLY_WRONG_INSTRUMENT'))
        reg['lineage']['context_id']=other.context_id
        C.register(db,'wrong-instrument',L.Kind.CASH.value,reg['lineage'],CUT,deps)
        obs={'value':1}
        out=R.freeze(db,'outcome',{'observation':obs},CUT+1,'test')
        action=C.record_action(db,'wrong-instrument',{'action':'CASH'},CUT)
        oid=C.attach(db,'wrong-instrument','wrong-final',L.Kind.CASH.value,L.Boundary.COUNTERFACTUAL.value,obs,CUT+1,[out,action])
    assert C.manifest(j._conn(),oid)['status']=='REPLAY_PARTIAL'
    assert chain(j,oid)[3].quality=='NON_AUTHORITATIVE'
