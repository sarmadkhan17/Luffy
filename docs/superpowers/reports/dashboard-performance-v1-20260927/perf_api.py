"""API latency, event-loop responsiveness and synthetic write-latency probe.
Usage: PERF_ROOT=... perf_api.py MODE PORT PID OUT.json. Not the trading core."""
import asyncio, json, math, os, sqlite3, sys, time
from pathlib import Path
import httpx
MODE, PORT, PID, OUT = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])
ROOT = Path(os.environ['PERF_ROOT']); URL = f'http://127.0.0.1:{PORT}'; H = {'x-luffy-token': 'isolated-profile-token'}
def proc():
    s = Path(f'/proc/{PID}/stat').read_text().split()
    return {'cpu_s': (int(s[13]) + int(s[14])) / os.sysconf('SC_CLK_TCK'), 'rss_mib': int(s[23]) * os.sysconf('SC_PAGE_SIZE') / 2**20}
def p95(xs): xs = sorted(xs); return round(xs[max(0, math.ceil(len(xs) * .95) - 1)], 2) if xs else None
stop = False; samples = []
async def writer():
    def tick():
        s = time.perf_counter(); c = sqlite3.connect(ROOT / 'data/luffy.db', timeout=.25)
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES('profile_tick',?)", (str(time.time()),)); c.commit(); w = (time.perf_counter() - s) * 1000
        c.execute("SELECT * FROM trades WHERE status='open' LIMIT 100").fetchall(); c.close()
        return {'write_ms': w, 'loop_ms': (time.perf_counter() - s) * 1000, 'time': time.time()}
    while not stop:
        try: samples.append(await asyncio.to_thread(tick))
        except Exception as e: samples.append({'error': str(e), 'time': time.time()})
        await asyncio.sleep(.1)
def window(a, b):
    ss = [s for s in samples if a <= s['time'] <= b]
    return {'n': len(ss), 'lock_errors': sum('error' in s for s in ss), 'write_p95_ms': p95([s['write_ms'] for s in ss if 'write_ms' in s]), 'loop_p95_ms': p95([s['loop_ms'] for s in ss if 'loop_ms' in s])}
async def timed(c, method, path, **kw):
    s = time.perf_counter(); r = await c.request(method, URL + path, **kw); return (time.perf_counter() - s) * 1000, r
async def main():
    global stop
    res = {'mode': MODE, 'conditions': 'isolated synthetic fixture; simulated network model; loopback; warm OS cache; NOT production'}
    task = asyncio.create_task(writer())
    async with httpx.AsyncClient(headers=H, timeout=120) as c:
        # 1. idle window
        a = proc(); t = time.time(); await asyncio.sleep(5); res['idle'] = {'server': [a, proc()], 'core_probe': window(t, time.time())}
        # 2. endpoint cold + warm p95 at 80 ms simulated network
        await c.post(URL + '/__profile__/model', json={'delay': .08})
        eps = [('GET', '/api/summary', {}), ('GET', '/api/org', {}), ('GET', '/api/pipeline', {}), ('GET', '/api/vault/tree', {}), ('GET', '/api/logs?lines=90', {}), ('GET', '/api/review_status', {}), ('POST', '/graphql', {'json': {'query': '{ trades(limit:25){id symbol status realized_pnl} }'}})]
        if MODE == 'after':   # v1 /api/summary is retired (410); v2 is the comparable route
            eps = [('GET', '/api/overview', {}), ('GET', '/api/enrichment', {}), ('GET', '/api/v2/summary', {})] + [e for e in eps if e[1] != '/api/summary']
        res['endpoints'] = {}
        for m, pth, kw in eps:
            rows = []
            for i in range(31):
                ms, r = await timed(c, m, pth, **kw); rows.append({'ms': ms, 'status': r.status_code, 'bytes': len(r.content)})
            res['endpoints'][pth] = {'cold_ms': round(rows[0]['ms'], 2), 'warm_p95_ms': p95([x['ms'] for x in rows[1:]]), 'statuses': sorted({x['status'] for x in rows}), 'bytes': rows[-1]['bytes']}
        # 3. responsiveness of '/' and a local list while a 5.05 s-per-call stall is in progress
        await c.post(URL + '/__profile__/model', json={'delay': 5.05})
        slow_path = '/api/enrichment' if MODE == 'after' else '/api/summary'
        pending = asyncio.create_task(timed(c, 'GET', slow_path)); await asyncio.sleep(.5)
        nav = [(await timed(c, 'GET', '/'))[0] for _ in range(5)]
        lst = [(await timed(c, 'POST', '/graphql', json={'query': '{ trades(limit:25){id} }'}))[0] for _ in range(5)]
        loc = [(await timed(c, 'GET', '/api/overview' if MODE == 'after' else '/api/review_status'))[0] for _ in range(5)]
        slow_ms, _ = await pending
        res['during_stall'] = {'stalled_request': slow_path, 'stalled_request_ms': round(slow_ms, 1), 'index_p95_ms': p95(nav), 'graphql_list_p95_ms': p95(lst), 'local_endpoint': '/api/overview' if MODE == 'after' else '/api/review_status', 'local_p95_ms': p95(loc), 'server_model': (await c.get(URL + '/__profile__/resource')).json()}
        if MODE == 'before':
            await asyncio.sleep(1)
        # 4. refresh load: 4 clients each polling the Overview's resources every 1 s for 10 s
        await c.post(URL + '/__profile__/model', json={'delay': .08})
        ov = ['/api/overview', '/api/enrichment'] if MODE == 'after' else ['/api/summary']
        paths = ov + ['/api/org', '/api/review_status', '/api/doctrine']
        async def client_loop():
            end = time.time() + 10; lat = []
            while time.time() < end:
                s = time.perf_counter(); await asyncio.gather(*[c.get(URL + p) for p in paths]); lat.append((time.perf_counter() - s) * 1000)
                await asyncio.sleep(max(0, 1 - (time.perf_counter() - s)))
            return lat
        a = proc(); t = time.time(); lats = await asyncio.gather(*[client_loop() for _ in range(4)])
        res['refresh_load'] = {'clients': 4, 'paths': paths, 'round_p95_ms': p95([x for l in lats for x in l]), 'server': [a, proc()], 'core_probe': window(t, time.time())}
        res['server_resource_end'] = (await c.get(URL + '/__profile__/resource')).json()
    stop = True; await task
    c2 = sqlite3.connect(ROOT / 'data/luffy.db'); res['passive_checkpoint'] = c2.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchall(); c2.close()
    OUT.write_text(json.dumps(res, indent=2)); print(json.dumps(res, indent=1))
asyncio.run(main())
