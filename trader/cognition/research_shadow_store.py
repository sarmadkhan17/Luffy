"""Research shadow store — the SQLite facade of the research shadow harness.

The shadow harness (``research_shadow.py``) runs the two TESTED research
families against the production journal WITHOUT writing it. This module is
the only place that opens either database for it:

- the **shadow database** (e.g. ``data/research_shadow.db``) is the
  connection's ``main`` database. It holds exactly the Journal's own
  ``research_*`` tables, indexes and triggers — their DDL is copied verbatim
  from ``trader.core.journal.SCHEMA`` (executed into an in-memory database
  and replayed from its ``sqlite_master``) — plus the harness's own
  ``research_shadow_*`` tables. It has no trading, decision or brain-event
  tables of its own.
- the **source database** (the production ``luffy.db``) is ATTACHed as
  ``src`` through a ``mode=ro`` URI: SQLite itself refuses every write to
  it ("attempt to write a readonly database").
- the only source tables the unchanged research readers can reach are
  exposed as TEMP views, which are not writable: ``brain_events`` (the
  strategy-health rows only, bounded as below) and ``decisions`` (read
  live; see limitations).
- an SQLite authorizer on the connection denies every insert, update,
  delete, DDL, pragma, ATTACH or DETACH against ``src``, and every read of
  a ``src`` table other than ``brain_events`` and ``decisions``. A reader
  that asked for any other table would get an error, not an empty result.

``ShadowJournal`` is a ``Journal`` subclass built without
``Journal.__init__`` (which runs schema DDL and a backfill UPDATE against
its own database). Every existing Q/P/E/R/Run/Bank reader and writer runs
unchanged on it. Its only overrides are the connection (the one prepared
connection, single-thread) and ``_tx``: inside an explicit ``unit()`` every
writer's transaction becomes a SAVEPOINT of the unit's one transaction, so
the whole unit commits or rolls back together; a writer that raises still
rolls back exactly its own work, as it does under ``Journal._tx``. Outside a
unit, ``_tx`` is ``Journal._tx`` unchanged.

Health view bound. ``set_bound(spec_max, sweep_max)`` fixes which
strategy-health rows the view shows: every sweep record with id <=
``sweep_max``; spec records with id <= ``spec_max``; and spec records
without a usable subject (NULL, empty or non-text) with id <= ``sweep_max``
(the families must still see those to refuse them). ``brain_events`` is
append-only and its ids are AUTOINCREMENT, so rows with id <= a committed
high-water id are fixed: the view is a stable snapshot although the source
is written concurrently. Non-health brain events are not exposed.

Limitations stated, not hidden: ``decisions`` rows are updated in place and
keyed by text ids, so they are read live, not snapshot-bounded. SQLite may
create or touch the source's ``-shm``/``-wal`` sidecars while reading, so
zero filesystem activity is not claimed. No network, LLM, Attention,
Kernel, Risk, Execution or trading authority.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from trader.core import journal as journal_mod
from trader.core.journal import Journal

SRC = "src"
HEALTH_KINDS = ("strategy_health_observed", "strategy_health_sweep")
SPEC_KIND, SWEEP_KIND = HEALTH_KINDS
SOURCE_TABLES = ("brain_events", "decisions")
MAX_EVENT_ID = 2 ** 63 - 1
BUSY_TIMEOUT_S = 5.0

SOURCE_ACCESS = ("sqlite_attach_uri_mode_ro;temp_views:brain_events("
                 "strategy_health_rows_bounded),decisions(live);"
                 "authorizer_denies_src_writes_and_other_tables")

SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS research_shadow_source_binding (
    binding_name TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    bound_by_invocation_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_cursor (
    cursor_name TEXT PRIMARY KEY,
    source_event_id INTEGER NOT NULL,
    anchor_record_sha256 TEXT,
    invocation_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_starts (
    invocation_id TEXT PRIMARY KEY,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_snapshots (
    invocation_id TEXT PRIMARY KEY,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_completions (
    invocation_id TEXT PRIMARY KEY,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_invocations (
    invocation_id TEXT PRIMARY KEY,
    schema TEXT NOT NULL,
    outcome TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL
);
"""
#: immutable table -> its key column. UPDATE and DELETE abort, and so does
#: any INSERT whose key already exists — including INSERT OR REPLACE /
#: REPLACE, whose implicit delete fires no delete trigger while
#: recursive_triggers is off (the SQLite default)
IMMUTABLE_TABLES = {"research_shadow_source_binding": "binding_name",
                    "research_shadow_starts": "invocation_id",
                    "research_shadow_snapshots": "invocation_id",
                    "research_shadow_completions": "invocation_id",
                    "research_shadow_invocations": "invocation_id"}
BINDING_NAME = "source_journal"


class ShadowStoreError(RuntimeError):
    """The shadow or source database cannot be used as the harness needs."""


def _immutable_ddl() -> str:
    out = []
    for t, key in IMMUTABLE_TABLES.items():
        for op in ("UPDATE", "DELETE"):
            out.append(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} "
                       f"BEFORE {op} ON {t} BEGIN SELECT RAISE(ABORT, "
                       f"'{t} is immutable'); END;")
        out.append(f"CREATE TRIGGER IF NOT EXISTS {t}_no_replace "
                   f"BEFORE INSERT ON {t} WHEN EXISTS (SELECT 1 FROM {t} "
                   f"WHERE {key} = NEW.{key}) BEGIN SELECT RAISE(ABORT, "
                   f"'{t} is immutable'); END;")
    return "\n".join(out)


def same_file(a, b) -> bool:
    """Whether two paths name one filesystem object (resolved-path alias,
    symlink or hard link). A missing path is never the same file."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def research_ddl() -> list:
    """[(name, sql)]: the Journal's own research_* tables, indexes and
    triggers, verbatim as SQLite stored them, tables first."""
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(journal_mod.SCHEMA)
        rows = mem.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND tbl_name LIKE 'research\\_%' ESCAPE '\\' "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rowid"
        ).fetchall()
    finally:
        mem.close()
    return rows


def _uri(path: Path, mode: str) -> str:
    return path.resolve().as_uri() + f"?mode={mode}"


#: the table that positively identifies an initialized shadow store
SHADOW_MARKER = "research_shadow_invocations"


def _names(conn) -> set:
    return {r[0] for r in conn.execute("SELECT tbl_name FROM sqlite_master")}


def _foreign(names) -> list:
    return sorted(n for n in names if not (
        n.startswith("research_") or n.startswith("sqlite_")))


def _initialize(conn) -> None:
    """Idempotent schema creation on an already-validated connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    have = _names(conn) | {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master")}
    with conn:
        for name, sql in research_ddl():
            if name not in have:
                conn.execute(sql)
        conn.executescript(SHADOW_DDL + _immutable_ddl())
    # a clean integrity result is required before any use
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ShadowStoreError("shadow_integrity_check_failed")


def init_shadow(shadow_path) -> None:
    """Create (or open) the shadow database. Never writes any database it
    has not validated ON THE SAME CONNECTION that writes it.

    - Existing path: one read-write connection is opened (SQLite opens the
      file at connect time, so later path replacement cannot redirect it)
      and, before its first mutation, that connection must show the shadow
      marker table and no non-research object. A journal reached through
      any alias — or swapped in before the open — has foreign tables and no
      marker, and is refused unwritten (``not_a_shadow_store``).
    - Absent path: the store is built in a fresh exclusive, randomly named
      file beside it (validated empty on its own connection), then linked
      into place with ``os.link``, which fails if anything appeared at the
      path meanwhile (``shadow_path_raced``). The harness never opens for
      writing a path it did not just create.

    Threat model: aliasing and replacement of the named paths. An actor who
    can rewrite files inside the store's directory at will is out of scope.
    Raises ShadowStoreError / sqlite3.Error."""
    path = Path(shadow_path)
    if path.exists() or path.is_symlink():
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S)
        try:
            names = _names(conn)
            if SHADOW_MARKER not in names or _foreign(names):
                raise ShadowStoreError("not_a_shadow_store")
            _initialize(conn)
        finally:
            conn.close()
        return
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.name}.init-")
    os.close(fd)
    tmp = Path(tmp)
    try:
        conn = sqlite3.connect(str(tmp), timeout=BUSY_TIMEOUT_S)
        try:
            if _names(conn):
                raise ShadowStoreError("not_a_shadow_store")
            _initialize(conn)
            conn.execute("PRAGMA journal_mode=DELETE")   # no sidecars left
        finally:
            conn.close()
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise ShadowStoreError("shadow_path_raced") from None
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(str(tmp) + suffix)
            except FileNotFoundError:
                pass
    init_shadow(path)            # re-validate on its own connection; WAL


# ── authorizer ────────────────────────────────────────────────────────────
_DENY_ON_SRC = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE, sqlite3.SQLITE_ALTER_TABLE,
                sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
                sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW,
                sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_ANALYZE,
                sqlite3.SQLITE_PRAGMA}
_SRC_READABLE = set(SOURCE_TABLES)


def _authorizer(action, arg1, _arg2, db_name, _trigger):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if db_name == SRC:
        if action in _DENY_ON_SRC:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg1 not in _SRC_READABLE:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


_BOUND_DDL = (
    "CREATE TEMP TABLE shadow_bound (spec_max INTEGER NOT NULL, "
    "sweep_max INTEGER NOT NULL)",
    "INSERT INTO temp.shadow_bound VALUES (0, 0)",
    "CREATE TEMP VIEW brain_events AS SELECT b.id AS id, b.ts AS ts, "
    "b.kind AS kind, b.subject AS subject, b.detail AS detail "
    "FROM src.brain_events b "
    f"WHERE b.kind IN ('{SPEC_KIND}', '{SWEEP_KIND}') "
    "AND b.id <= (SELECT sweep_max FROM temp.shadow_bound) "
    f"AND (b.kind = '{SWEEP_KIND}' "
    "OR b.id <= (SELECT spec_max FROM temp.shadow_bound) "
    "OR b.subject IS NULL OR typeof(b.subject) <> 'text' "
    "OR b.subject = '') ORDER BY b.id",
    "CREATE TEMP VIEW decisions AS SELECT * FROM src.decisions",
)


class ShadowJournal(Journal):
    """A Journal whose main database is the shadow store and whose only
    source access is the read-only views above. Single-thread."""

    def _conn(self) -> sqlite3.Connection:
        if threading.get_ident() != self._owner:
            raise ShadowStoreError("shadow_journal_single_thread")
        return self._shadow_conn

    @contextmanager
    def _tx(self):
        if not self._in_unit:
            with Journal._tx(self) as c:
                yield c
            return
        with self._write_lock:
            c = self._conn()
            c.execute("SAVEPOINT shadow_write")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK TO shadow_write")
                c.execute("RELEASE shadow_write")
                raise
            c.execute("RELEASE shadow_write")

    @contextmanager
    def unit(self):
        """One atomic unit of work: every writer inside it, and anything
        executed on the connection, commits together or not at all."""
        if self._readonly:
            raise ShadowStoreError("read_only_store")
        if self._in_unit:
            raise ShadowStoreError("nested_unit")
        c = self._conn()
        if c.in_transaction:
            raise ShadowStoreError("transaction_already_open")
        c.execute("BEGIN IMMEDIATE")
        self._in_unit = True
        try:
            yield c
        except BaseException:
            self._in_unit = False
            c.rollback()
            raise
        self._in_unit = False
        c.commit()

    def set_bound(self, spec_max: int, sweep_max: int) -> None:
        for v in (spec_max, sweep_max):
            if type(v) is not int or not 0 <= v <= MAX_EVENT_ID:
                raise ShadowStoreError("invalid_bound")
        c = self._conn()
        c.execute("UPDATE temp.shadow_bound SET spec_max=?, sweep_max=?",
                  (spec_max, sweep_max))
        # inside a unit the bound is part of the unit's transaction; outside
        # one, commit so the connection stays transaction-free
        if not self._in_unit:
            c.commit()

    def source_query(self, sql: str, params: tuple = ()) -> list:
        """A SELECT against the attached source (authorizer-limited)."""
        return [dict(r) for r in self._conn().execute(sql, params)]

    def close(self) -> None:
        self._shadow_conn.close()


def open_shadow(shadow_path, source_path, *, readonly: bool) -> ShadowJournal:
    """The ShadowJournal over an existing shadow store (``mode=rw``, or
    ``mode=ro`` for readers) with ``source_path`` attached ``mode=ro``.
    Raises ShadowStoreError / sqlite3.Error."""
    shadow, source = Path(shadow_path), Path(source_path)
    if not shadow.is_file():
        raise ShadowStoreError("shadow_missing")
    if not source.is_file():
        raise ShadowStoreError("source_missing")
    if same_file(shadow, source):
        raise ShadowStoreError("source_is_shadow")
    conn = sqlite3.connect(_uri(shadow, "ro" if readonly else "rw"),
                           uri=True, timeout=BUSY_TIMEOUT_S)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"ATTACH DATABASE ? AS {SRC}", (_uri(source, "ro"),))
        have = {r[0] for r in conn.execute(
            f"SELECT name FROM {SRC}.sqlite_master WHERE type='table'")}
        if not set(SOURCE_TABLES) <= have:
            raise ShadowStoreError("source_not_a_journal")
        main = {r[0] for r in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type IN "
            "('table','view')")}
        if set(SOURCE_TABLES) & main:
            raise ShadowStoreError("shadow_shadows_source_table")
        if "research_shadow_invocations" not in main:
            raise ShadowStoreError("shadow_not_initialized")
        for sql in _BOUND_DDL:
            conn.execute(sql)
        conn.commit()
        conn.set_authorizer(_authorizer)
    except BaseException:
        conn.close()
        raise
    j = ShadowJournal.__new__(ShadowJournal)
    j.db_path = shadow
    j.source_path = source
    j._local = threading.local()
    j._write_lock = threading.Lock()
    j._owner = threading.get_ident()
    j._shadow_conn = conn
    j._in_unit = False
    j._readonly = readonly
    return j


def open_shadow_only(shadow_path, *, readonly: bool) -> sqlite3.Connection:
    """A plain connection to the shadow store alone (no source attached):
    the parent's start/receipt writes and cursor reads. Raises
    ShadowStoreError / sqlite3.Error."""
    shadow = Path(shadow_path)
    if not shadow.is_file():
        raise ShadowStoreError("shadow_missing")
    conn = sqlite3.connect(_uri(shadow, "ro" if readonly else "rw"),
                           uri=True, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    return conn
