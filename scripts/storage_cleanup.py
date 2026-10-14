#!/usr/bin/env python3
"""Rehearse DA-10 on identified isolated copies; never apply to production."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.persistence.storage_cleanup import rehearse, snapshot
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--isolated-copies')
    inputs.add_argument('--source-data')
    parser.add_argument('--scratch')
    parser.add_argument('--inventory-copies', help='Read-only normalized copies for inventory; target remains in --isolated-copies')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    if args.source_data:
        if not args.scratch:
            parser.error('--source-data requires a new --scratch directory')
        copies = snapshot(args.source_data, args.scratch)
    else:
        copies = args.isolated_copies
    rehearse(copies, args.report, inventory_directory=args.inventory_copies)
