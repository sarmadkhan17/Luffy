"""Run under read-only bwrap; only OUT contains writable isolated samples."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from trader.core import journal_evidence as E
from trader.learning import capture as C
from trader.persistence import storage_cleanup as S
DATA=Path('/mnt/luffy-data/luffy/production/data');OUT=Path(__file__).resolve().parent
assert os.statvfs(DATA).f_flag & os.ST_RDONLY
report={'source_readonly_mount':True,'production_bytes_reclaimed':0,'sample_scope':'Bounded actual operational rows, not a whole-store physical projection'}

def stats(db,path):
    st=path.stat()
    return dict(file_bytes=st.st_size,allocated_bytes=st.st_blocks*512,
        pages=db.execute('PRAGMA page_count').fetchone()[0],free_pages=db.execute('PRAGMA freelist_count').fetchone()[0],
        page_size=db.execute('PRAGMA page_size').fetchone()[0])

with sqlite3.connect((DATA/'luffy.db').as_uri()+'?mode=ro',uri=True) as source:
    source.execute('PRAGMA query_only=ON');source.execute('PRAGMA temp_store=MEMORY');source.execute('BEGIN')
    rows={t:source.execute('SELECT * FROM '+t+' ORDER BY rowid DESC LIMIT 16').fetchall()[::-1] for t in C.TABLES}
    rows={t:[(*r[:-1],E.resolve(source,r[-1])) for r in records] for t,records in rows.items()}
    source.rollback()
with tempfile.TemporaryDirectory(prefix='learning-private-',dir=OUT) as scratch:
    path=Path(scratch)/'sample.db'
    with sqlite3.connect(path) as db:
        C.ensure(db)
        for t,records in rows.items():
            db.executemany('INSERT INTO '+t+' VALUES ('+','.join('?' for _ in records[0])+')',records) if records else None
        db.commit();db.execute('VACUUM')
        baseline=stats(db,path)
        original={t:list(C.read(db,'SELECT * FROM '+t+' ORDER BY rowid')) for t in C.TABLES}
        old_inline=new_inline=0
        with db:
            for t,records in rows.items():
                key=db.execute('PRAGMA table_info('+t+')').fetchone()[1]
                for row in records:
                    marker=C.packed(db,row[-1]);old_inline+=len(row[-1].encode());new_inline+=len(marker.encode())
                    db.execute('UPDATE '+t+' SET payload=? WHERE '+key+'=?',(marker,row[0]))
        after=stats(db,path)
        assert {t:list(C.read(db,'SELECT * FROM '+t+' ORDER BY rowid')) for t in C.TABLES}==original
        # Exercise reusable pages with a future source identity, then retain it.
        count=0
        with db:
            for t,records in rows.items():
                if t=='learning_source_blobs' and records:
                    C.insert(db,t,'sha256','isolated-page-reuse-proof',('isolated-page-reuse-proof',records[0][-1]));count+=1
        reused=stats(db,path)
        db.execute('VACUUM');compact=stats(db,path)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        report['learning']=dict(rows={t:len(r) for t,r in rows.items()},baseline=baseline,after_representations=after,
            after_one_future_source=reused,after_private_vacuum=compact,
            inline_bytes_removed=old_inline-new_inline,compressed_blob_bytes=db.execute('SELECT coalesce(sum(length(payload)),0) FROM '+S.BLOBS).fetchone()[0],
            physical_bytes_reclaimed=baseline['allocated_bytes']-compact['allocated_bytes'],
            pages_reused=max(0,after['free_pages']-reused['free_pages']),exact_logical_row_parity=True,
            caveat='Representation/forward-write measurement; actual lifecycle eligibility separately tested with complete replay manifests. No arbitrary learning row is eligible for logical deletion.')
# Three actual portfolio bodies: preserve logical bytes, measure old vs owner anchors.
with sqlite3.connect((DATA/'runtime-portfolio.db').as_uri()+'?mode=ro',uri=True) as source:
    source.execute('PRAGMA query_only=ON');source.execute('BEGIN')
    physical=source.execute('SELECT cut_id,payload FROM evaluations ORDER BY rowid DESC LIMIT 3').fetchall()[::-1]
    bodies=[E.resolve(source,r[1]) for r in physical];source.rollback()
report['portfolio_latest_expanded_bytes']=[len(b.encode()) for b in bodies]
with tempfile.TemporaryDirectory(prefix='portfolio-private-',dir=OUT) as scratch:
    from trader.portfolio.runtime import Consumer
    path=Path(scratch)/'portfolio.db';owner=Consumer(path)
    with owner._db() as db:
        # Simulate old writer's exact original representations, including state
        # copies later superseded. All source rows/checkpoints stay immutable.
        for (cut,_),text in zip(physical,bodies):
            current=S.canonical(json.loads(text)['event_receipt']['inputs']['current'])
            marker=E.store(db,text)
            db.execute('INSERT INTO evaluations VALUES (?,?)',(cut,marker))
            db.execute('INSERT OR REPLACE INTO state VALUES (1,?,NULL)',(E.store(db,current),))
        db.commit();db.execute('VACUUM');baseline=stats(db,path)
        deleted=payload=steps=0
        # Existing completion/restart hook, same bounded function as production.
        for _ in range(100):
            db.execute('BEGIN IMMEDIATE');out=S.portfolio_completion(db);db.commit();steps+=1
            if out.get('refused'): break
            deleted+=out['deleted'];payload+=out['compressed_bytes_removed']
            if not out['pending'] and steps>=len(bodies)+2: break
        after=stats(db,path)
        assert [E.resolve(db,r[0]) for r in db.execute('SELECT payload FROM evaluations ORDER BY rowid')]==bodies
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        db.execute('VACUUM');compact=stats(db,path)
        report['portfolio']=dict(checkpoints=len(bodies),baseline=baseline,after_cleanup=after,after_private_vacuum=compact,
            steps=steps,deleted_blobs=deleted,compressed_bytes_removed=payload,physical_bytes_reclaimed=baseline['allocated_bytes']-compact['allocated_bytes'],last=out,
            exact_logical_row_parity=True,source='Actual latest three operational evaluations; old representation reconstructed on private copy')
(OUT/'sample-measurements.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
