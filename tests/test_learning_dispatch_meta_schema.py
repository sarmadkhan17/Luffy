"""Eligibility schema preparation uses only temporary SQLite databases."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest

from trader.learning import dispatch as D
from trader.core import journal_evidence as E
import json


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / 'dispatch.db')
    try:
        yield conn
    finally:
        conn.close()


def test_creation_is_idempotent_and_does_not_read_legacy_evidence(db):
    db.execute('CREATE TABLE learning_produced_chains(outcome_id TEXT PRIMARY KEY,payload TEXT)')
    db.execute("INSERT INTO learning_produced_chains VALUES ('legacy','unreadable marker')")
    statements = []
    db.set_trace_callback(statements.append)
    D.ensure_chain_dispatch_meta(db)
    db.execute(f"INSERT INTO {D.CHAIN_META_TABLE} VALUES ('ready',1,'REPLAY_COMPLETE',NULL,'hash')")
    D.ensure_chain_dispatch_meta(db)
    db.set_trace_callback(None)
    assert not any('SELECT' in statement.upper() for statement in statements)
    assert db.execute(f'SELECT * FROM {D.CHAIN_META_TABLE}').fetchall() == [
        ('ready', 1, 'REPLAY_COMPLETE', None, 'hash')]
    assert db.execute('SELECT * FROM learning_produced_chains').fetchall() == [
        ('legacy', 'unreadable marker')]


@pytest.mark.parametrize('values', [
    ('ready', 2, 'REPLAY_COMPLETE', None, 'hash'),  # outcome identity collision
    ('other', 1, 'REPLAY_COMPLETE', None, 'hash'),  # row identity collision
    ('other', None, 'REPLAY_COMPLETE', None, 'hash'),
    ('other', 2, None, None, 'hash'),
    ('other', 2, 'REPLAY_COMPLETE', None, None),
])
def test_identity_and_required_metadata_constraints(db, values):
    D.ensure_chain_dispatch_meta(db)
    db.execute(f"INSERT INTO {D.CHAIN_META_TABLE} VALUES ('ready',1,'REPLAY_COMPLETE',NULL,'hash')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f'INSERT INTO {D.CHAIN_META_TABLE} VALUES (?,?,?,?,?)', values)


def test_ready_selection_has_a_covering_partial_index(db):
    D.ensure_chain_dispatch_meta(db)
    db.executemany(f'INSERT INTO {D.CHAIN_META_TABLE} VALUES (?,?,?,?,?)', [
        ('b', 1, 'REPLAY_COMPLETE', 'eb', 'hb'),
        ('a', 2, 'REPLAY_INCOMPLETE', None, 'ha'),
        ('c', 3, 'REPLAY_COMPLETE', 'ec', 'hc'),
    ])
    query = (f"SELECT outcome_id,chain_rowid FROM {D.CHAIN_META_TABLE} "
             "WHERE replay_gate='REPLAY_COMPLETE' ORDER BY outcome_id,chain_rowid LIMIT 1")
    assert db.execute(query).fetchall() == [('b', 1)]
    plan = ' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + query))
    assert 'learning_chain_ready_order_v1' in plan
    assert 'TEMP B-TREE' not in plan


def test_creation_preserves_the_callers_transaction(db):
    db.execute('BEGIN')
    D.ensure_chain_dispatch_meta(db)
    db.rollback()
    assert db.execute("SELECT name FROM sqlite_master WHERE name=?", (D.CHAIN_META_TABLE,)).fetchall() == []


def test_dispatch_ensure_creates_projection_without_a_chain_table(db, monkeypatch):
    @contextmanager
    def tx():
        with db:
            yield db

    journal = SimpleNamespace(_tx=tx)
    monkeypatch.setattr(D.R, 'ensure', lambda journal: None)
    D.ensure(journal)
    D.ensure(journal)
    assert db.execute(f'SELECT * FROM {D.CHAIN_META_TABLE}').fetchall() == []
    assert db.execute("SELECT name FROM sqlite_master WHERE name=?", (D.TABLE,)).fetchone()


@pytest.fixture
def journal(db):
    db.row_factory = sqlite3.Row
    E.install(db)
    db.execute(E.BLOB_SCHEMA)
    db.execute('CREATE TABLE learning_produced_chains(outcome_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    db.execute('CREATE VIEW learning_produced_chains_logical_v1 AS SELECT rowid AS rowid,'
               'outcome_id,journal_detail_v1(payload) AS payload FROM learning_produced_chains')
    db.commit()

    @contextmanager
    def tx():
        with db:
            yield db

    def add(oid, gate='REPLAY_COMPLETE', evidence_id=None):
        body = json.dumps(dict(replay_gate=gate, evidence=dict(outcome_id=evidence_id or oid),
                               padding='x' * 40000))
        with db:
            db.execute('INSERT INTO learning_produced_chains VALUES (?,?)', (oid, E.store(db, body)))

    return SimpleNamespace(_tx=tx, _conn=lambda: db, add=add,
        query=lambda sql, args=(): [dict(r) for r in db.execute(E.read_sql(db, sql), args)])


def test_warm_selection_only_expands_selected_ready_chains(journal, monkeypatch):
    for i in range(128):
        journal.add(f'bad{i:03}', 'REPLAY_INCOMPLETE')
    for i in range(12):
        journal.add(f'good{i:03}')
    assert len(D.pending(journal, 'registry', 8)) == 8
    calls = []
    real = E.chain_dispatch_metadata
    monkeypatch.setattr(E, 'chain_dispatch_metadata',
                        lambda *a: calls.append(a[2]) or real(*a))
    result = D.pending(journal, 'registry', 8)
    assert [row['outcome_id'] for row in result] == [f'good{i:03}' for i in range(8)]
    assert calls == [f'good{i:03}' for i in range(8)]
    assert all(len(row['payload']) > 40000 for row in result)


def test_direct_insert_after_population_is_not_lost_below_watermark(journal):
    journal.add('b')
    assert D.pending(journal, 'v1', 8)[0]['outcome_id'] == 'b'
    journal.add('a')
    assert [r['outcome_id'] for r in D.pending(journal, 'v1', 8, after_rowid=1)] == ['a']


def test_receipts_filter_by_evidence_identity_and_registry(journal):
    journal.add('chain', evidence_id='evidence')
    db = journal._conn()
    with db:
        db.execute(f'CREATE TABLE {D.TABLE}(outcome_id TEXT,registry_digest TEXT)')
        db.execute(f"INSERT INTO {D.TABLE} VALUES ('evidence','v1')")
    assert D.pending(journal, 'v1', 8) == []
    assert D.pending(journal, 'v2', 8)[0]['outcome_id'] == 'chain'


def test_selected_projection_hash_mismatch_refuses_dispatch(journal):
    journal.add('ready')
    D.pending(journal, 'v1', 8)
    with journal._conn() as db:
        db.execute(f"UPDATE {D.CHAIN_META_TABLE} SET payload_sha256='corrupt'")
    with pytest.raises(E.EvidenceError, match='metadata_mismatch'):
        D.pending(journal, 'v1', 8)


def test_legacy_population_rolls_back_on_unverifiable_evidence(journal):
    journal.add('good')
    with journal._conn() as db:
        db.execute("INSERT INTO learning_produced_chains VALUES ('bad',?)", (E.PREFIX + '{}',))
    with pytest.raises(E.EvidenceError):
        D.pending(journal, 'v1', 8)
    assert not journal.query('SELECT 1 FROM sqlite_master WHERE name=?', (D.CHAIN_META_TABLE,))


def test_shadow_selection_does_not_create_or_populate_metadata(journal):
    journal.add('ready')
    assert D.pending(journal, 'v1', 8, use_projection=False)[0]['outcome_id'] == 'ready'
    assert not journal.query('SELECT 1 FROM sqlite_master WHERE name=?', (D.CHAIN_META_TABLE,))


def test_physical_locator_swaps_preserve_eligibility(journal):
    journal.add('a')
    journal.add('b')
    D.pending(journal, 'v1', 8)
    with journal._conn() as db:
        db.execute("UPDATE learning_produced_chains SET rowid=-rowid")
        db.execute("UPDATE learning_produced_chains SET rowid=CASE outcome_id WHEN 'a' THEN 2 ELSE 1 END")
    assert [(r['outcome_id'], r['rowid']) for r in D.pending(journal, 'v1', 8, after_rowid=0)] == [
        ('a', 2), ('b', 1)]


@pytest.mark.parametrize('after', [None, 1, 2, 99])
def test_projection_matches_original_logical_query(journal, after):
    journal.add('z')
    journal.add('a', 'REPLAY_INCOMPLETE')
    journal.add('b')
    assert D.pending(journal, 'v1', 8, after) == D.pending(
        journal, 'v1', 8, after, use_projection=False)


def test_corrupt_selected_blob_is_still_verified_after_population(journal):
    journal.add('ready')
    D.pending(journal, 'v1', 8)
    with journal._conn() as db:
        db.execute("UPDATE journal_evidence_blobs_v1 SET payload=x'1234'")
    with pytest.raises(E.EvidenceError):
        D.pending(journal, 'v1', 8)
