import gzip
import sys,json,sqlite3,cProfile,pstats,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from trader.core import journal_evidence as E
from trader.learning import capture_runtime as R,foundation as L
from trader.observability.attention import capture,settings
p=Path('docs/tracker/evidence/perf03-capture-r1')
d=sqlite3.connect('file:data/luffy.db?mode=ro',uri=True);d.row_factory=sqlite3.Row
sid='scan_ff5de8fb66fe4d8d8b01619fcf0482fd'
def blob(sha):
 row=d.execute('SELECT payload FROM learning_source_blobs WHERE sha256=?',(sha,)).fetchone();raw=json.loads(E.resolve(d,row[0]));assert L.digest(raw)==sha;return raw
rows=d.execute('SELECT id,symbol FROM decisions WHERE scan_id=? ORDER BY rowid',(sid,)).fetchall();print('decisions',len(rows),flush=True)
reg=json.loads(d.execute('SELECT payload FROM learning_registrations WHERE event_key=?',('decision:'+rows[0]['id'],)).fetchone()[0]);dep=next(x for x in reg['dependencies'] if x['role']=='data');raw=blob(dep['sha256'])
chunks={s:blob(x['4h']) for s,x in raw['chunks']['universe'].items() if x.get('4h')}
cut=1791203403789
event={}
receipts={}
for id,detail in d.execute("SELECT id,detail FROM brain_events WHERE kind='market_provenance' AND id>=5280"):
 if detail.startswith(E.PREFIX):
  manifest=json.loads(detail[len(E.PREFIX):]);sha=manifest['chunks'][0];row=d.execute('SELECT byte_length,codec,payload FROM journal_evidence_blobs_v1 WHERE sha256=?',(sha,)).fetchone();head=E._decode_blob(row,sha).decode()
 else:head=detail[:1000]
 if rows[0]['id'] in head[:300]:
  receipts=json.loads(E.resolve(d,detail))['receipt']['universe_membership_receipts'];break
assert receipts

fixture=dict(scan_id=sid,cut=cut,members=[r['symbol'] for r in rows],frames=chunks,membership_receipts=receipts,cfg=settings(),retained_event=event,dependency=dep)
with gzip.GzipFile(str(p/'retained-fixture.json.gz'),'wb',mtime=0) as retained:
 retained.write(json.dumps(fixture,sort_keys=True).encode())
print('Reconstructed fixture written; original producer packet is not retained.')
