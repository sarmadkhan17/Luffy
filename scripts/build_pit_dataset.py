"""Freeze a point-in-time dataset declaration, then capture it. Read-only sources.

    ./venv/bin/python -m scripts.build_pit_dataset freeze \
        --declaration decl.json --receipt freeze.json
    ./venv/bin/python -m scripts.build_pit_dataset build \
        --declaration decl.json --receipt freeze.json \
        --store data/investigation.db --output dataset.json

`freeze` reads the actual wall clock and writes an immutable receipt. It is the
only evidence that the declaration predates the capture; a declaration cannot
date itself. `build` refuses without a matching receipt, refuses a capture clock
earlier than the observed freeze, and refuses to overwrite a differing output or
receipt. Re-running `build` with the same inputs rewrites nothing.

This tool opens every source read-only, never writes a ledger, never starts
discovery, evaluation, admission or an order path, and reads no configuration.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

from trader.cognition import (dataset as D, forecast_protocol as FP, investigation as I,
                              memory as M, outcomes as O)

OK, USAGE, CONFLICT = 0, 2, 3
MAX_DOC_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
# A written dataset may reach the builder's payload cap and then expand under
# indentation, so the re-read of an existing output is bounded well above it.
MAX_OUTPUT_BYTES = 4 * D.LIMITS['max_payload_bytes']
MAX_RECORD_BYTES = 256 * 1024
QUERY_SECONDS = 5.0


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Every module a replay of this dataset actually depends on: the builder, the
# typed-outcome producers it replays each receipt through, and this CLI.
MANIFEST_MODULES = (('builder', D), ('outcomes', O), ('forecast_protocol', FP),
                    ('investigation', I), ('memory', M))


def cli_manifest():
    """This side owns source hashing; the builder only carries what it is given."""
    manifest = {name: '%s:%s' % (module.__name__, _hash(Path(module.__file__)))
                for name, module in MANIFEST_MODULES}
    manifest['cli'] = 'scripts.build_pit_dataset:' + _hash(Path(__file__))
    return manifest


def _read_json(path, limit=MAX_DOC_BYTES):
    """Bound every source payload before it reaches the JSON decoder."""
    size = path.stat().st_size
    if size > limit:
        raise ValueError('input_payload_capacity: %s is %d bytes' % (path, size))
    return json.loads(path.read_text())


def _readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.5)
    started = time.monotonic()
    db.set_progress_handler(lambda: int(time.monotonic() - started > QUERY_SECONDS), 1000)
    db.execute('BEGIN')
    return db


def _record(payload, key):
    if len(payload.encode()) > MAX_RECORD_BYTES:
        raise ValueError('source_record_payload_capacity: %s' % key)
    return json.loads(payload)


def from_store(path, limit, now):
    """Bounded read of retained typed outcomes plus their LOCAL import clocks."""
    with closing(_readonly(path)) as db:
        rows = db.execute('SELECT source_key,imported_ms,payload FROM typed_outcomes '
                          'ORDER BY source_key LIMIT ?', (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise ValueError('dataset_record_capacity: store holds more than --max-records')
        total = db.execute('SELECT COUNT(*) FROM typed_outcomes').fetchone()[0]
        evicted = db.execute("SELECT value FROM typed_outcome_meta WHERE key='evicted_total'").fetchone()
        activated = db.execute("SELECT value FROM typed_outcome_meta WHERE key='activated_ms'").fetchone()
    receipts = [{'source_key': k, 'local_imported_ms': i, 'record': _record(p, k)} for k, i, p in rows]
    meta = {'source': 'typed_outcome_store', 'local_clock': 'store_imported_ms',
            'trustworthy_local_receipt': True, 'observed_source_read_ms': now,
            'retention': {'store_rows': total, 'evicted_total': evicted[0] if evicted else 0,
                          'forward_activated_ms': activated[0] if activated else None,
                          'store_capacity': 'bounded_evicting_store'}}
    return receipts, meta


def from_archive(path, limit, now):
    """An archive keeps no local restoration clock, so this read is when it was learned.

    The original import clock belongs to the machine that produced the archive and
    is never substituted for a local one: doing that would backdate knowledge this
    installation only acquired at restore. Point-in-time use before the observed
    read therefore fails the cut, which is the honest outcome without a real local
    restoration receipt.
    """
    archive = _read_json(path, MAX_ARCHIVE_BYTES)
    body = {k: archive.get(k) for k in ('schema_version', 'records')}
    if body['schema_version'] != O.SCHEMA:
        raise ValueError('archive_schema_mismatch: expected %s' % O.SCHEMA)
    if archive.get('sha256') != D.digest(body):
        raise ValueError('archive_integrity_mismatch')
    records = body['records'] or []
    if len(records) > limit:
        raise ValueError('dataset_record_capacity: archive holds more than --max-records')
    receipts = []
    for r in records:
        _record(json.dumps(r['record']), r['source_key'])
        receipts.append({'source_key': r['source_key'], 'local_imported_ms': now,
                         'record': r['record']})
    meta = {'source': 'typed_outcome_archive', 'local_clock': 'archive_read_wall_clock',
            'trustworthy_local_receipt': False, 'observed_source_read_ms': now,
            'retention': {'archive_records': len(records),
                          'diagnostic': 'No local restoration receipt survives in an archive. '
                                        'Records are stamped as learned at this read; the '
                                        'original import clock is retained inside each receipt '
                                        'but is never used as the local clock.'}}
    return receipts, meta


def _write_new(path, payload, accept_existing, err, limit=MAX_DOC_BYTES):
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    if path.exists():
        try:
            if accept_existing(_read_json(path, limit)):
                return 'unchanged'
        except (ValueError, KeyError, TypeError) as exc:
            raise SystemExit('%s (existing file refused: %s)' % (err, exc))
        raise SystemExit(err)
    with path.open('x') as out:
        out.write(text)
    return 'written'


def _accept_dataset(current, declaration, built):
    """Only a fully valid, replayable, same-declaration dataset counts as unchanged.

    The existing file is replayed and its freeze receipt revalidated first, so a
    tampered or stale output is a conflict rather than a silent no-op.
    """
    D.replay(current)
    D.validate_freeze(current['freeze'], declaration)

    def strip(d):
        # Only the three wall clocks a rerun cannot reproduce are set aside. Every
        # receipt's own local and original import clocks are compared in full.
        out = dict(d)
        out.pop('dataset_version', None)
        out['capture'] = {k: v for k, v in d['capture'].items() if k != 'observed_capture_ms'}
        out['status'] = {k: v for k, v in d['status'].items() if k != 'observed_capture_ms'}
        out['source_meta'] = {k: v for k, v in d['source_meta'].items()
                              if k != 'observed_source_read_ms'}
        return out
    return strip(current) == strip(built)


def cmd_freeze(args):
    declaration = _read_json(args.declaration)
    observed = int(time.time() * 1000)
    receipt = D.freeze(declaration, observed, code_manifest=cli_manifest())
    state = _write_new(args.receipt, receipt,
                       lambda cur: bool(D.validate_freeze(cur, declaration)),
                       'freeze_receipt_conflict: %s does not hold a valid receipt for this '
                       'declaration' % args.receipt)
    kept = _read_json(args.receipt)
    print(json.dumps({'state': state, 'declaration_version': kept['declaration_version'],
                      'observed_frozen_ms': kept['observed_frozen_ms'],
                      'observed_frozen_utc': datetime.fromtimestamp(
                          kept['observed_frozen_ms'] / 1000, timezone.utc).isoformat(),
                      'collection_mode': kept['declaration']['collection_mode'],
                      'receipt': str(args.receipt)}, sort_keys=True))
    return OK


def cmd_build(args):
    declaration = _read_json(args.declaration)
    receipt = _read_json(args.receipt)
    # Validate the freeze before touching any source: capture must follow it.
    D.validate_freeze(receipt, declaration)
    limit = min(args.max_records, declaration.get('limits', {}).get('max_records', args.max_records))
    read_ms = int(time.time() * 1000)
    receipts, meta = (from_store(args.store, limit, read_ms) if args.store
                      else from_archive(args.archive, limit, read_ms))
    capture = D.capture_receipt(int(time.time() * 1000), code_manifest=cli_manifest())
    built = D.build(declaration, receipt, capture, receipts, meta)
    D.replay(built)
    state = _write_new(args.output, built,
                       lambda cur: _accept_dataset(cur, declaration, built),
                       'dataset_output_conflict: %s holds a different dataset' % args.output,
                       MAX_OUTPUT_BYTES)
    kept = _read_json(args.output, MAX_OUTPUT_BYTES)
    print(json.dumps(dict(D.summary(kept), state=state, output=str(args.output)), sort_keys=True))
    return OK


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m scripts.build_pit_dataset', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    fr = sub.add_parser('freeze', help='record an observed wall clock for the declaration')
    fr.add_argument('--declaration', required=True, type=Path)
    fr.add_argument('--receipt', required=True, type=Path)
    bu = sub.add_parser('build', help='capture the declared dataset from retained receipts')
    bu.add_argument('--declaration', required=True, type=Path)
    bu.add_argument('--receipt', required=True, type=Path)
    src = bu.add_mutually_exclusive_group(required=True)
    src.add_argument('--store', type=Path, help='sqlite ledger with typed_outcomes (read-only)')
    src.add_argument('--archive', type=Path, help='self-contained typed-outcome archive JSON')
    bu.add_argument('--output', required=True, type=Path)
    bu.add_argument('--max-records', type=int, default=D.LIMITS['max_records'])
    args = ap.parse_args(argv)
    # The freeze receipt is an output of `freeze` and an input to `build`.
    inputs = ('declaration',) if args.command == 'freeze' else ('declaration', 'receipt', 'store', 'archive')
    for name in inputs:
        path = getattr(args, name, None)
        if path is not None and not path.exists():
            ap.error('%s does not exist: %s' % (name, path))
    if args.command == 'build' and args.output in (args.declaration, args.receipt):
        ap.error('--output must differ from its inputs')
    try:
        return cmd_freeze(args) if args.command == 'freeze' else cmd_build(args)
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'refused': type(exc).__name__, 'reason': str(exc)}, sort_keys=True),
              file=sys.stderr)
        return USAGE
    except SystemExit as exc:
        print(json.dumps({'refused': 'conflict', 'reason': str(exc)}, sort_keys=True), file=sys.stderr)
        return CONFLICT


if __name__ == '__main__':
    sys.exit(main())
