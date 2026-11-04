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


def test_required_window_keeps_15m_resolution_and_never_runs_history(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = 1_790_000_000_000 // 900_000 * 900_000
    seen = []
    def get(url, params=None, **kwargs):
        seen.append((url, dict(params)))
        if 'fundingRate' in url:
            step, tskey, valuekey = 28_800_000, 'fundingTime', 'fundingRate'
        else:
            assert params['period'] == '15m'
            step, tskey, valuekey = 900_000, 'timestamp', 'sumOpenInterest'
        first = (params['startTime'] // step + 1) * step
        return Resp([{tskey: t, valuekey: '1'} for t in
                     list(range(first, now + 1, step))[:params['limit']]])
    monkeypatch.setattr(D.requests, 'get', get)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    monkeypatch.setattr(f, '_deepen_to', lambda *a, **k: (_ for _ in ()).throw(AssertionError('history in current worker')))
    span = 617 * 900_000
    result = f.acquire_needs([('ETH/USDT', 'funding', span), ('ETH/USDT', 'oi', span)], delay=0)
    assert result['fetched'] == 2 and result['deepened'] == 0
    assert len(seen) == 3
    assert all(p['startTime'] >= now - span - 86_400_000 for _, p in seen)
    assert len(f.load('ETH/USDT', 'oi', as_of_ms=now)) >= 617
    assert f.acquire_needs([('ETH/USDT', 'funding', span)], delay=0)['requested'] == 0


def test_failed_save_is_retried_not_marked_fresh(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    calls = install(monkeypatch, now)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    save = f.save
    monkeypatch.setattr(f, 'save', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('save failed')))
    assert f.acquire_needs([('ETH/USDT', 'funding')], delay=0)['failed'] == 1
    monkeypatch.setattr(f, 'save', save)
    assert f.acquire_needs([('ETH/USDT', 'funding')], delay=0)['fetched'] == 1
    assert len(calls) == 2


def test_failed_high_rank_does_not_starve_unattempted_inputs(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    install(monkeypatch, now)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    requested = []
    def fail(sym, *a):
        requested.append(sym)
        raise RuntimeError('provider failed')
    monkeypatch.setattr(f, '_acquire_current', fail)
    needs = [(x, 'oi') for x in ('A/USDT','B/USDT','C/USDT')]
    for _ in range(3): f.acquire_needs(needs, max_requests=1, delay=0)
    assert requested == ['A/USDT','B/USDT','C/USDT']


def test_current_freshness_is_scoped_to_physical_database(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    install(monkeypatch, now)
    for name in ('one.db', 'two.db'):
        f = D.DerivFeed(tmp_path / name, clock_ms=lambda: now)
        assert f.acquire_needs([('ETH/USDT', 'funding')], delay=0)['fetched'] == 1


def test_derivative_commit_queues_cache_refresh_outside_acquisition(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    install(monkeypatch, now)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    calls = []
    monkeypatch.setattr(M, 'refresh_entries', lambda conn, key, **kw: calls.append(kw))
    f.acquire_needs([('ETH/USDT', 'funding')], delay=0)
    assert calls == [{'deferred': True}]


def test_failed_later_page_does_not_suppress_current_retry(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = 1_790_000_000_000 // 900_000 * 900_000
    n = []
    def get(url, params=None, **kwargs):
        n.append(1)
        if len(n) % 2 == 0: return Resp([])
        first = (params['startTime'] // 900_000 + 1) * 900_000
        return Resp([{'timestamp': first + i * 900_000, 'sumOpenInterest': '1'} for i in range(500)])
    monkeypatch.setattr(D.requests, 'get', get)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    needs = [('ETH/USDT', 'oi', 617 * 900_000)]
    f.acquire_needs(needs, delay=0)
    assert f.acquire_needs(needs, delay=0)['requested'] == 1
    assert len(n) == 4


def test_current_save_reads_ancestry_before_reserving_writer(tmp_path, monkeypatch):
    D.DerivFeed._fetched_at.clear()
    now = int(time.time() * 1000)
    install(monkeypatch, now)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    db = f.db
    statements = []
    db.set_trace_callback(statements.append)
    frame, acquisition = f._acquire_current('ETH/USDT', 'funding', 'funding')
    f.save('ETH/USDT', 'funding', frame, acquisition=acquisition)
    # Ancestry selection (the ROW_NUMBER parent query) runs before the writer
    # lock and is not repeated under it while the series witness is unchanged.
    lock = statements.index('BEGIN IMMEDIATE')
    assert any('ROW_NUMBER' in q for q in statements[:lock])
    assert not any('ROW_NUMBER' in q for q in statements[lock:])
    assert not db.in_transaction


def test_broad_recorder_does_not_duplicate_current_required_pairs(tmp_path, monkeypatch):
    now = int(time.time() * 1000)
    calls = install(monkeypatch, now)
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    f.record_all(['ETH/USDT'], delay=0, exclude={('ETH/USDT','funding'),('ETH/USDT','oi')})
    assert not any(name in ('fundingRate','openInterestHist') for name, _ in calls)


def test_current_window_reuses_proved_unchanged_history_but_keeps_corrections(tmp_path):
    import pandas as pd
    now = 1_790_000_000_000
    f = D.DerivFeed(tmp_path / 'd.db', clock_ms=lambda: now)
    key = 'derivative:ETH/USDT:oi'
    events = [now - (20 - i) * 900_000 for i in range(20)]
    def frame(clock, values):
        return M.annotate(pd.DataFrame({'ts':pd.to_datetime(events,unit='ms',utc=True),'value':values}),
            instrument_id='binance_usdm:futures:ETHUSDT', source=f.current_source('oi'),
            kind='derivative', received_ms=clock, max_age_ms=2_700_000)
    f.save('ETH/USDT', 'oi', frame(now,[1.] * 20), acquisition=dict(mode='required_window',now_ms=now))
    count = lambda: f.db.execute('SELECT count(*) FROM market_revisions WHERE series_key=?',(key,)).fetchone()[0]
    assert count() == 20
    now += 1000
    f.save('ETH/USDT', 'oi', frame(now,[1.] * 20), acquisition=dict(mode='required_window',now_ms=now))
    assert count() == 22                              # only two tip observations, not 20 copies
    now += 1000
    f.save('ETH/USDT', 'oi', frame(now,[7.] + [1.] * 19), acquisition=dict(mode='required_window',now_ms=now))
    assert count() == 25                              # old correction plus two tips
    rows = f.load('ETH/USDT','oi',as_of_ms=now)
    assert rows['value'].iloc[0] == 7.
    assert rows['observed_at_ms'].iloc[1] == now - 2000 # original receipt not restamped


def test_unknown_inventory_is_never_reused_as_current_history(tmp_path):
    import pandas as pd
    now=1_790_000_000_000
    f=D.DerivFeed(tmp_path/'d.db',clock_ms=lambda:now)
    events=[now-(10-i)*900_000 for i in range(10)]
    plain=pd.DataFrame({'ts':pd.to_datetime(events,unit='ms',utc=True),'value':[1.]*10})
    f.save('ETH/USDT','oi',plain)
    qualified=M.annotate(plain,instrument_id='binance_usdm:futures:ETHUSDT',source=f.current_source('oi'),
                         kind='derivative',received_ms=now,max_age_ms=2_700_000)
    f.save('ETH/USDT','oi',qualified,acquisition=dict(mode='required_window',now_ms=now))
    assert f.db.execute('SELECT count(*) FROM market_revisions').fetchone()[0]==20


def test_unchanged_refresh_replaces_selected_unknown_not_older_matching_value(tmp_path):
    import pandas as pd
    now=1_790_000_000_000
    f=D.DerivFeed(tmp_path/'d.db',clock_ms=lambda:now)
    events=[now-(10-i)*900_000 for i in range(10)]
    plain=pd.DataFrame({'ts':pd.to_datetime(events,unit='ms',utc=True),'value':[1.]*10})
    iid='binance_usdm:futures:ETHUSDT'; key='derivative:ETH/USDT:oi'
    def verified():
        return M.annotate(plain,instrument_id=iid,source=f.current_source('oi'),
                          kind='derivative',received_ms=now,max_age_ms=2_700_000)
    f.save('ETH/USDT','oi',verified())
    now+=1000
    f.save('ETH/USDT','oi',plain)
    unknown=f.db.execute("SELECT revision_id,record_json FROM market_revisions WHERE json_extract(record_json,'$.quality')='UNKNOWN'").fetchall()
    assert len(unknown)==10
    assert M.load_window_typed(f.db,key,as_of_ms=now,window_start_ms=events[0],instrument_id=iid)[1]=='identity_ambiguous'
    before=now;now+=1000
    f.save('ETH/USDT','oi',verified(),acquisition=dict(mode='required_window',now_ms=now))
    frame,reason=M.load_window_typed(f.db,key,as_of_ms=now,window_start_ms=events[0],instrument_id=iid)
    assert reason=='valid' and frame.quality.eq('VALID').all()
    assert frame.observed_at_ms.eq(now).all()
    assert M.load_window_typed(f.db,key,as_of_ms=before,window_start_ms=events[0],instrument_id=iid)[1]=='identity_ambiguous'
    for rid,raw in unknown:
        assert f.db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?',(rid,)).fetchone()[0]==raw


def test_required_window_handles_provider_returning_newest_page(tmp_path,monkeypatch):
    import pandas as pd
    step=900_000;now=20000*step+1234
    f=D.DerivFeed(tmp_path/'d.db',clock_ms=lambda:now)
    span=617*step;calls=[]
    def newest(symbol,period,limit,start_ms,end_ms):
        calls.append((start_ms,end_ms))
        events=list(range((start_ms+step-1)//step*step,end_ms+1,step))[-limit:]
        return M.annotate(pd.DataFrame({'ts':pd.to_datetime(events,unit='ms',utc=True),'value':[1.]*len(events)}),
            instrument_id='binance_usdm:futures:ETHUSDT',source=f.current_source('oi'),
            kind='derivative',received_ms=now,max_age_ms=2_700_000)
    monkeypatch.setattr(f,'open_interest',newest)
    frame,claim=f._required_window('ETH/USDT','oi',span)
    start=now-span-step
    expected=list(range((start+step-1)//step*step,now+1,step))
    assert M.ms(frame.ts).tolist()==expected
    assert len(calls)==2 and all(end-start<500*step for start,end in calls)
    assert claim['complete']
