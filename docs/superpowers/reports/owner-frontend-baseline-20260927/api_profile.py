import asyncio,json,os,sqlite3,subprocess,time
from pathlib import Path
import httpx
ROOT=Path('/tmp/owner-frontend-v1');URL='http://127.0.0.1:18786';HEADERS={'x-luffy-token':'isolated-profile-token'}
pid=int(subprocess.check_output(['pgrep','-f','^/home/sarmad/trader/venv/bin/python /tmp/owner-frontend-v1/browser_server.py$'],text=True).strip())
def proc():
    parts=Path(f'/proc/{pid}/stat').read_text().split();return {'cpu_s':(int(parts[13])+int(parts[14]))/os.sysconf('SC_CLK_TCK'),'rss_bytes':int(parts[23])*os.sysconf('SC_PAGE_SIZE')}
def p95(xs):return sorted(xs)[max(0,__import__('math').ceil(len(xs)*.95)-1)]
stop=False;samples=[]
async def writer():
    def tick():
        start=time.perf_counter();c=sqlite3.connect(ROOT/'data/luffy.db',timeout=.25)
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES('profile_tick',?)",(str(time.time()),));c.commit();write=(time.perf_counter()-start)*1000
        c.execute("SELECT * FROM trades WHERE status='open' LIMIT 100").fetchall();c.close()
        return {'write_ms':write,'loop_ms':(time.perf_counter()-start)*1000,'time':time.time()}
    while not stop:
        try:samples.append(await asyncio.to_thread(tick))
        except Exception as e:samples.append({'error':str(e),'time':time.time()})
        await asyncio.sleep(.1)
async def main():
    global stop
    task=asyncio.create_task(writer());result={'conditions':'Original HEAD; isolated synthetic fixture; 80ms simulated network; fresh server adapters then 30 sequential warm samples; HTTP transport through loopback, warm OS page cache','pid':pid}
    result['closed_start']=proc();t=time.time();await asyncio.sleep(3);result['closed_end']=proc();result['closed_window']=[t,time.time()]
    async with httpx.AsyncClient(headers=HEADERS,timeout=15) as c:
        await c.post(URL+'/__profile__/model',json={'delay':.08})
        result['open_start']=proc();t=time.time();rows=[]
        for i in range(31):
            s=time.perf_counter();r=await c.get(URL+'/api/summary');rows.append({'kind':'cold' if i==0 else 'warm','wall_ms':(time.perf_counter()-s)*1000,'bytes':len(r.content),'status':r.status_code})
        result['summary']=rows;result['summary_warm_p95_ms']=p95([r['wall_ms'] for r in rows[1:]])
        lists=[]
        for _ in range(30):
            s=time.perf_counter();r=await c.post(URL+'/graphql',json={'query':'{ trades(limit:25){id symbol status realized_pnl} }'});lists.append({'wall_ms':(time.perf_counter()-s)*1000,'bytes':len(r.content),'status':r.status_code,'graphql_errors':r.json().get('errors')})
        result['trade_list']=lists;result['list_p95_ms']=p95([r['wall_ms'] for r in lists]);result['open_end']=proc();result['open_window']=[t,time.time()]
    stop=True;await task
    for name in ('closed','open'):
        a,b=result[name+'_window'];ss=[s for s in samples if a<=s['time']<=b]
        result[name+'_core_probe']={'n':len(ss),'errors':[s for s in ss if 'error' in s], 'write_p95_ms':p95([s['write_ms'] for s in ss if 'write_ms' in s]),'loop_p95_ms':p95([s['loop_ms'] for s in ss if 'loop_ms' in s])}
    c=sqlite3.connect(ROOT/'data/luffy.db');result['passive_checkpoint']=c.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchall();c.close()
    result['core_probe_samples']=samples;(ROOT/'api-results.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('summary','trade_list','core_probe_samples')}))
asyncio.run(main())
