"""Exact bounded Portfolio delivery of retained current Attention evidence.

The existing logical scan and replay hashes stay unchanged. This publication
uses the installed lossless journal detail codec; readers verify both the
physical source identity and all referenced bytes. No historical scan expands.
"""
import hashlib
import json
import time
from trader.core import journal_evidence as E

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_sources_v1 (
 scan_id TEXT PRIMARY KEY REFERENCES scans(scan_id) ON DELETE CASCADE,
 detail TEXT NOT NULL
);
"""
LIMIT = 2 * 1024**2


def publish(db, scan_id, text):
    # Small publications retain their existing delivery and diagnostics.
    if len(text.encode()) <= LIMIT:
        return
    # Store calls ensure() before starting their transaction.
    marker = E.store(db, text)
    if len(marker.encode()) > LIMIT:
        raise ValueError('SOURCE_PAYLOAD_BOUND_EXCEEDED')
    old = db.execute('SELECT detail FROM scan_sources_v1 WHERE scan_id=?', (scan_id,)).fetchone()
    if old and E.resolve(db, old[0]) != text:
        raise ValueError('SCAN_SOURCE_IDENTITY_CHANGED')
    db.execute('INSERT OR IGNORE INTO scan_sources_v1 VALUES (?,?)', (scan_id, marker))


def ensure(db):
    db.executescript(E.SCHEMA + SCHEMA)


def prune(db):
    # Foreign keys prune manifests with scans. Retain only their exact chunks.
    chunks = {sha for (detail,) in db.execute('SELECT detail FROM scan_sources_v1')
              for sha in json.loads(detail[len(E.PREFIX):])['chunks']}
    for (sha,) in db.execute('SELECT sha256 FROM journal_evidence_blobs_v1').fetchall():
        if sha not in chunks:
            db.execute('DELETE FROM journal_evidence_blobs_v1 WHERE sha256=?', (sha,))


def read(db, sid, deadline, max_bytes=64*1024**2):
    row = db.execute('SELECT scan_id,payload FROM scans WHERE scan_id=? '
                     'AND payload IS NOT NULL AND causes_complete=1', (sid,)).fetchone()
    if not row:
        return None
    sid, logical = row
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    publication = (db.execute('SELECT detail FROM scan_sources_v1 WHERE scan_id=?', (sid,)).fetchone()
                   if 'scan_sources_v1' in tables else None)
    if publication is None:
        if len(logical.encode()) > LIMIT:
            raise ValueError('SOURCE_PAYLOAD_BOUND_EXCEEDED')
        text = logical
    else:
        marker = publication[0]
        if len(marker.encode()) > LIMIT or not marker.startswith(E.PREFIX):
            raise ValueError('SOURCE_PAYLOAD_BOUND_EXCEEDED')
        # Check against the exact retained source, never trust a detached cache.
        m = json.loads(marker[len(E.PREFIX):])
        if hashlib.sha256(logical.encode()).hexdigest() != m['sha256']:
            raise ValueError('SCAN_SOURCE_IDENTITY_CHANGED')
        text = E.resolve(db, marker, deadline=deadline, max_bytes=max_bytes)
    result = json.loads(text)
    if time.monotonic() > deadline:
        raise ValueError('SOURCE_READ_DEADLINE_EXCEEDED')
    return result


def latest(db, deadline, max_bytes=64*1024**2):
    row = db.execute('SELECT scan_id FROM scans WHERE payload IS NOT NULL '
                     'AND causes_complete=1 ORDER BY rowid DESC LIMIT 1').fetchone()
    return read(db, row[0], deadline, max_bytes) if row else None
