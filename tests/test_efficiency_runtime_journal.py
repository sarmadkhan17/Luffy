"""SP-01: slow Journal transaction phases. Fixture SQLite under tmp_path only."""
import json
import logging
import sqlite3
import threading
import time

import pytest

from trader.core import journal as jmod
from trader.core.journal import Journal


@pytest.fixture
def traced(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv('LUFFY_TRACE_SLOW_TRANSACTIONS', '1')
    monkeypatch.setattr(jmod, 'SLOW_TX_S', 0.0)
    caplog.set_level(logging.WARNING, logger=jmod.__name__)
    j = Journal(tmp_path / 'j.db')
    with j._tx() as c:
        c.execute('CREATE TABLE IF NOT EXISTS t(a TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS parent(id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS child(p INTEGER REFERENCES parent(id) '
                  'DEFERRABLE INITIALLY DEFERRED)')
    caplog.clear()
    return j


def reports(caplog):
    return [json.loads(r.getMessage().split('slow journal transaction ', 1)[1])
            for r in caplog.records if 'slow journal transaction' in r.getMessage()]


def test_mutex_wait_is_measured_before_the_lock_and_separate_from_body(traced, caplog):
    holder_in, release = threading.Event(), threading.Event()

    def hold():
        with traced._write_lock:
            holder_in.set()
            release.wait(5)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    holder_in.wait(5)
    threading.Timer(0.25, release.set).start()
    with traced._tx() as c:
        c.execute('INSERT INTO t VALUES (?)', ('x',))
    t.join(5)
    (r,) = reports(caplog)
    assert r['mutex_wait_s'] >= 0.2 and r['body_s'] < 0.2 and r['begin_s'] is None
    assert r['total_s'] >= r['mutex_wait_s']


def test_explicit_begin_immediate_wait_is_distinguished_from_body(traced, caplog):
    other = sqlite3.connect(str(traced.db_path), timeout=5, isolation_level=None, check_same_thread=False)
    other.execute('BEGIN IMMEDIATE')                      # another connection owns the writer
    threading.Timer(0.3, lambda: other.execute('COMMIT')).start()
    with traced._tx() as c:
        c.execute('BEGIN IMMEDIATE')                      # Risk._durable_hold's statement
        c.execute('INSERT INTO t VALUES (?)', ('x',))
    other.close()
    (r,) = reports(caplog)
    assert r['begin_s'] >= 0.25 and r['body_s'] < 0.2 and r['mutex_wait_s'] < 0.2


def test_body_time_without_explicit_begin_reports_none_not_zero(traced, caplog):
    with traced._tx() as c:
        time.sleep(0.12)
        c.execute('INSERT INTO t VALUES (?)', ('x',))
    (r,) = reports(caplog)
    assert r['begin_s'] is None and r['body_s'] >= 0.1
    assert r['commit_s'] is not None and r['cleanup_s'] is None
    assert set(r) >= {'pid', 'thread', 'connection', 'callers', 'total_s'}
    assert 0 < len(r['callers']) <= 6 and all(not x.startswith(('journal.py:', 'contextlib.py:')) for x in r['callers'])


def test_body_failure_keeps_exception_rolls_back_and_reports_cleanup(traced, caplog):
    class Boom(Exception):
        pass
    with pytest.raises(Boom):
        with traced._tx() as c:
            c.execute('INSERT INTO t VALUES (?)', ('x',))
            raise Boom
    assert traced.query('SELECT COUNT(*) n FROM t')[0]['n'] == 0
    (r,) = reports(caplog)
    assert r['cleanup_s'] is not None and r['commit_s'] is None


def test_commit_failure_is_separate_original_error_and_rolled_back(traced, caplog):
    with pytest.raises(sqlite3.IntegrityError):
        with traced._tx() as c:
            c.execute('INSERT INTO child VALUES (99)')    # deferred FK fails at COMMIT
    assert traced.query('SELECT COUNT(*) n FROM child')[0]['n'] == 0
    (r,) = reports(caplog)
    assert r['commit_s'] is not None and r['cleanup_s'] is not None
    with traced._tx() as c:                                # connection stays usable
        c.execute('INSERT INTO t VALUES (?)', ('ok',))
    assert traced.query('SELECT COUNT(*) n FROM t')[0]['n'] == 1


def test_report_carries_no_sql_arguments_or_statement_text(traced, caplog):
    with traced._tx() as c:
        c.execute('INSERT INTO t VALUES (?)', ('SECRET-VALUE-123',))
    text = ' '.join(r.getMessage() for r in caplog.records)
    assert 'SECRET-VALUE-123' not in text and 'INSERT' not in text


def test_reporting_cannot_recurse_through_a_journal_writing_handler(traced, caplog):
    seen = []

    class Writer(logging.Handler):
        def emit(self, record):
            seen.append(record)
            with traced._tx() as c:                        # would recurse without the guard
                c.execute('INSERT INTO t VALUES (?)', ('from-handler',))

    handler = Writer(level=logging.WARNING)
    logging.getLogger(jmod.__name__).addHandler(handler)
    try:
        with traced._tx() as c:
            c.execute('INSERT INTO t VALUES (?)', ('x',))
    finally:
        logging.getLogger(jmod.__name__).removeHandler(handler)
    assert len(seen) == 1
    assert traced.query('SELECT COUNT(*) n FROM t')[0]['n'] == 2


def test_trace_off_adds_no_log_and_no_connection_subclass(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv('LUFFY_TRACE_SLOW_TRANSACTIONS', raising=False)
    monkeypatch.setattr(jmod, 'SLOW_TX_S', 0.0)
    caplog.set_level(logging.WARNING, logger=jmod.__name__)
    j = Journal(tmp_path / 'j.db')
    with j._tx() as c:
        assert type(c) is sqlite3.Connection
        c.execute('CREATE TABLE IF NOT EXISTS t(a TEXT)')
    assert not reports(caplog)


def test_fast_transaction_below_threshold_is_not_reported(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv('LUFFY_TRACE_SLOW_TRANSACTIONS', '1')
    caplog.set_level(logging.WARNING, logger=jmod.__name__)
    j = Journal(tmp_path / 'j.db')
    with j._tx() as c:
        c.execute('CREATE TABLE IF NOT EXISTS t(a TEXT)')
    assert not reports(caplog)


def test_implicit_begin_lock_wait_is_body_time_with_begin_unknown_not_zero(traced, caplog):
    other = sqlite3.connect(str(traced.db_path), timeout=5, isolation_level=None, check_same_thread=False)
    other.execute('BEGIN IMMEDIATE')                      # second fixture writer
    threading.Timer(0.3, lambda: other.execute('COMMIT')).start()
    with traced._tx() as c:
        c.execute('INSERT INTO t VALUES (?)', ('implicit',))   # Python opens BEGIN itself, blocking here
    other.close()
    (r,) = reports(caplog)
    assert r['begin_s'] is None and r['begin_s'] != 0          # unobservable, never fabricated
    assert r['body_s'] >= 0.25 and r['mutex_wait_s'] < 0.2     # the wait is attributed to the body
    assert traced.query('SELECT COUNT(*) n FROM t')[0]['n'] == 1   # SQL semantics unchanged
