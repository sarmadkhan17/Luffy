"""Offline queue invariants for contained candle acquisition."""
import threading
from types import SimpleNamespace

from tests.test_feed_pagination import FakeEx
from trader.data.feed import DataFeed


def _queued_feed(tmp_path, monkeypatch, exchange=None):
    tasks = []
    monkeypatch.setattr('trader.data.feed.threading.Thread', lambda **kw:
                        SimpleNamespace(start=lambda: tasks.append(kw)))
    feed = DataFeed(exchange=exchange or FakeEx(), db_path=tmp_path/'queued.db')
    feed.background_acquisition = True
    assert feed._acquisition_lock.acquire(False)
    return feed, tasks


def test_default_synchronous_path_remains_immediate(tmp_path):
    ex = FakeEx()
    feed = DataFeed(exchange=ex, db_path=tmp_path/'sync.db')
    frame = feed.fetch_ohlcv('BTC/USDT', '15m')
    assert frame is not None
    assert ex.calls
    assert feed.background_acquisition_health()['pending'] == 0


def test_busy_worker_retains_cold_timeframes_in_fifo_order(tmp_path, monkeypatch):
    ex = FakeEx()
    feed, tasks = _queued_feed(tmp_path, monkeypatch, ex)
    for tf in ('1h', '4h', '15m'):
        assert feed.fetch_ohlcv('BTC/USDT', tf) is None
    assert feed.background_acquisition_health()['pending'] == 3
    feed._acquisition_lock.release()
    assert feed._start_background_worker()
    tasks.pop()['target']()
    assert [call[1] for call in ex.calls] == ['1h', '4h', '15m']
    assert feed.background_acquisition_health()['pending'] == 0


def test_duplicate_requests_coalesce_without_losing_parameters(tmp_path, monkeypatch):
    feed, _ = _queued_feed(tmp_path, monkeypatch)
    assert feed._queue_background_request('BTC/USDT', '1h', 100, False, 20)
    assert feed._queue_background_request('BTC/USDT:USDT', '1h', 25000, True, 50)
    assert list(feed._acquisition_queue.values()) == [
        {'symbol': 'BTC/USDT', 'tf': '1h', 'limit': 20000,
         'force': True, 'min_bars': 50}]
    assert feed.background_acquisition_health()['coalesced'] == 1
    feed._acquisition_lock.release()


def test_queue_capacity_is_hard_and_refusal_explicit(tmp_path, monkeypatch):
    assert DataFeed.BACKGROUND_QUEUE_CAPACITY == 128
    feed, _ = _queued_feed(tmp_path, monkeypatch)
    feed.BACKGROUND_QUEUE_CAPACITY = 2
    for symbol in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
        feed.fetch_ohlcv(symbol, '1h')
    health = feed.background_acquisition_health()
    assert health['pending'] == health['capacity'] == 2
    assert health['refused_full'] == 1
    assert health['last_outcome'] == 'refused_queue_full'
    feed._acquisition_lock.release()


def test_finite_work_progresses_in_fifo_order(tmp_path, monkeypatch):
    ex = FakeEx()
    feed, tasks = _queued_feed(tmp_path, monkeypatch, ex)
    for symbol in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
        feed.fetch_ohlcv(symbol, '15m')
    feed._acquisition_lock.release()
    feed._start_background_worker()
    tasks.pop()['target']()
    assert [call[0] for call in ex.calls] == ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    assert feed.background_acquisition_health()['completed'] == 3


def test_worker_skips_data_that_became_fresh(tmp_path, monkeypatch):
    ex = FakeEx()
    feed, tasks = _queued_feed(tmp_path, monkeypatch, ex)
    feed.fetch_ohlcv('BTC/USDT', '15m')
    feed._acquisition_lock.release()
    warm = DataFeed(exchange=FakeEx(), db_path=tmp_path/'queued.db')
    assert len(warm.fetch_ohlcv('BTC/USDT', '15m', limit=400)) == 400
    feed._start_background_worker()
    tasks.pop()['target']()
    assert ex.calls == []
    assert feed.background_acquisition_health()['skipped_fresh'] == 1


def test_failure_does_not_strand_later_work_or_lock(tmp_path, monkeypatch):
    ex = FakeEx()
    calls = []
    def sometimes_fails(symbol, tf, since=None, limit=None):
        calls.append(symbol)
        if symbol == 'BTC/USDT':
            raise TimeoutError('offline fixture')
        return FakeEx().fetch_ohlcv(symbol, tf, since, limit)
    ex.fetch_ohlcv = sometimes_fails
    feed, tasks = _queued_feed(tmp_path, monkeypatch, ex)
    feed.fetch_ohlcv('BTC/USDT', '15m')
    feed.fetch_ohlcv('ETH/USDT', '15m')
    feed._acquisition_lock.release()
    feed._start_background_worker()
    tasks.pop()['target']()
    assert calls == ['BTC/USDT', 'ETH/USDT']
    assert feed.background_acquisition_health()['failed'] == 1
    assert feed.background_acquisition_health()['completed'] == 1
    assert not feed._acquisition_lock.locked()


def test_provider_calls_remain_serialized(tmp_path, monkeypatch):
    active = maximum = 0
    guard = threading.Lock()
    ex = FakeEx()
    original = ex.fetch_ohlcv
    def tracked(*args, **kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        try:
            return original(*args, **kwargs)
        finally:
            with guard:
                active -= 1
    ex.fetch_ohlcv = tracked
    feed, tasks = _queued_feed(tmp_path, monkeypatch, ex)
    for symbol in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
        feed.fetch_ohlcv(symbol, '15m')
    feed._acquisition_lock.release()
    feed._start_background_worker()
    tasks.pop()['target']()
    assert maximum == 1


def test_shutdown_cancels_queue_without_delaying_position_service(tmp_path, monkeypatch):
    feed, _ = _queued_feed(tmp_path, monkeypatch)
    feed.fetch_ohlcv('BTC/USDT', '15m')
    from trader.kernel import Kernel
    kernel = Kernel.__new__(Kernel)
    kernel.journal = SimpleNamespace(open_trades=lambda: [])
    kernel._exposure_symbols = lambda: ('BTC/USDT',)
    serviced = []
    kernel._detect_exchange_exits = lambda symbol: serviced.append(symbol) or 0
    kernel._snapshot_for = lambda symbol, universe: feed.fetch_ohlcv(symbol, '15m')
    kernel._portfolio_market = {}
    kernel._portfolio_holdings_market = {}
    kernel._service_exposure(set(), {})
    assert serviced == ['BTC/USDT']
    kernel.feed = feed
    kernel._attention_call = lambda *args: None
    kernel._stop = False
    kernel._graceful(15, None)
    assert kernel._stop is True
    assert feed.background_acquisition_health()['cancelled'] == 1
    feed._acquisition_lock.release()
    assert not feed._queue_background_request('ETH/USDT', '15m', 400, False, 30)


def test_queue_preserves_provenance_closed_bars_and_offline_replay(tmp_path, monkeypatch):
    ex = FakeEx()
    feed, tasks = _queued_feed(tmp_path, monkeypatch, ex)
    feed.fetch_ohlcv('BTC/USDT', '15m', limit=100)
    feed._acquisition_lock.release()
    feed._start_background_worker()
    tasks.pop()['target']()
    asof = feed._clock_ms()
    current = feed.ohlcv_asof('BTC/USDT', '15m', as_of_ms=asof, limit=100)
    assert current is not None and len(current) == 100
    assert current['source'].notna().all()
    assert current['request_started_ms'].notna().all()
    assert current['available_at_ms'].notna().all()
    assert current['bar_state'].eq('FINAL').all()
    replay = feed.replay_ohlcv('BTC/USDT', '15m', limit=100)
    # only the newest bar was received inside its own window; the 99 backfills stay retrospective
    assert len(replay) == 1 and replay['ts'].iloc[0] == current['ts'].iloc[-1]
    assert replay['available_at_ms'].iloc[0] == current['available_at_ms'].iloc[-1]
    calls = len(ex.calls)
    assert feed.cached_ohlcv('BTC/USDT', '15m', limit=100, as_of_ms=asof) is not None
    assert len(ex.calls) == calls


def test_paging_stops_when_provider_does_not_advance(tmp_path):
    ex = FakeEx()
    batch = ex.fetch_ohlcv('BTC/USDT', '15m', limit=1000)
    calls = []
    def repeated(*a, **kw):
        calls.append(kw)
        return batch
    ex.fetch_ohlcv = repeated
    feed = DataFeed(exchange=ex, db_path=tmp_path/'candles.db')
    feed._fetch_paged('BTC/USDT', '15m', 20000)
    assert len(calls) <= 2
