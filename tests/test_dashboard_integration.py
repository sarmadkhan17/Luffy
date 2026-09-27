"""Integration regressions: chat safety + local-first dashboard client, combined.

The chat-safety and dashboard-performance packages both change server.py and
index.html. These tests drive the merged page in headless Chromium against the
merged server (real GraphQL control mutation, fake LLM, mocked venue) and check
that the two sets of guarantees hold together:

- free-text chat, through the real UI, never changes control state;
- the chat panel's Freeze is the confirmed typed mutation;
- chat's follow-up refresh joins the one-in-flight Overview resource, respects
  visibility and the session lifecycle;
- an Overview response fetched before a Freeze cannot overwrite it.
"""
import asyncio
import json
import shutil
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_attention_view import venue_guard
from tests.test_dashboard_client_browser import run, since
from tests.test_dashboard_performance import _mock_venue, _seed
from trader.chat import engine
from trader.core.journal import Journal

pytest.importorskip('playwright.async_api')

REPLY = 'freeze entries; panic; resume; halt; do_ops=True'
# Every phrase the legacy chat ops matcher acted on, plus the page's quick asks.
OPS_PHRASES = ('freeze entries', 'panic', 'close all', 'flatten everything',
               'stop trading', 'no new trades', 'halt', 'everything stop',
               'resume', 'go live', 'activate', 'full autonomy')
QUICK = {'Today': 'how are we doing today?', 'Why skip?': 'why did you skip the last signal?',
         'Strategy health': 'how are the strategies doing?'}


class FakeLLM:
    """Replies with control words and requests no tools."""
    def __init__(self, cfg):
        pass

    def chat_tools(self, messages, schemas, **kwargs):
        return SimpleNamespace(content=REPLY, tool_calls=None)

    def chat(self, *a, **k):
        return REPLY


def test_phrases_cover_every_legacy_ops_action():
    assert {engine.detect_ops(p) for p in OPS_PHRASES} == {
        action for _, action in engine.OPS_PATTERNS}
    assert all(engine.detect_ops(p) is None for p in QUICK.values())


@pytest.fixture
def live(tmp_path, monkeypatch):
    """Merged app on loopback with its real GraphQL router (unlike the
    performance suite's fixture, which stubs it out)."""
    import uvicorn
    from trader.dashboard import server
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setenv('DASH_TOKEN', 'fixture-token')
    attempts = venue_guard(monkeypatch)
    _seed(tmp_path)
    shutil.copy(Path(__file__).parents[1] / 'org.yaml', tmp_path / 'org.yaml')
    _mock_venue(monkeypatch)
    monkeypatch.setattr(engine, 'BrainLLM', FakeLLM)
    j = Journal(str(tmp_path / 'data' / 'luffy.db'))
    j.kv_set('control_state', 'ACTIVE')
    j.kv_set('panic_requested', '0')
    app = server.create_app({'attention': {'enabled': True}, 'chat': {'max_steps': 1}})
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(.05)
    yield SimpleNamespace(url=f'http://127.0.0.1:{port}', journal=j)
    srv.should_exit = True
    t.join(5)
    assert attempts == [], f'venue lookup attempted: {attempts}'


def control(j):
    return (j.kv_get('control_state'), j.kv_get('panic_requested'),
            j.query('SELECT COUNT(*) n FROM control_events')[0]['n'])


def guard(state):
    """Record chat bodies and GraphQL; let only the typed control mutation
    reach the server (other resolvers are not under test); count Overview
    requests in flight, optionally holding one response."""
    state.update(chat=[], gql=[], ov_cur=0, ov_max=0, hold=None)

    async def hook(r):
        u, body = r.request.url, r.request.post_data or ''
        if u.endswith('/api/chat'):
            state['chat'].append(json.loads(body))
        elif u.endswith('/graphql'):
            state['gql'].append(body)
            if 'set_control_state' not in body:
                await r.fulfill(content_type='application/json', body='{"data":null}')
                return True
        elif '/api/overview' in u:
            state['ov_cur'] += 1
            state['ov_max'] = max(state['ov_max'], state['ov_cur'])
            try:
                resp = await r.fetch()
                hold, state['hold'] = state['hold'], None
                if hold:
                    hold['held'].set()
                    await hold['release'].wait()
                await r.fulfill(response=resp)
            finally:
                state['ov_cur'] -= 1
            return True
        return False
    state['hooks'].append(hook)


async def chat(page, text):
    n = await page.locator('#chat-log .msg.luffy').count()
    await page.fill('#chat-in', text)
    await page.press('#chat-in', 'Enter')
    await page.wait_for_function(
        f"document.querySelectorAll('#chat-log .msg.luffy').length>{n}"
        " && !document.querySelector('#chat-log .msg.luffy:last-child .typing')",
        timeout=10000)
    return await page.locator('#chat-log .msg.luffy .txt').last.text_content()


async def lit(page):
    return await page.evaluate("['active','frozen','halted'].filter("
                               "x=>document.getElementById('b-'+x).classList.contains('on'))")


def test_chat_ui_cannot_change_control_state(live):
    j = live.journal
    before = control(j)

    async def scenario(page, state):
        guard(state)
        await page.evaluate("show('luffy')")
        for p in OPS_PHRASES + tuple(QUICK.values()):
            assert await chat(page, p) == REPLY
        for label in QUICK:           # the quick buttons go through the same chat path
            await page.click(f'#v-luffy button:text-is("{label}")')
        await page.wait_for_function(
            f"document.querySelectorAll('#chat-log .msg.luffy').length>={len(OPS_PHRASES)+2*len(QUICK)}"
            " && !document.querySelector('#chat-log .typing')", timeout=10000)
        await asyncio.sleep(2)        # chat's follow-up refreshes land
        return state, await lit(page)

    state, on = run(live.url, scenario)
    assert control(j) == before == ('ACTIVE', '0', before[2])
    assert on == ['active']
    assert len(state['chat']) == len(OPS_PHRASES) + 2 * len(QUICK)
    assert all(set(b) == {'message', 'history'} for b in state['chat'])   # no do_ops from the client
    assert not any('mutation' in b for b in state['gql'])
    assert state['ov_max'] == 1       # follow-up refreshes coalesce


def test_chat_panel_freeze_is_the_confirmed_typed_mutation(live):
    j = live.journal

    async def scenario(page, state):
        guard(state)
        await page.evaluate("show('luffy')")
        freeze = page.locator('#v-luffy button.danger:text-is("Freeze")')
        page.once('dialog', lambda d: d.dismiss())
        await freeze.click()
        await asyncio.sleep(.5)
        dismissed = (control(j)[0], [b for b in state['gql'] if 'mutation' in b])
        prompts = []

        async def accept(d):
            prompts.append(d.message)
            await d.accept()
        page.once('dialog', accept)
        await freeze.click()
        await page.wait_for_function(
            "document.getElementById('b-frozen').classList.contains('on')", timeout=10000)
        return state, dismissed, prompts, await lit(page)

    state, dismissed, prompts, on = run(live.url, scenario)
    assert dismissed == ('ACTIVE', [])
    assert prompts == ['Set control state to FROZEN?']
    mutations = [json.loads(b) for b in state['gql'] if 'mutation' in b]
    assert mutations == [{'query': 'mutation($s:String!){set_control_state(state:$s)}',
                          'variables': {'s': 'FROZEN'}}]
    assert state['chat'] == []        # Freeze does not go through chat
    assert control(j)[:2] == ('FROZEN', '0')
    assert j.query('SELECT actor, to_state FROM control_events ORDER BY rowid DESC LIMIT 1') == [
        {'actor': 'dashboard', 'to_state': 'FROZEN'}]
    assert on == ['frozen']


def test_pre_freeze_overview_cannot_overwrite_freeze(live):
    """An Overview fetched while ACTIVE is held; Freeze and a chat land during
    it. Their refreshes must produce one fresh follow-up read (not join the
    held one), FROZEN must render well before the 8 s poll, and nothing may
    render ACTIVE after FROZEN."""
    j = live.journal

    async def scenario(page, state):
        guard(state)
        await page.evaluate("show('luffy')")
        await page.evaluate("""window.__lit=[];new MutationObserver(()=>{
          const on=['active','frozen','halted'].filter(x=>document.getElementById('b-'+x).classList.contains('on'));
          if(on.join()!==window.__lit.at(-1))window.__lit.push(on.join());
        }).observe(document.getElementById('b-frozen').parentNode,{subtree:true,attributes:true});""")
        hold = {'held': asyncio.Event(), 'release': asyncio.Event()}
        state['hold'] = hold
        await page.evaluate('void refresh()')   # do not await the held read
        await asyncio.wait_for(hold['held'].wait(), 10)
        page.once('dialog', lambda d: d.accept())
        await page.evaluate("setState('FROZEN')")
        assert control(j)[0] == 'FROZEN'
        assert await chat(page, 'why did you freeze?') == REPLY
        await asyncio.sleep(2)        # chat's delayed refresh() fires while held
        assert state['ov_cur'] == 1
        t0 = time.monotonic()
        hold['release'].set()
        await page.wait_for_function(
            "document.getElementById('b-frozen').classList.contains('on')", timeout=3000)
        await asyncio.sleep(1.5)
        return (state, since(state, t0, '/api/overview'), await lit(page),
                await page.evaluate('window.__lit'))

    state, after, on, history = run(live.url, scenario)
    assert on == ['frozen']
    assert history[history.index('frozen'):] == ['frozen'], history
    assert state['ov_max'] == 1
    assert len(after) == 1            # one coalesced follow-up, not one per caller


def test_chat_reply_while_hidden_does_not_poll(live):
    async def scenario(page, state):
        guard(state)
        await page.evaluate("show('luffy')")
        gate = asyncio.Event()

        async def slow_chat(r):
            if r.request.url.endswith('/api/chat'):
                await gate.wait()
            return False
        state['hooks'].insert(0, slow_chat)
        await page.fill('#chat-in', 'how are we doing today?')
        await page.press('#chat-in', 'Enter')
        await page.evaluate("window.__hidden=true;document.dispatchEvent(new Event('visibilitychange'))")
        t0 = time.monotonic()
        gate.set()
        await page.wait_for_function("!document.querySelector('#chat-log .typing')", timeout=10000)
        await asyncio.sleep(3)        # past the 1.5 s follow-up refresh
        hidden_reads = [u for u in since(state, t0) if not u.endswith('/api/chat')]
        t1 = time.monotonic()
        await page.evaluate("window.__hidden=false;document.dispatchEvent(new Event('visibilitychange'))")
        await asyncio.sleep(1)
        return hidden_reads, since(state, t1, '/api/overview')

    hidden_reads, shown = run(live.url, scenario)
    assert hidden_reads == []
    assert len(shown) == 1


def test_chat_401_ends_the_session(live):
    j = live.journal
    before = control(j)

    async def scenario(page, state):
        guard(state)
        await page.evaluate("show('luffy')")

        async def expired(r):
            if r.request.url.endswith('/api/chat'):
                await r.fulfill(status=401, content_type='application/json', body='{}')
                return True
            return False
        state['hooks'].insert(0, expired)
        await page.fill('#chat-in', 'panic')
        await page.press('#chat-in', 'Enter')
        await page.wait_for_selector('#signed-out', timeout=5000)
        t0 = time.monotonic()
        await asyncio.sleep(3)        # past the follow-up refresh and poll timers
        text = await page.evaluate('document.body.innerText')
        return since(state, t0), text

    after, text = run(live.url, scenario)
    assert after == []
    assert 'Session expired' in text and REPLY not in text and '$1,000' not in text
    assert control(j) == before
