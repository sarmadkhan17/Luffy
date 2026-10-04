"""Real Universe -> Attention Store -> Portfolio source path at cutover scale."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
import pytest
from trader.core import journal_evidence as E
from trader.core.journal import Journal
from trader.data.feed import Universe
from trader.observability.attention import capture, settings
from trader.observability.store import Store, export_scan
from trader.observability import scan_source as S
from scripts.opportunity_context_shadow import capture as portfolio_capture, _json
from tests.test_attention_telemetry import frames
from tests.test_opportunity_live_integration import snapshot


class Venue:
    id = 'binanceusdm'
    # Match the large ccxt source mapping observed at the cutover, with a
    # production-shaped ticker population. All transport is local and fake.
    urls = {'api': {str(i): 'https://offline.example/fapi/v'+str(i) for i in range(24)}}
    def market(self, symbol):
        return dict(id=symbol.replace('/', ''), contract=True)
    def fetch_tickers(self):
        return {f'S{i}/USDT': dict(timestamp=int(time.time()*1000),
            quoteVolume=1_000_000+i, last=10, info=dict(symbol=f'S{i}USDT',
            bidPrice='10.00000000', askPrice='10.10000000', retained_endpoint_fields='x'*800))
            for i in range(557)}
    def fetch_ohlcv(self, *a, **kw):
        return [[1, 10, 11, 9, 10, 5]]


def test_real_producer_reader_large_source_retry_replay_growth(tmp_path, monkeypatch):
    import trader.data.feed as F
    ex=Venue();monkeypatch.setattr(F,'make_exchange',lambda *a,**k: ex)
    u=Universe({'universe': {'majors': ['S0/USDT','S1/USDT','S2/USDT'],
        'auto_scan': dict(top_n=10,min_volume_usdt=1,min_price=1,min_age_days=0)}},ex)
    u._rescan();members=u.symbols();at=int(time.time()*1000)
    data=frames(len(members),at)
    data=dict(zip(members,data.values()))
    cfg=settings({'max_symbols':16,'max_bytes':128*1024**2})
    event=capture(data,members,'observed-scale',cfg,at,
        membership_receipts=u.membership_receipts(as_of_ms=at))
    store=Store(tmp_path/'attention.db',cfg);store.write(event)
    store.write(dict(kind='causes',scan_id=event['scan_id'],as_of_ms=at,items=[]))
    original=store.db.execute('SELECT payload FROM scans').fetchone()[0]
    assert len(original.encode())>=16_226_290
    with pytest.raises(ValueError,match='SOURCE_PAYLOAD_BOUND_EXCEEDED'):_json(original)
    manifest=store.db.execute('SELECT detail FROM scan_sources_v1').fetchone()[0]
    assert len(manifest.encode())<S.LIMIT
    assert hashlib.sha256(original.encode()).hexdigest()==json.loads(manifest[len(E.PREFIX):])['sha256']
    assert E.resolve(store.db,manifest)==original
    before=store.db.execute('SELECT count(*),sum(length(payload)) FROM journal_evidence_blobs_v1').fetchone()
    store.write(event)
    assert store.db.execute('SELECT count(*),sum(length(payload)) FROM journal_evidence_blobs_v1').fetchone()==before
    assert store.db.execute('SELECT count(*) FROM scans').fetchone()[0]==1
    j=Journal(tmp_path/'luffy.db');j.kv_set('venue_position_snapshot',json.dumps(snapshot(side=None,cut=at)));j.kv_set('control_state','FROZEN')
    import trader.core.config as C
    config=C.load_config();config['attention']['stale_seconds']=300
    requests,_,control,inventory,detail=portfolio_capture(j.db_path,store.path,tmp_path/'absent.db',config)
    assert control=='FROZEN' and detail['scan_id']=='observed-scale' and requests
    retained_scan=json.loads(inventory.payload_json)['scan']
    assert retained_scan==json.loads(original)
    export_scan(store.path,'observed-scale',tmp_path/'replay.json')
    assert json.loads((tmp_path/'replay.json').read_text())['scan']==retained_scan
    # Cached verified chunks must never hide a changed stored source.
    store.db.execute("UPDATE journal_evidence_blobs_v1 SET payload=x'00' WHERE sha256=(SELECT sha256 FROM journal_evidence_blobs_v1 LIMIT 1)")
    with pytest.raises(E.EvidenceError):S.latest(store.db,time.monotonic()+5)
    store.close()


def test_decoder_cache_scoped_to_exact_bytes_and_deadline():
    db=sqlite3.connect(':memory:');db.executescript(E.SCHEMA)
    marker=E.store(db,'a'*200_000)
    assert E.resolve(db,marker)=='a'*200_000
    with pytest.raises(E.EvidenceError,match='deadline'):E.resolve(db,marker,deadline=time.monotonic()-1)
    db.execute("UPDATE journal_evidence_blobs_v1 SET byte_length=byte_length+1")
    with pytest.raises(E.EvidenceError):E.resolve(db,marker)


def test_existing_source_packet_keeps_exact_owner_root_when_anchored(tmp_path):
    db=sqlite3.connect(tmp_path/'legacy.db')
    db.executescript('CREATE TABLE scans(scan_id TEXT PRIMARY KEY,payload TEXT,causes_complete INTEGER);'
                     'CREATE TABLE scan_sources_v1(scan_id TEXT PRIMARY KEY,detail TEXT NOT NULL);'+E.SCHEMA)
    receipt=dict(raw='x'*350_000,context=dict(endpoint='retained'))
    text=json.dumps(dict(scan_id='legacy',membership=[dict(source_receipt=receipt) for _ in range(12)]),
                    sort_keys=True,separators=(',',':'))
    db.execute('INSERT INTO scans VALUES (?,?,1)',('legacy',text))
    original=E.store(db,text)
    db.execute('INSERT INTO scan_sources_v1 VALUES (?,?)',('legacy',original));db.commit()
    S.ensure(db)
    with db:S.publish(db,'legacy',text)
    primary,alternate=db.execute('SELECT detail,anchored_detail FROM scan_sources_v1').fetchone()
    assert primary==original and alternate and E.resolve(db,primary)==E.resolve(db,alternate)==text
    assert json.loads(primary[len(E.PREFIX):])['sha256']==json.loads(alternate[len(E.PREFIX):])['sha256']
    before=db.execute('SELECT count(*),sum(length(payload)) FROM journal_evidence_blobs_v1').fetchone()
    with db:S.publish(db,'legacy',text);S.prune(db)
    assert db.execute('SELECT count(*),sum(length(payload)) FROM journal_evidence_blobs_v1').fetchone()==before
    assert S.latest(db,time.monotonic()+5)==json.loads(text)
    assert E.resolve(db,primary)==text
    with pytest.raises(ValueError,match='SCAN_SOURCE_IDENTITY_CHANGED'):
        S.publish(db,'legacy',text.replace('retained','modified'))


def test_latest_decision_index_retains_equal_timestamp_selection(tmp_path):
    j=Journal(tmp_path/'journal.db')
    with sqlite3.connect(j.db_path) as db:
        db.execute('DROP INDEX idx_decisions_current_ts')
        for n in range(80):
            db.execute('INSERT INTO cycles(id,ts,symbol) VALUES (?,?,?)',(str(n),'2026-01-01','BTC/USDT'))
            db.execute('INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence) VALUES (?,?,?,?,?,0,0,0)',
                       (str(n),str(n),'2026-01-01' if n<40 else '2026-01-02','BTC/USDT','HOLD'))
        q='SELECT id,cycle_id,ts,symbol,signals_json FROM decisions ORDER BY ts DESC LIMIT 32'
        original=db.execute(q).fetchall()
        db.execute('CREATE INDEX idx_decisions_current_ts ON decisions(ts DESC)')
        assert db.execute(q).fetchall()==original
        assert 'idx_decisions_current_ts' in str(db.execute('EXPLAIN QUERY PLAN '+q).fetchall())
        assert 'TEMP B-TREE' not in str(db.execute('EXPLAIN QUERY PLAN '+q).fetchall())


def test_source_digest_memo_never_reuses_changed_payload_or_supplied_hash(monkeypatch):
    from trader.portfolio import allocator as A
    with A._SOURCE_DIGEST_LOCK:
        A._SOURCE_DIGESTS.clear();A._SOURCE_DIGEST_BYTES=0
    original=A.Source.freeze('source',dict(context='retained'))
    assert A.Source(original.source_id,original.sha256,original.payload_json)==original
    with pytest.raises(ValueError,match='SOURCE_INTEGRITY_REFUSED'):
        A.Source(original.source_id,original.sha256,original.payload_json.replace('retained','changed'))
    with pytest.raises(ValueError,match='SOURCE_INTEGRITY_REFUSED'):
        A.Source(original.source_id,'0'*64,original.payload_json)
    # Noncanonical legacy JSON still verifies the canonical logical hash.
    assert A.Source('legacy',original.sha256,'{ "context" : "retained" }').sha256==original.sha256
    monkeypatch.setattr(A,'_SOURCE_DIGEST_LIMIT',64)
    for n in range(100):A.Source.freeze(str(n),dict(text='x'*100,n=n))
    assert A._SOURCE_DIGEST_BYTES<=64 and len(A._SOURCE_DIGESTS)<=64


def test_disabled_provider_makes_zero_attempts_and_leaves_ledger_exact(tmp_path,monkeypatch):
    from tests.test_final_audit_llm_reservation import brain,invoke
    attempts=[]
    b=brain(tmp_path,monkeypatch,client=lambda **kw:attempts.append(kw))
    b._brain_config['enabled']=False
    b._usage_path.write_text('{"retained":"unchanged"}')
    original=b._usage_path.read_bytes()
    assert not b.available
    assert b._admit(dict(messages=[]),False,'research') is None
    assert invoke(b,False) is None and invoke(b,True) is None
    assert not attempts and b._usage_path.read_bytes()==original


def test_current_derivative_window_matches_full_alignment_with_late_revisions(tmp_path):
    import numpy as np
    import pandas as pd
    from trader.data import market_provenance as M
    from trader.data.derivatives import DerivFeed
    from trader.strategy.features_deriv import align
    from trader.strategy.spec_evidence import load_derivs
    at=6_000_000;start=4_500_000
    feed=DerivFeed(tmp_path/'derivs.db',clock_ms=lambda:at)
    events=np.arange(5000)*1000
    raw=pd.DataFrame(dict(ts=pd.to_datetime(events,unit='ms',utc=True),value=np.sin(events)))
    df=M.annotate(raw,instrument_id='binance_usdm:futures:BTCUSDT',source='offline:funding',kind='derivative',received_ms=at)
    # A real retained history with one acquisition clock for each observation,
    # plus late corrections that straddle the first consumer cut.
    df['available_at_ms']=events+500;df['observed_at_ms']=events+500
    feed.save('BTC/USDT','funding',df)
    for event,ready,value in [(0,start+100,90),(start-1000,start+1000,91),(start-2000,start+2000,92)]:
        correction=M.annotate(pd.DataFrame(dict(ts=pd.to_datetime([event],unit='ms',utc=True),value=[value])),
            instrument_id='binance_usdm:futures:BTCUSDT',source='offline:funding',kind='derivative',received_ms=ready)
        feed.save('BTC/USDT','funding',correction)
    full=load_derivs('BTC/USDT',['funding'],feed,as_of_ms=at)['funding']
    window=load_derivs('BTC/USDT',['funding'],feed,as_of_ms=at,window_start_ms=start)['funding']
    cuts=pd.to_datetime(np.arange(start,at,500),unit='ms',utc=True)
    pd.testing.assert_series_equal(align(full,cuts),align(window,cuts))
    assert len(full)==5000 and len(window)<600
    assert set(window.revision_id)<=set(full.revision_id)
    # Full historical/replay selection is still available with its old shape.
    historical=feed.load('BTC/USDT','funding')
    assert len(historical)==5003
    with pytest.raises(ValueError,match='current_window'):
        feed.load('BTC/USDT','funding',window_start_ms=start)


def test_reference_refresh_shared_response_preserves_all_source_rows(tmp_path, monkeypatch):
    from trader.data import ref_sources, market_provenance as mp
    from trader.data.references import RefStore, DAY
    # Same multi-year response shape/scale as the retained 3,232-row source.
    raw = [dict(date=str(1_500_000_000+i*86400),
                totalCirculatingUSD=dict(peggedUSD=10_000_000+i),
                totalCirculating={str(k):i+k for k in range(12)}) for i in range(3232)]
    frame = ref_sources.defillama_stables(get=lambda url:raw)
    cut = int(frame.ts.iloc[-1].timestamp()*1000)+DAY
    expected_hash = mp.digest(raw)
    original = mp.digest
    calls=[]
    def digest(value):
        if value is raw: calls.append(1)
        return original(value)
    monkeypatch.setattr(mp,'digest',digest)
    store=RefStore(tmp_path/'references.db',clock_ms=lambda:cut)
    assert store.save('stables',frame,now_ms=cut)==3232
    assert len(calls)==1
    retained = store.db.execute('SELECT raw_json FROM market_raw_sources WHERE content_hash=?',(expected_hash,)).fetchone()
    assert json.loads(retained[0])==raw
    current=store.load('stables',as_of_ms=cut)
    assert len(current)==3232
    assert current.close.tolist()==frame.close.tolist()
    assert all(json.loads(text)['raw_source_hash']==expected_hash for text in current.raw_json)


def test_derivative_receipt_index_preserves_duplicates_and_source_context(tmp_path):
    from trader.data.derivatives import DerivFeed
    base=1_700_000_000_000
    feed=DerivFeed(tmp_path/'deriv.db',clock_ms=lambda:base+1000*3_600_000)
    raw=[dict(fundingTime=base+i*3_600_000,fundingRate=str(i/10000)) for i in range(1000)]
    raw += [dict(raw[17]),dict(timestamp=base+17*3_600_000,fundingRate='duplicate'),
            dict(timestamp=None,fundingTime=base+17*3_600_000),dict(timestamp=[base])]
    receipt=dict(source='https://fapi.binance.com/fapi/v1/fundingRate',
                 params=dict(symbol='BTCUSDT'),request_id='exact',
                 request_started_ms=base,received_ms=base+1000*3_600_000,raw=raw)
    feed._local.receipts=[receipt]
    frame=feed._parse_funding(raw[:1000])
    for text,event in zip(frame.raw_json,frame.event_time_ms):
        payload=json.loads(text)
        expected=[r for r in raw if r.get('timestamp',r.get('fundingTime'))==event]
        assert payload['receipts'][0]['raw']==expected
        assert payload['receipts'][0]['request_id']=='exact'
    feed.save('BTC/USDT','funding',frame)
    retained=feed.load('BTC/USDT','funding',as_of_ms=receipt['received_ms'])
    assert retained.raw_json.tolist()==frame.raw_json.tolist()


def test_batched_ancestry_matches_sequential_retention_across_retries(tmp_path):
    from trader.data import market_provenance as mp
    import pandas as pd
    import sqlite3
    frame=pd.DataFrame(dict(ts=pd.date_range('2025-01-01',periods=600,freq='h',tz='UTC'),value=[i/1000 for i in range(600)]))
    cut=int(frame.ts.iloc[-1].timestamp()*1000)+3600000
    def records(source,clock,request):
        annotated=mp.annotate(frame,instrument_id='venue:futures:BTC',source=source,
                              kind='derivative',received_ms=clock,request_id=request)
        return mp.prepare(annotated)
    first=records('source-a',cut,'first');new=records('source-a',cut+2000,'new')
    tie=records('source-a',cut+2000,'equal-clock-new-row');old=records('source-a',cut+1000,'older')
    other=records('source-b',cut+3000,'other-identity')
    left=sqlite3.connect(tmp_path/'sequential.db');right=sqlite3.connect(tmp_path/'batched.db')
    for db in (left,right):mp.init(db)
    def sequential(db,rows):
        for original in rows:
            r=dict(original)
            prior=db.execute("SELECT revision_id FROM market_revisions WHERE series_key=? AND event_ms=? AND json_extract(record_json,'$.instrument_id') IS ? AND json_extract(record_json,'$.source') IS ? ORDER BY observed_ms DESC,rowid DESC LIMIT 1",('series',r['event_time_ms'],r['instrument_id'],r['source'])).fetchone()
            r['supersedes']=prior[0] if prior and prior[0]!=r['revision_id'] else None
            db.execute('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)',(r['revision_id'],'series',r['event_time_ms'],r['available_at_ms'],r['observed_at_ms'],mp.encode(r)))
    for rows in (first,tuple(new)+tuple(new[:170])+tuple(first[:100])+tuple(tie)+tuple(old)+tuple(other),first):
        with left:sequential(left,rows)
        with right:mp.append(right,'series',prepared=rows)
        assert left.execute('SELECT rowid,* FROM market_revisions ORDER BY rowid').fetchall()==right.execute('SELECT rowid,* FROM market_revisions ORDER BY rowid').fetchall()
    selected=mp.load(right,'series',as_of_ms=cut+3000,source='source-a',instrument_id='venue:futures:BTC')
    assert len(selected)==600
    assert set(selected.request_id)=={'equal-clock-new-row'}
