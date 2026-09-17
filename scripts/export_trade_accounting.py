"""Read-only export/replay of normal trade accounting provenance; no venue calls."""
import argparse
import json
from pathlib import Path
import sqlite3

from trader.engine.booking import export, replay_export


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal', type=Path, default=Path('data/luffy.db'))
    p.add_argument('--trade-id')
    p.add_argument('--output', type=Path)
    p.add_argument('--replay', type=Path)
    a = p.parse_args()
    if a.replay:
        print(json.dumps(replay_export(json.loads(a.replay.read_text()))))
        return
    if not a.trade_id or not a.output:
        p.error('--trade-id and --output required')
    if a.output.exists():
        p.error('output exists; use a new filename')
    with sqlite3.connect(a.journal.resolve().as_uri()+'?mode=ro', uri=True) as db:
        result = export(db, a.trade_id)
    replay_export(result)
    with a.output.open('x') as f:
        json.dump(result, f, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps({'output': str(a.output), **replay_export(result)}))


if __name__ == '__main__':
    main()
