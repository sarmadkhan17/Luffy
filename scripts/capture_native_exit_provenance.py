"""Read-only demo native-exit provenance: journal-referenced protective algo
ID -> actual executed order -> fills. Leg-only; whole-trade economics are not
established here (see scripts/reconcile_trade_accounting.py for the full
chain). Field contract for the algo-order lookup confirmed against the
official Binance docs 2026-09-17 (see native_exit_provenance.py); note the
2026-09-17 demo smoke found all live stops still NEW/untriggered, so the
full triggered-and-filled path remains untested against a live response.

Reads the journal (mode=ro) and bounded demo venue history only; never writes
the live journal or submits an order. Retries require a new output filename.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from trader.engine import native_exit_provenance as NEP


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal', type=Path, default=Path('data/luffy.db'))
    p.add_argument('--trade-id')
    p.add_argument('--output', type=Path)
    p.add_argument('--replay', type=Path)
    args = p.parse_args()
    if args.replay:
        if args.trade_id or args.output:
            p.error('--replay cannot be combined with capture arguments')
        artifact = json.loads(args.replay.read_text())
    else:
        if not args.trade_id or not args.output:
            p.error('--trade-id and --output required for capture')
        if args.output.exists():
            p.error('output already exists; use a new filename')
        from trader.core.config import Env
        if Env.get('BINANCE_DEMO', 'true').lower() not in ('1', 'true', 'yes'):
            p.error('this capture command is limited to demo accounting')
        with sqlite3.connect(args.journal.resolve().as_uri()+'?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM trades WHERE id=?', (args.trade_id,)).fetchone()
            if row is None:
                p.error('trade not found')
            reference = NEP.reference_snapshot(row)
        from trader.data.feed import make_exchange
        ex = make_exchange('futures', demo=True)
        ex.timeout = 5000
        artifact = NEP.capture(reference, ex)
        NEP.replay(artifact)
        with args.output.open('x') as out:
            json.dump(artifact, out, indent=2, sort_keys=True, allow_nan=False)
    result = NEP.replay(artifact)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
