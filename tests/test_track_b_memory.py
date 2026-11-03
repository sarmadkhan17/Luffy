"""Content hashes, retention reachability and maintenance rollback boundaries."""
import json
import sqlite3
import zlib

import pytest

from trader.observability import investigation as C, memory as M

NOW = 1_800_000_000_000


def context():
    archive = dict(investigation={'id':'source'},updates=[{'value':'x'*30000}],inputs=[])
    return dict(context_id='unchanged',cases=[],source_archives=[archive],
                unassessable_closures=dict(source_archives=[archive]),
                assessed_descriptions=dict(source_archives=[archive]))


def case(db, iid, terminal=None):
    db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)',(iid,'ep-'+iid,'S-'+iid,1,terminal,'{}'))


def test_deduplicated_context_roundtrip_retention_and_verified_hash(tmp_path):
    with C.ledger(tmp_path/'inv.db') as db:
        original = context()
        for iid in ('a','b'):
            case(db,iid)
            db.execute('INSERT INTO memory_contexts VALUES (?,?)',(iid,json.dumps(original)))
        assert M.compact_contexts(db,1) == 1
        assert M.compact_contexts(db,1) == 1
        assert M.compact_contexts(db) == 0
        assert db.execute('SELECT COUNT(*) FROM memory_source_archives').fetchone()[0] == 1
        assert M.owner_context(db,'a')['context'] == original
        assert len(db.execute('SELECT payload FROM memory_contexts LIMIT 1').fetchone()[0]) < 2000
        db.execute("DELETE FROM cases WHERE id='a'"); M.retain(db)
        assert M.owner_context(db,'b')['context'] == original
        assert db.execute('SELECT COUNT(*) FROM memory_source_archives').fetchone()[0] == 1
        db.execute('UPDATE memory_source_archives SET payload=?',(zlib.compress(b'{}'),))
        with pytest.raises(ValueError,match='integrity'): M.owner_context(db,'b')
        db.execute('DELETE FROM cases'); M.retain(db)
        assert db.execute('SELECT COUNT(*) FROM memory_source_archives').fetchone()[0] == 0


def test_maintenance_commits_prune_before_failed_ingestion_and_preserves_referenced_case(tmp_path):
    path = tmp_path/'inv.db'
    with C.ledger(path) as db:
        for iid in ('expired','referenced'): case(db,iid,NOW-C.RETENTION_MS-1)
        db.execute('INSERT INTO typed_outcomes VALUES (?,?,?)',('out',NOW,json.dumps({'case':'referenced'})))
    detail = {}
    with pytest.raises(RuntimeError,match='ingest_failed'):
        with C._maintained_ledger(path,NOW,detail) as db:
            # A separate reader already sees durable retention work.
            with sqlite3.connect(path) as reader:
                assert {r[0] for r in reader.execute('SELECT id FROM cases')} == {'referenced'}
            case(db,'new')
            raise RuntimeError('ingest_failed')
    with sqlite3.connect(path) as db:
        assert {r[0] for r in db.execute('SELECT id FROM cases')} == {'referenced'}
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {'case_inputs_input_v1','cases_terminal_time_v1','allocation_decisions_time_v1'} <= indexes
