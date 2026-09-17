"""Read-only memory export and offline replay; no worker or trading commands.

python -m scripts.memory_archive export data/investigation.db /tmp/memory.json
python -m scripts.memory_archive replay /tmp/memory.json
Add --investigation ID to export one investigation with its prior raw sources.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from trader.observability import outcomes as O, memory as M
from trader.observability.learning import encode


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('export','replay'));p.add_argument('source',type=Path)
    p.add_argument('destination',type=Path,nargs='?');p.add_argument('--investigation')
    a=p.parse_args()
    if a.action=='export':
        if not a.destination: p.error('export requires a destination')
        with closing(sqlite3.connect(a.source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('BEGIN')
            archive=M.export_case(db,a.investigation) if a.investigation else O.export(db)
        # Refuse accidental overwrites of frozen artifacts.
        with a.destination.open('x') as out: out.write(encode(archive)+'\n')
    else:
        if a.source.stat().st_size > O.MAX_CASES*O.MAX_PAYLOAD: raise ValueError('archive_capacity')
        archive=json.loads(a.source.read_text())
        if archive['schema_version']=='investigation-archive.v1': M.replay_export(archive)
        else:
            body={k:archive[k] for k in ('schema_version','records')}
            if O.O.digest(body)!=archive['sha256'] or body['schema_version']!=O.O.SCHEMA:
                raise ValueError('archive_integrity')
            for item in body['records']: O.O.replay(item['record'])
    print(encode({'status':'ok','action':a.action,'schema_version':archive['schema_version']}))


if __name__=='__main__': main()
