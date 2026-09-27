"""Before/after browser scenarios against perf_server.py on loopback.
Usage: PERF_ROOT=... perf_browser.py MODE PORT OUT.json scenarios|behaviours
Run each part against a FRESH server: the original server keeps executing
queued slow summaries after the slow scenario's page closes.
All browser external requests are fulfilled locally or aborted."""
import asyncio, json, os, sys, time
from pathlib import Path
import httpx
from playwright.async_api import async_playwright
MODE, PORT, OUT, PART = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
URL = f'http://127.0.0.1:{PORT}'; HEADERS = {'x-luffy-token': 'isolated-profile-token'}
INIT = """window.__p={req:[],active:0,maxActive:0,ws:0,marker:Math.random()};
const of=window.fetch;window.fetch=async function(...a){const t=performance.now(),u=String(a[0]);__p.active++;__p.maxActive=Math.max(__p.active,__p.maxActive);
 try{const r=await of(...a);__p.req.push({u,t,ms:performance.now()-t,s:r.status});return r;}catch(e){__p.req.push({u,t,ms:performance.now()-t,e:String(e)});throw e;}finally{__p.active--;}};
const OW=window.WebSocket;window.WebSocket=function(...a){__p.ws++;return new OW(...a);};window.WebSocket.prototype=OW.prototype;
Object.assign(window.WebSocket,{CONNECTING:0,OPEN:1,CLOSING:2,CLOSED:3});
window.__hidden=false;Object.defineProperty(document,'hidden',{get:()=>window.__hidden});
Object.defineProperty(document,'visibilityState',{get:()=>window.__hidden?'hidden':'visible'});"""
CHART = "window.LightweightCharts={createChart:()=>({addAreaSeries:()=>({setData:()=>{}}),addCandlestickSeries:()=>({setData:()=>{}}),applyOptions:()=>{},timeScale:()=>({fitContent:()=>{}}),remove:()=>{}})};"
USEFUL = "document.querySelector('#equity').textContent==='$1,000'"
results = {'mode': MODE, 'scenarios': [], 'behaviours': []}

async def new_page(browser, cdn_delay=0, context=None, extra_route=None):
    context = context or await browser.new_context(viewport={'width': 1440, 'height': 1000}, extra_http_headers=HEADERS)
    if not getattr(context, '_perf_init', False):
        await context.add_init_script(INIT); context._perf_init = True
    page = await context.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
    async def route(r):
        url = r.request.url
        if url.startswith(URL):
            if extra_route and await extra_route(r): return
            return await r.continue_()
        if 'lightweight-charts' in url: return await r.fulfill(content_type='application/javascript', body=CHART)
        if 'motion' in url:
            if cdn_delay: await asyncio.sleep(cdn_delay)
            return await r.fulfill(content_type='application/javascript', body='/* offline */')
        if 'vis-network' in url: return await r.fulfill(content_type='application/javascript', body='/* offline */')
        return await r.abort()
    if not getattr(context, '_perf_routed', False):
        # context-wide, so every page (incl. the warm scenario's) is guarded
        await context.route('**/*', route); context._perf_routed = True
    return context, page, errors

async def ready(page):
    await page.goto(URL, wait_until='commit', timeout=120000)
    await page.wait_for_function("typeof show==='function'", timeout=120000)
    await page.locator('[data-v="luffy"]').click(no_wait_after=True)
    await page.locator('#chat-in').fill('isolated responsiveness probe — not sent')
    nav = await page.evaluate('performance.now()')
    await page.locator('[data-v="overview"]').click(no_wait_after=True)
    await page.wait_for_function(USEFUL, timeout=120000)
    return nav, await page.evaluate('performance.now()')

def count(reqs, part, since=0):
    return sum(1 for r in reqs if part in r['u'] and r['t'] >= since)

async def run():
    async with httpx.AsyncClient(headers=HEADERS, timeout=180) as http, async_playwright() as p:
        browser = await p.chromium.launch(headless=True); results['browser'] = browser.version
        ctx = None
        for name, delay, cdn in [] if PART != 'scenarios' else [('cold_offline_dependencies', .08, 0), ('warm_browser', None, 0), ('delayed_parser_script', .08, 8), ('slow_serial_network', 5.05, 0)]:
            if delay is not None: await http.post(URL + '/__profile__/model', json={'delay': delay})
            if name == 'warm_browser':
                ctx, page, errors = await new_page(browser, 0, context=ctx)
            else:
                ctx, page, errors = await new_page(browser, cdn)
            nav, useful = await ready(page)
            extra = {}
            if name == 'slow_serial_network':
                # typing/navigation while the venue stall continues
                t = time.perf_counter(); await page.locator('[data-v="trades"]').click(no_wait_after=True); await page.locator('[data-v="luffy"]').click(no_wait_after=True)
                await page.locator('#chat-in').fill('typing during stall'); await page.locator('[data-v="overview"]').click(no_wait_after=True)
                extra['nav_during_stall_ms'] = (time.perf_counter() - t) * 1000
                if MODE == 'after':
                    try:
                        await page.wait_for_function("document.querySelector('#assets').textContent.includes('wallet ≈')", timeout=90000)
                        extra['wallet_enrichment_ms'] = await page.evaluate('performance.now()')
                    except Exception as e: extra['wallet_enrichment_ms'] = None
            prof = await page.evaluate('window.__p?{maxActive:__p.maxActive,n:__p.req.length}:null')
            try: model = (await http.get(URL + '/__profile__/resource', timeout=5)).json()
            except httpx.TimeoutException: model = 'server_unresponsive_within_5s'
            row = {'name': name, 'navigation_and_composer_ms': nav, 'first_useful_local_data_ms': useful, 'errors': errors, 'fetch': prof, 'server': model, **extra}
            results['scenarios'].append(row); print(json.dumps(row), flush=True)
            await page.close()
            if name != 'cold_offline_dependencies': await ctx.close()
        if PART != 'behaviours':
            await browser.close(); OUT.write_text(json.dumps(results, indent=2)); return
        await http.post(URL + '/__profile__/model', json={'delay': .08})
        # hidden tab: requests while hidden, then on return
        ctx, page, errors = await new_page(browser); await ready(page); await asyncio.sleep(1)
        t0 = await page.evaluate("(window.__hidden=true,document.dispatchEvent(new Event('visibilitychange')),performance.now())")
        await asyncio.sleep(20)
        # boundary taken BEFORE the visibility event: requests it triggers count as "after return"
        t1 = await page.evaluate("(()=>{const t=performance.now();window.__hidden=false;document.dispatchEvent(new Event('visibilitychange'));return t;})()")
        await asyncio.sleep(3)
        reqs = await page.evaluate('__p.req')
        hidden = [r for r in reqs if t0 <= r['t'] < t1]; back = [r for r in reqs if r['t'] >= t1]
        b = {'name': 'hidden_tab_20s', 'requests_while_hidden': len(hidden), 'hidden_requests': [{'u': r['u'].split('?')[0], 'start_after_hide_ms': round(r['t'] - t0), 'ms': round(r['ms'])} for r in hidden], 'requests_first_3s_after_return': len(back), 'return_urls': sorted(r['u'].split('?')[0] for r in back), 'errors': errors}
        results['behaviours'].append(b); print(json.dumps(b), flush=True)
        # websocket drop: no reload, one replacement connection
        marker = await page.evaluate('__p.marker'); ws0 = await page.evaluate('__p.ws')
        await page.evaluate('ws.close()'); await asyncio.sleep(6)
        b = {'name': 'websocket_drop', 'same_document': marker == await page.evaluate('__p.marker'), 'ws_constructed_after_drop': await page.evaluate('__p.ws') - ws0, 'ws_status': await page.inner_text('#sb-ws'), 'errors': errors}
        results['behaviours'].append(b); print(json.dumps(b), flush=True)
        await ctx.close()
        if MODE == 'after':
            # out-of-order: a later response carrying OLDER data must not win
            state = {'n': 0}
            async def ooo(r):
                if '/api/overview' not in r.request.url: return False
                state['n'] += 1
                resp = await r.fetch(); body = await resp.json()
                if state['n'] == 2:
                    body['generated_at'] = '2000-01-01T00:00:00+00:00'; body['equity']['equity'] = 5
                await r.fulfill(response=resp, json=body); return True
            ctx, page, errors = await new_page(browser, extra_route=ooo); await ready(page)
            await page.evaluate('refresh()'); await asyncio.sleep(1.5)
            b = {'name': 'out_of_order_overview', 'overview_requests': state['n'], 'equity_text': await page.inner_text('#equity'), 'errors': errors}
            results['behaviours'].append(b); print(json.dumps(b), flush=True); await ctx.close()
            # overlapping manual/timer refreshes coalesce to one in flight
            ctx, page, errors = await new_page(browser); await ready(page)
            await http.post(URL + '/__profile__/model', json={'delay': .08})
            t = await page.evaluate('performance.now()')
            await page.evaluate('for(let i=0;i<10;i++)refresh()'); await asyncio.sleep(2)
            reqs = await page.evaluate('__p.req')
            b = {'name': 'ten_manual_refreshes', 'overview_requests': count(reqs, '/api/overview', t), 'max_concurrent_fetches': await page.evaluate('__p.maxActive'), 'errors': errors}
            results['behaviours'].append(b); print(json.dumps(b), flush=True); await ctx.close()
            # enrichment failure keeps local data
            async def fail_enrich(r):
                if '/api/enrichment' in r.request.url: await r.abort(); return True
                return False
            ctx, page, errors = await new_page(browser, extra_route=fail_enrich); nav, useful = await ready(page); await asyncio.sleep(1)
            b = {'name': 'enrichment_failure', 'first_useful_local_data_ms': useful, 'equity_text': await page.inner_text('#equity'), 'assets_text': await page.inner_text('#assets'), 'errors': errors}
            results['behaviours'].append(b); print(json.dumps(b), flush=True); await ctx.close()
            # session expiry: everything stops, private data is removed
            expired = {'on': False, 'after': 0}
            async def expire(r):
                if expired['on'] and ('/api/' in r.request.url or '/graphql' in r.request.url):
                    expired['after'] += 1
                    await r.fulfill(status=401, json={'error': 'authentication required'}); return True
                return False
            ctx, page, errors = await new_page(browser, extra_route=expire); await ready(page)
            expired['on'] = True; await page.evaluate('refresh()'); await asyncio.sleep(1)
            n0 = expired['after']; await asyncio.sleep(17)
            body = await page.inner_text('body')
            b = {'name': 'session_expiry', 'body_text': body[:120], 'equity_visible': '$1,000' in body, 'requests_next_17s': expired['after'] - n0, 'ws_closed': await page.evaluate('ws===null'), 'errors': errors}
            results['behaviours'].append(b); print(json.dumps(b), flush=True); await ctx.close()
        await browser.close()
    OUT.write_text(json.dumps(results, indent=2))
asyncio.run(run())
