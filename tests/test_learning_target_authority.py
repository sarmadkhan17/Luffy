"""Synthetic policies/rules/owner stores exist ONLY in this test module."""
import json
import sqlite3
from dataclasses import asdict, replace, FrozenInstanceError
from types import MappingProxyType

import pytest
from trader.learning import application as A, authority as T, dispatch as D, foundation as L
from tests.test_learning_application import prepared
from tests.test_learning_foundation import chain

CATEGORIES = tuple(c for c in L.Target if c != L.Target.LIFECYCLE)
COORDINATE = L.canonical(dict(subject='TEST_ONLY', dimension='test_dimension', context='TEST_ONLY-context'))


class TestOwner(T.AuthorityAdapter):
    __test__ = False

    def __init__(self, category):
        self.category = category

    def read(self, journal, cfg, target_id):
        row = journal.query('SELECT * FROM test_owner WHERE category=? AND target_id=?',
                            (self.category.value, target_id))[0]
        policy = T.Boundary('TEST_ONLY-policy', '1', 'test_dimension',
                           T.MutationShape.REPLACE_DIMENSION, '["before","after"]')
        state = dict(test_dimension=row['value'], unrelated_dimension=row['unrelated'])
        return T.LearningTargetRef(self.category, target_id, T.OWNERS[self.category],
            L.canonical(state), str(row['revision']), L.digest(state), policy,
            L.canonical(dict(source='TEST_ONLY-owner', context='TEST_ONLY-context')))

    def _write(self, journal, cfg, target, mutation, *, at_ms, application_id, connection):
        cur = connection.execute('UPDATE test_owner SET value=?,revision=revision+1 WHERE category=? AND target_id=? AND revision=?',
            (json.loads(mutation.value_json), self.category.value, target.target_id, int(target.state_version)))
        assert cur.rowcount == 1
        return dict(authority=T.OWNERS[self.category], application_id=application_id)


def install_test_rule(monkeypatch, category, *, eligible=True):
    rule = D.RegisteredUpdate('TEST_ONLY-replace', 'TEST_ONLY-v1', category,
        lambda e, t: eligible and e.quality == 'VERIFIED_REPLAY',
        lambda e, t: T.Mutation(T.MutationShape.REPLACE_DIMENSION, 'test_dimension', '"after"'))
    monkeypatch.setattr(D, 'EXTENSIONS', MappingProxyType({rule.key: rule}))
    return rule


def setup_owner(prepared, monkeypatch, category):
    j, cfg, e, _, _, ms = prepared
    with j._tx() as c:
        c.execute('CREATE TABLE test_owner(category TEXT,target_id TEXT,value TEXT,unrelated TEXT,revision INTEGER)')
        c.execute('INSERT INTO test_owner VALUES(?,?,?,?,?)', (category.value, COORDINATE, 'before', 'untouched', 1))
    owner = TestOwner(category)
    monkeypatch.setattr(T, 'AUTHORITIES', MappingProxyType(dict(T.AUTHORITIES, **{category: owner})))
    rule = install_test_rule(monkeypatch, category)
    target = T.read_target(j, cfg, category, COORDINATE)
    p = L.propose(e, category, target, rule=rule.rule_id, rule_version=rule.rule_version)
    r = A.request(e, p, target, A.source_versions(cfg))
    return j, cfg, e, p, r, ms, owner, target


@pytest.mark.parametrize('category', CATEGORIES)
def test_each_production_authority_refuses_policy_and_unregistered_rule(prepared, category):
    j, cfg, e, _, _, ms = prepared
    before = list(j.conn.iterdump())
    target = T.read_target(j, cfg, category, COORDINATE)
    assert target.owner_authority == T.OWNERS[category]
    assert target.allowed_mutation is None
    with pytest.raises(T.Refused) as refusal:
        T.AUTHORITIES[category].validate_request(target,
            T.Mutation(T.MutationShape.REPLACE_DIMENSION, 'test_dimension', '"after"'))
    assert refusal.value.result == ('TARGET_NOT_SUPPORTED' if category == L.Target.WORLD else 'POLICY_UNAVAILABLE')
    p = L.propose(e, category, target, rule='TEST_ONLY-replace', rule_version='TEST_ONLY-v1')
    r = A.request(e, p, target, A.source_versions(cfg))
    assert p.status == L.Status.UNREGISTERED
    assert A.apply(j, cfg, e, p, r, at_ms=ms, shadow=True)['result'] == 'UNREGISTERED_RULE'
    assert before == list(j.conn.iterdump())


@pytest.mark.parametrize('category', CATEGORIES)
def test_each_registered_test_rule_authority_apply_read_retry_immutable_receipt(prepared, monkeypatch, category):
    j, cfg, e, p, r, ms, owner, target = setup_owner(prepared, monkeypatch, category)
    before = list(j.conn.iterdump())
    assert A.apply(j, cfg, e, p, r, at_ms=ms, shadow=True)['result'] == 'APPLIED'
    assert before == list(j.conn.iterdump())
    receipt = A.apply(j, cfg, e, p, r, at_ms=ms)
    assert receipt['result'] == 'APPLIED'
    future = owner.read(j, cfg, COORDINATE)
    assert json.loads(future.current_json) == dict(test_dimension='after', unrelated_dimension='untouched')
    assert future.state_version == '2'
    assert A.apply(j, cfg, e, p, r, at_ms=ms+1) == receipt
    assert owner.read(j, cfg, COORDINATE) == future
    common = A.AuthorityReceipt(**receipt['authority_receipt'])
    assert common.target_category == category
    assert common.owner_authority == T.OWNERS[category]
    assert common.prior_version == '1' and common.resulting_version == '2'
    assert common.evidence_id == e.evidence_id and common.timestamp == ms
    with pytest.raises(FrozenInstanceError):
        common.timestamp = ms+1
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with j._tx() as c:
            c.execute(f'DELETE FROM {A.TABLE}')


@pytest.mark.parametrize('category', CATEGORIES)
def test_registered_rule_does_not_grant_missing_production_policy(prepared, monkeypatch, category):
    j, cfg, e, _, _, ms = prepared
    rule = install_test_rule(monkeypatch, category)
    target = T.read_target(j, cfg, category, COORDINATE)
    p = L.propose(e, category, target, rule=rule.rule_id, rule_version=rule.rule_version)
    r = A.request(e, p, target, A.source_versions(cfg))
    before = list(j.conn.iterdump())
    expected = 'TARGET_NOT_SUPPORTED' if category == L.Target.WORLD else 'POLICY_UNAVAILABLE'
    assert A.apply(j, cfg, e, p, r, at_ms=ms, shadow=True)['result'] == expected
    assert before == list(j.conn.iterdump())


@pytest.mark.parametrize('field,value', [('rule','not-registered'), ('rule_version','wrong'), ('target',L.Target.RESEARCH)])
def test_exact_dispatch_no_rule_id_version_or_category_alias(prepared, monkeypatch, field, value):
    j, cfg, e, p, r, ms, owner, target = setup_owner(prepared, monkeypatch, L.Target.CONFIDENCE)
    p = replace(p, **{field:value})
    r = A.request(e, p, target, A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result'] == 'UNREGISTERED_RULE'
    assert owner.read(j,cfg,COORDINATE).state_version == '1'


@pytest.mark.parametrize('change', ['value','ABA','policy','owner','dimension','context','replay','eligibility','source'])
def test_stale_forged_or_ineligible_targets_never_mutate(prepared, monkeypatch, change):
    j,cfg,e,p,r,ms,owner,target = setup_owner(prepared, monkeypatch, L.Target.CONFIDENCE)
    if change in ('value','ABA'):
        with j._tx() as c:
            c.execute('UPDATE test_owner SET revision=revision+1,value=?', ('after' if change=='value' else 'before',))
    elif change == 'policy':
        target=replace(target,allowed_mutation=replace(target.allowed_mutation,policy_version='wrong'))
        p=replace(p,current_json=L.canonical(target.to_dict()))
    elif change == 'owner':
        raw=target.to_dict();raw['owner_authority']='Risk'
        r=replace(r,target_json=L.canonical(raw))
    elif change == 'dimension':
        mutation=T.Mutation(T.MutationShape.REPLACE_DIMENSION,'unrelated_dimension','"after"')
        p=replace(p,proposed_json=L.canonical(asdict(mutation)))
    elif change == 'context':
        target=replace(target,target_id=L.canonical(dict(subject='other',dimension='test_dimension',context='other-context')))
        # Keep the original proposal binding: no cross-context transplant.
    elif change == 'replay':
        e=replace(e,historical_json='{}');p=replace(p,evidence_id=e.evidence_id)
    elif change == 'eligibility':
        install_test_rule(monkeypatch,L.Target.CONFIDENCE,eligible=False)
    elif change == 'source':
        cfg=dict(cfg, unrelated_new_policy=True)
    if change != 'owner': r=A.request(e,p,target,A.source_versions(cfg))
    before=owner.read(j,cfg,COORDINATE)
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result'] in ('STALE','CONFLICT','INCOMPLETE_REPLAY','INSUFFICIENT_EVIDENCE')
    assert owner.read(j,cfg,COORDINATE)==before


def test_authority_direct_call_cannot_bypass_registration_or_transaction(prepared,monkeypatch):
    j,cfg,e,p,r,ms,owner,target=setup_owner(prepared,monkeypatch,L.Target.CONFIDENCE)
    mutation=T.Mutation.from_dict(json.loads(p.proposed_json))
    from trader.learning.application import _BoundJournal
    with j._tx() as c:
        bound=_BoundJournal(c)
        with pytest.raises(T.Refused,match='not registered'):
            owner.compare_and_apply(bound,cfg,target,mutation,evidence=e,proposal=replace(p,rule='unknown'),at_ms=ms,application_id=r.application_id,connection=c)
        with pytest.raises(T.Refused,match='transaction required'):
            owner.compare_and_apply(j,cfg,target,mutation,evidence=e,proposal=p,at_ms=ms,application_id=r.application_id,connection=c)
    assert owner.read(j,cfg,COORDINATE)==target


def test_atomic_owner_and_receipt_rollback(prepared,monkeypatch):
    j,cfg,e,p,r,ms,owner,target=setup_owner(prepared,monkeypatch,L.Target.CONFIDENCE)
    A.ensure(j)
    with j._tx() as c:
        c.execute(f"CREATE TRIGGER test_fail BEFORE INSERT ON {A.TABLE} BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='test failure'):
        A.apply(j,cfg,e,p,r,at_ms=ms)
    assert owner.read(j,cfg,COORDINATE)==target
    assert not j.query(f'SELECT * FROM {A.TABLE}')


def test_common_typed_lifecycle_path_and_receipt(prepared):
    j,cfg,e,_,r,ms=prepared
    vid=json.loads(r.target_json)['version_id']
    target=T.read_target(j,cfg,L.Target.LIFECYCLE,vid)
    p=L.propose(e,L.Target.LIFECYCLE,target,rule=L.DECAY_RULE,rule_version=L.digest(L.code_manifest()))
    r=A.request(e,p,target,A.source_versions(cfg))
    receipt=A.apply(j,cfg,e,p,r,at_ms=ms)
    assert receipt['result']=='APPLIED'
    assert receipt['authority_receipt']['owner_authority']=='StrategyGovernor'
    assert receipt['authority_receipt']['resulting_version'] != target.state_version
    assert json.loads(T.read_target(j,cfg,L.Target.LIFECYCLE,vid).current_json)['state']=='RETIRED'


def test_no_test_registration_or_mutable_policy_in_fresh_runtime():
    import subprocess,sys
    code="from trader.learning import dispatch as D,authority as T,foundation as L; assert not D.EXTENSIONS; assert set(T.OWNERS)==set(L.Target); assert len(T.AUTHORITIES)==7; print('EMPTY_RUNTIME_EXTENSIONS')"
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='EMPTY_RUNTIME_EXTENSIONS'
    for forbidden in ('RISK_LIMITS','STRATEGY_SPEC','CAPACITY','ORDER','ACTIVATE'):
        with pytest.raises(T.Refused): T.read_target(None,{},forbidden,'x')


def test_confidence_boundary_cannot_modify_other_dimensions():
    owner=TestOwner(L.Target.CONFIDENCE)
    state=dict(test_dimension='before',unrelated_dimension='untouched')
    ref=T.LearningTargetRef(L.Target.CONFIDENCE,COORDINATE,T.OWNERS[L.Target.CONFIDENCE],L.canonical(state),'1',L.digest(state),T.Boundary('TEST_ONLY','1','test_dimension',T.MutationShape.REPLACE_DIMENSION,'["before","after"]'),'{}')
    for dimension,value in [('unrelated_dimension','"after"'),('test_dimension','999')]:
        with pytest.raises(T.Refused): owner.validate_request(ref,T.Mutation(T.MutationShape.REPLACE_DIMENSION,dimension,value))


def test_generic_concurrent_retry_and_fresh_process_receipt_replay(prepared,monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import subprocess,sys
    from tests.test_learning_foundation import MemoryJournal
    j,cfg,e,p,r,ms,owner,target=setup_owner(prepared,monkeypatch,L.Target.CONFIDENCE)
    path=tmp_path/'owner.db'
    db=sqlite3.connect(path);j.conn.backup(db);db.close()
    class FileJournal(MemoryJournal):
        def __init__(self):
            self.conn=sqlite3.connect(path,timeout=10)
            self.conn.row_factory=sqlite3.Row
    local=FileJournal();A.ensure(local);local.conn.close()
    barrier=Barrier(2)
    def worker():
        local=FileJournal();barrier.wait(timeout=10)
        try: return A.apply(local,cfg,e,p,r,at_ms=ms)
        finally: local.conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts=list(pool.map(lambda _:worker(),range(2)))
    assert receipts[0]==receipts[1] and receipts[0]['result']=='APPLIED'
    local=FileJournal()
    assert owner.read(local,cfg,COORDINATE).state_version=='2'
    assert len(local.query(f'SELECT * FROM {A.TABLE}'))==1
    local.conn.close()
    payload=tmp_path/'replay.json'
    payload.write_text(L.canonical(dict(db=str(path),cfg=cfg,e=asdict(e),p=asdict(p),r=asdict(r),ms=ms)))
    code="""
import json,sqlite3,sys
from trader.learning import application as A,foundation as L,dispatch as D
from tests.test_learning_foundation import MemoryJournal
raw=json.load(open(sys.argv[1]))
j=MemoryJournal();j.conn.close();j.conn=sqlite3.connect(raw['db']);j.conn.row_factory=sqlite3.Row
assert not D.EXTENSIONS
# Existing immutable result is replayed; no test rule exists in this process.
e=L.LearningEvidence(**raw['e']);p=L.LearningUpdateProposal(**raw['p']);r=A.Request(**raw['r'])
print(L.canonical(A.apply(j,raw['cfg'],e,p,r,at_ms=raw['ms']+100)))
assert j.query('SELECT revision FROM test_owner')[0]['revision']==2
"""
    result=subprocess.run([sys.executable,'-c',code,str(payload)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==receipts[0]


def test_valid_adapter_call_requires_application_receipt_gate(prepared,monkeypatch):
    j,cfg,e,p,r,ms,owner,target=setup_owner(prepared,monkeypatch,L.Target.CONFIDENCE)
    with j._tx() as c:
        from trader.learning.application import _BoundJournal
        from trader.strategy.factory_handoff import _begin
        _begin(c)
        with pytest.raises(T.Refused,match='receipt transaction required'):
            owner.compare_and_apply(_BoundJournal(c),cfg,target,T.Mutation.from_dict(json.loads(p.proposed_json)),evidence=e,proposal=p,at_ms=ms,application_id=r.application_id,connection=c)
    assert owner.read(j,cfg,COORDINATE)==target


def test_owner_cannot_change_unrelated_dimension_even_with_registered_rule(prepared,monkeypatch):
    j,cfg,e,p,r,ms,owner,target=setup_owner(prepared,monkeypatch,L.Target.CONFIDENCE)
    original=owner._write
    def escaped(journal,cfg,target,mutation,**kwargs):
        result=original(journal,cfg,target,mutation,**kwargs)
        kwargs['connection'].execute("UPDATE test_owner SET unrelated='escaped'")
        return result
    monkeypatch.setattr(owner,'_write',escaped)
    with pytest.raises(T.Refused,match='escaped approved dimension'):
        A.apply(j,cfg,e,p,r,at_ms=ms)
    assert owner.read(j,cfg,COORDINATE)==target
    assert not j.query(f'SELECT * FROM {A.TABLE}')


def test_production_authorities_have_no_risk_spec_capacity_activation_or_order_writer():
    import ast
    from pathlib import Path
    for module in (A,T,D):
        tree=ast.parse(Path(module.__file__).read_text())
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        assert not calls.intersection({'_transition','retire_version','_save','refit','upsert_spec','set_control','create_order','execute_order','activate','allocate','write_text','write_bytes'})
    assert not D.EXTENSIONS
    assert list(L.REGISTERED_RULES)==[L.DECAY_RULE]


def test_free_form_mutation_fields_are_refused(prepared,monkeypatch):
    j,cfg,e,p,r,ms,owner,target=setup_owner(prepared,monkeypatch,L.Target.CONFIDENCE)
    raw=json.loads(p.proposed_json)
    raw['risk_limits']={'leverage':999}
    p=replace(p,proposed_json=L.canonical(raw))
    r=A.request(e,p,target,A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result']=='CONFLICT'
    assert owner.read(j,cfg,COORDINATE)==target


def test_unregistered_lifecycle_rule_with_untyped_mutation_still_refuses(prepared):
    j,cfg,e,p,r,ms=prepared
    p=replace(p,rule='not-registered',proposed_json='123')
    r=A.request(e,p,json.loads(r.target_json),A.source_versions(cfg))
    assert A.apply(j,cfg,e,p,r,at_ms=ms)['result']=='UNREGISTERED_RULE'
