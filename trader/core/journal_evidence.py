"""Versioned lossless physical detail storage; no observation/value deduplication.

Only identical UTF-8 serialized byte segments share a SHA256 blob. All event
identities and clocks remain in brain_events. A marker is NOT logical JSON:
readers must resolve it and verify every segment and the complete detail hash.
SQLite backups carry both events and blobs. No external files are dependencies.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass
from trader.core import evidence_zlib

PREFIX = '!luffy-journal-detail.v1!'
BLOCK = 64 * 1024
BLOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_evidence_blobs_v1 (
 sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
 byte_length INTEGER NOT NULL CHECK(byte_length>=0),
 codec TEXT NOT NULL CHECK(codec='zlib.v1'),
 payload BLOB NOT NULL
);
"""
SCHEMA = BLOB_SCHEMA + """
CREATE TABLE IF NOT EXISTS journal_representation_v1 (
 version TEXT PRIMARY KEY, status TEXT NOT NULL, manifest TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS brain_events_logical_v1 AS
 SELECT id,ts,kind,subject,journal_detail_v1(detail) AS detail FROM brain_events;
"""


class EvidenceError(ValueError):
    """A referenced detail cannot be read and verified. Never substitute {}."""


def pieces(raw: str):
    """Bounded exact segments anchored at JSON object boundaries when possible.

    No parsing or normalization: spaces, escape sequences, numbers, ordering
    and non-JSON legacy text are preserved byte for byte.
    """
    start = 0
    while start < len(raw):
        end = min(start + BLOCK, len(raw))
        if end < len(raw):
            anchor = raw.find('},', end, min(end + BLOCK, len(raw)))
            if anchor >= 0:
                end = anchor + 2
        yield raw[start:end]
        start = end


@dataclass(frozen=True)
class FrozenJSON:
    """Pre-serialized immutable source acquisition; shared across decisions."""
    segments: tuple[str, ...]

    @classmethod
    def freeze(cls, value):
        return cls(tuple(pieces(json.dumps(value, sort_keys=True, allow_nan=False))))


def serialize(value):
    """Stream ordinary JSON and already frozen acquisitions without reencoding."""
    if isinstance(value, FrozenJSON):
        yield from value.segments
    elif isinstance(value, dict):
        yield '{'
        for i, (key, item) in enumerate(value.items()):
            if i:
                yield ', '
            encoded_key = json.dumps({key: 0})
            yield encoded_key[1:encoded_key.rfind(':')] + ': '
            yield from serialize(item)
        yield '}'
    elif isinstance(value, (list, tuple)):
        yield '['
        for i, item in enumerate(value):
            if i:
                yield ', '
            yield from serialize(item)
        yield ']'
    else:
        yield json.dumps(value)


def store(conn, detail):
    """Store one manifest; repeated segments are retained exactly once.

    The caller owns the transaction. A hash collision or corrupt existing blob
    refuses the write rather than silently binding to other evidence.
    """
    raw_parts = pieces(detail) if isinstance(detail, str) else serialize(detail)
    hasher = hashlib.sha256()
    size = 0
    refs = []
    buffer = []
    buffered = 0

    pending = {}
    hash_pending = []

    def flush():
        if not pending:
            return
        hasher.update(b''.join(hash_pending))
        hash_pending.clear()
        keys = tuple(pending)
        found = _read_blobs(conn, 'main', keys)
        inserts = []
        for sha, data in pending.items():
            row = found.get(sha)
            if row is None:
                inserts.append((sha, len(data), 'zlib.v1', evidence_zlib.compress(data, 6)))
            elif _blob(row, sha) != data:
                raise EvidenceError('evidence_hash_collision')
        conn.executemany('INSERT INTO journal_evidence_blobs_v1 VALUES (?,?,?,?)', inserts)
        pending.clear()

    def save(raw):
        nonlocal size
        data = raw.encode('utf-8')
        hash_pending.append(data)
        size += len(data)
        sha = _segment_sha(data)
        if sha in pending and pending[sha] != data:
            raise EvidenceError('evidence_hash_collision')
        pending[sha] = data
        refs.append(sha)
        # At most 64 bounded segments await verification/insertion. Keep the
        # original streaming contract even for full owner history exports.
        if len(hash_pending) >= 64:
            flush()

    for part in raw_parts:
        # Preserve pre-frozen segment boundaries. Ordinary tiny JSON tokens
        # accumulate in bounded blocks; never build the giant event string.
        if len(part) >= BLOCK:
            if buffer:
                save(''.join(buffer)); buffer = []; buffered = 0
            for segment in pieces(part):
                save(segment)
        else:
            buffer.append(part); buffered += len(part)
            if buffered >= BLOCK:
                save(''.join(buffer)); buffer = []; buffered = 0
    if buffer:
        save(''.join(buffer))
    flush()
    return PREFIX + json.dumps({'sha256': hasher.hexdigest(), 'byte_length': size, 'chunks': refs}, separators=(',', ':'))


# A content-only memo for the pure segment digest. Exact immutable bytes are
# the key; changed bytes never reuse a digest. This avoids repeatedly yielding
# the GIL to CPU-heavy background work for every identical shared segment.
_SEGMENT_HASHES = OrderedDict()
_SEGMENT_LOCK = threading.Lock()
_SEGMENT_BYTES = 0
_SEGMENT_LIMIT = 64 * 1024**2


def _chunk_sha(data):
    # hashlib releases the GIL above 2047 bytes per update. A bounded chunk
    # can be verified with the identical SHA256 using small updates, avoiding
    # one scheduler handoff per chunk under measured background CPU load.
    digest = hashlib.sha256()
    view = memoryview(data)
    for offset in range(0, len(view), 2047):
        digest.update(view[offset:offset + 2047])
    return digest.hexdigest()


def _read_blobs(conn, schema, keys):
    # One bounded SQLite result rather than per-row cursor/GIL crossings.
    # Hex transports the exact compressed bytes without changing the codec.
    query = f'''SELECT json_group_array(json_array(sha256,byte_length,codec,
                typeof(payload),CASE WHEN typeof(payload)='blob' THEN hex(payload) END))
                FROM {schema}.journal_evidence_blobs_v1 WHERE sha256 IN ('''
    packet = conn.execute(query + ','.join('?' for _ in keys) + ')', keys).fetchone()[0]
    return {sha:(length,codec,bytes.fromhex(payload) if typ=='blob' else None)
            for sha,length,codec,typ,payload in json.loads(packet)}


def _segment_sha(data):
    global _SEGMENT_BYTES
    with _SEGMENT_LOCK:
        hit = _SEGMENT_HASHES.get(data)
        if hit is not None:
            _SEGMENT_HASHES.move_to_end(data)
            return hit
    sha = _chunk_sha(data)
    with _SEGMENT_LOCK:
        if data not in _SEGMENT_HASHES:
            _SEGMENT_HASHES[data] = sha
            _SEGMENT_BYTES += len(data)
            while _SEGMENT_BYTES > _SEGMENT_LIMIT or len(_SEGMENT_HASHES) > 1024:
                old, _ = _SEGMENT_HASHES.popitem(last=False)
                _SEGMENT_BYTES -= len(old)
    return sha


# Cache only bytes already verified against these EXACT stored bytes and
# metadata. No connection identity/data_version assumption can hide corruption.
# Entries from another database are safe only when the entire source is equal.
_VERIFIED = OrderedDict()
_VERIFIED_LOCK = threading.Lock()
_VERIFIED_BYTES = 0
_VERIFIED_LIMIT = 32 * 1024**2


def _blob(row, sha):
    global _VERIFIED_BYTES
    try:
        length, codec, payload = row
        if type(length) is not int or not 0 <= length <= 16 * BLOCK or codec != 'zlib.v1' or not isinstance(payload, bytes):
            raise EvidenceError('evidence_blob_metadata_invalid:' + sha)
    except (TypeError, ValueError) as exc:
        raise EvidenceError('evidence_blob_metadata_invalid:' + sha) from exc
    key = (sha, length, codec, payload)
    with _VERIFIED_LOCK:
        hit = _VERIFIED.get(key)
        if hit is not None:
            _VERIFIED.move_to_end(key)
            return hit
    data = _decode_blob(row, sha)
    cost = len(data) + len(row[2])
    with _VERIFIED_LOCK:
        if key not in _VERIFIED:
            _VERIFIED[key] = data
            _VERIFIED_BYTES += cost
            while _VERIFIED_BYTES > _VERIFIED_LIMIT or len(_VERIFIED) > 1024:
                old, value = _VERIFIED.popitem(last=False)
                _VERIFIED_BYTES -= len(value) + len(old[3])
    return data


def _decode_blob(row, sha):
    try:
        length, codec, payload = row
        if codec != 'zlib.v1' or type(length) is not int or not 0 <= length <= 16 * BLOCK:
            raise EvidenceError('evidence_blob_metadata_invalid:' + sha)
        data = evidence_zlib.decompress(payload, length)
        if _chunk_sha(data) != sha:
            raise EvidenceError('evidence_blob_hash_mismatch:' + sha)
        return data
    except (TypeError, ValueError, zlib.error) as exc:
        raise EvidenceError('evidence_blob_corrupt:' + sha) from exc


def resolve(conn, detail, *, schema='main', deadline=None, max_bytes=None):
    if not isinstance(detail, str) or not detail.startswith(PREFIX):
        return detail
    if schema not in ('main', 'src'):
        raise EvidenceError('evidence_schema_invalid')
    try:
        manifest = json.loads(detail[len(PREFIX):])
        if set(manifest) != {'sha256', 'byte_length', 'chunks'} or not isinstance(manifest['chunks'], list):
            raise EvidenceError('evidence_manifest_invalid')
        if type(manifest['byte_length']) is not int or manifest['byte_length'] < 0:
            raise EvidenceError('evidence_manifest_length_invalid')
        if max_bytes is not None and manifest['byte_length'] > max_bytes:
            raise EvidenceError('evidence_expanded_bound_exceeded')
        out = bytearray()
        # Repeated immutable references within this single resolve share the
        # same fetched, verified physical bytes. Bound the local working set;
        # every new resolve rereads the database, so changed/corrupt sources
        # cannot be hidden by the process-wide content verification memo.
        retained = OrderedDict()
        for offset in range(0, len(manifest['chunks']), 64):
            if deadline is not None and time.monotonic() > deadline:
                raise EvidenceError('evidence_read_deadline_exceeded')
            batch = manifest['chunks'][offset:offset + 64]
            if any(not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha) for sha in batch):
                raise EvidenceError('evidence_reference_invalid')
            keys = tuple(dict.fromkeys(batch))
            missing = tuple(sha for sha in keys if sha not in retained)
            rows = _read_blobs(conn, schema, missing) if missing else {}
            rows.update((sha, retained[sha]) for sha in keys if sha in retained)
            for sha in batch:
                if deadline is not None and time.monotonic() > deadline:
                    raise EvidenceError('evidence_read_deadline_exceeded')
                row = rows.get(sha)
                if row is None:
                    raise EvidenceError('evidence_missing:' + sha)
                out.extend(_blob(row, sha))
                retained[sha] = row
                retained.move_to_end(sha)
                while len(retained) > 64:
                    retained.popitem(last=False)
                if len(out) > manifest['byte_length']:
                    raise EvidenceError('evidence_detail_length_exceeded')
        if len(out) != manifest['byte_length'] or hashlib.sha256(out).hexdigest() != manifest['sha256']:
            raise EvidenceError('evidence_detail_hash_mismatch')
        return out.decode('utf-8')
    except EvidenceError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise EvidenceError('evidence_detail_invalid') from exc


def install(conn, *, schema='main'):
    """Install the verifying SQL function on any supported direct-SQL reader."""
    name = 'journal_detail_v1' if schema == 'main' else 'journal_src_detail_v1'
    conn.create_function(name, 1, lambda detail: resolve(conn, detail, schema=schema))
    return conn


def read_sql(conn, sql):
    """Route Journal SELECTs through the logical view; legacy DBs need no DDL."""
    if not re.search(r'\bbrain_events\b', sql, re.I) or not re.match(r'\s*(SELECT|WITH)\b', sql, re.I):
        return sql
    if conn.execute("SELECT 1 FROM main.sqlite_master WHERE name='brain_events_logical_v1'").fetchone():
        token = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/|\bbrain_events\b", re.I | re.S)
        return token.sub(lambda match: 'brain_events_logical_v1' if match.group().lower() == 'brain_events' else match.group(), sql)
    return sql
