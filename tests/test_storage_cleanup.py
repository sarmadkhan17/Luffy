"""Deletion safety: unique/uncertain evidence survives; stale plans fail closed."""
import hashlib
import json
import sqlite3

import pytest

from trader.core import journal_evidence as E
from trader.persistence import storage_cleanup as C
from trader.data.sqlite_tx import save_projection


def fixture_db(tmp_path):
    copies = tmp_path / 'copies'; copies.mkdir()
    path = copies/'runtime-portfolio.db'
    path.touch()
    original = tmp_path/'original.db'; original.write_bytes(b'original')
    (tmp_path/'snapshot.json').write_text(json.dumps({path.name:{'source_files':{str(original):[]}}}))
    db = C.read_copy(path)
    db.executescript(E.BLOB_SCHEMA + '''
    CREATE TABLE state(id INTEGER PRIMARY KEY,inputs_json TEXT,previous_proposal_id TEXT);
    CREATE TABLE checkpoints(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE evaluations(cut_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    ''')
    previous = {'source': 'x'*150000, 'value': 1}
    raw = C.canonical(previous)
    old_marker = E.store(db, raw)  # stale state, superseded but reconstructable
    body = {'schema':'runtime-portfolio-checkpoint.v1','portfolio_cut_id':'cut',
            'event_receipt':{'inputs':{'previous':previous,'current':{'value':2}}}}
    db.execute('INSERT INTO evaluations VALUES (?,?)', ('cut',C.canonical(body)))
    marker = E.store(db, 'unique current evidence')
    db.execute('INSERT INTO state VALUES (1,?,NULL)', (marker,))
    unique = E.store(db, 'unique orphan evidence, refuse to guess')
    db.commit()
    return db, old_marker, marker, unique


def plan(db):
    roots,_ = C.references(db)
    candidates,refusal = C.portfolio_candidates(db,roots)
    return dict(before=C.inventory(db),external_roots=[],delete=candidates),refusal


def test_only_exact_redundant_unreferenced_segments_deleted(tmp_path):
    db,old,current,unique = fixture_db(tmp_path)
    manifest,refusal = plan(db)
    assert manifest['delete']
    assert refusal['unique_or_uncertain_orphans_retained']==1
    before = C.inventory(db)
    C.apply_copy(db,manifest)
    assert E.resolve(db,current)=='unique current evidence'
    assert E.resolve(db,unique)=='unique orphan evidence, refuse to guess'
    after = C.inventory(db)
    for table in ('state','checkpoints','evaluations'):
        assert after['tables'][table]==before['tables'][table]
    with pytest.raises(E.EvidenceError): E.resolve(db,old)
    db.close()


def test_stale_manifest_refused_without_deletion(tmp_path):
    db,*_ = fixture_db(tmp_path); manifest,_ = plan(db)
    db.execute('INSERT INTO checkpoints VALUES (?,?)',('new','new history'));db.commit()
    before = C.inventory(db)
    with pytest.raises(ValueError,match='STALE_MANIFEST'):C.apply_copy(db,manifest)
    assert C.inventory(db)==before
    assert not db.in_transaction
    db.close()


def test_referenced_old_segment_preserved(tmp_path):
    db,old,*_ = fixture_db(tmp_path)
    db.execute('INSERT INTO checkpoints VALUES (?,?)',('reference',old));db.commit()
    manifest,_ = plan(db)
    assert not manifest['delete']
    db.close()


def test_unknown_table_refuses_all_deletion(tmp_path):
    db,*_ = fixture_db(tmp_path)
    db.execute('CREATE TABLE unknown_history(payload TEXT)');db.commit()
    manifest,refusal = plan(db)
    assert not manifest['delete']
    assert refusal['reason']=='UNKNOWN_SCHEMA_RETAINED'
    db.close()


def test_corrupt_reference_refuses_cleanup(tmp_path):
    db,old,*_ = fixture_db(tmp_path)
    db.execute('INSERT INTO checkpoints VALUES (?,?)',('reference',old))
    sha=json.loads(old[len(E.PREFIX):])['chunks'][0]
    db.execute('UPDATE journal_evidence_blobs_v1 SET payload=? WHERE sha256=?',(b'corrupt',sha));db.commit()
    with pytest.raises(E.EvidenceError):plan(db)
    db.close()


def test_apply_rejects_unidentified_database(tmp_path):
    db=sqlite3.connect(tmp_path/'production.db')
    with pytest.raises(FileNotFoundError):C.apply_copy(db,{})
    db.close()


def test_projection_skip_null_and_correction():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE refs(key TEXT,ts INTEGER,value REAL, PRIMARY KEY(key,ts))')
    args=('refs',('key','ts','value'),('key','ts'))
    save_projection(db,*args,[('a',1,None)])
    before=db.total_changes
    save_projection(db,*args,[('a',1,None)])
    assert db.total_changes==before
    save_projection(db,*args,[('a',1,2.)])
    assert db.total_changes==before+1
    save_projection(db,*args,[('a',1,None)])
    assert db.execute('SELECT value FROM refs').fetchone()==(None,)


def test_receipt_retry_skips_encoding_but_new_acquisition_survives(tmp_path,monkeypatch):
    import pandas as pd
    from trader.data import market_provenance as mp
    db=sqlite3.connect(tmp_path/'receipts.db');mp.init(db)
    frame=pd.DataFrame({'ts':pd.to_datetime([1000],unit='ms',utc=True),'value':[2.]})
    first=mp.annotate(frame,instrument_id='binance_usdm:futures:BTCUSDT',source='test',kind='derivative',received_ms=2000,request_id='one')
    prepared=mp.prepare(first)
    mp.append(db,'derivative:BTC/USDT:oi',prepared=prepared);db.commit()
    calls=[];original=mp.encode
    monkeypatch.setattr(mp,'encode',lambda obj:(calls.append(obj),original(obj))[1])
    before=db.total_changes
    mp.append(db,'derivative:BTC/USDT:oi',prepared=prepared);db.commit()
    assert not calls and db.total_changes==before
    second=mp.annotate(frame,instrument_id='binance_usdm:futures:BTCUSDT',source='test',kind='derivative',received_ms=3000,request_id='two')
    mp.append(db,'derivative:BTC/USDT:oi',prepared=mp.prepare(second));db.commit()
    assert db.execute('SELECT count(*) FROM market_revisions').fetchone()[0]==2
    assert mp.load(db,'derivative:BTC/USDT:oi',as_of_ms=2000).iloc[0]['request_id']=='one'
    assert mp.load(db,'derivative:BTC/USDT:oi',as_of_ms=3000).iloc[0]['request_id']=='two'
    db.close()


def test_duplicate_members_retrieve_once_per_pass(tmp_path,monkeypatch):
    import pandas as pd
    from trader.data.derivatives import DerivFeed
    feed=DerivFeed(db_path=tmp_path/'derivs.db',clock_ms=lambda:2000)
    calls=[]
    def fetch(symbol):
        calls.append(symbol)
        return pd.DataFrame({'ts':pd.to_datetime([1000],unit='ms',utc=True),'value':[2.]})
    monkeypatch.setattr(feed,'_SERIES_FETCHERS',[('oi','open_interest')])
    monkeypatch.setattr(feed,'open_interest',fetch)
    feed.record_all(['BTC/USDT','BTC/USDT'],delay=0)
    assert calls==['BTC/USDT']
    feed.record_all(['BTC/USDT'],delay=0)
    assert calls==['BTC/USDT','BTC/USDT']
    assert feed.db.execute('SELECT count(*) FROM market_revisions').fetchone()[0]==2
    feed.db.close()


def test_small_candle_cache_does_not_hide_larger_request(tmp_path):
    from trader.data.feed import DataFeed
    from tests.test_feed_pagination import FakeEx
    ex=FakeEx(total=3000)
    feed=DataFeed(exchange=ex,db_path=tmp_path/'candles.db')
    assert len(feed.fetch_ohlcv('BTC/USDT','15m',limit=400))==400
    assert len(feed.fetch_ohlcv('BTC/USDT','15m',limit=1500))==1500
    before=len(ex.calls)
    assert len(feed.fetch_ohlcv('BTC/USDT','15m',limit=1500))==1500
    assert len(ex.calls)==before
    feed.db.close()


def test_deletion_trigger_refused(tmp_path):
    db,*_ = fixture_db(tmp_path)
    db.execute('''CREATE TRIGGER surprise BEFORE DELETE ON journal_evidence_blobs_v1
        BEGIN DELETE FROM checkpoints; END''');db.commit()
    before=C.inventory(db)
    with pytest.raises(ValueError,match='BLOB_TRIGGER_REFUSED'):plan(db)
    assert C.inventory(db)==before
    db.close()


def test_snapshot_preserves_source_and_refuses_nested_destination(tmp_path):
    source=tmp_path/'source';source.mkdir()
    db=sqlite3.connect(source/'test.db');db.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,payload TEXT)')
    db.execute('INSERT INTO evidence VALUES (1,?)',('immutable',));db.commit();db.close()
    before=(source/'test.db').read_bytes()
    copies=C.snapshot(source,tmp_path/'isolated')
    assert (source/'test.db').read_bytes()==before
    assert (copies/'test.db').read_bytes()==before
    with pytest.raises(ValueError,match='DESTINATION_WITHIN_SOURCE'):C.snapshot(source,source/'nested')
    with pytest.raises(FileExistsError):C.snapshot(source,tmp_path/'isolated')


def test_readonly_inventory_refuses_pending_wal(tmp_path):
    db,*_ = fixture_db(tmp_path)
    path=tmp_path/'copies/runtime-portfolio.db';db.close()
    before=path.read_bytes()
    scan=C.read_normalized_copy(path)
    assert scan.execute('SELECT count(*) FROM evaluations').fetchone()==(1,)
    with pytest.raises(sqlite3.OperationalError):scan.execute('DELETE FROM evaluations')
    scan.close()
    assert path.read_bytes()==before
    (path.parent/(path.name+'-wal')).write_bytes(b'pending WAL')
    with pytest.raises(ValueError,match='PENDING_SIDECAR'):C.read_normalized_copy(path)


def test_hardlink_cannot_be_an_isolated_target(tmp_path):
    db,*_ = fixture_db(tmp_path);db.close()
    path=tmp_path/'copies/runtime-portfolio.db'
    (tmp_path/'alias.db').hardlink_to(path)
    with pytest.raises(ValueError,match='HARDLINK_DATABASE_REFUSED'):C.read_copy(path)
