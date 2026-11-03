"""Offline cold exports with revalidated, dry-run-by-default bounded deletion.

No scheduler, live database opener, VACUUM, or consumer SQL rewriting is installed.
Callers supply an isolated-copy connection. Production-volume paths are refused.

Consumer map (source inspection, not a claim of closed history):
* votes.cycle_id -> cycles.id; decisions/outcomes join by cycle and symbol;
  trades join decisions.id. Calibration, weights_online, meta_label, owner reads
  and GraphQL consume these joins. learning.capture_runtime freezes vote copies
  in the retained reasons source, rather than retaining an SQL vote-id pointer.
* brain_events.detail and control_events.detail can hold journal markers.
* learning_{source_blobs,registrations,outcome_captures,actions,
  decision_source_manifests,produced_chains}.payload can hold the same markers.
* portfolio evaluations.payload/state.inputs_json, scan_sources_v1 detail fields,
  admission/research payloads and future tables can hold indirect references.

Reachability therefore discovers ALL ordinary tables/columns, including newly
introduced JSON columns and declared foreign keys. It walks only rooted blobs,
not orphan-to-orphan graphs. Ambiguous binary/nested encodings refuse pruning.
Direct retained vote_id/vote_ids references and incoming FKs pin vote rows.
Historical SQL consumers must explicitly use read_votes for cold history.
"""
from __future__ import annotations

import base64
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile

from trader.core import journal_evidence as E

SCHEMA = 'luffy-cold-archive.v1'
BLOBS = 'journal_evidence_blobs_v1'
HEX = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])')
MAX_LINE = 64 * 1024**2


class Refused(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _guard(db):
    for _, name, filename in db.execute('PRAGMA database_list'):
        if name not in ('main', 'temp'):
            raise Refused('attached_database_scope')
        if filename:
            path = Path(filename).resolve()
            if path.is_relative_to('/mnt/luffy-data'):
                raise Refused('production_volume_refused')
            if path.stat().st_nlink != 1:
                raise Refused('hardlinked_database_refused')


@contextmanager
def _snapshot(db):
    _guard(db)
    owned = not db.in_transaction
    if owned:
        db.execute('BEGIN')
    try:
        yield
    finally:
        if owned:
            db.rollback()


def reference_inventory(db):
    """Discover physical tables, all columns and incoming FK references."""
    _guard(db)
    result = {}
    for name, ddl in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name.startswith('sqlite_'):
            continue
        if 'CREATE VIRTUAL TABLE' in ddl.upper():
            raise Refused('virtual_table_scope:' + name)
        result[name] = dict(sql=ddl,
            columns=[tuple(r) for r in db.execute('PRAGMA table_xinfo(' + _q(name) + ')')],
            foreign_keys=[tuple(r) for r in db.execute('PRAGMA foreign_key_list(' + _q(name) + ')')])
    return result


def _cell(value):
    if value is None:
        return ['null']
    if isinstance(value, int):
        return ['integer', str(value)]
    if isinstance(value, float):
        return ['real', struct.pack('>d', value).hex()]
    if isinstance(value, str):
        return ['text', value]
    if isinstance(value, bytes):
        return ['blob', base64.b64encode(value).decode('ascii')]
    raise Refused('unsupported_sqlite_value')


def _uncell(value):
    kind = value[0]
    if kind == 'null' and len(value) == 1:
        return None
    if len(value) != 2:
        raise Refused('invalid_typed_cell')
    if kind == 'integer':
        return int(value[1])
    if kind == 'real':
        return struct.unpack('>d', bytes.fromhex(value[1]))[0]
    if kind == 'text':
        return value[1]
    if kind == 'blob':
        return base64.b64decode(value[1], validate=True)
    raise Refused('invalid_typed_cell')


def _line(row):
    return _json([_cell(v) for v in row]) + b'\n'


def _utc(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def _cutoff(value):
    return _utc(value).isoformat()


@dataclass(frozen=True)
class Reachability:
    blobs: frozenset[str]
    vote_ids: frozenset[int]
    scanned_columns: tuple[tuple[str, str], ...]


def reachable(db):
    """Verified transitive roots from every retained physical table/column.

    Unknown hash-bearing text conservatively pins matching blobs. Invalid
    markers, missing explicit blob keys, and opaque binary cells fail closed.
    Logical-image sqlite_blob_v1 cells use the existing base64 convention.
    """
    with _snapshot(db):
        inventory = reference_inventory(db)
        keys = {r[0] for r in db.execute(f'SELECT sha256 FROM {BLOBS}')} if BLOBS in inventory else set()
        roots, vote_ids, queue, visited = set(), set(), deque(), set()
        columns = []

        def add(key, explicit=False):
            if not isinstance(key, str):
                if explicit:
                    raise Refused('invalid_blob_reference')
                return
            if key not in keys:
                if explicit:
                    raise Refused('dangling_blob_reference:' + str(key))
                return
            if key not in roots:
                roots.add(key)
                queue.append(key)

        def inspect(value, key=None):
            if key in ('vote_id', 'vote_ids'):
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, bool) or not isinstance(item, (str, int)):
                        raise Refused('ambiguous_vote_reference')
                    try:
                        vote_ids.add(int(item))
                    except ValueError as exc:
                        raise Refused('ambiguous_vote_reference') from exc
            if key in ('blob_sha256', 'journal_blob_sha256', 'blob_key', 'blob_keys'):
                for item in value if isinstance(value, list) else [value]:
                    add(item, explicit=True)
            if isinstance(value, dict):
                if set(value) == {'sqlite_blob_v1'}:
                    inspect(base64.b64decode(value['sqlite_blob_v1'], validate=True))
                else:
                    for k, v in value.items():
                        inspect(v, k)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
            elif isinstance(value, bytes):
                try:
                    inspect(value.decode('utf-8'))
                except UnicodeDecodeError as exc:
                    raise Refused('opaque_binary_reference_scope') from exc
            elif isinstance(value, str):
                for candidate in HEX.findall(value):
                    add(candidate.lower())
                # Parse JSON first so escaped nested markers regain exact bytes.
                try:
                    parsed = json.loads(value)
                except (ValueError, RecursionError):
                    parsed = None
                if isinstance(parsed, (dict, list, str)) and parsed != value:
                    inspect(parsed)
                    return
                offset = 0
                while (start := value.find(E.PREFIX, offset)) >= 0:
                    try:
                        _, end = json.JSONDecoder().raw_decode(value[start + len(E.PREFIX):])
                        marker = value[start:start + len(E.PREFIX) + end]
                        manifest = json.loads(marker[len(E.PREFIX):])
                        logical = E.resolve(db, marker, max_bytes=MAX_LINE)
                        for chunk in manifest['chunks']:
                            add(chunk, explicit=True)
                    except (ValueError, KeyError, TypeError) as exc:
                        raise Refused('invalid_retained_manifest') from exc
                    fingerprint = _sha(marker.encode())
                    if fingerprint not in visited:
                        visited.add(fingerprint)
                        inspect(logical)
                    offset = start + len(marker)

        # DDL may itself retain constant references in a view/trigger/default.
        for (ddl,) in db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
            inspect(ddl)
        for table, info in inventory.items():
            if table == BLOBS:
                continue
            names = [c[1] for c in info['columns']]
            columns.extend((table, n) for n in names)
            for row in db.execute('SELECT ' + ','.join(map(_q, names)) + ' FROM ' + _q(table)):
                for name, value in zip(names, row):
                    inspect(value, name)
        while queue:
            sha = queue.popleft()
            row = db.execute(f'SELECT byte_length,codec,payload FROM {BLOBS} WHERE sha256=?', (sha,)).fetchone()
            inspect(E._blob(tuple(row), sha))
        return Reachability(frozenset(roots), frozenset(vote_ids), tuple(columns))


def _vote_query(db):
    inventory = reference_inventory(db)
    required = dict(votes={'id', 'cycle_id', 'ts', 'symbol', 'agent'},
        cycles={'id', 'ts', 'symbol'}, decisions={'id', 'cycle_id', 'ts', 'action', 'executed'},
        outcomes={'decision_id', 'cycle_id', 'ts', 'resolved_at', 'fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_24h'},
        trades={'decision_id', 'status', 'opened_at', 'closed_at'})
    for table, names in required.items():
        if table not in inventory or not names <= {c[1] for c in inventory[table]['columns']}:
            raise Refused('unknown_settlement_schema:' + table)
    if not any(c[1] == 'id' and c[2].upper() == 'INTEGER' and c[5] == 1
               for c in inventory['votes']['columns']):
        raise Refused('unknown_vote_identity_schema')
    cycle_status = (' AND c.status IN (\'closed\',\'settled\')' if
                    'status' in {c[1] for c in inventory['cycles']['columns']} else '')
    return """SELECT v.* FROM votes v JOIN cycles c ON c.id=v.cycle_id
      WHERE julianday(v.ts)<julianday(:cutoff) AND julianday(c.ts)<julianday(:cutoff)
      AND v.symbol=c.symbol
      AND EXISTS(SELECT 1 FROM decisions d WHERE d.cycle_id=c.id)
      AND NOT EXISTS(SELECT 1 FROM votes x WHERE x.cycle_id=c.id
        AND (julianday(x.ts) IS NULL OR julianday(x.ts)>=julianday(:cutoff)))
      AND NOT EXISTS(SELECT 1 FROM outcomes o WHERE o.cycle_id=c.id
        AND (o.resolved_at IS NULL OR julianday(o.resolved_at) IS NULL
          OR julianday(o.ts) IS NULL OR julianday(o.resolved_at)<julianday(o.ts)
          OR julianday(o.resolved_at)>=julianday(:cutoff)
          OR o.fwd_ret_1h IS NULL OR o.fwd_ret_4h IS NULL OR o.fwd_ret_24h IS NULL))
      AND NOT EXISTS(SELECT 1 FROM decisions d WHERE d.cycle_id=c.id AND
        (julianday(d.ts) IS NULL OR julianday(d.ts)>=julianday(:cutoff)
         OR d.action NOT IN ('HOLD','BUY','SELL') OR d.action IS NULL
         OR d.executed IS NULL OR d.executed NOT IN (0,1)
         OR (d.action='HOLD' AND d.executed!=0)
         OR (d.action!='HOLD' AND NOT EXISTS(SELECT 1 FROM outcomes o WHERE o.decision_id=d.id
              AND o.cycle_id=c.id AND o.resolved_at IS NOT NULL AND o.fwd_ret_1h IS NOT NULL
              AND o.fwd_ret_4h IS NOT NULL AND o.fwd_ret_24h IS NOT NULL))
         OR (d.executed=1 AND NOT EXISTS(SELECT 1 FROM trades t WHERE t.decision_id=d.id))))
      AND NOT EXISTS(SELECT 1 FROM trades t JOIN decisions d ON d.id=t.decision_id
        WHERE d.cycle_id=c.id AND (t.status IS NULL OR t.status!='closed'
          OR t.closed_at IS NULL OR julianday(t.closed_at) IS NULL
          OR julianday(t.opened_at) IS NULL OR julianday(t.closed_at)<julianday(t.opened_at)
          OR julianday(t.closed_at)>=julianday(:cutoff)))
      AND NOT EXISTS(SELECT 1 FROM trades t WHERE (t.decision_id IS NULL
        OR NOT EXISTS(SELECT 1 FROM decisions d WHERE d.id=t.decision_id))
        AND (t.status IS NULL OR t.status!='closed'))
    """ + cycle_status


def _incoming(inventory, table, pk):
    refs = []
    for other, info in inventory.items():
        for fk in info['foreign_keys']:
            if fk[2] == table:
                if fk[4] not in (None, pk):
                    raise Refused('unknown_incoming_foreign_key')
                refs.append((other, fk[3]))
    return refs


def _pinned_fk(db, refs, value):
    return any(db.execute('SELECT 1 FROM ' + _q(table) + ' WHERE ' + _q(column) + '=? LIMIT 1',
                         (value,)).fetchone() for table, column in refs)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _export(db, destination, table, cutoff=None):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.cold-archive-', dir=destination.parent))
    try:
        with _snapshot(db):
            inventory = reference_inventory(db)
            if table not in inventory:
                raise Refused('missing_source_table')
            info = inventory[table]
            names = [c[1] for c in info['columns']]
            if any(c[6] != 0 for c in info['columns']):
                raise Refused('generated_source_columns')
            reach = reachable(db)
            incoming = _incoming(inventory, table, 'id' if table == 'votes' else 'sha256')
            if table == 'votes':
                cutoff = _cutoff(cutoff)
                query = _vote_query(db) + ' ORDER BY strftime(\'%Y-%m\',v.ts),v.symbol,v.agent,julianday(v.ts),v.id'
                rows = db.execute(query, {'cutoff': cutoff})
            else:
                rows = db.execute(f'SELECT * FROM {BLOBS} ORDER BY sha256')
            parts, writer, raw_file = [], None, None

            def finish():
                nonlocal writer, raw_file
                if writer is None:
                    return
                writer.close()
                raw_file.flush()
                os.fsync(raw_file.fileno())
                raw_file.close()
                part['sha256'] = _file_sha(stage / part['file'])
                part['rows_sha256'] = row_hash.hexdigest()
                parts.append(dict(part))
                writer = raw_file = None

            try:
                for row in rows:
                    values = tuple(row)
                    record = dict(zip(names, values))
                    if table == 'votes':
                        if record['id'] in reach.vote_ids or _pinned_fk(db, incoming, record['id']):
                            continue
                        partition = _utc(record['ts']).strftime('%Y-%m')
                    else:
                        if record['sha256'] in reach.blobs:
                            continue
                        E._blob((record['byte_length'], record['codec'], record['payload']), record['sha256'])
                        partition = record['sha256'][:2]
                    if writer is None or partition != part['partition']:
                        finish()
                        part = dict(partition=partition, file=table+'-'+partition+'.jsonl.gz', count=0)
                        row_hash = hashlib.sha256()
                        raw_file = (stage / part['file']).open('xb')
                        writer = gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=raw_file)
                    line = _line(values)
                    if len(line) > MAX_LINE:
                        raise Refused('archive_row_bound')
                    writer.write(line)
                    row_hash.update(line)
                    part['count'] += 1
                finish()
            finally:
                if writer is not None:
                    writer.close()
                if raw_file is not None:
                    raw_file.close()
            manifest = dict(schema=SCHEMA, table=table, format='typed-jsonl.gzip.v1',
                columns=names, table_sql=info['sql'], table_columns=info['columns'],
                user_version=db.execute('PRAGMA user_version').fetchone()[0], cutoff=cutoff,
                order=['symbol', 'agent', 'utc(ts)', 'id'] if table == 'votes' else ['sha256'],
                count=sum(p['count'] for p in parts), parts=parts)
            manifest['manifest_sha256'] = _sha(_json(manifest))
            _write(stage / 'manifest.json', _json(manifest))
        # Independently reread compressed files and count/hash decoded rows
        # before making the archive visible. No database rows have been deleted.
        with _verified(stage):
            pass
        _sync_dir(stage)
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(stage, destination)
        _sync_dir(destination.parent)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def export_votes(db, destination, *, cutoff):
    """Export only provably settled historical monthly vote partitions."""
    return _export(db, destination, 'votes', cutoff)


def export_blobs(db, destination):
    """Archive verified orphan blobs; reachable blobs are never export candidates."""
    return _export(db, destination, BLOBS)


@contextmanager
def _verified(directory):
    """Verify everything into a stable /tmp spool before exposing any row."""
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    claimed = manifest.get('manifest_sha256')
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    if claimed != _sha(_json(body)) or body['schema'] != SCHEMA or body['format'] != 'typed-jsonl.gzip.v1':
        raise Refused('archive_manifest_verification')
    if body['table'] not in ('votes', BLOBS) or len(set(body['columns'])) != len(body['columns']):
        raise Refused('archive_schema_verification')
    count, filenames = 0, set()
    with tempfile.TemporaryFile(dir='/tmp') as spool:
        for part in body['parts']:
            name = part['file']
            if Path(name).name != name or name in filenames or (directory / name).is_symlink():
                raise Refused('archive_partition_path')
            filenames.add(name)
            with tempfile.TemporaryFile(dir='/tmp') as compressed:
                digest = hashlib.sha256()
                with (directory / name).open('rb') as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block)
                        compressed.write(block)
                if digest.hexdigest() != part['sha256']:
                    raise Refused('archive_file_digest')
                compressed.seek(0)
                row_hash, rows = hashlib.sha256(), 0
                with gzip.GzipFile(fileobj=compressed, mode='rb') as stream:
                    while line := stream.readline(MAX_LINE + 1):
                        if len(line) > MAX_LINE or not line.endswith(b'\n'):
                            raise Refused('archive_row_bound')
                        values = [_uncell(v) for v in json.loads(line)]
                        if len(values) != len(body['columns']) or _line(values) != line:
                            raise Refused('archive_row_encoding')
                        row_hash.update(line)
                        spool.write(line)
                        rows += 1
                if rows != part['count'] or row_hash.hexdigest() != part['rows_sha256']:
                    raise Refused('archive_count_or_rows_digest')
                count += rows
        if count != body['count']:
            raise Refused('archive_count')
        spool.seek(0)
        yield manifest, spool


def read_archive(directory):
    """Yield original SQLite values only after every partition is verified."""
    with _verified(directory) as (manifest, spool):
        for line in spool:
            yield dict(zip(manifest['columns'], (_uncell(v) for v in json.loads(line))))


def read_votes(directories, *, symbol=None, agent=None, cycle_id=None, since=None, until=None):
    """Verified offline history across partitions, preserving exact cell values.

    Filters use UTC timestamps, not lexical ISO ordering. Identical exports
    deduplicate by id; conflicting versions refuse instead of overwriting.
    """
    seen = {}
    since, until = (_utc(since) if since is not None else None), (_utc(until) if until is not None else None)
    for directory in directories:
        with _verified(directory) as (manifest, spool):
            if manifest['table'] != 'votes':
                raise Refused('not_a_votes_archive')
            for line in spool:
                row = dict(zip(manifest['columns'], (_uncell(v) for v in json.loads(line))))
                if ((symbol is not None and row['symbol'] != symbol) or
                    (agent is not None and row['agent'] != agent) or
                    (cycle_id is not None and row['cycle_id'] != cycle_id) or
                    (since is not None and _utc(row['ts']) < since) or
                    (until is not None and _utc(row['ts']) >= until)):
                    continue
                digest = _sha(line)
                if row['id'] in seen:
                    if seen[row['id']] != digest:
                        raise Refused('conflicting_archived_vote')
                    continue
                seen[row['id']] = digest
                yield row


def _recheck_archive(directory, manifest):
    """Cold files must still match the verified spool at the deletion boundary."""
    directory = Path(directory)
    if _json(json.loads((directory / 'manifest.json').read_bytes())) != _json(manifest):
        raise Refused('archive_changed_before_prune')
    for part in manifest['parts']:
        path = directory / part['file']
        if path.is_symlink() or _file_sha(path) != part['sha256']:
            raise Refused('archive_changed_before_prune')


def prune(db, directory, *, dry_run=True, limit=256):
    """Verify cold bytes, then recheck current roots/settlement under one lock.

    At most limit DELETEs commit atomically. Changed source rows, unknown target
    triggers or schema drift refuse the batch. No schema/cursor is written.
    """
    _guard(db)
    if db.in_transaction:
        raise Refused('caller_transaction_open')
    if type(limit) is not int or not 1 <= limit <= 4096 or type(dry_run) is not bool:
        raise Refused('invalid_prune_bound')
    with _verified(directory) as (manifest, spool):
        db.execute('BEGIN' if dry_run else 'BEGIN IMMEDIATE')
        try:
            inventory = reference_inventory(db)
            table = manifest['table']
            if (table not in inventory or inventory[table]['sql'] != manifest['table_sql']
                    or [list(c) for c in inventory[table]['columns']] != manifest['table_columns']
                    or db.execute('PRAGMA user_version').fetchone()[0] != manifest['user_version']):
                raise Refused('source_schema_changed')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchone():
                raise Refused('target_trigger_scope')
            reach = reachable(db)
            pk = 'id' if table == 'votes' else 'sha256'
            incoming = _incoming(inventory, table, pk)
            eligible_query = _vote_query(db) + ' AND v.id=:id' if table == 'votes' else None
            candidates = []
            for line in spool:
                archived = [_uncell(v) for v in json.loads(line)]
                record = dict(zip(manifest['columns'], archived))
                identity = record[pk]
                current = db.execute('SELECT * FROM ' + _q(table) + ' WHERE ' + _q(pk) + '=?', (identity,)).fetchone()
                if current is None:
                    continue  # restart after an already committed prune
                if _line(current) != line:
                    raise Refused('source_row_changed')
                if _pinned_fk(db, incoming, identity):
                    continue
                if table == 'votes':
                    if identity in reach.vote_ids or not db.execute(eligible_query,
                            {'cutoff': manifest['cutoff'], 'id': identity}).fetchone():
                        continue
                else:
                    if identity in reach.blobs:
                        continue
                    E._blob((record['byte_length'], record['codec'], record['payload']), identity)
                candidates.append(identity)
                if len(candidates) == limit:
                    break
            if not dry_run:
                _recheck_archive(directory, manifest)
                for identity in candidates:
                    if db.execute('DELETE FROM ' + _q(table) + ' WHERE ' + _q(pk) + '=?', (identity,)).rowcount != 1:
                        raise Refused('delete_identity_changed')
                db.commit()
            else:
                db.rollback()
            return dict(dry_run=dry_run, table=table, eligible=len(candidates),
                        limit=limit,
                        deleted=0 if dry_run else len(candidates), identities=candidates,
                        archive_sha256=manifest['manifest_sha256'])
        except BaseException:
            db.rollback()
            raise
