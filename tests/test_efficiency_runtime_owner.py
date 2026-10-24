"""SP-03: one bounded audit flusher per OwnerService lifetime. Fixture SQLite only."""
import sqlite3
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

from trader.core import journal as jmod
from trader.core.journal import Journal
from trader.engine.state import ControlStateMachine
from trader.owner.authz import Authorizer, Principal
from trader.owner.contract import OPERATIONS, OwnerRequest, new_request_id
from trader.owner.ipc import InsecurePath, OwnerIPCServer
from trader.owner.service import OwnerService

TESTER = Principal("tester", frozenset(OPERATIONS), frozenset({"test"}))


def req(op="status"):
    return OwnerRequest(new_request_id("test"), op, "test", "fixture-owner",
                        issued_at=time.time(), request_ref="ref")


def flushers():
    return [t for t in threading.enumerate() if t.name == "owner-audit-flush" and t.is_alive()]


def audit_count(path):
    with sqlite3.connect(str(path)) as c:
        return c.execute("SELECT COUNT(*) FROM owner_audit").fetchone()[0]


def wait_for(predicate, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class Held:
    """Another connection owning the SQLite writer, like another process."""

    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.conn.execute("BEGIN IMMEDIATE")

    def release(self):
        self.conn.execute("COMMIT")
        self.conn.close()


@pytest.fixture(autouse=True)
def short_busy_timeout(monkeypatch):
    """The journal waits 30 s for a held writer; fixtures wait 0.25 s instead."""
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: real(*a, **{**k, "timeout": min(k.get("timeout", 5), 0.25)}))


@pytest.fixture
def service(tmp_path):
    j = Journal(tmp_path / "j.db")
    s = OwnerService(j, ControlStateMachine(j), resume=lambda *a, **k: None)
    s.authorizer.principals["tester"] = TESTER
    s.authorizer.bind("test", "fixture-owner", "tester")
    s.audit_flush_interval_s = 0.05
    yield s
    s.stop_audit_flusher(timeout_s=3)
    assert wait_for(lambda: not flushers()), "leaked audit flusher"


def test_reads_enqueue_without_waiting_for_a_held_writer_then_commit_once(service, tmp_path):
    assert service.start_audit_flusher() is True
    held = Held(service.journal.db_path)
    try:
        started = time.monotonic()
        for _ in range(5):
            assert service.execute(req()).status == "ACCEPTED"
        assert time.monotonic() - started < 1.0          # no read waited for the writer
        assert service.audit_status()["remaining"] >= 1
    finally:
        held.release()
    assert wait_for(lambda: service.audit_status()["remaining"] == 0)
    assert audit_count(service.journal.db_path) == 5      # exactly once each
    assert service.audit_status()["flushed_total"] == 5


def test_inline_flush_by_size_is_skipped_while_a_flusher_owns_the_service(service):
    service.start_audit_flusher()
    service.audit_flush_interval_s = 60                   # the loop will not wake
    service.audit_flush_rows = 2
    held = Held(service.journal.db_path)
    try:
        started = time.monotonic()
        for _ in range(4):
            service.execute(req())
        assert time.monotonic() - started < 1.0
    finally:
        held.release()


def test_reads_never_flush_inline_even_without_a_flusher_or_when_due(service):
    service.audit_flush_rows = 2
    held = Held(service.journal.db_path)
    try:
        started = time.monotonic()
        for _ in range(6):
            service.execute(req())
        assert time.monotonic() - started < 1.0
    finally:
        held.release()
    assert audit_count(service.journal.db_path) == 0 and service.audit_status()["remaining"] == 6
    assert service.flush_audit() is True                    # explicit callers still can
    assert audit_count(service.journal.db_path) == 6


def test_stopping_or_stopped_flusher_does_not_revert_reads_to_inline_writes(service):
    service.start_audit_flusher()
    service.audit_flush_rows = 1
    assert service.stop_audit_flusher(timeout_s=3)["drained"] is True
    held = Held(service.journal.db_path)
    try:
        started = time.monotonic()
        for _ in range(3):
            service.execute(req())
        assert time.monotonic() - started < 1.0
    finally:
        held.release()
    assert audit_count(service.journal.db_path) == 0 and not flushers()


def test_failing_flusher_leaves_reads_nonblocking_and_buffered(service, monkeypatch):
    service.audit_flush_rows = 1
    service.start_audit_flusher()
    real_tx = service.journal._tx

    def failing():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(service.journal, "_tx", failing)
    started = time.monotonic()
    for _ in range(3):
        service.execute(req())
    assert time.monotonic() - started < 1.0
    assert wait_for(lambda: service.audit_status()["flush_failures"] >= 1)
    assert service.audit_status()["remaining"] == 3
    monkeypatch.setattr(service.journal, "_tx", real_tx)
    assert wait_for(lambda: audit_count(service.journal.db_path) == 3)


def test_failing_ipc_startup_does_not_stop_direct_service_flushing(service, tmp_path):
    service.start_audit_flusher()
    server = OwnerIPCServer(service, tmp_path / "ipc-absent" / "x")
    with pytest.raises(Exception):
        server.start()                                    # unusable runtime dir
    service.execute(req())
    assert wait_for(lambda: audit_count(service.journal.db_path) == 1)
    assert len(flushers()) == 1


def test_disabled_ipc_direct_reads_under_held_writer_recover(service):
    service.start_audit_flusher()                         # no IPC server exists at all
    held = Held(service.journal.db_path)
    try:
        service.execute(req())
        service.execute(req())
        time.sleep(0.4)                                   # flusher blocked, rows stay queued
        assert service.audit_status()["remaining"] == 2
    finally:
        held.release()
    assert wait_for(lambda: audit_count(service.journal.db_path) == 2)
    assert service.audit_status()["flush_failures"] >= 1  # the blocked attempts were counted


def test_duplicate_start_and_per_request_calls_never_add_threads(service):
    before = threading.active_count()
    assert service.start_audit_flusher() is True
    assert service.start_audit_flusher() is False
    for _ in range(40):
        service.execute(req())
    assert len(flushers()) == 1 and threading.active_count() == before + 1


def test_ipc_enabled_reuses_the_service_flusher_without_duplicate(service):
    base = Path(tempfile.mkdtemp(prefix="ipc", dir="/tmp"))   # sticky ancestor: passes the IPC ancestry rule
    ipc_dir = base / "d"
    ipc_dir.mkdir(mode=0o700)
    try:
        service.start_audit_flusher()
        server = OwnerIPCServer(service, ipc_dir)
        server.start()
        assert len(flushers()) == 1 and server._owns_flusher is False
        server.stop()
        assert len(flushers()) == 1                       # service lifetime, not transport's
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_ipc_alone_starts_and_stops_a_single_flusher_it_owns(service):
    base = Path(tempfile.mkdtemp(prefix="ipc", dir="/tmp"))   # sticky ancestor: passes the IPC ancestry rule
    ipc_dir = base / "d"
    ipc_dir.mkdir(mode=0o700)
    try:
        server = OwnerIPCServer(service, ipc_dir)
        server.start()
        assert len(flushers()) == 1 and server._owns_flusher is True
        server.stop()
        assert wait_for(lambda: not flushers())
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_overflow_drops_oldest_and_counts_then_records_the_drop(service):
    service.audit_buffer_max = 3
    held = Held(service.journal.db_path)
    try:
        for _ in range(5):
            service.execute(req())
        status = service.audit_status()
        assert status["remaining"] == 3 and status["dropped_total"] == 2
        assert status["dropped_unrecorded"] == 2
    finally:
        held.release()
    assert service.flush_audit() is True
    status = service.audit_status()
    assert status["remaining"] == 0 and status["dropped_unrecorded"] == 0
    assert status["dropped_total"] == 2
    with sqlite3.connect(str(service.journal.db_path)) as c:
        rows = c.execute("SELECT status, reasons FROM owner_audit").fetchall()
    assert len(rows) == 4 and any(s == "AUDIT_DROPPED" and "dropped:2" in r for s, r in rows)


def test_failed_flush_keeps_rows_and_counts_then_retries(service, monkeypatch):
    service.execute(req())
    real_tx = service.journal._tx
    calls = []

    def failing_tx():
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(service.journal, "_tx", failing_tx)
    assert service.flush_audit() is False
    assert service.audit_status()["remaining"] == 1 and service.audit_status()["flush_failures"] == 1
    monkeypatch.setattr(service.journal, "_tx", real_tx)
    assert service.flush_audit() is True
    assert audit_count(service.journal.db_path) == 1 and service.audit_status()["remaining"] == 0


def test_bounded_shutdown_never_claims_drain_while_the_writer_is_held(service):
    service.start_audit_flusher()
    held = Held(service.journal.db_path)
    try:
        for _ in range(3):
            service.execute(req())
        time.sleep(0.15)
        started = time.monotonic()
        status = service.stop_audit_flusher(timeout_s=0.2)
        assert time.monotonic() - started < 1.5           # finite bound
        assert status["drained"] is False and status["remaining"] == 3
    finally:
        held.release()
    assert wait_for(lambda: not flushers())
    final = service.stop_audit_flusher(timeout_s=2)
    assert final["drained"] is True and final["remaining"] == 0
    assert audit_count(service.journal.db_path) == 3


def test_a_stopped_service_can_start_a_new_flusher_but_not_while_one_is_alive(service):
    service.start_audit_flusher()
    assert service.stop_audit_flusher(timeout_s=3)["drained"] is True
    assert service.start_audit_flusher() is True
    assert len(flushers()) == 1


def test_control_refusal_receipt_is_durable_synchronously_even_with_a_flusher(service):
    service.start_audit_flusher()
    service.audit_flush_interval_s = 60
    denied = OwnerRequest(new_request_id("test"), "freeze", "test", "stranger",
                          issued_at=time.time(), request_ref="ref")
    result = service.execute(denied)
    assert result.status != "ACCEPTED"
    assert audit_count(service.journal.db_path) >= 1       # no flusher pass was needed


def test_accepted_control_receipt_is_committed_before_return_not_queued(service):
    service.start_audit_flusher()
    service.audit_flush_interval_s = 60                   # a flusher pass cannot explain the row
    result = service.execute(OwnerRequest(new_request_id("test"), "freeze", "test", "fixture-owner",
                                          issued_at=time.time(), request_ref="ref"))
    assert result.status == "ACCEPTED", result
    with sqlite3.connect(str(service.journal.db_path)) as c:
        rows = c.execute("SELECT status FROM owner_audit WHERE operation='freeze'").fetchall()
    assert rows == [("ACCEPTED",)] and service.audit_status()["remaining"] == 0
