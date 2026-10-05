import sys,sqlite3,json,time,subprocess,os,threading
from pathlib import Path
W='/mnt/luffy-data/luffy/workspaces/runtime-observer-attention-repair-r1';sys.path.insert(0,W)
from trader.observability.safety import SafetyHealth,SafetyObserver
O=Path(__file__).parent
if '--ioerr-child' in sys.argv:
 o=SafetyObserver(SafetyHealth(O/'ioerr-health.json',sink=lambda e:None));print(json.dumps(o.store(O/'isolated-ioerr.db','journal',journal=True)));raise SystemExit()
def setup(p):
 p.unlink(missing_ok=True)
 with sqlite3.connect(p) as db:
  db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT)')
def probe(p,label):return SafetyObserver(SafetyHealth(O/(label+'-health.json'),sink=lambda e:None)).store(p,'journal',journal=True)
results={};p=O/'isolated-busy.db';setup(p)
with sqlite3.connect(p) as writer:
 writer.execute('BEGIN IMMEDIATE');t=time.monotonic();phase='connect'
 try:
  with sqlite3.connect(p,timeout=0) as db:
   phase='read';db.execute('PRAGMA quick_check').fetchall();db.execute('SELECT key,value FROM state_kv LIMIT 1').fetchall();phase='write';db.execute('INSERT OR REPLACE INTO state_kv VALUES (?,?)',('probe','x'));phase='commit';db.commit()
 except sqlite3.Error as e:results['deployed_zero_timeout']=dict(sqlite_errorcode=e.sqlite_errorcode,sqlite_errorname=e.sqlite_errorname,phase=phase,elapsed_s=time.monotonic()-t,competing_writer_pid=os.getpid(),competing_writer_transaction='BEGIN IMMEDIATE',committed=False)
 results['persistent_busy']=probe(p,'busy')
for label in ('full','readonly'):
 p=O/('isolated-'+label+'.db');setup(p)
 if label=='full':
  with sqlite3.connect(p) as db:
   db.execute('CREATE TABLE quota(value BLOB)');db.execute('CREATE TRIGGER exhaust BEFORE INSERT ON state_kv BEGIN INSERT INTO quota VALUES(zeroblob(100000)); END;')
 connect=sqlite3.connect
 def failure(*a,**k):
  db=connect(*a,**k)
  if label=='full':db.execute('PRAGMA max_page_count='+str(db.execute('PRAGMA page_count').fetchone()[0]))
  else:db.execute('PRAGMA query_only=ON')
  return db
 sqlite3.connect=failure
 try:results[label]=probe(p,label)
 finally:sqlite3.connect=connect
p=O/'isolated-ioerr.db';setup(p);env=dict(os.environ,LD_PRELOAD=str(O/'io_fault.so'))
r=subprocess.run([sys.executable,str(Path(__file__)),'--ioerr-child'],env=env,text=True,capture_output=True,check=True);results['ioerr']=json.loads(r.stdout)
for name,r in results.items():print(name,json.dumps(r),flush=True)
assert results['full']['sqlite_error']['sqlite_errorcode']==sqlite3.SQLITE_FULL
assert results['readonly']['sqlite_error']['sqlite_errorcode']&255==sqlite3.SQLITE_READONLY
assert results['ioerr']['sqlite_error']['sqlite_errorcode']&255==sqlite3.SQLITE_IOERR
assert not any(x.get('committed') for x in results.values())
(O/'storage-failure-reproductions.json').write_text(json.dumps(results,indent=2))
