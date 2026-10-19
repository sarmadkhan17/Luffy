"""Offline owner completion, lossless representations and bounded crash recovery."""
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import closing

import pytest
from trader.core import journal_evidence as E
from trader.learning import capture as C, producers as P
from trader.persistence import storage_cleanup as S
from trader.portfolio.runtime import Consumer
from tests.test_historical_outcome_capture import prospective, finish, chain
from tests.test_storage_cleanup import fixture_db
from tests.test_operational_lifecycle import receipt, market_db, legacy


def test_learning_completion_and_restart_keep_exact_replay(prospective):
    j,d,_=prospective
    oid=finish(j,d,observation={'measurement':'TEST_ONLY','values':list(range(3000))})
    before=chain(j,oid)
    with j._tx() as db: expected=P.materialize(db,oid)
    db=j._conn()
    rows={table:list(C.read(db,'SELECT * FROM '+table)) for table in C.TABLES}
    assert any(db.execute('SELECT 1 FROM '+table+' WHERE payload LIKE ? LIMIT 1',(E.PREFIX+'%',)).fetchone() for table in C.TABLES)
    assert chain(j,oid)==before
    path=j.db_path if hasattr(j,'db_path') else Path(db.execute('PRAGMA database_list').fetchone()[2])
    from trader.core.journal import Journal
    restarted=Journal(path)
    with restarted._tx() as fresh: assert P.materialize(fresh,oid)==expected
    assert {t:list(C.read(restarted._conn(),'SELECT * FROM '+t)) for t in C.TABLES}==rows
    assert chain(restarted,oid)==before


def test_learning_legacy_inline_retired_only_with_exact_equality(prospective):
    j,d,_=prospective;oid=finish(j,d)
    with j._tx() as db: P.materialize(db,oid)
    db=j._conn();expected=chain(j,oid)
    table='learning_registrations'
    key,text=db.execute('SELECT event_key,payload FROM '+table).fetchone()
    logical=E.resolve(db,text)
    with db: db.execute('UPDATE '+table+' SET payload=? WHERE event_key=?',(logical,key))
    db.execute('BEGIN IMMEDIATE');answer=C.compact_completed(db,oid);db.rollback()
    assert answer['compacted']>=1
    assert db.execute('SELECT payload FROM '+table).fetchone()[0]==logical
    with db:
        db.execute('BEGIN IMMEDIATE');C.compact_completed(db,oid)
    assert E.resolve(db,db.execute('SELECT payload FROM '+table).fetchone()[0])==logical
    with pytest.raises(sqlite3.IntegrityError):
        with db: db.execute('UPDATE '+table+" SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError):
        with db: db.execute('UPDATE '+table+" SET event_key='changed'")
    assert chain(j,oid)==expected
    with db:
        db.execute('CREATE TRIGGER unknown_learning AFTER UPDATE ON '+table+' BEGIN SELECT 1; END')
        # Even an exactly equivalent legacy payload remains untouched with an unknown trigger.
        db.execute('UPDATE '+table+' SET payload=?',(logical,))
    with db:
        db.execute('BEGIN IMMEDIATE');out=C.compact_completed(db,oid)
    assert table+':unknown_trigger' in out['refused']
    assert db.execute('SELECT payload FROM '+table).fetchone()[0]==logical


def test_learning_stages_share_source_bytes_and_immutable_ids():
    db=E.install(sqlite3.connect(':memory:'));C.ensure(db)
    raw=C.L.canonical({'candles':[{'close':i,'source':'same'} for i in range(6000)]})
    with db:
        C.insert(db,'learning_source_blobs','sha256','one',('one',raw))
        first=db.execute('SELECT count(*) FROM journal_evidence_blobs_v1').fetchone()[0]
        C.insert(db,'learning_source_blobs','sha256','two',('two',raw))
    assert db.execute('SELECT count(*) FROM journal_evidence_blobs_v1').fetchone()[0]==first
    assert [r[0] for r in C.read(db,'SELECT payload FROM learning_source_blobs')]==[raw,raw]
    with db: assert C.insert(db,'learning_source_blobs','sha256','one',('one',raw))=='DUPLICATE'


def test_market_historical_sweep_automatic_bounded_restart(tmp_path):
    from trader.data import market_provenance as M
    path=tmp_path/'market.db';db=sqlite3.connect(path);M.init(db);db.commit()
    records=[receipt(30_000_000+i*1000,event=i) for i in range(20)]
    legacy(db,records)
    # A malformed older receipt refuses locally; unrelated valid receipts progress.
    db.execute("UPDATE market_revisions SET record_json='{}' WHERE rowid=2");db.commit()
    for i in range(3):
        with db: M.append(db,'key',prepared=[receipt(40_000_000+i*1000,event=100+i)])
        db.close();db=sqlite3.connect(path)
    assert db.execute('SELECT count(*) FROM market_revisions').fetchone()[0]==23
    for i,r in enumerate(records):
        stored=db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?',(r['revision_id'],)).fetchone()[0]
        if i==1: assert stored=='{}'
        else: assert M.TEXT_REF in stored and M.read_record(db,stored)==r
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_portfolio_cleanup_restart_and_references(tmp_path):
    db,old,current,unique=fixture_db(tmp_path)
    path=Path(db.execute('PRAGMA database_list').fetchone()[2]);db.close()
    owner=Consumer(path)
    assert owner.lifecycle['deleted']>=1
    with closing(owner._db()) as db:
        assert E.resolve(db,current)=='unique current evidence'
        assert E.resolve(db,unique)=='unique orphan evidence, refuse to guess'
        assert db.execute('SELECT count(*) FROM evaluations').fetchone()[0]==1
        with pytest.raises(E.EvidenceError): E.resolve(db,old)
    again=Consumer(path);assert again.lifecycle['deleted']==0


def test_portfolio_reference_unknown_and_budget_refusals(tmp_path):
    db,old,*_=fixture_db(tmp_path);db.execute(E.REPRESENTATION_SCHEMA)
    with db: db.execute('INSERT INTO checkpoints VALUES (?,?)',('active-reference',old))
    path=Path(db.execute('PRAGMA database_list').fetchone()[2]);db.close()
    owner=Consumer(path);db=owner._db()
    assert owner.lifecycle['deleted']==0 and E.resolve(db,old)
    with db:
        db.execute('BEGIN IMMEDIATE');out=S.portfolio_completion(db,max_rows=1)
    assert out['deleted']==0
    assert out.get('scanned',0)<=1
    with db: db.execute('CREATE TABLE open_positions(payload TEXT)')
    with db:
        db.execute('BEGIN IMMEDIATE');out=S.portfolio_completion(db)
    assert out['refused']=='unknown_portfolio_schema'
    assert E.resolve(db,old)


def test_portfolio_scan_resumes_and_sees_references_appended_after_scan(tmp_path,monkeypatch):
    db,old,*_=fixture_db(tmp_path)
    path=Path(db.execute('PRAGMA database_list').fetchone()[2]);db.close()
    with monkeypatch.context() as mp:
        mp.setattr(S,'portfolio_completion',lambda db:dict(deleted=0))
        owner=Consumer(path)
    with closing(owner._db()) as db, db:
        db.execute('BEGIN IMMEDIATE');first=S.portfolio_completion(db,max_rows=1)
        assert first['pending'] and first['scanned']==1 and first['deleted']==0
    with closing(owner._db()) as db, db:
        db.execute('INSERT INTO checkpoints VALUES (?,?)',('arrived-after-partial-scan',old))
    restarted=Consumer(path)
    assert restarted.lifecycle['deleted']==0
    with closing(restarted._db()) as db: assert E.resolve(db,old)


def test_learning_nested_source_shares_bytes_across_different_stage_envelopes():
    db=E.install(sqlite3.connect(':memory:'));C.ensure(db)
    source={'candles':[{'close':i,'source':'same'} for i in range(6000)]}
    first=C.L.canonical(source)
    second=C.L.canonical({'stage':'completed-outcome','source':source})
    with db:
        one=C.packed(db,first);two=C.packed(db,second)
    a=set(json.loads(one[len(E.PREFIX):])['chunks']);b=set(json.loads(two[len(E.PREFIX):])['chunks'])
    substantial={sha for sha in a if db.execute('SELECT byte_length FROM journal_evidence_blobs_v1 WHERE sha256=?',(sha,)).fetchone()[0]>=1024}
    assert substantial and substantial<=b
    assert E.resolve(db,one)==first and E.resolve(db,two)==second


def test_unknown_learning_view_and_changed_owned_guard_refuse_legacy_cleanup(prospective):
    j,d,_=prospective;oid=finish(j,d)
    with j._tx() as db: P.materialize(db,oid)
    db=j._conn();table='learning_registrations'
    key,physical=db.execute('SELECT event_key,payload FROM '+table).fetchone()
    logical=E.resolve(db,physical)
    with db:
        db.execute('UPDATE '+table+' SET payload=?',(logical,))
        db.execute('CREATE VIEW unknown_learning_consumer AS SELECT payload FROM '+table)
    with db:
        db.execute('BEGIN IMMEDIATE');out=C.compact_completed(db,oid)
    assert table+':unknown_view' in out['refused']
    assert db.execute('SELECT payload FROM '+table).fetchone()[0]==logical
    with db:
        db.execute('DROP VIEW unknown_learning_consumer')
        db.execute('DROP TRIGGER '+table+'_update')
        # Same owned name and function text, but a different logical contract.
        db.execute('CREATE TRIGGER '+table+'_update BEFORE UPDATE ON '+table+' BEGIN SELECT journal_detail_v1(OLD.payload); END')
    with db:
        db.execute('BEGIN IMMEDIATE');out=C.compact_completed(db,oid)
    assert table+':unknown_trigger' in out['refused']
    assert db.execute('SELECT payload FROM '+table).fetchone()[0]==logical


def test_unknown_portfolio_consumer_view_refuses_chunk_deletion(tmp_path,monkeypatch):
    db,old,*_=fixture_db(tmp_path);path=Path(db.execute('PRAGMA database_list').fetchone()[2]);db.close()
    with monkeypatch.context() as mp:
        mp.setattr(S,'portfolio_completion',lambda db:dict(deleted=0))
        owner=Consumer(path)
    with closing(owner._db()) as db, db:
        db.execute('CREATE VIEW unknown_consumer AS SELECT payload FROM journal_evidence_blobs_v1')
        db.execute('BEGIN IMMEDIATE')
        out=S.portfolio_completion(db)
        assert out['refused']=='unknown_portfolio_view' and E.resolve(db,old)
