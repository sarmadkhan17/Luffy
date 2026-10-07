"""Offline governed bridge proof, using synthetic data and protocol fixtures."""
from copy import deepcopy
from dataclasses import asdict
import json
import shutil
import sqlite3

import pytest

from trader.cognition import predictive as P
from trader.research import predictive_bridge as B, predictive_experiment as E
from tests.test_predictive_research_bridge import population, NumericalFixture, CFG
from tests.test_investigation_state_feedback import paths


def copy_db(source, dest):
    with sqlite3.connect(source) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)


@pytest.fixture(scope='module')
def base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('res08-base')
    mp = pytest.MonkeyPatch()
    p = paths.__wrapped__(tmp, mp)
    yield population.__wrapped__(p, tmp, mp)
    mp.undo()


@pytest.fixture
def case(base, tmp_path):
    source, _, candles, created, later, s = base
    copy_db(source, tmp_path/'investigation.db')
    copy_db(source.parent/'luffy.db', tmp_path/'luffy.db')
    return tmp_path/'investigation.db', tmp_path/'bridge.db', candles, created, later, s


def test_changed_data_revision_creates_new_experiment_identity(base, tmp_path):
    _, _, candles, created, later, s = base
    h = P.propose(s, 'volume_breakout_context.v1', created)
    copy = tmp_path/'changed.db'
    shutil.copyfile(candles, copy)
    a = E.translate(h, copy, CFG, later)
    with sqlite3.connect(copy) as db:
        db.execute('UPDATE candles SET volume=volume+1 WHERE rowid=(SELECT MIN(rowid) FROM candles)')
    b = E.translate(h, copy, CFG, later)
    assert a['experiment_id'] != b['experiment_id']


def test_bank_supported_label_cannot_self_certify(case):
    *_, s = case
    h = P.propose(s, 'volume_breakout_context.v1', case[3])
    with B.store(case[1]) as db:
        with pytest.raises(ValueError):
            B.feedback(db, h, None, 'SUPPORTED', None, {'state':'referee_passed'}, case[3])
        assert B.records(db, 'bridge_bank_results') == []


def cycle(case, fake, now):
    return B.cycle(case[0],case[1],case[2],CFG,now_ms=now,run=fake)


def rows(bank, table):
    with B.store(bank) as db:
        return B.records(db,table)


@pytest.fixture(scope='module')
def completed(base, tmp_path_factory):
    source, _, candles, created, later, s = base
    tmp=tmp_path_factory.mktemp('res08-completed')
    copy_db(source,tmp/'investigation.db')
    copy_db(source.parent/'luffy.db',tmp/'luffy.db')
    case=(tmp/'investigation.db',tmp/'bridge.db',candles,created,later,s)
    fake=NumericalFixture()
    cycle(case,fake,created)
    for _ in range(6): cycle(case,fake,later)
    results=rows(case[1],'bridge_bank_results')
    supported=next(r for r in results if r['classification']=='SUPPORTED')
    ex=next(e for e in rows(case[1],'bridge_experiments') if e['experiment_id']==supported['experiment_id'])
    h=P.hypothesis_from_dict(ex['hypothesis'])
    return case, fake, h, ex, supported


def reseal(body, h):
    from trader.research import predictive_receipt as M
    core={k:v for k,v in body.items() if k not in ('result_id','next_questions','conclusion_id')}
    body['conclusion_id']=P.digest(core)
    source=P.source_from_dict(json.loads(h.provenance_json)['source'])
    body['next_questions']=P.next_questions(source,body['classification'],[dict(
        conclusion_id=body['conclusion_id'],hypothesis_id=h.hypothesis_id,
        experiment_id=body['experiment_id'],classification=body['classification'])])
    body['result_id']=P.digest({k:v for k,v in body.items() if k!='result_id'})
    return body


def test_normal_exact_lineage_and_persisted_conclusion(completed):
    from trader.research import predictive_receipt as M
    from trader.cognition import predictive_bank
    case, _, h, ex, result=completed
    M.validate(h,ex,result)
    assert result['hypothesis_id']==h.hypothesis_id and result['hypothesis_schema']==h.schema
    assert result['experiment_id']==ex['experiment_id'] and result['experiment_schema']==ex['schema']
    assert result['measurement_id']==P.digest(result['measured_result'])
    assert result['quantitative_evidence_id']==P.digest(result['quantitative_evidence'])
    assert result['source_provenance_hash']==h.source_hash
    assert result['provenance']['split']==ex['split']
    assert result['provenance']['data_provenance']==ex['data_provenance']
    assert result['provenance']['evaluation_version']==ex['evaluation_version']
    assert {'fdr','growth','planner','ledger','strategy.vector_backtest'} <= set(ex['evaluation_version'])
    assert result['quantitative_outcome']=='PASS'
    assert result['limitations'] and result['next_questions']
    qs={q['question_id']:q for q in rows(case[1],'bridge_questions')}
    assert all(qs[q['question_id']]==q for q in result['next_questions'])
    extensions=predictive_bank.predictive_extensions(case[1],h.bank_id)
    assert result in extensions['results']
    assert ex['translation_contract']['proposal_contract']==json.loads(h.proposal_contract_json)
    assert ex['translation_contract']['falsification']==h.falsification_criteria


@pytest.mark.parametrize('mutation', [
    'hypothesis', 'experiment', 'wrong_cut', 'wrong_version', 'missing_referee', 'untested',
    'missing_measurement', 'raw_dependence', 'loose_alpha',
])
def test_rehashed_wrong_identity_stale_cut_and_unregistered_evidence_refused(completed,mutation):
    from trader.research import predictive_receipt as M
    _, _, h, ex, original=completed
    body=deepcopy(original)
    if mutation=='hypothesis': body['hypothesis_id']='0'*64
    if mutation=='experiment': body['experiment_id']='0'*64
    if mutation in ('wrong_cut','wrong_version'):
        r=body['measurement_receipts'][0]
        if mutation=='wrong_cut': r['binding']['split']['cut_ms']+=1
        else: r['binding']['evaluation_version']['job']='0'*64
        r['measurement_id']=P.digest({k:v for k,v in r.items() if k!='measurement_id'})
    if mutation=='missing_referee': body['referee_disposition'].pop('evidence')
    if mutation=='untested':
        measured=body['measured_result']
        measured['outcome']='UNTESTED'
        body['measurement_id']=P.digest(measured)
        for r in body['measurement_receipts']:
            for raw in (r.get('value') or {}).get('results',[]):
                if raw.get('hash')==measured['hash']:
                    raw['outcome']='UNTESTED'
            r['measurement_id']=P.digest({k:v for k,v in r.items() if k!='measurement_id'})
    if mutation=='missing_measurement': body['measurement_receipts']=[]
    if mutation in ('raw_dependence','loose_alpha'):
        q=body['quantitative_evidence']
        look=next(r for r in q['spent_looks'] if r['hash']==body['measured_result']['hash'])
        cand=next(r for r in q['candidates'] if r['hash']==body['measured_result']['hash'])
        g1=json.loads(cand['gate1'])
        if mutation=='raw_dependence':
            for key in ('a','b'): g1[key]['consistency_p_dep']=None
        else:
            look['alpha_t']=.9
            g1['alpha']=.9
        cand['gate1']=P.canonical(g1)
        body['quantitative_evidence_id']=P.digest(q)
    reseal(body,h)
    with pytest.raises(ValueError): M.validate(h,ex,body)


def test_duplicate_retry_and_fresh_interpreter_preserve_protected_provenance(completed):
    import subprocess
    import sys
    case, fake, h, ex, result=completed
    before=rows(case[1],'bridge_bank_results')
    calls=len(fake.calls)
    cycle(case,fake,case[4]+1)
    assert len(fake.calls)==calls
    assert rows(case[1],'bridge_bank_results')==before
    with B.store(case[1]) as db:
        duplicate=B.feedback(db,h,ex['experiment_id'],result['classification'],
            result['measured_result'],result['referee_disposition'],case[4]+2)
        assert duplicate==result
    script=('import json,sys; from trader.cognition.predictive_bank import predictive_extensions; '
            'print(json.dumps(predictive_extensions(sys.argv[1],sys.argv[2]),sort_keys=True))')
    replay=json.loads(subprocess.check_output([sys.executable,'-c',script,str(case[1]),h.bank_id],text=True))
    assert result in replay['results']
    assert next(e for e in replay['experiments'] if e['experiment_id']==ex['experiment_id'])==ex


def test_unsupported_paired_null_is_retained_without_quantitative_result(completed):
    case,*_=completed
    h=next(r['hypothesis'] for r in rows(case[1],'bridge_hypotheses')
           if r['hypothesis']['transformation']=='volume_breakout_no_benefit.v1')
    result=next(r for r in rows(case[1],'bridge_bank_results') if r['hypothesis_id']==h['hypothesis_id'])
    assert result['classification']=='NOT_TESTABLE' and result['quantitative_outcome']=='UNTESTED'
    assert result['experiment_id'] is result['measurement_id'] is result['quantitative_evidence_id'] is None
    assert result['measured_result'] is None and not result['predictive_strategy_validation']
    translations=[r for r in rows(case[1],'bridge_translations') if r['hypothesis_id']==h['hypothesis_id']]
    assert translations and translations[0]['translation']['status']=='UNSUPPORTED_EXPERIMENT_SHAPE'
    assert translations[0]['contract']['proposal_contract']==json.loads(h['proposal_contract_json'])


def test_unavailable_quantitative_result_cannot_read_a_candidate_as_pass(base):
    from types import SimpleNamespace
    _,_,_,created,_,s=base
    h=P.propose(s,'volume_breakout_context.v1',created)
    fake=NumericalFixture()
    from trader.research import job
    gauges=fake(job.measure_job,dict(cfg=dict(research=dict(predictive_split=dict(cut_ms=1))))).value['gauges']
    class Ledger:
        def gauges(self,_): return gauges
        def result(self,_): return dict(verdict='untested',outcome='UNTESTED',testable=False)
        def candidate(self,_): pytest.fail('UNTESTED must be refused before candidate admission')
    status, _, ref=B.classify(SimpleNamespace(ledger=Ledger()),h,{'experiment_id':'test'})
    assert status=='DATA_INSUFFICIENT' and ref['state']=='POWER_UNAVAILABLE'


def test_changed_protected_revision_and_cut_are_new_versions(base,tmp_path):
    _,_,candles,created,later,s=base
    h=P.propose(s,'volume_breakout_context.v1',created)
    copy=tmp_path/'revised.db'; shutil.copyfile(candles,copy)
    a=E.translate(h,copy,CFG,later)
    assert E.translate(h,copy,CFG,later)==a
    with sqlite3.connect(copy) as db:
        rid,raw=db.execute('SELECT revision_id,record_json FROM market_revisions WHERE event_ms>=? LIMIT 1',
                          (a['split']['cut_ms'],)).fetchone()
        record=json.loads(raw); record['transform_version']='TEST_ONLY_REVISED'
        db.execute('UPDATE market_revisions SET record_json=? WHERE revision_id=?',(P.canonical(record),rid))
    b=E.translate(h,copy,CFG,later)
    c=E.translate(h,copy,CFG,later+14_400_000)
    assert len({a['experiment_id'],b['experiment_id'],c['experiment_id']})==3
    assert a['data_provenance']['slices']['protected_b']!=b['data_provenance']['slices']['protected_b']
    E.validate(a); E.validate(b); E.validate(c)
    with pytest.raises(ValueError,match='source_data_revision_changed'):
        E.freeze_candles(copy,tmp_path/'snapshot.db',a)


def test_referee_wrong_cut_is_untested_and_still_spends_the_look(case):
    class WrongCut(NumericalFixture):
        def __call__(self,fn,payload,**kw):
            result=super().__call__(fn,payload,**kw)
            if fn.__name__=='referee_job': result.value['cut_ms']+=14_400_000
            return result
    fake=WrongCut()
    cycle(case,fake,case[3])
    for _ in range(6): cycle(case,fake,case[4])
    measured=[r for r in rows(case[1],'bridge_bank_results') if r['experiment_id']]
    final=measured[-1]
    assert final['classification']=='DATA_INSUFFICIENT'
    assert final['quantitative_outcome']=='UNTESTED'
    assert not final['predictive_strategy_validation']
    assert len(rows(case[1],'bridge_tests'))==1
    refused=[r for r in rows(case[1],'bridge_measurements') if r.get('acceptance')=='REFUSED']
    assert len(refused)==1 and refused[0]['refusal_reason']=='quantitative_wrong_cut'
    calls=len(fake.calls)
    cycle(case,fake,case[4]+1)
    assert len(fake.calls)==calls and len(rows(case[1],'bridge_tests'))==1


def test_registered_budget_is_copied_with_its_spent_sequence(case):
    from trader.research.ledger import Ledger
    from trader.core.journal import Journal
    h=P.propose(case[-1],'volume_breakout_context.v1',case[3])
    ex=E.translate(h,case[2],CFG,case[4])
    led=Ledger(Journal(case[0].parent/'luffy.db'))
    led.register_budget(.02,.01)
    _,alpha=led.next_alpha(.02,.01)
    led.record_test('prior-protected-look','4h','fixed','gate1',.8,alpha,False,
                    dict(cut_ms=ex['split']['cut_ms'],reason='OFFLINE_PRIOR_FIXTURE'))
    fake=NumericalFixture()
    cycle(case,fake,case[3])
    for _ in range(6): cycle(case,fake,case[4])
    supported=next(r for r in rows(case[1],'bridge_bank_results') if r['classification']=='SUPPORTED')
    budget=supported['quantitative_evidence']['registered_budget'][0]
    assert (budget['alpha'],budget['w0'])==(.02,.01)
    assert [r['seq'] for r in rows(case[1],'bridge_tests')]==[1,2]


def test_changed_source_version_is_retained_as_related_not_suppressed(base,tmp_path):
    from tests.test_investigation_research_family import measured
    from trader.observability import investigation_research as R
    old=base[-1]
    retained=json.loads(old.retained_chain_json)
    registered=json.loads(retained['evidence']['frozen']['case_payload'])['registered_ms']
    p=(tmp_path/'attention.db',tmp_path/'investigation.db',registered)
    iid,end=measured(p,'normalization')
    assert R.run(p[1],iid,recorded_at_ms=end+2)['status']=='OK'
    chain=R.chain(p[1],iid)
    new=P.source_from_chain(chain,chain['runs'][-1],end+2)
    a=P.propose(old,'volume_breakout_context.v1',old.available_ms)
    b=P.propose(new,'volume_breakout_context.v1',new.available_ms)
    assert a.source_hash!=b.source_hash and a.hypothesis_id!=b.hypothesis_id
    # Re-observing the same retained chain changes its availability version
    # while preserving the semantic question. It must also remain related.
    observed=P.source_from_chain(retained,retained,old.available_ms+1)
    c=P.propose(observed,'volume_breakout_context.v1',observed.available_ms)
    assert a.semantic_hash==c.semantic_hash and a.source_hash!=c.source_hash
    with B.store(tmp_path/'bank.db') as db:
        B.append(db,'bridge_hypotheses',a.hypothesis_id,dict(hypothesis=asdict(a)))
        assert B.prior_classification(db,b)[0]=='RELATED_PRIOR'
        assert B.prior_classification(db,c)[0]=='RELATED_PRIOR'


def test_legacy_replay_preserves_payload_but_cannot_certify_new_unbound_result(completed,tmp_path):
    from trader.cognition import predictive_bank
    _,_,h,current,_=completed
    ex=deepcopy(current)
    for key in ('translation_contract','evaluation_version','data_provenance'): ex.pop(key)
    ex['schema']='predictive-experiment.v1'
    ex['experiment_id']=P.digest({k:v for k,v in ex.items() if k!='experiment_id'})
    E.validate(ex)
    legacy=dict(schema='predictive-research-bank-result.v1',bank_kind='predictive_experiment',
        bank_id=h.bank_id,hypothesis_id=h.hypothesis_id,experiment_id=ex['experiment_id'],
        classification='INCONCLUSIVE',measured_result=None,
        referee_disposition=dict(state='PENDING_QUANTITATIVE_PIPELINE'),stage='MEASURED_RESULT',
        authority='RESEARCH_ONLY',predictive_strategy_validation=False)
    with B.store(tmp_path/'legacy.db') as db:
        B.append(db,'bridge_hypotheses',h.hypothesis_id,dict(hypothesis=asdict(h)))
        B.append(db,'bridge_experiments',ex['experiment_id'],ex)
        B.append(db,'bridge_bank_results',P.digest(legacy),legacy)
        with pytest.raises(ValueError,match='legacy_experiment_unbound'):
            B.feedback(db,h,ex['experiment_id'],'SUPPORTED',{},dict(state='referee_passed'),0)
    read=predictive_bank.predictive_extensions(tmp_path/'legacy.db',h.bank_id)
    assert read['results']==[legacy] and read['experiments']==[ex]
