"""Paid source ideas are reusable; financial evidence and authority are not."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tests.test_compose_proposer import LLM, Q, proposal
from tests.test_brain_research_intake import setup  # noqa: F401
from tests.test_closure_question_consumer import _bank, VOLUME
from trader.research.compose import proposer as P, proposal_cache as PC
from trader.research.closure import calibration as C, study as S, digest, ClosureRefused
from trader.research.closure.store import Store


def test_same_question_in_different_studies_calls_provider_once_and_rebinds_identity(tmp_path):
    cache = Store(tmp_path/'cache.db')
    stores = [Store(tmp_path/f'study-{i}.db') for i in range(2)]
    client = LLM([{'proposal': proposal()}])
    try:
        results = [P.GraphProposer(client, s, tf='1h', study_id=str(i), cache_store=cache)(Q,'BRAIN',Q)
                   for i,s in enumerate(stores)]
        assert all(r[0] == 'COMPILED' for r in results) and len(client.calls) == 1
        # Economic identity stays equal; per-study provenance is rebuilt.
        assert results[0][2]['candidate_id'] == results[1][2]['candidate_id']
        assert results[0][2]['provenance']['study_id'] == '0'
        assert results[1][2]['provenance']['study_id'] == '1'
        assert all(not s.all_once('runs') for s in [cache,*stores])
        packs = [PC.batch(s,str(i),tf='1h',provider_id='configured') for i,s in enumerate(stores)]
        assert packs[0] == packs[1]
        assert len(cache.all_once('proposal_cache')) == 1
    finally:
        for s in [cache,*stores]:s.close()


@pytest.mark.parametrize('change', ['source','learning','provider','horizon','compiler','catalog'])
def test_semantic_changes_cannot_reuse_a_paid_reply(tmp_path,monkeypatch,change):
    cache=Store(tmp_path/'cache.db'); first=Store(tmp_path/'first.db'); second=Store(tmp_path/'second.db')
    raw=proposal(); other=deepcopy(raw)
    if change=='horizon':other['tf']='4h'
    client=LLM([{'proposal':raw},{'proposal':other}])
    try:
        a=P.GraphProposer(client,first,tf='1h',study_id='first',cache_store=cache,provider_id='provider-a')(Q,'BRAIN',Q)
        q=deepcopy(Q);options={}
        if change=='source':q['passage']['text']='different mechanism'
        if change=='learning':options['learning_tasks']=[{'lesson':'verified new observation'}]
        if change=='catalog':monkeypatch.setattr(P.catalog,'catalog_id',lambda:'new-catalog')
        if change=='compiler':monkeypatch.setattr(P,'PROMPT_VERSION','changed-compiler-contract')
        b=P.GraphProposer(client,second,tf='4h' if change=='horizon' else '1h',study_id='second',
            cache_store=cache,provider_id='provider-b' if change=='provider' else 'provider-a',**options)(q,'BRAIN',q)
        assert a[0]==b[0]=='COMPILED' and len(client.calls)==2
        assert a[3]['proposal_semantic_id']!=b[3]['proposal_semantic_id']
    finally:
        for s in (cache,first,second):s.close()


def test_frozen_cache_miss_never_calls_provider(tmp_path):
    cache=Store(tmp_path/'cache.db');s=Store(tmp_path/'s.db');client=LLM([])
    try:
        result=P.GraphProposer(client,s,tf='1h',study_id='replicate',cache_store=cache,
            allow_provider_calls=False)(Q,'BRAIN',Q)
        assert result[0]=='WAITING_DATA' and result[2] is None
        assert not client.calls and not cache.all_once('proposal_cache_reservations')
        assert not s.all_once('proposals')
    finally:cache.close();s.close()


def test_lost_provider_reply_is_not_a_free_new_attempt_under_another_study(tmp_path):
    cache=Store(tmp_path/'cache.db');a=Store(tmp_path/'a.db');b=Store(tmp_path/'b.db');client=LLM([None])
    try:
        for name,s in [('a',a),('b',b)]:
            result=P.GraphProposer(client,s,tf='1h',study_id=name,cache_store=cache)(Q,'BRAIN',Q)
            assert result[0]=='WAITING_DATA'
        assert len(client.calls)==1
        assert len(cache.all_once('proposal_cache_reservations'))==1
        assert len(cache.all_once('proposal_cache'))==1
    finally:
        for s in (cache,a,b):s.close()


def test_prepare_before_generation_then_replay_two_real_intakes_without_provider(setup,tmp_path,monkeypatch):  # noqa: F811
    bank=_bank(setup,[('one',VOLUME)])
    work=tmp_path/'calibration';work.mkdir()
    p=dict(C.PROTOCOL,**C.SMOKE,tf='1h',intake_bank_path=str(bank),
           intake_mode='FROZEN_PROPOSAL_BATCH',unit_timeout_s=33)
    client=LLM([{'proposal':proposal()}])
    monkeypatch.setattr('trader.brain.llm.BrainLLM',lambda cfg:client)
    def bounded(fn,payload,timeout,child,limits):
        assert child is True and timeout==33 and limits['memory_mb']>0
        assert not list(work.glob('*/snapshot.db'))  # no final market has been generated
        return SimpleNamespace(ok=True,value=fn(payload),terminated=True)
    monkeypatch.setattr(S,'_call',bounded)
    C._freeze_intake(p,work);C._freeze_proposals(p,work)
    assert len(client.calls)==1 and p['proposal_batch_id']
    before=(work/'proposal-batch.json').read_bytes()
    C._freeze_proposals(p,work)
    assert (work/'proposal-batch.json').read_bytes()==before and len(client.calls)==1
    with pytest.raises(ValueError,match='calibration_proposal_batch_identity_changed'):
        C._freeze_proposals(dict(p,proposal_batch_id='wrong-declared-batch'),work)
    from trader.core.config import load_config
    for i in range(2):
        result=S.intake_job(dict(store_path=str(tmp_path/f'replicate-{i}.db'),bank_path=p['intake_bank_path'],
            now_ms=2,tf='1h',study_id=f'replicate-{i}',proposal_mode='compositional',cfg=load_config(),
            proposal_cache_path=p['proposal_cache_path'],proposal_batch_id=p['proposal_batch_id']))
        assert result['compiled']==1 and result['proposal_batch']['batch_id']==p['proposal_batch_id']
    assert len(client.calls)==1
    changed=dict(p,proposal_batch_id='another-batch')
    with pytest.raises(ClosureRefused,match='frozen_proposal_batch_differs'):
        S.intake_job(dict(store_path=str(tmp_path/'wrong.db'),bank_path=p['intake_bank_path'],now_ms=2,tf='1h',
            study_id='wrong',proposal_mode='compositional',cfg=load_config(),
            proposal_cache_path=p['proposal_cache_path'],proposal_batch_id=changed['proposal_batch_id']))


def test_approval_is_conditional_on_the_matching_batch_not_only_search_limits():
    # A verifier fixture, not measured calibration or financial qualification.
    env=dict(search_contract_id='search',numeric_id='numbers',cost_id='cost',evaluator_id='code')
    report=dict(schema='closure-calibration-report.v3',search_scope='COMPOSITIONAL_SEARCH',**env,
        calibration_status='CALIBRATED',kind='FINAL',numerics='EXACT_CONFIGURED',authority_granted=False,
        intake_mode='FROZEN_PROPOSAL_BATCH',proposal_batch_id='batch-a')
    report['report_id']=digest(report);approval=dict(report_id=report['report_id'])
    assert C.report_authorizes(report,approval,env,proposal_batch_id='batch-a')
    assert not C.report_authorizes(report,approval,env,proposal_batch_id='batch-b')
    assert not C.report_authorizes(report,approval,env)


def test_batch_receipt_cannot_be_edited_or_empty():
    body=dict(schema=PC.SCHEMA,tf='1h',provider_id='provider',member_ids=['member'])
    batch=dict(body,batch_id=digest(body))
    assert PC.verify(batch)==batch
    for changed in (dict(batch,member_ids=[]),dict(batch,tf='4h')):
        with pytest.raises(ClosureRefused,match='proposal_batch_identity_invalid'):PC.verify(changed)


def test_unreaped_preparation_worker_prevents_another_producer(tmp_path,monkeypatch):
    calls=[]
    def incomplete(*args):
        calls.append(1)
        return SimpleNamespace(ok=False,terminated=False,pid=999999,start_ticks=1,artifact_dir=None)
    monkeypatch.setattr(S,'_call',incomplete)
    p=dict(intake_bank_path='detached-bank',intake_corpus_id='verified-fixture-corpus',
           intake_mode='FROZEN_PROPOSAL_BATCH',tf='1h',unit_timeout_s=7)
    with pytest.raises(ClosureRefused,match='calibration_batch_child_ownership_unknown'):
        C._freeze_proposals(p,tmp_path)
    assert (tmp_path/'proposal-batch.db.child-unreaped').exists()
    monkeypatch.setattr(S,'reconcile_child',lambda owner:False)
    with pytest.raises(ClosureRefused,match='calibration_batch_child_ownership_unknown'):
        C._freeze_proposals(p,tmp_path)
    assert len(calls)==1 and not (tmp_path/'proposal-batch.json').exists()


def test_operational_store_reuses_definition_but_scopes_intake_and_revision_to_each_study(setup,tmp_path,monkeypatch):  # noqa: F811
    from trader.research.closure import consumer
    from trader.core.config import load_config
    bank=_bank(setup,[('one',VOLUME)]);client=LLM([{'proposal':proposal()}])
    monkeypatch.setattr('trader.brain.llm.BrainLLM',lambda cfg:client)
    path=tmp_path/'operational.db'
    for sid in ('study-a','study-b'):
        result=S.intake_job(dict(store_path=str(path),bank_path=str(bank),now_ms=2,tf='1h',
            study_id=sid,proposal_mode='compositional',cfg=load_config()))
        assert result['compiled']==1
    store=Store(path)
    try:
        assert len(client.calls)==1 and len(store.all_once('candidates'))==1
        roots=[]
        for sid in ('study-a','study-b'):
            items=store.owned_items(sid)
            assert len(items)==1
            assert json.loads(items[0]['payload'])['provenance']['study_id']==sid
            roots.append(items[0]['candidate_id'])
        for sid,root in zip(('study-a','study-b'),roots):
            result=consumer.revise(store,root,dict(stage='DEVELOPMENT',candidate_id=root,result_id=sid,
                outcome='NEGATIVE_ECONOMICS',metrics={'total_pct':-1},baselines={}),
                now_ms=3,study_id=sid,max_revisions=1)
            assert result['status']=='COMPILED'
        assert len(store.all_once('candidates'))==2
    finally:store.close()


def test_shared_envelope_cannot_select_another_studys_proposal_batch(tmp_path):
    store=Store(tmp_path/'s.db')
    try:
        for sid,member in [('one','first'),('two','second')]:
            with store._tx() as db:
                db.execute('INSERT INTO studies(study_id,payload,status,last_progress_ms) VALUES(?,?,?,?)',
                    (sid,json.dumps(dict(envelope_id='shared',bank_path='bank')),'CREATED',1))
            S._ensure_unit(store,sid,'intake','intake')
            body=dict(schema=PC.SCHEMA,tf='1h',provider_id='provider',member_ids=[member])
            S._set_unit(store,sid,'intake','DONE',2,result=dict(proposal_batch=dict(body,batch_id=digest(body))))
        with pytest.raises(ClosureRefused,match='proposal_batch_ambiguous_for_envelope'):
            PC.for_envelope(store,dict(envelope_id='shared'))
    finally:store.close()


def test_cached_proposal_has_identical_actual_numerical_results_on_matched_input(tmp_path):
    from tests.closure_fixtures import build
    from trader.research.closure import development as D
    bundle=build(tmp_path,name='cached-proposal-numerical-parity',n_bars=1500)
    cache=Store(tmp_path/'cache.db');second=Store(tmp_path/'second.db')
    raw=proposal();raw['tf']='4h';client=LLM([{'proposal':raw}])
    try:
        a=P.GraphProposer(client,bundle.store,tf='4h',study_id='first',cache_store=cache)(Q,'BRAIN',Q)
        b=P.GraphProposer(client,second,tf='4h',study_id='second',cache_store=cache)(Q,'BRAIN',Q)
        # Actual evaluator/controls, three draws only: parity machinery, not
        # statistical calibration or a profitable-strategy claim.
        first=D.develop(a[2],bundle.snap,bundle.env,draws=3)
        reused=D.develop(b[2],bundle.snap,bundle.env,draws=3)
        assert first['outcome']==reused['outcome']
        assert first.get('metrics')==reused.get('metrics')
        assert first.get('baselines')==reused.get('baselines')
        assert first['result_id']==reused['result_id']
        assert first['outcome'] not in ('CODE_FAILURE','BLOCKED_DATA')
        assert len(client.calls)==1 and not second.all_once('runs')
    finally:
        bundle.store.close();cache.close();second.close()
