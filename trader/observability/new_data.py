"""Attention new-data trigger over retained candle versions. Event creation only.

The Attention store (store.py) already decides novelty: a `versions` row is
appended only for a new or value-changed candle (unchanged re-observation
reuses the row). This module re-verifies each retained row's evidence and
records one immutable `attention-new-data-event.v1` per row in a separate
ledger. It never writes the Attention store, fetches data, calls an LLM or
touches trading/Risk/Execution.

Progress is an `attention-new-data-cursor.v1` over the Store's
`attention-version-append.v1` sequence: each pass reads at most `batch_size`
receipts after the cursor in one read-only Store transaction and commits the
events, refusals, gap receipts, payload retirements and the cursor advance in
one ledger transaction. A missing sequence at or below the Store high-water is
a committed version pruned before it was observed and is recorded as
`pruned_before_observed`, never read as "no data". A cursor is bound once to
one Store instance (store_meta identity); a different instance, a high-water
below the cursor or a changed anchor fails closed and is recorded, never
reset. Restore detection is limited to those checks plus the cursor's own
anchor: a same-instance restore that resurrects a gap recorded behind the
anchor is not detected (the cursor never rewinds, so it is neither re-read nor
emitted). Arbitrary same-instance restore/recovery semantics are unresolved and
belong to operator restore policy. Legacy-migration receipts give processing order only, not historical
insertion chronology.

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
from .store import read_append_meta

EVENT_SCHEMA = "attention-new-data-event.v1"
CURSOR_SCHEMA = "attention-new-data-cursor.v1"
GAP = "pruned_before_observed"
DISPATCH = "DOWNSTREAM_DISPATCH_PREREQUISITE_REQUIRED"
# Implementation safety bounds of this package, not derived from any documented
# store/LUFFY capacity. MAX_EVENTS counts retained events plus retired
# tombstones, so it is a lifetime ceiling: once reached, the cursor halts at
# the first new identity (status `blocked`, reason `event_capacity`, not
# recorded as a permanent refusal) until an owner capacity policy exists (EVENT_IDENTITY_CAPACITY_POLICY_REQUIRED).
# Tombstones are never deleted to regain room.
# BATCH_SIZE / CLEANUP_BATCH bound one pass's work; they are not completeness
# claims: `more_available` reports what remains for the next pass.
# RETENTION_MS only delays deleting a payload whose version already left the
# store; it is not the store's retention (max_age_seconds) and never affects
# tombstones (EVENT_PAYLOAD_RETENTION_POLICY_REQUIRED).
# READ_DEADLINE_S is implementation policy: an overrun interrupts the read and
# the pass errors with nothing committed; the value affects liveness only.
MAX_EVENTS = 262_144
BATCH_SIZE, CLEANUP_BATCH = 4096, 256
RETENTION_MS = 30 * 86_400_000
READ_DEADLINE_S = 1.0
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
# Store identity a cursor is bound to; store_meta is immutable, so any change
# under the same instance id is divergence.
_BOUND = (("store_instance_id", "store_instance_id"), ("meta_schema", "schema"),
          ("append_schema", "append_schema"), ("append_origin", "append_origin"),
          ("migration_receipts_through_seq", "migration_receipts_through_seq"))


def _hex(value):
    return isinstance(value, str) and bool(_HEX.match(value))


def _int(value):
    return type(value) is int and value >= 0


def _connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)


def _fault(cursor, meta, high_water, anchor):
    """Why this Store may not continue from `cursor`, or None."""
    if cursor is None:
        return None
    if cursor["cursor_schema"] != CURSOR_SCHEMA:
        return "cursor_schema_unsupported"
    if meta["store_instance_id"] != cursor["store_instance_id"]:
        return "store_instance_mismatch"
    if any(meta[m] != cursor[c] for c, m in _BOUND):
        return "store_divergence"
    if high_water < cursor["last_seq"] or high_water < cursor["observed_high_water"]:
        return "store_regression"
    # Sequences are never reused: the receipt at the cursor is either the one
    # observed, or pruned. A gap-anchored cursor must still be a gap. Only the
    # anchor is checked: earlier gaps are not re-scanned (see module docstring).
    if anchor is not None and anchor != cursor["last_version_id"]:
        return "store_divergence"
    return None


def _predecessor(db, row, seq, migrated_through):
    """The row the store chained from: among same-bar versions inserted earlier,
    the latest by first_seen_ms. Insertion order is the append sequence; legacy
    migrated rows (sequence = migration order) keep the Store's rowid order,
    within this one snapshot. Point lookup on the versions_key index."""
    def order(s, rowid):
        return (0, rowid) if s <= migrated_through else (1, s)
    mine = order(seq, row[0])
    best = None
    for cand in db.execute(
            "SELECT v.rowid,v.id,v.symbol,v.tf,v.open_ms,v.first_seen_ms,v.value_hash,r.seq "
            "FROM versions v JOIN version_append_receipts r ON r.version_id=v.id "
            "WHERE v.symbol=? AND v.tf=? AND v.open_ms=?", (row[2], row[3], row[4])):
        at = order(cand[7], cand[0])
        if at >= mine:
            continue
        key = (cand[5] if type(cand[5]) is int else -1, at)
        if best is None or key > best[0]:
            best = (key, cand)
    return best and best[1]


def _snapshot(path, cursor, batch_size, cleanup):
    """One coherent, bounded, read-only Store transaction: the append receipts
    after the cursor (at most batch_size) with point lookups for their evidence,
    and point presence checks for payload-retirement candidates."""
    started = time.monotonic()
    with closing(_connect(path)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() - started > READ_DEADLINE_S), 1000)
        db.execute("BEGIN")
        meta = read_append_meta(db)
        if meta is None:
            return {"fault": "append_sequence_unavailable"}
        high_water = db.execute("SELECT seq FROM version_append_high_water").fetchone()[0]
        last = cursor["last_seq"] if cursor else 0
        anchor = db.execute("SELECT version_id FROM version_append_receipts WHERE seq=?",
                            (last,)).fetchone()
        snap = {"meta": meta, "high_water": high_water,
                "fault": _fault(cursor, meta, high_water, anchor and anchor[0])}
        if snap["fault"]:
            return snap
        origins = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='version_origins'").fetchone()
        receipts = db.execute(
            "SELECT r.seq,r.version_id,v.rowid,v.id,v.symbol,v.tf,v.open_ms,v.first_seen_ms,"
            "v.value_hash,v.previous_value_hash,v.payload,"
            + ("o.origin_scan_id FROM version_append_receipts r "
               "LEFT JOIN version_origins o ON o.version_id=r.version_id " if origins else
               "NULL FROM version_append_receipts r ")
            + "LEFT JOIN versions v ON v.id=r.version_id "
            "WHERE r.seq>? ORDER BY r.seq LIMIT ?", (last, batch_size)).fetchall()
        scans, items = None, []
        for seq, receipt_vid, *row, origin in receipts:
            item = {"seq": seq, "version_id": receipt_vid, "row": row, "origin": origin,
                    "predecessor": None, "scan_ids": ()}
            if row[1] is not None:
                item["predecessor"] = _predecessor(db, row, seq,
                                                   meta["migration_receipts_through_seq"])
                if origin is None:  # receipt-less legacy row: referencing scans, by key
                    if scans is None:
                        scans = [s for (s,) in db.execute("SELECT scan_id FROM scans")]
                    item["scan_ids"] = [s for s in scans if db.execute(
                        "SELECT 1 FROM scan_versions WHERE scan_id=? AND version_id=?",
                        (s, row[1])).fetchone()]
            items.append(item)
        snap.update(items=items, exhausted=len(receipts) < batch_size,
                    present={v for v in cleanup if db.execute(
                        "SELECT 1 FROM versions WHERE id=?", (v,)).fetchone()})
    return snap


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


def _append_only(name):
    return "".join(f"""
      CREATE TRIGGER IF NOT EXISTS {name}_no_{op} BEFORE {op.upper()} ON {name}
        BEGIN SELECT RAISE(ABORT, 'append_only'); END;""" for op in ("update", "delete"))


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
      CREATE INDEX IF NOT EXISTS events_recorded ON events(recorded_ms, event_id);
      CREATE TRIGGER IF NOT EXISTS events_append_only BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT, 'append_only'); END;
      -- attention-new-data-cursor.v1: bound once per store label, immutable.
      CREATE TABLE IF NOT EXISTS cursor_binding (
        store_label TEXT PRIMARY KEY, cursor_schema TEXT NOT NULL,
        store_instance_id TEXT NOT NULL, meta_schema TEXT NOT NULL,
        append_schema TEXT NOT NULL, append_origin TEXT NOT NULL,
        migration_receipts_through_seq INTEGER NOT NULL, bound_ms INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS cursor (
        store_label TEXT PRIMARY KEY REFERENCES cursor_binding(store_label),
        last_seq INTEGER NOT NULL CHECK (last_seq >= 0), last_version_id TEXT,
        observed_high_water INTEGER NOT NULL CHECK (observed_high_water >= last_seq),
        updated_ms INTEGER NOT NULL);
      CREATE TRIGGER IF NOT EXISTS cursor_forward_only BEFORE UPDATE ON cursor
        WHEN NEW.store_label IS NOT OLD.store_label OR NEW.last_seq < OLD.last_seq
          OR NEW.observed_high_water < OLD.observed_high_water
        BEGIN SELECT RAISE(ABORT, 'cursor_forward_only'); END;
      CREATE TRIGGER IF NOT EXISTS cursor_undeletable BEFORE DELETE ON cursor
        BEGIN SELECT RAISE(ABORT, 'cursor_forward_only'); END;
      -- Committed sequences pruned before this ledger observed them.
      CREATE TABLE IF NOT EXISTS append_gaps (
        store_instance_id TEXT NOT NULL, first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL,
        store_label TEXT NOT NULL, reason TEXT NOT NULL CHECK (reason = 'pruned_before_observed'),
        recorded_ms INTEGER NOT NULL, PRIMARY KEY (store_instance_id, first_seq),
        CHECK (0 < first_seq AND first_seq <= last_seq));
      -- Retained rows that failed verification; recorded once, never emitted.
      CREATE TABLE IF NOT EXISTS append_refusals (
        store_instance_id TEXT NOT NULL, append_seq INTEGER NOT NULL CHECK (append_seq > 0),
        version_id TEXT NOT NULL, store_label TEXT NOT NULL, reason TEXT NOT NULL,
        recorded_ms INTEGER NOT NULL, PRIMARY KEY (store_instance_id, append_seq));
      -- Fail-closed Store conditions (replacement/regression/divergence).
      CREATE TABLE IF NOT EXISTS cursor_faults (
        store_label TEXT NOT NULL, fault TEXT NOT NULL, store_instance_id TEXT NOT NULL,
        cursor_last_seq INTEGER NOT NULL, observed_high_water INTEGER NOT NULL,
        first_ms INTEGER NOT NULL,
        PRIMARY KEY (store_label, fault, store_instance_id, cursor_last_seq, observed_high_water));
      -- Operational scan position of bounded payload retirement; not evidence.
      CREATE TABLE IF NOT EXISTS cleanup_position (
        store_label TEXT PRIMARY KEY, recorded_ms INTEGER NOT NULL, event_id TEXT NOT NULL);
    """ + "".join(_append_only(t) for t in ("cursor_binding", "append_gaps",
                                             "append_refusals", "cursor_faults")))
    return db


def cursor_state(db, store):
    row = db.execute("SELECT b.*,c.last_seq,c.last_version_id,c.observed_high_water "
                     "FROM cursor_binding b JOIN cursor c USING (store_label) "
                     "WHERE b.store_label=?", (store,)).fetchone()
    return dict(row) if row else None


def _cleanup_candidates(db, store, now):
    """At most CLEANUP_BATCH old events after the rotating position; the Store
    is then asked about exactly these ids, never scanned."""
    pos = db.execute("SELECT recorded_ms,event_id FROM cleanup_position WHERE store_label=?",
                     (store,)).fetchone()
    rows = db.execute("SELECT version_id,event_id,recorded_ms,payload FROM events "
                      "WHERE recorded_ms<? AND (recorded_ms,event_id)>(?,?) "
                      "ORDER BY recorded_ms,event_id LIMIT ?",
                      (now - RETENTION_MS, *(tuple(pos) if pos else (-1, "")),
                       CLEANUP_BATCH)).fetchall()
    mine = [(r[0], r[1]) for r in rows if json.loads(r[3])["store"]["label"] == store]
    return rows, mine


def _advance(db, store, cursor, last, last_vid, high_water, now):
    """Written last in the pass transaction: the cursor moves only with its evidence."""
    if cursor is None:
        db.execute("INSERT INTO cursor VALUES (?,?,?,?,?)", (store, last, last_vid, high_water, now))
    elif (last, high_water) != (cursor["last_seq"], cursor["observed_high_water"]):
        db.execute("UPDATE cursor SET last_seq=?,last_version_id=?,observed_high_water=?,"
                   "updated_ms=? WHERE store_label=?", (last, last_vid, high_water, now, store))


def process(store_path, events_path, store, now_ms=None, batch_size=BATCH_SIZE):
    """One bounded, idempotent pass. Emits at most one event per version, ever."""
    if not isinstance(store, str) or not _LABEL.match(store):
        raise ValueError("store_identity_missing")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size_invalid")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    result = {"schema": EVENT_SCHEMA, "cursor_schema": CURSOR_SCHEMA, "store": store,
              "status": "ok", "emitted": 0, "existing": 0, "retired": 0, "refused": {},
              GAP: 0, "pruned": 0, "dispatch": DISPATCH}
    if not Path(store_path).exists():
        return dict(result, status="waiting", reason="no_store")

    def refuse(reason):
        result["refused"][reason] = result["refused"].get(reason, 0) + 1

    with closing(ledger(events_path)) as db, db:
        # The write lock is held across the Store read so the cursor read here
        # is the cursor advanced below.
        db.execute("BEGIN IMMEDIATE")
        cursor = cursor_state(db, store)
        examined, cleanup = _cleanup_candidates(db, store, now)
        snap = _snapshot(store_path, cursor, batch_size, [v for v, _ in cleanup])
        meta, high_water = snap.get("meta"), snap.get("high_water")
        last = cursor["last_seq"] if cursor else 0
        result.update(last_seq=last, high_water=high_water,
                      store_instance_id=meta and meta["store_instance_id"])
        if snap["fault"]:
            if meta:
                db.execute("INSERT OR IGNORE INTO cursor_faults VALUES (?,?,?,?,?,?)",
                           (store, snap["fault"], meta["store_instance_id"], last,
                            high_water, now))
            return dict(result, status="blocked", reason=snap["fault"], more_available=None)
        instance = meta["store_instance_id"]
        if cursor is None:
            db.execute("INSERT INTO cursor_binding VALUES (?,?,?,?,?,?,?,?)",
                       (store, CURSOR_SCHEMA, *(meta[m] for _, m in _BOUND), now))
        last_vid = cursor["last_version_id"] if cursor else None

        def gap(first, through):
            db.execute("INSERT INTO append_gaps VALUES (?,?,?,?,?,?)",
                       (instance, first, through, store, GAP, now))
            result[GAP] += through - first + 1

        count = (db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                 + db.execute("SELECT COUNT(*) FROM retired").fetchone()[0])
        for item in snap["items"]:
            seq = item["seq"]
            if seq > last + 1:
                gap(last + 1, seq - 1)
                last, last_vid = seq - 1, None
            if item["row"][1] is None:
                event, reason = None, "version_identity_missing"
            else:
                event, reason = verify(item["row"], item["predecessor"], item["scan_ids"],
                                       store, item["origin"])
            if event is not None:
                old = db.execute("SELECT event_id,0 FROM events WHERE version_id=? UNION ALL "
                                 "SELECT event_id,1 FROM retired WHERE version_id=?",
                                 (event["version_id"],) * 2).fetchone()
                if old is not None:
                    if old[0] != event["event_id"]:
                        # Never overwrite history: a conflicting re-derivation is refused.
                        reason = "event_conflict"
                    else:
                        result["retired" if old[1] else "existing"] += 1
                elif count >= MAX_EVENTS:
                    # A ledger condition, not a property of the row: halt before
                    # this sequence with no refusal; every retry halts here again.
                    result["blocked"] = {"reason": "event_capacity", "append_seq": seq}
                    break
                else:
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                               (event["event_id"], event["version_id"], event["symbol"],
                                event["timeframe"], event["open_ms"], event["first_seen_ms"],
                                now, json.dumps(event, sort_keys=True, separators=(",", ":"))))
                    count += 1
                    result["emitted"] += 1
            if reason:
                db.execute("INSERT INTO append_refusals VALUES (?,?,?,?,?,?)",
                           (instance, seq, item["version_id"], store, reason, now))
                refuse(reason)
            last, last_vid = seq, item["version_id"]
        else:
            if snap["exhausted"] and high_water > last:
                gap(last + 1, high_water)
                last, last_vid = high_water, None
        # Only the payload goes; the identity stays as an append-only tombstone.
        stale = [(v, e) for v, e in cleanup if v not in snap["present"]]
        db.executemany("INSERT INTO retired VALUES (?,?,?)", [(v, e, now) for v, e in stale])
        db.executemany("DELETE FROM events WHERE version_id=?", [(v,) for v, _ in stale])
        result["pruned"] = len(stale)
        if len(examined) < CLEANUP_BATCH:  # reached the end: next pass starts over
            db.execute("DELETE FROM cleanup_position WHERE store_label=?", (store,))
        else:
            db.execute("INSERT OR REPLACE INTO cleanup_position VALUES (?,?,?)",
                       (store, examined[-1][2], examined[-1][1]))
        _advance(db, store, cursor, last, last_vid, high_water, now)
    result.update(last_seq=last, more_available=last < high_water)
    if "blocked" in result:  # the cursor cannot pass this sequence: not progress
        result.update(status="blocked", reason="event_capacity")
    elif result["refused"] or result[GAP]:
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
    return int(result["status"] in ("error", "blocked"))


if __name__ == "__main__":
    raise SystemExit(main())
