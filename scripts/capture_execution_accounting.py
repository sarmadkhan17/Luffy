"""Export one emergency recovery accounting receipt, or replay it offline.

Reads archived intents and demo fill history only; never writes the live journal.
Missing history can be retried into a new output file. Funding remains unknown.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from trader.engine.accounting import capture, replay


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal', type=Path, default=Path('data/luffy.db'))
    p.add_argument('--intent-id')
    p.add_argument('--list', action='store_true', help='list archived intent IDs without venue access')
    p.add_argument('--output', type=Path)
    p.add_argument('--replay', type=Path)
    args = p.parse_args()
    if args.replay:
        print(json.dumps(replay(json.loads(args.replay.read_text())), sort_keys=True))
        return
    if args.list:
        with sqlite3.connect(args.journal.resolve().as_uri()+'?mode=ro', uri=True) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_accounting'").fetchone()
            rows = db.execute('SELECT id FROM execution_accounting ORDER BY id').fetchall() if exists else []
        print(json.dumps({'intent_ids': [r[0] for r in rows], 'ledger_present': bool(exists)}))
        return
    if not args.intent_id or not args.output:
        p.error('--intent-id and --output required for capture')
    if args.output.exists():
        p.error('output already exists; use a new filename')
    with sqlite3.connect(args.journal.resolve().as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM execution_accounting WHERE id=?', (args.intent_id,)).fetchone()
    if row is None:
        p.error('archived intent not found')
    from trader.core.config import Env
    if Env.get('BINANCE_DEMO', 'true').lower() not in ('1', 'true', 'yes'):
        p.error('this capture command is limited to demo accounting')
    from trader.data.feed import make_exchange
    artifact = capture(json.loads(row[0]), make_exchange('futures', demo=True))
    replay(artifact)
    with args.output.open('x') as out:
        json.dump(artifact, out, indent=2, sort_keys=True, allow_nan=False)
    print(json.dumps({'output': str(args.output), 'sha256': artifact['sha256'],
                      'assessment': artifact['assessment']}, sort_keys=True))


if __name__ == '__main__':
    main()
