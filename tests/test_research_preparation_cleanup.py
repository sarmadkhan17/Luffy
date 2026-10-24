"""Interrupted preparation only discards its own unfinished files."""
from pathlib import Path
from trader.core.child import ChildResult
from trader.research.closure import auto


def test_timeout_reclaims_child_partial_output(tmp_path,monkeypatch):
    root=tmp_path/'inputs'
    def run(job,payload,limits,child):
        (Path(payload['snapshot_dir'])/'large-unfinished.db').write_bytes(b'partial')
        return ChildResult(ok=False,timed_out=True,error='deadline')
    monkeypatch.setattr(auto,'_run',run)
    out=auto._prepare(None,{'snapshot_dir':str(root)},auto.DEFAULTS,{},timeout_s=5,child=True)
    assert not out.ok and out.timed_out and list(root.iterdir())==[]


def test_success_delivers_completed_files_and_retains_existing_inputs(tmp_path,monkeypatch):
    root=tmp_path/'inputs';root.mkdir();(root/'unrelated.db').write_bytes(b'owner data')
    def run(job,payload,limits,child):
        p=Path(payload['snapshot_dir'])/'candles-1.db';p.write_bytes(b'completed')
        return ChildResult(ok=True,value={'candles':{'path':str(p)},'derivs':None})
    monkeypatch.setattr(auto,'_run',run)
    out=auto._prepare(None,{'snapshot_dir':str(root)},auto.DEFAULTS,{},timeout_s=5,child=True)
    assert out.ok and Path(out.value['candles']['path']).read_bytes()==b'completed'
    assert (root/'unrelated.db').read_bytes()==b'owner data' and not list(root.glob('.preparing-*'))
    before=(root/'candles-1.db').read_bytes()
    refused=auto._prepare(None,{'snapshot_dir':str(root)},auto.DEFAULTS,{},timeout_s=5,child=True)
    assert not refused.ok and (root/'candles-1.db').read_bytes()==before
    assert not list(root.glob('.preparing-*'))


def test_running_preparation_is_visible_without_changing_retry_policy(tmp_path,monkeypatch):
    from trader.research.closure.store import Store
    cfg={'research':{'closure':{'enabled':True,'auto':{'enabled':True,'snapshot_dir':str(tmp_path/'inputs'),'market_db':str(tmp_path/'missing.db')}}}}
    a=auto.settings(cfg);store=Store(tmp_path/'research.db')
    def prepare(*args,**kwargs):
        assert store.schedule_events(1)[0]['status']=='PREPARING'
        assert auto.due(store,a,100)[0] # progress event is not a fabricated completed attempt
        return ChildResult(ok=False,error='test missing source')
    monkeypatch.setattr(auto,'_prepare',prepare)
    out=auto.tick(cfg,store,a,now_ms=100,budget_s=60,child=False)
    assert out['status']=='PREP_FAILED' and not auto.due(store,a,101)[0]
    assert [r['status'] for r in store.schedule_events(2)]==['PREP_FAILED','PREPARING']
    store.close()
