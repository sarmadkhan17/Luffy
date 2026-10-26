"""The publication cut stays identical while indexed seeks avoid scanning unrelated history."""
import sqlite3,time
from trader.research.closure.publish import _candle_tails


def test_text_cache_eviction_keeps_all_evidence_and_rechecks_reloaded_text():
    import json,sys
    import pytest
    from trader.data import market_provenance as mp
    from trader.research.closure.publish import Bounds,_copy_revisions
    src=sqlite3.connect(':memory:');dst=sqlite3.connect(':memory:')
    mp.init(src);mp.init(dst)
    texts=[str(i)+'x'*2048 for i in range(4)]
    markers=[mp.text_reference(text) for text in texts]
    for text,marker in zip(texts,markers):
        src.execute('INSERT INTO market_raw_sources VALUES (?,?,?)',(marker[len(mp.TEXT_REF):],'immutable-text.v1',text))
    for index in range(8):
        src.execute('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',
            (str(index),'reference:fixture',index,index,index,json.dumps({'source':markers[index%4],'raw_json':'{}'})))
    bound=Bounds(100,100000,None);bound.text_cache_limit=4300
    _copy_revisions(src,dst,'reference:fixture',0,7,None,{'revisions':0},bound)
    actual=[mp.read_record(dst,r[0])['source'] for r in dst.execute('SELECT record_json FROM market_revisions ORDER BY event_ms')]
    assert actual==texts*2
    assert dst.execute('SELECT COUNT(*) FROM market_raw_sources').fetchone()[0]==4
    assert len(bound.raw_texts)==len(bound.checked_texts)==2
    assert bound.text_cache_bytes==sum(sys.getsizeof(text) for text in bound.raw_texts.values())<=bound.text_cache_limit
    assert markers[0][len(mp.TEXT_REF):] not in bound.raw_texts
    # An evicted body must be read and checked again, not trusted merely
    # because a different source connection/cut once contained this key.
    src.execute('UPDATE market_raw_sources SET raw_json=? WHERE content_hash=?',('tampered',markers[0][len(mp.TEXT_REF):]))
    with pytest.raises(ValueError,match='missing_or_corrupt'):
        _copy_revisions(src,dst,'reference:fixture',0,0,None,{'revisions':0},bound)
    src.close();dst.close()


def test_revision_transport_streams_batches_and_enforces_shared_limit():
    import json
    import pytest
    from trader.data import market_provenance as mp
    from trader.research.closure import ClosureRefused
    from trader.research.closure.publish import Bounds,_copy_revisions
    # Physical transport fixture only, not qualified market evidence.
    src=sqlite3.connect(':memory:');dst=sqlite3.connect(':memory:')
    mp.init(src);mp.init(dst)
    src.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',[
        (str(i),'reference:fixture',i,i,i,json.dumps({'raw_json':'{}'})) for i in range(600)])
    reads=[0];batches=[]
    class TrackedBounds(Bounds):
        def iterate(self,*a,**kw):
            for row in super().iterate(*a,**kw):
                reads[0]+=1
                yield row
        def take(self,*a,**kw):
            pytest.fail('revision history must not materialize through take()')
    class Destination:
        def executemany(self,sql,rows):
            batches.append((reads[0],len(rows)))
            return dst.executemany(sql,rows)
    count={'revisions':0}
    _copy_revisions(src,Destination(),'reference:fixture',0,599,None,count,TrackedBounds(600,1000000,None))
    assert batches==[(256,256),(512,256),(600,88)]
    assert count=={'revisions':600}
    assert dst.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()==src.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()
    with pytest.raises(ClosureRefused,match='rows_exceed_bound'):
        _copy_revisions(src,Destination(),'reference:fixture',0,599,None,{'revisions':0},TrackedBounds(599,1000000,None))
    src.close();dst.close()


def test_large_physical_records_flush_by_bytes_before_reaching_row_limit():
    import json
    from trader.data import market_provenance as mp
    from trader.research.closure.publish import Bounds,_copy_revisions
    src=sqlite3.connect(':memory:');dst=sqlite3.connect(':memory:')
    mp.init(src);mp.init(dst)
    body=json.dumps({'raw_json':'{}','transport_fixture':'x'*700000})
    src.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',
        ((str(i),'reference:fixture',i,i,i,body) for i in range(3)))
    batches=[]
    class Destination:
        def executemany(self,sql,rows):
            batches.append(len(rows));return dst.executemany(sql,rows)
    counter={'revisions':0}
    _copy_revisions(src,Destination(),'reference:fixture',0,2,None,counter,Bounds(10,4000000,None))
    assert batches==[1,1,1] and counter=={'revisions':3}
    assert dst.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()==src.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()
    src.close();dst.close()


def test_missing_legacy_body_keys_use_one_query_per_batch_and_preserve_receipts():
    import json
    from trader.data import market_provenance as mp
    from trader.research.closure.publish import Bounds,_copy_revisions
    src=sqlite3.connect(':memory:');dst=sqlite3.connect(':memory:')
    mp.init(src);mp.init(dst)
    src.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',
        ((str(i),'reference:fixture',i,i,i,json.dumps({'content_hash':f'{i:064x}','raw_json':'{}'})) for i in range(600)))
    queries=[];src.set_trace_callback(lambda sql:queries.append(sql) if 'FROM market_raw_sources' in sql else None)
    counter={'revisions':0}
    _copy_revisions(src,dst,'reference:fixture',0,599,None,counter,Bounds(1000,1000000,None))
    assert len(queries)==3 and all('content_hash IN (' in sql for sql in queries)
    assert counter=={'revisions':600}
    assert dst.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()==src.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()
    src.close();dst.close()


def test_legacy_content_hash_body_is_copied_even_without_a_text_marker():
    import hashlib,json
    from trader.data import market_provenance as mp
    from trader.research.closure.publish import Bounds,_copy_revisions
    src=sqlite3.connect(':memory:');dst=sqlite3.connect(':memory:')
    mp.init(src);mp.init(dst)
    body='retained legacy raw body';key=hashlib.sha256(body.encode()).hexdigest()
    src.execute('INSERT INTO market_raw_sources VALUES (?,?,?)',(key,'legacy',body))
    src.execute('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',
        ('r','reference:fixture',1,1,1,json.dumps({'content_hash':key,'raw_json':'{}'})))
    _copy_revisions(src,dst,'reference:fixture',0,1,None,{'revisions':0},Bounds(10,10000,None))
    assert dst.execute('SELECT * FROM market_raw_sources').fetchall()==src.execute('SELECT * FROM market_raw_sources').fetchall()
    src.close();dst.close()


def test_indexed_tails_preserve_global_cut_and_declared_universe():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,PRIMARY KEY(symbol,tf,ts))')
    db.executemany('INSERT INTO candles VALUES (?,?,?)',[(s,t,i) for s in ('A','B','C') for t in ('15m','4h') for i in range(10000)])
    work=[0]
    def count():work[0]+=1;return 0
    db.set_progress_handler(count,100)
    names,tails=_candle_tails(db,'15m',9997,['A'],time.monotonic()+2)
    assert names==['A'] and tails=={'A':9997,'B':9997,'C':9997}
    assert work[0]<30 # fewer than 3000 VM operations, despite 60000 rows
    db.set_progress_handler(None,0)
    db.execute("INSERT INTO candles VALUES ('C','15m',10003)")
    names,tails=_candle_tails(db,'15m',10003,['A'],time.monotonic()+2)
    assert max(tails.values())==db.execute("SELECT MAX(ts) FROM candles WHERE tf='15m' AND ts<=10003").fetchone()[0]
    assert names==['A'] # global cut is retained, no extra symbol's history is copied


def test_shared_text_is_verified_and_delivered_to_each_destination(tmp_path):
    import pytest
    from trader.data import market_provenance as mp
    from trader.research.closure.publish import publish_exact_cut
    step=14400000;ts=step*100
    c,d=tmp_path/'c.db',tmp_path/'d.db'
    text='same retained venue description '*100
    # Tiny physical transport fixture: the same immutable text appears in both
    # independent sources. This is not a qualified market receipt or a strategy.
    for path,key in ((c,'candle:A:4h'),(d,'derivative:A:funding')):
        with sqlite3.connect(path) as db:
            if path==c:
                db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,tf,ts))')
                db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)',('A','4h',ts,1,1,1,1,1))
            else:
                db.execute('CREATE TABLE derivs(symbol TEXT,series TEXT,ts INTEGER,value REAL)')
                db.execute('INSERT INTO derivs VALUES (?,?,?,?)',('A','funding',ts,.001))
            mp.init(db)
            raw=mp.store_record(db,{'source':text,'raw_json':'{}','content_hash':'absent'})
            db.execute('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',('r',key,ts,ts+step,ts+step,raw))
    dst,ddst=tmp_path/'out'/'c.db',tmp_path/'out'/'d.db'
    publish_exact_cut(c,dst,tf='4h',window_bars=4,deriv_src=d,dst_derivs=ddst,now_ms=ts+2*step)
    for path in (dst,ddst):
        with sqlite3.connect(path) as db:
            assert mp.read_record(db,db.execute('SELECT record_json FROM market_revisions').fetchone()[0])['source']==text
    with sqlite3.connect(d) as db:db.execute("UPDATE market_raw_sources SET raw_json='corrupt'")
    dst2,ddst2=tmp_path/'bad'/'c.db',tmp_path/'bad'/'d.db'
    with pytest.raises(ValueError,match='missing_or_corrupt'):
        publish_exact_cut(c,dst2,tf='4h',window_bars=4,deriv_src=d,dst_derivs=ddst2,now_ms=ts+2*step)
    assert not dst2.exists() and not ddst2.exists()


def test_fit_prefilter_preserves_every_endpoint_eligible_symbol(tmp_path):
    from trader.research.closure.publish import publish_exact_cut
    from trader.research.closure.manifest import _coverage
    step=14400000;source=tmp_path/'source.db'
    spans={'A':(28,99),'B':(33,99),'C':(34,99),'D':(10,66),'E':(10,65)}
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,tf,ts))')
        for sym,(lo,hi) in spans.items():
            db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)',[(sym,'4h',v*step,1,1,1,1,1) for v in (lo,hi)])
    out=publish_exact_cut(source,tmp_path/'published.db',tf='4h',window_bars=90,now_ms=101*step,
                         coverage_filter=dict(fit_bars=40,reserve_bars=30,max_gap_bars=3))
    expected=[]
    for sym,(lo,hi) in spans.items():
        inv=dict(stored_first_ms=lo*step,stored_last_ms=hi*step,stored_bars=2,stored_gap_count=1)
        if _coverage(inv,30*step,69*step,step,3,1,'RETROSPECTIVE') is None:expected.append(sym)
    assert out['symbols']==expected==['A','B','D']
    assert out['coverage_filter']['considered']==5 and out['coverage_filter']['excluded_symbols']==['C','E']
    assert out['as_of_ms']==100*step # filtering does not move the shared source cut
