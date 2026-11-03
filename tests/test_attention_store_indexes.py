"""Attention index installation and real query plans on temporary stores."""
import sqlite3

import pytest

from trader.observability.attention import settings
from trader.observability.store import Store


ROWS = [('a', 200, '{}', 1), ('b', 200, '{}', 1),
        ('empty', 300, None, 1), ('incomplete', 400, '{}', 0)]


@pytest.mark.parametrize('legacy', [False, True])
def test_initialization_migrates_and_reopens_without_changing_scans(tmp_path, legacy):
    path = tmp_path / 'attention.db'
    if legacy:
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE scans(scan_id TEXT PRIMARY KEY, as_of_ms INTEGER NOT NULL,'
                       'payload TEXT, causes_complete INTEGER DEFAULT 0)')
            db.executemany('INSERT INTO scans VALUES (?,?,?,?)', ROWS)
    store = Store(path, settings())
    try:
        indexes = dict(store.db.execute("SELECT name,rootpage FROM sqlite_master "
            "WHERE type='index' AND name IN ('scans_time_v1','scans_complete_time_v1')"))
        assert set(indexes) == {'scans_time_v1', 'scans_complete_time_v1'}
        assert [tuple(r) for r in store.db.execute('SELECT * FROM scans ORDER BY rowid')] == (ROWS if legacy else [])
    finally:
        store.close()
    reopened = Store(path, settings())
    try:
        assert dict(reopened.db.execute("SELECT name,rootpage FROM sqlite_master "
            "WHERE type='index' AND name IN ('scans_time_v1','scans_complete_time_v1')")) == indexes
        assert [tuple(r) for r in reopened.db.execute('SELECT * FROM scans ORDER BY rowid')] == (ROWS if legacy else [])
    finally:
        reopened.close()


def test_latest_complete_query_uses_partial_index_and_tracks_completion(tmp_path):
    store = Store(tmp_path / 'attention.db', settings())
    try:
        with store.db:
            store.db.executemany('INSERT INTO scans VALUES (?,?,?,?)', ROWS)
        query = ('SELECT scan_id FROM scans WHERE causes_complete=1 AND payload IS NOT NULL '
                 'ORDER BY as_of_ms DESC,scan_id DESC LIMIT 1')
        plan = ' '.join(r[3] for r in store.db.execute('EXPLAIN QUERY PLAN ' + query))
        assert 'scans_complete_time_v1' in plan
        assert 'TEMP B-TREE' not in plan
        assert store.db.execute(query).fetchone()[0] == 'b'
        with store.db:
            store.db.execute("UPDATE scans SET causes_complete=1 WHERE scan_id='incomplete'")
        assert store.db.execute(query).fetchone()[0] == 'incomplete'
        with store.db:
            store.db.execute("UPDATE scans SET payload=NULL WHERE scan_id='incomplete'")
        assert store.db.execute(query).fetchone()[0] == 'b'
    finally:
        store.close()


def test_retention_order_and_age_filter_use_time_index(tmp_path):
    store = Store(tmp_path / 'attention.db', settings({'max_scans': 2}))
    try:
        with store.db:
            store.db.executemany('INSERT INTO scans VALUES (?,?,?,?)', ROWS)
        order = 'SELECT scan_id FROM scans ORDER BY as_of_ms DESC,scan_id DESC LIMIT -1 OFFSET 2'
        age = 'SELECT scan_id FROM scans WHERE as_of_ms < ?'
        for sql, args in [(order, ()), (age, (250,))]:
            plan = ' '.join(r[3] for r in store.db.execute('EXPLAIN QUERY PLAN ' + sql, args))
            assert 'scans_time_v1' in plan
            assert 'TEMP B-TREE' not in plan
        assert [r[0] for r in store.db.execute(order)] == ['b', 'a']
        with store.db:
            store._prune(1000)
        assert {r[0] for r in store.db.execute('SELECT scan_id FROM scans')} == {'empty', 'incomplete'}
    finally:
        store.close()
