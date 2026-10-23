"""DA-05 closure: retained evidence and explicitly synthetic acquisition probes.

No provider, Kernel boot, strategy admission, order or trading permission is
involved. The deterministic venue is a code-path fixture, not market coverage.
"""
from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import yaml

from trader import attention_admission as A
from trader.core.types import TF_MS, MarketType
from trader.data import market_provenance as P
from trader.data.attention_history import decision_state
from trader.data.feed import DataFeed
from trader.engine.orchestrator import symbol_allows
from trader.learning.capture_runtime import restore_frame, exact_frame
from trader.learning.foundation import digest
from trader.observability.attention import evaluate_snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec
from tests.test_compile import _spec
from tests.test_da05_history_coverage import scheduler, drain
from tests.test_hierarchical_admission import broad_kernel, CUT


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    denied = Mock(side_effect=AssertionError('offline DA05 closure'))
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr('trader.data.feed.make_exchange', denied)


class Venue:
    """Finite synthetic source with real since/limit semantics and no transport."""
    id = 'binanceusdm'
    urls = {'api': {'fapiPublic': 'https://offline.invalid'}}

    def __init__(self, count=1600):
        self.now = CUT
        self.count = count
        self.calls = []
        self.unavailable = set()

    def market(self, symbol):
        return dict(id=symbol.split(':')[0].replace('/', ''), contract=True)

    def fetch_ohlcv(self, symbol, tf, since=None, limit=1000):
        self.calls.append((symbol, tf, since, limit))
        if tf in self.unavailable:
            raise TimeoutError('synthetic source unavailable')
        step = TF_MS[tf]
        end = self.now // step * step
        rows = []
        for i in range(self.count):
            price = 100 + i * .3
            rows.append([end - (self.count-i)*step, price, price+2,
                         price-2, price+.2, 1000+i*3])
        return ([r for r in rows if r[0] >= since][:limit]
                if since is not None else rows[-limit:])


@pytest.mark.parametrize('initial,available', [(246,1600),(250,250)])
def test_legacy_floor_hint_is_rechecked_once_without_losing_evidence(tmp_path, initial, available):
    path = tmp_path/'candles.db'
    first = (CUT//TF_MS['4h']-initial)*TF_MS['4h']
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE candle_floor(symbol TEXT,tf TEXT,first_ts INTEGER,PRIMARY KEY(symbol,tf))')
        db.execute('INSERT INTO candle_floor VALUES (?,?,?)',('S00/USDT','4h',first))
    v = Venue(available)
    f = DataFeed(exchange=v,db_path=path,clock_ms=lambda:v.now)
    try:
        # Retain exact received rows and the old optimization hint. Neither
        # the clock nor a schema upgrade invents the missing source window.
        raw = v.fetch_ohlcv('S00/USDT','4h',limit=initial)
        f._merge_save('S00/USDT','4h',None,f._received_frame(raw,'S00/USDT','4h'))
        before = {r[0] for r in f.db.execute('SELECT revision_id FROM market_revisions')}
        assert f._floor('S00/USDT','4h') is None
        assert f.db.execute('SELECT first_ts FROM candle_floor').fetchone()[0] == first
        result = f.fetch_ohlcv('S00/USDT','4h',400,min_bars=26)
        assert len(result) == min(available,400)
        assert before <= {r[0] for r in f.db.execute('SELECT revision_id FROM market_revisions')}
        assert len(v.calls) == 2 and v.calls[-1][3] == 1000
        requests = len(v.calls)
    finally: f.db.close()
    restarted = DataFeed(exchange=v,db_path=path,clock_ms=lambda:v.now)
    try:
        same = restarted.fetch_ohlcv('S00/USDT','4h',400,min_bars=26)
        assert same.revision_id.tolist() == result.revision_id.tolist()
        assert len(v.calls) == requests
    finally: restarted.db.close()


def test_legacy_floor_upgrade_is_serialized_for_concurrent_openers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    path = tmp_path/'candles.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE candle_floor(symbol TEXT,tf TEXT,first_ts INTEGER,PRIMARY KEY(symbol,tf))')
        db.execute('INSERT INTO candle_floor VALUES (?,?,?)',('S00/USDT','4h',123))
    start = threading.Barrier(2)
    def open_store(_):
        f = DataFeed(exchange=Venue(),db_path=path)
        start.wait(timeout=10)
        try:
            assert 'window_version' in {r[1] for r in f.db.execute('PRAGMA table_info(candle_floor)')}
            assert f._floor('S00/USDT','4h') is None
            assert not f.db.in_transaction
        finally: f.db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(open_store,range(2)))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT symbol,tf,first_ts FROM candle_floor').fetchall() == [('S00/USDT','4h',123)]


@pytest.mark.parametrize('initial,target,available,tf', [
    (154,400,1600,'4h'), (300,400,1600,'4h'),
    (399,400,1600,'4h'), (154,1000,1600,'4h'),
    (154,400,250,'4h'), (154,400,1600,'1h'),
    (154,1500,1600,'4h'),  # existing paged expansion is a positive control
    (154,154,1600,'4h'),   # existing fresh reuse is a positive control
])
def test_expand_qualified_attention_window_to_requested_depth(tmp_path, initial, target, available, tf):
    v = Venue(available)
    path = tmp_path/'candles.db'
    f = DataFeed(exchange=v, db_path=path, clock_ms=lambda:v.now)
    try:
        before = f.fetch_ohlcv('S00/USDT', tf, initial, min_bars=26)
        old_ids = set(before.revision_id)
        result = f.fetch_ohlcv('S00/USDT', tf, target, min_bars=26)
        expected = min(target, available)
        assert len(result) == expected
        assert result.quality.eq('VALID').all()
        assert old_ids <= {r[0] for r in f.db.execute('SELECT revision_id FROM market_revisions')}
        assert f._contiguous_tail(result, tf, expected)
        floor = f._floor('S00/USDT', tf)
        assert floor == (None if available >= target else int(P.ms(result.ts)[0]))
        requests = len(v.calls)
    finally:
        f.db.close()
    restarted = DataFeed(exchange=v, db_path=path, clock_ms=lambda:v.now)
    try:
        same = restarted.fetch_ohlcv('S00/USDT', tf, target, min_bars=26)
        assert same.revision_id.tolist() == result.revision_id.tolist()
        assert len(v.calls) == requests
        assert max(call[3] for call in v.calls) <= 1000
        assert len(v.calls) <= 3
    finally:
        restarted.db.close()


@pytest.mark.parametrize('minimum', [10,26,30])
def test_failed_refresh_preserves_minimum_and_retains_short_history(tmp_path, minimum):
    v = Venue(20)
    f = DataFeed(exchange=v,db_path=tmp_path/'candles.db',clock_ms=lambda:v.now)
    try:
        assert f.fetch_ohlcv('S00/USDT','4h',400,min_bars=26) is None
        original = f.db.execute('SELECT revision_id FROM market_revisions ORDER BY revision_id').fetchall()
        v.unavailable.add('4h')
        result = f.fetch_ohlcv('S00/USDT','4h',400,min_bars=minimum,force=True)
        assert (result is None) == (minimum > 20)
        if result is not None: assert len(result) == 20
        assert f.db.execute('SELECT revision_id FROM market_revisions ORDER BY revision_id').fetchall() == original
        assert f.db.execute('SELECT count(*) FROM candles').fetchone()[0] == 20
    finally:
        f.db.close()


def kernel(tmp_path, monkeypatch, v):
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    # Historical history-ranking contract; production now uses volume selection.
    cfg['attention'].pop('selection', None)
    cfg['attention']['admission'] = dict(max_deep_slots=12,event_reserve=2,exploration_reserve=2,minimum_slots=0)
    k = broad_kernel(tmp_path, cfg, n=4)
    k.feed = DataFeed(exchange=v, db_path=tmp_path/'candles.db', clock_ms=lambda:v.now)
    k.market_type = MarketType.FUTURES
    k._derivs_for = lambda *a, **kw: None
    k._market_for = lambda **kw: None
    k._exposure_symbols = lambda: ()
    sp = _spec(id='da05-readiness-probe', timeframe='4h',
        universe={'include':['S00/USDT'], 'required_evaluation':['S00/USDT']},
        entry_long='close > ema(300)', filters=[], regime_filter=[])
    k._spec_rows = [({}, sp)]
    monkeypatch.setattr('trader.kernel.time.time', lambda:v.now/1000)
    return k, compile_spec(sp)


def test_admission_warmup_deep_acquisition_and_existing_evaluator(tmp_path, monkeypatch):
    v = Venue()
    k, compiled = kernel(tmp_path, monkeypatch, v)
    tasks = scheduler(monkeypatch)
    f = k.feed
    try:
        f.fetch_ohlcv('S00/USDT','4h',154,min_bars=26)
        f.background_acquisition = True
        f._acquisition_lock.acquire()
        receipt, _ = k._admission_plan()
        assert receipt and 'S00/USDT' in receipt['admitted_symbols']
        assert len(receipt['observation']['rows']) == 4
        assert receipt['history_coverage']['counts']['decision_ready'] == 0
        assert receipt['capacity_used'] <= 12
        frozen = A.canonical(receipt)
        v.now += 1  # later acquisition must not qualify the frozen earlier cut
        assert k._candidate_snapshot_for('S00/USDT', receipt, {}) is None
        drain(f, tasks)
        snap = k._candidate_snapshot_for('S00/USDT', receipt, {})
        assert snap is not None and len(snap.dfs['4h']) == 400
        assert snap.admission_context['receipt_id'] == receipt['receipt_id']
        sig = compiled.to_evaluator()(compiled.spec, snap)
        assert sig is not None and sig.symbol == 'S00/USDT'
        assert sig.params['signal_timeframe'] == '4h'
        assert decision_state(snap, NS(id='test', strategy_signals=[vars(sig)]))['state'] == 'DECISION_READY'
        assert decision_state(snap, NS(id='test', strategy_signals=[]))['state'] == 'WARMING'
        assert len(f.cached_ohlcv('S00/USDT','4h',as_of_ms=CUT,limit=400)) == 154
        assert A.canonical(A.latest(k.journal)) == frozen
        assert A.canonical(A.verify_receipt(json.loads(frozen))) == frozen
        assert len(f._acquisition_queue) <= f.BACKGROUND_QUEUE_CAPACITY
        assert k._history_preparation['submitted'] <= 16
        assert max(call[3] for call in v.calls) <= 1000
    finally:
        f.cancel_background_acquisition()
        if f._acquisition_lock.locked(): f._acquisition_lock.release()
        f.db.close()
        k.journal._conn().close()


@pytest.mark.parametrize('case', ['unavailable','short','stale'])
def test_missing_four_hour_evidence_never_becomes_ready(tmp_path, monkeypatch, case):
    v = Venue(20 if case == 'short' else 1600)
    k, compiled = kernel(tmp_path, monkeypatch, v)
    try:
        if case == 'stale':
            k.feed.fetch_ohlcv('S00/USDT','4h',400,min_bars=26)
            v.now += 1_800_001
        if case in ('stale','unavailable'): v.unavailable.add('4h')
        # Other intervals remain available. An execution snapshot cannot
        # stand in for a missing declared strategy timeframe.
        if case == 'short':
            k.feed.fetch_ohlcv('S00/USDT','4h',400,min_bars=26)
            v.count = 1600
            v.unavailable.add('4h')
        snap = k._snapshot_for('S00/USDT')
        assert snap is not None and '4h' not in snap.dfs
        reasons = []
        assert compiled.to_evaluator()(compiled.spec, snap,
            diagnostic=lambda reason, *args:reasons.append(reason)) is None
        assert reasons == ['missing_closed_timeframe']
        assert decision_state(snap, NS(id='test', strategy_signals=[]))['state'] == 'WARMING'
    finally:
        k.feed.db.close()
        k.journal._conn().close()


def test_retained_event_2338_replays_without_promoting_coverage():
    original = Path('tests/fixtures/da01-event2338.json.gz').read_bytes()
    retained = json.loads(gzip.decompress(original))
    result = evaluate_snapshot(retained['observation']['attention_event'])
    assert {r['symbol']:r for r in result['rows']} == retained['observation']['salience_rows']
    assert Counter(r['status'] for r in result['rows']) == {'ok':5,'missing':407,'stale':27}
    assert A.canonical(A.verify_receipt(deepcopy(retained))) == A.canonical(retained)
    assert Path('tests/fixtures/da01-event2338.json.gz').read_bytes() == original


def test_retained_da06_snapshots_do_not_establish_missing_strategy_history():
    fixture = json.loads(gzip.decompress(Path('reports/da10-full/historical-sources.json.gz').read_bytes()))
    assert all(digest(raw) == sha for sha,raw in fixture['blobs'].items())
    original = json.loads(gzip.decompress(Path('tests/fixtures/da01-event2338.json.gz').read_bytes()))
    spec = StrategySpec.from_dict(original['observation']['strategy_specs'][0])
    compiled = compile_spec(spec)
    short, complete, allowed = [], [], []
    for entry in fixture['entries']:
        data = fixture['blobs'][entry['data_sha']]
        raw = fixture['blobs'][data['chunks']['dfs']['4h']]
        frame = restore_frame(raw)
        assert digest(exact_frame(frame)) == digest(raw)
        safe, reason = P.current_frame_status(frame,'4h',data['cutoff_ms'],1800)
        assert reason == 'valid' and len(safe) == len(frame)
        if len(safe) < 101:
            short.append(data['symbol'])
            lo, sh = compiled.entries({'4h':safe}, symbol=data['symbol'])
            assert not lo.any() and not sh.any()
        else:
            complete.append(data['symbol'])
        if symbol_allows(NS(symbols=spec.universe['include']),data['symbol']):
            allowed.append(data['symbol'])
    assert len(short) == 15 and complete == ['RLC/USDT']
    assert len(allowed) == 13 and set(allowed) <= set(short)
    # This retained packet proves the historical gap, never present coverage.
    assert not set(allowed) & set(complete)
