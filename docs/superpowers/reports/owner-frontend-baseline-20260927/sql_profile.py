import sys,json,sqlite3,time,threading,statistics
from pathlib import Path
sys.path.insert(0,'/home/sarmad/trader-world')
from trader.core.journal import Journal
ROOT=Path('/tmp/owner-frontend-v1'); path=ROOT/'data/luffy.db'; c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
old=json.loads((ROOT/'baseline-80ms.json').read_text())
plans=[]
for row in old['measurements'][0]['queries']:
    sql=row['sql']; params=('2026-09-27%',) if '?' in sql else ()
    plans.append({'sql':sql,'plan':c.execute('EXPLAIN QUERY PLAN '+sql,params).fetchall()})
c.close()
# Idle-store timings only: no production process or data is involved.
measure=[];j=Journal(str(path))
for _ in range(25):
    t=time.perf_counter();j.query("SELECT * FROM trades WHERE status='closed' ORDER BY opened_at DESC LIMIT 25");measure.append((time.perf_counter()-t)*1000)
ready=threading.Event()
def hold():
    db=sqlite3.connect(path);db.execute('BEGIN IMMEDIATE');ready.set();time.sleep(.25);db.rollback();db.close()
t=threading.Thread(target=hold);t.start();ready.wait();start=time.perf_counter();j.query('SELECT * FROM equity ORDER BY ts DESC LIMIT 2');read_ms=(time.perf_counter()-start)*1000
start=time.perf_counter();other=Journal(str(path));startup_ms=(time.perf_counter()-start)*1000;t.join()
result={'query_plans':plans,'list_25_samples_ms':measure,'wal_read_while_writer_ms':read_ms,'journal_initialization_with_250ms_writer_ms':startup_ms,'condition':'isolated fixture; Journal __init__ performs DDL/migrations/backfill statements; no production DB; OS caches warm'}
(ROOT/'sql-results.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('query_plans','list_25_samples_ms')}));print('list p95_ms', sorted(measure)[23])
