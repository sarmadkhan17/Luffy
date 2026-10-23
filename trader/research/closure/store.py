"""Durable closure store (one SQLite file, WAL). Write-once records are
verified byte-for-byte on a repeat; only work items and study units carry a
mutable status. Nothing here touches the Journal, the Bank or any market
database: it is the research layer's own bounded ledger of what was asked,
compiled, run, frozen and qualified."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time

from . import ClosureRefused, canonical

ONCE_TABLES = ('candidates', 'runs', 'freezes', 'qualifications', 'looks', 'manifests', 'envelopes', 'exposure')


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._tx() as c:
            for t in ONCE_TABLES:
                c.execute(f'CREATE TABLE IF NOT EXISTS {t}(id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS items(item_id TEXT PRIMARY KEY, kind TEXT NOT NULL, '
                      'question_id TEXT, status TEXT NOT NULL, reason TEXT, attempts INTEGER NOT NULL DEFAULT 0, '
                      'candidate_id TEXT, payload TEXT NOT NULL, updated_ms INTEGER NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS studies(study_id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
                      'status TEXT NOT NULL, stop_reason TEXT, last_progress_ms INTEGER, next_work TEXT, '
                      'steps INTEGER NOT NULL DEFAULT 0)')
            c.execute('CREATE TABLE IF NOT EXISTS units(study_id TEXT NOT NULL, unit_id TEXT NOT NULL, '
                      'seq INTEGER NOT NULL, kind TEXT NOT NULL, deps TEXT NOT NULL, status TEXT NOT NULL, '
                      'attempts INTEGER NOT NULL DEFAULT 0, result TEXT, note TEXT, updated_ms INTEGER, '
                      'PRIMARY KEY(study_id, unit_id))')

    def _conn(self):
        c = getattr(self._local, 'conn', None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA journal_mode=WAL')
            self._local.conn = c
        return c

    @contextmanager
    def _tx(self):
        c = self._conn()
        c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.execute('COMMIT')
        except BaseException:
            c.execute('ROLLBACK')
            raise

    def close(self):
        c = getattr(self._local, 'conn', None)
        if c is not None:
            c.close()
            self._local.conn = None

    # ── write-once records ───────────────────────────────────────────────
    def put_once(self, table: str, key: str, obj) -> bool:
        assert table in ONCE_TABLES
        text = canonical(obj)
        with self._tx() as c:
            old = c.execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone()
            if old is not None:
                if old[0] != text:
                    raise ClosureRefused('record_conflict', f'{table}:{key[:12]}')
                return False
            c.execute(f'INSERT INTO {table} VALUES (?,?)', (key, text))
            return True

    def get_once(self, table: str, key: str):
        assert table in ONCE_TABLES
        row = self._conn().execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all_once(self, table: str) -> list:
        assert table in ONCE_TABLES
        return [json.loads(r[0]) for r in self._conn().execute(f'SELECT payload FROM {table} ORDER BY rowid')]

    def get_run(self, key):
        return self.get_once('runs', key)

    def put_run(self, key, result):
        return self.put_once('runs', key, result)

    # ── work items ───────────────────────────────────────────────────────
    def claim_item(self, item_id, kind, question_id, payload, now_ms) -> bool:
        """Create the item once; a duplicate delivery returns False."""
        with self._tx() as c:
            if c.execute('SELECT 1 FROM items WHERE item_id=?', (item_id,)).fetchone():
                return False
            c.execute('INSERT INTO items(item_id,kind,question_id,status,payload,updated_ms) VALUES (?,?,?,?,?,?)',
                      (item_id, kind, question_id, 'PENDING', canonical(payload), now_ms))
            return True

    def item(self, item_id):
        r = self._conn().execute('SELECT * FROM items WHERE item_id=?', (item_id,)).fetchone()
        return dict(r) if r else None

    def items(self, status=None, kind=None):
        sql, args = 'SELECT * FROM items WHERE 1=1', []
        for col, v in (('status', status), ('kind', kind)):
            if v is not None:
                sql += f' AND {col}=?'
                args.append(v)
        return [dict(r) for r in self._conn().execute(sql + ' ORDER BY updated_ms,item_id', args)]

    def set_item(self, item_id, status, reason, now_ms, candidate_id=None, bump=False):
        with self._tx() as c:
            c.execute('UPDATE items SET status=?,reason=?,candidate_id=COALESCE(?,candidate_id),'
                      'attempts=attempts+?,updated_ms=? WHERE item_id=?',
                      (status, reason, candidate_id, 1 if bump else 0, now_ms, item_id))

    # ── studies ──────────────────────────────────────────────────────────
    def study(self, study_id):
        r = self._conn().execute('SELECT * FROM studies WHERE study_id=?', (study_id,)).fetchone()
        return dict(r) if r else None

    def studies(self):
        return [dict(r) for r in self._conn().execute('SELECT * FROM studies ORDER BY rowid')]

    def units(self, study_id):
        return [dict(r) for r in self._conn().execute(
            'SELECT * FROM units WHERE study_id=? ORDER BY seq', (study_id,))]

    @staticmethod
    def now_ms():
        return int(time.time() * 1000)
