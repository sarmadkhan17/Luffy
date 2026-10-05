import sys,sqlite3,json,time,hashlib
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,'/mnt/luffy-data/luffy/workspaces/runtime-observer-attention-repair-r1')
from trader.core import journal_evidence as E
from trader.learning import capture_runtime as R,foundation as L
from trader.observability import collector as C,scan_source as S
P=Path('/mnt/luffy-data/luffy/production');O=Path(__file__).parent;I=Path('/mnt/luffy-recovery/recovery/continued-frozen-observation-r1/evidence')
d=sqlite3.connect((P/'data/luffy.db').as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row
f=json.load(open(I/'final-state.json'));errors=f['last_heartbeat']['context']['attention']['recent_errors'];attempts={r['heartbeat']['context']['attention']['last_scan_id']:r['heartbeat']['context']['attention'] for r in json.load(open(I/'cycles.json'))};attempts[f['last_heartbeat']['context']['attention']['last_scan_id']]=f['last_heartbeat']['context']['attention']
def blob(sha):
 raw=json.loads(E.resolve(d,d.execute('SELECT payload FROM learning_source_blobs WHERE sha256=?',(sha,)).fetchone()[0]));assert L.digest(raw)==sha;return raw
# Locate exact decision-specific acquisition receipts using only their first
# verified chunk, then reconstruct only selected roots.
roots={}
for id,detail in d.execute("SELECT id,detail FROM brain_events WHERE kind='market_provenance' AND id>=5280"):
 if detail.startswith(E.PREFIX):
  m=json.loads(detail[len(E.PREFIX):]);sha=m['chunks'][0];row=d.execute('SELECT byte_length,codec,payload FROM journal_evidence_blobs_v1 WHERE sha256=?',(sha,)).fetchone();head=E._decode_blob(row,sha).decode()
 else:head=detail[:1000]
 import re
 match=re.search(r'"decision_id"\s*:\s*"([a-z0-9_]+)"',head[:300])
 if match:roots[match[1]]=(id,detail)
results=[]
for err in errors:
 decisions=d.execute('SELECT id,symbol FROM decisions WHERE scan_id=? ORDER BY rowid',(err['scan_id'],)).fetchall();dec=decisions[0];reg=json.loads(d.execute('SELECT payload FROM learning_registrations WHERE event_key=?',('decision:'+dec['id'],)).fetchone()[0]);dep=next(x for x in reg['dependencies'] if x['role']=='data');raw=blob(dep['sha256']);frames={s:{tf:R.restore_frame(blob(sha)) for tf,sha in x.items() if tf=='4h' and sha} for s,x in raw['chunks']['universe'].items()}
 acquisition=json.loads(E.resolve(d,roots[dec['id']][1]))['receipt'];receipts=acquisition['universe_membership_receipts'];members=[x['symbol'] for x in decisions]
 h=attempts[err['scan_id']];cut=h['last_attempt_ms']+round(h['capture_ms']);directory=O/('fixed-'+str(err['seq']));directory.mkdir(exist_ok=True)
 if not (directory/'luffy.db').exists():(directory/'luffy.db').symlink_to(O/'retained-clone/luffy.db')
 for ext in ('','-wal','-shm'):(directory/('attention.db'+ext)).unlink(missing_ok=True)
 old=C.uuid4;C.uuid4=lambda:SimpleNamespace(hex=err['scan_id'].removeprefix('scan_'))
 child=C.Collector(directory,start=True)
 t=time.perf_counter()
 try:
  sid=child.begin(frames,members,as_of_ms=cut,membership_receipts=receipts);assert sid==err['scan_id']
  causesdb=sqlite3.connect((P/'data/attention.db').as_uri()+'?mode=ro',uri=True)
  items=[json.loads(r[0]) for r in causesdb.execute('SELECT payload FROM causes WHERE scan_id=? AND symbol!=?',(sid,''))];causesdb.close()
  child.causes(sid,items);deadline=time.monotonic()+25
  while child.queue.unfinished_tasks and time.monotonic()<deadline:time.sleep(.05)
  assert not child.queue.unfinished_tasks
  health=child.health();assert health['errors']==0,health['recent_errors'];assert health['last_complete']['scan_id']==sid,health
  db=sqlite3.connect(directory/'attention.db');scan=S.read(db,sid,time.monotonic()+5);assert scan['scan_id']==sid
  text=db.execute('SELECT payload FROM scans WHERE scan_id=?',(sid,)).fetchone()[0];physical=db.execute('SELECT detail FROM scan_sources_v1 WHERE scan_id=?',(sid,)).fetchone()[0];assert E.resolve(db,physical)==text
  r=dict(scan_id=sid,retained_decision_id=dec['id'],retained_brain_event_id=roots[dec['id']][0],input_sha256=dep['sha256'],original_event_retained=False,replay_cut_ms=cut,capture_ms=health['capture_ms'],elapsed_s=time.perf_counter()-t,worker_errors=health['errors'],last_complete=health['last_complete'],logical_bytes=len(text.encode()),manifest_bytes=len(physical.encode()),logical_sha256=hashlib.sha256(text.encode()).hexdigest(),portfolio_source_read='PASS',stored_allocation_bytes=(directory/'attention.db').stat().st_blocks*512,selection_outcome=scan.get('selection'))
  results.append(r);print(json.dumps({k:v for k,v in r.items() if k!='selection_outcome'}),flush=True)
 finally:child.close();C.uuid4=old
(O/'attention-fixed-reproductions.json').write_text(json.dumps(results,indent=2))
