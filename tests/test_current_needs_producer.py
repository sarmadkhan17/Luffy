"""Strategy-required derivative needs reach the current producer, bounded and sampled."""
import time

from trader.data import derivatives as D
from trader.data import market_provenance as M


class Resp:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


def install(monkeypatch, now_ms):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append((url.rsplit('/', 1)[-1], params['symbol']))
        if 'fundingRate' in url:
            return Resp([{'fundingTime': now_ms - 3_600_000 * k, 'fundingRate': '0.0001'} for k in range(5, 0, -1)])
        step = 900_000
        base = now_ms // step * step
        key = 'sumOpenInterest' if 'openInterest' in url else 'buySellRatio'
        return Resp([{'timestamp': base - step * k, key: '5'} for k in range(5, 0, -1)])
    monkeypatch.setattr(D.requests, 'get', get)
    return calls


def test_only_required_pairs_are_requested_once_per_source_period(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    calls = install(monkeypatch, now)
    feed = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    needs = [('BTC/USDT', 'funding'), ('ETH/USDT', 'oi'), ('ETH/USDT', 'ls_account_ratio')]
    out = feed.acquire_needs(needs, delay=0)
    assert out['requested'] == 2 and out['fetched'] == 2 and out['unsupported'] == ['ETH/USDT:ls_account_ratio']
    assert sorted(calls) == [('fundingRate', 'BTCUSDT'), ('openInterestHist', 'ETHUSDT')]   # nothing optional added
    assert feed.load('BTC/USDT', 'funding', as_of_ms=now) is not None
    again = feed.acquire_needs(needs, delay=0)                                       # same source period
    assert again['requested'] == 0 and again['skipped_fresh'] == 2 and len(calls) == 2
    later = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now + 16 * 60_000)
    assert later.acquire_needs(needs, delay=0)['requested'] == 2                    # next period boundary


def test_request_budget_defers_without_dropping_priority_order(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    calls = install(monkeypatch, now)
    feed = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    needs = [(f'C{i}/USDT', 'oi') for i in range(5)]
    out = feed.acquire_needs(needs, max_requests=2, delay=0)
    assert out['requested'] == 2 and out['deferred'] == 3
    assert [c[1] for c in calls] == ['C0USDT', 'C1USDT']             # priority order kept
    assert feed.acquire_needs(needs, max_requests=2, delay=0)['requested'] == 2   # next pass continues


def test_broad_recorder_acquisition_counts_as_fresh_for_needs(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    calls = install(monkeypatch, now)
    feed = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    feed.record_all(['BTC/USDT'], delay=0)
    n = len(calls)
    out = feed.acquire_needs([('BTC/USDT', 'oi'), ('BTC/USDT', 'funding')], delay=0)
    assert out['requested'] == 0 and len(calls) == n


def test_history_backfill_runs_in_resumable_slices_and_yields_at_boundaries(tmp_path, monkeypatch):
    feed = D.DerivFeed(tmp_path / 'd.db')
    done, yields = [], []
    monkeypatch.setattr(feed, 'funding_history', lambda sym, **k: done.append(sym) or feed._frame([]))
    monkeypatch.setattr(feed, 'basis_history', lambda sym, **k: feed._frame([]))
    for _, method in feed._SERIES_FETCHERS:
        monkeypatch.setattr(feed, method, lambda *a, **k: feed._frame([]))
    feed.history_yield = lambda: yields.append(1)
    symbols = ['A/USDT', 'B/USDT', 'C/USDT']
    feed.backfill_start, feed.backfill_budget_s = 0, 0.0
    feed.backfill(symbols, delay=0)
    assert done == ['A/USDT'] and feed.history_resume == 1 and yields          # one slice, then stop
    feed.backfill_start = feed.history_resume
    feed.backfill(symbols, delay=0)
    assert done == ['A/USDT', 'B/USDT'] and feed.history_resume == 2
    feed.backfill_start, feed.backfill_budget_s = 2, None
    feed.backfill(symbols, delay=0)
    assert done[-1] == 'C/USDT' and feed.history_resume is None


def test_required_lookback_pages_bounded_and_only_once_per_retry_window(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear(); D.DerivFeed._deepened_at.clear()
    now = int(time.time() * 1000)
    seen = []

    def get(url, params=None, headers=None, timeout=None):
        seen.append((url.rsplit('/', 1)[-1], dict(params)))
        if 'fundingRate' in url:
            start = params.get('startTime', now - 3_600_000 * 5)
            return Resp([{'fundingTime': start + 28_800_000 * k, 'fundingRate': '0.0001'}
                         for k in range(5) if start + 28_800_000 * k < now])
        step = {'15m': 900_000, '1h': 3_600_000, '4h': 14_400_000}[params['period']]
        base = now // step * step
        return Resp([{'timestamp': base - step * k, 'sumOpenInterest': '5'} for k in range(int(params['limit']), 0, -1)])
    monkeypatch.setattr(D.requests, 'get', get)
    feed = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    day = 86_400_000
    needs = [('BTC/USDT', 'funding', 20 * day), ('ETH/USDT', 'oi', 12 * day), ('SOL/USDT', 'oi', 2 * day)]
    out = feed.acquire_needs(needs, delay=0)
    funding = [p for n, p in seen if n == 'fundingRate']
    assert any(abs(p.get('startTime', 0) - (now - 20 * day)) < 1000 for p in funding)       # paged from the required start
    oi = [p for n, p in seen if n == 'openInterestHist' and p['symbol'] == 'ETHUSDT']
    assert {p['period'] for p in oi} == {'15m', '1h'}          # standard request + one coarser deepening for 12 days
    assert out['deepened'] == 2                                 # SOL's 2-day need is covered by the standard request
    before = len(seen)
    feed.acquire_needs(needs, delay=0)
    assert len(seen) == before                                  # same period + retry window: nothing re-requested
    out = feed.acquire_needs([('A/USDT', 'oi', 12 * day), ('B/USDT', 'oi', 12 * day), ('C/USDT', 'oi', 12 * day)],
                             delay=0, max_deepen=1)
    assert out['deepened'] == 1 and out['deepen_deferred'] == 2           # bounded per pass
