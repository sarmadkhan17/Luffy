"""Build an immutable historical PIT dataset artifact from retained receipts.

The ``freeze`` command must be run before ``build``. Sources are read-only.
JSON artifacts must contain receipt envelopes with explicit
``local_imported_ms``; no archive-read clock is substituted.
"""
import argparse
import hashlib
import sqlite3
from pathlib import Path
import sys
import time
import json

from trader.cognition import dataset as D
from trader.cognition.historical_pit import (build_artifact, _immutable_write, _manifest,
                                             _read_json)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_local(value):
    return type(value) is int and value >= 0


def _accept(envelope, source, accepted, rejected, seen):
    if not isinstance(envelope, dict):
        rejected.append({'source': source, 'reason': 'receipt_not_object'}); return
    key = envelope.get('source_key')
    if not isinstance(key, str) or not key:
        rejected.append({'source': source, 'reason': 'missing_source_key'}); return
    if not _valid_local(envelope.get('local_imported_ms')):
        rejected.append({'source': source, 'source_key': key,
                         'reason': 'missing_local_imported_ms'}); return
    if not isinstance(envelope.get('record'), dict):
        rejected.append({'source': source, 'source_key': key, 'reason': 'missing_record'}); return
    if key in seen:
        rejected.append({'source': source, 'source_key': key, 'reason': 'duplicate_source_key',
                         'first_source': seen[key]}); return
    seen[key] = source
    accepted.append({'source_key': key, 'local_imported_ms': envelope['local_imported_ms'],
                     'record': envelope['record']})


def _store(path, limit, accepted, rejected, seen, hashes, metas):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('source_not_found:%s' % path)
    hashes[str(path)] = _sha256(path)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.5)
    try:
        db.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'typed_outcomes' not in tables:
            raise ValueError('typed_outcomes_table_missing:%s' % path)
        rows = db.execute('SELECT source_key,imported_ms,payload FROM typed_outcomes '
                          'ORDER BY source_key LIMIT ?', (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise ValueError('source_record_capacity:%s' % path)
        evicted = db.execute("SELECT value FROM typed_outcome_meta WHERE key='evicted_total'").fetchone() \
            if 'typed_outcome_meta' in tables else None
        metas[str(path)] = {'kind': 'typed_outcome_store', 'evicted_total': evicted[0] if evicted else 0,
                            'rows': len(rows)}
        for key, imported, payload in rows:
            try:
                record = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                rejected.append({'source': str(path), 'source_key': key, 'reason': 'record_json_invalid'})
                continue
            _accept({'source_key': key, 'local_imported_ms': imported, 'record': record},
                    str(path), accepted, rejected, seen)
    finally:
        db.close()


def _artifact(path, accepted, rejected, seen, hashes, metas):
    path = Path(path).resolve()
    hashes[str(path)] = _sha256(path)
    body = _read_json(path)
    receipts = body.get('receipts') if isinstance(body, dict) else None
    if not isinstance(receipts, list):
        rejected.append({'source': str(path), 'reason': 'artifact_receipts_missing_or_not_list'}); return
    metas[str(path)] = {'kind': 'receipt_artifact', 'rows': len(receipts), 'local_clock_required': True}
    for envelope in receipts:
        _accept(envelope, str(path), accepted, rejected, seen)


def load_receipts(*, stores=(), artifacts=(), limit=512):
    accepted, rejected, seen, hashes, metas = [], [], {}, {}, {}
    for path in stores:
        _store(path, limit, accepted, rejected, seen, hashes, metas)
    for path in artifacts:
        _artifact(path, accepted, rejected, seen, hashes, metas)
    if not accepted and not rejected:
        raise ValueError('no_historical_sources')
    return accepted, {'source_hashes': hashes, 'source_meta': metas,
                      'adapter_rejections': rejected,
                      'retention': {'sources': metas,
                                    'evicted_total': sum(int(v.get('evicted_total', 0))
                                                         for v in metas.values())}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    fr = sub.add_parser('freeze')
    fr.add_argument('--declaration', required=True, type=Path)
    fr.add_argument('--receipt', required=True, type=Path)
    bu = sub.add_parser('build')
    bu.add_argument('--declaration', required=True, type=Path)
    bu.add_argument('--receipt', required=True, type=Path)
    bu.add_argument('--output', required=True, type=Path)
    bu.add_argument('--store', action='append', type=Path, default=[])
    bu.add_argument('--artifact', action='append', type=Path, default=[])
    args = ap.parse_args(argv)
    try:
        if args.command == 'freeze':
            declaration = _read_json(args.declaration)
            if declaration.get('collection_mode') != 'retrospective_snapshot':
                raise ValueError('historical_adapter_requires_retrospective_snapshot')
            receipt = D.freeze(declaration, int(time.time() * 1000),
                               code_manifest=_manifest())
            state = _immutable_write(args.receipt, receipt)
            print(json.dumps({'state': state, 'declaration_version': receipt['declaration_version'],
                              'observed_frozen_ms': receipt['observed_frozen_ms']}))
            return 0
        if not args.store and not args.artifact:
            raise ValueError('historical_source_required')
        declaration, freeze = _read_json(args.declaration), _read_json(args.receipt)
        D.validate_freeze(freeze, declaration)
        receipts, meta = load_receipts(stores=args.store, artifacts=args.artifact,
                                       limit=declaration['limits']['max_records'])
        artifact = build_artifact(declaration, freeze, receipts, meta)
        state = _immutable_write(args.output, artifact)
        print(json.dumps({'state': state, 'status': artifact['sufficiency']['status'],
                          'rows': artifact['dataset']['accounting']['rows'],
                          'sequences': artifact['dataset']['accounting']['sequence_rows'],
                          'replay_hash': artifact['replay_hash']}))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'refused': type(exc).__name__, 'reason': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
