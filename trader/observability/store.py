"""Separate, size/age bounded operational store; never opens the trading journal.

Each scan freezes its inputs and evidence. Repeated values share version rows;
changed values append versions. Retention is explicit, not research storage.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from .attention import SCHEMA, code_manifest, digest, evaluate_snapshot


IDENTITY_SCHEMA = "attention-scan-identity.v1"
IDENTITY_KEYS = frozenset(("schema", "instance_id", "seq"))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def valid_identity(value):
    """Versioned collector identity metadata. Old records carry none."""
    return (isinstance(value, dict) and set(value) == IDENTITY_KEYS
            and value["schema"] == IDENTITY_SCHEMA
            and isinstance(value["instance_id"], str) and 0 < len(value["instance_id"]) <= 64
            and type(value["seq"]) is int and value["seq"] >= 1)


def _identity(event):
    ident = event.get("identity")
    if ident is not None and not valid_identity(ident):
        raise ValueError("invalid_identity")
    return ident


def _proof(db, scan_id, kind):
    """Read back what was persisted for both halves; returned to the parent."""
    row = db.execute("SELECT payload,causes_complete FROM scans WHERE scan_id=?", (scan_id,)).fetchone()
    idents = {encode(json.loads(p).get("collector_identity")) for (p,) in
              db.execute("SELECT payload FROM causes WHERE scan_id=?", (scan_id,))}
    rows = db.execute("SELECT COUNT(*) FROM causes WHERE scan_id=?", (scan_id,)).fetchone()[0]
    return {"scan_id": scan_id, "kind": kind, "present": row is not None,
            "payload": bool(row and row[0] is not None),
            "causes_complete": bool(row and row[1]),
            "scan_identity": json.loads(row[0]).get("collector_identity") if row and row[0] else None,
            "cause_identities": [json.loads(i) for i in sorted(idents)], "cause_rows": rows,
            "completion_marker": any(json.loads(r[0]).get('collector_completion') is True for r in db.execute('SELECT payload FROM causes WHERE scan_id=?',(scan_id,)))}


class Store:
    def __init__(self, path, cfg):
        self.path, self.cfg = Path(path), cfg
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=0.05)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA auto_vacuum=FULL")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS scans (
            scan_id TEXT PRIMARY KEY, as_of_ms INTEGER NOT NULL, payload TEXT, causes_complete INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS versions (
            id TEXT PRIMARY KEY, symbol TEXT, tf TEXT, open_ms INTEGER,
            first_seen_ms INTEGER, value_hash TEXT, previous_value_hash TEXT,
            payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS versions_key
            ON versions(symbol, tf, open_ms, first_seen_ms);
          CREATE TABLE IF NOT EXISTS scan_versions (
            scan_id TEXT REFERENCES scans(scan_id) ON DELETE CASCADE,
            version_id TEXT REFERENCES versions(id),
            PRIMARY KEY(scan_id, version_id));
          CREATE TABLE IF NOT EXISTS causes (
            event_id TEXT PRIMARY KEY, scan_id TEXT REFERENCES scans(scan_id)
              ON DELETE CASCADE, symbol TEXT, payload TEXT NOT NULL);
        """)
        # Enforce a hard SQLite allocation ceiling as well as logical retention.
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.db.execute(f"PRAGMA max_page_count={max(16, cfg['max_bytes'] // page_size)}")

    def close(self):
        self.db.close()

    def _prune(self, now_ms, reserve=0):
        self.db.execute("DELETE FROM scans WHERE as_of_ms < ?",
                        (now_ms - self.cfg["max_age_seconds"] * 1000,))
        keep = max(0, self.cfg["max_scans"] - reserve)
        self.db.execute("DELETE FROM scans WHERE scan_id IN "
                        "(SELECT scan_id FROM scans ORDER BY as_of_ms DESC, scan_id DESC "
                        "LIMIT -1 OFFSET ?)", (keep,))
        self.db.execute("DELETE FROM versions WHERE id NOT IN "
                        "(SELECT version_id FROM scan_versions)")

    def write(self, event):
        """Persist one event; return the read-back identity proof of its scan."""
        now_ms = int(time.time() * 1000)
        scan_id = event["scan_id"]
        ident = _identity(event)
        if len(encode(event)) > self.cfg["max_bytes"] // 2:
            raise ValueError("snapshot exceeds storage budget")
        # Prune before allocation so a full store can recover on the next job.
        with self.db:
            self._prune(now_ms, reserve=int(not self.db.execute(
                "SELECT 1 FROM scans WHERE scan_id=?", (scan_id,)).fetchone()))
            # Leave headroom for a whole snapshot and SQLite indexes.
            while self.db.execute("PRAGMA page_count").fetchone()[0] * self.db.execute(
                    "PRAGMA page_size").fetchone()[0] > self.cfg["max_bytes"] // 2:
                oldest = self.db.execute("SELECT scan_id FROM scans ORDER BY as_of_ms "
                                         "LIMIT 1").fetchone()
                if not oldest:
                    break
                self.db.execute("DELETE FROM scans WHERE scan_id=?", (oldest[0],))
                self.db.execute("DELETE FROM versions WHERE id NOT IN "
                                "(SELECT version_id FROM scan_versions)")
                # File pages shrink at commit (FULL auto-vacuum).
                self.db.commit()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO scans (scan_id,as_of_ms,payload) VALUES (?,?,NULL)",
                            (scan_id, event["as_of_ms"]))
            if event["kind"] == "causes":
                if ident:
                    marker=dict(collector_completion=True, collector_identity=ident, symbol='')
                    mid=digest([scan_id,'collector_completion'])
                    old=self.db.execute('SELECT payload FROM causes WHERE event_id=?',(mid,)).fetchone()
                    if old and json.loads(old[0])!=marker: raise ValueError('identity_conflict')
                    self.db.execute('INSERT OR IGNORE INTO causes VALUES (?,?,?,?)',(mid,scan_id,'',encode(marker)))
                self.db.execute("UPDATE scans SET causes_complete=1 WHERE scan_id=?", (scan_id,))
                for original in event["items"]:
                    item=dict(original)
                    if ident: item['collector_identity']=ident
                    eid = digest([scan_id, item["symbol"], item.get("decision_id")])
                    self.db.execute("INSERT OR IGNORE INTO causes VALUES (?,?,?,?)",
                                    (eid, scan_id, item["symbol"], encode(item)))
            elif event["kind"] == "scan":
                prior = self.db.execute("SELECT payload FROM scans WHERE scan_id=?",
                                        (scan_id,)).fetchone()[0]
                if prior is not None:
                    if json.loads(prior).get('collector_identity')!=ident:
                        raise ValueError('identity_conflict')
                    return _proof(self.db,scan_id,event['kind'])
                refs = []
                for candle in event["input"]["candles"]:
                    tf = event["input"]["timeframe"]
                    content = {k: v for k, v in candle.items() if k != "available_ms"}
                    hashed = digest(content)
                    prev = self.db.execute("SELECT * FROM versions WHERE symbol=? AND tf=? "
                                           "AND open_ms=? ORDER BY first_seen_ms DESC, rowid DESC LIMIT 1",
                                           (candle["symbol"], tf, candle["open_ms"])).fetchone()
                    if prev and prev["value_hash"] == hashed:
                        vid, first_seen = prev["id"], prev["first_seen_ms"]
                    else:
                        first_seen = event["as_of_ms"]
                        vid = digest([scan_id, tf, content])
                        self.db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)",
                                        (vid, candle["symbol"], tf, candle["open_ms"], first_seen,
                                         hashed, prev["value_hash"] if prev else None, encode(content)))
                    candle["available_ms"] = first_seen
                    self.db.execute("INSERT OR IGNORE INTO scan_versions VALUES (?,?)", (scan_id, vid))
                    refs.append({"version_id": vid, "first_seen_ms": first_seen,
                                 "symbol": candle["symbol"], "open_ms": candle["open_ms"]})
                payload = evaluate_snapshot(event)
                if ident: payload['collector_identity']=ident
                payload.update(input_versions=refs, code_manifest=code_manifest(),
                               input_hash=digest(event["input"]),
                               membership=event["input"]["membership"],
                               timeframe=event["input"]["timeframe"],
                               prior_availability="unknown", persisted_at_ms=now_ms)
                self.db.execute("UPDATE scans SET payload=? WHERE scan_id=?",
                                (encode(payload), scan_id))
            else:
                raise ValueError("unknown event kind")
            self._prune(now_ms)
        return _proof(self.db,scan_id,event["kind"])


def read_latest(path, *, now_ms=None, stale_seconds=300, health=None):
    """Read-only API helper. Never creates a database or masks a stale success."""
    explicit_now = now_ms
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    health = health or {}
    from . import collector_health as H
    if health.get('health_schema') == H.SCHEMA:
        try:
            source,evidence=H.bound_snapshot(path,now_ms=explicit_now, fresh_ms=stale_seconds*1000)
            return dict(schema_version=SCHEMA,status='ok',collector_status=health.get('status'),
                        health=health,scan=source[0],causes=source[2],causes_complete=True,
                        age_seconds=max(0,(now_ms-source[0]['as_of_ms'])/1000),collector_evidence=evidence)
        except H.Refused as exc:
            return dict(schema_version=SCHEMA,status='degraded',health=health,scan=None,causes=[],
                        collector_status=exc.reason,collector_evidence=exc.evidence)
    failed = bool(health.get("errors", 0) or health.get("last_error") or health.get("kernel_error"))
    result = {"schema_version": SCHEMA, "status": "error" if failed else "waiting",
              "health": health, "scan": None, "causes": []}
    if not Path(path).exists():
        return result
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=0.05)) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT scan_id,as_of_ms,payload,causes_complete FROM scans "
                             "ORDER BY as_of_ms DESC,rowid DESC LIMIT 1").fetchone()
            if not row:
                return result
            sid, as_of, payload, complete = row
            result["causes_complete"] = bool(complete)
            if payload is None:
                result.update(status="incomplete", scan={"scan_id": sid, "as_of_ms": as_of})
            else:
                result.update(status="ok" if complete else "pending_causes", scan=json.loads(payload))
            result["causes"] = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM causes WHERE scan_id=? ORDER BY symbol,event_id", (sid,))]
            result["age_seconds"] = max(0, (now_ms - as_of) / 1000)
            if now_ms - as_of > stale_seconds * 1000:
                result["status"] = "stale"
            elif health.get("last_scan_id") and health["last_scan_id"] != sid:
                result["status"] = "pending" if not health.get("last_error") else "error"
            elif health.get("last_error") or health.get("kernel_error"):
                result["status"] = "degraded"
            if not health or now_ms - health.get("updated_ms", 0) > stale_seconds * 1000:
                result["collector_status"] = "unknown" if not health else "stale"
                if result["status"] == "ok":
                    result["status"] = "health_unknown"
            else:
                result["collector_status"] = health.get("status", "unknown")
    except (sqlite3.Error, ValueError, OSError):
        result["status"] = "unavailable"
    if result['status']=='ok': result['status']='health_unknown'
    return result


def export_scan(path, scan_id, destination):
    """Explicit immutable export, outside operational retention. No overwrite."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM scans WHERE scan_id=?", (scan_id,)).fetchone()
        if not row or not row[0]:
            raise ValueError("scan unavailable")
        versions = [dict(zip(("version_id", "first_seen_ms", "previous_value_hash", "payload"), r))
                    for r in db.execute("SELECT v.id,v.first_seen_ms,v.previous_value_hash,v.payload "
                                        "FROM versions v JOIN scan_versions s ON v.id=s.version_id "
                                        "WHERE s.scan_id=?", (scan_id,))]
        causes = [json.loads(r[0]) for r in db.execute("SELECT payload FROM causes WHERE scan_id=?", (scan_id,))]
    with Path(destination).open("x") as out:
        out.write(encode({"scan": json.loads(row[0]), "versions": versions, "causes": causes}))
