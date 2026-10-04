#!/usr/bin/env python3
"""Bounded real-record lossless proof and measured equivalent cycle writes."""
import hashlib,json,os,sqlite3,sys,time,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.core import journal_evidence as E
from compact_journal import protect_source,dump

root=Path(__file__).resolve().parents[2];proof=root/'proof-r2';proof.mkdir(mode=0o700)
source=Path('/mnt/luffy-recovery/recovery/journal-working-20261004T141639Z-c048bf08/journal/luffy.db');src=protect_source(source)
# Includes both first provenance (small/older) and the largest repeated payload.
largest_id=4622
largest=src.execute('SELECT detail FROM brain_events WHERE id=?',(largest_id,)).fetchone()[0]
body=json.loads(largest);cycle=body['cycle_id'];receipt=body['receipt'];largest_bytes=len(largest.encode());del body
# Orchestrator uses a distinct cycle_id for each decision. This is an
# equivalent 21-decision scan window with all original identities preserved.
ids=[r[0] for r in src.execute("SELECT id FROM brain_events WHERE kind='market_provenance' AND id BETWEEN ? AND ? ORDER BY id",(largest_id-10,largest_id+10))]
if len(ids)!=21:raise RuntimeError('representative_window_not_21:'+str(ids))
old=sqlite3.connect(proof/'before.db');new=sqlite3.connect(proof/'after.db')
for db in (old,new):
 db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA wal_autocheckpoint=0');db.execute('CREATE TABLE brain_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,kind TEXT NOT NULL,subject TEXT,detail TEXT)');db.executescript(E.SCHEMA);db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
E.install(new)
def size(path):return {suffix:Path(str(path)+suffix).stat().st_size if Path(str(path)+suffix).exists() else 0 for suffix in ('','-wal','-shm')}
before_baseline=size(proof/'before.db');after_baseline=size(proof/'after.db');logical_before=0;physical_detail=0;ratio=0;hashes=[];receipt_hashes=[]
for id in ids:
 row=src.execute('SELECT id,ts,kind,subject,detail FROM brain_events WHERE id=?',(id,)).fetchone();raw=row[-1];data=raw.encode();logical_before+=len(data)
 encoded=E.store(new,raw);new.execute('INSERT INTO brain_events VALUES (?,?,?,?,?)',(*row[:-1],encoded));new.commit()
 old.execute('INSERT INTO brain_events VALUES (?,?,?,?,?)',row);old.commit()
 resolved=E.resolve(new,encoded)
 assert resolved==raw and resolved.encode()==data and json.loads(resolved)==json.loads(raw)
 original_hash=hashlib.sha256(data).hexdigest();hashes.append({'id':id,'detail_sha256':original_hash,'bytes':len(data),'matches':True})
 # Historical canonical receipt and all stored nested receipt identities retain
 # exact values because the entire logical object is equal; compare canonical
 # receipt hashes independently of the event byte hash.
 for item in (json.loads(raw)['receipt'],json.loads(resolved)['receipt']):
  receipt_hashes.append(hashlib.sha256(json.dumps(item,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest())
 assert receipt_hashes[-1]==receipt_hashes[-2]
 compressed=sum(len(zlib.compress(p.encode(),6)) for p in E.pieces(raw));ratio=max(ratio,compressed/len(data));physical_detail+=len(encoded.encode())
 print(json.dumps({'proof_event':id,'raw_bytes':len(data),'manifest_bytes':len(encoded.encode())}),flush=True)
before_growth=size(proof/'before.db');after_growth=size(proof/'after.db')
unique=new.execute('SELECT count(*),sum(byte_length),sum(length(payload)) FROM journal_evidence_blobs_v1').fetchone()
# A bounded older provenance event and a legacy non-JSON/JSON byte record.
first=src.execute("SELECT id FROM brain_events WHERE kind='market_provenance' ORDER BY id LIMIT 1").fetchone()[0]
for id in (first,1):
 raw=src.execute('SELECT detail FROM brain_events WHERE id=?',(id,)).fetchone()[0];marker=E.store(new,raw);assert E.resolve(new,marker)==raw
# Repeated event references add only the manifest; no payload blob count growth.
count=new.execute('SELECT count(*) FROM journal_evidence_blobs_v1').fetchone()[0];same=E.store(new,largest);assert new.execute('SELECT count(*) FROM journal_evidence_blobs_v1').fetchone()[0]==count
new.commit()
report={'status':'PASS','largest_decision_cycle_id':cycle,'cycle_semantics':'Equivalent 21-decision scan window; original code allocates a distinct cycle_id per decision','event_ids':ids,'decisions':len(ids),'largest_event_id':largest_id,'largest_detail_bytes':largest_bytes,'all_reconstructed_bytes_match':True,'all_logical_records_match':True,'historical_receipt_hashes_match':True,'event_hashes':hashes,'receipt_hash_pairs':receipt_hashes,'legacy_and_early_records':True,'logical_before_bytes':logical_before,'logical_after_manifest_bytes':physical_detail,'unique_blob_count':unique[0],'unique_blob_uncompressed_bytes':unique[1],'unique_blob_compressed_bytes':unique[2],'max_sample_nondedup_compression_ratio':ratio,'before_baseline':before_baseline,'after_baseline':after_baseline,'before_database_wal_growth':{s:before_growth[s]-before_baseline[s] for s in before_growth},'after_database_wal_growth':{s:after_growth[s]-after_baseline[s] for s in after_growth},'measurement_scope':'Real historical 21-decision cycle, exact provenance detail writes with same event metadata; other unchanged per-cycle tables add equal bytes and are excluded. WAL autocheckpoint disabled, commit per event, same page size and FULL synchronous. Includes physical DB/WAL and logical manifest + unique payload storage.'}
dump(root/'evidence/representation-proof.json',report)
for db in (old,new):db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()
# Conservative source-space upper estimate from observed payloads; cap remains
# guarded at every transaction even if the sample estimate proves optimistic.
source_size=source.stat().st_size;logical_total=65055107880
unchanged_upper=source_size-logical_total
compressed_upper=int(logical_total*min(1.05,max(.10,ratio*2)))
manifest_upper=512*1024*2311
indexes_temp=4*1024**3;wal=512*1024**2;workspace=2*1024**3
estimate=unchanged_upper+compressed_upper+manifest_upper+indexes_temp+wal+workspace
budget={'source_bytes':source_size,'provenance_characters_ascii_dominated':logical_total,'unchanged_tables_and_existing_indexes_upper':unchanged_upper,'sample_max_compression_ratio':ratio,'conservative_compressed_payload_upper':compressed_upper,'manifest_upper':manifest_upper,'indexes_and_temporary_upper':indexes_temp,'wal_upper':wal,'verification_workspace_upper':workspace,'estimate_bytes':estimate,'headroom_bytes':10*1024**3,'free_bytes':os.statvfs(root).f_bavail*os.statvfs(root).f_frsize,'fit':estimate+10*1024**3<os.statvfs(root).f_bavail*os.statvfs(root).f_frsize}
dump(root/'evidence/candidate-space-estimate.json',budget);print(json.dumps(budget),flush=True)
