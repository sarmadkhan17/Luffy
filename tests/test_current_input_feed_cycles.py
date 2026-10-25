"""Feed/producer/scheduler integration of the current-input access correction."""
import time

import pandas as pd

from tests.test_feed_pagination import FakeEx
from trader.data import market_provenance as M
from trader.data.feed import DataFeed


def make(tmp_path, total=600):
    ex = FakeEx(total=total)
    feed = DataFeed(exchange=ex, db_path=tmp_path / 'c.db')
    feed.fetch_ohlcv('BTC/USDT', '15m', limit=400, force=True)
    return ex, feed


def test_latest_ohlcv_reuses_unchanged_window_across_scans_without_payload_reads(tmp_path):
    M.CURRENT.clear()
    _, feed = make(tmp_path)
    first = feed.latest_ohlcv('BTC/USDT', '15m', 400)
    decoded = M.STATS['payload_rows_decoded']
    for _ in range(5):
        again = feed.latest_ohlcv('BTC/USDT', '15m', 400)
    assert M.STATS['payload_rows_decoded'] == decoded
    pd.testing.assert_frame_equal(first, again)
    again.loc[:, 'close'] = -1                        # owned copy
    assert feed.latest_ohlcv('BTC/USDT', '15m', 400)['close'].iloc[-1] != -1


def test_new_close_is_acquired_immediately_even_with_a_fresh_previous_window(tmp_path, monkeypatch):
    M.CURRENT.clear()
    ex, feed = make(tmp_path)
    old_tip = int(feed._last_ms(feed.latest_ohlcv('BTC/USDT', '15m', 400)))
    new_boundary = old_tip + 2 * 900_000
    assert feed.tip_complete('BTC/USDT', '15m', new_boundary) is False        # metadata only, no payload
    # a new bar closed: force=True extends the stored TAIL (one request), not history
    ex.total += 2
    feed._clock_ms = lambda: new_boundary + 900_000 + 5_000
    calls = len(ex.calls)
    df = feed.fetch_ohlcv('BTC/USDT', '15m', limit=400, force=True)
    assert feed._last_ms(df) >= new_boundary and len(ex.calls) - calls == 1


def test_tip_complete_decodes_payload_only_when_the_witness_changes(tmp_path):
    M.CURRENT.clear()
    _, feed = make(tmp_path)
    tip = int(feed._last_ms(feed.latest_ohlcv('BTC/USDT', '15m', 2)))
    assert feed.tip_complete('BTC/USDT', '15m', tip)
    before = M.STATS['payload_rows_decoded']
    for _ in range(10):
        assert feed.tip_complete('BTC/USDT', '15m', tip)
    assert M.STATS['payload_rows_decoded'] == before


def test_cache_only_scan_read_never_loads_and_reports_warming(tmp_path):
    M.CURRENT.clear()
    M._WARM.clear()
    _, feed = make(tmp_path)
    M.CURRENT.clear()
    before = M.STATS['payload_rows_decoded']
    with M.cache_only():
        assert feed.latest_ohlcv('BTC/USDT', '15m', 400) is None
    assert M.STATS['payload_rows_decoded'] == before        # nothing loaded inside the scan
    assert M.run_warm() >= 1
    with M.cache_only():
        assert len(feed.latest_ohlcv('BTC/USDT', '15m', 400)) == 400


def test_alts_unchanged_is_detected_before_any_history_is_loaded(tmp_path, monkeypatch):
    from trader.data import ref_sources as rs
    from trader.data.references import RefStore
    M.CURRENT.clear()
    rs._ALT_SAVED.clear()
    exch = FakeEx(total=300, tf_ms=4 * 3_600_000)
    feed = DataFeed(exchange=exch, db_path=tmp_path / 'c.db')
    members = ['A/USDT', 'B/USDT', 'C/USDT', 'D/USDT', 'E/USDT', 'F/USDT']
    for m in members:
        feed.fetch_ohlcv(m, '4h', limit=250, force=True)
    store = RefStore(tmp_path / 'c.db')
    now = int(time.time() * 1000)
    out = {}
    rs._refresh_alts(feed, members, store, now, out, allow_cold=True)
    assert out['alts'] > 0
    loads = []
    real = feed.cached_ohlcv
    monkeypatch.setattr(feed, 'cached_ohlcv', lambda *a, **k: (loads.append(a), real(*a, **k))[1])
    out2 = {}
    rs._refresh_alts(feed, members, store, now + 60_000, out2)
    assert out2['alts'] == 0 and out2['alts_reuse']['reason'] == 'witness_unchanged_before_load' and not loads
    feed.fetch_ohlcv('A/USDT', '4h', limit=250, force=True)          # member receipts changed
    out3 = {}
    rs._refresh_alts(feed, members, store, now + 120_000, out3)
    assert loads and out3['alts_reuse']['kind'] != 'unchanged' or out3['alts'] != 0


def test_alts_changed_members_read_only_their_tail_and_equal_a_cold_rebuild(tmp_path):
    import numpy as np
    from trader.data import ref_sources as rs
    from trader.data.references import RefStore
    M.CURRENT.clear()
    rs._ALT_SAVED.clear()
    rs.CACHE_RESET() if hasattr(rs, 'CACHE_RESET') else None
    from trader.data.alt_index_state import CACHE
    CACHE.invalidate()
    exch = FakeEx(total=299, tf_ms=4 * 3_600_000)
    feed = DataFeed(exchange=exch, db_path=tmp_path / 'c.db')
    members = [f'{c}/USDT' for c in 'ABCDEF']
    for m in members:
        feed.fetch_ohlcv(m, '4h', limit=250, force=True)
    store = RefStore(tmp_path / 'c.db')
    now = int(time.time() * 1000)
    out = {}
    rs._refresh_alts(feed, members, store, now, out, allow_cold=True)
    assert out['alts_reuse']['members']['full'] == 6                      # cold
    exch.total = 300                                                        # one new closed bar for everyone
    for m in members[:2]:
        feed.fetch_ohlcv(m, '4h', limit=250, force=True)
    out2 = {}
    rs._refresh_alts(feed, members, store, now + 1000, out2)
    rep = out2['alts_reuse']['members']
    assert rep['full'] == 0 and rep['tail'] == 2 and rep['reused'] == 4 and 2 <= rep['tail_rows'] <= 80
    frames = {m: feed.cached_ohlcv(m, '4h', limit=40000, as_of_ms=now + 1000) for m in members}
    cold = rs.alts_index(frames)
    stored = store.load('alts', as_of_ms=now + 1000)
    got = stored.set_index('ts')['close']
    want = cold.set_index('ts')['close']
    np.testing.assert_allclose(got.loc[want.index].to_numpy(), want.to_numpy(), equal_nan=True)


def test_alts_skip_ends_when_a_future_dated_member_row_becomes_visible(tmp_path):
    from trader.data import ref_sources as rs
    from trader.data.references import RefStore
    from trader.data.alt_index_state import CACHE
    M.CURRENT.clear(); rs._ALT_SAVED.clear(); CACHE.invalidate()
    exch = FakeEx(total=300, tf_ms=4 * 3_600_000)
    feed = DataFeed(exchange=exch, db_path=tmp_path / 'c.db')
    members = [f'{c}/USDT' for c in 'ABCDEF']
    for m in members:
        feed.fetch_ohlcv(m, '4h', limit=250, force=True)
    store = RefStore(tmp_path / 'c.db')
    now = int(time.time() * 1000)
    out = {}
    rs._refresh_alts(feed, members, store, now, out, allow_cold=True)
    key = next(iter(rs._ALT_SAVED))
    rs._ALT_SAVED[key]['next_ready']['A/USDT'] = now + 5_000      # a committed row becomes visible then
    out2 = {}
    rs._refresh_alts(feed, members, store, now + 1_000, out2)
    assert out2['alts_reuse']['reason'] == 'witness_unchanged_before_load'
    out3 = {}
    rs._refresh_alts(feed, members, store, now + 6_000, out3)
    assert out3['alts_reuse'].get('reason') != 'witness_unchanged_before_load'
    assert out3['alts_reuse']['members']['full'] >= 1                 # conservative full member read


def test_active_inputs_are_reobserved_before_their_original_ttl_with_real_tail_requests(tmp_path, monkeypatch):
    """Advancing clock, no new close: held and shortlisted series stay fresh through REAL tail
    observations (new receipt clocks); unselected broad coverage is not re-observed at that cadence."""
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    import trader.scheduling.kernel_runtime as module
    from trader.scheduling.kernel_runtime import KernelRuntime
    from trader.core.types import ControlState
    M.CURRENT.clear()
    real_now = int(time.time() * 1000)
    ex = FakeEx(total=600)
    base = ex.first_ts + 600 * 900_000 + 10_000                       # 10s after the latest close
    offset = [0]
    clock = lambda: base + offset[0]
    feed = DataFeed(exchange=ex, db_path=tmp_path / 'c.db', clock_ms=clock)
    symbols = ['HELD/USDT', 'SHORT/USDT', 'BROAD/USDT']
    for sym in symbols:
        feed.fetch_ohlcv(sym, '15m', limit=400, force=True)
    feed.background_acquisition = True
    k = NS(cfg={'timeframes': {'context': []}}, journal=NS(db_path=str(tmp_path / 'j.db')), feed=feed,
           _stop=False, _boot_complete=True, heartbeat=Mock(), state_machine=NS(state=ControlState.FROZEN),
           _exposure_symbols=lambda: {'HELD/USDT'}, universe=NS(symbols=lambda: symbols))
    r = KernelRuntime(k)
    r.candle_active = {'SHORT/USDT': {'15m'}}
    monkeypatch.setattr(module.time, 'time', lambda: clock() / 1000)
    ttl_ms = feed.ttl['15m'] * 1000
    first_calls = len(ex.calls)

    def drain():
        for _ in range(200):
            health = feed.background_acquisition_health()
            if not health['pending'] and not health['worker_busy']:
                return
            time.sleep(0.02)
        raise AssertionError('acquisition queue did not drain')

    ages = []
    for minute in range(1, 11):
        offset[0] = minute * 60_000
        r.candle_pending.clear()                                   # no new close: nothing is pending
        r.candles()
        drain()
        for sym in ('HELD/USDT', 'SHORT/USDT'):
            frame = mp_usable(feed, sym, clock(), feed.ttl['15m'])
            assert frame is not None and frame['quality'].eq('VALID').all(), (minute, sym)
            ages.append(clock() - int(frame['observed_at_ms'].iloc[-1]))
    assert max(ages) < ttl_ms                                     # never past the ORIGINAL receipt budget
    per_symbol = {s: [c for c in ex.calls[first_calls:] if c[0] == s] for s in symbols}
    assert len(per_symbol['HELD/USDT']) >= 4 and len(per_symbol['SHORT/USDT']) >= 4     # ~every 90s over 10 minutes
    assert [c for c in per_symbol['BROAD/USDT'] if c[1] == '15m'] == []       # (1h/4h calls are first-time cold fetches)
    # every observation was a bounded tail request, not a history re-download
    assert all(c[3] is not None and c[3] <= 1000 for c in ex.calls[first_calls:])


def mp_usable(feed, sym, at, ttl_s):
    frame = M.eligible_frame(feed.ohlcv_asof(sym, '15m', as_of_ms=at, limit=50), '15m', at)
    return M.usable_current(frame, '15m', at, ttl_s)


def test_tail_save_does_not_reread_the_whole_old_window(tmp_path):
    M.CURRENT.clear()
    ex, feed = make(tmp_path, total=600)
    feed.latest_ohlcv('BTC/USDT', '15m', 400)                      # a consumer window exists
    decoded = M.STATS['payload_rows_decoded']
    df = feed.fetch_ohlcv('BTC/USDT', '15m', limit=400, force=True)    # tail observation + merge + commit refresh
    assert len(df) == 400 and (df['quality'] == 'VALID').all()
    assert M.STATS['payload_rows_decoded'] - decoded < 60          # overlap bars only, not 2x the 400-row window
    again = feed.latest_ohlcv('BTC/USDT', '15m', 400)
    pd.testing.assert_frame_equal(again.reset_index(drop=True), df.reset_index(drop=True), check_dtype=False)


def test_merge_with_a_forming_bar_keeps_the_original_partial_semantics(tmp_path):
    M.CURRENT.clear()
    ex = FakeEx(total=600)
    now = ex.first_ts + 600 * 900_000 + 60_000                       # one minute into the next (forming) bar
    feed = DataFeed(exchange=ex, db_path=tmp_path / 'c.db', clock_ms=lambda: now)
    feed.fetch_ohlcv('BTC/USDT', '15m', limit=400, force=True)
    ex.total += 1                                                   # the forming bar now exists at the source
    df = feed.fetch_ohlcv('BTC/USDT', '15m', limit=400, force=True)
    forming = ex.first_ts + 600 * 900_000
    assert df is not None and int(feed._last_ms(df)) < forming       # a forming bar is never served as current
    partial = feed.ohlcv_asof('BTC/USDT', '15m', as_of_ms=now, limit=400, include_partial=True)
    assert partial['bar_state'].eq('PARTIAL').any() and int(partial['event_time_ms'].iloc[-1]) == forming
    decoded = M.STATS['payload_rows_decoded']
    again = feed.fetch_ohlcv('BTC/USDT', '15m', limit=400, force=True)
    assert again is not None and int(feed._last_ms(again)) < forming
    assert M.STATS['payload_rows_decoded'] - decoded < 60


def test_cold_alts_rebuild_is_budgeted_history_work_separate_from_the_current_refresh(tmp_path):
    from trader.data import ref_sources as rs
    from trader.data.references import RefStore
    from trader.data.alt_index_state import CACHE
    M.CURRENT.clear(); rs._ALT_SAVED.clear(); rs._COLD.clear(); CACHE.invalidate()
    exch = FakeEx(total=300, tf_ms=4 * 3_600_000)
    feed = DataFeed(exchange=exch, db_path=tmp_path / 'c.db')
    members = [f'{c}/USDT' for c in 'ABCDEFGH']
    for m in members:
        feed.fetch_ohlcv(m, '4h', limit=250, force=True)
    store = RefStore(tmp_path / 'c.db')
    now = int(time.time() * 1000)
    loads = []
    real = feed.cached_ohlcv
    feed.cached_ohlcv = lambda *a, **k: (loads.append(a[0]), real(*a, **k))[1]
    out = {}
    rs._refresh_alts(feed, members, store, now, out)                 # the hourly current refresh
    assert loads == [] and out['alts_reuse']['reason'] == 'cold_rebuild_is_history_maintenance'
    assert rs.alts_cold_pending(store, members) and store.last_ts('alts') is None
    yields = []
    first = rs.rebuild_alts_cold(feed, members, store, now, members_per_slice=3, yield_hook=lambda: yields.append(1))
    assert first == dict(state='in_progress', loaded=3, total=8) and len(loads) == 3 and len(yields) == 3
    second = rs.rebuild_alts_cold(feed, members, store, now, members_per_slice=3, yield_hook=lambda: yields.append(1))
    assert second['loaded'] == 6 and len(loads) == 6                  # resumes; never rereads loaded members
    done = rs.rebuild_alts_cold(feed, members, store, now, members_per_slice=3, yield_hook=lambda: yields.append(1))
    assert done['state'] == 'committed' and len(loads) == 8 and store.last_ts('alts') is not None
    assert not rs.alts_cold_pending(store, members)
    cold = rs.alts_index({m: real(m, '4h', limit=40000, as_of_ms=now) for m in members})
    import numpy as np
    got = store.load('alts', as_of_ms=now + 60_000).set_index('ts')['close']
    want = cold.set_index('ts')['close']
    np.testing.assert_allclose(got.loc[want.index].to_numpy(), want.to_numpy(), equal_nan=True)
    out2 = {}
    rs._refresh_alts(feed, members, store, now + 60_000, out2)       # afterwards the current refresh is the cheap path
    assert out2['alts_reuse']['reason'] == 'witness_unchanged_before_load' and len(loads) == 8
