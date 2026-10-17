"""Extract retained evidence read-only; never opens source SQLite for writing."""
import gzip, json, sqlite3, hashlib
from pathlib import Path
from trader.core import journal_evidence as E
from trader.learning.foundation import digest
OUT=Path(__file__).parent
SOURCE=Path('/mnt/luffy-data/luffy/production/data/luffy.db')
c=sqlite3.connect('file:'+str(SOURCE)+'?mode=ro&immutable=1',uri=True)
c.row_factory=sqlite3.Row
ac=sqlite3.connect('.da06-tmp/attention.db');ac.row_factory=sqlite3.Row
scan=ac.execute('select * from scans order by as_of_ms desc limit 1').fetchone()
payload=json.loads(E.resolve(ac,scan['payload']))
causes=[json.loads(r[0]) for r in ac.execute('select payload from causes where scan_id=? and symbol != ? order by symbol',(scan['scan_id'],''))]
attention_bars={}
for ref in payload['input_versions']:
    raw=json.loads(E.resolve(ac,ac.execute('select payload from versions where id=?',(ref['version_id'],)).fetchone()[0]))
    raw.update(version_id=ref['version_id'],available_ms=ref['first_seen_ms'])
    attention_bars.setdefault(ref['symbol'],[]).append(raw)
blobs={}
def blob(sha):
    row=c.execute('select payload from learning_source_blobs where sha256=?',(sha,)).fetchone()
    raw=json.loads(E.resolve(c,row[0]));assert digest(raw)==sha
    blobs[sha]=raw
    return raw
entries=[]
for cause in causes:
    d=dict(c.execute('select * from decisions where id=?',(cause['decision_id'],)).fetchone())
    reg=json.loads(c.execute('select payload from learning_registrations where event_key=?',('decision:'+d['id'],)).fetchone()[0])
    dep=next(x for x in reg['dependencies'] if x['role']=='data')
    raw=blob(dep['sha256'])
    for section in ('dfs','derivs','market'):
        for sha in raw['chunks'][section].values():
            if sha:blob(sha)
    cfg=next(x for x in reg['dependencies'] if x['role']=='risk_config')
    blob(cfg['sha256'])
    entries.append(dict(decision=d,cause=cause,data_sha=dep['sha256'],config_sha=cfg['sha256'],
        historical_votes=[dict(r) for r in c.execute('select * from votes where cycle_id=? order by rowid',(d['cycle_id'],))]))
fixture=dict(schema='da06.retained.v1',source=dict(path=str(SOURCE),mode='ro immutable base pages; excludes WAL',
    attention_mode='copied DB + copied WAL; no source SQLite open',
    attention_db_sha256=hashlib.sha256(Path('.da06-tmp/attention.db').read_bytes()).hexdigest()),
    attention=payload,attention_bars=attention_bars,entries=entries,blobs=blobs,
    limitation='Historical scan predates hierarchical admission and typed analyst records. Replay compares current baseline/new code on retained bytes; it does not assert reproduction of old code decisions or a historical 12-slot receipt.')
with gzip.GzipFile(str(OUT/'retained.json.gz'),'wb',mtime=0) as f:f.write(json.dumps(fixture,sort_keys=True).encode())
print(json.dumps(dict(scan_id=scan['scan_id'],entries=len(entries),blobs=len(blobs),bytes=(OUT/'retained.json.gz').stat().st_size)))
