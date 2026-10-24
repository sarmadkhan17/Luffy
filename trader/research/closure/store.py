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

ONCE_TABLES = ('candidates', 'runs', 'freezes', 'qualifications', 'looks', 'manifests', 'envelopes', 'exposure',
                'budgets', 'attempts', 'requirements', 'proposals', 'proposal_reservations')


def read_cached_run(path, key):
    """Detached worker reads an existing research cache; never creates/writes."""
    from contextlib import closing
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        row = db.execute('SELECT payload FROM runs WHERE id=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


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
            c.execute('CREATE INDEX IF NOT EXISTS items_candidate ON items(candidate_id)')
            c.execute('CREATE TABLE IF NOT EXISTS studies(study_id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
                      'status TEXT NOT NULL, stop_reason TEXT, last_progress_ms INTEGER, next_work TEXT, '
                      'steps INTEGER NOT NULL DEFAULT 0)')
            c.execute('CREATE TABLE IF NOT EXISTS units(study_id TEXT NOT NULL, unit_id TEXT NOT NULL, '
                      'seq INTEGER NOT NULL, kind TEXT NOT NULL, deps TEXT NOT NULL, status TEXT NOT NULL, '
                      'attempts INTEGER NOT NULL DEFAULT 0, result TEXT, note TEXT, updated_ms INTEGER, '
                      'PRIMARY KEY(study_id, unit_id))')
            c.execute('CREATE TABLE IF NOT EXISTS schedule_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, at_ms INTEGER NOT NULL, '
                      'status TEXT NOT NULL, study_id TEXT, stop_reason TEXT, units_run INTEGER, elapsed_s REAL, detail TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS run_index(run_id TEXT PRIMARY KEY, candidate_id TEXT, envelope_id TEXT, '
                      'result_id TEXT, stage TEXT)')
            c.execute('CREATE INDEX IF NOT EXISTS run_index_candidate ON run_index(candidate_id)')
            c.execute('CREATE INDEX IF NOT EXISTS run_index_envelope ON run_index(envelope_id)')
            c.execute('CREATE INDEX IF NOT EXISTS run_index_result ON run_index(result_id)')
            c.execute('CREATE TABLE IF NOT EXISTS run_summaries(run_id TEXT PRIMARY KEY, outcome TEXT, reason TEXT, data_id TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS resource_ledger(seq INTEGER PRIMARY KEY AUTOINCREMENT, study_id TEXT NOT NULL, '
                      'unit_id TEXT NOT NULL, kind TEXT NOT NULL, started_ms INTEGER, ended_ms INTEGER, elapsed_s REAL NOT NULL, '
                      'outcome TEXT NOT NULL, fits INTEGER NOT NULL DEFAULT 0, reads INTEGER NOT NULL DEFAULT 0)')
            cols = {r[1] for r in c.execute('PRAGMA table_info(resource_ledger)')}
            for col in ('fits', 'reads'):                      # stores created before dispatch charging existed
                if col not in cols:
                    c.execute(f'ALTER TABLE resource_ledger ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0')
            c.execute('CREATE TABLE IF NOT EXISTS data_requests(request_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, '
                      'candidate_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_ms INTEGER NOT NULL, '
                      'updated_ms INTEGER NOT NULL, resolution TEXT)')

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

    def runs_for(self, *, envelope_id=None, result_id=None, candidate_id=None):
        """Select indexed identities before decoding numerical result payloads."""
        filters = [(k, v) for k, v in (('envelope_id', envelope_id), ('result_id', result_id),
                                      ('candidate_id', candidate_id)) if v is not None]
        if not filters:
            raise ValueError('run selector required')
        # Legacy put_once records may predate run_index. Narrow them in SQL too;
        # absence of an acceleration index must never hide existing evidence.
        indexed = ' AND '.join('i.' + k + '=?' for k, _ in filters)
        legacy = ' AND '.join("json_extract(r.payload,'$." + k + "')=?" for k, _ in filters)
        sql = ('SELECT r.payload FROM runs r LEFT JOIN run_index i ON i.run_id=r.id '
               'WHERE (' + indexed + ') OR (i.run_id IS NULL AND ' + legacy + ') ORDER BY r.rowid')
        args = [v for _, v in filters]
        return [json.loads(r[0]) for r in self._conn().execute(sql, args + args)]

    def attempts_for(self, study_id):
        return [json.loads(r[0]) for r in self._conn().execute(
            "SELECT payload FROM attempts WHERE json_extract(payload,'$.study_id')=? ORDER BY rowid", (study_id,))]

    def owned_items(self, study_id):
        legacy = self._conn().execute('SELECT COUNT(*) FROM studies').fetchone()[0] == 1
        return [dict(r) for r in self._conn().execute(
            "SELECT * FROM items WHERE json_extract(payload,'$.study_id')=? OR "
            "(? AND json_extract(payload,'$.study_id') IS NULL) ORDER BY updated_ms,item_id", (study_id, legacy))]

    def get_run(self, key):
        return self.get_once('runs', key)

    def put_run(self, key, result):
        created = self.put_once('runs', key, result)
        with self._tx() as c:        # indexed owner lookups: a detail view must not decode every stored run
            c.execute('INSERT OR IGNORE INTO run_index VALUES (?,?,?,?,?)',
                      (key, result.get('candidate_id'), result.get('envelope_id'), result.get('result_id'), result.get('stage')))
            c.execute('INSERT OR IGNORE INTO run_summaries VALUES (?,?,?,?)',
                      (key, result.get('outcome'), (result.get('reason') or '')[:160], result.get('data_id')))
        return created

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

    def items(self, status=None, kind=None, candidate_id=None, root=None, parent_candidate_id=None, outcome=None):
        sql, args = 'SELECT * FROM items WHERE 1=1', []
        for col, v in (('status', status), ('kind', kind), ('candidate_id', candidate_id)):
            if v is not None:
                sql += f' AND {col}=?'
                args.append(v)
        for key, value in (('provenance.root', root), ('parent_candidate_id', parent_candidate_id), ('outcome', outcome)):
            if value is not None:
                sql += " AND json_extract(payload, '$." + key + "')=?"
                args.append(value)
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

    # ── cumulative resources: EVERY attempt (success, timeout, refusal, crash-retry) is appended and never overwritten ──
    def start_resource(self, study_id, unit_id, kind, started_ms, fits=0, reads=0) -> int:
        """Reserve one dispatch: its MAXIMUM fits and dataset reads are charged now and kept whatever happens next
        (timeout, crash, retry): how many actually finished is unknowable, so the reservation is the charge."""
        with self._tx() as c:
            return c.execute('INSERT INTO resource_ledger(study_id,unit_id,kind,started_ms,ended_ms,elapsed_s,outcome,fits,reads) '
                             "VALUES (?,?,?,?,NULL,0,'RUNNING',?,?)", (study_id, unit_id, kind, started_ms, int(fits), int(reads))).lastrowid

    def finish_resource(self, seq, ended_ms, elapsed_s, outcome):
        with self._tx() as c:
            c.execute('UPDATE resource_ledger SET ended_ms=?,elapsed_s=?,outcome=? WHERE seq=?', (ended_ms, float(elapsed_s), outcome, seq))

    def close_interrupted(self, study_id, unit_id, now_ms, cap_s):
        """A unit found RUNNING had its process killed. Charge it what it could have used (wall time since start, at
        most one unit cap): an interrupted attempt is never free."""
        with self._tx() as c:
            for seq, started in c.execute("SELECT seq,started_ms FROM resource_ledger WHERE study_id=? AND unit_id=? AND outcome='RUNNING'",
                                          (study_id, unit_id)).fetchall():
                c.execute("UPDATE resource_ledger SET ended_ms=?,elapsed_s=?,outcome='INTERRUPTED' WHERE seq=?",
                          (now_ms, min(max((now_ms - (started or now_ms)) / 1000.0, 0.0), float(cap_s)), seq))

    def resource_totals(self, study_id) -> dict:
        s, f, r, n = self._conn().execute('SELECT COALESCE(SUM(elapsed_s),0),COALESCE(SUM(fits),0),COALESCE(SUM(reads),0),COUNT(*) '
                                          'FROM resource_ledger WHERE study_id=?', (study_id,)).fetchone()
        return dict(seconds=float(s), fits=int(f), reads=int(r), dispatches=int(n))

    def resource_seconds(self, study_id) -> float:
        return float(self._conn().execute('SELECT COALESCE(SUM(elapsed_s),0) FROM resource_ledger WHERE study_id=?',
                                          (study_id,)).fetchone()[0])

    def resource_ledger(self, study_id):
        return [dict(r) for r in self._conn().execute('SELECT * FROM resource_ledger WHERE study_id=? ORDER BY seq', (study_id,))]

    # ── scheduled-run outcomes (the kernel callback's return value is not kept; this is) ──
    def log_schedule(self, at_ms, status, study_id=None, stop_reason=None, units_run=None, elapsed_s=None, detail=None):
        with self._tx() as c:
            c.execute('INSERT INTO schedule_events(at_ms,status,study_id,stop_reason,units_run,elapsed_s,detail) VALUES (?,?,?,?,?,?,?)',
                      (at_ms, status, study_id, stop_reason, units_run, elapsed_s, canonical(detail) if detail is not None else None))
            c.execute("DELETE FROM schedule_events WHERE status!='STUDY_CREATED' AND seq NOT IN "
                      '(SELECT seq FROM schedule_events ORDER BY seq DESC LIMIT 500)')

    def schedule_events(self, limit=50):
        return [dict(r) for r in self._conn().execute('SELECT * FROM schedule_events ORDER BY seq DESC LIMIT ?', (limit,))]

    # ── owner data-access requests (alerts, never approvals) ─────────────
    def open_data_request(self, req, now_ms) -> bool:
        """Record once per (study, candidate, series, span). A repeat is a no-op; a RESOLVED
        request whose need returns is reopened, its earlier resolution kept in history."""
        with self._tx() as c:
            old = c.execute('SELECT status FROM data_requests WHERE request_id=?', (req['request_id'],)).fetchone()
            if old is None:
                c.execute('INSERT INTO data_requests VALUES (?,?,?,?,?,?,?,NULL)',
                          (req['request_id'], req['study_id'], req['candidate_id'], 'OPEN', canonical(req), now_ms, now_ms))
                return True
            if old[0] == 'RESOLVED':
                c.execute("UPDATE data_requests SET status='OPEN',updated_ms=? WHERE request_id=?", (now_ms, req['request_id']))
                return True
            return False

    def data_request(self, request_id):
        r = self._conn().execute('SELECT * FROM data_requests WHERE request_id=?', (request_id,)).fetchone()
        return dict(json.loads(r['payload']), status=r['status']) if r else None

    def data_requests(self, study_id=None, candidate_id=None, status=None):
        sql, args = 'SELECT * FROM data_requests WHERE 1=1', []
        for col, v in (('study_id', study_id), ('candidate_id', candidate_id), ('status', status)):
            if v is not None:
                sql += f' AND {col}=?'
                args.append(v)
        return [dict(json.loads(r['payload']), status=r['status'], created_ms=r['created_ms'], updated_ms=r['updated_ms'],
                     resolution=json.loads(r['resolution']) if r['resolution'] else None)
                for r in self._conn().execute(sql + ' ORDER BY rowid', args)]

    def open_count(self, study_id) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM data_requests WHERE study_id=? AND status='OPEN'",
                                    (study_id,)).fetchone()[0]

    def resolve_data_request(self, request_id, now_ms, resolution):
        with self._tx() as c:
            c.execute("UPDATE data_requests SET status='RESOLVED',updated_ms=?,resolution=? WHERE request_id=? AND status='OPEN'",
                      (now_ms, canonical(resolution), request_id))

    @staticmethod
    def now_ms():
        return int(time.time() * 1000)
