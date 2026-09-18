"""Recovery contract and adversarial temporal/identity tests; no live data."""
import copy
import json
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from trader.observability import collector_health as H, collector as C
from trader.observability.store import Store
from tests.test_attention_telemetry import frames, event
from tests.test_attention_learning import publish, update_health

NOW=1789689660000


def collector(tmp_path):
    now=[NOW]
    c=C.Collector(tmp_path,start=False,clock=lambda:now[0])
    c.thread=SimpleNamespace(is_alive=lambda:True)
    return c,now


def cycle(c, now):
    sid=c.begin(frames(now=now),['S0/USDT','S1/USDT'],as_of_ms=now)
    c.causes(sid,[{'symbol':'S0/USDT','decision_id':'test'}])
    for _ in range(2):
        e=c.queue.get_nowait()
        c._sync_producer()
        with patch('trader.observability.store.time',SimpleNamespace(time=lambda:now/1000)):
            st=Store(c.path,c.cfg);proof=st.write(e);st.close()
        c._success(e,proof);c._health['processed']+=1;c.queue.task_done()
    c._write_health()
    return sid


def failure(c,reason='worker_failed'):
    c._health['worker_errors']+=1
    rec=dict(ts_ms=c.clock(),seq=c._producer[0],scan_id='failed',phase='worker',error_class=reason)
    c._incident(rec);c._recent.append(rec);c._write_health()


def test_real_persistence_recovery_keeps_errors_and_binds_snapshot(tmp_path):
    c,n=collector(tmp_path); cycle(c,n[0])
    source,ev=H.bound_snapshot(c.path,now_ms=n[0]);assert ev['failure_generation']==0
    failure(c)
    with pytest.raises(H.Refused): H.bound_snapshot(c.path,now_ms=n[0])
    n[0]+=H.QUARANTINE_MS
    cycle(c,n[0])
    with pytest.raises(H.Refused): H.bound_snapshot(c.path,now_ms=n[0])
    n[0]+=1; sid=cycle(c,n[0])
    source,ev=H.bound_snapshot(c.path,now_ms=n[0])
    assert source[0]['scan_id']==sid and ev['errors']==1 and ev['certificate']
    assert c.health()['recent_errors'] and c.health()['status']=='recovered'
    failure(c)
    with pytest.raises(H.Refused): H.bound_snapshot(c.path,now_ms=n[0])


def test_prequeued_scans_do_not_cross_failure_observation_fence(tmp_path):
    c,n=collector(tmp_path)
    for _ in range(2):
        sid=c.begin(frames(now=n[0]),['S0/USDT'],as_of_ms=n[0]);c.causes(sid,[])
    failure(c);assert c.health()['fence_seq']==2
    n[0]+=H.QUARANTINE_MS
    while not c.queue.empty():
        e=c.queue.get_nowait()
        with patch('trader.observability.store.time',SimpleNamespace(time=lambda:n[0]/1000)):
            st=Store(c.path,c.cfg); proof=st.write(e);st.close()
        c._success(e,proof);c.queue.task_done()
    c._write_health();assert c.health()['certificate'] is None


def test_producer_signal_survives_full_event_queue_and_lost_details(tmp_path):
    c,n=collector(tmp_path)
    for i in range(100):
        c._health['dropped']+=1;c._producer_failure(str(i),'queue_full','scan')
    assert c.health()['last_error']=='producer_failure_pending'
    c._write_health();h=c.health()
    assert h['failure_generation']==100 and h['details_lost']==36
    assert len(h['recent_errors'])<=64
    assert c.health_path.stat().st_size<=H.MAX_HEALTH_BYTES


def test_capture_failure_has_sequence_and_invalidates_certificate(tmp_path,monkeypatch):
    c,n=collector(tmp_path);cycle(c,n[0])
    monkeypatch.setattr(C,'capture',lambda *a:(_ for _ in ()).throw(ValueError('SECRET')))
    assert c.begin({},[]) is None
    assert c.health()['fence_seq']==2 and c.health()['certificate'] is None
    c._write_health();assert 'SECRET' not in c.health_path.read_text()


@pytest.mark.parametrize('change,reason',[
    ({'health_schema':'old'},'collector_health_schema_old'),
    ({'updated_ms':NOW+1},'collector_health_future'),
    ({'updated_ms':NOW-300001},'collector_health_stale'),
    ({'worker_alive':False},'collector_worker_dead'),
    ({'errors':True},'collector_health_invalid'),
    ({'errors':1},'collector_health_invalid'),
    ({'failure_generation':None},'collector_health_invalid'),
    ({'last_complete':None},'snapshot_incomplete'),
    ({'instance_id':'x'},'collector_health_invalid'),
])
def test_malformed_and_current_failure_health_refuses(tmp_path,change,reason):
    p=tmp_path/'attention.db';publish(p,NOW);update_health(p,**change)
    with pytest.raises(H.Refused,match=reason):H.bound_snapshot(p,now_ms=NOW)


@pytest.mark.parametrize('part',['scan','cause','marker','complete'])
def test_missing_or_wrong_persisted_identity_refuses(tmp_path,part):
    p=tmp_path/'attention.db';publish(p,NOW)
    with sqlite3.connect(p) as db:
        if part=='scan':
            x=json.loads(db.execute('SELECT payload FROM scans').fetchone()[0]);x['collector_identity']['instance_id']='2'*32
            db.execute('UPDATE scans SET payload=?',(json.dumps(x),))
        elif part=='complete':db.execute('UPDATE scans SET causes_complete=0')
        else:
            rows=db.execute('SELECT event_id,payload FROM causes').fetchall()
            for eid,payload in rows:
                x=json.loads(payload)
                if part=='marker' and x.get('collector_completion'):db.execute('DELETE FROM causes WHERE event_id=?',(eid,))
                elif part=='cause' and not x.get('collector_completion'):
                    x['collector_identity']['seq']+=1;db.execute('UPDATE causes SET payload=? WHERE event_id=?',(json.dumps(x),eid))
    with pytest.raises(H.Refused):H.bound_snapshot(p,now_ms=NOW)


def test_health_recheck_detects_failure_or_restart(tmp_path,monkeypatch):
    p=tmp_path/'attention.db';publish(p,NOW);raw=H.read_health(p.with_name('attention_health.json'));calls=[]
    def read(_):
        calls.append(1);x=copy.deepcopy(raw)
        if len(calls)>1:x['instance_id']='2'*32
        return x
    monkeypatch.setattr(H,'read_health',read)
    with pytest.raises(H.Refused,match='health_changed_during_read'):H.bound_snapshot(p,now_ms=NOW)


def test_pending_new_attempt_does_not_select_arbitrary_newer_row(tmp_path):
    p=tmp_path/'attention.db';publish(p,NOW,'first');old=H.read_health(p.with_name('attention_health.json'))
    publish(p,NOW+1,'second');p.with_name('attention_health.json').write_text(json.dumps(dict(old,updated_ms=NOW+1)))
    src,_=H.bound_snapshot(p,now_ms=NOW+1);assert src[0]['scan_id']=='first'


def test_live_health_clock_sampled_after_read(tmp_path,monkeypatch):
    p=tmp_path/'attention.db';publish(p,NOW+1)
    monkeypatch.setattr(H,'clock_ms',lambda:NOW+1)
    assert H.bound_snapshot(p)[1]['accepted']
    with pytest.raises(H.Refused,match='future'):H.bound_snapshot(p,now_ms=NOW)


def test_empty_child_response_classification_and_hash_mismatch(tmp_path,monkeypatch):
    c,n=collector(tmp_path)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='',stderr='ModuleNotFoundError: SECRET',returncode=1))
    with pytest.raises(C.WorkerError,match='ModuleNotFoundError') as e:c._run(event())
    assert 'SECRET' not in str(e.value) and e.value.phase=='child_no_result'
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=json.dumps({'ok':True,'code_hash':'wrong'}),stderr='',returncode=0))
    with pytest.raises(C.WorkerError,match='code_mismatch'):c._run(event())


def test_timeout_committed_row_never_earns_credit(tmp_path,monkeypatch):
    c,n=collector(tmp_path)
    sid=c.begin(frames(now=n[0]),['S0/USDT'],as_of_ms=n[0])
    def fail(e):
        with patch('trader.observability.store.time',SimpleNamespace(time=lambda:n[0]/1000)):
            st=Store(c.path,c.cfg);st.write(e);st.close()
        c.close();raise subprocess.TimeoutExpired('safe',10)
    monkeypatch.setattr(c,'_run',fail);c._loop()
    assert c.health()['timeouts']==1 and c.health()['certificate'] is None
    assert c.health()['recent_errors'][-1]['row_present_after_timeout'] is True


def test_actual_child_and_thread_publish_bound_receipt(tmp_path):
    import time
    c=C.Collector(tmp_path)
    try:
        now=H.clock_ms();sid=c.begin(frames(now=now),['S0/USDT'],as_of_ms=now)
        c.causes(sid,[])
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                source,evidence=H.bound_snapshot(c.path)
                assert source[0]['scan_id']==sid and evidence['accepted']
                break
            except H.Refused:
                time.sleep(.05)
        else: pytest.fail('actual worker did not publish valid bound snapshot')
    finally:
        c.close();c.thread.join(timeout=12)
    assert not c.thread.is_alive()


def test_gap_transition_deduplicates_and_retains_restart(tmp_path):
    c,n=collector(tmp_path);cycle(c,n[0]);failure(c)
    n[0]+=H.QUARANTINE_MS;cycle(c,n[0]);n[0]+=1;cycle(c,n[0])
    _,e=H.bound_snapshot(c.path,now_ms=n[0])
    events=[]
    pop=SimpleNamespace(version='window',emit=lambda *args:events.append(args))
    with sqlite3.connect(':memory:') as db:
        H.record(db,pop,e,n[0]);H.record(db,pop,e,n[0]+1)
        assert len(events)==1 and events[0][2]['reason']=='collector_outage'
        e=dict(e,instance_id='2'*32,certificate=None)
        H.record(db,pop,e,n[0]+2)
        assert len(events)==2 and events[-1][2]['reason']=='collector_instance_changed'
