"""Write transactions on data/candles.db that never outlive a failure.

The stores keep one sqlite connection per thread. Python's sqlite3 opens a
transaction implicitly on the first INSERT; when the COMMIT then fails with
"database is locked", SQLite leaves that transaction OPEN — and the
connection keeps the PENDING lock it took while waiting to commit. In the
DELETE journal mode candles.db runs in, a PENDING lock refuses every new
reader and writer on every connection, including after whoever caused the
original contention has gone. The thread-local connection held it until
that same thread next happened to commit, so one busy moment became a
self-sustaining lock episode, and the next successful commit on the thread
silently committed the batch its caller had been told failed.

`write_tx` ends every write in exactly one of two states: committed, or
rolled back with no transaction open. If the rollback itself fails the
connection is closed (releasing its locks) and dropped from thread-local
storage, so the next call opens a fresh one. Cleanup swallows anything it
raises — including KeyboardInterrupt/SystemExit — so the original exception
is always the one re-raised; callers keep their own warnings.

No retry: once the transaction is gone the lock is gone, and the stores'
writes are idempotent re-writes (INSERT OR REPLACE on the primary key)
that the next refresh repeats anyway.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from itertools import islice
from contextlib import contextmanager

log = logging.getLogger(__name__)

#: sqlite3's own default busy timeout, stated explicitly so it is visible
#: and cannot drift. Contention is handled by releasing locks, not by
#: waiting longer.
BUSY_TIMEOUT_S = 5.0


def insert_rows(conn, prefix, rows, width):
    """Bounded VALUES statements inside the caller's atomic transaction.

    Unlike executemany's per-row SQLite crossings, one statement does each
    bounded group while retaining input order, conflict policy and rollback.
    All current producers use at most nine columns: 64 rows fit even the
    historical 999-variable SQLite bound. prefix is code-owned SQL only.
    """
    iterator = iter(rows)
    while batch := list(islice(iterator, 64)):
        conn.execute(prefix + ' VALUES ' + ','.join(
            '(' + ','.join('?' for _ in range(width)) + ')' for _ in batch),
            tuple(value for row in batch for value in row))


def close_quietly(conn: sqlite3.Connection, label: str = "") -> None:
    """Close `conn` during failure cleanup. Never raises — not even
    KeyboardInterrupt/SystemExit — so the caller's original exception is
    the one that propagates. Only for cleanup paths that re-raise."""
    try:
        conn.close()
    except BaseException as e:                  # cleanup must not mask
        log.error(f"candle store {label}: close of failed connection "
                  f"interrupted ({type(e).__name__}: {e})")


def discard(local: threading.local, conn: sqlite3.Connection,
            label: str = "") -> None:
    """Forget `conn` first — so this thread's next call reconnects whatever
    happens next — then close it. Never raises."""
    if getattr(local, "conn", None) is conn:
        local.conn = None
    close_quietly(conn, label)


def abort(local: threading.local, conn: sqlite3.Connection,
          label: str = "") -> None:
    """Leave `conn` with no open transaction, or discard it. Never raises.

    Only a rollback that completes and leaves no transaction open keeps the
    connection; a rollback that raises anything (including an interrupt) or
    leaves a transaction open gets it evicted and closed."""
    try:
        if conn.in_transaction:
            conn.rollback()
        if not conn.in_transaction:
            return
        log.error(f"candle store {label}: rollback left a transaction open;"
                  " discarding connection")
    except BaseException as e:                  # cleanup must not mask
        log.error(f"candle store {label}: rollback failed "
                  f"({type(e).__name__}: {e}); discarding connection")
    discard(local, conn, label)


@contextmanager
def write_tx(local: threading.local, conn: sqlite3.Connection,
             label: str = ""):
    """Run the body's writes on `conn`, then commit. On any failure — in the
    body or the commit — roll back (or discard the connection) and re-raise.
    """
    try:
        yield conn
        conn.commit()
    except BaseException:
        abort(local, conn, label)
        raise
