"""Rehearse real synthetic protocol's completed work on another isolated copy.

No operational/production paths are opened. Archive/working baselines preserve
all original logical Bank rows and exact frozen candle bytes. Only this private
copy is compacted. Dataset bars are synthetic protocol fixtures, not live alpha.
"""
from contextlib import closing
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from trader.core import journal_evidence as E
from trader.learning import capture as C
from trader.persistence import storage_cleanup as S
from trader.research import predictive_bridge as B, predictive_receipt as M
OUT=Path(__file__).resolve().parent
SOURCE=Path('/mnt/luffy-data/sol1-complete-rehearsal')
report={'scope':'Isolated copies of genuine synthetic protocol completions; no production research deletion or population estimate','production_bytes_reclaimed':0,'samples':{}}
def allocated(path): return path.stat().st_blocks*512
for disposition in ('supported','unsupported'):
    with tempfile.TemporaryDirectory(prefix='research-private-',dir=OUT) as scratch:
        bank=Path(scratch)/'bridge.db';shutil.copyfile(SOURCE/disposition/'bridge.db',bank)
        with closing(B.store(bank)) as db:
            final=next(r for r in B.records(db,'bridge_bank_results') if r['classification']==disposition.upper())
            ex=next(r for r in B.records(db,'bridge_experiments') if r['experiment_id']==final['experiment_id'])
            eid=ex['experiment_id'];key=S.RESEARCH_CAPSULE_KEY+eid
            raw=db.execute('SELECT payload FROM bridge_runtime_bindings WHERE id=?',(key,)).fetchone()[0]
            capsule=C.payload(db,raw);capsule_shas=set(json.loads(raw[len(E.PREFIX):])['chunks'])
            immutable=db.execute("SELECT sql FROM sqlite_master WHERE name='bridge_runtime_bindings_no_delete'").fetchone()[0]
            # Undo the prior archive ONLY on this disposable copy, forming an
            # honest pre-cleanup baseline with no pre-paid capsule storage.
            db.execute('DROP TRIGGER bridge_runtime_bindings_no_delete')
            db.execute('DELETE FROM bridge_runtime_bindings WHERE id=?',(key,));db.execute(immutable)
            roots=set()
            for name,ddl in S.tables(db):
                if name==S.BLOBS: continue
                for row in S.rows(db,name,ddl):
                    for value in row:
                        if isinstance(value,str):
                            roots.update(S.HEX.findall(value))
                            if value.startswith(E.PREFIX): roots.update(S.HEX.findall(E.resolve(db,value)))
            for sha in capsule_shas-roots: db.execute('DELETE FROM '+S.BLOBS+' WHERE sha256=?',(sha,))
            db.commit();db.execute('VACUUM')
            work=bank.parent/(bank.stem+'-experiments')/eid;work.mkdir(parents=True)
            (work/'candles.db').write_bytes(base64.b64decode(capsule['candles_b64']))
            with closing(S.restore_image(capsule['quantitative'])) as q: (work/'quantitative.db').write_bytes(q.serialize())
            original=M.snapshot(db,ex)
            baseline=allocated(bank)+sum(allocated(p) for p in work.iterdir())
            main_before=bank.stat().st_size;work_before=sum(p.stat().st_size for p in work.iterdir())
            out=S.retire_research_work(db,bank,ex,final)
            assert out['status']=='RETIRED',out
            assert M.snapshot(db,ex)==original
            applied=allocated(bank);free=db.execute('PRAGMA freelist_count').fetchone()[0]
            assert S.retire_research_work(db,bank,ex,final)['status']=='ALREADY_RETIRED'
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            db.execute('VACUUM')
            compact=allocated(bank)
            report['samples'][disposition]=dict(experiment_id=eid,result_id=final['result_id'],
                main_bytes_before=main_before,working_file_bytes_before=work_before,baseline_allocated_bytes=baseline,
                allocated_bytes_after_archive_and_unlink=applied,allocated_bytes_after_private_vacuum=compact,
                net_bytes_removed_before_compaction=baseline-applied,physical_bytes_reclaimed=baseline-compact,
                free_pages_after_archive=free,exact_quantitative_replay=True,statistical_rows_unchanged=True,integrity='ok',retirement=out)
(OUT/'research-measurements.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
