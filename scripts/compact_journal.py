#!/usr/bin/env python3
"""Offline, space-guarded exact journal migration. Never opens production.

A new destination only. Source mode=ro/immutable requires a quiescent validated
source with no WAL/SHM. Preserve every original schema object, rowid, event and
sequence. Verification records source typed digests while streaming; candidate
comparison covers every row, including every reconstructed detail. BUILDING or
INTERRUPTED output is never VERIFIED. No startup/deployment authority.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.core import journal_evidence as E

HEADROOM=10*1024**3


def q(name):return '"'+name.replace('"','""')+'"'


def update_digest(h,row):
    h.update(b'R'+struct.pack('>Q',len(row)))
    for value in row:
        if value is None:h.update(b'N')
        elif isinstance(value,int):h.update(b'I'+struct.pack('>q',value))
        elif isinstance(value,float):h.update(b'F'+struct.pack('>d',value))
        else:
            data=value.encode('utf-8') if isinstance(value,str) else bytes(value)
            h.update((b'T' if isinstance(value,str) else b'B')+struct.pack('>Q',len(data))+data)


def protect_source(path):
    for suffix in ('-wal','-shm','-journal'):
        if Path(str(path)+suffix).exists():raise RuntimeError('source_sidecar_present:'+suffix)
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA temp_store=MEMORY')
    return db


def space(root):
    if (root.parent/'evidence/unsafe.stop').exists():raise RuntimeError('runtime_guard_failed')
    free=os.statvfs(root).f_bavail*os.statvfs(root).f_frsize
    if free<HEADROOM:raise RuntimeError('free_space_headroom_failed')
    return free


def dump(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2));os.replace(tmp,path)


def migrate(source,destination,*,estimate,stop_after=None):
    source=Path(source);destination=Path(destination)
    if destination.exists():raise FileExistsError('destination_exists')
    if os.statvfs(destination.parent).f_bavail*os.statvfs(destination.parent).f_frsize < estimate+HEADROOM:
        raise RuntimeError('estimate_plus_headroom_does_not_fit')
    destination.mkdir(mode=0o700)
    status={'status':'BUILDING','source':str(source),'estimate_bytes':estimate,'headroom_bytes':HEADROOM,'started':time.time(),'completed_tables':{}}
    dump(destination/'manifest.json',status)
    src=protect_source(source)
    db=sqlite3.connect(destination/source.name);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA temp_store=MEMORY');db.execute('PRAGMA cache_size=-32768');db.execute('PRAGMA wal_autocheckpoint=4096');db.execute('PRAGMA foreign_keys=OFF')
    E.install(db)
    schema=list(src.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"))
    tables=[r for r in schema if r[0]=='table' and r[1]!='sqlite_sequence']
    events_written=0
    try:
        for _,name,_,sql in tables:db.execute(sql)
        db.executescript(E.SCHEMA)
        db.execute('INSERT INTO journal_representation_v1 VALUES (?,?,?)',('detail.v1','BUILDING',json.dumps(status)));db.commit()
        source_digests={}
        with (destination/'event-detail-hashes.jsonl').open('w') as hashes:
            for _,name,_,sql in tables:
                has_rowid='WITHOUT ROWID' not in sql.upper()
                columns=[r[1] for r in src.execute('PRAGMA table_xinfo('+q(name)+')') if r[6]==0]
                select_cols=('rowid,' if has_rowid else '')+','.join(q(c) for c in columns)
                order='rowid' if has_rowid else ','.join(q(c) for c in columns)
                query='SELECT '+select_cols+' FROM '+q(name)+' ORDER BY '+order
                insert='INSERT INTO '+q(name)+'('+select_cols+') VALUES ('+','.join('?' for _ in range(len(columns)+int(has_rowid)))+')'
                count=0;h=hashlib.sha256();logical=0;transformed=0
                detail_index=columns.index('detail')+int(has_rowid) if name=='brain_events' else None
                kind_index=columns.index('kind')+int(has_rowid) if name=='brain_events' else None
                id_index=columns.index('id')+int(has_rowid) if name=='brain_events' else None
                ts_index=columns.index('ts')+int(has_rowid) if name=='brain_events' else None
                subject_index=columns.index('subject')+int(has_rowid) if name=='brain_events' else None
                for row in src.execute(query):
                    update_digest(h,row);out=list(row)
                    if name=='brain_events':
                        detail=row[detail_index];raw=detail.encode('utf-8') if detail is not None else None
                        rec={'id':row[id_index],'rowid':row[0] if has_rowid else None,'ts':row[ts_index],'kind':row[kind_index],'subject':row[subject_index], 'detail_sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None,'detail_bytes':len(raw) if raw is not None else None,'transformed':row[kind_index]=='market_provenance'}
                        hashes.write(json.dumps(rec,separators=(',',':'))+'\n')
                        if rec['transformed']:
                            out[detail_index]=E.store(db,detail);logical+=len(raw);transformed+=1
                        events_written+=1
                    db.execute(insert,out);count+=1
                    if count%1000==0 or name=='brain_events':
                        db.commit();space(destination)
                        if name=='brain_events' and count%20==0:
                            status['current']={'table':name,'rows':count,'logical_detail_bytes':logical,'candidate_bytes':sum(p.stat().st_size for p in destination.glob(source.name+'*'))};dump(destination/'manifest.json',status);print(json.dumps(status['current']),flush=True)
                    if stop_after is not None and events_written>=stop_after:raise InterruptedError('injected_migration_interruption')
                db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');space(destination)
                source_digests[name]={'count':count,'sha256':h.hexdigest(),'columns':columns,'rowid':has_rowid,'transformed':transformed,'logical_transformed_bytes':logical}
                status['completed_tables'][name]=source_digests[name];dump(destination/'manifest.json',status);print(json.dumps({'table_complete':name,'rows':count}),flush=True)
        if src.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            db.execute('DELETE FROM sqlite_sequence');rows=list(src.execute('SELECT rowid,name,seq FROM sqlite_sequence ORDER BY rowid'))
            db.executemany('INSERT INTO sqlite_sequence(rowid,name,seq) VALUES (?,?,?)',rows)
            h=hashlib.sha256()
            for row in rows:update_digest(h,row)
            source_digests['sqlite_sequence']={'count':len(rows),'sha256':h.hexdigest(),'columns':['name','seq'],'rowid':True}
        db.commit()
        for typ,name,_,sql in schema:
            if typ in ('index','trigger','view'):db.execute(sql)
        db.execute('PRAGMA user_version='+str(src.execute('PRAGMA user_version').fetchone()[0]));db.execute('PRAGMA application_id='+str(src.execute('PRAGMA application_id').fetchone()[0]));db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        dump(destination/'source-table-digests.json',source_digests)
        dump(destination/'source-schema.json',schema)
        status['status']='COPIED_UNVERIFIED';dump(destination/'manifest.json',status)
        verify(db,destination,source_digests,schema)
        status['status']='VERIFIED';status['completed']=time.time();status['candidate_total_bytes']=sum(p.stat().st_size for p in destination.iterdir() if p.is_file());status['free_space_remaining']=space(destination)
        db.execute('UPDATE journal_representation_v1 SET status=?,manifest=? WHERE version=?',('VERIFIED',json.dumps(status),'detail.v1'));db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');dump(destination/'manifest.json',status)
        print(json.dumps({'verified':True,'free':status['free_space_remaining']}),flush=True)
    except BaseException as error:
        db.rollback();status['status']='INTERRUPTED' if isinstance(error,(InterruptedError,KeyboardInterrupt)) else 'FAILED';status['error']=repr(error);dump(destination/'manifest.json',status);raise
    finally:db.close();src.close()


def verify(db,destination,digests,schema):
    report={'status':'VERIFYING','unchanged_tables':{},'events':{},'schema_exact':False}
    # Exact original DDL for every source object remains; additions are explicit.
    for typ,name,table,sql in schema:
        row=db.execute('SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
        if row!=(typ,table,sql):raise RuntimeError('schema_changed:'+name)
    report['schema_exact']=True
    for name,expected in digests.items():
        cols=('rowid,' if expected['rowid'] else '')+','.join(q(c) for c in expected['columns'])
        query='SELECT '+cols+' FROM '+q(name)+' ORDER BY '+('rowid' if expected['rowid'] else ','.join(q(c) for c in expected['columns']))
        h=hashlib.sha256();count=0
        detail_index=expected['columns'].index('detail')+int(expected['rowid']) if name=='brain_events' else None
        for row in db.execute(query):
            row=list(row)
            if name=='brain_events':row[detail_index]=E.resolve(db,row[detail_index])
            update_digest(h,row);count+=1
            if count%10000==0:space(destination)
        result={'count':count,'sha256':h.hexdigest(),'match':count==expected['count'] and h.hexdigest()==expected['sha256']}
        report['unchanged_tables'][name]=result
        if not result['match']:raise RuntimeError('content_digest_mismatch:'+name)
        dump(destination/'verification.json',report);print(json.dumps({'verified_table':name,'rows':count}),flush=True)
    transformed=0;all_events=0
    for line in (destination/'event-detail-hashes.jsonl').open():
        record=json.loads(line);row=db.execute('SELECT rowid,id,ts,kind,subject,detail FROM brain_events WHERE id=?',(record['id'],)).fetchone()
        if tuple(row[:5])!=(record['rowid'],record['id'],record['ts'],record['kind'],record['subject']):raise RuntimeError('event_identity_changed')
        raw=E.resolve(db,row[5]);data=raw.encode('utf-8') if raw is not None else None
        if (hashlib.sha256(data).hexdigest() if data is not None else None)!=record['detail_sha256']:raise RuntimeError('event_detail_hash_changed')
        all_events+=1;transformed+=int(record['transformed'])
        if all_events%50==0:space(destination);print(json.dumps({'event_hashes_verified':all_events}),flush=True)
    report['events']={'all':all_events,'transformed':transformed,'all_detail_hashes_match':True,'all_identities_match':True}
    # Every dependency, including unreferenced blobs, is checked; no missing or
    # corrupt referenced data can be accepted because resolver verifies both.
    blobs=0
    for sha,n,codec,payload in db.execute('SELECT * FROM journal_evidence_blobs_v1'):
        E._blob((n,codec,payload),sha);blobs+=1
    report['verified_blobs']=blobs
    print('complete integrity_check starting',flush=True)
    integrity=list(db.execute('PRAGMA integrity_check'));report['integrity_check']=integrity
    if integrity!=[('ok',)]:raise RuntimeError('integrity_check_failed')
    fk=list(db.execute('PRAGMA foreign_key_check'));report['foreign_key_check']=fk
    if fk:raise RuntimeError('foreign_key_check_failed')
    report['status']='PASS';dump(destination/'verification.json',report)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--destination',required=True);p.add_argument('--estimate-bytes',required=True,type=int);p.add_argument('--stop-after-events',type=int);args=p.parse_args()
    migrate(args.source,args.destination,estimate=args.estimate_bytes,stop_after=args.stop_after_events)
