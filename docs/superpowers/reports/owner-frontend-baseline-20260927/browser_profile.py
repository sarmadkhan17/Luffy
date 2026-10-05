import asyncio, json, os, platform, sqlite3, statistics, time
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
ROOT=Path('/tmp/owner-frontend-v1'); URL='http://127.0.0.1:18786'; HEADERS={'x-luffy-token':'isolated-profile-token'}
INIT="""window.__profile={longTasks:[],requests:[],intervals:[],active:0,maxActive:0};
new PerformanceObserver(l=>l.getEntries().forEach(e=>__profile.longTasks.push({start:e.startTime,ms:e.duration}))).observe({type:'longtask',buffered:true});
const orig=window.fetch;window.fetch=async function(...args){const t=performance.now();const url=String(args[0]);__profile.active++;__profile.maxActive=Math.max(__profile.active,__profile.maxActive);try{const r=await orig(...args);__profile.requests.push({url,start:t,ms:performance.now()-t,status:r.status});return r;}catch(e){__profile.requests.push({url,start:t,ms:performance.now()-t,error:String(e)});throw e;}finally{__profile.active--;}};
const si=window.setInterval;window.setInterval=(fn,ms,...a)=>{__profile.intervals.push(ms);return si(fn,ms,...a)};
"""
CHART="""window.LightweightCharts={createChart:()=>({addAreaSeries:()=>({setData:()=>{}}),addCandlestickSeries:()=>({setData:()=>{}}),addHistogramSeries:()=>({setData:()=>{}}),applyOptions:()=>{},timeScale:()=>({fitContent:()=>{}}),remove:()=>{}})};"""
results={'machine':{'platform':platform.platform(),'cpus':os.cpu_count(),'ram_bytes':os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')},'conditions':{'external_network':'All browser external requests fulfilled locally or aborted; server DNS/socket guards refuse external I/O','cdn':'Lightweight Charts is a local no-op stub; Motion and vis-network are empty; no actual third-party JS parsing/render/GPU cost measured','dataset':'existing isolated synthetic fixture, 100k votes, 100k decisions, 10k closed trades, one equity point, no open positions','core_probe':'100ms cadence synthetic write/read loop; NOT the production trading core'},'scenarios':[]}
stop=False; samples=[]
async def core_probe():
    def tick():
        t=time.perf_counter(); c=sqlite3.connect(ROOT/'data/luffy.db',timeout=.25)
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES('profile_tick',?)",(str(time.time()),)); c.commit()
        rows=c.execute("SELECT * FROM trades WHERE status='open' LIMIT 100").fetchall(); c.close()
        return (time.perf_counter()-t)*1000
    while not stop:
        start=time.perf_counter()
        try:ms=await asyncio.to_thread(tick);samples.append({'time':time.time(),'ms':ms})
        except Exception as e:samples.append({'time':time.time(),'error':str(e)})
        await asyncio.sleep(max(0,.1-(time.perf_counter()-start)))
async def run():
    global stop
    probe=asyncio.create_task(core_probe());t=time.time();await asyncio.sleep(2);results['closed_window']=[t,time.time()]
    async with httpx.AsyncClient(headers=HEADERS,timeout=180) as http, async_playwright() as p:
        browser=await p.chromium.launch(headless=True);results['browser']=browser.version
        for name,delay,cdn_delay in [('cold_offline_dependencies',.08,0),('warm_browser',None,0),('delayed_parser_script',.08,8),('slow_serial_network',5.05,0)]:
            if delay is not None:await http.post(URL+'/__profile__/model',json={'delay':delay})
            if name!='warm_browser':
                context=await browser.new_context(viewport={'width':1440,'height':1000},extra_http_headers=HEADERS)
                await context.add_init_script(INIT)
            page=await context.new_page();errors=[];external=[];page.on('pageerror',lambda e:errors.append(str(e)))
            async def route(r):
                url=r.request.url
                if url.startswith(URL):return await r.continue_()
                external.append(url)
                if 'lightweight-charts' in url:return await r.fulfill(content_type='application/javascript',body=CHART)
                if 'motion' in url:
                    if cdn_delay:await asyncio.sleep(cdn_delay)
                    return await r.fulfill(content_type='application/javascript',body='/* offline profile: animation dependency absent */')
                if 'vis-network' in url:return await r.fulfill(content_type='application/javascript',body='/* offline profile: graph dependency absent */')
                return await r.abort()
            await page.route('**/*',route)
            cdp=await context.new_cdp_session(page);await cdp.send('Performance.enable')
            start=time.time(); await page.goto(URL,wait_until='commit',timeout=120000)
            await page.wait_for_function("typeof show==='function'",timeout=120000)
            await page.locator('[data-v="luffy"]').click(no_wait_after=True)
            await page.locator('#chat-in').fill('isolated responsiveness probe — not sent')
            nav_ms=await page.evaluate('performance.now()')
            await page.locator('[data-v="overview"]').click(no_wait_after=True)
            await page.wait_for_function("document.querySelector('#equity').textContent==='$1,000'",timeout=120000)
            useful_ms=await page.evaluate('performance.now()')
            if name!='slow_serial_network':await asyncio.sleep(.3)
            await page.evaluate("document.body.insertAdjacentHTML('afterbegin','<div style=\"position:fixed;top:0;right:0;z-index:99999;background:#6b1834;color:white;padding:8px\">ISOLATED SYNTHETIC BASELINE • NETWORK SIMULATED</div>')")
            await page.screenshot(path=str(ROOT/(name+'.png')))
            metrics=await cdp.send('Performance.getMetrics')
            row={'name':name,'window':[start,time.time()],'navigation_and_composer_ms':nav_ms,'first_journal_equity_ms':useful_ms,'errors':errors,'external_intercepted':external,'browser':await page.evaluate("({profile:__profile,navigation:performance.getEntriesByType('navigation')[0].toJSON(),resources:performance.getEntriesByType('resource').map(x=>({name:x.name,start:x.startTime,ms:x.duration,transfer:x.transferSize,decoded:x.decodedBodySize})),memory:performance.memory?performance.memory.usedJSHeapSize:null})"),'cdp':{x['name']:x['value'] for x in metrics['metrics'] if x['name'] in ('TaskDuration','ScriptDuration','LayoutDuration','RecalcStyleDuration','JSHeapUsedSize','Nodes','LayoutCount')}}
            results['scenarios'].append(row);(ROOT/'browser-results.json').write_text(json.dumps(results,indent=2));print(name,round(nav_ms,1),round(useful_ms,1),errors,flush=True)
            await page.close()
            if name!='cold_offline_dependencies':await context.close()
        await browser.close()
    stop=True;await probe;results['core_probe_samples']=samples
    (ROOT/'browser-results.json').write_text(json.dumps(results,indent=2))
asyncio.run(run())
