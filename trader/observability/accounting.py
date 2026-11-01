"""Off-path demo accounting queue. No order writes. Completed receipts are
delivered to Learning through trader.learning.producers (journal writes).

One process owns the queue lock. Attempts are reserved durably before capture;
crash recovery replays any complete file before deciding to retry. Evidence is
never overwritten/deleted. Capacity exhaustion pauses work explicitly.
"""
from __future__ import annotations

import argparse
from contextlib import closing
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
MAX_CONFLICT_ATTEMPTS = 3
TERMINAL_STATUSES = ('complete', 'unsupported_scope', 'conflict')
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
        status = ('complete' if result['learning_eligible'] else 'leg_complete'
                  if artifact.get('schema_version') == A.HEDGE_LEG_VERSION and result.get('leg_verified') is True
                  else 'retry')
        reasons = result['reasons']
    except Exception as exc:
        prior = db.execute('SELECT reasons FROM attempts WHERE id=?', (aid,)).fetchone()
        status = 'retry'
        reasons = json.loads(prior[0]) + ['attempt_unavailable_retry:' + type(exc).__name__]
    count = db.execute('SELECT attempts FROM jobs WHERE trade_id=?', (tid,)).fetchone()[0]
    if (status == 'retry' and count >= MAX_CONFLICT_ATTEMPTS and reasons
            and all(isinstance(r, str) and not r.endswith('_retry')
                    and not r.startswith(('attempt_unavailable_retry', 'capture_failed'))
                    for r in reasons)):
        # trade_accounting names every transient reason `*_retry`; a reason
        # without that suffix (ownership/clock/balance conflict, unsupported
        # scope) is a statement about the evidence, not about timing. Retrying
        # it forever only spends the global attempt budget and venue reads.
        # Evidence is retained; the job leaves the due queue and stays visible
        # in the health counts.
        status = 'conflict'
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
    with closing(sqlite3.connect(directory / 'queue.db', timeout=2)) as db, db:
        initialize(db)
        # At most one unfinished capture can exist under the exclusive process lock.
        for attempt in db.execute("SELECT id,trade_id,path FROM attempts WHERE status='running' LIMIT 2").fetchall():
            finish(db, attempt, now)
        capacity = MAX_JOBS - db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        cursor = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]
        with closing(connect(journal)) as src, src:
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
        for (tid,) in db.execute("SELECT trade_id FROM jobs WHERE status NOT IN ('complete','leg_complete','unsupported_scope','conflict') AND due_ms<=? ORDER BY due_ms,trade_id LIMIT ?",
                                 (now, max_trades)).fetchall():
            used = sum(p.stat().st_size for p in directory.glob('*.json'))
            if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= MAX_ATTEMPTS or used + MAX_ARTIFACT > MAX_BYTES:
                storage_blocked = True
                break
            with closing(connect(journal)) as src, src:
                source = booking.export(src, tid)
                trade = source['trade']
                from trader.engine.position_mode import position_side
                recorded = dict(src.execute('SELECT * FROM trades WHERE id=?', (tid,)).fetchone())
                native = position_side(recorded)
                flat = None
                if native != 'BOTH':
                    from types import SimpleNamespace
                    from trader.engine.coordinated_close import retained_terminal_flat
                    root = json.loads(recorded.get('entry_identity_json') or '{}')
                    group_id = (root.get('coordinated') or {}).get('group_id')
                    if group_id and trade['status'] == 'closed':
                        reader = SimpleNamespace(query=lambda sql, params=():[dict(r) for r in src.execute(sql, params)])
                        flat = retained_terminal_flat(reader, tid, group_id, A.legs(source)[-1]['order_id'])
                overlaps = [r[0] for r in src.execute(
                    'SELECT id FROM trades WHERE symbol=? AND id<>? AND (closed_at IS NULL OR closed_at>=?) '
                    'AND (? IS NULL OR opened_at<=?)',
                    (trade['symbol'], tid, trade.get('opened_at') or '', trade.get('closed_at'), trade.get('closed_at')))]
            if trade['status'] != 'closed':
                with db:
                    db.execute("UPDATE jobs SET status='waiting_close',due_ms=?,reasons=? WHERE trade_id=?",
                               (now + BASE_RETRY_MS, '["natural_close_pending"]', tid))
                continue
            if trade.get('exec_mode') != 'live' or trade.get('market_type') != 'futures':
                # Out of scope (e.g. paper): terminal, no venue construction, no attempts.
                with db:
                    db.execute("UPDATE jobs SET status='unsupported_scope',reasons=? WHERE trade_id=?",
                               ('["unsupported_trade_scope"]', tid))
                continue
            aid = uuid.uuid4().hex
            path = directory.resolve() / (aid + '.json')
            with db:
                db.execute("INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES (?,?,?,'running',?)",
                           (aid, tid, str(path), now))
                db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE trade_id=?", (tid,))
            # Network initialization is lazy: an empty queue makes no venue calls.
            try:
                artifact = (A.capture(source, exchange_factory(), overlaps, position_side=native, terminal_flat=flat)
                            if native != 'BOTH' else A.capture(source, exchange_factory(), overlaps))
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
        learning = deliver_completed(db, journal)
        counts = dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status'))
        return dict(schema_version='accounting-worker-health.v1', updated_ms=int(time.time()*1000),
                    status='capacity_retry' if capacity_blocked or storage_blocked else 'ok',
                    scanned_receipts=scanned, attempted=attempted, jobs=counts,
                    capacity_blocked=capacity_blocked, storage_blocked=storage_blocked,
                    automatic_memory_import=False, learning_delivery=learning)


def deliver_completed(db, journal):
    from trader.learning import producers
    db.execute('CREATE TABLE IF NOT EXISTS learning_deliveries(attempt_id TEXT PRIMARY KEY,outcome_id TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS learning_delivery_waits(attempt_id TEXT PRIMARY KEY,due_ms INTEGER NOT NULL)')
    report=dict(delivered=0,retry=0,unbound=0)
    now=int(time.time()*1000)
    groups={}
    rows=db.execute("SELECT id,path FROM attempts WHERE status IN ('complete','leg_complete') AND id NOT IN (SELECT attempt_id FROM learning_deliveries) "
        "AND id NOT IN (SELECT attempt_id FROM learning_delivery_waits WHERE due_ms>?) ORDER BY started_ms,id LIMIT 2",(now,)).fetchall()
    for aid,path in rows:
        grouped, source = False, None
        try:
            artifact=json.loads(Path(path).read_text())
            grouped,source=group_source(db,journal,artifact)
            if grouped and source is None:
                # A still-open or not-yet-reconciled group must not sit at the
                # head of the delivery queue and starve unrelated outcomes.
                with db:db.execute('INSERT OR REPLACE INTO learning_delivery_waits VALUES (?,?)',(aid,now+BASE_RETRY_MS))
                report['retry']+=1
                continue
            group_id=source['closure']['plan']['group_id'] if grouped else None
            if group_id in groups:
                with db:db.execute('INSERT INTO learning_deliveries VALUES (?,?)',(aid,groups[group_id]))
                continue
            oid=(producers.deliver_group_accounting(journal,source) if grouped
                 else producers.deliver_accounting(journal,artifact))
            if oid is None:
                report['retry']+=1
                continue
            with db:
                db.execute('INSERT INTO learning_deliveries VALUES (?,?)',(aid,oid))
                db.execute('DELETE FROM learning_delivery_waits WHERE attempt_id=?',(aid,))
            if grouped:groups[group_id]=oid
            report['delivered']+=1
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # A corrupt artifact is terminal for delivery too, but its message
            # is not stored: record only the exception type.
            with db:
                db.execute('INSERT INTO learning_deliveries VALUES (?,?)',(aid,'UNBOUND:artifact_unreadable:'+type(exc).__name__))
            report['unbound']+=1
        except ValueError as exc:
            # Missing historical identity is terminal, not a retry that can
            # starve prospective receipts. Never create a historical registration.
            # Only a short reason code is stored, never free-form text.
            code = str(exc)
            if (grouped and source is not None and code in {
                    'native_group_funding_publication_pending', 'native_group_funding_history_changed',
                    'native_group_funding_events_changed'}):
                # A verified leg is not a final funding observation. Retain
                # its old artifact and schedule bounded fresh captures; never
                # turn a publication delay into permanent UNBOUND learning.
                with db:
                    for member in source['closure']['plan']['members']:
                        tid = member['trade_id']
                        db.execute("UPDATE attempts SET status='retry' WHERE trade_id=? AND status='leg_complete'", (tid,))
                        db.execute("UPDATE jobs SET status='retry',due_ms=?,reasons=? WHERE trade_id=?",
                            (now+BASE_RETRY_MS, json.dumps([code]), tid))
                    db.execute('INSERT OR REPLACE INTO learning_delivery_waits VALUES (?,?)', (aid, now+BASE_RETRY_MS))
                report['retry'] += 1
                continue
            if not (0 < len(code) <= 80 and all(c.islower() or c.isdigit() or c == '_' for c in code)):
                code = 'value_error'
            with db:
                db.execute('INSERT INTO learning_deliveries VALUES (?,?)',(aid,'UNBOUND:'+code))
            report['unbound']+=1
        except Exception:
            report['retry']+=1
    return report


def group_source(queue,journal,artifact):
    """Reuse completed captures; never fetch a second copy or learn per leg."""
    from trader.engine.coordinated_accounting import VERSION
    with closing(connect(journal)) as src,src:
        src.row_factory=sqlite3.Row
        trade_id=artifact['bookings']['trade']['id']
        row=src.execute('SELECT entry_identity_json FROM trades WHERE id=?',(trade_id,)).fetchone()
        identity=json.loads(row[0] or '{}') if row else {}
        group=identity.get('coordinated')
        if not group:return False,None
        if not src.execute("SELECT 1 FROM sqlite_master WHERE name='coordinated_group_unwinds'").fetchone():
            return True,None
        row=src.execute('SELECT * FROM coordinated_group_unwinds WHERE group_id=?',(group['group_id'],)).fetchone()
        if not row or row['state']!='CLOSED':return True,None
        plan=json.loads(row['plan_json']);parts=[]
        for member in plan['members']:
            complete=queue.execute("SELECT path FROM attempts WHERE trade_id=? AND status IN ('complete','leg_complete') "
                "ORDER BY finished_ms,id LIMIT 1",(member['trade_id'],)).fetchone()
            if not complete:return True,None
            capture=json.loads(Path(complete[0]).read_text())
            trade=src.execute('SELECT entry_identity_json FROM trades WHERE id=?',(member['trade_id'],)).fetchone()
            if not trade:raise ValueError('coordinated_accounting_member_missing')
            identity=json.loads(trade[0]);binding=identity['execution_binding']
            request=src.execute('SELECT * FROM execution_requests WHERE logical_id=?',(binding['logical_id'],)).fetchone()
            if not request:raise ValueError('coordinated_accounting_entry_missing')
            parts.append(dict(capture=capture,identity=identity,entry_request=dict(
                logical_id=request['logical_id'],intent=json.loads(request['intent_json']),
                intent_hash=request['intent_hash'],state=request['state'],order_id=request['order_id'],
                result=json.loads(request['result_json'] or '{}'))))
        return True,dict(schema_version=VERSION,observed_ms=int(time.time()*1000),
            closure=dict(plan=plan,plan_sha256=row['plan_sha256'],state=row['state']),components=parts)


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
