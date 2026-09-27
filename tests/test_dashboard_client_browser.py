"""Browser regressions for the dashboard client contract (headless Chromium).

Loopback server over a synthetic store; the venue is mocked and every
external browser request is stubbed or aborted. Skips if Chromium is absent.
"""
import asyncio
import json
import socket
import threading
import time

import pytest

from tests.test_attention_view import server  # noqa: F401  (fixture)
from tests.test_dashboard_performance import _mock_venue, _seed

playwright = pytest.importorskip('playwright.async_api')
H = {'x-luffy-token': 'fixture-token'}
CHART = ("window.LightweightCharts={createChart:()=>({addAreaSeries:()=>({setData:()=>{}}),"
         "applyOptions:()=>{},timeScale:()=>({fitContent:()=>{}})})};")
INIT = """window.__hidden=false;Object.defineProperty(document,'hidden',{get:()=>window.__hidden});
Object.defineProperty(document,'visibilityState',{get:()=>window.__hidden?'hidden':'visible'});"""


@pytest.fixture
def live(server, tmp_path, monkeypatch):
    import uvicorn
    import shutil
    from pathlib import Path
    _seed(tmp_path)
    shutil.copy(Path(__file__).parents[1] / 'org.yaml', tmp_path / 'org.yaml')   # company roster
    _mock_venue(monkeypatch)
    app = server.create_app({'attention': {'enabled': True}})
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(.05)
    yield f'http://127.0.0.1:{port}'
    srv.should_exit = True
    t.join(5)


def run(url, scenario, lib_delay=0):
    async def main():
        async with playwright.async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True)
            except Exception as e:                        # pragma: no cover
                pytest.skip(f'chromium unavailable: {e}')
            ctx = await browser.new_context(extra_http_headers=H)
            await ctx.add_init_script(INIT)
            state = {'net': [], 'hooks': []}

            async def route(r):
                u = r.request.url
                if u.startswith(url):
                    state['net'].append((time.monotonic(), u[len(url):]))
                    for hook in state['hooks']:
                        if await hook(r):
                            return
                    return await r.continue_()
                if 'lightweight-charts' in u:
                    if lib_delay:
                        await asyncio.sleep(lib_delay)
                    return await r.fulfill(content_type='application/javascript', body=CHART)
                if 'motion' in u or 'vis-network' in u:
                    return await r.fulfill(content_type='application/javascript', body='')
                return await r.abort()
            await ctx.route('**/*', route)
            page = await ctx.new_page()
            await page.goto(url, wait_until='commit')
            await page.wait_for_function("document.querySelector('#equity')?.textContent==='$1,000'",
                                         timeout=15000)
            try:
                return await scenario(page, state)
            finally:
                await browser.close()
    return asyncio.run(main())


def since(state, t, part=''):
    return [u for (ts, u) in state['net'] if ts >= t and part in u]


def test_session_expiry_conceals_data_and_stops_everything(live):
    async def scenario(page, state):
        held, release, expired = asyncio.Event(), asyncio.Event(), {'on': False}

        async def expire(r):
            u = r.request.url
            if '/api/overview' in u and not held.is_set():
                held.set()
                await release.wait()           # genuinely pending across expiry
                try:
                    await r.fulfill(status=200, content_type='application/json',
                                    body=json.dumps({'generated_at': '2999-01-01T00:00:00+00:00',
                                                     'equity': {'equity': 7777}}))
                except Exception:
                    pass                       # the client already aborted it
                return True
            if expired['on'] and ('/api/' in u or '/graphql' in u):
                await r.fulfill(status=401, content_type='application/json', body='{}')
                return True
            return False
        state['hooks'].append(expire)
        await page.evaluate('void refresh()')          # not awaited
        await asyncio.wait_for(held.wait(), 5)         # the refresh is now held
        expired['on'] = True
        await page.evaluate("fetch('/api/doctrine').catch(()=>{})")    # 401 ends it
        await asyncio.sleep(.3)
        release.set()                                  # late success arrives
        await asyncio.sleep(.5)
        t = time.monotonic()
        await asyncio.sleep(12)
        body = await page.inner_text('body')
        return {'body': body, 'after': since(state, t),
                'ws': await page.evaluate('ws===null'), 'lost': await page.evaluate('luffySession.lost')}
    r = run(live, scenario)
    assert r['lost'] and r['ws']
    assert 'Session expired' in r['body'] and '$1,000' not in r['body'] and '7777' not in r['body']
    assert r['after'] == []                       # no poll, panel or socket traffic


def test_enrichment_ordering_failure_and_source_age(live):
    async def scenario(page, state):
        await page.wait_for_function("document.querySelector('#assets').textContent.includes('wallet')",
                                     timeout=10000)
        mode = {'m': None}

        async def enrich(r):
            if '/api/enrichment' not in r.request.url or not mode['m']:
                return False
            if mode['m'] == 'fail':
                await r.abort(); return True
            resp = await r.fetch(); body = await resp.json()
            if mode['m'] == 'older':
                body['account']['retrieved_at'] = '2000-01-01T00:00:00+00:00'
                body['account']['value']['assets_usd_total'] = 1
            if mode['m'] == 'oldtick':
                body['tickers']['value']['prices']['SOL/USDT']['source_time'] = '2000-01-01T00:00:00+00:00'
                body['tickers']['retrieved_at'] = '2999-01-01T00:00:00+00:00'
            await r.fulfill(response=resp, json=body); return True
        state['hooks'].append(enrich)
        out = {}
        for m in ('older', 'fail', 'oldtick'):
            mode['m'] = m
            await page.evaluate('enRes.now()'); await asyncio.sleep(.8)
            out[m] = (await page.inner_text('#assets'), await page.inner_text('#tickstrip'))
        return out
    r = run(live, scenario)
    assert 'wallet ≈ $1 ' not in r['older'][0] + ' '        # older version rejected
    assert 'last refresh failed' in r['fail'][0] and 'STALE' in r['fail'][0]
    assert 'STALE' in r['oldtick'][1]                         # old ticker observation


def test_pipeline_truncation_is_disclosed(live):
    async def scenario(page, state):
        async def pipe(r):
            if '/api/pipeline' not in r.request.url or '/book' in r.request.url:
                return False
            book = [{'id': f's{i}', 'name': f's{i}', 'kind': 'k', 'state': 'paper',
                     'origin': 'brain', 'retire_reason': ''} for i in range(500)]
            await r.fulfill(json={'funnel': {}, 'verdicts': [], 'tv_health': {},
                                  'population': {'by_state': {'paper': 600}, 'harvested': [],
                                                 'book': book, 'book_truncated': True,
                                                 'book_total': 600,
                                                 'book_rest': '/api/pipeline/book?offset=500'}})
            return True
        state['hooks'].append(pipe)
        await page.evaluate("show('os')")
        await page.evaluate("osTab('disc',document.querySelector('#v-os .subtabs .tab'))")
        await asyncio.sleep(1)
        return await page.inner_text('#disc-book-pager')
    assert 'listed 500 of 600' in run(live, scenario)


def test_org_is_one_request_across_consumers(live):
    async def scenario(page, state):
        async def slow_org(r):
            if '/api/org' in r.request.url:
                await asyncio.sleep(1)
            return False
        state['hooks'].append(slow_org)
        t = time.monotonic()
        await page.evaluate("show('company');show('os');companyRes.now();deckRes.now();loadCompany(true);loadDeck(true)")
        await asyncio.sleep(1.5)
        # all consumers share the one in-flight request: nothing else starts
        # while it is held (1 s). A coalesced follow-up after it completes is
        # allowed; a simultaneous one is not.
        starts = [ts for ts, u in state['net'] if ts >= t and '/api/org' in u]
        return [x for x in starts if x - starts[0] < .9]
    assert len(run(live, scenario)) == 1


def test_no_reads_while_hidden_even_after_a_library_await(live):
    async def scenario(page, state):
        await page.evaluate("window.__hidden=true;document.dispatchEvent(new Event('visibilitychange'))")
        t = time.monotonic()
        # a fresh (2 s) chart-library load, then its continuation's GraphQL read
        await page.evaluate("delete window.LightweightCharts;delete _libs.lwc;eqChart=null;"
                            "loadEqCurve().catch(()=>{})")
        await asyncio.sleep(4)
        return since(state, t)
    assert run(live, scenario, lib_delay=2) == []


def test_lower_generation_and_missing_ticker_time(live):
    async def scenario(page, state):
        await page.wait_for_function("document.querySelector('#assets').textContent.includes('wallet')",
                                     timeout=10000)
        mode = {'m': None}

        async def enrich(r):
            if '/api/enrichment' not in r.request.url or not mode['m']:
                return False
            resp = await r.fetch(); body = await resp.json()
            if mode['m'] == 'gen2':
                body['account']['generation'] = 2
                body['account']['value']['margin_equity'] = 2000
                body['account']['value']['assets_usd_total'] = 2000
            if mode['m'] == 'gen1':
                body['account']['generation'] = 1
                body['account']['retrieved_at'] = '2999-01-01T00:00:00+00:00'
                body['account']['value']['assets_usd_total'] = 1
                body['tickers']['value']['prices']['SOL/USDT']['source_time'] = None
                body['tickers']['retrieved_at'] = '2999-01-01T00:00:00+00:00'
            await r.fulfill(response=resp, json=body); return True
        state['hooks'].append(enrich)
        out = {}
        for m in ('gen2', 'gen1'):
            mode['m'] = m
            await page.evaluate('enRes.now()'); await asyncio.sleep(.8)
            out[m] = (await page.inner_text('#assets'), await page.inner_text('#tickstrip'))
        return out
    r = run(live, scenario)
    assert '2,000' in r['gen2'][0]
    assert '2,000' in r['gen1'][0]               # generation 1 cannot overwrite 2
    assert 'prices fresh' not in r['gen1'][1] and 'STALE' in r['gen1'][1]


def test_cached_overview_snapshot_is_aged_from_serve_time(live):
    async def scenario(page, state):
        async def stale(r):
            if '/api/overview' not in r.request.url:
                return False
            resp = await r.fetch(); body = await resp.json()
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            old = (now - timedelta(hours=1)).isoformat()
            body['generated_at'] = '2999-01-01T00:00:00+00:00'   # pass the order check
            body['equity']['observed_at'] = old
            body['cache'] = {'age_s': 3600, 'stale': True, 'reason': 'timeout',
                             'served_at': now.isoformat()}
            await r.fulfill(response=resp, json=body); return True
        state['hooks'].append(stale)
        await page.evaluate('refresh()'); await asyncio.sleep(.5)
        return await page.inner_text('#eq-src'), await page.inner_text('#datastat')
    eq, ds = run(live, scenario)
    assert 'recorded 60m ago' in eq and 'STALE' in eq
    assert 'server busy' in ds


def test_changed_book_refuses_the_completeness_claim(live):
    async def scenario(page, state):
        async def pipe(r):
            u = r.request.url
            if '/api/pipeline/book' in u:
                await r.fulfill(status=409, json={'status': 'changed', 'version': 'v2', 'total': 601})
                return True
            if '/api/pipeline' not in u:
                return False
            book = [{'id': f's{i}', 'name': f's{i}', 'kind': 'k', 'state': 'paper',
                     'origin': 'brain', 'retire_reason': ''} for i in range(500)]
            await r.fulfill(json={'funnel': {}, 'verdicts': [], 'tv_health': {},
                                  'population': {'by_state': {'paper': 600}, 'harvested': [],
                                                 'book': book, 'book_truncated': True,
                                                 'book_total': 600, 'book_version': 'v1',
                                                 'book_rest': '/api/pipeline/book?offset=500&version=v1'}})
            return True
        state['hooks'].append(pipe)
        await page.evaluate("show('os')")
        await page.evaluate("osTab('disc',document.querySelector('#v-os .subtabs .tab'))")
        await asyncio.sleep(.8)
        await page.evaluate("loadBookRest().catch(()=>{})"); await asyncio.sleep(.8)
        return await page.inner_text('#disc-book-pager')
    txt = run(live, scenario)
    assert 'population changed while listing' in txt and 'not complete' in txt


def test_late_page_from_a_superseded_book_is_discarded(live):
    async def scenario(page, state):
        gate = asyncio.Event()

        def book(n, version, rest):
            return {'funnel': {}, 'verdicts': [], 'tv_health': {},
                    'population': {'by_state': {'paper': n}, 'harvested': [],
                                   'book': [{'id': f'{version}{i}', 'name': f'{version}{i}', 'kind': 'k',
                                             'state': 'paper', 'origin': 'brain', 'retire_reason': ''}
                                            for i in range(500)],
                                   'book_truncated': True, 'book_total': n, 'book_version': version,
                                   'book_rest': rest}}
        calls = {'pipe': 0}

        async def pipe(r):
            u = r.request.url
            if '/api/pipeline/book' in u:
                async def late():             # version A's page arrives late
                    await gate.wait()
                    await r.fulfill(json={'offset': 500, 'total': 501, 'version': 'A', 'more': False,
                                          'rows': [{'id': 'A500', 'name': 'A500', 'kind': 'k',
                                                    'state': 'paper', 'origin': 'brain',
                                                    'retire_reason': ''}]})
                # route handlers run one at a time: fulfil from a task so the
                # later /api/pipeline request is not blocked behind this one
                state.setdefault('tasks', []).append(asyncio.create_task(late()))
                return True
            if '/api/pipeline' not in u:
                return False
            calls['pipe'] += 1
            v = 'A' if calls['pipe'] == 1 else 'B'
            await r.fulfill(json=book(502 if v == 'B' else 501, v,
                                      f'/api/pipeline/book?offset=500&version={v}'))
            return True
        state['hooks'].append(pipe)
        await page.evaluate("show('os')")
        await page.evaluate("osTab('disc',document.querySelector('#v-os .subtabs .tab'))")
        await asyncio.sleep(.6)
        await page.evaluate("window.__p1=loadBookRest().then(()=>'ok').catch(e=>'err:'+e);0")   # pending, not awaited
        await asyncio.sleep(.3)
        await page.evaluate("loadDiscovery()")                           # now version B
        await asyncio.sleep(.5)
        gate.set()
        assert await asyncio.wait_for(page.evaluate('window.__p1'), 10) == 'ok'   # A's page completed
        await asyncio.sleep(.2)
        return await page.inner_text('#disc-book-pager'), await page.evaluate('(window.__bookRest||[]).length')
    txt, rest = run(live, scenario)
    assert rest == 0                                  # A's page never joined B's list
    assert 'listed 500 of 502' in txt


def test_failed_panel_refresh_is_labelled_stale(live):
    async def scenario(page, state):
        fail = {'on': False}

        async def org(r):
            if '/api/org' in r.request.url and fail['on']:
                await r.fulfill(status=500, json={}); return True
            return False
        state['hooks'].append(org)
        await page.evaluate("show('company')")
        await page.wait_for_function("document.querySelector('#company-graph').textContent.length>0 || window.vis===undefined", timeout=5000)
        await asyncio.sleep(1)
        fail['on'] = True
        await page.evaluate("loadCompany(true)"); await page.evaluate("loadDeck(true)")
        await asyncio.sleep(.3)
        notes = await page.evaluate("[...document.querySelectorAll('.cache-note')].map(n=>n.textContent)")
        return notes
    notes = run(live, scenario)
    assert notes and all('refresh failed' in n for n in notes)
