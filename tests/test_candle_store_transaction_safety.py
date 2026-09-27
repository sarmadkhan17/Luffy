"""A failed candles.db write must not leave its transaction — or its lock — open.

Python's sqlite3 opens a transaction implicitly on the first INSERT. When
the COMMIT fails with "database is locked" the transaction stays open and
the connection keeps the PENDING lock it took while waiting to commit. In
DELETE journal mode (candles.db's mode) a PENDING lock refuses every new
reader and writer on every connection. DataFeed and RefStore keep one
connection per thread, and `_store_save` / `_note_floor` only logged the
failure and moved on, so the lock outlived whatever caused the contention
until the same thread happened to commit again — which then also committed
the batch its caller had been told failed.

Observed live on 2026-09-27 23:24–23:30: 36 "candle store write … database
is locked" and 37 "candle store read … database is locked" warnings, ~5 s
apart (sqlite3's default busy timeout), in a DELETE-mode candles.db. The
original holder is unknown; these tests pin the part that made it sticky.

Isolated temporary databases only.
"""
import contextlib
import sqlite3
import threading
import time

import pandas as pd
import pytest

from trader.data import sqlite_tx
from trader.data.feed import DataFeed
from trader.data.references import HOUR, RefStore

TF = "15m"
TF_MS = 900_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % TF_MS)
FAR_FUTURE = T0 + 10 ** 12          # every bar in these tests is closed
TIMEOUT = 0.2                       # busy timeout under test, seconds


@pytest.fixture(autouse=True)
def _short_busy_timeout(monkeypatch):
    monkeypatch.setattr(DataFeed, "BUSY_TIMEOUT_S", TIMEOUT)
    monkeypatch.setattr(RefStore, "BUSY_TIMEOUT_S", TIMEOUT)


def _bars(start_i: int, n: int, close0: float = 100.0) -> pd.DataFrame:
    ts = [T0 + (start_i + i) * TF_MS for i in range(n)]
    c = [close0 + i for i in range(n)]
    return pd.DataFrame({
        "ts": pd.to_datetime(ts, unit="ms", utc=True),
        "open": c, "high": [x + 1 for x in c], "low": [x - 1 for x in c],
        "close": c, "volume": [10.0] * n})


def _ref_bars(start_i: int, n: int) -> pd.DataFrame:
    ts = [T0 + (start_i + i) * 4 * HOUR for i in range(n)]
    c = [50.0 + i for i in range(n)]
    return pd.DataFrame({"ts": pd.to_datetime(ts, unit="ms", utc=True),
                         "open": c, "high": c, "low": c, "close": c,
                         "volume": [1.0] * n})


def _feed(path) -> DataFeed:
    f = DataFeed(exchange=object(), db_path=path)
    f.db                                            # create the tables
    return f


def _count(path, sql="SELECT COUNT(*) FROM candles") -> int:
    c = sqlite3.connect(path, timeout=TIMEOUT)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def _can_read(path) -> bool:
    """A fresh connection can take a SHARED lock (no PENDING/EXCLUSIVE held)."""
    c = sqlite3.connect(path, timeout=0.05)
    try:
        c.execute("SELECT COUNT(*) FROM candles").fetchone()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


def _can_write(path) -> bool:
    c = sqlite3.connect(path, timeout=0.05, isolation_level=None)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


class _Reader:
    """Another connection holding a SHARED lock: an INSERT still succeeds,
    the COMMIT cannot get EXCLUSIVE and fails "database is locked"."""

    def __init__(self, path):
        self.c = sqlite3.connect(path, isolation_level=None)
        self.c.execute("BEGIN")
        self.c.execute("SELECT COUNT(*) FROM candles").fetchone()

    def release(self):
        self.c.execute("COMMIT")
        self.c.close()


class FaultConn:
    """A real connection with injectable failures (sqlite3.Connection's own
    methods are read-only, so faults are injected by delegation)."""

    def __init__(self, real, commit=None, rollback=None, after_exec=None):
        self.real, self.closed = real, False
        self.fail_commit, self.fail_rollback = commit, rollback
        self.fail_after_exec = after_exec

    def __getattr__(self, name):
        return getattr(self.real, name)

    @property
    def in_transaction(self):
        return self.real.in_transaction

    def execute(self, *a):
        r = self.real.execute(*a)
        if self.fail_after_exec:
            raise self.fail_after_exec
        return r

    def executemany(self, *a):
        r = self.real.executemany(*a)
        if self.fail_after_exec:
            raise self.fail_after_exec
        return r

    def commit(self):
        if self.fail_commit:
            raise self.fail_commit
        return self.real.commit()

    def rollback(self):
        if self.fail_rollback:
            raise self.fail_rollback
        return self.real.rollback()

    def close(self):
        self.closed = True
        self.real.close()

    def real_closed(self) -> bool:
        try:
            self.real.execute("SELECT 1")
            return False
        except sqlite3.ProgrammingError:
            return True


LOCKED = sqlite3.OperationalError("database is locked")
IOERR = sqlite3.OperationalError("disk I/O error")


def _inject(owner, **faults) -> FaultConn:
    fc = FaultConn(owner._local.conn, **faults)
    owner._local.conn = fc
    return fc


# ── A. executemany succeeds, commit raises "database is locked" ────────────
def test_real_commit_lock_rolls_back_and_releases(tmp_path, caplog):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    holder = _Reader(path)
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    conn = feed._local.conn
    assert not conn.in_transaction
    assert "candle store write BTC/USDT 15m: database is locked" in caplog.text
    # the old code kept PENDING here: no one else could even read
    assert _can_read(path)
    holder.release()
    assert _can_write(path)
    assert _count(path) == 0                   # nothing half-committed


def test_injected_commit_lock_rolls_back(tmp_path):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, commit=LOCKED)
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert not fc.in_transaction and not fc.closed
    assert feed._local.conn is fc              # usable, so kept
    assert _can_write(path) and _count(path) == 0


# ── B. SQL raises after the transaction began ──────────────────────────────
def test_error_after_insert_rolls_back(tmp_path, caplog):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, after_exec=IOERR)
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert not fc.in_transaction
    assert "disk I/O error" in caplog.text     # not swallowed silently
    assert _can_write(path) and _count(path) == 0


# ── C. rollback itself raises → connection discarded, next call recovers ────
def test_failed_rollback_discards_connection(tmp_path, caplog):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, commit=LOCKED, rollback=IOERR)
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert fc.closed                           # its locks went with it
    assert getattr(feed._local, "conn", None) is None
    assert "rollback failed" in caplog.text
    assert "database is locked" in caplog.text  # original error still shown
    assert _can_write(path) and _count(path) == 0
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert feed._local.conn is not fc and _count(path) == 5


def test_rollback_leaving_transaction_open_discards(tmp_path):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, commit=LOCKED)
    fc.rollback = lambda: None                 # "succeeds", does nothing
    feed._store_save("BTC/USDT", TF, _bars(0, 3), now_ms=FAR_FUTURE)
    assert fc.closed and getattr(feed._local, "conn", None) is None
    assert _can_write(path) and _count(path) == 0


def test_write_tx_reraises_the_original_error():
    local = threading.local()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (k PRIMARY KEY)")
    local.conn = conn
    with pytest.raises(ZeroDivisionError):
        with sqlite_tx.write_tx(local, conn):
            conn.execute("INSERT INTO t VALUES (1)")
            1 / 0
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


# ── D. one writer fails, another proceeds afterwards ───────────────────────
def test_other_writer_proceeds_after_a_failure(tmp_path):
    path = str(tmp_path / "c.db")
    a, b = _feed(path), _feed(path)
    holder = _Reader(path)
    a._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    holder.release()
    b._store_save("ETH/USDT", TF, _bars(0, 4), now_ms=FAR_FUTURE)
    assert _count(path) == 4
    got = b._store_load("ETH/USDT", TF, 100)
    assert got is not None and len(got) == 4


# ── E. repeated failure does not accumulate locks ──────────────────────────
def test_repeated_failures_hold_nothing(tmp_path):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    holder = _Reader(path)
    for i in range(5):
        feed._store_save("BTC/USDT", TF, _bars(i * 10, 3), now_ms=FAR_FUTURE)
        assert not feed._local.conn.in_transaction
        assert _can_read(path)
    holder.release()
    assert _can_write(path) and _count(path) == 0


# ── F. success after a failed transaction commits only its own batch ────────
def test_success_after_failure_does_not_commit_the_failed_batch(tmp_path):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    holder = _Reader(path)
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)  # fails
    holder.release()
    feed._store_save("ETH/USDT", TF, _bars(0, 3), now_ms=FAR_FUTURE)  # ok
    assert _count(path, "SELECT COUNT(*) FROM candles "
                        "WHERE symbol='BTC/USDT'") == 0
    assert _count(path) == 3
    # and the caller's natural retry lands exactly once
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert _count(path, "SELECT COUNT(*) FROM candles "
                        "WHERE symbol='BTC/USDT'") == 5


# ── G. RefStore shares the database and the risk ───────────────────────────
def test_refstore_failure_raises_and_leaves_nothing_open(tmp_path):
    path = str(tmp_path / "c.db")
    _feed(path)
    store = RefStore(path)
    store.db
    holder = _Reader(path)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE)
    assert not store._local.conn.in_transaction
    assert _can_read(path)
    holder.release()
    assert _can_write(path)
    assert _count(path, "SELECT COUNT(*) FROM refs") == 0
    assert store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE) == 4
    assert store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE) == 4
    assert _count(path, "SELECT COUNT(*) FROM refs") == 4


def test_refstore_failed_rollback_reconnects(tmp_path):
    path = str(tmp_path / "c.db")
    store = RefStore(path)
    store.db
    fc = _inject(store, commit=LOCKED, rollback=IOERR)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE)
    assert fc.closed and getattr(store._local, "conn", None) is None
    assert store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE) == 4


# ── H. note-floor failure cleanup ──────────────────────────────────────────
def test_note_floor_lock_rolls_back(tmp_path, caplog):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    holder = _Reader(path)
    df = _bars(0, 3)
    assert feed._note_floor("BTC/USDT", TF, 100, df) is df
    assert "candle floor BTC/USDT 15m: database is locked" in caplog.text
    assert not feed._local.conn.in_transaction
    assert _can_read(path)
    holder.release()
    assert _count(path, "SELECT COUNT(*) FROM candle_floor") == 0
    feed._note_floor("BTC/USDT", TF, 100, df)
    assert feed._floor("BTC/USDT", TF) == T0


def test_note_floor_failed_rollback_reconnects(tmp_path):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, commit=LOCKED, rollback=IOERR)
    feed._note_floor("BTC/USDT", TF, 100, _bars(0, 3))
    assert fc.closed and getattr(feed._local, "conn", None) is None
    feed._note_floor("BTC/USDT", TF, 100, _bars(0, 3))
    assert feed._floor("BTC/USDT", TF) == T0


def test_connect_failure_keeps_no_connection(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")          # schema setup cannot proceed
    feed = DataFeed(exchange=object(), db_path=path)
    with pytest.raises(sqlite3.OperationalError):
        feed.db
    assert getattr(feed._local, "conn", None) is None
    holder.execute("ROLLBACK")
    holder.close()
    assert feed.db is not None


# ── 6. contention: readers, candle writers, ref writers, a held lock ────────
def test_contention_recovers_after_a_held_write_lock(tmp_path):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    store = RefStore(path)
    store.db
    feed._store_save("SEED/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)

    hold_s = 1.0
    released = threading.Event()
    stop = threading.Event()
    stats = {k: {"fail_held": 0, "ok_after": 0, "fail_after": 0,
                 "open_txn": 0}
             for k in ("read", "write", "ref")}
    lock = threading.Lock()

    def note(kind, ok):
        with lock:
            s = stats[kind]
            if not released.is_set():
                s["fail_held"] += not ok
            elif ok:
                s["ok_after"] += 1
            else:
                s["fail_after"] += 1

    def reader():
        while not stop.is_set():
            note("read", feed._store_load("SEED/USDT", TF, 100) is not None)
        c = getattr(feed._local, "conn", None)
        with lock:
            stats["read"]["open_txn"] += bool(c and c.in_transaction)

    def writer(sym):
        i = 0
        while not stop.is_set():
            try:
                feed._store_save(sym, TF, _bars(i % 50, 5), now_ms=FAR_FUTURE)
                c = feed._local.conn
                # _store_save logs rather than raises: probe the store
                ok = c is not None and _row_present(c, sym, i % 50 + 4)
            except Exception:
                ok = False
            note("write", ok)
            i += 1
        c = getattr(feed._local, "conn", None)
        with lock:
            stats["write"]["open_txn"] += bool(c and c.in_transaction)

    def ref_writer():
        i = 0
        while not stop.is_set():
            try:
                store.save("btcdom", _ref_bars(i % 50, 3), now_ms=FAR_FUTURE)
                ok = True
            except sqlite3.OperationalError:
                ok = False
            note("ref", ok)
            i += 1
        c = getattr(store._local, "conn", None)
        with lock:
            stats["ref"]["open_txn"] += bool(c and c.in_transaction)

    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")          # blocks readers and writers
    threads = [threading.Thread(target=reader) for _ in range(2)] + \
              [threading.Thread(target=writer, args=(s,))
               for s in ("A/USDT", "B/USDT")] + \
              [threading.Thread(target=ref_writer)]
    for t in threads:
        t.start()
    time.sleep(hold_s)
    holder.execute("ROLLBACK")
    holder.close()
    released.set()
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join(timeout=5 * TIMEOUT + 2)
    assert not any(t.is_alive() for t in threads), "a caller stayed blocked"

    for kind, s in stats.items():
        assert s["fail_held"] > 0, (kind, s)   # the lock really bit
        assert s["ok_after"] > 0, (kind, s)    # and every path recovered
        assert s["open_txn"] == 0, (kind, s)   # nothing left open
    assert _can_write(path)
    # PK semantics held: one row per (symbol, tf, ts) / (key, ts)
    assert _count(path, "SELECT COUNT(*) FROM (SELECT symbol, tf, ts, "
                        "COUNT(*) n FROM candles GROUP BY 1,2,3 "
                        "HAVING n > 1)") == 0
    assert _count(path, "SELECT COUNT(*) FROM candles WHERE symbol IN "
                        "('A/USDT','B/USDT')") <= 2 * 54
    assert _count(path, "SELECT COUNT(*) FROM refs") <= 52


def _row_present(conn, sym, i) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM candles WHERE symbol=? AND tf=? AND ts=?",
            (sym, TF, T0 + i * TF_MS)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


# ── I. interrupted cleanup: the original error wins, the conn is evicted ────
class _CloseFault(FaultConn):
    """close() raises; `after` = the real close happened first (interrupt
    landed after it), otherwise the real connection is left open."""

    def __init__(self, real, close_exc, after=True, **faults):
        super().__init__(real, **faults)
        self.close_exc, self.after = close_exc, after

    def close(self):
        self.closed = True
        if self.after:
            self.real.close()
        raise self.close_exc


@contextlib.contextmanager
def _contained():
    """An interrupt escaping cleanup would abort pytest itself; report it
    as this test's failure instead."""
    try:
        yield
    except (KeyboardInterrupt, SystemExit) as e:
        pytest.fail(f"cleanup let {e!r} escape over the original error")


INTERRUPTS = [KeyboardInterrupt("^C"), SystemExit(3)]
_ids = lambda e: type(e).__name__                   # noqa: E731


def _batch_absent_then_fresh_write(feed, fc, path):
    assert getattr(feed._local, "conn", None) is None   # evicted
    if not fc.real_closed():
        fc.real.close()                        # finaliser of the orphan
    assert _can_write(path) and _count(path) == 0
    feed._store_save("ETH/USDT", TF, _bars(0, 3), now_ms=FAR_FUTURE)
    assert feed._local.conn is not fc          # fresh connection
    assert _count(path) == 3                   # only its own batch
    assert _count(path, "SELECT COUNT(*) FROM candles "
                        "WHERE symbol='BTC/USDT'") == 0


@pytest.mark.parametrize("intr", INTERRUPTS, ids=_ids)
def test_write_tx_rollback_interrupt_keeps_original_and_evicts(intr):
    local = threading.local()
    real = sqlite3.connect(":memory:")
    real.execute("CREATE TABLE t (k PRIMARY KEY)")
    real.commit()
    fc = FaultConn(real, commit=LOCKED, rollback=intr)
    local.conn = fc
    with _contained():
        with pytest.raises(sqlite3.OperationalError, match="locked") as ei:
            with sqlite_tx.write_tx(local, fc, "t"):
                fc.execute("INSERT INTO t VALUES (1)")
    assert ei.value is LOCKED
    assert local.conn is None and fc.closed


@pytest.mark.parametrize("intr", INTERRUPTS, ids=_ids)
def test_store_save_rollback_interrupt(tmp_path, caplog, intr):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, commit=LOCKED, rollback=intr)
    with _contained():
        feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert "candle store write BTC/USDT 15m: database is locked" in caplog.text
    assert type(intr).__name__ in caplog.text
    _batch_absent_then_fresh_write(feed, fc, path)


@pytest.mark.parametrize("after", [True, False], ids=["closed", "orphan"])
@pytest.mark.parametrize("intr", INTERRUPTS, ids=_ids)
def test_store_save_close_interrupt_still_evicts(tmp_path, caplog, intr,
                                                 after):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    real = feed._local.conn
    fc = _CloseFault(real, intr, after=after, commit=LOCKED, rollback=IOERR)
    feed._local.conn = fc
    with _contained():
        feed._store_save("BTC/USDT", TF, _bars(0, 5), now_ms=FAR_FUTURE)
    assert "database is locked" in caplog.text
    assert "close of failed connection interrupted" in caplog.text
    _batch_absent_then_fresh_write(feed, fc, path)


def test_rollback_ok_but_open_then_close_interrupt(tmp_path):
    """rollback 'succeeds' yet leaves the transaction open; close is
    interrupted — still evicted, still the original error."""
    path = str(tmp_path / "c.db")
    store = RefStore(path)
    store.db
    fc = _CloseFault(store._local.conn, KeyboardInterrupt(), after=False,
                     commit=LOCKED)
    fc.rollback = lambda: None
    store._local.conn = fc
    with _contained():
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE)
    assert getattr(store._local, "conn", None) is None
    fc.real.close()
    assert store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE) == 4
    assert store._local.conn is not fc
    assert _count(path, "SELECT COUNT(*) FROM refs") == 4


@pytest.mark.parametrize("intr", INTERRUPTS, ids=_ids)
def test_note_floor_rollback_interrupt(tmp_path, caplog, intr):
    path = str(tmp_path / "c.db")
    feed = _feed(path)
    fc = _inject(feed, commit=LOCKED, rollback=intr)
    df = _bars(0, 3)
    with _contained():
        assert feed._note_floor("BTC/USDT", TF, 100, df) is df
    assert "candle floor BTC/USDT 15m: database is locked" in caplog.text
    assert getattr(feed._local, "conn", None) is None
    assert _count(path, "SELECT COUNT(*) FROM candle_floor") == 0
    feed._note_floor("BTC/USDT", TF, 100, df)
    assert feed._local.conn is not fc
    assert feed._floor("BTC/USDT", TF) == T0


@pytest.mark.parametrize("intr", INTERRUPTS, ids=_ids)
def test_refstore_rollback_interrupt(tmp_path, intr):
    path = str(tmp_path / "c.db")
    store = RefStore(path)
    store.db
    fc = _inject(store, commit=LOCKED, rollback=intr)
    with _contained():
        with pytest.raises(sqlite3.OperationalError, match="locked") as ei:
            store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE)
    assert ei.value is LOCKED
    assert getattr(store._local, "conn", None) is None
    assert _count(path, "SELECT COUNT(*) FROM refs") == 0
    assert store.save("btcdom", _ref_bars(0, 5), now_ms=FAR_FUTURE) == 5
    assert store._local.conn is not fc
    assert _count(path, "SELECT COUNT(*) FROM refs") == 5


def test_clean_rollback_keeps_connection(tmp_path):
    """E: a rollback that completes with no transaction open is reused."""
    path = str(tmp_path / "c.db")
    store = RefStore(path)
    store.db
    fc = _inject(store, commit=LOCKED)
    with _contained():
        with pytest.raises(sqlite3.OperationalError):
            store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE)
    assert store._local.conn is fc and not fc.closed
    fc.fail_commit = None
    assert store.save("btcdom", _ref_bars(0, 4), now_ms=FAR_FUTURE) == 4


# ── J. initialisation: setup error wins over any close failure ─────────────
SETUP_ERR = sqlite3.OperationalError("setup: disk I/O error")


class _InitFault:
    def __init__(self, real, close_exc):
        self.real, self.close_exc, self.closed = real, close_exc, False

    def execute(self, *a):
        raise SETUP_ERR

    def close(self):
        self.closed = True
        self.real.close()
        if self.close_exc is not None:
            raise self.close_exc


def _fail_first_connect(monkeypatch, close_exc):
    real_connect = sqlite3.connect
    made = []

    def connect(*a, **k):
        c = real_connect(*a, **k)
        if not made:
            made.append(_InitFault(c, close_exc))
            return made[0]
        made.append(c)
        return c

    monkeypatch.setattr(sqlite3, "connect", connect)
    return made


CLOSE_FAULTS = [None, RuntimeError("close failed"), KeyboardInterrupt("^C")]


@pytest.mark.parametrize("close_exc", CLOSE_FAULTS,
                         ids=["close-ok", "close-Exception", "close-KI"])
@pytest.mark.parametrize("kind", ["feed", "ref"])
def test_init_setup_error_wins_and_nothing_cached(tmp_path, monkeypatch,
                                                  kind, close_exc):
    path = str(tmp_path / "c.db")
    owner = (DataFeed(exchange=object(), db_path=path) if kind == "feed"
             else RefStore(path))
    made = _fail_first_connect(monkeypatch, close_exc)
    with _contained():
        with pytest.raises(sqlite3.OperationalError) as ei:
            owner.db
    assert ei.value is SETUP_ERR
    assert made[0].closed
    assert getattr(owner._local, "conn", None) is None
    conn = owner.db                            # next init: fresh, works
    assert conn is made[1] and owner._local.conn is conn
    if kind == "feed":
        owner._store_save("BTC/USDT", TF, _bars(0, 2), now_ms=FAR_FUTURE)
        assert _count(path) == 2
    else:
        assert owner.save("btcdom", _ref_bars(0, 2), now_ms=FAR_FUTURE) == 2
