import json
from types import SimpleNamespace
import pytest
from trader.dashboard import overview_desk as D

@pytest.fixture
def runtime(tmp_path, monkeypatch):
    (tmp_path/'data').mkdir()
    from trader import runtime_identity
    monkeypatch.setattr(runtime_identity,'status_only',lambda root: dict(state='RUNNING',verified=True,record=dict(pid=42,started_at=900)))
    data=dict(pid=42,latest_scan=dict(schema='current-scan-summary.v1',scan_id='42:3',observed_at=1000.,shortlisted=2,evaluated=1,
        coins=[dict(decision_id='d1',symbol='BTC/USDT',action='HOLD',score=0.,threshold=.24,confidence=0.,executed=False,evaluated=True),
               dict(decision_id='scan:ETH',symbol='ETH/USDT',action='NOT_EVALUATED',score=None,threshold=None,confidence=None,executed=False,evaluated=False,skip_reason='NO_ELIGIBLE_STRATEGY')]))
    def write(): (tmp_path/'data/cycle_runtime.json').write_text(json.dumps(data))
    write();return tmp_path,data,write

def test_current_completed_scan_does_not_query_historical_decisions(runtime):
    root,data,write=runtime
    class NoJournalReads:
        def query(self,*args): pytest.fail('must not show old MAGMA or write nontrades')
    scan=D.latest_scan(NoJournalReads(),root=root,now=1010)
    assert [c['symbol'] for c in scan['coins']]==['BTC/USDT','ETH/USDT']
    assert scan['coins'][0]['score']==0 and scan['coins'][1]['score'] is None
    assert scan['freshness']=='fresh' and scan['age_s']==10 and scan['traded']==0
    assert scan['shortlisted']==2 and scan['evaluated']==1
    assert D.latest_scan(None,root=root,now=1200)['freshness']=='stale'

@pytest.mark.parametrize('variant',['other_pid','old_instance','future','bad_number','too_many'])
def test_invalid_or_other_instance_never_falls_back(runtime,variant):
    root,data,write=runtime
    if variant=='other_pid':data['pid']=43
    if variant=='old_instance':data['latest_scan']['observed_at']=899
    if variant=='future':data['latest_scan']['observed_at']=1100
    if variant=='bad_number':data['latest_scan']['coins'][0]['score']=float('nan')
    if variant=='too_many':data['latest_scan']['coins']*=31
    write()
    with pytest.raises(ValueError): D.latest_scan(None,root=root,now=1010)

def test_first_cycle_waits_instead_of_showing_old_records(runtime):
    root,data,write=runtime;data['latest_scan']=None;write()
    assert D.latest_scan(None,root=root,now=1010) is None

def test_stopped_process_does_not_claim_a_current_scan(runtime,monkeypatch):
    from trader import runtime_identity
    monkeypatch.setattr(runtime_identity,'status_only',lambda root:dict(state='STOPPED',verified=False))
    with pytest.raises(ValueError):D.latest_scan(None,root=runtime[0],now=1010)


def test_runtime_summary_is_completed_batch_scalars_and_detached(tmp_path):
    from tests.test_split_kernel_runtime import minimal
    _, runtime = minimal(tmp_path)
    runtime.scans_completed = 3
    runtime.last_scan_completed_at = 1000.
    runtime.last_decision_stats = dict(shortlisted=1,decisions=1,scan_coins=[dict(symbol='BTC/USDT',score=0)])
    snapshot = runtime.snapshot()['latest_scan']
    assert snapshot['schema']=='current-scan-summary.v1' and snapshot['observed_at']==1000.
    assert snapshot['evaluated']==1 and snapshot['coins'][0]['score']==0
    runtime.last_decision_stats['scan_coins'][0]['score']=.9
    assert snapshot['coins'][0]['score']==0
    runtime.last_scan_completed_at=None
    assert runtime.snapshot()['latest_scan'] is None


def test_http_desk_uses_completed_runtime_batch_not_old_journal(runtime, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import make_app
    from tests.test_trade_history import H
    root,data,write=runtime
    app,journal,_=make_app(root,monkeypatch)
    data['latest_scan']['observed_at']=time.time()
    write()
    result=TestClient(app).get('/owner-api/v1/desk',headers=H)
    assert result.status_code==200
    body=result.json()
    assert 'scan' not in body['errors']
    assert body['scan']['scan_id']=='42:3'
    assert body['scan']['evaluated']==1 and len(body['scan']['coins'])==2
    assert body['scan']['coins'][0]['symbol']=='BTC/USDT'
    assert body['scan']['freshness']=='fresh'
