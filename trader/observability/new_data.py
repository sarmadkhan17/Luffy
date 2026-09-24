"""Attention new-data trigger over retained candle versions. Event creation only.

The Attention store (store.py) already decides novelty: a `versions` row is
appended only for a new or value-changed candle (unchanged re-observation
reuses the row). This module re-verifies each retained row's evidence and
records one immutable `attention-new-data-event.v1` per row in a separate
ledger. It never writes the Attention store, fetches data, calls an LLM or
touches trading/Risk/Execution.

A version id is digest([creating scan id, tf, content]). The creator is taken
from the store's `version_origins` receipt; later scans that merely reference
the version cannot reproduce it. A row without a receipt (written before
receipts existed) verifies only while its creator scan is still retained, else
it is refused as `origin_unavailable`; no origin is inferred from later scans.

Ledger rows are never updated. Retention deletes an event's payload only after
its version left the store, and moves its identity into the append-only
`retired` table in the same transaction: the store does not forbid a caller
reusing a pruned scan id, so a retired version id can recur and must not be
emitted again. Retired identities count toward the ledger capacity bound.

Downstream dispatch is not implemented: the investigation consumer is a
timer-driven, per-scan worker with no bounded event seam, so every result
reports DOWNSTREAM_DISPATCH_PREREQUISITE_REQUIRED.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time

from trader.cognition.contracts import TF_MS
from .attention import SCHEMA as STORE_SCHEMA, digest

EVENT_SCHEMA = "attention-new-data-event.v1"
DISPATCH = "DOWNSTREAM_DISPATCH_PREREQUISITE_REQUIRED"
# Implementation safety bounds of this package, not derived from any documented
# store/LUFFY capacity. MAX_EVENTS counts retained events plus retired
# tombstones, so it is a lifetime ceiling: once reached, every new identity is
# refused as `event_capacity` (fail closed) until an owner capacity policy
# exists (EVENT_IDENTITY_CAPACITY_POLICY_REQUIRED). Tombstones are never
# deleted to regain room.
# MAX_VERSIONS is likewise invented: the store bounds versions only indirectly
# (scans, age, bytes), and scan_versions references are not counted at all
# (INPUT_VERSION_CAPACITY_POLICY_REQUIRED). Exceeding it errors, never "no data".
# RETENTION_MS only delays deleting a payload whose version already left the
# store; it is not the store's retention (max_age_seconds) and never affects
# tombstones (EVENT_PAYLOAD_RETENTION_POLICY_REQUIRED).
# READ_DEADLINE_S is implementation policy: an overrun interrupts the read and
# the pass errors (fail closed); the value affects liveness, not correctness.
MAX_VERSIONS, MAX_EVENTS = 65_536, 262_144
RETENTION_MS = 30 * 86_400_000
READ_DEADLINE_S = 1.0
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")


def _hex(value):
    return isinstance(value, str) and bool(_HEX.match(value))


def _int(value):
    return type(value) is int and value >= 0


def source_versions(path):
    """Coherent, bounded, read-only snapshot of retained versions and their scans."""
    started = time.monotonic()
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                                 timeout=.1)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() - started > READ_DEADLINE_S), 1000)
        db.execute("BEGIN")
        if db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] > MAX_VERSIONS:
            raise ValueError("input_bound_exceeded")
        rows = db.execute("SELECT rowid,id,symbol,tf,open_ms,first_seen_ms,value_hash,"
                          "previous_value_hash,payload FROM versions ORDER BY rowid").fetchall()
        scans = {}
        for sid, vid in db.execute("SELECT scan_id,version_id FROM scan_versions"):
            scans.setdefault(vid, []).append(sid)
        origins = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='version_origins'").fetchone():
            origins = dict(db.execute("SELECT version_id,origin_scan_id FROM version_origins"))
    return rows, scans, origins


def _predecessor(rows):
    """The row the store chained from: latest by (first_seen_ms, rowid) inserted earlier."""
    out, seen = {}, {}
    for row in rows:
        key = (row[2], row[3], row[4])
        out[row[1]] = seen.get(key)
        best = seen.get(key)
        if best is None or (row[5], row[0]) > (best[5], best[0]):
            seen[key] = row
    return out


def verify(row, predecessor, scan_ids, store, origin=None):
    """Return (event, None) or (None, refusal_reason). Fail closed on any doubt.

    `origin` is the store's creator receipt; `scan_ids` (current references) are
    consulted only for receipt-less legacy rows, where a reproduced digest is an
    exact proof of the creator."""
    _rowid, vid, symbol, tf, open_ms, first_seen, value_hash, previous, payload = row
    if not _hex(vid):
        return None, "version_identity_missing"
    if not isinstance(symbol, str) or not symbol or tf not in TF_MS or not _int(open_ms):
        return None, "bar_identity_invalid"
    if not _int(first_seen):
        return None, "first_seen_invalid"
    try:
        content = json.loads(payload)
        if not isinstance(content, dict):
            raise ValueError
        content_hash = digest(content)
    except (TypeError, ValueError):
        return None, "payload_invalid"
    if not _hex(value_hash) or value_hash != content_hash:
        return None, "value_hash_unverifiable"
    if content.get("symbol") != symbol or content.get("open_ms") != open_ms:
        return None, "payload_identity_mismatch"
    if origin is not None:
        if not isinstance(origin, str) or digest([origin, tf, content]) != vid:
            return None, "version_identity_unverifiable"
    elif not any(digest([sid, tf, content]) == vid for sid in scan_ids):
        return None, "origin_unavailable"
    source = content.get("source")
    if not isinstance(source, str) or not 0 < len(source) <= 128:
        return None, "source_identity_missing"
    # Only fully closed bars: the bar closed no later than it was first retained.
    if open_ms + TF_MS[tf] > first_seen:
        return None, "bar_not_closed"
    if previous is None:
        if predecessor is not None:
            return None, "hash_chain_mismatch"
        reason = "new_version"
    else:
        if not _hex(previous) or previous == value_hash:
            return None, "previous_hash_invalid"
        if predecessor is None:
            return None, "hash_chain_unverifiable"
        if predecessor[6] != previous:
            return None, "hash_chain_mismatch"
        reason = "value_changed"
    body = {"schema": EVENT_SCHEMA, "reason": reason, "version_id": vid,
            "symbol": symbol, "timeframe": tf, "open_ms": open_ms,
            "value_hash": value_hash, "previous_value_hash": previous,
            "first_seen_ms": first_seen, "source": source,
            "store": {"schema": STORE_SCHEMA, "label": store}}
    # Identity is the immutable retained evidence only; no wall-clock input.
    return dict(body, event_id=digest(body)), None


def ledger(path):
    db = sqlite3.connect(str(path), timeout=1)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS retired (
        version_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, retired_ms INTEGER NOT NULL);
      CREATE TRIGGER IF NOT EXISTS retired_no_update BEFORE UPDATE ON retired
        BEGIN SELECT RAISE(ABORT, 'append_only'); END;
      CREATE TRIGGER IF NOT EXISTS retired_no_delete BEFORE DELETE ON retired
        BEGIN SELECT RAISE(ABORT, 'append_only'); END;
      CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY, version_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL, timeframe TEXT NOT NULL, open_ms INTEGER NOT NULL,
        first_seen_ms INTEGER NOT NULL, recorded_ms INTEGER NOT NULL, payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS events_order
        ON events(first_seen_ms, symbol, timeframe, open_ms, event_id);
      CREATE TRIGGER IF NOT EXISTS events_append_only BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT, 'append_only'); END;
    """)
    return db


def process(store_path, events_path, store, now_ms=None):
    """One idempotent pass. Emits at most one event per retained version, ever."""
    if not isinstance(store, str) or not _LABEL.match(store):
        raise ValueError("store_identity_missing")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    result = {"schema": EVENT_SCHEMA, "store": store, "status": "ok", "emitted": 0,
              "existing": 0, "retired": 0, "refused": {}, "pruned": 0, "dispatch": DISPATCH}
    if not Path(store_path).exists():
        return dict(result, status="waiting", reason="no_store")
    rows, scans, origins = source_versions(store_path)
    chain = _predecessor(rows)
    candidates = []
    for row in rows:
        event, refusal = verify(row, chain[row[1]], scans.get(row[1], ()), store,
                                origins.get(row[1]))
        if refusal:
            result["refused"][refusal] = result["refused"].get(refusal, 0) + 1
        else:
            candidates.append(event)
    candidates.sort(key=lambda e: (e["first_seen_ms"], e["symbol"], e["timeframe"],
                                   e["open_ms"], e["event_id"]))
    retained = {row[1] for row in rows}
    with closing(ledger(events_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        count = (db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                 + db.execute("SELECT COUNT(*) FROM retired").fetchone()[0])
        for event in candidates:
            old = db.execute("SELECT event_id,0 FROM events WHERE version_id=? UNION ALL "
                             "SELECT event_id,1 FROM retired WHERE version_id=?",
                             (event["version_id"],) * 2).fetchone()
            if old is not None:
                if old[0] != event["event_id"]:
                    # Never overwrite history: a conflicting re-derivation is refused.
                    result["refused"]["event_conflict"] = result["refused"].get("event_conflict", 0) + 1
                else:
                    result["retired" if old[1] else "existing"] += 1
                continue
            if count >= MAX_EVENTS:
                result["refused"]["event_capacity"] = result["refused"].get("event_capacity", 0) + 1
                continue
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                       (event["event_id"], event["version_id"], event["symbol"],
                        event["timeframe"], event["open_ms"], event["first_seen_ms"],
                        now, json.dumps(event, sort_keys=True, separators=(",", ":"))))
            count += 1
            result["emitted"] += 1
        # Only the payload goes; the identity stays as an append-only tombstone.
        stale = [(v, e) for v, e in db.execute(
            "SELECT version_id,event_id FROM events WHERE recorded_ms<?",
            (now - RETENTION_MS,)) if v not in retained]
        db.executemany("INSERT INTO retired VALUES (?,?,?)", [(v, e, now) for v, e in stale])
        db.executemany("DELETE FROM events WHERE version_id=?", [(v,) for v, _ in stale])
        result["pruned"] = len(stale)
    if result["refused"]:
        result["status"] = "degraded"
    return result


def events(path, limit=256):
    """Deterministically ordered read of retained events."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return [json.loads(p) for (p,) in db.execute(
            "SELECT payload FROM events ORDER BY first_seen_ms,symbol,timeframe,open_ms,event_id "
            "LIMIT ?", (limit,))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    if not args.once or not args.enable:
        print(json.dumps({"status": "disabled"})); return 0
    from trader.core.config import ROOT
    from .declared import source_path
    data = ROOT / "data"
    src = source_path(data)
    label = "declared-population" if src.parent.name == "declared-population" else "attention"
    try:
        result = process(src, data / "attention_new_data.db", label)
    except (sqlite3.Error, ValueError, OSError) as exc:
        result = {"status": "error", "error_type": type(exc).__name__, "dispatch": DISPATCH}
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
