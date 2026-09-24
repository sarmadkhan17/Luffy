"""Append-only, hash-chained SQLite log for the forced-liquidation recorder.

Separate file from the trading journal. Rows are never updated or deleted
(SQLite triggers refuse it), there is no retention pruning, and every row
binds the previous row's hash so truncation, reordering or rewriting is
detectable offline. ``synchronous=FULL`` makes each committed append durable.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .forced_liquidation import digest, encode, sha256

STORE_SCHEMA = "forced-liquidation-log.v1"
GENESIS = "0" * 64

# Record kinds written by the recorder.
SESSION_OPEN = "session_open"
HANDSHAKE = "handshake"
HANDSHAKE_FAILURE = "handshake_failure"
PING_SENT = "ping_sent"
PONG = "pong"
SERVER_PING = "server_ping"
FRAME = "frame"
DROP = "drop"
READER_ERROR = "reader_error"
WRITE_FAILURE = "write_failure"
SESSION_CLOSE = "session_close"
KINDS = frozenset((SESSION_OPEN, HANDSHAKE, HANDSHAKE_FAILURE, PING_SENT, PONG, SERVER_PING,
                   FRAME, DROP, READER_ERROR, WRITE_FAILURE, SESSION_CLOSE))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(
  seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, session_id TEXT NOT NULL,
  utc_ms INTEGER NOT NULL, mono_ns INTEGER NOT NULL, raw BLOB, raw_sha256 TEXT,
  body TEXT NOT NULL, prev_hash TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS blobs(sha256 TEXT PRIMARY KEY, envelope TEXT NOT NULL, raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS window_seals(
  window_key TEXT PRIMARY KEY, sealed_utc_ms INTEGER NOT NULL,
  seal_hash TEXT NOT NULL, payload TEXT NOT NULL);
"""
_APPEND_ONLY = ("records", "blobs", "window_seals")


def record_hash(seq, kind, session_id, utc_ms, mono_ns, raw_sha256, body_text, prev_hash) -> str:
    return digest({"seq": seq, "kind": kind, "session_id": session_id, "utc_ms": utc_ms,
                   "mono_ns": mono_ns, "raw_sha256": raw_sha256, "body_sha256":
                   sha256(body_text.encode()), "prev_hash": prev_hash})


class LogStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(_SCHEMA)
        for table in _APPEND_ONLY:
            for op in ("UPDATE", "DELETE"):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, 'append-only {table}'); END")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (STORE_SCHEMA,))
        if self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != STORE_SCHEMA:
            raise ValueError("unsupported forced-liquidation log schema")
        self.bytes_persisted = 0

    def close(self):
        self.db.close()

    def append(self, kind, session_id, utc_ms, mono_ns, body, raw: bytes | None = None) -> dict:
        """Durably append one record; raises on any failure (caller records it)."""
        if kind not in KINDS:
            raise ValueError(f"unknown record kind {kind}")
        body_text = encode(body)
        raw_hash = sha256(raw) if raw is not None else None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            last = self.db.execute(
                "SELECT seq, record_hash FROM records ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
            rhash = record_hash(seq, kind, session_id, utc_ms, mono_ns, raw_hash, body_text, prev)
            self.db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (seq, kind, session_id, utc_ms, mono_ns, raw, raw_hash,
                             body_text, prev, rhash))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.bytes_persisted += len(body_text) + (len(raw) if raw is not None else 0)
        return {"seq": seq, "record_hash": rhash, "raw_sha256": raw_hash}

    def put_blob(self, raw: bytes, envelope: dict) -> str:
        key = sha256(raw)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO blobs VALUES (?,?,?)", (key, encode(envelope), raw))
        return key

    def put_seal(self, window_key: str, sealed_utc_ms: int, seal_hash: str, payload_text: str) -> str:
        """Immutable: a second seal for the same window must be byte-identical."""
        row = self.db.execute("SELECT seal_hash FROM window_seals WHERE window_key=?",
                              (window_key,)).fetchone()
        if row:
            if row[0] != seal_hash:
                raise ValueError("window already sealed with a different receipt")
            return row[0]
        with self.db:
            self.db.execute("INSERT INTO window_seals VALUES (?,?,?,?)",
                            (window_key, sealed_utc_ms, seal_hash, payload_text))
        return seal_hash


def load(path) -> tuple[list[dict], dict[str, dict]]:
    """Read-only export of every record (seq order) and metadata blob."""
    uri = f"file:{Path(path)}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        records = [dict(zip(("seq", "kind", "session_id", "utc_ms", "mono_ns", "raw",
                             "raw_sha256", "body", "prev_hash", "record_hash"), row))
                   for row in db.execute("SELECT seq, kind, session_id, utc_ms, mono_ns, raw, "
                                         "raw_sha256, body, prev_hash, record_hash FROM records "
                                         "ORDER BY seq")]
        blobs = {key: {"envelope": env, "raw": raw}
                 for key, env, raw in db.execute("SELECT sha256, envelope, raw FROM blobs")}
    return records, blobs


def load_seal(path, window_key: str) -> str | None:
    uri = f"file:{Path(path)}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        row = db.execute("SELECT payload FROM window_seals WHERE window_key=?", (window_key,)).fetchone()
    return row[0] if row else None
