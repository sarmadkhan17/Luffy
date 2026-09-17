"""Read-only demo whole-trade capture; retries require new filenames.

Optional memory import accepts complete, replayed receipts only. No orders,
prospective population backfill or legacy journal P&L mutation.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from trader.engine import booking, trade_accounting as A


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal', type=Path, default=Path('data/luffy.db'))
    p.add_argument('--trade-id')
    p.add_argument('--output', type=Path)
    p.add_argument('--replay', type=Path)
    p.add_argument('--import-memory', type=Path, help='existing typed-memory DB; complete receipts only')
    args = p.parse_args()
    if args.replay:
        artifact = json.loads(args.replay.read_text())
    else:
        if not args.trade_id or not args.output:
            p.error('--trade-id and --output required')
        if args.output.exists():
            p.error('output exists; use a new filename')
        from trader.core.config import Env
        if Env.get('BINANCE_DEMO', 'true').lower() not in ('1','true','yes'):
            p.error('demo only')
        with sqlite3.connect(args.journal.resolve().as_uri()+'?mode=ro', uri=True) as db:
            source = booking.export(db, args.trade_id)
            t = source['trade']
            overlaps = [r[0] for r in db.execute(
                'SELECT id FROM trades WHERE symbol=? AND id<>? AND (closed_at IS NULL OR closed_at>=?)',
                (t['symbol'], t['id'], t.get('opened_at') or ''))]
        from trader.data.feed import make_exchange
        artifact = A.capture(source, make_exchange('futures', demo=True), overlaps)
        A.replay(artifact)
        with args.output.open('x') as f:
            json.dump(artifact, f, indent=2, sort_keys=True, allow_nan=False)
    result = A.replay(artifact)
    imported = False
    if args.import_memory:
        from trader.observability.outcomes import put
        outcome = A.verified_outcome(artifact, int(time.time()*1000))
        with sqlite3.connect(args.import_memory.resolve().as_uri()+'?mode=rw', uri=True) as db:
            activated = db.execute("SELECT value FROM typed_outcome_meta WHERE key='activated_ms'").fetchone()
            if not activated or artifact['start_ms'] < activated[0]:
                raise ValueError('verified_trade_not_forward_registered')
            key = 'verified-trade:'+artifact['bookings']['trade']['id']
            old = db.execute('SELECT payload FROM typed_outcomes WHERE source_key=?', (key,)).fetchone()
            if old:
                prior = json.loads(old[0])
                if prior['source']['whole_trade_capture']['sha256'] != artifact['sha256']:
                    raise ValueError('verified_trade_terminal_conflict')
            else:
                imported = put(db, key, outcome)
    print(json.dumps(dict(status=result['status'], reasons=result['reasons'],
                          learning_eligible=result['learning_eligible'], imported=imported)))


if __name__ == '__main__':
    main()
