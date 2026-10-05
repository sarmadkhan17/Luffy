"""Live monitoring separates committed durability, contention and maintenance."""
import json
import sqlite3
import threading
import time
import pytest
from trader.observability.safety import SafetyHealth, SafetyObserver


def database(path):
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT)')


def observer(tmp_path):
    return SafetyObserver(SafetyHealth(tmp_path/'health.json', sink=lambda event: None))


def test_deployed_zero_timeout_refuses_legitimate_writer(tmp_path):
    p=tmp_path/'db';database(p)
    with sqlite3.connect(p) as writer:
        writer.execute('BEGIN IMMEDIATE')
        with sqlite3.connect(p,timeout=0) as probe:
            probe.execute('PRAGMA quick_check').fetchall()
            probe.execute('SELECT key,value FROM state_kv LIMIT 1').fetchall()
            with pytest.raises(sqlite3.OperationalError) as caught:
                probe.execute('INSERT OR REPLACE INTO state_kv VALUES (?,?)',('probe','x'))
            assert caught.value.sqlite_errorcode==sqlite3.SQLITE_BUSY
            assert caught.value.sqlite_errorname=='SQLITE_BUSY'


def test_real_contention_commits_after_legitimate_writer_releases(tmp_path):
    p=tmp_path/'db';database(p);locked=threading.Event()
    def write():
        with sqlite3.connect(p) as db:
            db.execute('BEGIN IMMEDIATE');locked.set();time.sleep(.15)
            db.execute('INSERT INTO state_kv VALUES (?,?)',('owner','retained'))
    t=threading.Thread(target=write);t.start();locked.wait()
    o=observer(tmp_path);r=o.store(p,'journal',journal=True);t.join()
    assert r['committed'] and r['status']=='AVAILABLE'
    assert [s['stage'] for s in r['stages']]==['connect','read','begin','write','commit']
    with sqlite3.connect(p) as db:
        assert db.execute('SELECT value FROM state_kv WHERE key=?',('owner',)).fetchone()==('retained',)
        assert db.execute('SELECT value FROM state_kv WHERE key=?',('__critical_storage_probe__',)).fetchone()
    assert not o.health.entry_block()


def test_persistent_busy_remains_unresolved_and_blocks(tmp_path,monkeypatch):
    import trader.data.sqlite_tx as T
    monkeypatch.setattr(T,'BUSY_TIMEOUT_S',.15)  # speed up the same deadline policy only in test
    p=tmp_path/'db';database(p)
    with sqlite3.connect(p) as writer:
        writer.execute('BEGIN IMMEDIATE');o=observer(tmp_path);r=o.store(p,'journal',journal=True)
    assert not r['committed'] and r['status']=='WRITE_UNAVAILABLE'
    assert r['sqlite_error']['sqlite_errorname']=='SQLITE_BUSY'
    assert r['sqlite_error']['phase']=='begin'
    assert r['sqlite_error']['reason']=='SQLITE_BUSY_WRITABILITY_UNRESOLVED'
    assert .1<=r['elapsed_s']<.8 and o.health.entry_block()


@pytest.mark.parametrize('code',[sqlite3.SQLITE_FULL,sqlite3.SQLITE_READONLY,sqlite3.SQLITE_IOERR_WRITE])
@pytest.mark.parametrize('phase',['write','commit'])
def test_storage_failure_and_commit_failure_never_claim_success(tmp_path,monkeypatch,code,phase):
    p=tmp_path/'db';database(p);connect=sqlite3.connect
    class Fault(sqlite3.Connection):
        def fail(self):
            e=sqlite3.OperationalError('private data must not be echoed')
            e.sqlite_errorcode=code;e.sqlite_errorname={13:'SQLITE_FULL',8:'SQLITE_READONLY',778:'SQLITE_IOERR_WRITE'}[code]
            raise e
        def execute(self,sql,*a,**k):
            if phase=='write' and sql.startswith('INSERT'):self.fail()
            return super().execute(sql,*a,**k)
        def commit(self):
            if phase=='commit':self.fail()
            return super().commit()
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:connect(*a,**k,factory=Fault))
    o=observer(tmp_path);r=o.store(p,'journal',journal=True)
    assert not r['committed'] and r['sqlite_error']['phase']==phase
    assert r['sqlite_error']['sqlite_errorcode']==code and o.health.entry_block()
    assert 'private' not in json.dumps(r)
    with connect(p) as db:assert not db.execute('SELECT * FROM state_kv').fetchall()


def test_live_probe_does_not_scan_integrity_and_keeps_prior_status(tmp_path,monkeypatch):
    p=tmp_path/'db';database(p);o=observer(tmp_path)
    result=o.integrity(p,'journal');assert result['status']=='VERIFIED'
    connect=sqlite3.connect
    class NoScan(sqlite3.Connection):
        def execute(self,sql,*a,**k):
            assert 'quick_check' not in sql and 'integrity_check' not in sql
            return super().execute(sql,*a,**k)
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:connect(*a,**k,factory=NoScan))
    assert o.store(p,'journal',journal=True)['committed']
    assert o.health.read()['integrity']['journal']==result


def test_heartbeat_only_does_not_touch_database_and_full_pass_observes_it_first(tmp_path,monkeypatch):
    from scripts.monitor import safety_check
    calls=[]
    monkeypatch.setattr(SafetyObserver,'heartbeat',lambda *a,**k:calls.append('heartbeat') or {'status':'FRESH'})
    monkeypatch.setattr(SafetyObserver,'store',lambda *a,**k:calls.append('store') or {'status':'AVAILABLE'})
    safety_check(tmp_path,{},heartbeat_only=True);assert calls==['heartbeat']
    calls.clear();safety_check(tmp_path,{});assert calls==['heartbeat','store']


def test_nullable_receipt_metadata_restores_null_but_required_values_refuse():
    from trader.data.market_provenance import receipt_metadata
    from trader.data.market_provenance import META
    row={k:'identity' for k in META}
    for k in ('event_time_ms','observed_at_ms','available_at_ms','request_started_ms','max_age_ms'):row[k]=123
    row['supersedes']=float('nan');row['max_age_ms']=float('nan')
    r=receipt_metadata(row);assert r['supersedes'] is None and r['max_age_ms'] is None
    row['revision_id']=float('nan')
    with pytest.raises(ValueError,match='required_receipt_metadata_unavailable'):receipt_metadata(row)

from tests.test_historical_outcome_capture import prospective, CUT


def test_normal_outcome_capture_nullable_metadata_and_bad_capture_isolation(prospective,monkeypatch):
    import pandas as pd
    from dataclasses import replace
    from datetime import datetime, timezone
    from trader.engine.outcomes import resolve_pending
    from trader.core.types import Action
    from trader.learning import capture as C,capture_runtime as R
    import trader.core.journal as JM
    j,old,inputs=prospective
    monkeypatch.setattr(JM,'now_utc',lambda:datetime.fromtimestamp(CUT/1000,timezone.utc))
    for name in ('bad','valid'):
        decision=replace(old,id=name,action=Action.BUY,symbol=name)
        j.log_decision(decision,capture_inputs=inputs)
        j.schedule_outcome(name,decision.cycle_id,name,decision.ts,'BUY',100)
    # The legacy resolver keeps other rows moving even if strict evidence
    # capture refuses one. Future bars also fail the existing replay contract.
    class Feed:
        def fetch_ohlcv(self,symbol,*a,**k):
            return pd.DataFrame({'ts':pd.to_datetime([CUT+3600000,CUT+14400000],unit='ms',utc=True),
                'close':[float('nan') if symbol=='bad' else 110,120],
                'taker_buy':[float('nan'),float('nan')]})
    assert resolve_pending(j,Feed(),now_ms=CUT+14400000+300000)==2
    failed=j.query('SELECT * FROM learning_capture_failures')
    assert len(failed)==1 and failed[0]['event_key']=='decision:bad'
    assert 'forward_target_nonfinite_required_value' in failed[0]['reason']
    complete=j.query("SELECT outcome_id,payload FROM learning_outcome_captures WHERE outcome_key LIKE 'forward:%'")
    assert len(complete)==1
    body=json.loads(complete[0]['payload']);dep=next(x for x in body['dependencies'] if x['role']=='outcome')
    with j._conn() as db:measurement=C.resolve(db,dep)
    assert measurement['target_bars']['1h']['taker_buy'] is None
    assert not any('NaN' in r['payload'] for r in j.query('SELECT payload FROM learning_source_blobs'))
    assert j.query('SELECT * FROM learning_capture_failures')==failed


def test_real_collector_producer_worker_reader_with_mixed_revisions_at_observed_scale(tmp_path):
    from tests.test_bounded_evidence_latency import Venue
    from tests.test_attention_telemetry import frames
    from trader.data.feed import Universe
    from trader.data import market_provenance as M
    from trader.observability.collector import Collector
    from trader.observability import scan_source as S
    from trader.core import journal_evidence as E
    ex=Venue();universe=Universe({'universe':{'majors':['S0/USDT','S1/USDT','S2/USDT'],
        'auto_scan':dict(top_n=16,min_volume_usdt=1,min_price=1,min_age_days=0)}},ex)
    universe._rescan();members=universe.symbols();cut=int(time.time()*1000)
    original=frames(len(members),cut);data={}
    with sqlite3.connect(tmp_path/'source.db') as db:
        M.init(db)
        for symbol,source in zip(members,original.values()):
            df=source['4h'];key='candle:'+symbol+':4h';M.append(db,key,df)
            # Real revision append/load naturally produces a mixed column:
            # initial bars have no predecessor, the revised last bar does.
            updated=M.annotate(df.iloc[-1:][['ts','open','high','low','close','volume']],
                instrument_id=df['instrument_id'].iloc[-1],source=df['source'].iloc[-1],
                kind='candle',received_ms=cut+1,timeframe='4h')
            M.append(db,key,updated);data[symbol]={'4h':M.load(db,key,as_of_ms=cut+2)}
            assert data[symbol]['4h']['supersedes'].isna().any()
    child=Collector(tmp_path,start=True)
    try:
        sid=child.begin(data,members,as_of_ms=cut+2,membership_receipts=universe.membership_receipts(as_of_ms=cut))
        child.causes(sid,[])
        deadline=time.monotonic()+25
        while child.queue.unfinished_tasks and time.monotonic()<deadline:time.sleep(.02)
        assert not child.queue.unfinished_tasks
        h=child.health();assert not h['errors'],h['recent_errors']
        assert h['last_complete']['scan_id']==sid
        with sqlite3.connect(tmp_path/'attention.db') as db:
            text=db.execute('SELECT payload FROM scans WHERE scan_id=?',(sid,)).fetchone()[0]
            assert len(text.encode())>=16_226_290
            physical=db.execute('SELECT detail FROM scan_sources_v1 WHERE scan_id=?',(sid,)).fetchone()[0]
            assert E.resolve(db,physical)==text
            result=S.read(db,sid,time.monotonic()+5)
            assert result['scan_id']==sid and len(result['membership'])==16
            assert any(x['receipt']['supersedes'] is None for x in result['market_source_receipts'])
        assert (tmp_path/'attention.db').stat().st_size<=64*1024**2
    finally:child.close()
