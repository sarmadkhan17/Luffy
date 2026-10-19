#!/usr/bin/env python3
"""Rehearse DA-10 on identified isolated copies; never apply to production."""
import argparse
import os
import shutil
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.persistence.storage_cleanup import rehearse, snapshot, operational_dry_run, apply_lifecycle_copy
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--isolated-copies')
    inputs.add_argument('--source-data')
    inputs.add_argument('--operational-dry-run', help='Read-only current operational data directory; no copying or apply')
    parser.add_argument('--lifecycle-batch', choices=('market','research'), help='Bounded rehearsal; requires identified --isolated-copies and --database')
    parser.add_argument('--database', choices=('candles.db','derivs.db','luffy.db'))
    parser.add_argument('--after', type=int, default=0)
    parser.add_argument('--windows', type=int, default=8)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--scratch')
    parser.add_argument('--inventory-copies', help='Read-only normalized copies for inventory; target remains in --isolated-copies')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    if args.lifecycle_batch:
        if not args.isolated_copies or not args.database:
            parser.error('--lifecycle-batch requires --isolated-copies and --database')
        if (args.lifecycle_batch == 'research') != (args.database == 'luffy.db'):
            parser.error('research uses luffy.db; market uses candles.db or derivs.db')
        result = apply_lifecycle_copy(Path(args.isolated_copies)/args.database, args.lifecycle_batch,
                                      after=args.after, limit=args.batch_size)
        output = Path(args.report).resolve()
        if output.suffix != '.json':
            parser.error('lifecycle batch report must be a JSON file')
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,indent=2))
        raise SystemExit(0)
    if args.operational_dry_run:
        source = Path(args.operational_dry_run).resolve()
        output_dir = Path(args.report).resolve().parent
        if source == output_dir or source in output_dir.parents or output_dir in source.parents:
            parser.error('dry-run report requires a separate dedicated output directory')
        if not os.statvfs(source).f_flag & os.ST_RDONLY:
            bwrap = shutil.which('bwrap')
            if not bwrap:
                parser.error('bwrap is required to deny all operational filesystem writes; no unsafe fallback')
            output_dir.mkdir(parents=True, exist_ok=True)
            os.execv(bwrap, [bwrap, '--die-with-parent', '--unshare-net', '--ro-bind', '/', '/',
                '--bind', str(output_dir), str(output_dir), '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                '--', sys.executable, '-B', str(Path(__file__).resolve()), *sys.argv[1:]])
        operational_dry_run(args.operational_dry_run, args.report, windows=args.windows, limit=args.batch_size)
        raise SystemExit(0)
    if args.source_data:
        if not args.scratch:
            parser.error('--source-data requires a new --scratch directory')
        copies = snapshot(args.source_data, args.scratch)
    else:
        copies = args.isolated_copies
    rehearse(copies, args.report, inventory_directory=args.inventory_copies)
