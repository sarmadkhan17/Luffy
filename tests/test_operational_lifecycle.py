"""Lifecycle boundaries: preserve logical identity, refuse unknowns, rollback."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from trader.core import journal_evidence as E
from trader.data import market_provenance as M
from trader.persistence import storage_cleanup as C
from trader.research.ledger import SCHEMA, _slim


def receipt(clock=30_000_000, event=0):
    frame = pd.DataFrame(dict(ts=pd.to_datetime([event],unit='ms',utc=True),
                              open=[1.], high=[2.], low=[1.], close=[2.], volume=[5.]))
    return M.prepare(M.annotate(frame,instrument_id='binance:futures:BTCUSDT',
        source='source:'+('immutable-descriptor'*80),kind='candle',timeframe='4h',
        received_ms=clock,request_id=str(clock),raw=[dict(context='same-source'*80)]))[0]


def market_db():
    db=sqlite3.connect(':memory:');M.init(db);db.commit()
    return db


def legacy(db, records):
    db.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',
        [(r['revision_id'],'key',r['event_time_ms'],r['available_at_ms'],r['observed_at_ms'],M.encode(r)) for r in records])
    db.commit()


def test_distinct_acquisitions_share_bytes_and_keep_replay_and_lineage():
    db=market_db(); records=[receipt(),receipt(30_001_000)]
    with db:M.append(db,'key',prepared=records)
    stored=db.execute('SELECT record_json FROM market_revisions ORDER BY rowid').fetchall()
    assert all(M.TEXT_REF in r[0] for r in stored)
    assert db.execute('SELECT count(*) FROM market_raw_sources').fetchone()[0]==1
    assert M.read_record(db,stored[0][0])==records[0]
    second=M.read_record(db,stored[1][0]);assert second['supersedes']==records[0]['revision_id']
    first=M.load(db,'key',as_of_ms=30_000_000,source=records[0]['source'])
    last=M.load(db,'key',as_of_ms=30_001_000,source=records[0]['source'])
    assert first.iloc[0]['revision_id']==records[0]['revision_id']
    assert last.iloc[0]['revision_id']==records[1]['revision_id']
    retrospective=M.retrospective_load(db,'key',cut_ms=40_000_000,tf='4h')
    assert retrospective.iloc[0]['revision_id']==records[1]['revision_id']
    lineage=dict(schema_version=M.SCHEMA,as_of_ms=30_001_000,instrument_id=records[0]['instrument_id'],frames={'4h':[r['revision_id'] for r in records]})
    assert set(M.resolve_lineage([db],lineage))=={r['revision_id'] for r in records}
    before=db.total_changes
    with db:M.append(db,'key',prepared=records)
    assert db.total_changes==before


def test_backfill_dry_run_rollback_idempotence_and_unknown_is_local():
    db=market_db(); records=[receipt(),receipt(30_001_000)];legacy(db,records)
    db.execute("INSERT INTO market_revisions VALUES ('unknown','key',1,1,1,'{}')");db.commit()
    original=db.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()
    changes=db.total_changes
    plan=C.market_batch(db);assert len(plan['records'])==2 and len(plan['refused'])==1
    assert db.total_changes==changes
    db.execute('BEGIN IMMEDIATE');C.market_batch(db,apply=True);db.rollback()
    assert db.execute('SELECT * FROM market_revisions ORDER BY rowid').fetchall()==original
    assert db.execute('SELECT count(*) FROM market_raw_sources').fetchone()[0]==0
    with db:
        db.execute('BEGIN IMMEDIATE');applied=C.market_batch(db,apply=True)
    assert applied['records']==plan['records']
    assert C.market_batch(db)['records']==[]
    assert [M.read_record(db,r[0]) for r in db.execute("SELECT record_json FROM market_revisions WHERE revision_id!='unknown' ORDER BY rowid")]==records
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_missing_or_corrupt_shared_source_refuses_every_reader():
    db=market_db()
    with db:M.append(db,'key',prepared=[receipt()])
    db.execute("UPDATE market_raw_sources SET raw_json='corrupt'");db.commit()
    with pytest.raises(ValueError,match='missing_or_corrupt'):M.load(db,'key',as_of_ms=30_000_000)


def test_unknown_market_schema_never_writes():
    db=sqlite3.connect(':memory:');db.execute('CREATE TABLE market_revisions(id,payload)')
    assert C.market_batch(db)['refused']=='UNKNOWN_MARKET_SCHEMA'


def test_portfolio_anchors_share_complete_state_chunks_without_changing_bytes():
    db=sqlite3.connect(':memory:');db.executescript(E.BLOB_SCHEMA)
    value=C.canonical(dict(bars=[dict(close=i, source='immutable') for i in range(6000)]))
    state=E.store(db,value)
    before=db.execute('SELECT count(*) FROM journal_evidence_blobs_v1').fetchone()[0]
    text='{"current":'+value+',"receipt":"one"}'
    marker=E.store(db,E.anchored(text,[value]))
    assert E.resolve(db,marker)==text and E.resolve(db,state)==value
    a=set(json.loads(state[len(E.PREFIX):])['chunks'])
    b=set(json.loads(marker[len(E.PREFIX):])['chunks'])
    assert a<=b
    assert db.execute('SELECT count(*) FROM journal_evidence_blobs_v1').fetchone()[0]<=before+2


def research_db():
    db=sqlite3.connect(':memory:');db.executescript(SCHEMA)
    for h,status in [('failed','error'),('passed','scored'),('referenced','error'),('active','scored'),('unknown','scored')]:
        value=dict(hash=h,portfolio=dict(total_pct=2.,max_dd_pct=1.,order=['unused']*100),status=status,alpha_t=.01)
        db.execute('INSERT INTO research_combos(hash,tf,geo,result,created_at,status,k) VALUES (?,?,?,?,?,?,1)',
                   (h,'4h','active' if h=='active' else 'fixed',json.dumps(value),'2026-01-01',status))
    db.execute("UPDATE research_combos SET result='not-json' WHERE hash='unknown'")
    db.execute("INSERT INTO research_batches(tf,geo,started,finished,ok) VALUES ('4h','fixed','2025','2027',1)")
    db.execute("INSERT INTO research_batches(tf,geo,started) VALUES ('4h','active','2025')")
    db.execute("INSERT INTO research_tests(hash,seq,alpha_t) VALUES ('referenced',1,.01)")
    db.commit();return db


def test_research_failed_and_successful_identity_budget_and_active_work_survive():
    db=research_db();before={h:json.loads(r) for h,r in db.execute("SELECT hash,result FROM research_combos WHERE hash!='unknown'")}
    plan=C.research_batch(db)
    assert {r['hash'] for r in plan['records']}=={'failed','passed'}
    assert {r['hash'] for r in plan['refused']}=={'referenced','active','unknown'}
    db.execute('BEGIN IMMEDIATE');C.research_batch(db,apply=True);db.rollback()
    assert json.loads(db.execute("SELECT result FROM research_combos WHERE hash='failed'").fetchone()[0])==before['failed']
    with db:
        db.execute('BEGIN IMMEDIATE');C.research_batch(db,apply=True)
    for h in ('failed','passed'):
        assert json.loads(db.execute('SELECT result FROM research_combos WHERE hash=?',(h,)).fetchone()[0])==_slim(before[h])
    assert db.execute('SELECT seq,alpha_t FROM research_tests').fetchall()==[(1,.01)]
    assert C.research_batch(db)['records']==[]
    assert db.execute('SELECT count(*) FROM research_combos').fetchone()[0]==5


def test_actual_file_dry_run_has_no_source_writes_or_sidecar_creation(tmp_path):
    data=tmp_path/'data';data.mkdir();path=data/'candles.db'
    db=sqlite3.connect(path);M.init(db);db.commit();legacy(db,[receipt(),receipt(30_001_000)]);db.close()
    before=hashlib.sha256(path.read_bytes()).hexdigest();stat=path.stat()
    output=tmp_path/'reports'/'report.json'
    with pytest.raises(ValueError,match='READONLY_FILESYSTEM'):
        C.operational_dry_run(data,output,windows=1,limit=128)
    subprocess.run([sys.executable,'-B','scripts/storage_cleanup.py','--operational-dry-run',str(data),
                    '--report',str(output),'--windows','1'],check=True)
    report=json.loads(output.read_text())
    assert report['read_only'] and report['physical_storage_savings_bytes']==0
    assert len(report['approval_records'])==2
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert path.stat().st_mtime_ns==stat.st_mtime_ns
    assert list(data.iterdir())==[path]


def test_predictive_fingerprint_survives_physical_market_compaction(monkeypatch):
    from trader.research import predictive_experiment as P
    from trader.data.feed import DataFeed
    db=market_db()
    db.execute('CREATE TABLE candles(symbol,tf,ts,open,high,low,close,volume,taker_buy)')
    records=[receipt(event=0),receipt(event=14_400_000)]
    legacy(db,records)
    formatted=json.dumps(records[0],indent=2)
    db.execute('UPDATE market_revisions SET record_json=? WHERE rowid=1',(formatted,))
    assert M.logical_record_json(db,formatted)==formatted
    db.execute('UPDATE market_revisions SET series_key=?',(DataFeed._series_key('BTC/USDT','4h'),))
    for event in (0,14_400_000):
        db.execute("INSERT INTO candles VALUES ('BTC/USDT','4h',?,1,2,1,2,5,NULL)",(event,))
    db.commit()
    monkeypatch.setattr(P,'DISCOVERY',['BTC/USDT']);monkeypatch.setattr(P,'HELDOUT',[])
    split=dict(start_ms=0,cut_ms=14_400_000,end_ms=28_800_000)
    original=P.data_provenance(db,split)
    with db:
        db.execute('BEGIN IMMEDIATE');C.market_batch(db,apply=True)
    assert P.data_provenance(db,split)==original


def test_existing_exact_raw_content_is_reused_without_losing_identity():
    db=market_db();r=receipt()
    sha=M.text_reference(r['raw_json'])[len(M.TEXT_REF):]
    db.execute('INSERT INTO market_raw_sources VALUES (?,?,?)',(sha,'existing-immutable-body',r['raw_json']))
    with db:M.append(db,'key',prepared=[r])
    raw=db.execute('SELECT record_json FROM market_revisions').fetchone()[0]
    assert json.loads(raw)['raw_json']==M.TEXT_REF+sha
    assert M.read_record(db,raw)==r


def test_bounded_copy_rehearsal_never_opens_source_writable(tmp_path):
    source=tmp_path/'source';source.mkdir();src=source/'candles.db'
    db=sqlite3.connect(src);M.init(db);db.commit();legacy(db,[receipt(),receipt(30_001_000)]);db.close()
    digest=hashlib.sha256(src.read_bytes()).hexdigest()
    copies=tmp_path/'copies';copies.mkdir();dest=copies/'candles.db';dest.write_bytes(src.read_bytes())
    (tmp_path/'snapshot.json').write_text(json.dumps({'candles.db':{'source_files':{str(src):[]}}}))
    result=C.apply_lifecycle_copy(dest,'market',limit=1)
    assert result['scanned']==1 and len(result['records'])==1
    assert not result['production_apply_authorized']
    assert C.apply_lifecycle_copy(dest,'market',limit=1)['records']==[]
    assert len(C.apply_lifecycle_copy(dest,'market',after=1,limit=1)['records'])==1
    assert hashlib.sha256(src.read_bytes()).hexdigest()==digest
    with pytest.raises((ValueError,FileNotFoundError)):
        C.apply_lifecycle_copy(src,'market')


def test_unknown_trigger_cannot_mutate_financial_records_during_backfill():
    db=market_db();legacy(db,[receipt()])
    db.executescript("CREATE TABLE financial_records(value); INSERT INTO financial_records VALUES (7);"
                     "CREATE TRIGGER surprise AFTER UPDATE ON market_revisions BEGIN UPDATE financial_records SET value=0; END;")
    assert C.market_batch(db)['refused']=='UNKNOWN_MARKET_TRIGGER'
    assert db.execute('SELECT * FROM financial_records').fetchall()==[(7,)]
    # This refusal has no global switch: independently proven R&D can continue.
    research=research_db()
    assert len(C.research_batch(research)['records'])==2


def test_existing_research_owner_enforces_approved_cursor_and_restart(tmp_path):
    from trader.core.journal import Journal
    from trader.research.ledger import Ledger
    path=tmp_path/'luffy.db';j=Journal(path);led=Ledger(j);led.ensure()
    original=dict(hash='failed',portfolio=dict(total_pct=-2.,order=['unused']*20),status='error')
    with j._tx() as db:
        db.execute("INSERT INTO research_combos(hash,tf,geo,k,result,created_at,status) VALUES ('failed','4h','fixed',1,?,'2025','error')",(json.dumps(original),))
    first=led.start_batch('4h','fixed','test',1);led.finish_batch(first,False,0,error='failed')
    assert led.result('failed')==original # no implicit historical production purge
    j.kv_set('lifecycle.research.backfill.approved.v1','1')
    second=led.start_batch('4h','fixed','test',1);led.finish_batch(second,False,0,error='failed')
    assert led.result('failed')==_slim(original)
    assert j.kv_get('lifecycle.research.rowid.v1')=='1'
    restarted=Ledger(Journal(path));restarted.ensure()
    assert restarted.has('failed') and restarted.result('failed')==_slim(original)
    third=restarted.start_batch('4h','fixed','test',1);restarted.finish_batch(third,True,0)
    assert restarted.journal.kv_get('lifecycle.research.rowid.v1')=='0'
    with restarted.journal._tx() as db:
        db.execute("CREATE TRIGGER unknown_result_update BEFORE UPDATE ON research_combos BEGIN SELECT RAISE(ABORT,'unknown'); END")
    fourth=restarted.start_batch('4h','fixed','test',1);restarted.finish_batch(fourth,False,0)
    assert restarted.journal.query('SELECT finished FROM research_batches WHERE id=?',(fourth,))[0]['finished']
    restarted.journal.kv_set('lifecycle.research.rowid.v1','uncertain')
    fifth=restarted.start_batch('4h','fixed','test',1);restarted.finish_batch(fifth,False,0)
    assert restarted.journal.query('SELECT finished FROM research_batches WHERE id=?',(fifth,))[0]['finished']
