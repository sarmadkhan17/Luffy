"""DA-10 conservative, copy-only historical cleanup.

Only redundant, unreferenced portfolio state segments are eligible. Unique
orphan bytes, receipt revisions, research budgets, all events and unknown
schemas are retained. No production apply entry point exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import shutil
import time

from trader.core import journal_evidence as E
from trader.portfolio.allocator import canonical

BLOBS = 'journal_evidence_blobs_v1'
HEX = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def digest_row(h, row):
    h.update(b'R' + struct.pack('>Q', len(row)))
    for value in row:
        if value is None:
            h.update(b'N')
        elif isinstance(value, int):
            h.update(b'I' + struct.pack('>q', value))
        elif isinstance(value, float):
            h.update(b'F' + struct.pack('>d', value))
        else:
            data = value.encode() if isinstance(value, str) else bytes(value)
            h.update((b'T' if isinstance(value, str) else b'B') + struct.pack('>Q', len(data)) + data)


def schema(db):
    return list(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))


def tables(db):
    return list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"))


def rows(db, name, ddl):
    columns = [r[1] for r in db.execute('PRAGMA table_xinfo(' + quote(name) + ')') if r[6] == 0]
    has_rowid = 'WITHOUT ROWID' not in ddl.upper()
    prefix = 'rowid,' if has_rowid else ''
    query = 'SELECT ' + prefix + ','.join(map(quote, columns)) + ' FROM ' + quote(name)
    query += ' ORDER BY ' + ('rowid' if has_rowid else ','.join(map(quote, columns)))
    return db.execute(query)


def inventory(db):
    result = {}
    for name, ddl in tables(db):
        h = hashlib.sha256(); count = 0
        for row in rows(db, name, ddl):
            digest_row(h, row); count += 1
        result[name] = {'rows': count, 'typed_rowid_sha256': h.hexdigest()}
    return {'tables': result, 'schema_sha256': hashlib.sha256(canonical(schema(db)).encode()).hexdigest(),
            'page_size': db.execute('PRAGMA page_size').fetchone()[0],
            'pages': db.execute('PRAGMA page_count').fetchone()[0],
            'free_pages': db.execute('PRAGMA freelist_count').fetchone()[0],
            'objects': list(db.execute('SELECT name,sum(pgsize),sum(payload),sum(unused) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC'))}


def snapshot(source, destination):
    """Copy stable database+sidecars without opening a source in SQLite.

    A changed file set or metadata refuses the snapshot. Never checkpoint,
    recover WAL, create SHM, or acquire a SQLite writer on the source.
    """
    source = Path(source).resolve(); destination = Path(destination).resolve()
    if destination == source or source in destination.parents:
        raise ValueError('DESTINATION_WITHIN_SOURCE_REFUSED')
    if destination.exists():
        raise FileExistsError('DESTINATION_EXISTS')
    destination.mkdir(parents=True)
    copies = destination/'copies'; copies.mkdir()
    receipt = {}
    def signature(path):
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns, stat.st_ino]
    paths = sorted(source.glob('*.db'))
    for path in paths:
        def files():
            return [path] + [Path(str(path)+suffix) for suffix in ('-wal','-shm','-journal')
                             if Path(str(path)+suffix).exists()]
        before = {str(p): signature(p) for p in files()}
        for item in before:
            shutil.copyfile(item, copies/Path(item).name)
        after = {str(p): signature(p) for p in files()}
        if before != after:
            raise ValueError('SOURCE_CHANGED_REFUSED:' + path.name)
        hashes = {}
        for item in before:
            with (copies/Path(item).name).open('rb') as stream:
                hashes[Path(item).name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        receipt[path.name] = {'source_files': before, 'stable': True, 'copy_files': hashes}
        (destination/'snapshot.json').write_text(json.dumps(receipt, indent=2))
    if sorted(source.glob('*.db')) != paths:
        raise ValueError('SOURCE_FILE_SET_CHANGED_REFUSED')
    return copies


def guard_copy(path):
    path = Path(path).resolve()
    if path.stat().st_nlink != 1:
        raise ValueError('HARDLINK_DATABASE_REFUSED')
    receipt = json.loads((path.parent.parent / 'snapshot.json').read_text())
    if path.name not in receipt or path.parent.name != 'copies':
        raise ValueError('UNRECOGNIZED_ISOLATED_COPY')
    for source in receipt[path.name]['source_files']:
        if path.samefile(source):
            raise ValueError('SOURCE_DATABASE_REFUSED')
    return path


def blob_digest(db):
    digest = hashlib.sha256()
    for row in db.execute('SELECT * FROM '+BLOBS+' ORDER BY sha256'):
        digest_row(digest, row)
    return digest.hexdigest()


def read_copy(path):
    # No source/production database is opened by SQLite, including for reads.
    path = guard_copy(path)
    db = sqlite3.connect(path)
    E.install(db)
    db.execute('PRAGMA temp_store=MEMORY')
    db.execute('PRAGMA cache_size=-32768')
    return db


def read_normalized_copy(path):
    path = guard_copy(path)
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('READONLY_SCAN_PENDING_SIDECAR_REFUSED:' + path.name)
    db = E.install(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True))
    db.execute('PRAGMA query_only=ON')
    return db


def references(db):
    """Conservative roots: every hash in every surviving cell, not just FKs.

    Manifest resolution verifies complete hashes and all chunk dependencies.
    Decoded blobs are also scanned, protecting embedded/cross-record hashes.
    """
    roots = set(); manifests = 0
    for name, ddl in tables(db):
        if name == BLOBS:
            continue
        for row in rows(db, name, ddl):
            for value in row:
                if isinstance(value, bytes):
                    value = value.decode('utf-8', errors='ignore')
                if not isinstance(value, str):
                    continue
                roots.update(HEX.findall(value))
                if value.startswith(E.PREFIX):
                    logical = E.resolve(db, value)
                    roots.update(HEX.findall(logical)); manifests += 1
                elif E.PREFIX in value:
                    # Unknown nested encoding cannot be proven unreferenced.
                    raise ValueError('NESTED_MANIFEST_REFUSED:' + name)
    if any(name == BLOBS for name, _ in tables(db)):
        for sha, n, codec, payload in db.execute('SELECT * FROM ' + BLOBS):
            logical = E._blob((n, codec, payload), sha).decode('utf-8')
            roots.update(HEX.findall(logical))
            if E.PREFIX in logical:
                raise ValueError('EMBEDDED_MANIFEST_REFUSED')
    return roots, manifests


def portfolio_candidates(db, roots):
    """Exact witness: old state chunks reproduced from surviving evaluation inputs.

    This proves byte redundancy, in addition to absence of references. An
    orphan which cannot be reconstructed is unique evidence and is refused.
    """
    actual = {name for name, _ in tables(db)}
    expected = {'state', 'checkpoints', 'evaluations', BLOBS}
    if actual != expected:
        return [], {'reason': 'UNKNOWN_SCHEMA_RETAINED', 'tables': sorted(actual)}
    expected_columns = {'state': ['id','inputs_json','previous_proposal_id'],
                        'checkpoints': ['event_id','payload'], 'evaluations': ['cut_id','payload'],
                        BLOBS: ['sha256','byte_length','codec','payload']}
    for name, expected_cols in expected_columns.items():
        if [r[1] for r in db.execute('PRAGMA table_info('+quote(name)+')')] != expected_cols:
            return [], {'reason': 'UNKNOWN_COLUMNS_RETAINED'}
    if list(db.execute('PRAGMA foreign_key_check')):
        raise ValueError('FOREIGN_KEY_CHECK_FAILED')
    # Unknown trigger side effects may mutate immutable history on deletion.
    if list(db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (BLOBS,))):
        raise ValueError('BLOB_TRIGGER_REFUSED')
    witnesses = {}
    for cut_id, marker in db.execute('SELECT cut_id,payload FROM evaluations ORDER BY rowid'):
        body = json.loads(E.resolve(db, marker))
        if body.get('schema') != 'runtime-portfolio-checkpoint.v1' or body.get('portfolio_cut_id') != cut_id:
            raise ValueError('EVALUATION_IDENTITY_REFUSED')
        inputs = body.get('event_receipt', {}).get('inputs', {})
        for field in ('current', 'previous'):
            value = inputs.get(field)
            if not isinstance(value, dict):
                continue
            for segment in E.pieces(canonical(value)):
                data = segment.encode(); sha = hashlib.sha256(data).hexdigest()
                witnesses.setdefault(sha, {'cut_id': cut_id, 'field': field, 'segment_sha256': sha})
    candidates = []; refused = 0
    for rowid, sha, n, codec, payload in db.execute('SELECT rowid,* FROM '+BLOBS+' ORDER BY sha256'):
        if sha in roots:
            continue
        data = E._blob((n, codec, payload), sha)
        if sha not in witnesses:
            refused += 1; continue
        h = hashlib.sha256(); digest_row(h, (rowid,sha,n,codec,payload))
        candidates.append({'table': BLOBS, 'rowid': rowid, 'sha256': sha, 'byte_length': n,
                           'compressed_bytes': len(payload), 'payload_sha256': hashlib.sha256(payload).hexdigest(),
                           'typed_row_sha256': h.hexdigest(), 'witness': witnesses[sha],
                           'reason': 'UNREFERENCED_EXACT_DERIVED_STATE_SEGMENT'})
    return candidates, {'unique_or_uncertain_orphans_retained': refused, 'witness_segments': len(witnesses)}


def apply_copy(db, manifest):
    """Apply only an exact freshly recomputed manifest, in one transaction."""
    main = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    guard_copy(main)
    if db.in_transaction:
        raise ValueError('OPEN_TRANSACTION_REFUSED')
    db.execute('BEGIN IMMEDIATE')
    try:
        current = inventory(db)
        if current != manifest['before']:
            raise ValueError('STALE_MANIFEST_REFUSED')
        roots, _ = references(db)
        candidates, _ = portfolio_candidates(db, roots | set(manifest['external_roots']))
        if candidates != manifest['delete']:
            raise ValueError('CANDIDATES_CHANGED_REFUSED')
        survivors = hashlib.sha256()
        removed = {item['sha256'] for item in candidates}
        for row in db.execute('SELECT * FROM '+BLOBS+' ORDER BY sha256'):
            if row[0] not in removed:
                digest_row(survivors, row)
        for candidate in candidates:
            changed = db.execute('DELETE FROM '+BLOBS+' WHERE rowid=? AND sha256=?',
                                 (candidate['rowid'], candidate['sha256'])).rowcount
            if changed != 1:
                raise ValueError('DELETE_IDENTITY_REFUSED')
        # Every noncandidate cell, rowid, sequence and original DDL must match.
        after = inventory(db)
        if after['schema_sha256'] != current['schema_sha256']:
            raise ValueError('SCHEMA_CHANGED')
        for name in current['tables']:
            if name != BLOBS and after['tables'][name] != current['tables'][name]:
                raise ValueError('PRESERVED_HISTORY_CHANGED:' + name)
        if list(db.execute('PRAGMA foreign_key_check')):
            raise ValueError('FOREIGN_KEY_CHECK_FAILED')
        references(db)
        remaining = hashlib.sha256()
        for row in db.execute('SELECT * FROM '+BLOBS+' ORDER BY sha256'):
            digest_row(remaining, row)
        if survivors.digest() != remaining.digest():
            raise ValueError('SURVIVING_BLOB_CHANGED')
        db.commit()
    except BaseException:
        db.rollback(); raise
    return after


def rehearse(directory, report_path, *, inventory_directory=None):
    directory = Path(directory).resolve(); report_path = Path(report_path)
    if report_path.exists():
        raise FileExistsError('REPORT_EXISTS')
    report_path.mkdir(parents=True)
    inventory_directory = Path(inventory_directory).resolve() if inventory_directory else directory
    paths = sorted(inventory_directory.glob('*.db'))
    connections = {}; baseline = {}; root_sets = {}; manifest_counts = {}; physical_hashes = {}
    def file_sha(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    try:
        for path in paths:
            if inventory_directory == directory:
                disk = read_copy(path)
                disk.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                disk.execute('PRAGMA journal_mode=DELETE')
                disk.close()
            else:
                read_normalized_copy(path).close()
            physical_hashes[path.name] = file_sha(path)
            # Sequential materialization avoids millions of random reads on
            # fragmented historical stores. Only one scan DB owns RAM at once.
            # Databases above four GiB keep bounded cache and disk streaming.
            memory_available = next(int(line.split()[1])*1024 for line in
                                    Path('/proc/meminfo').read_text().splitlines()
                                    if line.startswith('MemAvailable:'))
            if (path.stat().st_size <= 4 * 1024**3
                    and memory_available >= path.stat().st_size * 1.15 + 512 * 1024**2):
                db = E.install(sqlite3.connect(':memory:'))
                scan = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)
                try:
                    scan.backup(db, pages=4096)
                finally:
                    scan.close()
                db.execute('PRAGMA temp_store=MEMORY')
            else:
                db = read_copy(path)
            baseline[path.name] = inventory(db)
            root_sets[path.name], manifest_counts[path.name] = references(db)
            db.close()
            (report_path/'inventory-progress.json').write_text(json.dumps(baseline, indent=2))
            print('inventoried', path.name, flush=True)
        (report_path/'inventory.json').write_text(json.dumps(baseline, indent=2))
        name = 'runtime-portfolio.db'; db = read_copy(directory/name); connections[name] = db
        if inventory(db) != baseline[name]:
            raise ValueError('TARGET_COPY_DIFFERS_FROM_INVENTORY')
        external = set().union(*(roots for key, roots in root_sets.items() if key != name))
        candidates, refusal = portfolio_candidates(db, root_sets[name] | external)
        manifest = {'version': 'da10.cleanup.v1', 'production_apply_authorized': False,
                    'database': name, 'before': baseline[name], 'external_roots': sorted(external),
                    'delete': candidates, 'refusal': refusal,
                    'compressed_payload_bytes': sum(r['compressed_bytes'] for r in candidates),
                    'snapshot': json.loads((directory.parent/'snapshot.json').read_text())}
        (report_path/'production-cleanup-manifest.json').write_text(json.dumps(manifest, indent=2))
        start = time.perf_counter(); before_bytes = (directory/name).stat().st_size
        after = apply_copy(db, manifest)
        surviving_blobs = blob_digest(db)
        elapsed = time.perf_counter()-start
        # Physical reclaim only on the recognized private copy. Verify rowid
        # digests after VACUUM; a rewritten replay ordering is a refusal.
        db.execute('PRAGMA journal_mode=DELETE'); db.execute('VACUUM')
        compact = inventory(db)
        if blob_digest(db) != surviving_blobs:
            raise ValueError('VACUUM_BLOB_CONTENT_CHANGED')
        for table in after['tables']:
            if table != BLOBS and compact['tables'][table] != after['tables'][table]:
                raise ValueError('VACUUM_ROWID_CHANGED:' + table)
        if compact['schema_sha256'] != after['schema_sha256']:
            raise ValueError('VACUUM_SCHEMA_CHANGED')
        if list(db.execute('PRAGMA integrity_check')) != [('ok',)]:
            raise ValueError('INTEGRITY_CHECK_FAILED')
        references(db)  # verify every surviving manifest and dependency again
        # Pure historical portfolio/Risk replay, no providers or write authority.
        from trader.portfolio.runtime import replay
        replayed = 0; replay_failures = []
        for cut_id, marker in db.execute('SELECT cut_id,payload FROM evaluations ORDER BY rowid'):
            try:
                replay(json.loads(E.resolve(db, marker))); replayed += 1
            except Exception as exc:
                replay_failures.append({'cut_id':cut_id, 'error':str(exc)})
        result = {'status': 'VERIFIED_COPY', 'records_removed': len(candidates),
                  'before_bytes': before_bytes, 'after_bytes': (directory/name).stat().st_size,
                  'physical_reclaimed_bytes': before_bytes-(directory/name).stat().st_size,
                  'cleanup_seconds': elapsed, 'before': baseline[name], 'after': compact,
                  'preserved_tables_exact': True, 'surviving_blob_content_exact': True,
                  'manifests_verified': manifest_counts,
                  'portfolio_replayed': replayed, 'portfolio_replay_failures': replay_failures}
        # Verify all other databases were never mutated by cleanup.
        for key, expected_sha in physical_hashes.items():
            if (key != name or inventory_directory != directory) and file_sha(inventory_directory/key) != expected_sha:
                raise ValueError('OTHER_DATABASE_CHANGED:' + key)
        result['other_database_files_unchanged'] = True
        result['normalized_copy_file_sha256'] = physical_hashes
        (report_path/'verification.json').write_text(json.dumps(result, indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in ('before','after')}),flush=True)
        return result
    finally:
        for db in connections.values():
            db.close()
