import json
import sqlite3
from dataclasses import replace
from copy import deepcopy

import pytest
from trader.learning import application as A, foundation as L
from trader.strategy import factory_handoff as F
from tests.test_learning_foundation import chain, get_evidence, MemoryJournal


@pytest.fixture
def prepared(chain):
    o, retained, current, version = chain
    j = MemoryJournal()
    F.ensure(j)
    with j._tx() as c:
        F._insert_version(c, version, o.decision_ms-1)
        for state in (F.VALIDATED, F.SHADOW, F.APPROVAL_REQUIRED, F.APPROVED_FIRST_LIVE):
            F._transition(c, version['version_id'], state, 'TEST_ONLY', None, 'factory', o.decision_ms)
    ev = get_evidence(o, retained)
    cfg = json.loads(ev.historical_json)['risk_config']
    p = L.propose(ev, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    target = F.lifecycle_target(j, version['version_id'])
    req = A.request(ev, p, target, A.source_versions(cfg))
    return j, cfg, ev, p, req, o.observed_ms


def execute(prepared, **kwargs):
    j,cfg,e,p,r,ms = prepared
    return A.apply(j,cfg,e,p,r,at_ms=ms,**kwargs)


def test_closed_loop_receipt_future_state_retry_and_immutability(prepared):
    j,cfg,e,p,r,ms = prepared
    before = F.load_version(j, json.loads(p.current_json)['version_id'])
    config_before = deepcopy(cfg)
    receipt = execute(prepared)
    assert receipt['result'] == 'APPLIED'
    vid = receipt['target_id']
    assert F.state_of(j,vid) == F.RETIRED
    assert receipt['schema'] == A.SCHEMA
    assert receipt['governor_transition_receipt']['event']['actor'] == 'strategy_governor'
    assert receipt['governor_transition_receipt']['event']['reason_code'] == r.application_id
    assert len(F.governor_events(j,vid)) == 1
    assert A.apply(j,cfg,e,p,r,at_ms=ms+100) == receipt
    assert len(F.governor_events(j,vid)) == 1
    assert F.load_version(j,vid) == before and cfg == config_before
    assert len(j.query(f'SELECT * FROM {A.TABLE}')) == 1
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with j._tx() as c:
            c.execute(f'DELETE FROM {A.TABLE}')
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with j._tx() as c:
            c.execute(f"UPDATE {A.TABLE} SET result='NO_CHANGE'")


def test_incomplete_replay_and_forged_evidence_cannot_apply(prepared):
    j,cfg,e,p,r,ms = prepared
    e = replace(e, historical_json='{}')
    r = A.request(e,p,json.loads(r.target_json),A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result'] == 'INCOMPLETE_REPLAY'
    assert F.state_of(j,json.loads(p.current_json)['version_id']) != F.RETIRED


@pytest.mark.parametrize('target', list(L.Target))
def test_unregistered_targets_and_rules_no_mutation(prepared,target):
    j,cfg,e,p,r,ms = prepared
    p = replace(p,target=target,rule='unknown.v1')
    r = A.request(e,p,json.loads(r.target_json),A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result'] == 'UNREGISTERED_RULE'
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])
    if target != L.Target.LIFECYCLE:
        assert not A.TARGET_REGISTRY[target]


def test_stale_target_no_overwrite(prepared):
    j,cfg,e,p,r,ms = prepared
    vid = json.loads(p.current_json)['version_id']
    F.govern_version(j,cfg,vid,'PAUSED',actor='strategy_governor',reason_code='newer',at_ms=ms)
    receipt=execute(prepared)
    assert receipt['result']=='STALE' and F.state_of(j,vid)=='PAUSED'
    assert len(F.governor_events(j,vid))==1


def test_history_token_detects_same_state_changed_history(prepared):
    j,cfg,e,p,r,ms=prepared
    vid=json.loads(p.current_json)['version_id']
    F.govern_version(j,cfg,vid,F.DEGRADED,actor='strategy_governor',reason_code='degraded',at_ms=ms)
    target=F.lifecycle_target(j,vid)
    current={k:v for k,v in target.items() if k!='history_sha256'}
    p=L.propose(e,L.Target.LIFECYCLE,current,rule=L.DECAY_RULE)
    r=A.request(e,p,target,A.source_versions(cfg))
    for state in ('PAUSED',F.DEGRADED):
        F.govern_version(j,cfg,vid,state,actor='strategy_governor',reason_code='newer',at_ms=ms)
    assert F.state_of(j,vid)==target['state']
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result']=='STALE'


@pytest.mark.parametrize('field', ['rule_version','proposed_json','evidence_id'])
def test_exact_proposal_binding(prepared,field):
    j,cfg,e,p,r,ms = prepared
    value = 'wrong' if field != 'proposed_json' else L.canonical(dict(json.loads(p.current_json),state='ACTIVE'))
    p = replace(p,**{field:value})
    req = A.request(e,p,json.loads(r.target_json),A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,req,at_ms=ms)['result'] in ('STALE','CONFLICT','UNREGISTERED_RULE')
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])


def test_changed_source_policy_and_risk_expansion_refused(prepared):
    j,cfg,e,p,r,ms=prepared
    changed=deepcopy(cfg)
    changed['risk']['owner_risk_limit_TEST_ONLY']=999
    req=A.request(e,p,json.loads(r.target_json),A.source_versions(changed))
    assert A.apply(j,changed,e,p,req,at_ms=ms)['result']=='STALE'
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])


@pytest.mark.parametrize('forbidden', ['RISK_LIMITS','CATASTROPHE_BOUNDARY','LEVERAGE_POLICY','CONTROL_STATE','CAPACITY','STRATEGY_SPEC'])
def test_forbidden_targets_cannot_apply(prepared,forbidden):
    j,cfg,e,p,r,ms=prepared
    p=replace(p,target=forbidden)
    req=A.request(e,p,json.loads(r.target_json),A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,req,at_ms=ms)['result']=='UNREGISTERED_RULE'
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])


def test_governor_owns_mutation_and_atomic_rollback(prepared,monkeypatch):
    j,cfg,e,p,r,ms=prepared
    real=F.govern_version
    def fail(*args,**kwargs):
        real(*args,**kwargs)
        raise RuntimeError('after Governor insertion')
    monkeypatch.setattr(F,'govern_version',fail)
    with pytest.raises(RuntimeError): execute(prepared)
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])
    assert not j.query(f'SELECT * FROM {A.TABLE}')
    monkeypatch.setattr(F,'govern_version',real)
    assert execute(prepared)['result']=='APPLIED'


def test_shadow_deterministic_zero_mutations(prepared,monkeypatch):
    j,cfg,e,p,r,ms=prepared
    before='\n'.join(j.conn.iterdump())
    monkeypatch.setattr(F,'govern_version',lambda *a,**k:pytest.fail('shadow mutation'))
    monkeypatch.setattr(A,'ensure',lambda *a:pytest.fail('shadow migration'))
    one=execute(prepared,shadow=True)
    assert one['result']=='APPLIED'
    assert one==execute(prepared,shadow=True)
    assert before=='\n'.join(j.conn.iterdump())


def test_duplicate_evidence_not_pooled_and_prior_application_not_reversed(prepared):
    j,cfg,e,p,r,ms=prepared
    execute(prepared)
    # Rebinding the same proposal to another source snapshot cannot create evidence.
    req=replace(r,source_versions_json=L.canonical(dict(A.source_versions(cfg),nonce='new')))
    assert A.apply(j,cfg,e,p,req,at_ms=ms)['result']=='STALE'
    assert len(F.governor_events(j,json.loads(p.current_json)['version_id']))==1
    req=replace(r,target_json=L.canonical(dict(json.loads(r.target_json),history_sha256='changed')))
    assert A.apply(j,cfg,e,p,req,at_ms=ms)['result']=='NO_CHANGE'
    assert len(F.governor_events(j,json.loads(p.current_json)['version_id']))==1


def test_stage6_shared_lineage_grouping_preserves_unknown_independence():
    from trader.portfolio.common_factor import evidence_groups
    from types import SimpleNamespace
    candidates=[SimpleNamespace(identity=('a',),version_id='v1',strategy_id='s',spec_hash='h1'),SimpleNamespace(identity=('b',),version_id='v2',strategy_id='s',spec_hash='h2')]
    groups=evidence_groups(candidates,dict(v1=dict(root_version_id='root',strategy_id='s',spec_hash='h1'),v2=dict(root_version_id='root',strategy_id='s',spec_hash='h2')))
    assert groups['evidence_group_count']==1 and groups['shared_lineage_count']==1
    assert groups['independent_evidence_count'] is None
    assert groups['confidence_aggregation']=='NONE'


def test_durable_concurrent_retry_one_governor_event(prepared,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    j,cfg,e,p,r,ms=prepared
    path=tmp_path/'authority.db'
    class FileJournal(MemoryJournal):
        def __init__(self):
            self.conn=sqlite3.connect(path,timeout=10)
            self.conn.row_factory=sqlite3.Row
    db=sqlite3.connect(path)
    j.conn.backup(db)
    db.close()
    persistent=FileJournal()
    A.ensure(persistent)
    persistent.conn.close()
    barrier=Barrier(2)
    def worker():
        local=FileJournal()
        barrier.wait(timeout=10)
        try:
            return A.apply(local,cfg,e,p,r,at_ms=ms)
        finally:
            local.conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker) for _ in range(2)]
        receipts=[f.result(timeout=30) for f in futures]
    assert receipts[0]==receipts[1]
    reopened=FileJournal()
    assert A.apply(reopened,cfg,e,p,r,at_ms=ms+1000)==receipts[0]
    assert len(F.governor_events(reopened,receipts[0]['target_id']))==1
    assert len(reopened.query(f'SELECT * FROM {A.TABLE}'))==1
    reopened.conn.close()


from tests.test_historical_outcome_capture import prospective, CUT


def test_captured_execution_quality_cannot_change_predictive_confidence(prospective):
    from trader.engine.booking import assess
    from trader.engine.accounting import digest
    from trader.learning import producers as P
    j,d,inputs=prospective
    trade=dict(id='t',decision_id=d.id,status='closed')
    receipt=dict(schema_version='trade-booking.v1',trade_id='t',kind='close:test',observed_ms=CUT+100,
        before=None,after=trade,evidence=dict(basis='venue_order_fills',order_id='123',quantity=1,slippage=5,
            symbol='BTCUSDT',side='sell',since_ms=CUT,observed_ms=CUT+100,
            fills=[dict(id='fill',order='123',symbol='BTCUSDT',side='sell',timestamp=CUT+100,
                amount=1,price=101,commission=.1,commission_asset='USDT',realized_pnl=-1)]))
    receipt['assessment']=assess(receipt['evidence']);receipt['sha256']=digest(receipt)
    with j._tx() as db:
        oid=P.execution_quality(db,trade,receipt)
    e,p,r=A.prepare_captured(j,inputs['config'],oid)
    assert e.quality=='VERIFIED_REPLAY' and not e.eligible_targets
    before=j.query('SELECT * FROM state_kv')
    assert A.apply(j,inputs['config'],e,p,r,at_ms=CUT+100)['result']=='INSUFFICIENT_EVIDENCE'
    p=L.propose(e,L.Target.CONFIDENCE,.5)
    r=A.request(e,p,{},A.source_versions(inputs['config']))
    assert A.apply(j,inputs['config'],e,p,r,at_ms=CUT+100)['result']=='UNREGISTERED_RULE'
    assert j.query('SELECT * FROM state_kv')==before


def test_application_shadow_reads_captured_evidence_zero_writes(prospective):
    from scripts.learning_application_shadow import run
    from tests.test_historical_outcome_capture import finish
    j,d,inputs=prospective
    finish(j,d)
    before=list(j._conn().iterdump())
    report=run(j.db_path,inputs['config'],as_of_ms=CUT+100)
    assert report['counts']['proposals']==len(j.query('SELECT * FROM learning_outcome_captures'))
    assert report['counts']['replay_complete']==1
    assert report['counts']['would_apply']==report['production_mutations']==0
    assert report['counts']['insufficient']==1
    assert list(j._conn().iterdump())==before
    assert run(j.db_path,inputs['config'],as_of_ms=CUT+100)==report


def test_failed_research_recall_cannot_suppress_application(prepared):
    j,cfg,e,p,r,ms=prepared
    proposal=L.propose(e,L.Target.RESEARCH,{'priority':1},rule='failed-research-suppression.v1')
    req=A.request(e,proposal,{},A.source_versions(cfg))
    assert A.apply(j,cfg,e,proposal,req,at_ms=ms)['result']=='UNREGISTERED_RULE'
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])


def test_registered_sample_minimum_is_required_even_for_replayed_loss(prepared):
    from dataclasses import asdict
    from trader.learning import decision_sources as D
    j,cfg,e,p,r,ms=prepared
    history=json.loads(e.historical_json)
    # A fresh test registration with its own policy; no production threshold changes.
    history['risk_config']['strategies']['decay_min_trades']=history['outcome']['evaluation']['trades']+1
    # Match the actual registered evaluator's underpowered branch receipt.
    from trader.strategy import rolling
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import StrategySpec
    import pandas as pd
    compiled=compile_spec(StrategySpec.from_dict(history['strategy']['spec']))
    frames={s:pd.DataFrame(rows) for s,rows in history['data']['frames'].items()}
    for frame in frames.values(): frame['ts']=pd.to_datetime(frame['ts'],utc=True)
    policy=history['risk_config']['strategies']
    _,evaluation=rolling.has_decayed(compiled,frames,history['risk_config']['risk'],compiled.spec.timeframe,recent_days=policy['decay_recent_days'],min_trades=policy['decay_min_trades'],floor_pf=policy['decay_floor_pf'])
    history['outcome']['evaluation']=evaluation
    observed=json.loads(e.observed_json)
    observed['evaluation']=evaluation
    history['outcome']['observation']=observed
    wrapper=history['decision_manifest']
    wrapper['observation_json']=L.canonical(observed)
    reg=wrapper['registration']
    for dep in reg['dependencies']:
        dep['sha256']=L.digest(history[dep['role']])
    wrapper['manifest']=D.make(reg)
    sources=tuple(replace(L.Source(**raw),sha256=L.digest(history[raw['role']])) for raw in wrapper['outcome_sources'])
    wrapper['outcome_sources']=[asdict(s) for s in sources]
    sources+=(L.Source('decision_manifest',wrapper['manifest']['manifest_id'],D.SCHEMA,L.digest(wrapper),reg['decision_ms']),)
    o=L.Outcome(L.Kind(wrapper['kind']),L.Boundary(wrapper['boundary']),L.Lineage(**dict(reg['lineage'],execution_ids=tuple(reg['lineage']['execution_ids']),trade_ids=tuple(reg['lineage']['trade_ids']))),reg['decision_ms'],wrapper['observed_ms'],sources,wrapper['observation_json'],wrapper['label'])
    retained={(s.source_id,s.version):history[s.role] for s in sources}
    e=get_evidence(o,retained)
    assert e.quality=='VERIFIED_REPLAY'
    cfg=history['risk_config']
    p=L.propose(e,L.Target.LIFECYCLE,json.loads(p.current_json),rule=L.DECAY_RULE)
    assert p.status==L.Status.INSUFFICIENT
    req=A.request(e,p,json.loads(r.target_json),A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,req,at_ms=ms)['result']=='INSUFFICIENT_EVIDENCE'
    assert not F.governor_events(j,json.loads(p.current_json)['version_id'])


def test_application_never_writes_lifecycle_spec_risk_or_order_state_directly():
    import ast
    from pathlib import Path
    tree=ast.parse(Path(A.__file__).read_text())
    banned={'_transition','retire_version','upsert_spec','set_control','create_order','execute_order','activate'}
    calls=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
    assert not banned.intersection(calls)
    assert 'govern_version' in calls
    writes=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='execute':
            sql=ast.unparse(node.args[0])
            if 'INSERT' in sql or 'UPDATE' in sql or 'DELETE' in sql:
                writes.append(sql)
    assert len(writes)==1 and 'INSERT INTO {TABLE}' in writes[0]
