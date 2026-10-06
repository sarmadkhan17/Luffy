"""Offline writer/reader, corruption, backup and disabled transport regressions."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from trader.core.journal import Journal
from trader.core import journal_evidence as E
from trader.brain.llm import BrainLLM


def test_exact_detail_shared_references_clocks_and_legacy(tmp_path):
    j=Journal(tmp_path/'journal.db')
    # Different acquisitions that happen to report the same values must remain
    # separate events and retain distinct revisions, availability and clocks.
    raw=' {"receipt": {"revision": "r1", "price": 1.00}, "escaped": "\\u0041"} '
    with j._tx() as db:
        first=E.store(db,raw)
        db.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES(?,?,?,?)",('t1','market_provenance','d1',first))
        before=db.execute('SELECT COUNT(*) FROM journal_evidence_blobs_v1').fetchone()[0]
        second=E.store(db,raw)
        assert second==first
        db.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES(?,?,?,?)",('t2','market_provenance','d2',second))
        assert db.execute('SELECT COUNT(*) FROM journal_evidence_blobs_v1').fetchone()[0]==before
        third=E.store(db,raw.replace('r1','r2'))
        assert third != first
    rows=j.query('SELECT * FROM brain_events ORDER BY id')
    assert [r['detail'] for r in rows]==[raw,raw]
    assert [r['ts'] for r in rows]==['t1','t2']
    assert j.query("SELECT name FROM sqlite_master WHERE name='brain_events'")
    assert j.query("SELECT 'brain_events' literal, detail FROM brain_events LIMIT 1")[0]['literal']=='brain_events'
    assert j.query("SELECT json_extract(detail,'$.receipt.price') value FROM brain_events")[0]['value']==1
    j.log_brain_event('legacy','s',{'status':'ok'})
    assert json.loads(j.query('SELECT detail FROM brain_events ORDER BY id DESC LIMIT 1')[0]['detail'])=={'status':'ok'}
    # Complete SQLite binary backup is self-contained: no external blob files.
    backup=sqlite3.connect(tmp_path/'backup.db');j._conn().backup(backup);E.install(backup)
    assert backup.execute('SELECT detail FROM brain_events_logical_v1 ORDER BY id LIMIT 1').fetchone()[0]==raw
    assert backup.execute('PRAGMA integrity_check').fetchall()==[('ok',)]


@pytest.mark.parametrize('corruption',['missing','payload','manifest'])
def test_corrupt_or_missing_evidence_fails(tmp_path,corruption):
    j=Journal(tmp_path/'journal.db');j.log_brain_event('market_provenance','s',{'revision':'r1'})
    stored=j._conn().execute('SELECT detail FROM brain_events').fetchone()[0]
    with pytest.raises(json.JSONDecodeError):json.loads(stored)
    with j._tx() as db:
        if corruption=='missing':db.execute('DELETE FROM journal_evidence_blobs_v1')
        elif corruption=='payload':db.execute("UPDATE journal_evidence_blobs_v1 SET payload=x'1234'")
        else:db.execute('UPDATE brain_events SET detail=?',(stored.replace('"byte_length":','"byte_length":9'),))
    with pytest.raises(sqlite3.OperationalError):j.query('SELECT detail FROM brain_events')


def test_streaming_frozen_acquisitions_exact_and_once(tmp_path,monkeypatch):
    j=Journal(tmp_path/'journal.db');receipt={'r':{'available_ms':100,'revision':'different-observation','raw':'e'*150000}}
    frozen=E.FrozenJSON.freeze(receipt)
    original=json.dumps(receipt,sort_keys=True)
    def no_again(*args,**kw):raise AssertionError('frozen source reserialized')
    monkeypatch.setattr(E.FrozenJSON,'freeze',no_again)
    for i in range(21):
        j.log_brain_event('market_provenance',str(i),{'decision_id':str(i),'receipt':frozen})
    blobs=j.query('SELECT COUNT(*) n FROM journal_evidence_blobs_v1')[0]['n']
    assert blobs<21*len(frozen.segments)
    for i,row in enumerate(j.query('SELECT detail FROM brain_events ORDER BY id')):
        assert row['detail']=='{"decision_id": '+json.dumps(str(i))+', "receipt": '+original+'}'


@pytest.mark.parametrize('method',['chat','chat_tools','chat_json','_call','_admit'])
def test_disabled_zero_transport_and_ledger_unchanged(tmp_path,monkeypatch,method):
    monkeypatch.setattr('trader.brain.llm.Env.deepseek_key',lambda:'offline-fake-key')
    cfg={'brain':{'enabled':False,'model_fast':'f','model_deep':'d','max_tokens_per_call':100,'daily_token_budget':100000}}
    b=BrainLLM(cfg);b._usage_path=tmp_path/'usage.json'
    original={'historical-day':234,'_reservations':{'old':{'day':'historical-day','purpose':'research','tokens':100}}}
    b._usage_path.write_text(json.dumps(original));before=b._usage_path.read_bytes();attempts=[]
    b._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:attempts.append(kw))))
    assert not b.available
    args={'chat':('p',),'chat_json':('p',),'chat_tools':([{'role':'user','content':'p'}],[]),'_call':({},False,'background'),'_admit':({},False,'background')}
    assert getattr(b,method)(*args[method]) is None
    assert attempts==[] and b._usage_path.read_bytes()==before
    assert not b._usage_path.with_suffix('.json.lock').exists()


def test_interrupted_migration_never_verified(tmp_path):
    from scripts.compact_journal import migrate
    src=tmp_path/'original.db';db=sqlite3.connect(src)
    db.execute('CREATE TABLE brain_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,kind TEXT NOT NULL,subject TEXT,detail TEXT)')
    db.executemany('INSERT INTO brain_events(ts,kind,subject,detail) VALUES (?,?,?,?)',[('t','market_provenance',str(i),'{"r":1}') for i in range(3)])
    db.commit();db.close();dest=tmp_path/'partial'
    with pytest.raises(InterruptedError):migrate(src,dest,estimate=1000,stop_after=1)
    assert json.loads((dest/'manifest.json').read_text())['status']=='INTERRUPTED'
    candidate=sqlite3.connect(dest/'original.db')
    assert candidate.execute('SELECT status FROM journal_representation_v1').fetchone()[0]=='BUILDING'
    assert candidate.execute('SELECT COUNT(*) FROM brain_events').fetchone()[0]==1
    with pytest.raises(E.EvidenceError,match='journal_candidate_not_verified'):Journal(dest/'original.db')
    with pytest.raises(FileExistsError):migrate(src,dest,estimate=1000)


def test_full_small_migration_preserves_rowid_sequence_schema(tmp_path):
    from scripts.compact_journal import migrate
    src=tmp_path/'original.db';db=sqlite3.connect(src)
    db.executescript('CREATE TABLE brain_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,kind TEXT NOT NULL,subject TEXT,detail TEXT);CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT);CREATE INDEX original_idx ON brain_events(kind);CREATE TRIGGER original_guard BEFORE DELETE ON state_kv BEGIN SELECT RAISE(ABORT,"immutable"); END;')
    db.executemany('INSERT INTO brain_events(id,ts,kind,subject,detail) VALUES (?,?,?,?,?)',[(5,'t1','market_provenance','d1',' {"receipt":1.00} '),(8,'t2','legacy','s','raw text')])
    db.execute("INSERT INTO state_kv(rowid,key,value) VALUES(42,'control','FROZEN')");db.execute('UPDATE sqlite_sequence SET seq=99');db.commit();db.close()
    dest=tmp_path/'full';migrate(src,dest,estimate=100000)
    manifest=json.loads((dest/'manifest.json').read_text());assert manifest['status']=='VERIFIED'
    result=json.loads((dest/'verification.json').read_text());assert result['events']['all']==2
    candidate=sqlite3.connect(dest/'original.db');assert candidate.execute('SELECT rowid FROM state_kv').fetchone()[0]==42
    assert candidate.execute('SELECT seq FROM sqlite_sequence').fetchone()[0]==99
    assert not candidate.execute("SELECT name FROM sqlite_master WHERE name='partial_exit_intents'").fetchall()


def test_attached_research_sql_resolves_and_verifies(tmp_path):
    from trader.cognition import research_shadow_store as S
    source=Journal(tmp_path/'source.db');raw=' {"schema_version": "legacy.v1", "price": 1.00} '
    with source._tx() as db:
        marker=E.store(db,raw)
        db.execute('INSERT INTO brain_events(ts,kind,subject,detail) VALUES (?,?,?,?)',('t',S.SPEC_KIND,'s1',marker))
    source._conn().close();S.init_shadow(tmp_path/'shadow.db')
    j=S.open_shadow(tmp_path/'shadow.db',tmp_path/'source.db',readonly=False);j.set_bound(1,1)
    assert j.query('SELECT detail FROM brain_events')[0]['detail']==raw
    assert j.source_query('SELECT detail FROM src.brain_events')[0]['detail']==raw
    with pytest.raises(sqlite3.DatabaseError):j.source_query('UPDATE src.brain_events SET detail=\'{}\'')
    j.close()


def test_streaming_preserves_standard_json_key_and_scalar_contract():
    value={True:'\u2603',None:1.0,2:['x',False],3.5:None}
    assert ''.join(E.serialize(value))==json.dumps(value)
    with pytest.raises(TypeError):''.join(E.serialize({object():1}))


def test_provenance_retry_adds_no_event_but_distinct_observation_does(tmp_path):
    j=Journal(tmp_path/'journal.db');receipt={'revision':'r1','raw':'x'*1000}
    def n():return j.query("SELECT COUNT(*) n FROM brain_events")[0]['n']
    def blobs():return j.query('SELECT COUNT(*) n FROM journal_evidence_blobs_v1')[0]['n']
    j.log_brain_event('market_provenance','market_data',{'decision_id':'d1','cycle_id':'c1','receipt':receipt})
    events,stored=n(),blobs()
    j.log_brain_event('market_provenance','market_data',{'decision_id':'d1','cycle_id':'c1','receipt':receipt})  # exact retry
    assert (n(),blobs())==(events,stored)
    # Adverse variants: same payload bytes, different observation identity.
    j.log_brain_event('market_provenance','market_data',{'decision_id':'d2','cycle_id':'c1','receipt':receipt})
    j.log_brain_event('market_provenance','other_subject',{'decision_id':'d1','cycle_id':'c1','receipt':receipt})
    assert n()==events+2
    # Other kinds are never deduplicated.
    for _ in range(2):j.log_brain_event('legacy','s',{'status':'ok'})
    assert n()==events+4
