"""Offline observation-only Attention and isolated-child regressions."""
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from trader.data.feed import Universe
from trader.observability import supplemental as S
from trader.observability.attention import capture, evaluate_snapshot, settings
from trader.observability.collector import Collector

TF = 14_400_000
NEED = S.required_bars()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*a, **kw):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


class FakeEx:
    """Newest-first venue on the absolute 4h grid, with a forming bar."""

    def __init__(self, total=60, lag=0, holes=(), forming=True, fail=None):
        self.calls = []
        self.fail = fail
        now = int(time.time() * 1000)
        newest = now // TF * TF - lag * TF          # open of the forming bar
        opens = [newest - i * TF for i in range(total)][::-1]
        if not forming:
            opens = opens[:-1]
        self.rows = [[t, 100 + i % 5, 101 + i % 5, 99 + i % 5, 100 + i % 5 + 0.5 * np.sin(i), 10 + i % 7]
                     for i, t in enumerate(opens) if t not in set(holes)]
        self.newest = newest

    def fetch_ohlcv(self, symbol, tf, limit=None, min_bars=1):
        self.calls.append((symbol, tf, None, limit))
        if self.fail:
            raise self.fail
        rows = self.rows[-(limit or 1000):]
        return pd.DataFrame({"ts": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
                             "open": [r[1] for r in rows], "high": [r[2] for r in rows],
                             "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                             "volume": [r[5] for r in rows]})


def feed_of(tmp_path, **kw):
    ex = FakeEx(**kw)
    source = S.IsolatedSource(2)
    source.acquire = ex.fetch_ohlcv
    return source, ex


def now_ms():
    return int(time.time() * 1000)


def scan_frames(symbols, now=None):
    now = now or now_ms()
    anchor = now // TF * TF
    out = {}
    for j, sym in enumerate(symbols):
        c = 100 * np.exp(np.cumsum(np.sin(np.arange(30) + j) * .01))
        out[sym] = {'4h': pd.DataFrame({
            'ts': pd.to_datetime([anchor - (30 - i) * TF for i in range(30)], unit='ms', utc=True),
            'open': c, 'high': c * 1.01, 'low': c * .99, 'close': c,
            'volume': 100 + np.arange(30) % 7})}
    return out


# ── one-symbol bound ──────────────────────────────────────────────────────

def test_exactly_one_symbol_accepted_and_none_is_none():
    assert S.one_symbol(None) is None
    assert S.one_symbol([]) is None
    assert S.one_symbol('C/USDT') == 'C/USDT'
    assert S.one_symbol(['C/USDT']) == 'C/USDT'


@pytest.mark.parametrize('raw', [['A/USDT', 'B/USDT'], ('A/USDT', 'B/USDT', 'C/USDT'),
                                 [''], 5, {'C/USDT': 1}])
def test_multiple_or_malformed_symbols_are_refused(raw, tmp_path):
    with pytest.raises(ValueError):
        S.one_symbol(raw)
    feed, ex = feed_of(tmp_path)
    with pytest.raises(ValueError):
        S.fetch(feed, raw, '4h', now_ms())
    assert ex.calls == []


def test_cut_must_be_explicit(tmp_path):
    feed, ex = feed_of(tmp_path)
    for bad in (None, 1.5, True):
        with pytest.raises(ValueError):
            S.fetch(feed, 'C/USDT', '4h', bad)
    assert ex.calls == []


def test_shared_feed_shape_is_refused_before_acquisition():
    feed = NS(acquire=Mock(), fetch_ohlcv=Mock())
    with pytest.raises(TypeError, match='IsolatedSource'):
        S.fetch(feed, 'C/USDT', '4h', now_ms())
    assert feed.acquire.call_count == feed.fetch_ohlcv.call_count == 0


# ── bounded fetch and statuses ────────────────────────────────────────────

def test_usable_window_is_bounded_single_request(tmp_path):
    feed, ex = feed_of(tmp_path, total=500)
    as_of = now_ms()
    r = S.fetch(feed, 'C/USDT', '4h', as_of)
    assert r.status == S.USABLE and r.reason == 'complete_window'
    assert len(ex.calls) == 1
    assert ex.calls[0][3] == r.requested_limit == max(NEED + 2, S.MIN_REQUEST_BARS) == 30
    assert len(r.frame) == r.bars_present == NEED
    assert r.account_eligibility == 'UNKNOWN' and r.role == 'observation_only'


def test_ninety_day_trading_age_rule_is_not_applied(tmp_path, monkeypatch):
    # listed ~5 days ago: the trading Universe would refuse it
    monkeypatch.setattr(Universe, '_old_enough',
                        Mock(side_effect=AssertionError('universe rule applied')))
    monkeypatch.setattr(Universe, 'symbols',
                        Mock(side_effect=AssertionError('universe consulted')))
    feed, ex = feed_of(tmp_path, total=NEED + 2)
    r = S.fetch(feed, 'NEW/USDT', '4h', now_ms())
    assert r.status == S.USABLE
    assert Universe._old_enough.call_count == 0


def test_only_closed_bars_by_the_cut_are_supplied(tmp_path):
    feed, ex = feed_of(tmp_path, total=60)
    live = now_ms()
    r = S.fetch(feed, 'C/USDT', '4h', live)
    opens = [int(t.timestamp() * 1000) for t in r.frame['ts']]
    assert ex.newest not in set(opens)              # forming bar excluded
    assert all(t + TF <= live for t in opens)
    # an earlier cut excludes every bar that closed after it
    past = live - 2 * TF
    (tmp_path / 'b').mkdir()
    feed2, _ = feed_of(tmp_path / 'b', total=60)
    r2 = S.fetch(feed2, 'C/USDT', '4h', past)
    last = int(r2.frame['ts'].iloc[-1].timestamp() * 1000)
    assert r2.status == S.USABLE and last + TF <= past and last == r2.anchor_open_ms


def test_insufficient_history_is_warmup(tmp_path):
    feed, _ = feed_of(tmp_path, total=10)
    r = S.fetch(feed, 'C/USDT', '4h', now_ms())
    assert (r.status, r.reason, r.bars_present, r.frame) == (S.WARMUP, 'insufficient_history', 9, None)


def test_stale_gap_missing_and_error_are_explicit(tmp_path):
    (tmp_path / 's').mkdir(); (tmp_path / 'g').mkdir(); (tmp_path / 'm').mkdir()
    as_of = now_ms()
    stale, _ = feed_of(tmp_path / 's', total=60, lag=3)
    assert S.fetch(stale, 'C/USDT', '4h', as_of).status == S.STALE
    hole = as_of // TF * TF - 6 * TF
    gap, _ = feed_of(tmp_path / 'g', total=60, holes=(hole,))
    r = S.fetch(gap, 'C/USDT', '4h', as_of)
    assert (r.status, r.frame) == (S.GAP, None)
    empty, _ = feed_of(tmp_path / 'm', total=0)
    assert S.fetch(empty, 'C/USDT', '4h', as_of).status == S.MISSING
    broken, _ = feed_of(tmp_path / 'm', fail=RuntimeError('venue down'))
    assert S.fetch(broken, 'X/USDT', '4h', as_of).status == S.ERROR
    raising = S.IsolatedSource(2)
    raising.acquire = Mock(side_effect=OSError('boom'))
    r = S.fetch(raising, 'C/USDT', '4h', as_of)
    assert (r.status, r.reason) == (S.ERROR, 'fetch_failed:OSError')
    assert raising.acquire.call_count == 1       # no retry


def test_source_frame_is_not_mutated_or_shared(tmp_path):
    feed, ex = feed_of(tmp_path, total=60)
    before = [list(row) for row in ex.rows]
    r = S.fetch(feed, 'C/USDT', '4h', now_ms())
    r.frame.loc[:, 'close'] = -1
    assert ex.rows == before


def test_same_input_same_result(tmp_path):
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    as_of = now_ms()
    a = S.fetch(feed_of(tmp_path / 'a', total=60)[0], 'C/USDT', '4h', as_of)
    b = S.fetch(feed_of(tmp_path / 'b', total=60)[0], 'C/USDT', '4h', as_of)
    assert a == b and a.summary() == b.summary()
    pd.testing.assert_frame_equal(a.frame, b.frame)


# ── Attention capture handoff ─────────────────────────────────────────────

def test_no_supplemental_keeps_capture_shape():
    now = now_ms()
    data = scan_frames(['A/USDT', 'B/USDT'], now)
    legacy = capture(data, list(data), 's', settings(), now)
    explicit = capture(data, list(data), 's', settings(), now, None)
    for e in (legacy, explicit):
        e.pop('capture_ms')
    assert legacy == explicit
    assert 'supplemental' not in legacy['scope']
    assert {m['source'] for m in legacy['input']['membership']} == {'kernel.scan_symbols'}


def test_usable_supplemental_reaches_attention_capture(tmp_path):
    feed, _ = feed_of(tmp_path, total=60)
    now = now_ms()
    r = S.fetch(feed, 'C/USDT', '4h', now)
    data = scan_frames(['A/USDT', 'B/USDT'], now)
    event = capture(data, ['A/USDT', 'B/USDT'], 's', settings(), now, r)
    assert 'C/USDT' not in data                     # caller's frames untouched
    assert event['scope']['included_count'] == 2    # strategy subset unchanged
    assert event['scope']['supplemental']['status'] == 'usable'
    member = [m for m in event['input']['membership'] if m['symbol'] == 'C/USDT']
    assert member == [{'symbol': 'C/USDT', 'from_ms': now, 'to_ms': None,
                       'available_ms': now, 'source': 'attention.supplemental'}]
    rows = [c for c in event['input']['candles'] if c['symbol'] == 'C/USDT']
    assert len(rows) == NEED and all(c['source'] == 'attention.supplemental' for c in rows)
    result = evaluate_snapshot(event)
    status = {row['symbol']: row['status'] for row in result['rows']}
    assert status['C/USDT'] == 'ok'


def test_unusable_supplemental_is_an_issue_not_a_member(tmp_path):
    feed, _ = feed_of(tmp_path, total=10)
    now = now_ms()
    r = S.fetch(feed, 'C/USDT', '4h', now)
    event = capture(scan_frames(['A/USDT', 'B/USDT'], now), ['A/USDT', 'B/USDT'],
                    's', settings(), now, r)
    assert {'symbol': 'C/USDT', 'reason': 'supplemental_warmup',
            'detail': 'insufficient_history'} in event['issues']
    assert all(m['symbol'] != 'C/USDT' for m in event['input']['membership'])
    assert all(c['symbol'] != 'C/USDT' for c in event['input']['candles'])
    assert event['scope']['supplemental']['account_eligibility'] == 'UNKNOWN'


def test_supplemental_that_is_a_scan_member_is_refused(tmp_path):
    feed, _ = feed_of(tmp_path, total=60)
    now = now_ms()
    r = S.fetch(feed, 'A/USDT', '4h', now)
    with pytest.raises(ValueError):
        capture(scan_frames(['A/USDT']), ['A/USDT'], 's', settings(), now, r)


# ── kernel separation ─────────────────────────────────────────────────────

def kernel(tmp_path, extra, feed):
    from trader.kernel import Kernel
    from trader.core.types import Decision, Action, MarketType, ControlState
    k = object.__new__(Kernel)
    k.cfg = {'timeframes': {'execution': '15m'}}
    k.population = []
    k.state_machine = NS(refresh=lambda: ControlState.ACTIVE)
    k._fetch_balance = lambda: 1000
    k.risk = NS(update_equity=lambda _: {'equity': 1000, 'drawdown_pct': 0}, daily_loss_block=.05)
    k._drain_close_requests = lambda: 0
    k.journal = NS(kv_get=lambda key, default=None: default, kv_set=lambda *a: None,
                   query=lambda *a: [{'n': 0}], update_decision_outcome=Mock(),
                   open_trades=lambda: [], log_equity=Mock())
    k.macro_guard = k.news_guard = NS(check=lambda: {'active': False})
    k.market_type = MarketType.SPOT
    k.executor = NS(recovery_pending=lambda: False, recover_entries=Mock())
    k._funding_map = k._oi_map = lambda: {}
    k._refresh_btc_context = lambda: None
    k._scan_symbols = lambda: ['A/USDT', 'B/USDT']
    data = scan_frames(['A/USDT', 'B/USDT'])
    k._universe_frames = Mock(return_value=data)
    k._snapshot_for = Mock(side_effect=lambda s, **kw: NS(symbol=s, price=100))
    k.positioning_agent = k.depth_agent = NS(set_context=Mock())
    k._order_book = Mock(return_value={})
    decided = []

    def decide(snap, *a, **kw):
        d = Decision('d' + snap.symbol, 'c', snap.symbol, Action.BUY, .7, .2, .8, [], [])
        decided.append(d)
        return d
    k.orchestrator = NS(decide=Mock(side_effect=decide), journalize=Mock())
    k._try_enter = Mock(return_value=True)
    k._detect_exchange_exits = Mock(return_value=0)
    k._manages_exits = lambda: True
    k._manage_one = Mock(return_value=None)
    k._manage_orphan_positions = Mock(return_value=0)
    k._maybe_resolve_outcomes = Mock()
    k.heartbeat = NS(beat=Mock())
    k.notifier = NS(send=Mock())
    # the kernel's shared DataFeed must never serve the supplemental symbol
    k.feed = Mock()
    k._attention_source = feed
    k._attention = Collector(tmp_path, {}, start=False)
    k._attention_extra = extra
    return k, data, decided


def trading_calls(k, decided):
    return {'universe': [c.args for c in k._universe_frames.call_args_list],
            'snap': [c.args for c in k._snapshot_for.call_args_list],
            'decide': [c.args[0].symbol for c in k.orchestrator.decide.call_args_list],
            'journalize': [c.args[0].symbol for c in k.orchestrator.journalize.call_args_list],
            'enter': [c.args[0].symbol for c in k._try_enter.call_args_list],
            'context': [c.args[0] for c in k.positioning_agent.set_context.call_args_list],
            'book': [c.args for c in k._order_book.call_args_list],
            'exits': [c.args for c in k._detect_exchange_exits.call_args_list],
            'orphans': [c.args for c in k._manage_orphan_positions.call_args_list],
            'decisions': [d.symbol for d in decided]}


def test_kernel_without_supplemental_is_unchanged(tmp_path):
    feed = Mock()
    k, data, decided = kernel(tmp_path, None, feed)
    k._attention.begin = Mock(wraps=k._attention.begin)
    stats = k.cycle()
    assert feed.method_calls == [] and k.feed.method_calls == []
    k._attention.begin.assert_called_once_with(data, ['A/USDT', 'B/USDT'])
    assert 'attention_supplemental' not in stats
    event = k._attention.queue.get_nowait()
    assert 'supplemental' not in event['scope']


def test_kernel_supplemental_is_observed_but_never_traded(tmp_path):
    (tmp_path / 'base').mkdir(); (tmp_path / 'extra').mkdir(); (tmp_path / 'db').mkdir()
    base, base_data, base_decided = kernel(tmp_path / 'base', None, Mock())
    base_stats = base.cycle()
    feed, ex = feed_of(tmp_path / 'db', total=60)
    k, data, decided = kernel(tmp_path / 'extra', 'C/USDT', feed)
    stats = k.cycle()
    # exactly one bounded attempt, for C only
    assert [c[0] for c in ex.calls] == ['C/USDT'] and ex.calls[0][3] == 30
    assert k.feed.method_calls == []
    assert stats.pop('attention_supplemental') == {
        'symbol': 'C/USDT', 'status': 'usable', 'reason': 'complete_window'}
    # trading path identical to the no-supplemental run; C appears nowhere
    assert trading_calls(k, decided) == trading_calls(base, base_decided)
    assert 'C/USDT' not in repr(trading_calls(k, decided))
    assert 'C/USDT' not in data and 'C/USDT' not in k._scan_symbols()
    for key in ('scanned', 'decisions', 'entries', 'skips'):
        assert stats[key] == base_stats[key]
    # ...and C reached Attention capture as an observation-only member
    event = k._attention.queue.get_nowait()
    assert event['scope']['supplemental']['symbol'] == 'C/USDT'
    assert any(m['symbol'] == 'C/USDT' and m['source'] == 'attention.supplemental'
               for m in event['input']['membership'])


@pytest.mark.parametrize('failure', ['feed_raises', 'fetch_raises', 'collector_raises'])
def test_kernel_supplemental_failure_does_not_fail_trading(tmp_path, monkeypatch, failure):
    feed = S.IsolatedSource(2)
    feed.acquire = Mock(side_effect=OSError('venue down'))
    k, data, decided = kernel(tmp_path, 'C/USDT', feed)
    if failure == 'fetch_raises':
        monkeypatch.setattr(S, 'fetch', Mock(side_effect=RuntimeError('bug')))
    if failure == 'collector_raises':
        k._attention.begin = Mock(side_effect=OSError('SECRET'))
    stats = k.cycle()
    assert stats['scanned'] == stats['decisions'] == 2
    assert stats['entries'] == 2
    assert [c.args[0].symbol for c in k._try_enter.call_args_list] == ['A/USDT', 'B/USDT']
    if failure == 'feed_raises':
        assert stats['attention_supplemental']['status'] == 'error'
        assert feed.acquire.call_count == 1


def test_kernel_skips_supplemental_already_in_scan(tmp_path):
    feed = Mock()
    k, data, decided = kernel(tmp_path, 'A/USDT', feed)
    stats = k.cycle()
    assert feed.method_calls == [] and 'attention_supplemental' not in stats


# ── isolated, killable acquisition; all transport is mocked ───────────────

import io
import json
import os
import sys
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from trader.observability import _supplemental_child as child
from trader.observability.supplemental import IsolatedSource


def klines(total=30):
    newest = now_ms() // TF * TF
    rows = []
    for i, t in enumerate(newest - j * TF for j in reversed(range(total))):
        c = 100 + i % 5
        rows.append([t, str(c), str(c + 1), str(c - 1), str(c + .5), '10',
                     t + TF - 1, '1000', 5, '4', '400', '0'])
    return rows


def child_script(tmp_path, extra='', *, rows=True):
    path = tmp_path / 'child.py'
    body = '''import json, sys, time
url, symbol, tf, limit, timeout, max_bytes = sys.argv[1:]
started = time.time_ns() // 1000000
'''
    if rows:
        body += '''tf_ms = 14400000
newest = started // tf_ms * tf_ms
rows = []
for i, t in enumerate(newest - j * tf_ms for j in reversed(range(int(limit)))):
    c = 100 + i % 5
    rows.append([t, str(c), str(c+1), str(c-1), str(c+.5), '10', t+tf_ms-1, '1000', 5, '4', '400', '0'])
'''
    body += extra
    if rows:
        body += '''print(json.dumps({'symbol': symbol, 'interval': tf, 'limit': int(limit),
 'request_start_ms': started, 'request_end_ms': time.time_ns() // 1000000,
 'rows': rows}))
'''
    path.write_text(body)
    return path


@pytest.mark.parametrize('bad', [0, -1, 31, True, '5', None])
def test_isolated_timeout_must_be_explicit_and_bounded(bad):
    with pytest.raises(ValueError):
        IsolatedSource(bad)


def test_public_endpoint_and_one_request_with_no_redirect(monkeypatch):
    assert S.KLINES_URL == 'https://fapi.binance.com/fapi/v1/klines'
    calls = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n): return json.dumps(klines()).encode()

    class Opener:
        def open(self, url, timeout):
            calls.append((url, timeout))
            return Response()

    handlers = []
    def build(*args):
        handlers.extend(args)
        return Opener()
    monkeypatch.setattr(child.urllib.request, 'build_opener', build)
    kind, rows = child._request(S.KLINES_URL, 'CUSDT', '4h', 30, 2, S.MAX_BYTES)
    assert kind == 'rows' and len(rows) == 30 and len(calls) == 1
    assert urlparse(calls[0][0]).path == '/fapi/v1/klines'
    assert '/private/' not in calls[0][0] and '/account' not in calls[0][0]
    assert parse_qs(urlparse(calls[0][0]).query) == {
        'symbol': ['CUSDT'], 'interval': ['4h'], 'limit': ['30']}
    assert any(isinstance(h, child._NoRedirect) for h in handlers)
    assert not any('Auth' in type(h).__name__ for h in handlers)
    assert child._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other') is None
    assert kind != 'error'


def test_redirect_is_error_without_second_request(monkeypatch):
    calls = []
    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            raise HTTPError(url, 302, 'redirect', {}, None)
    monkeypatch.setattr(child.urllib.request, 'build_opener', lambda *a: Opener())
    assert child._request(S.KLINES_URL, 'CUSDT', '4h', 30, 2, S.MAX_BYTES) == ('error', 'http_302')
    assert len(calls) == 1


def test_isolated_success_is_bounded_and_reaped(tmp_path):
    src = IsolatedSource(2, child=child_script(tmp_path))
    r = S.fetch(src, 'C/USDT:USDT', '4h', now_ms())
    assert (r.status, r.reason, len(r.frame)) == (S.USABLE, 'complete_window', NEED)
    assert r.requested_limit == 30 and src._proc is None


def test_one_child_issues_one_mocked_http_request(tmp_path, monkeypatch):
    count = tmp_path / 'http_count'
    script = tmp_path / 'transport_stub.py'
    script.write_text(f'''import importlib.util, json, sys, time
spec = importlib.util.spec_from_file_location("worker", {str(S.CHILD)!r})
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n):
        now = time.time_ns() // 1000000
        tf = 14400000
        anchor = now // tf * tf
        rows = [[t, "100", "101", "99", "100", "10", t+tf-1,
                 "1000", 5, "4", "400", "0"]
                for t in (anchor - j*tf for j in reversed(range(30)))]
        return json.dumps(rows).encode()
class Opener:
    def open(self, url, timeout):
        with open({str(count)!r}, "a") as f: f.write("1")
        return Response()
worker.urllib.request.build_opener = lambda *handlers: Opener()
sys.exit(worker.main(sys.argv[1:]))
''')
    launched = []
    original = S.subprocess.Popen
    def record(*args, **kwargs):
        proc = original(*args, **kwargs)
        launched.append(proc)
        return proc
    monkeypatch.setattr(S.subprocess, 'Popen', record)
    result = S.fetch(IsolatedSource(2, child=script), 'C/USDT', '4h', now_ms())
    assert result.status == S.USABLE
    assert len(launched) == 1 and launched[0].poll() is not None
    assert count.read_text() == '1'


def test_schema_identity_count_and_closed_cut_validated_in_parent(tmp_path):
    good = klines()
    t = now_ms()
    message = lambda rows, **kw: {'symbol': 'CUSDT', 'interval': '4h', 'limit': 30,
        'request_start_ms': t, 'request_end_ms': t, 'rows': rows, **kw}
    parse = lambda m: S._parse(json.dumps(m).encode(), symbol='CUSDT', interval='4h',
                                 limit=30, started_ms=t, ended_ms=t)
    with pytest.raises(S.SupplementalFetchError, match='identity'):
        parse(message(good, symbol='WRONG'))
    with pytest.raises(S.SupplementalFetchError, match='row_count'):
        parse(message(good + good[:1]))
    bad = [list(r) for r in good]
    bad[0][1] = 'NaN'
    with pytest.raises(S.SupplementalFetchError, match='row_value'):
        parse(message(bad))
    df = parse(message(good))
    assert len(df) == 30
    source = S.IsolatedSource(2)
    source.acquire = Mock(return_value=df)
    result = S.fetch(source, 'C/USDT', '4h', t)
    assert result.status == S.USABLE
    assert all(int(x.timestamp() * 1000) + TF <= t for x in result.frame.ts)
    assert len(result.frame) == NEED


def test_timeout_terminates_and_joins_before_return(tmp_path):
    marker = tmp_path / 'late_write'
    script = child_script(tmp_path,
        extra=f'''import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(1.2)
open({str(marker)!r}, 'w').write('late')
''')
    src = IsolatedSource(.25, child=script)
    launched = []
    original = S.subprocess.Popen
    def record(*args, **kwargs):
        proc = original(*args, **kwargs)
        launched.append(proc)
        return proc
    from unittest.mock import patch
    start = time.monotonic()
    with patch.object(S.subprocess, 'Popen', side_effect=record):
        r = S.fetch(src, 'C/USDT', '4h', now_ms())
    assert (r.status, r.reason) == (S.ERROR, 'fetch_timeout')
    assert time.monotonic() - start < src.bound_s + .3
    assert len(launched) == 1 and launched[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(launched[0].pid, 0)
    assert src._proc is None
    time.sleep(1.3)
    assert not marker.exists()


def test_spawn_time_is_inside_deadline(tmp_path, monkeypatch):
    script = child_script(tmp_path)
    original = S.subprocess.Popen
    launched = []
    def slow_spawn(*args, **kwargs):
        time.sleep(.25)
        proc = original(*args, **kwargs)
        launched.append(proc)
        return proc
    monkeypatch.setattr(S.subprocess, 'Popen', slow_spawn)
    src = IsolatedSource(.1, child=script)
    r = S.fetch(src, 'C/USDT', '4h', now_ms())
    assert r.reason == 'fetch_timeout'
    assert len(launched) == 1 and launched[0].poll() is not None
    assert src._proc is None


def test_child_has_no_store_or_credential_access(tmp_path, monkeypatch):
    seen = tmp_path / 'env.json'
    script = child_script(tmp_path, extra=f'''import os
open({str(seen)!r}, 'w').write(json.dumps(sorted(os.environ)))
''')
    monkeypatch.setenv('BINANCE_API_KEY', 'SECRET')
    monkeypatch.setenv('BINANCE_API_SECRET', 'SECRET')
    r = S.fetch(IsolatedSource(2, child=script), 'C/USDT', '4h', now_ms())
    assert r.status == S.USABLE
    assert not any('BINANCE' in k for k in json.loads(seen.read_text()))
    import ast
    tree = ast.parse(S.CHILD.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    modules = {n.name for n in ast.walk(tree) if isinstance(n, ast.Import)
               for n in n.names}
    assert 'DataFeed' not in names and 'sqlite3' not in modules and 'ccxt' not in modules
    assert 'load_markets' not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert 'open' not in names  # no built-in file open; only opener.open for HTTP
    assert not (tmp_path / 'candles.db').exists()


def test_single_flight_refuses_second_child(tmp_path):
    started = tmp_path / 'started'
    script = child_script(tmp_path, extra=f'''open({str(started)!r}, 'w').write('started')
time.sleep(1)
''')
    src1, src2 = IsolatedSource(.3, child=script), IsolatedSource(.3, child=script)
    results = []
    thread = threading.Thread(target=lambda: results.append(S.fetch(src1, 'C/USDT', '4h', now_ms())))
    thread.start()
    for _ in range(50):
        if started.exists(): break
        time.sleep(.01)
    assert started.exists()
    refused = S.fetch(src2, 'D/USDT', '4h', now_ms())
    assert (refused.status, refused.reason) == (S.ERROR, 'fetch_in_flight')
    thread.join(timeout=2)
    assert not thread.is_alive() and results[0].reason == 'fetch_timeout'
    assert src1._proc is None and src2._proc is None


def test_kernel_cycle_timeout_is_nonfatal_and_cannot_late_mutate(tmp_path):
    (tmp_path / 'base').mkdir(); (tmp_path / 'extra').mkdir()
    base, _, base_decided = kernel(tmp_path / 'base', None, Mock())
    base_stats = base.cycle()
    marker = tmp_path / 'late_write'
    script = child_script(tmp_path, extra=f'''time.sleep(1)
open({str(marker)!r}, 'w').write('late')
''')
    src = IsolatedSource(.2, child=script)
    k, data, decided = kernel(tmp_path / 'extra', 'C/USDT', src)
    stats = k.cycle()
    assert stats.pop('attention_supplemental') == {
        'symbol': 'C/USDT', 'status': 'error', 'reason': 'fetch_timeout'}
    assert trading_calls(k, decided) == trading_calls(base, base_decided)
    assert stats['scanned'] == stats['entries'] == 2
    event = k._attention.queue.get_nowait()
    assert {'symbol': 'C/USDT', 'reason': 'supplemental_error',
            'detail': 'fetch_timeout'} in event['issues']
    while not k._attention.queue.empty():
        k._attention.queue.get_nowait()
    time.sleep(1.1)
    assert not marker.exists() and src._proc is None
    assert k.feed.method_calls == [] and k._attention.queue.empty()
