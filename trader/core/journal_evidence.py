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
from dataclasses import dataclass

PREFIX = '!luffy-journal-detail.v1!'
BLOCK = 64 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_evidence_blobs_v1 (
 sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
 byte_length INTEGER NOT NULL CHECK(byte_length>=0),
 codec TEXT NOT NULL CHECK(codec='zlib.v1'),
 payload BLOB NOT NULL
);
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

    def save(raw):
        nonlocal size
        data = raw.encode('utf-8')
        hasher.update(data)
        size += len(data)
        sha = hashlib.sha256(data).hexdigest()
        row = conn.execute('SELECT byte_length,codec,payload FROM journal_evidence_blobs_v1 WHERE sha256=?', (sha,)).fetchone()
        if row is None:
            conn.execute('INSERT INTO journal_evidence_blobs_v1 VALUES (?,?,?,?)',
                         (sha, len(data), 'zlib.v1', zlib.compress(data, 6)))
        else:
            existing = _blob(row, sha)
            if existing != data:
                raise EvidenceError('evidence_hash_collision')
        refs.append(sha)

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
    return PREFIX + json.dumps({'sha256': hasher.hexdigest(), 'byte_length': size, 'chunks': refs}, separators=(',', ':'))


def _blob(row, sha):
    try:
        length, codec, payload = row
        if codec != 'zlib.v1' or type(length) is not int or not 0 <= length <= 16 * BLOCK:
            raise EvidenceError('evidence_blob_metadata_invalid:' + sha)
        decoder = zlib.decompressobj()
        data = decoder.decompress(payload, length + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(data) != length:
            raise EvidenceError('evidence_blob_length_invalid:' + sha)
        if hashlib.sha256(data).hexdigest() != sha:
            raise EvidenceError('evidence_blob_hash_mismatch:' + sha)
        return data
    except (TypeError, ValueError, zlib.error) as exc:
        raise EvidenceError('evidence_blob_corrupt:' + sha) from exc


def resolve(conn, detail, *, schema='main'):
    if not isinstance(detail, str) or not detail.startswith(PREFIX):
        return detail
    if schema not in ('main', 'src'):
        raise EvidenceError('evidence_schema_invalid')
    try:
        manifest = json.loads(detail[len(PREFIX):])
        if set(manifest) != {'sha256', 'byte_length', 'chunks'} or not isinstance(manifest['chunks'], list):
            raise EvidenceError('evidence_manifest_invalid')
        out = bytearray()
        for sha in manifest['chunks']:
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                raise EvidenceError('evidence_reference_invalid')
            row = conn.execute(f'SELECT byte_length,codec,payload FROM {schema}.journal_evidence_blobs_v1 WHERE sha256=?', (sha,)).fetchone()
            if row is None:
                raise EvidenceError('evidence_missing:' + sha)
            out.extend(_blob(row, sha))
        if len(out) != manifest['byte_length'] or hashlib.sha256(out).hexdigest() != manifest['sha256']:
            raise EvidenceError('evidence_detail_hash_mismatch')
        return out.decode('utf-8')
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
