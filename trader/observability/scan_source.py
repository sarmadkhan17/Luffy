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
 detail TEXT NOT NULL,
 anchored_detail TEXT
);
"""
LIMIT = 2 * 1024**2


def _anchored(text):
    # Anchor repeated complete immutable membership receipts, retaining exact
    # original bytes/order/format rather than reserializing the scan. A shifted
    # prefix otherwise makes every copy occupy different fixed-size chunks.
    value = json.loads(text)
    receipts = {json.dumps(m['source_receipt'], sort_keys=True, separators=(',', ':'), allow_nan=False)
                for m in value.get('membership', ()) if isinstance(m.get('source_receipt'), dict)}
    spans = []
    for receipt in receipts:
        if len(receipt) < E.BLOCK:
            continue
        offset = 0
        while (start := text.find(receipt, offset)) >= 0:
            spans.append((start, start + len(receipt)))
            offset = start + len(receipt)
    parts = []
    offset = 0
    for start, end in sorted(spans, key=lambda span:(span[0], -span[1])):
        if start < offset:
            continue
        parts.extend(E.pieces(text[offset:start]))
        parts.extend(E.pieces(text[start:end]))
        offset = end
    parts.extend(E.pieces(text[offset:]))
    return E.FrozenJSON(tuple(parts))


def publish(db, scan_id, text):
    # Small publications retain their existing delivery and diagnostics.
    if len(text.encode()) <= LIMIT:
        return
    # Store calls ensure() before starting their transaction.
    marker = E.store(db, _anchored(text))
    if len(marker.encode()) > LIMIT:
        raise ValueError('SOURCE_PAYLOAD_BOUND_EXCEEDED')
    old = db.execute('SELECT detail,anchored_detail FROM scan_sources_v1 WHERE scan_id=?', (scan_id,)).fetchone()
    if old and E.resolve(db, old[0]) != text:
        raise ValueError('SCAN_SOURCE_IDENTITY_CHANGED')
    if old:
        if old[1] is not None and E.resolve(db, old[1]) != text:
            raise ValueError('SCAN_SOURCE_IDENTITY_CHANGED')
        if old[0] != marker and old[1] is None:
            # Keep the original immutable packet and its chunks for exact
            # owner replay; publish one optional equivalent bounded variant.
            db.execute('UPDATE scan_sources_v1 SET anchored_detail=? WHERE scan_id=? AND anchored_detail IS NULL',
                       (marker, scan_id))
    else:
        db.execute('INSERT INTO scan_sources_v1(scan_id,detail) VALUES (?,?)', (scan_id, marker))


def ensure(db):
    db.executescript(E.SCHEMA + SCHEMA)
    if 'anchored_detail' not in {r[1] for r in db.execute('PRAGMA table_info(scan_sources_v1)')}:
        db.execute('ALTER TABLE scan_sources_v1 ADD COLUMN anchored_detail TEXT')


def prune(db):
    # Foreign keys prune manifests with scans. Retain only their exact chunks.
    chunks = {sha for (detail,) in db.execute('SELECT detail FROM scan_sources_v1 UNION ALL '
                                            'SELECT anchored_detail FROM scan_sources_v1 WHERE anchored_detail IS NOT NULL')
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
    tables = set(json.loads(db.execute("SELECT json_group_array(name) FROM sqlite_master WHERE type='table'").fetchone()[0]))
    column = ('COALESCE(anchored_detail,detail)' if 'anchored_detail' in json.loads(db.execute(
               "SELECT json_group_array(name) FROM pragma_table_info('scan_sources_v1')").fetchone()[0]) else 'detail') if 'scan_sources_v1' in tables else 'detail'
    publication = (db.execute('SELECT '+column+' FROM scan_sources_v1 WHERE scan_id=?', (sid,)).fetchone()
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
