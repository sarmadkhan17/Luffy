"""Off-path demo accounting queue. No order writes or automatic memory import.

One process owns the queue lock. Attempts are reserved durably before capture;
crash recovery replays any complete file before deciding to retry. Evidence is
never overwritten/deleted. Capacity exhaustion pauses work explicitly.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
import uuid

from trader.engine import booking, trade_accounting as A

MAX_JOBS = 10000
MAX_ATTEMPTS = 10000
MAX_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT = 8 * 1024 * 1024
BASE_RETRY_MS = 300000
MAX_RETRY_MS = 28800000


def connect(path, mode='ro'):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=' + mode, uri=True, timeout=2)


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
        INSERT OR IGNORE INTO meta VALUES('cursor',0);
        CREATE TABLE IF NOT EXISTS jobs(
            trade_id TEXT PRIMARY KEY, status TEXT NOT NULL, due_ms INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS attempts(
            id TEXT PRIMARY KEY, trade_id TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL, started_ms INTEGER NOT NULL, finished_ms INTEGER,
            reasons TEXT NOT NULL DEFAULT '[]');
    ''')


def finish(db, attempt, now):
    aid, tid, path = attempt
    try:
        artifact = json.loads(Path(path).read_text())
        result = A.replay(artifact)
        if artifact['bookings']['trade']['id'] != tid:
            raise ValueError('identity')
        status = 'complete' if result['learning_eligible'] else 'retry'
        reasons = result['reasons']
    except Exception as exc:
        prior = db.execute('SELECT reasons FROM attempts WHERE id=?', (aid,)).fetchone()
        status = 'retry'
        reasons = json.loads(prior[0]) + ['attempt_unavailable_retry:' + type(exc).__name__]
    count = db.execute('SELECT attempts FROM jobs WHERE trade_id=?', (tid,)).fetchone()[0]
    delay = min(MAX_RETRY_MS, BASE_RETRY_MS * 2 ** min(count - 1, 7))
    with db:
        db.execute('UPDATE attempts SET status=?,finished_ms=?,reasons=? WHERE id=?',
                   (status, now, json.dumps(reasons), aid))
        db.execute('UPDATE jobs SET status=?,due_ms=?,reasons=? WHERE trade_id=?',
                   (status, now + delay, json.dumps(reasons), tid))


def step(journal, directory, exchange_factory, *, now_ms=None, max_trades=2):
    """Caller must hold lock and impose a wall deadline; capture itself is bounded."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not 1 <= max_trades <= 2:
        raise ValueError('invalid_batch_limit')
    with sqlite3.connect(directory / 'queue.db', timeout=2) as db:
        initialize(db)
        # At most one unfinished capture can exist under the exclusive process lock.
        for attempt in db.execute("SELECT id,trade_id,path FROM attempts WHERE status='running' LIMIT 2").fetchall():
            finish(db, attempt, now)
        capacity = MAX_JOBS - db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        cursor = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]
        with connect(journal) as src:
            rows = src.execute('SELECT id,trade_id FROM trade_accounting_bookings WHERE id>? ORDER BY id LIMIT 100',
                               (cursor,)).fetchall()
        scanned = 0
        with db:
            for receipt_id, tid in rows:
                exists = db.execute('SELECT 1 FROM jobs WHERE trade_id=?', (tid,)).fetchone()
                if not exists and capacity <= 0:
                    break
                if not exists:
                    db.execute("INSERT INTO jobs(trade_id,status,due_ms) VALUES (?,'pending',?)", (tid, now))
                    capacity -= 1
                db.execute("UPDATE meta SET value=? WHERE key='cursor'", (receipt_id,))
                scanned += 1
        capacity_blocked = scanned < len(rows)
        attempted = 0
        storage_blocked = False
        for (tid,) in db.execute("SELECT trade_id FROM jobs WHERE status!='complete' AND due_ms<=? ORDER BY due_ms,trade_id LIMIT ?",
                                 (now, max_trades)).fetchall():
            used = sum(p.stat().st_size for p in directory.glob('*.json'))
            if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= MAX_ATTEMPTS or used + MAX_ARTIFACT > MAX_BYTES:
                storage_blocked = True
                break
            with connect(journal) as src:
                source = booking.export(src, tid)
                trade = source['trade']
                overlaps = [r[0] for r in src.execute(
                    'SELECT id FROM trades WHERE symbol=? AND id<>? AND (closed_at IS NULL OR closed_at>=?)',
                    (trade['symbol'], tid, trade.get('opened_at') or ''))]
            if trade['status'] != 'closed':
                with db:
                    db.execute("UPDATE jobs SET status='waiting_close',due_ms=?,reasons=? WHERE trade_id=?",
                               (now + BASE_RETRY_MS, '["natural_close_pending"]', tid))
                continue
            aid = uuid.uuid4().hex
            path = directory.resolve() / (aid + '.json')
            with db:
                db.execute("INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES (?,?,?,'running',?)",
                           (aid, tid, str(path), now))
                db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE trade_id=?", (tid,))
            # Network initialization is lazy: an empty queue makes no venue calls.
            try:
                artifact = A.capture(source, exchange_factory(), overlaps)
                A.replay(artifact)
                encoded = json.dumps(artifact, sort_keys=True, allow_nan=False).encode()
                if len(encoded) > MAX_ARTIFACT:
                    raise ValueError('artifact_capacity_retry')
                with path.open('xb') as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception as exc:
                # Never record exception messages (URLs or credentials may appear).
                # Leave any partially written file intact for diagnosis.
                with db:
                    db.execute('UPDATE attempts SET reasons=? WHERE id=?',
                               (json.dumps(['capture_failed:' + type(exc).__name__]), aid))
            finish(db, (aid, tid, str(path)), int(time.time()*1000) if now_ms is None else now)
            attempted += 1
        counts = dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status'))
        return dict(schema_version='accounting-worker-health.v1', updated_ms=int(time.time()*1000),
                    status='capacity_retry' if capacity_blocked or storage_blocked else 'ok',
                    scanned_receipts=scanned, attempted=attempted, jobs=counts,
                    capacity_blocked=capacity_blocked, storage_blocked=storage_blocked,
                    automatic_memory_import=False)


class Deadline(BaseException):
    pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--once', action='store_true')
    p.add_argument('--enable', action='store_true')
    p.add_argument('--journal', type=Path, default=Path('data/luffy.db'))
    p.add_argument('--directory', type=Path, default=Path('data/accounting-worker'))
    args = p.parse_args()
    if not args.once or not args.enable:
        p.error('--once --enable required')
    from trader.core.config import Env
    if Env.get('BINANCE_DEMO', 'true').lower() not in ('true', '1', 'yes'):
        p.error('demo only')
    args.directory.mkdir(parents=True, exist_ok=True)
    with (args.directory / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'lock_busy'}))
            return
        def deadline(signum, frame):
            raise Deadline()
        previous = signal.signal(signal.SIGALRM, deadline)
        signal.alarm(45)
        try:
            from trader.data.feed import make_exchange
            def exchange():
                ex = make_exchange('futures', demo=True)
                ex.timeout = 5000
                return ex
            health = step(args.journal, args.directory, exchange)
        except Deadline:
            health = dict(status='deadline_retry', updated_ms=int(time.time()*1000))
        except Exception as exc:
            health = dict(status='worker_error_retry', error_type=type(exc).__name__, updated_ms=int(time.time()*1000))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        temp = args.directory / 'health.tmp'
        temp.write_text(json.dumps(health, sort_keys=True))
        temp.replace(args.directory / 'health.json')
        print(json.dumps(health, sort_keys=True))


if __name__ == '__main__':
    main()
