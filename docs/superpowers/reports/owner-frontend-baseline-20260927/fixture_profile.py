import sys, os, time, json, asyncio, sqlite3, resource, inspect
from starlette.concurrency import run_in_threadpool
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, '/home/sarmad/trader-world')
from trader.core.journal import Journal
import subprocess, types
server = types.ModuleType('trader.dashboard.profile_original')
server.__file__ = '/home/sarmad/trader-world/trader/dashboard/server.py'
server.__package__ = 'trader.dashboard'
original = subprocess.check_output(['git', 'show', '1eb00e151113d0add6033ef809ccdf6e30623003:trader/dashboard/server.py'], cwd='/home/sarmad/trader-world', text=True)
exec(compile(original, server.__file__, 'exec'), server.__dict__)
from trader.core.config import Env
from fastapi import APIRouter
ROOT=Path('/tmp/owner-frontend-v1')
(ROOT/'data').mkdir(exist_ok=True)
j=Journal(str(ROOT/'data/luffy.db'))
with j._tx() as c:
    if not c.execute('select count(*) from votes').fetchone()[0]:
        c.execute("insert into cycles(id,ts,symbol) values('fixture','2026-09-27T00:00:00+00:00','FIXTURE/USDT')")
        c.executemany('insert into votes(cycle_id,ts,symbol,agent,side,conviction,confidence,rationale) values(?,?,?,?,?,?,?,?)',(('fixture','2026-09-27T00:00:00+00:00','FIXTURE/USDT',f'fixture-{i%8}','long',.5,.5,'synthetic load fixture '+('x'*256)) for i in range(100000)))
        c.executemany('insert into decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence) values(?,?,?,?,?,?,?,?)',((str(i),'fixture','2026-09-27T00:00:00+00:00','FIXTURE/USDT','HOLD',0,.5,.5) for i in range(100000)))
        c.executemany('insert into trades(id,symbol,side,amount,entry_price,opened_at,status,closed_at,realized_pnl) values(?,?,?,?,?,?,?,?,?)',((str(i),'FIXTURE/USDT','long',1,100,'2026-09-26','closed','2026-09-27',i%3-1) for i in range(10000)))
        c.execute("insert into equity values('2026-09-27T00:00:00+00:00',1000,1000,0)")
metrics={'fixture':{'provenance':'SYNTHETIC; no production copy; shape representative, production distribution unverified','votes':100000,'decisions':100000,'trades':10000},'measurements':[]}
class Reply:
    def json(self): return {'assets':[], 'price':'100','totalMarginBalance':'1000'}
def net(*a,**k):
    time.sleep(float(os.environ.get("PROFILE_DELAY_S", ".08")))
    return Reply()
class Feed:
    def __init__(self,*a,**kw): pass
    def price(self,s): time.sleep(float(os.environ.get("PROFILE_DELAY_S", ".08"))); return 100
async def main():
    app=server.create_app({'attention':{'enabled':False}})
    endpoint=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/api/summary')
    for label in (['cold'] if float(os.environ.get('PROFILE_DELAY_S', '.08')) > 1 else ['cold','warm','warm','warm','warm']):
        queries=[]
        original=Journal.query
        def traced(self,sql,params=()):
            start=time.perf_counter(); result=original(self,sql,params)
            queries.append({'sql':sql,'ms':(time.perf_counter()-start)*1000,'rows':len(result)})
            return result
        lag=[]
        async def tick():
            t=time.perf_counter(); await asyncio.sleep(.001); lag.append((time.perf_counter()-t)*1000)
        task=asyncio.create_task(tick()); await asyncio.sleep(0)
        with patch.object(Journal,'query',traced):
            start=time.perf_counter(); cpu=time.process_time(); result=await endpoint() if inspect.iscoroutinefunction(endpoint) else await run_in_threadpool(endpoint); elapsed=(time.perf_counter()-start)*1000
        await task
        metrics['measurements'].append({'kind':label,'wall_ms':elapsed,'cpu_ms':(time.process_time()-cpu)*1000,'event_loop_delay_ms':lag[0],'payload_bytes':len(json.dumps(result).encode()),'queries':queries})
    c=sqlite3.connect(ROOT/'data/luffy.db'); c.execute('BEGIN IMMEDIATE')
    start=time.perf_counter(); j.query('select * from equity order by ts desc limit 2'); metrics['wal_writer_read_ms']=(time.perf_counter()-start)*1000; c.rollback(); c.close()
    metrics['max_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    metrics['store_bytes']=sum(p.stat().st_size for p in (ROOT/'data').glob('luffy.db*'))
    metrics['network_model']=f'12 serialized calls at simulated {os.environ.get("PROFILE_DELAY_S", ".08")}s each, never real venue calls'
    (ROOT/os.environ.get('PROFILE_OUTPUT', 'baseline.json')).write_text(json.dumps(metrics,indent=2))
    print(json.dumps({k:v for k,v in metrics.items() if k!='measurements'}))
    print([(x['kind'],round(x['wall_ms'],1),round(x['event_loop_delay_ms'],1)) for x in metrics['measurements']])
with patch.object(server,'ROOT',ROOT),patch.object(server,'make_graphql_router',lambda _:APIRouter()),patch.dict(os.environ,{'DASH_TOKEN':'isolated-profile-token'}),patch('trader.data.feed.DataFeed',Feed),patch('trader.data.feed.make_exchange',lambda *a:None),patch.object(Env,'binance_keys',return_value=('fixture','fixture')),patch('requests.get',net):
    asyncio.run(main())
