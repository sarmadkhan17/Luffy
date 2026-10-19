"""Existing cleanup owner, with bounded operational lifecycle helpers.

Historical portfolio GC and CLI backfills apply only to identified copies.
Market/R&D helpers preserve logical identities in caller-owned transactions.
Unknown dependencies, unique evidence and statistical expenditure are retained.
No production historical-purge CLI exists.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from datetime import datetime, timezone
import json
import os
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


def retained_inventory(db, physical_sha256):
    """Non-target stores are protected more strongly by complete file hashes.

    Counts and DDL inventory use the readonly normalized copy. No candidate
    belongs to these stores; every byte is compared again after cleanup.
    Evidence manifests resolve in their own SQLite main/src namespace, never
    through a global cross-database SHA catalog.
    """
    return {'tables': {name: {'rows': db.execute('SELECT count(*) FROM '+quote(name)).fetchone()[0]}
                       for name, _ in tables(db)},
            'schema_sha256': hashlib.sha256(canonical(schema(db)).encode()).hexdigest(),
            'page_size': db.execute('PRAGMA page_size').fetchone()[0],
            'pages': db.execute('PRAGMA page_count').fetchone()[0],
            'free_pages': db.execute('PRAGMA freelist_count').fetchone()[0],
            'verification': 'COMPLETE_DATABASE_FILE_BYTE_PARITY',
            'file_sha256': physical_sha256}


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
    # The existing copy path materializes logical file bytes, including WAL.
    # Refuse a known impossible copy before consuming scratch space. This is
    # a capacity check, not a reservation against concurrent external writers.
    required = sum(item.stat().st_size for path in paths for item in
                   [path, *(Path(str(path)+s) for s in ('-wal','-shm','-journal'))]
                   if item.exists())
    if required > shutil.disk_usage(copies).free:
        raise ValueError('SNAPSHOT_SPACE_INSUFFICIENT')
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
    if sorted(source.glob('*.db')) != paths:
        raise ValueError('SOURCE_FILE_SET_CHANGED_REFUSED')
    for record in receipt.values():
        for original, expected in record['source_files'].items():
            if not Path(original).exists() or signature(Path(original)) != expected:
                raise ValueError('SOURCE_CHANGED_DURING_MULTI_DATABASE_SNAPSHOT')
    (destination/'snapshot.json').write_text(json.dumps(receipt, indent=2))
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
    if 'journal_representation_v1' in actual: expected.add('journal_representation_v1')
    if actual != expected:
        return [], {'reason': 'UNKNOWN_SCHEMA_RETAINED', 'tables': sorted(actual)}
    expected_columns = {'state': ['id','inputs_json','previous_proposal_id'],
                        'checkpoints': ['event_id','payload'], 'evaluations': ['cut_id','payload'],
                        BLOBS: ['sha256','byte_length','codec','payload']}
    if 'journal_representation_v1' in actual:
        expected_columns['journal_representation_v1']=['version','status','manifest']
    for name, expected_cols in expected_columns.items():
        if [r[1] for r in db.execute('PRAGMA table_info('+quote(name)+')')] != expected_cols:
            return [], {'reason': 'UNKNOWN_COLUMNS_RETAINED'}
    if list(db.execute('PRAGMA foreign_key_check')):
        raise ValueError('FOREIGN_KEY_CHECK_FAILED')
    if any(list(db.execute('PRAGMA foreign_key_list('+quote(name)+')')) for name in actual):
        return [], {'reason': 'UNKNOWN_FOREIGN_KEYS_RETAINED'}
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
                with closing(read_copy(path)) as disk:
                    disk.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    disk.execute('PRAGMA journal_mode=DELETE')
            else:
                read_normalized_copy(path).close()
            physical_hashes[path.name] = file_sha(path)
            with closing(read_normalized_copy(path)) as db:
                if path.name != 'runtime-portfolio.db':
                    baseline[path.name] = retained_inventory(db, physical_hashes[path.name])
                    root_sets[path.name] = set()
                    manifest_counts[path.name] = 'BYTE_EXACT_FILE_PRESERVED'
                else:
                    # Only the deletion target needs an expanded root closure.
                    # Decoder lookup is database-local: main or its attached src.
                    baseline[path.name] = inventory(db)
                    root_sets[path.name], manifest_counts[path.name] = references(db)
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
                    'snapshot': json.loads((directory.parent/'snapshot.json').read_text()),
                    'reference_scope': 'Target main/src namespace; other stores preserved byte-for-byte; runtime.py, owner/queries.py and learning readers resolve retained evaluation/state rows'}
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


# Operational lifecycle additions reuse the existing owners and transactions.
# These functions never commit, checkpoint, vacuum, discover stores or unlink files.
MAX_BATCH = 128


def market_batch(db, *, after=0, limit=MAX_BATCH, apply=False):
    """Losslessly share source/raw strings in a bounded receipt rowid range.

    Unknown schemas/records refuse individually. The caller owns a stable read
    snapshot or write transaction. Old logical receipts and every ID survive.
    """
    from trader.data import market_provenance as mp
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH or type(after) is not int or after < 0:
        raise ValueError('INVALID_LIFECYCLE_BOUND')
    expected = ['revision_id','series_key','event_ms','available_ms','observed_ms','record_json']
    if [r[1] for r in db.execute('PRAGMA table_info(market_revisions)')] != expected:
        return dict(refused='UNKNOWN_MARKET_SCHEMA', records=[], next_rowid=after)
    if [r[1] for r in db.execute('PRAGMA table_info(market_raw_sources)')] != ['content_hash','source','raw_json']:
        return dict(refused='UNKNOWN_SOURCE_SCHEMA', records=[], next_rowid=after)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('market_revisions','market_raw_sources') LIMIT 1").fetchone():
        return dict(refused='UNKNOWN_MARKET_TRIGGER', records=[], next_rowid=after)
    if apply and not db.in_transaction:
        raise ValueError('WRITE_TRANSACTION_REQUIRED')
    pending, records, refused, delta = {}, [], [], 0
    rows = db.execute('SELECT rowid,* FROM market_revisions WHERE rowid>? ORDER BY rowid LIMIT ?', (after,limit)).fetchall()
    for rowid, rid, key, event, available, observed, raw in rows:
        try:
            r = mp.read_record(db, raw)
            values = {k:r[k] for k in mp.VALUES if k in r}
            body = {k:r[k] for k in mp.META if k not in ('revision_id','supersedes')}
            if (r['content_hash'] != mp.digest({'raw':json.loads(r['raw_json']), 'value':values})
                    or r['revision_id'] != mp.digest(body)
                    or (rid,event,available,observed) != (r['revision_id'],r['event_time_ms'],r['available_at_ms'],r['observed_at_ms'])):
                raise ValueError('MARKET_RECEIPT_IDENTITY_REFUSED')
            if mp.TEXT_REF not in raw and raw != mp.encode(r):
                raise ValueError('NONCANONICAL_SERIALIZED_PROVENANCE_RETAINED')
            physical = dict(r)
            additions = {}
            for field in ('source','raw_json'):
                text = r[field]
                if not isinstance(text,str) or len(text.encode()) < mp.TEXT_MIN:
                    continue
                marker = mp.text_reference(text); sha = marker[len(mp.TEXT_REF):]
                old = db.execute('SELECT raw_json FROM market_raw_sources WHERE content_hash=?',(sha,)).fetchone()
                if (old is not None and old[0] != text) or (sha in pending and pending[sha] != text):
                    raise ValueError('MARKET_SOURCE_CONFLICT')
                if old is None and field == 'raw_json':
                    continue
                if old is None and sha not in pending:
                    additions[sha] = text
                physical[field] = marker
            changed = mp.encode(physical)
            if changed == raw:
                continue
            # Never expand one legacy record just to normalize its formatting.
            saving = len(raw.encode()) - len(changed.encode())
            source_bytes = sum(len(t.encode()) + len(h) + len('immutable-text.v1') for h,t in additions.items())
            # A first shared value can cost one small row; subsequent receipts
            # amortize it. Future append uses exactly the same representation.
            pending.update(additions)
            delta += saving - source_bytes
            records.append(dict(rowid=rowid, revision_id=rid, before_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                                after_sha256=hashlib.sha256(changed.encode()).hexdigest(),
                                record_bytes_removed=saving, source_payload_bytes_added=source_bytes))
            if apply:
                db.execute('SAVEPOINT lifecycle_record')
                try:
                    stored = mp.store_record(db,r)
                    if mp.read_record(db,stored) != r:
                        raise ValueError('MARKET_ROUNDTRIP_REFUSED')
                    db.execute('UPDATE market_revisions SET record_json=? WHERE rowid=? AND record_json=?',(stored,rowid,raw))
                    db.execute('RELEASE lifecycle_record')
                except Exception:
                    db.execute('ROLLBACK TO lifecycle_record'); db.execute('RELEASE lifecycle_record')
                    raise
        except (ValueError,KeyError,TypeError) as exc:
            refused.append(dict(rowid=rowid,revision_id=rid,reason=str(exc)))
    return dict(records=records, refused=refused, scanned=len(rows), next_rowid=rows[-1][0] if rows else after,
                net_payload_bytes_removed=delta, physical_bytes_reclaimed=0)


def research_batch(db, *, after=0, limit=32, apply=False):
    """Apply the ledger's existing _slim contract only after all batch work ends.

    Failed and successful identities/statistics remain known. Registrations,
    tests, candidates, archives and reproducible evidence are never rewritten.
    """
    from trader.research.ledger import _slim
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='research_combos' LIMIT 1").fetchone():
        return dict(refused='UNKNOWN_RESEARCH_TRIGGER', records=[], next_rowid=after)
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH or type(after) is not int or after < 0:
        raise ValueError('INVALID_LIFECYCLE_BOUND')
    if apply and not db.in_transaction:
        raise ValueError('WRITE_TRANSACTION_REQUIRED')
    rows = db.execute('SELECT rowid,hash,result,tf,geo,created_at FROM research_combos WHERE rowid>? ORDER BY rowid LIMIT ?', (after,limit)).fetchall()
    records, refused = [], []
    for rowid, identity, text, tf, geo, created in rows:
        try:
            value = json.loads(text)
            if not isinstance(value,dict) or not isinstance(value.get('portfolio'),dict):
                raise ValueError('UNKNOWN_RESULT_SHAPE')
            if 'order' not in value['portfolio']:
                continue
            if value.get('evidence') or value.get('hash') != identity:
                raise ValueError('IMMUTABLE_OR_UNKNOWN_RESULT')
            if (db.execute('SELECT 1 FROM research_tests WHERE hash=? LIMIT 1',(identity,)).fetchone()
                    or db.execute('SELECT 1 FROM research_candidates WHERE hash=? OR twin_of=? LIMIT 1',(identity,identity)).fetchone()):
                raise ValueError('REFERENCED_RESULT')
            if db.execute('SELECT 1 FROM research_batches WHERE tf=? AND geo=? AND finished IS NULL LIMIT 1',(tf,geo)).fetchone():
                raise ValueError('ACTIVE_RESEARCH')
            if not created or not db.execute('SELECT 1 FROM research_batches WHERE tf=? AND geo=? AND finished>=? LIMIT 1',(tf,geo,created)).fetchone():
                raise ValueError('COMPLETION_NOT_ESTABLISHED')
            slim = _slim(value)
            changed = json.dumps(slim)
            records.append(dict(rowid=rowid, hash=identity, before_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                after_sha256=hashlib.sha256(changed.encode()).hexdigest(),
                                bytes_removed=len(text.encode())-len(changed.encode()), field='portfolio.order'))
            if apply:
                db.execute('UPDATE research_combos SET result=? WHERE rowid=? AND result=?',(changed,rowid,text))
        except (ValueError,TypeError,KeyError) as exc:
            refused.append(dict(rowid=rowid,hash=identity,reason=str(exc)))
    return dict(records=records,refused=refused,scanned=len(rows),next_rowid=rows[-1][0] if rows else after,
                net_payload_bytes_removed=sum(r['bytes_removed'] for r in records),physical_bytes_reclaimed=0)


OPERATIONAL_STORES = ('candles.db', 'derivs.db', 'luffy.db', 'attention.db',
    'investigation.db', 'runtime-portfolio.db', 'attention_learning.db',
    'execution-evidence.db', 'accounting-worker/queue.db',
    'declared-population/attention.db', 'stage5-public/public.db')


def operational_dry_run(directory, report_path, *, windows=8, limit=128):
    """Bounded read-only evidence from the already verified operational catalog.

    Each store has its own short read transaction. Live WAL is read normally;
    immutable=1 is deliberately forbidden. No DDL, writes, temp files,
    checkpoints, VACUUM, providers or runtime imports are invoked.
    """
    if type(windows) is not int or not 1 <= windows <= 32 or not 1 <= limit <= MAX_BATCH:
        raise ValueError('INVALID_DRY_RUN_BOUND')
    directory = Path(directory).resolve()
    if not os.statvfs(directory).f_flag & os.ST_RDONLY:
        raise ValueError('READONLY_FILESYSTEM_REQUIRED_FOR_OPERATIONAL_DRY_RUN')
    output = Path(report_path).resolve()
    if directory == output or directory in output.parents:
        raise ValueError('REPORT_WITHIN_OPERATIONAL_STORAGE_REFUSED')
    report = dict(version='operational-lifecycle.v1', started_utc=datetime.now(timezone.utc).isoformat(),
                  read_only=True, production_apply_authorized=False,
                  stores={}, approval_records=[], physical_storage_savings_bytes=0,
                  filesystem_write_denial='READONLY_MOUNT_VERIFIED_PER_STORE',
                  physical_savings_reason='No production writes, checkpoints or compaction',
                  catalog_evidence='/mnt/luffy-data/storage-measurement-20261019/inventory.json',
                  estimate_scope='Stratified bounded rowid windows; net payload estimate excludes B-tree/index/row overhead and compaction. Rowid gaps and workload shifts limit extrapolation.')
    for name in OPERATIONAL_STORES:
        path = directory/name
        if not path.is_file():
            report['stores'][name] = dict(refused='STORE_UNAVAILABLE'); continue
        if not os.statvfs(path.resolve()).f_flag & os.ST_RDONLY:
            report['stores'][name] = dict(refused='WRITABLE_STORE_MOUNT'); continue
        result = report['stores'][name] = dict(path=str(path.resolve()), action='RETAIN')
        files = {}
        for suffix in ('','-wal','-shm','-journal'):
            item = Path(str(path)+suffix)
            if item.exists():
                st = item.stat(); files[suffix or 'main'] = dict(bytes=st.st_size,allocated_bytes=st.st_blocks*512)
        result['files'] = files
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)) as db:
            db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA temp_store=MEMORY')
            started = time.monotonic()
            db.set_progress_handler(lambda: int(time.monotonic()-started > 3),1000)
            try:
                db.execute('BEGIN')
                size = db.execute('PRAGMA page_size').fetchone()[0]
                pages = db.execute('PRAGMA page_count').fetchone()[0]
                free = db.execute('PRAGMA freelist_count').fetchone()[0]
                result.update(page_bytes=size, retained_page_bytes=(pages-free)*size,
                              reusable_page_bytes=free*size)
                targets = [('market_revisions',market_batch)] if name in ('candles.db','derivs.db') else [('research_combos',research_batch)] if name == 'luffy.db' else []
                for table, inspect in targets:
                    last = db.execute('SELECT rowid FROM '+table+' ORDER BY rowid DESC LIMIT 1').fetchone()
                    high = last[0] if last else 0
                    offsets = sorted({max(0, int((high-limit)*i/max(1,windows-1))) for i in range(windows)})
                    batches = []
                    for after in offsets:
                        # Reset the query deadline per bounded range; no long snapshot.
                        started = time.monotonic()
                        batch = inspect(db,after=after,limit=limit)
                        batches.append(batch)
                        report['approval_records'].extend(dict(database=name,table=table,**r) for r in batch.get('records',()))
                    scanned = sum(b.get('scanned',0) for b in batches)
                    net = sum(b.get('net_payload_bytes_removed',0) for b in batches)
                    result.update(action='LOSSLESS_REPRESENTATION_COMPACTION_ONLY', table=table,
                                  maximum_rowid=high,batches=batches,sampled_rows=scanned,
                                  sampled_net_payload_bytes_removed=net,
                                  estimated_net_payload_bytes_removed=net*high/scanned if scanned else 0,
                                  estimate_assumes_dense_rowids=True)
            except (sqlite3.Error,ValueError) as exc:
                result['refused'] = str(exc)
            finally:
                db.rollback()
    report['retained_page_bytes'] = sum(s.get('retained_page_bytes',0) for s in report['stores'].values())
    report['reusable_page_bytes'] = sum(s.get('reusable_page_bytes',0) for s in report['stores'].values())
    report['estimated_reclaimable_payload_GB'] = sum(s.get('estimated_net_payload_bytes_removed',0) for s in report['stores'].values())/1e9
    report['completed_utc'] = datetime.now(timezone.utc).isoformat()
    report['approval_scope'] = 'Only these exact sampled records are identified; unsampled records require a fresh bounded manifest and the same eligibility checks under the applying write transaction.'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2))
    return report


def apply_lifecycle_copy(path, family, *, after=0, limit=128):
    """Bounded historical rehearsal on a recognized existing DA-10 copy only."""
    path = guard_copy(path)
    methods = {'market': market_batch, 'research': research_batch}
    if family not in methods:
        raise ValueError('UNKNOWN_LIFECYCLE_FAMILY')
    inspect = methods[family]
    with closing(read_copy(path)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            planned = inspect(db,after=after,limit=limit)
            applied = inspect(db,after=after,limit=limit,apply=True)
            if applied != planned:
                raise ValueError('LIFECYCLE_PLAN_CHANGED')
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return dict(copy=str(path), production_apply_authorized=False, family=family, **applied)


# Completed predictive experiments already have a Bank, a writer flock, immutable
# results and a shared error-budget ledger. Their recovery evidence belongs there.
RESEARCH_CAPSULE = 'research-completed-work.v1'
RESEARCH_CAPSULE_KEY = 'completed-work:'
RESEARCH_WORK_LIMIT = 256*1024**2


def _cell(value):
    import base64
    return {'sqlite_blob_v1':base64.b64encode(value).decode()} if isinstance(value,bytes) else value


def _uncell(value):
    import base64
    return base64.b64decode(value['sqlite_blob_v1'],validate=True) if isinstance(value,dict) else value


def logical_image(db):
    """Keep owned logical rows, rowids and DDL, discarding SQLite free pages."""
    image=dict(schema=[list(r) for r in schema(db)],tables=[])
    for name,ddl in tables(db):
        if name.startswith('sqlite_') and name!='sqlite_sequence':
            raise ValueError('UNKNOWN_SQLITE_INTERNAL_TABLE')
        cols=[r[1] for r in db.execute('PRAGMA table_info('+quote(name)+')')]
        without='WITHOUT ROWID' in ddl.upper()
        data=[[ _cell(v) for v in row] for row in rows(db,name,ddl)]
        image['tables'].append(dict(name=name,columns=cols,without_rowid=without,rows=data))
    return image


def restore_image(image):
    """Recreate an exact logical experiment in RAM; never create a writable file."""
    db=E.install(sqlite3.connect(':memory:'))
    try:
        for kind,name,table,ddl in image['schema']:
            if kind=='table' and name!='sqlite_sequence': db.execute(ddl)
        for table in image['tables']:
            name=table['name'];cols=table['columns']
            if name=='sqlite_sequence': db.execute('DELETE FROM sqlite_sequence')
            prefix='' if table['without_rowid'] else 'rowid,'
            sql='INSERT INTO '+quote(name)+'('+prefix+','.join(map(quote,cols))+') VALUES ('
            n=len(cols)+int(not table['without_rowid'])
            db.executemany(sql+','.join('?' for _ in range(n))+')',
                           [tuple(_uncell(v) for v in row) for row in table['rows']])
        for kind,name,table,ddl in image['schema']:
            if kind!='table' and ddl: db.execute(ddl)
        db.commit()
        if logical_image(db)!=image: raise ValueError('RESEARCH_ARCHIVE_ROUNDTRIP')
        db.execute('PRAGMA query_only=ON')
        return db
    except BaseException:
        db.close();raise


def completed_reader(bank, experiment_id, *, dataset=False):
    """Existing replay/Factory consumers read either working evidence or Bank evidence."""
    import base64
    from trader.learning import capture as C
    from trader.research import predictive_experiment as X
    if not re.fullmatch('[0-9a-f]{64}',experiment_id): raise ValueError('experiment_identity')
    bank=Path(bank).resolve()
    name='candles.db' if dataset else 'quantitative.db'
    path=bank.parent/(bank.stem+'-experiments')/experiment_id/name
    if path.is_file() and not path.is_symlink(): return E.install(X.readonly(path))
    with closing(X.readonly(bank)) as db:
        row=db.execute('SELECT payload FROM bridge_runtime_bindings WHERE id=?',
                       (RESEARCH_CAPSULE_KEY+experiment_id,)).fetchone()
        if not row: raise ValueError('RESEARCH_REPRODUCTION_EVIDENCE_UNAVAILABLE')
        capsule=C.payload(db,row[0])
    expected=capsule.pop('capsule_id',None)
    if expected!=hashlib.sha256(canonical(capsule).encode()).hexdigest() or capsule['experiment_id']!=experiment_id:
        raise ValueError('RESEARCH_ARCHIVE_IDENTITY')
    if not dataset: return restore_image(capsule['quantitative'])
    raw=base64.b64decode(capsule['candles_b64'],validate=True)
    if hashlib.sha256(raw).hexdigest()!=capsule['candle_sha256']: raise ValueError('RESEARCH_DATASET_HASH')
    db=E.install(sqlite3.connect(':memory:'))
    db.deserialize(raw)
    db.execute('PRAGMA query_only=ON')
    return db


def terminal_research(result):
    return (result.get('classification') in ('SUPPORTED','UNSUPPORTED','NOT_TESTABLE')
            or result.get('referee_disposition',{}).get('state') in (
                'DISCOVERY_ONLY','CONTROL_UNDERPOWERED','POWER_UNAVAILABLE','EVALUATION_ERROR',
                'CONTROL_DATA_INSUFFICIENT','PROTECTED_CUT_INCOMPATIBLE',
                'DUPLICATE_EXPERIMENT_IDENTITY','CANCELLED','ABANDONED'))


def retire_research_work(db, bank, ex, result):
    """Bank-owner completion/restart hook, under its EXISTING exclusive flock.

    Archive commit precedes every unlink. Restart verifies exact surviving file
    hashes before retrying. Only two owned databases, closed WAL sidecars and the
    producer's completed .tmp file are eligible. Unknown dependencies retain work.
    """
    import base64
    from trader.research import predictive_bridge as B, predictive_receipt as M
    from trader.learning import capture as C
    bank=Path(bank).resolve();eid=ex['experiment_id']
    if db.in_transaction: return dict(status='REFUSED',reason='bank_transaction_open')
    if len(eid)!=64 or any(c not in '0123456789abcdef' for c in eid):
        return dict(status='REFUSED',reason='experiment_identity')
    root=bank.parent/(bank.stem+'-experiments');work=root/eid
    try:
        if not terminal_research(result): return dict(status='RETAINED',reason='research_incomplete')
        # Result hash and immutable Bank registration must still bind this terminal.
        if (result.get('result_id')!=B.P.digest({k:v for k,v in result.items() if k!='result_id'})
                or result.get('experiment_id')!=eid): raise ValueError('terminal_result_identity')
        held=db.execute('SELECT payload FROM bridge_bank_results WHERE id=?',(result['result_id'],)).fetchone()
        registered=db.execute('SELECT payload FROM bridge_experiments WHERE id=?',(eid,)).fetchone()
        if not held or C.payload(db,held[0])!=result or not registered or C.payload(db,registered[0])!=ex:
            raise ValueError('terminal_not_committed')
        hrow=db.execute('SELECT payload FROM bridge_hypotheses WHERE id=?',(result['hypothesis_id'],)).fetchone()
        if not hrow: raise ValueError('hypothesis_not_committed')
        M.validate(B.P.hypothesis_from_dict(C.payload(db,hrow[0])['hypothesis']),ex,result)
        expected_bank_guards={f'bridge_runtime_bindings_no_{op}':f"CREATE TRIGGER bridge_runtime_bindings_no_{op} BEFORE {op.upper()} ON bridge_runtime_bindings BEGIN SELECT RAISE(ABORT,'immutable'); END" for op in ('update','delete')}
        if dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('bridge_runtime_bindings','journal_evidence_blobs_v1')"))!=expected_bank_guards:
            raise ValueError('unknown_bank_archive_trigger')
        if root.is_symlink() or work.is_symlink(): raise ValueError('work_symlink')
        old=db.execute('SELECT payload FROM bridge_runtime_bindings WHERE id=?',(RESEARCH_CAPSULE_KEY+eid,)).fetchone()
        if old:
            capsule=C.payload(db,old[0]);saved=capsule.pop('capsule_id',None)
            if (saved!=B.P.digest(capsule) or capsule.get('schema')!=RESEARCH_CAPSULE
                    or capsule.get('experiment_id')!=eid or capsule['result_id']!=result['result_id']):
                raise ValueError('existing_archive_identity')
        if not work.exists():
            return dict(status='ALREADY_RETIRED' if old else 'RETAINED',reason='no_working_copy')
        allowed={'candles.db','quantitative.db','candles.db.tmp',
                 'quantitative.db-wal','quantitative.db-shm','candles.db-wal','candles.db-shm'}
        files=list(work.iterdir())
        if any(p.name not in allowed or not p.is_file() or p.is_symlink() or p.stat().st_nlink!=1 for p in files):
            raise ValueError('unknown_work_dependency')
        if any(p.name.endswith('-wal') and p.stat().st_size for p in files):
            raise ValueError('unresolved_WAL_or_writer')
        if sum(p.stat().st_size for p in files)>RESEARCH_WORK_LIMIT: raise ValueError('archive_byte_bound')
        if not old:
            if not (work/'candles.db').is_file() or not (work/'quantitative.db').is_file():
                raise ValueError('working_evidence_missing')
            initial_proofs={p.name:file_hash(p) for p in files}
            if M.snapshot(db,ex)!=result['quantitative_evidence']:
                raise ValueError('final_quantitative_evidence_changed')
            with closing(B.E.readonly(work/'quantitative.db')) as q:
                q.execute('BEGIN')
                if q.execute('SELECT id FROM predictive_experiment_binding').fetchall()!=[(eid,)]:
                    raise ValueError('working_binding')
                if q.execute('SELECT 1 FROM research_batches WHERE finished IS NULL LIMIT 1').fetchone():
                    raise ValueError('unfinished_research_batch')
                # A bound R&D file must never host financial/execution/recovery work.
                from trader.core.journal import Journal
                from trader.research.ledger import Ledger
                template=Journal(':memory:');Ledger(template).ensure();C.ensure(template._conn())
                try:
                    known={name:{r[1] for r in template._conn().execute('PRAGMA table_info('+quote(name)+')')} for name,_ in tables(template._conn())}
                    objects={(kind,name):ddl for kind,name,_,ddl in schema(template._conn())}
                finally: template._conn().close()
                known['predictive_experiment_binding']={'id'}
                for name,_ in tables(q):
                    cols={r[1] for r in q.execute('PRAGMA table_info('+quote(name)+')')}
                    if name not in known or not cols<=known[name]: raise ValueError('unknown_quantitative_schema:'+name)
                    if not name.startswith(('research_','learning_','journal_','sqlite_')) and name not in ('predictive_experiment_binding','state_kv'):
                        if q.execute('SELECT 1 FROM '+quote(name)+' LIMIT 1').fetchone(): raise ValueError('financial_or_recovery_dependency:'+name)
                if q.execute("SELECT 1 FROM state_kv WHERE key NOT LIKE 'research.%' AND key NOT LIKE 'lifecycle.research.%' LIMIT 1").fetchone():
                    raise ValueError('recovery_state_dependency')
                for kind,name,_,ddl in schema(q):
                    if kind in ('view','trigger') and objects.get((kind,name))!=ddl:
                        raise ValueError('unknown_quantitative_object:'+name)
                q.row_factory=sqlite3.Row
                for row in q.execute('SELECT * FROM research_tests ORDER BY seq'):
                    retained=db.execute('SELECT payload FROM bridge_tests WHERE id=?',(str(row['seq']),)).fetchone()
                    if not retained or C.payload(db,retained[0])!=dict(row): raise ValueError('spent_look_not_in_bank')
                budget=[dict(r) for r in q.execute('SELECT * FROM research_budget ORDER BY id')]
                if budget:
                    retained=db.execute("SELECT payload FROM bridge_runtime_bindings WHERE id='registered-error-budget'").fetchone()
                    if not retained or C.payload(db,retained[0])!=budget: raise ValueError('budget_not_in_bank')
                image=logical_image(q)
            with closing(restore_image(image)) as check:
                if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('archive_integrity')
            snapshot=db.execute('SELECT payload FROM bridge_snapshots WHERE id=?',(eid,)).fetchone()
            raw=(work/'candles.db').read_bytes();sha=hashlib.sha256(raw).hexdigest()
            if not snapshot or C.payload(db,snapshot[0])['candle_sha256']!=sha: raise ValueError('dataset_snapshot_binding')
            capsule=dict(schema=RESEARCH_CAPSULE,experiment_id=eid,result_id=result['result_id'],
                         final_classification=result['classification'],reason=result['referee_disposition'],
                         quantitative=image,candles_b64=base64.b64encode(raw).decode(),candle_sha256=sha,
                         file_proofs={p.name:file_hash(p) for p in work.iterdir() if p.name in allowed})
            if any(capsule['file_proofs'].get(name)!=sha for name,sha in initial_proofs.items()):
                raise ValueError('working_evidence_changed_during_archive')
            capsule['capsule_id']=B.P.digest(capsule)
            with db: B.append(db,'bridge_runtime_bindings',RESEARCH_CAPSULE_KEY+eid,capsule)
            # Read the committed recovery capsule before permitting any deletion.
            held=db.execute('SELECT payload FROM bridge_runtime_bindings WHERE id=?',(RESEARCH_CAPSULE_KEY+eid,)).fetchone()
            if C.payload(db,held[0])!=capsule: raise ValueError('archive_commit_not_verified')
        proofs=capsule['file_proofs'];removed=0;bytes_removed=0
        surviving=list(work.iterdir())
        for p in surviving:
            if p.name not in proofs or file_hash(p)!=proofs[p.name]:
                raise ValueError('working_file_changed_after_archive')
        for p in surviving:
            bytes_removed+=p.stat().st_blocks*512;p.unlink();removed+=1
        work.rmdir()
        return dict(status='RETIRED',files_removed=removed,allocated_bytes_removed=bytes_removed,
                    experiment_id=eid,capsule_id=capsule.get('capsule_id',saved if old else None))
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
        return dict(status='REFUSED',reason=str(exc),experiment_id=eid)


def file_hash(path):
    with open(path,'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def market_completion(db):
    """Eight historical receipts per acquisition, in its existing transaction."""
    if not db.in_transaction: return dict(refused='transaction_required')
    db.execute('SAVEPOINT market_lifecycle')
    try:
        db.execute(E.REPRESENTATION_SCHEMA)
        if ([r[1] for r in db.execute('PRAGMA table_info(journal_representation_v1)')]!=['version','status','manifest']
                or db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='journal_representation_v1' LIMIT 1").fetchone()):
            raise ValueError('unknown_lifecycle_metadata')
        row=db.execute("SELECT manifest FROM journal_representation_v1 WHERE version='market.lifecycle.v1'").fetchone()
        after=json.loads(row[0])['after'] if row else 0
        # Limit bytes read as well as record count; an oversized legacy receipt
        # refuses locally and the next invocation progresses past it.
        ids=list(db.execute('SELECT rowid,length(CAST(record_json AS BLOB)) FROM market_revisions WHERE rowid>? ORDER BY rowid LIMIT 8',(after,)))
        result=dict(records=[],refused=[],scanned=0,next_rowid=after)
        budget=0
        for rid,n in ids:
            if n>1024**2 or budget+n>4*1024**2:
                result['refused'].append(dict(rowid=rid,reason='byte_bound'));result['next_rowid']=rid;continue
            part=market_batch(db,after=rid-1,limit=1,apply=True);budget+=n
            if isinstance(part.get('refused'),str): raise ValueError(part['refused'])
            result['records'].extend(part['records']);result['refused'].extend(part['refused']);result['scanned']+=1;result['next_rowid']=rid
        db.execute('INSERT OR REPLACE INTO journal_representation_v1 VALUES (?,?,?)',
                   ('market.lifecycle.v1','bounded',canonical({'after':result['next_rowid'] if ids else 0})))
        db.execute('RELEASE market_lifecycle');return result
    except (ValueError,KeyError,sqlite3.Error) as exc:
        db.execute('ROLLBACK TO market_lifecycle');db.execute('RELEASE market_lifecycle')
        return dict(refused=str(exc))


def portfolio_completion(db, *, max_rows=64, max_bytes=32*1024**2):
    """Bounded private-owner reference sweep, resumed in existing codec metadata.

    Append-only evaluations/checkpoints and insert-only evidence are scanned to
    their current tails before deletion. New tails catch up across invocations;
    mutable state is checked in the deleting transaction. Unknown dependencies
    cancel only this witness. The same owner serializes writes and cleanup.
    """
    if not db.in_transaction: return dict(deleted=0,refused='transaction_required')
    version='portfolio.lifecycle.v1';cursor=0;pending=None
    db.execute('SAVEPOINT portfolio_lifecycle')
    try:
        columns={'state':['id','inputs_json','previous_proposal_id'],
            'checkpoints':['event_id','payload'],'evaluations':['cut_id','payload'],
            BLOBS:['sha256','byte_length','codec','payload'],
            'journal_representation_v1':['version','status','manifest']}
        if {name for name,_ in tables(db)}!=set(columns): raise ValueError('unknown_portfolio_schema')
        for name,expected in columns.items():
            if [r[1] for r in db.execute('PRAGMA table_info('+quote(name)+')')]!=expected:
                raise ValueError('unknown_portfolio_columns')
            if list(db.execute('PRAGMA foreign_key_list('+quote(name)+')')): raise ValueError('unknown_foreign_key')
        expected_triggers={}
        for table,prefix,message in (('checkpoints','checkpoint','checkpoint immutable'),('evaluations','evaluation','evaluation immutable')):
            for op in ('update','delete'):
                expected_triggers[prefix+'_no_'+op]=f"CREATE TRIGGER {prefix}_no_{op} BEFORE {op.upper()} ON {table} BEGIN SELECT RAISE(ABORT,'{message}'); END"
        normalize=lambda sql:' '.join(sql.split())
        expected_views={'evaluations_logical_v1':'CREATE VIEW evaluations_logical_v1 AS SELECT cut_id,journal_detail_v1(payload) AS payload FROM evaluations',
            'state_logical_v1':'CREATE VIEW state_logical_v1 AS SELECT id,journal_detail_v1(inputs_json) AS inputs_json,previous_proposal_id FROM state'}
        actual_views=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='view'"))
        if {k:normalize(v) for k,v in actual_views.items()}!=expected_views: raise ValueError('unknown_portfolio_view')
        actual_triggers=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        if {k:normalize(v) for k,v in actual_triggers.items()}!={k:normalize(v) for k,v in expected_triggers.items()}:
            raise ValueError('unknown_portfolio_trigger')
        saved=db.execute('SELECT manifest FROM journal_representation_v1 WHERE version=?',(version,)).fetchone()
        state=json.loads(saved[0]) if saved else {'after':0}
        cursor=state['after'];pending=state.get('pending')
        if pending is None:
            row=db.execute('SELECT rowid,cut_id,payload FROM evaluations WHERE rowid>? ORDER BY rowid LIMIT 1',(cursor,)).fetchone()
            if row is None:
                state={'after':0}
            else:
                cursor=row[0]
                body=json.loads(E.resolve(db,row[2],max_bytes=max_bytes//2))
                if body.get('schema')!='runtime-portfolio-checkpoint.v1' or body.get('portfolio_cut_id')!=row[1]:
                    raise ValueError('evaluation_identity')
                candidates={}
                inputs=body.get('event_receipt',{}).get('inputs',{})
                for field in ('current','previous'):
                    if not isinstance(inputs.get(field),dict): continue
                    for segment in E.pieces(canonical(inputs[field])):
                        if len(candidates)>=32: break
                        sha=hashlib.sha256(segment.encode()).hexdigest()
                        blob=db.execute('SELECT byte_length,codec,payload FROM '+BLOBS+' WHERE sha256=?',(sha,)).fetchone()
                        if blob:
                            if E._blob(blob,sha)!=segment.encode(): raise ValueError('witness_differs')
                            candidates[sha]=hashlib.sha256(blob[2]).hexdigest()
                pending=dict(witness=cursor,candidates=candidates,scanned={n:0 for n in ('evaluations','checkpoints',BLOBS)})
                state={'after':state['after'],'pending':pending}
        used=count=deleted=compressed=0
        def scan(value):
            nonlocal used
            if not isinstance(value,str): return
            n=len(value.encode())
            if value.startswith(E.PREFIX):
                manifest=json.loads(value[len(E.PREFIX):]);n+=manifest['byte_length']
            if used+n>max_bytes: raise ValueError('reference_byte_bound')
            used+=n
            pending['candidates']={k:v for k,v in pending['candidates'].items() if k not in HEX.findall(value)}
            logical=E.resolve(db,value,max_bytes=max_bytes-used+n)
            if E.PREFIX in logical: raise ValueError('nested_manifest')
            pending['candidates']={k:v for k,v in pending['candidates'].items() if k not in HEX.findall(logical)}
        if pending:
            for table,last in list(pending['scanned'].items()):
                if not pending['candidates']: break
                remaining=max_rows-count
                if remaining<=0: break
                columns_sql='rowid,*' if table==BLOBS else 'rowid,payload'
                for row in db.execute('SELECT '+columns_sql+' FROM '+quote(table)+' WHERE rowid>? ORDER BY rowid LIMIT ?',(last,remaining)):
                    # Finish this row in a later invocation if the byte budget
                    # is spent. A single oversized root refuses this witness.
                    n=row[2] if table==BLOBS else len(row[1].encode())+(json.loads(row[1][len(E.PREFIX):])['byte_length'] if row[1].startswith(E.PREFIX) else 0)
                    if used+n>max_bytes:
                        if count: break
                        raise ValueError('reference_byte_bound')
                    if table==BLOBS:
                        scan(E._blob((row[2],row[3],row[4]),row[1]).decode())
                    else: scan(row[1])
                    pending['scanned'][table]=row[0];count+=1
            caught_up=not pending['candidates'] or all(not db.execute('SELECT 1 FROM '+quote(t)+' WHERE rowid>? LIMIT 1',(last,)).fetchone() for t,last in pending['scanned'].items())
            if caught_up:
                for row in db.execute('SELECT inputs_json,previous_proposal_id FROM state'):
                    for value in row: scan(value)
                for sha,proof in pending['candidates'].items():
                    blob=db.execute('SELECT byte_length,codec,payload FROM '+BLOBS+' WHERE sha256=?',(sha,)).fetchone()
                    if blob:
                        if hashlib.sha256(blob[2]).hexdigest()!=proof: raise ValueError('candidate_changed')
                        E._blob(blob,sha)
                        db.execute('DELETE FROM '+BLOBS+' WHERE sha256=?',(sha,));deleted+=1;compressed+=len(blob[2])
                state={'after':pending['witness']}
        db.execute('INSERT OR REPLACE INTO journal_representation_v1 VALUES (?,?,?)',(version,'bounded',canonical(state)))
        db.execute('RELEASE portfolio_lifecycle')
        return dict(deleted=deleted,compressed_bytes_removed=compressed,scanned=count,pending='pending' in state)
    except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        db.execute('ROLLBACK TO portfolio_lifecycle');db.execute('RELEASE portfolio_lifecycle')
        # Skip the refused witness, so uncertain work cannot starve unrelated work.
        if cursor:
            db.execute('INSERT OR REPLACE INTO journal_representation_v1 VALUES (?,?,?)',(version,'refused',canonical({'after':cursor})))
        return dict(deleted=0,refused=str(exc))
