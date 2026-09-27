"""Dashboard performance package: local-first Overview, bounded enrichment,
bounded reads and shared work. All external dependencies are mocked."""
import asyncio
import hashlib
import json
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_attention_view import server  # noqa: F401  (fixture)
from trader.core.journal import Journal
from trader.dashboard import enrichment, local_view

H = {'x-luffy-token': 'fixture-token'}


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _seed(root: Path, equity_age_s=30, heartbeat_age_s=10, positions=True):
    j = Journal(str(root / 'data' / 'luffy.db'))
    now = datetime.now(timezone.utc)
    with j._tx() as c:
        c.execute("INSERT INTO state_kv VALUES('control_state','FROZEN')")
        c.execute("INSERT INTO state_kv VALUES('market_type','futures')")
        c.execute("INSERT INTO control_events(ts,event,from_state,to_state,actor)"
                  " VALUES(?,?,?,?,?)", (_iso(now - timedelta(hours=1)),
                                         'state_change', 'ACTIVE', 'FROZEN', 'operator'))
        c.execute("INSERT INTO equity VALUES(?,?,?,?)",
                  (_iso(now - timedelta(seconds=equity_age_s + 60)), 990.0, 990.0, 0))
        c.execute("INSERT INTO equity VALUES(?,?,?,?)",
                  (_iso(now - timedelta(seconds=equity_age_s)), 1000.0, 995.0, 1))
        if positions:
            c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,notional_usdt,"
                      "stop_loss,take_profit,opened_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      ('t1', 'SOL/USDT', 'long', 2.0, 100.0, 200.0, 95.0, None,
                       _iso(now - timedelta(hours=2)), 'open'))
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,status,"
                  "closed_at,realized_pnl,strategy_name) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ('t0', 'BTC/USDT', 'short', 1, 100, _iso(now - timedelta(hours=5)),
                   'closed', _iso(now), 5.5, 'donchian'))
        c.execute("INSERT INTO cycles(id,ts,symbol,regime) VALUES('c1',?,'SOL/USDT','trend')",
                  (_iso(now),))
        for i, agent in enumerate(('a', 'b', 'a')):
            c.execute("INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,confidence)"
                      " VALUES('c1',?,?,?,?,?,?)",
                      (_iso(now - timedelta(seconds=60 - i)), 'SOL/USDT', agent, 'long', .1 * i, .5))
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,"
                  "confidence,executed) VALUES('d1','c1',?,'SOL/USDT','BUY',.5,.3,.5,1)",
                  (_iso(now),))
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,"
                  "confidence,executed) VALUES('d2','c1',?,'SOL/USDT','HOLD',0,.3,.5,0)",
                  (_iso(now),))
    (root / 'data' / 'heartbeat_luffy.json').write_text(
        json.dumps({'timestamp': time.time() - heartbeat_age_s}))
    return j


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError('external network attempted')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'getaddrinfo', refuse)
    import requests
    monkeypatch.setattr(requests, 'get', refuse)


def _db_digest(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / 'data').glob('luffy.db*')}


# ── local-first Overview ──────────────────────────────────────────────────
def test_overview_is_local_only_and_labels_provenance(server, tmp_path, no_network, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(enrichment.Enrichment, 'refresh',
                        lambda *_: pytest.fail('enrichment on local path'))
    before = _db_digest(tmp_path)
    ov = TestClient(server.create_app({'attention': {'enabled': False}})).get(
        '/api/overview', headers=H).json()
    assert ov['control']['control_state'] == 'FROZEN'
    assert ov['control']['verified'] is False and ov['control']['source'] == 'journal.state_kv'
    assert ov['equity']['equity'] == 1000.0 and ov['equity']['equity_prev'] == 990.0
    assert ov['equity']['freshness'] == 'fresh' and ov['equity']['venue_confirmed'] is False
    assert ov['heartbeat']['freshness'] == 'fresh'
    [pos] = ov['positions']['rows']
    assert pos['protection'] == {'source': 'journal.trades', 'venue_verified': False,
                                 'stop_loss': 95.0, 'take_profit': None,
                                 'has_stop_order_id': False}
    assert ov['today']['taken'] == 1 and ov['today']['holds'] == 1
    assert ov['today']['realized_pnl_today'] == 5.5
    assert ov['performance']['winrate'] == 100.0
    assert [a['agent'] for a in ov['agents']['rows']] == ['a', 'b']
    assert ov['strategies']['paper_or_active'] == 0
    assert len(json.dumps(ov)) < 8000
    # the dashboard's own Journal() at app start is the only writer; the
    # overview request itself left the store byte-identical
    after = _db_digest(tmp_path)
    assert before['luffy.db'] == after['luffy.db']


def test_stale_and_missing_local_data_are_explicit(server, tmp_path, no_network):
    _seed(tmp_path, equity_age_s=3600, heartbeat_age_s=3600, positions=False)
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    ov = client.get('/api/overview', headers=H).json()
    assert ov['equity']['freshness'] == 'stale' and ov['equity']['equity'] == 1000.0
    assert ov['heartbeat']['freshness'] == 'stale'
    (tmp_path / 'data' / 'heartbeat_luffy.json').unlink()
    store = local_view.ReadOnlyStore(tmp_path / 'data' / 'luffy.db')
    hb = local_view.overview(store, tmp_path)['heartbeat']
    assert hb['freshness'] == 'unavailable' and hb['age_s'] is None


def test_missing_store_is_unavailable_not_zero(tmp_path, no_network):
    store = local_view.ReadOnlyStore(tmp_path / 'data' / 'luffy.db')
    ov = local_view.overview(store, tmp_path)
    for key in ('control', 'equity', 'positions', 'today', 'performance', 'agents'):
        assert ov[key]['freshness'] == 'unavailable', key
    assert 'equity' not in ov['equity'] and ov['heartbeat']['freshness'] == 'unavailable'
    assert not (tmp_path / 'data' / 'luffy.db').exists()      # never created


def test_empty_equity_table_is_unavailable(tmp_path):
    Journal(str(tmp_path / 'luffy.db'))
    ov = local_view.overview(local_view.ReadOnlyStore(tmp_path / 'luffy.db'), tmp_path)
    assert ov['equity'] == {'source': 'journal.equity', 'observed_at': None, 'age_s': None,
                            'freshness': 'unavailable', 'reason': 'no equity recorded'}
    assert ov['control']['control_state'] is None           # no ACTIVE default


def test_read_only_store_cannot_write(tmp_path):
    Journal(str(tmp_path / 'luffy.db'))
    store = local_view.ReadOnlyStore(tmp_path / 'luffy.db')
    with pytest.raises(local_view.StoreUnavailable):
        store.query("INSERT INTO state_kv VALUES('x','y')")
    assert store.query("SELECT COUNT(*) n FROM state_kv")[0]['n'] == 0


def test_read_only_store_reads_during_writer_lock(tmp_path):
    Journal(str(tmp_path / 'luffy.db'))
    w = sqlite3.connect(tmp_path / 'luffy.db')
    w.execute('PRAGMA journal_mode=WAL')
    w.execute('BEGIN IMMEDIATE')
    w.execute("INSERT INTO state_kv VALUES('pending','1')")
    try:
        start = time.perf_counter()
        rows = local_view.ReadOnlyStore(tmp_path / 'luffy.db').query(
            "SELECT value FROM state_kv WHERE key='pending'")
        assert rows == [] and time.perf_counter() - start < .5
    finally:
        w.rollback()
        w.close()


def test_overview_is_shared_across_concurrent_clients(server, tmp_path, no_network, monkeypatch):
    _seed(tmp_path)
    calls = []
    real = server.local_overview

    def counted(*a, **k):
        calls.append(1)
        time.sleep(.1)
        return real(*a, **k)
    monkeypatch.setattr(server, 'local_overview', counted)
    app = server.create_app({'attention': {'enabled': False}})

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://t', headers=H) as c:
            rs = await asyncio.gather(*[c.get('/api/overview') for _ in range(8)])
        assert all(r.status_code == 200 for r in rs)
    asyncio.run(burst())
    assert len(calls) == 1


# ── optional enrichment ──────────────────────────────────────────────────
def test_single_flight_joins_and_never_replaces_running_job():
    c = enrichment.SingleFlightCache(ThreadPoolExecutor(2))
    release, started = threading.Event(), []

    def slow():
        started.append(1)
        assert release.wait(5)
        return {'v': 1}, '2026-09-27T00:00:00+00:00'
    f1 = c.ensure('k', slow, 60)
    f2 = c.ensure('k', slow, 60)
    assert f1 is f2 and c.submitted == 1
    view = c.view('k', 60)
    assert view['refreshing'] and view['freshness'] == 'unavailable' and view['value'] is None
    release.set()
    f1.result(5)
    view = c.view('k', 60)
    assert view['value'] == {'v': 1} and view['freshness'] == 'fresh'
    assert c.ensure('k', slow, 60) is None and len(started) == 1


def test_timestamp_is_taken_at_completion():
    now = [1000.0]
    c = enrichment.SingleFlightCache(ThreadPoolExecutor(1), clock=lambda: now[0])

    def loader():
        now[0] = 1050.0              # the job took 50 s
        return {'p': 1}, None
    c.ensure('k', loader, 15).result(5)
    now[0] = 1060.0
    view = c.view('k', 15)
    assert view['age_s'] == 10.0 and view['freshness'] == 'fresh'


def test_failed_refresh_keeps_old_value_and_stays_stale():
    now = [1000.0]
    c = enrichment.SingleFlightCache(ThreadPoolExecutor(1), clock=lambda: now[0])
    c.ensure('k', lambda: ({'p': 1}, 'src'), 15).result(5)
    now[0] = 1100.0

    def boom():
        raise TimeoutError('slow venue')
    c.ensure('k', boom, 15).result(5)
    view = c.view('k', 15)
    assert view['value'] == {'p': 1} and view['source_time'] == 'src'
    assert view['freshness'] == 'stale' and view['age_s'] == 100.0
    assert view['last_refresh_failed'] and view['last_error'] == 'TimeoutError'
    assert c.ensure('k', boom, 15) is None            # failure back-off
    now[0] = 1100.0 + enrichment.RETRY_AFTER_FAILURE_S + 1
    c.ensure('k', lambda: ({'p': 2}, None), 15).result(5)
    assert c.view('k', 15)['value'] == {'p': 2} and not c.view('k', 15)['last_refresh_failed']


def test_account_error_body_is_failure_not_zero(monkeypatch):
    import requests
    from trader.core.config import Env

    class R:
        def json(self):
            return {'code': -2015, 'msg': 'Invalid API-key'}
    seen = {}

    def get(*a, **k):
        seen['timeout'] = k.get('timeout')
        return R()
    monkeypatch.setattr(requests, 'get', get)
    with pytest.raises(RuntimeError, match='account_response_invalid'):
        enrichment.fetch_account(('k', 's'))
    assert seen['timeout'] == enrichment.ACCOUNT_TIMEOUT


def test_account_update_time_is_not_a_freshness_time(monkeypatch):
    import requests
    from trader.core.config import Env

    class R:
        def __init__(self, body): self.body = body
        def json(self): return self.body
    monkeypatch.setattr(requests, 'get', lambda url, **k: R(
        {'assets': [{'asset': 'USDT', 'walletBalance': '50', 'updateTime': 946684800000},
                    {'asset': 'BNB', 'walletBalance': '1.5', 'updateTime': 0}],
         'totalMarginBalance': '55.5'}))
    value, src = enrichment.fetch_account(('k', 's'))
    assert src is None                                   # freshness = retrieval
    assert value['last_balance_change_at'].startswith('2000-01-01')
    assert value['assets_usd'] == {'USDT': 50.0} and value['assets_unconverted'] == {'BNB': '1.500000'}
    assert value['assets_usd_total'] == 50.0 and value['assets_usd_total_complete'] is False


def test_ticker_job_is_budgeted_and_reports_every_symbol(monkeypatch):
    class Ex:
        def fetch_ticker(self, sym):
            if sym == 'BAD/USDT':
                raise TimeoutError()
            return {'last': 10.0, 'timestamp': 1790000000000}
    clock = iter([0, 0, 1, 2, 100, 100, 100])
    monkeypatch.setattr(enrichment.time, 'monotonic', lambda: next(clock))
    value, _ = enrichment.fetch_tickers(Ex(), ['A/USDT', 'BAD/USDT', 'C/USDT', 'D/USDT'])
    assert set(value['prices']) == {'A/USDT', 'C/USDT'}
    assert value['errors'] == {'BAD/USDT': 'TimeoutError'} and value['skipped'] == ['D/USDT']
    assert value['prices']['A/USDT']['source_time'].startswith('2026-09-21')
    assert value['prices']['A/USDT']['retrieved_at']


def test_partial_ticker_failure_keeps_old_price_as_stale():
    now = time.time()
    old = {'prices': {'A/USDT': {'price': 1.0, 'source_time': enrichment._iso(now - 5),
                                 'retrieved_at': enrichment._iso(now - 5)},
                      'B/USDT': {'price': 2.0, 'source_time': enrichment._iso(now - 5),
                                 'retrieved_at': enrichment._iso(now - 5)}}}
    new = {'prices': {'B/USDT': {'price': 3.0, 'source_time': enrichment._iso(now),
                                 'retrieved_at': enrichment._iso(now)}},
           'errors': {'A/USDT': 'TimeoutError'}, 'skipped': []}
    merged = enrichment.merge_tickers(old, new)
    assert merged['prices']['B/USDT']['price'] == 3.0
    a = merged['prices']['A/USDT']
    assert a['price'] == 1.0 and a['last_error'] == 'TimeoutError'
    assert a['retrieved_at'] == old['prices']['A/USDT']['retrieved_at']   # own timestamp kept
    assert enrichment.symbol_freshness(a, now, 15)['freshness'] == 'stale'
    assert enrichment.symbol_freshness(merged['prices']['B/USDT'], now, 15)['freshness'] == 'fresh'


def test_missing_or_future_ticker_time_is_not_fresh():
    now = time.time()
    base = {'price': 1.0, 'retrieved_at': enrichment._iso(now - 1)}
    assert enrichment.symbol_freshness({**base, 'source_time': None}, now, 15)['freshness'] == 'unknown'
    assert enrichment.symbol_freshness({**base, 'source_time': 'garbage'}, now, 15)['freshness'] == 'unknown'
    future = {**base, 'source_time': enrichment._iso(now + 3600)}
    assert enrichment.symbol_freshness(future, now, 15)['freshness'] == 'unknown'
    ok = {**base, 'source_time': enrichment._iso(now - 2)}
    assert enrichment.symbol_freshness(ok, now, 15)['freshness'] == 'fresh'


def test_fresh_retrieval_of_an_old_ticker_is_stale():
    now = time.time()
    p = {'price': 1.0, 'source_time': '2000-01-01T00:00:00+00:00',
         'retrieved_at': enrichment._iso(now - 1)}
    f = enrichment.symbol_freshness(p, now, 15)
    assert f['freshness'] == 'stale' and f['retrieved_age_s'] < 2 and f['source_age_s'] > 1e8


def _mock_venue(monkeypatch, account=lambda: ({'margin_equity': 1.0}, None),
                tickers=None, fingerprint='fpA'):
    monkeypatch.setattr(enrichment, 'fetch_account', lambda keys: account())
    monkeypatch.setattr(enrichment, '_account_identity', lambda: (fingerprint, ('k', 's')))
    monkeypatch.setattr(enrichment.Enrichment, '_exchange', lambda self, route: object())
    monkeypatch.setattr(enrichment, 'fetch_tickers', tickers or (lambda ex, s: (
        {'prices': {'SOL/USDT': {'price': 110.0, 'source_time': enrichment._iso(time.time()),
                                 'retrieved_at': enrichment._iso(time.time())}},
         'errors': {}, 'skipped': []}, None)))


def test_enrichment_endpoint_answers_while_venue_stalls(server, tmp_path, monkeypatch):
    _seed(tmp_path)
    release = threading.Event()
    calls = {'account': 0, 'tickers': 0}

    def stalled_account():
        calls['account'] += 1
        release.wait(10)
        return {'margin_equity': 1.0}, None

    def stalled_tickers(ex, symbols):
        calls['tickers'] += 1
        assert 'SOL/USDT' in symbols
        release.wait(10)
        t = enrichment._iso(time.time())
        return {'prices': {'SOL/USDT': {'price': 110.0, 'source_time': t, 'retrieved_at': t}},
                'errors': {}, 'skipped': []}, None
    _mock_venue(monkeypatch, stalled_account, stalled_tickers)
    monkeypatch.setattr(server, 'ENRICH_WAIT_S', .2)
    app = server.create_app({'attention': {'enabled': False}})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://t', headers=H) as c:
            t = time.perf_counter()
            first, second = await asyncio.gather(c.get('/api/enrichment'),
                                                 c.get('/api/enrichment'))
            assert time.perf_counter() - t < 1.5
            ov = await asyncio.wait_for(c.get('/api/overview'), .5)
            assert ov.json()['equity']['equity'] == 1000.0
            assert (await asyncio.wait_for(c.get('/'), .5)).status_code == 200
            return first.json(), second.json()
    first, second = asyncio.run(exercise())
    assert calls == {'account': 1, 'tickers': 1}          # joined, not duplicated
    assert first['account']['freshness'] == 'unavailable' and first['account']['refreshing']
    assert first['marks']['rows'] == {}
    release.set()
    time.sleep(.2)
    done = TestClient(app).get('/api/enrichment', headers=H).json()
    assert calls == {'account': 1, 'tickers': 1}
    assert done['tickers']['freshness'] == 'fresh'
    m = done['marks']['rows']['t1']
    assert (m['mark'], m['upnl_estimate'], m['upnl_pct'], m['freshness']) == (110.0, 20.0, 10.0, 'fresh')


def test_enrichment_cache_is_bound_to_app_and_account(server, tmp_path, monkeypatch):
    _seed(tmp_path)
    who = {'fp': 'fpA'}
    monkeypatch.setattr(enrichment, '_account_identity', lambda: (who['fp'], (who['fp'], 's')))
    # the loader uses the credentials captured at admission
    monkeypatch.setattr(enrichment, 'fetch_account',
                        lambda keys: ({'margin_equity': {'fpA': 1.0, 'fpB': 2.0}[keys[0]]}, None))
    monkeypatch.setattr(enrichment.Enrichment, '_exchange', lambda self, route: object())
    monkeypatch.setattr(enrichment, 'fetch_tickers', lambda ex, s: (_ for _ in ()).throw(TimeoutError()))

    def equity(client):
        for _ in range(20):
            v = client.get('/api/enrichment', headers=H).json()['account']['value']
            if v:
                return v['margin_equity']
            time.sleep(.05)
    a = TestClient(server.create_app({'attention': {'enabled': False}}))
    assert equity(a) == 1.0
    who['fp'] = 'fpB'                                    # another app, another account
    b = TestClient(server.create_app({'attention': {'enabled': False}}))
    assert equity(b) == 2.0
    # the same app never serves A's cached value once its identity is B
    assert equity(a) == 2.0


def test_legacy_summary_v2_never_waits_and_keeps_legacy_fields(server, tmp_path, monkeypatch):
    _seed(tmp_path)
    release = threading.Event()
    _mock_venue(monkeypatch, lambda: (release.wait(10), None),
                lambda ex, s: (release.wait(10), None))
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    t = time.perf_counter()
    s = client.get('/api/v2/summary', headers=H).json()
    assert time.perf_counter() - t < 1.0
    release.set()
    assert s['schema'] == 'luffy.summary.v2'
    assert s['equity'] == 1000.0 and s['assets_total'] is None and s['prices'] == {}
    [p] = s['open_positions']
    assert p['stop_loss'] == 95.0 and p['sl_dist'] == 5.0 and p['tp_dist'] is None
    assert 'upnl' not in p and 'decision_id' in p and 'exec_mode' in p   # full journal row
    assert s['total_upnl'] is None and s['freshness']['total_upnl_complete'] is False
    assert s['freshness']['equity']['freshness'] == 'fresh'


def test_new_endpoints_require_authentication(server, tmp_path):
    _seed(tmp_path)
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    for path in ('/api/overview', '/api/enrichment', '/api/v2/summary', '/api/summary',
                 '/api/pipeline/book'):
        assert client.get(path).status_code == 401


# ── accounting completeness ──────────────────────────────────────────────
def test_unknown_accounting_is_null_not_zero(tmp_path):
    j = _seed(tmp_path)
    now = _iso(datetime.now(timezone.utc))
    with j._tx() as c:
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,status,"
                  "closed_at,realized_pnl) VALUES('tn','X/USDT','long',1,1,?, 'closed',?,NULL)",
                  (now, now))
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,notional_usdt,"
                  "opened_at,status) VALUES('tm','Y/USDT','short',1,1,NULL,?,'open')", (now,))
    ov = local_view.overview(local_view.ReadOnlyStore(tmp_path / 'data' / 'luffy.db'), tmp_path)
    t = ov['today']
    assert t['closed_today'] == 2 and t['realized_pnl_today'] is None
    assert t['realized_pnl_missing'] == 1 and t['realized_pnl_known_sum'] == 5.5
    perf = ov['performance']
    assert perf['closed_trades'] == 2 and perf['winrate_basis_trades'] == 1
    assert perf['winrate'] == 100.0 and perf['pnl_missing'] == 1
    ex = ov['positions']['entry_notional']
    assert ex['complete'] is False and ex['long'] is None and ex['short'] is None
    assert ex['known_long'] == 200.0 and ex['rows_missing_notional'] == 1


def test_known_empty_book_is_a_true_zero(tmp_path):
    Journal(str(tmp_path / 'luffy.db'))
    ov = local_view.overview(local_view.ReadOnlyStore(tmp_path / 'luffy.db'), tmp_path)
    assert ov['today']['realized_pnl_today'] == 0.0 and ov['today']['closed_today'] == 0
    ex = ov['positions']['entry_notional']
    assert ex['complete'] and ex['long'] == 0.0 and ov['positions']['open_total'] == 0


def test_position_cap_is_disclosed_and_exposure_covers_all(tmp_path, monkeypatch):
    j = _seed(tmp_path, positions=False)
    monkeypatch.setattr(local_view, 'MAX_OPEN_ROWS', 2)
    with j._tx() as c:
        for i in range(3):
            c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,notional_usdt,"
                      "opened_at,status) VALUES(?,?,?,?,?,?,?,?)",
                      (f'o{i}', 'Z/USDT', 'long', 1, 1, 10.0, _iso(datetime.now(timezone.utc)), 'open'))
    pos = local_view.overview(local_view.ReadOnlyStore(tmp_path / 'data' / 'luffy.db'), tmp_path)['positions']
    assert pos['shown'] == 2 and pos['open_total'] == 3 and pos['truncated'] is True
    assert pos['entry_notional']['long'] == 30.0


# ── bounded reads and shared work ─────────────────────────────────────────
def test_log_tail_reads_a_bounded_suffix(server, tmp_path, monkeypatch):
    (tmp_path / 'logs').mkdir()
    log = tmp_path / 'logs' / 'luffy.log'
    log.write_bytes(b''.join(b'line %07d \x00padding padding padding\n' % i
                             for i in range(200_000)))          # ~7.6 MB
    reads = []
    real_open = open

    def spy(path, mode='r', *a, **k):
        f = real_open(path, mode, *a, **k)
        orig = f.read
        f.read = lambda n=-1: reads.append(n) or orig(n)
        return f
    monkeypatch.setattr(local_view, 'open', spy, raising=False)
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    tail = client.get('/api/logs?lines=3', headers=H).json()['tail']
    assert tail == ['line %07d padding padding padding' % i for i in (199997, 199998, 199999)]
    assert reads and max(reads) <= local_view.LOG_TAIL_MAX_BYTES
    assert len(client.get('/api/logs?lines=100000', headers=H).json()['tail']) == \
        local_view.LOG_TAIL_MAX_LINES
    log.unlink()
    assert client.get('/api/logs', headers=H).json() == {'tail': []}


def test_websocket_frames_are_shared_across_connections(server, tmp_path, monkeypatch):
    _seed(tmp_path)
    calls = []
    real = server.live_payload
    monkeypatch.setattr(server, 'live_payload', lambda s: calls.append(1) or real(s))
    origin = {**H, 'origin': 'http://testserver'}
    # one event loop, as in the served app (TestClient otherwise gives each
    # websocket its own portal loop)
    with TestClient(server.create_app({'attention': {'enabled': False}})) as client, \
            client.websocket_connect('/ws/live', headers=origin) as a, \
            client.websocket_connect('/ws/live', headers=origin) as b:
        fa, fb = a.receive_json(), b.receive_json()
    assert fa == fb and fa['open_trades'][0]['id'] == 't1'
    assert len(calls) == 1


def test_pipeline_book_cap_is_disclosed_with_a_route_to_the_rest(server, tmp_path, monkeypatch):
    j = _seed(tmp_path)
    monkeypatch.setattr(server, 'PIPELINE_BOOK_ROWS', 3)
    with j._tx() as c:
        for i in range(5):
            c.execute("INSERT INTO strategies(id,name,kind,params,state,created_at,origin)"
                      " VALUES(?,?,?,?,?,?,?)", (f's{i}', f's{i}', 'k', '{}', 'paper',
                                                 _iso(datetime.now(timezone.utc)), 'brain'))
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    pop = client.get('/api/pipeline', headers=H).json()['population']
    assert len(pop['book']) == 3 and pop['book_truncated'] is True and pop['book_total'] == 5
    rest = client.get(pop['book_rest'], headers=H).json()
    first = client.get('/api/pipeline/book?offset=0', headers=H).json()
    assert [r['id'] for r in first['rows']] == [r['id'] for r in pop['book']] and first['more']
    ids = [r['id'] for r in first['rows'] + rest['rows']]
    assert set(ids) == {f's{i}' for i in range(5)}
    assert rest['total'] == 5 and rest['more'] is False


def test_waiting_on_shared_work_holds_no_worker_token(server, tmp_path, monkeypatch):
    """45 callers wait on one stalled org computation; Overview still runs."""
    _seed(tmp_path)
    release, entered = threading.Event(), threading.Event()

    def stalled(*a):
        entered.set()
        assert release.wait(20)
        return {'employees': []}
    monkeypatch.setattr(server, 'build_company', stalled)
    app = server.create_app({'attention': {'enabled': False}})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://t', headers=H, timeout=30) as c:
            orgs = [asyncio.create_task(c.get('/api/org')) for _ in range(45)]
            assert await asyncio.to_thread(entered.wait, 5)
            await asyncio.sleep(.2)
            t = time.perf_counter()
            ov = await asyncio.wait_for(c.get('/api/overview'), 3)
            took = time.perf_counter() - t
            release.set()
            rs = await asyncio.gather(*orgs)
            return ov, took, rs
    ov, took, rs = asyncio.run(exercise())
    assert ov.status_code == 200 and took < 2
    codes = sorted({r.status_code for r in rs})
    assert codes in ([200], [200, 503])
    busy = [r for r in rs if r.status_code == 503]
    assert len(busy) == 45 - 32 and all(r.json()['status'] == 'busy' for r in busy)


def test_shared_work_timeout_serves_stale_value_labelled():
    async def run():
        clock = [0.0]
        s = local_view.SharedTTL(clock=lambda: clock[0], wait_s=.2)
        v, meta = await s.get('k', 1, lambda: 'first')
        assert v == 'first' and meta['stale'] is False
        clock[0] = 5.0
        gate = threading.Event()
        v, meta = await s.get('k', 1, lambda: gate.wait(5) and 'second')
        assert v == 'first' and meta == {'age_s': 5.0, 'stale': True, 'reason': 'timeout'}
        gate.set()
        await asyncio.sleep(.1)
    asyncio.run(run())


def test_blocking_work_runs_off_the_event_loop(server):
    import inspect
    app = server.create_app({'attention': {'enabled': False}})
    sync_paths = {'/', '/api/logs', '/api/vault/tree', '/api/vault/file', '/api/doctrine',
                  '/api/review_status', '/api/brain/last_autopsy', '/api/klines',
                  '/api/pipeline/book'}
    for r in app.routes:
        if getattr(r, 'path', None) in sync_paths:
            assert not inspect.iscoroutinefunction(r.endpoint), r.path
    # async handlers delegate their work through SharedTTL/run_in_threadpool
    src = inspect.getsource(server.create_app)
    for name in ('overview', 'summary', 'org', 'pipeline', 'vault_graph'):
        assert f'async def {name}(' in src


# ── controls unchanged ───────────────────────────────────────────────────
def test_control_mutation_behaviour_is_unchanged(tmp_path, monkeypatch):
    """The real GraphQL router still writes control intent exactly as before."""
    from trader.dashboard import server as srv
    monkeypatch.setattr(srv, 'ROOT', tmp_path)
    monkeypatch.setenv('DASH_TOKEN', 'fixture-token')
    _seed(tmp_path)
    client = TestClient(srv.create_app({'attention': {'enabled': False}}))
    r = client.post('/graphql', headers={**H, 'origin': 'http://testserver'},
                    json={'query': 'mutation{set_control_state(state:"HALTED")}'})
    assert r.status_code == 200 and not r.json().get('errors')
    j = Journal(str(tmp_path / 'data' / 'luffy.db'))
    assert j.kv_get('control_state') == 'HALTED'
    assert client.post('/graphql', json={'query': 'mutation{panic}'},
                       headers={'origin': 'http://evil'}).status_code == 403


# ── client contract (static) ──────────────────────────────────────────────
def test_client_startup_has_no_parser_blocking_optional_scripts():
    html = (Path(__file__).parents[1] / 'trader/dashboard/web/index.html').read_text()
    head = html.split('</head>')[0]
    assert '<script src=' not in head
    assert 'location.reload' not in html
    assert "location.protocol==='https:'?'wss':'ws'" in html
    assert "fetch('/api/summary" not in html
    for js in ('attention.js', 'investigation.js'):
        src = (Path(__file__).parents[1] / 'trader/dashboard/web' / js).read_text()
        assert 'document.hidden' in src and 'luffySession' in src
    # every client read goes through the session-aware shared coordinator;
    # only POSTs (chat, autopsy) and gql() mutations call fetch directly
    import re
    direct = re.findall(r"fetch\('([^']+)'", html)
    assert set(direct) <= {'/api/brain/autopsy', '/api/chat', '/graphql'}, direct


# ── revision 3: identity at admission, versioned pages, v2 summary ────────
def test_identity_switch_during_first_fetch_never_leaks_previous_account():
    e = enrichment.Enrichment()
    who, gate, started = {'fp': 'fpA'}, threading.Event(), threading.Event()

    def fetch(keys):
        started.set()
        if keys[0] == 'fpA':
            assert gate.wait(5)            # A's first fetch held after capture
        return {'owner': keys[0]}, None
    import unittest.mock as um
    with um.patch.object(enrichment, '_account_identity', lambda: (who['fp'], (who['fp'], 's'))), \
            um.patch.object(enrichment, 'fetch_account', fetch), \
            um.patch.object(enrichment.Enrichment, '_exchange', lambda self, r: object()), \
            um.patch.object(enrichment, 'fetch_tickers', lambda ex, s: (_ for _ in ()).throw(TimeoutError())):
        [a_job, *_] = e.refresh([])
        gen_a = e.view([])['account']['generation']
        assert started.wait(5)
        who['fp'] = 'fpB'
        jobs_b = e.refresh([])                 # joins A's still-running job
        assert a_job in jobs_b and e.cache.submitted == 2   # A account + tickers only
        view = e.view([])['account']
        assert view['value'] is None and view['generation'] > gen_a
        gate.set(); a_job.result(5)
        assert e.view([])['account']['value'] is None       # A's result discarded
        for j in e.refresh([]):
            j.result(5)
        acct = e.view([])['account']
        assert acct['value'] == {'owner': 'fpB'} and acct['generation'] == view['generation']


def test_unresolved_identity_fails_closed():
    e = enrichment.Enrichment()
    import unittest.mock as um
    with um.patch.object(enrichment, '_account_identity', lambda: ('fpA', ('fpA', 's'))), \
            um.patch.object(enrichment, 'fetch_account', lambda keys: ({'owner': keys[0]}, None)), \
            um.patch.object(enrichment.Enrichment, '_exchange', lambda self, r: object()), \
            um.patch.object(enrichment, 'fetch_tickers', lambda ex, s: (_ for _ in ()).throw(TimeoutError())):
        for j in e.refresh([]):
            j.result(5)
        assert e.view([])['account']['value'] == {'owner': 'fpA'}

    def broken():
        raise RuntimeError('account_credentials_missing')
    with um.patch.object(enrichment, '_account_identity', broken), \
            um.patch.object(enrichment, 'fetch_tickers', lambda ex, s: (_ for _ in ()).throw(TimeoutError())), \
            um.patch.object(enrichment.Enrichment, '_exchange', lambda self, r: object()):
        e.refresh([])
    acct = e.view([])['account']
    assert acct['value'] is None and acct['reason'] == 'identity_unresolved'


def test_failure_of_a_superseded_job_is_discarded_too():
    c = enrichment.SingleFlightCache(ThreadPoolExecutor(1))
    gate = threading.Event()

    def boom():
        gate.wait(5)
        raise TimeoutError()
    f = c.ensure('k', boom, 15, identity='A')
    gen_a = c.view('k', 15)['generation']
    c.invalidate('k', 'B')
    gate.set(); f.result(5)
    v = c.view('k', 15)
    assert v['last_error'] is None and v['generation'] == gen_a + 1


def test_book_pages_are_bound_to_a_version(server, tmp_path, monkeypatch):
    j = _seed(tmp_path)
    monkeypatch.setattr(server, 'PIPELINE_BOOK_ROWS', 3)
    now = datetime.now(timezone.utc)
    with j._tx() as c:
        for i in range(5):
            c.execute("INSERT INTO strategies(id,name,kind,params,state,created_at,origin)"
                      " VALUES(?,?,?,?,?,?,?)", (f's{i}', f's{i}', 'k', '{}', 'paper',
                                                 _iso(now - timedelta(minutes=i)), 'brain'))
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    pop = client.get('/api/pipeline', headers=H).json()['population']
    assert 'version=' in pop['book_rest'] and pop['book_version']
    with j._tx() as c:                         # the book changes between pages
        c.execute("INSERT INTO strategies(id,name,kind,params,state,created_at,origin)"
                  " VALUES('new','new','k','{}','active',?,'brain')", (_iso(now),))
    r = client.get(pop['book_rest'], headers=H)
    assert r.status_code == 409 and r.json()['status'] == 'changed' and r.json()['total'] == 6


def test_v1_summary_is_retired_with_migration_guidance(server, tmp_path):
    _seed(tmp_path)
    r = TestClient(server.create_app({'attention': {'enabled': False}})).get('/api/summary', headers=H)
    assert r.status_code == 410 and r.json()['use'] == '/api/v2/summary'


def test_v2_summary_never_reports_a_partial_wallet_as_total(server, tmp_path, monkeypatch):
    _seed(tmp_path)
    _mock_venue(monkeypatch, lambda: ({'margin_equity': 1.0, 'assets_usd': {'USDT': 100.0},
                                       'assets_unconverted': {'BTC': '1.000000'},
                                       'assets_usd_total': 100.0,
                                       'assets_usd_total_complete': False}, None))
    client = TestClient(server.create_app({'attention': {'enabled': False}}))
    for _ in range(40):
        s = client.get('/api/v2/summary', headers=H).json()
        if s['assets']:
            break
        time.sleep(.05)
    assert s['assets'] == {'USDT': 100.0, 'BTC': '1.000000'}
    assert s['assets_total'] is None and s['assets_known_usd_subtotal'] == 100.0
    assert s['assets_total_complete'] is False and s['freshness']['assets_total_complete'] is False


def test_shared_responses_carry_their_own_serve_time(server, tmp_path):
    _seed(tmp_path)
    ov = TestClient(server.create_app({'attention': {'enabled': False}})).get(
        '/api/overview', headers=H).json()
    assert ov['cache']['served_at'] and ov['generated_at']


# ── revision 4 ────────────────────────────────────────────────────────────
def test_exchange_uses_the_captured_route_not_current_config(monkeypatch):
    import trader.data.feed as feed
    seen = []

    class Feed:
        def __init__(self, ex): self.ex = ex
    monkeypatch.setattr(feed, 'DataFeed', Feed)
    monkeypatch.setattr(feed, 'make_exchange',
                        lambda mt, demo=None, **k: seen.append(demo) or type('X', (), {})())
    monkeypatch.setenv('BINANCE_DEMO', 'false')      # config now says production
    e = enrichment.Enrichment()
    e._exchange('demo')                               # route captured at admission
    e._exchange('production')
    assert seen == [True, False]


def test_venue_guard_detects_attempts_negative_control(tmp_path, monkeypatch):
    """The attention fixture's guard really sees venue calls made in the
    enrichment worker threads (which swallow exceptions)."""
    from fastapi import APIRouter
    from trader.dashboard import server as srv
    from tests.test_attention_view import venue_guard
    monkeypatch.setattr(srv, 'ROOT', tmp_path)
    monkeypatch.setattr(srv, 'make_graphql_router', lambda _: APIRouter())
    monkeypatch.setenv('DASH_TOKEN', 'fixture-token')
    _seed(tmp_path)
    attempts = venue_guard(monkeypatch)
    monkeypatch.setattr(enrichment, '_account_identity', lambda: ('fp', ('k', 's')))
    client = TestClient(srv.create_app({'attention': {'enabled': False}}))
    r = client.get('/api/enrichment', headers=H)
    assert r.status_code == 200
    for _ in range(40):
        if {'account', 'tickers'} <= set(attempts):
            break
        time.sleep(.05)
    assert {'account', 'tickers', 'exchange'} <= set(attempts)
    assert r.json()['account']['value'] is None        # the refused call cached nothing
