"""Scheduling fixtures only: unfinished experiments never become economics."""
import json
import os
import pytest

from tests.test_compose_core import spec
from trader.research.closure import auto,canonical,study as S
from trader.research.closure.store import Store
from trader.research.compose import graph,search


@pytest.mark.parametrize('ceiling',['fits','reads','seconds'])
def test_fixed_budget_ends_work_without_stranding_later_units(tmp_path,monkeypatch,ceiling):
    store=Store(tmp_path/'study.db');sid='s';seen=[]
    fit=lambda p:{'fit_spec':dict(model='ridge',side='long',horizon=6,inputs=[{'f':'ret24'}],alpha=p)}
    roots=[spec(long={'top':{'a':fit(.01),'p':.8,'dir':'above'}},short=None),
           spec(long={'top':{'a':fit(.02),'p':.8,'dir':'above'}},short=None),
           spec(long={'cmp':'gt','a':{'f':'ret24'},'b':{'lit':0}},short=None)]
    for root in roots:root['structure']='long_only'
    budget=dict(max_fits=5,dataset_reads=100,cpu_seconds=100)
    if ceiling=='reads':budget.update(max_fits=50,dataset_reads=1)
    if ceiling=='seconds':budget.update(max_fits=50,cpu_seconds=1)
    study=dict(bank_path=None,journal_path=None,seed_families=[],seed_graphs=roots,
        search=budget,max_candidates=3,max_revisions=0,tf='4h',discovery_symbols=['A'])
    with store._tx() as db:
        db.execute("INSERT INTO studies(study_id,payload,status,steps) VALUES(?,?,'CREATED',0)",(sid,canonical(study)))
    S._ensure_unit(store,sid,'search','search')
    S._set_unit(store,sid,'search','DONE',1,result={})
    monkeypatch.setattr(S,'_load',lambda *a:(study,{}))
    def unit(st,sid,plan,env,u,now,run,**kwargs):
        seen.append(u['unit_id'])
        if u['kind']=='freeze':return dict(freeze_id=None,reason='SCHEDULING_FIXTURE:no numerical evidence')
        # Scheduling-only result. There is deliberately no numerical run or
        # freeze, so none of these fixtures can enter qualification.
        return dict(outcome='DEVELOPMENT_POSITIVE',result_id=u['unit_id'],reason='SCHEDULING_FIXTURE')
    monkeypatch.setattr(S,'_run_unit',unit)
    if ceiling=='seconds':
        row=store.start_resource(sid,'prior','develop',1)
        store.finish_resource(row,1001,1.,'DONE')
    out=S.step(store,sid,budget_s=30,child=False,cfg={},now_ms=2)
    units=store.units(sid)
    assert out['process_status']=='COMPLETED',out
    assert not [u for u in units if u['status'] in ('PENDING','RUNNING')]
    postponed=[u for u in units if u['status']=='POSTPONED']
    assert postponed and all('BUDGET_EXHAUSTED' in u['note'] for u in postponed)
    assert not store.all_once('qualifications') and not store.all_once('looks')
    assert auto.runnable(store,sid) is None
    totals=store.resource_totals(sid)
    if ceiling=='fits':
        cheap='develop:'+graph.make_candidate(roots[-1])['candidate_id']
        assert cheap in seen and totals['fits']==5
    if ceiling=='reads':assert totals['reads']==1
    before=(seen.copy(),totals)
    assert S.step(store,sid,budget_s=30,child=False,cfg={},now_ms=3)['process_status']=='COMPLETED'
    assert (seen,store.resource_totals(sid))==before
    assert search.budget_for(store,sid,study)['max_fits']==budget['max_fits']
    store.close()


def test_independent_assessment_keeps_its_own_worker_memory_limit(tmp_path,monkeypatch):
    from tests.test_calibration_execution_completion import _worker_isolation_probe
    store=Store(tmp_path/'assessment.db');sid='s'
    store.put_once('freezes','fixture',{'schema':'SCHEDULING_FIXTURE'})
    S._ensure_unit(store,sid,'freeze','freeze')
    S._set_unit(store,sid,'freeze','DONE',1,result={'freeze_id':'fixture'})
    def qualification(*args,run,**kwargs):
        result=run(_worker_isolation_probe,{'store_path':str(store.path)})
        assert result.ok,result.error
        return dict(status='INCONCLUSIVE',reason='SCHEDULING_FIXTURE',qualification_id=None,evidence_id=None)
    monkeypatch.setattr(S.qualification,'qualify',qualification)
    plan=dict(unit_timeout_s=10,search={'memory_mb':3072},snapshot_path='SCHEDULING_FIXTURE',
        workdir=str(tmp_path),book=[],source_journal_path=None,budget_journal_path=None)
    result=S._run_unit(store,sid,plan,{},dict(kind='qualify',unit_id='qualify'),2,None,
        timeout_s=10,child=True,cfg={})
    observed=json.loads((tmp_path/'assessment.db.worker-probe').read_text())
    assert observed['pid']!=os.getpid() and observed['memory_limit']==3072*1024*1024
    assert result['status']=='INCONCLUSIVE' and not store.all_once('looks')
    store.close()
