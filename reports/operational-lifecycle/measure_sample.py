"""Private bounded sample rehearsal; invoke inside the CLI's read-only mount.

Only the report directory is writable. VACUUM applies to a fresh private sample.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from trader.data import market_provenance as M
from trader.persistence import storage_cleanup as C
from trader.core import journal_evidence as E

DATA=Path('/mnt/luffy-data/luffy/production/data')
OUT=Path(__file__).resolve().parent
assert os.statvfs(DATA).f_flag & os.ST_RDONLY
report={'source_readonly_mount':True,'source_rows_limit':2048,'production_physical_savings_bytes':0}
with sqlite3.connect((DATA/'candles.db').as_uri()+'?mode=ro',uri=True) as source:
    source.execute('PRAGMA query_only=ON');source.execute('PRAGMA temp_store=MEMORY');source.execute('BEGIN')
    actual=source.execute('SELECT * FROM market_revisions ORDER BY rowid DESC LIMIT 2048').fetchall()
    actual.reverse() # retain source rowid order, including equal-clock replay ties
    logical=[M.read_record(source,r[-1]) for r in actual]
    source.rollback()
with tempfile.TemporaryDirectory(prefix='sample-',dir=OUT) as scratch:
    path=Path(scratch)/'sample.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA temp_store=MEMORY')
        M.init(db)
        db.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',actual)
        db.commit()
        db.execute('VACUUM') # normalize private baseline to avoid claiming insertion fragmentation
        before=path.stat();before_pages=db.execute('PRAGMA page_count').fetchone()[0]
        original=[M.read_record(db,r[0]) for r in db.execute('SELECT record_json FROM market_revisions ORDER BY rowid')]
        raw_hash=hashlib.sha256(repr(original).encode()).hexdigest()
        cursor=0;changed=0;payload=0
        while True:
            db.execute('BEGIN IMMEDIATE')
            batch=C.market_batch(db,after=cursor,apply=True)
            db.commit();cursor=batch['next_rowid']
            changed+=len(batch['records']);payload+=batch['net_payload_bytes_removed']
            if not batch['scanned']:break
        after=path.stat();free=db.execute('PRAGMA freelist_count').fetchone()[0]
        after_logical=[M.read_record(db,r[0]) for r in db.execute('SELECT record_json FROM market_revisions ORDER BY rowid')]
        assert after_logical==original
        assert not C.market_batch(db)['records']
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        # Replays select from exact unchanged logical values and clocks.
        keys=db.execute('SELECT DISTINCT series_key FROM market_revisions').fetchall()
        replayed=0
        for (key,) in keys:
            cut=max(r['observed_at_ms'] for r,row in zip(original,actual) if row[1]==key)
            f=M.load(db,key,as_of_ms=cut)
            assert f is not None
            replayed+=len(f)
        db.execute('VACUUM') # own sample only, never source/operational
        compact=path.stat()
        assert [M.read_record(db,r[0]) for r in db.execute('SELECT record_json FROM market_revisions ORDER BY rowid')]==original
        report['market_sample']=dict(rows=len(actual),records_compacted=changed,logical_sha256=raw_hash,
            exact_logical_parity=True,source_rowid_order_preserved=True,as_of_receipts_replayed=replayed,idempotent=True,integrity='ok',
            net_payload_bytes_removed=payload,before_bytes=before.st_size,
            after_apply_bytes=after.st_size,free_pages_after_apply=free,
            filesystem_bytes_reclaimed_without_compaction=before.st_blocks*512-after.st_blocks*512,
            after_private_vacuum_bytes=compact.st_size,
            physical_sample_savings_bytes=before.st_blocks*512-compact.st_blocks*512)
    # Actual latest portfolio checkpoint, compared with identical logical bytes.
    with sqlite3.connect((DATA/'runtime-portfolio.db').as_uri()+'?mode=ro',uri=True) as source:
        source.execute('PRAGMA query_only=ON');source.execute('PRAGMA temp_store=MEMORY');source.execute('BEGIN')
        rows=source.execute('SELECT cut_id,payload FROM evaluations ORDER BY rowid DESC LIMIT 3').fetchall()
        bodies=[E.resolve(source,r[1]) for r in rows];source.rollback()
    modes={}
    for mode in ('existing','anchored'):
        p=Path(scratch)/(mode+'.db')
        with sqlite3.connect(p) as db:
            db.executescript(E.BLOB_SCHEMA)
            for text in reversed(bodies):
                body=json.loads(text)
                values=[C.canonical(v) for v in body['event_receipt']['inputs'].values() if isinstance(v,dict)]
                marker=E.store(db,text if mode=='existing' else E.anchored(text,values))
                assert E.resolve(db,marker)==text
                current=C.canonical(body['event_receipt']['inputs']['current'])
                state=E.store(db,current);assert E.resolve(db,state)==current
            db.commit()
            modes[mode]=dict(blobs=db.execute('SELECT count(*) FROM '+C.BLOBS).fetchone()[0],
                compressed_bytes=db.execute('SELECT sum(length(payload)) FROM '+C.BLOBS).fetchone()[0],
                main_bytes=p.stat().st_size)
    report['portfolio_sample']=dict(checkpoints=len(bodies),modes=modes,
        compressed_payload_growth_reduction_bytes=modes['existing']['compressed_bytes']-modes['anchored']['compressed_bytes'],
        exact_logical_parity=True)
(OUT/'sample-rehearsal.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
