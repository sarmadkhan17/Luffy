"""Bounded current-input access: typed window reader, cross-cycle cache, witnesses."""
import random
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.data import market_provenance as M
from trader.data.derivatives import DerivFeed

IID = 'binance_usdm:futures:BTCUSDT'
SRC = DerivFeed.FUNDING_SOURCE


def frame(events, values=None, **kw):
    values = np.arange(len(events), dtype=float) if values is None else values
    return pd.DataFrame(dict(ts=pd.to_datetime(list(events), unit='ms', utc=True), value=values))


def save(feed, events, ready, symbol='BTC/USDT', series='funding', iid=IID, values=None, source=SRC):
    df = M.annotate(frame(events, values), instrument_id=iid, source=source, kind='derivative', received_ms=ready)
    df['available_at_ms'] = ready
    df = M.seal(df)
    feed.save(symbol, series, df)


def fixture_db(tmp_path, seed, name='d.db'):
    """Randomised retained revisions: duplicates, equal clocks, late corrections, future rows."""
    rnd = random.Random(seed)
    feed = DerivFeed(tmp_path / name, clock_ms=lambda: 10**9)
    for _ in range(rnd.randint(8, 25)):
        events = sorted(rnd.sample(range(0, 60), rnd.randint(1, 6)))
        ready = rnd.choice([rnd.randint(0, 80), rnd.randint(0, 80), 500])
        save(feed, [e * 1000 for e in events], ready * 1000, values=[rnd.random() for _ in events])
    return feed


def old_window(feed, at, start):
    key = 'derivative:BTC/USDT:funding'
    return M.load(feed.db, key, as_of_ms=at, window_start_ms=start)


@pytest.mark.parametrize('seed', range(40))
def test_typed_window_equals_old_query_differentially(tmp_path, seed):
    feed = fixture_db(tmp_path, seed)
    key = 'derivative:BTC/USDT:funding'
    rnd = random.Random(seed + 1000)
    for _ in range(6):
        at = rnd.randint(0, 90) * 1000
        start = rnd.randint(0, at // 1000) * 1000
        old = old_window(feed, at, start)
        new, reason = M.load_window_typed(feed.db, key, as_of_ms=at, window_start_ms=start, instrument_id=IID)
        if old is None:
            assert new is None
        else:
            assert reason == 'valid'
            assert list(new.revision_id) == list(old.revision_id)
            pd.testing.assert_frame_equal(new.reset_index(drop=True), old.reset_index(drop=True))


def test_equal_clock_rowid_winner_and_predecessor(tmp_path):
    feed = DerivFeed(tmp_path / 'd.db')
    key = 'derivative:BTC/USDT:funding'
    save(feed, [1000, 2000], 5000, values=[1, 2])
    save(feed, [2000], 5000, values=[3])       # same clock, later rowid wins
    save(feed, [9000], 9000, values=[4])
    new, _ = M.load_window_typed(feed.db, key, as_of_ms=9000, window_start_ms=9000, instrument_id=IID)
    old = old_window(feed, 9000, 9000)
    assert list(new.revision_id) == list(old.revision_id)
    assert new['value'].tolist() == [3, 4]    # exact predecessor kept, with winning revision


def test_typed_reader_does_not_decode_unrelated_history(tmp_path):
    feed = DerivFeed(tmp_path / 'd.db')
    key = 'derivative:BTC/USDT:funding'
    ev = [i * 1000 for i in range(2000)]
    save(feed, ev, 3_000_000)
    save(feed, [e for e in ev[-5:]], 4_000_000)
    # Old history becomes unreadable payload. Index-ranked selection never touches it.
    feed.db.execute("UPDATE market_revisions SET record_json='not json' WHERE event_ms<1900000")
    feed.db.execute("UPDATE market_series_meta SET resolved=1")   # corruption is a test device only
    feed.db.commit()
    before = M.STATS['payload_rows_decoded']
    df, reason = M.load_window_typed(feed.db, key, as_of_ms=4_000_000, window_start_ms=1_990_000, instrument_id=IID)
    assert reason == 'valid' and df.event_time_ms.max() == 1_999_000
    assert M.STATS['payload_rows_decoded'] - before <= 20
    plan = ' '.join(r[3] for r in feed.db.execute(
        "EXPLAIN QUERY PLAN SELECT rowid,event_ms,available_ms,observed_ms FROM market_revisions "
        "WHERE series_key=? AND available_ms<=? AND observed_ms<=?", (key, 1, 1)))
    assert 'COVERING INDEX' in plan


def test_unproven_series_use_exact_legacy_query_and_foreign_identity_is_never_selected(tmp_path):
    feed = DerivFeed(tmp_path / 'd.db')
    key = 'derivative:BTC/USDT:funding'
    save(feed, [1000, 2000], 3000)
    feed.db.execute("UPDATE market_series_meta SET resolved=0")
    before = M.STATS['legacy_window_reads']
    df, reason = M.load_window_typed(feed.db, key, as_of_ms=5000, window_start_ms=0, instrument_id=IID)
    assert reason == 'valid' and len(df) == 2 and M.STATS['legacy_window_reads'] == before + 1
    save(feed, [3000], 4000, iid='binance_usdm:futures:ETHUSDT')
    df, reason = M.load_window_typed(feed.db, key, as_of_ms=5000, window_start_ms=0, instrument_id=IID)
    assert df is None            # the original query refuses an ambiguous series; nothing is chosen
    assert old_window(feed, 5000, 0) is None


def test_equal_event_revisions_across_resolver_budget_are_not_skipped(tmp_path):
    feed = DerivFeed(tmp_path / 'd.db')
    key = 'derivative:BTC/USDT:funding'
    for i in range(7):                                    # seven revisions of ONE event
        save(feed, [1000], 1000 + i, iid=IID if i != 5 else 'binance_usdm:futures:ETHUSDT')
    feed.db.execute("UPDATE market_series_meta SET resolved=0,idents='|',epoch=0")
    feed.db.commit()
    M.demand(feed.db, key, True)
    for _ in range(20):
        M.resolve_pending(feed.db, budget_rows=2)
        if feed.db.execute('SELECT resolved FROM market_series_meta').fetchone()[0]:
            break
    idents = feed.db.execute('SELECT idents FROM market_series_meta').fetchone()[0]
    assert 'ETHUSDT' in idents and IID in idents
    df, reason = M.load_window_typed(feed.db, key, as_of_ms=9000, window_start_ms=0, instrument_id=IID)
    assert df is None


def test_resolver_resumes_and_matches_scan(tmp_path):
    feed = DerivFeed(tmp_path / 'd.db')
    key = 'derivative:BTC/USDT:funding'
    save(feed, [i * 1000 for i in range(50)], 99_000)
    feed.db.execute("UPDATE market_series_meta SET resolved=0,epoch=0,gen=gen")   # pre-trigger state
    feed.db.commit()
    M.demand(feed.db, key, True)
    for _ in range(10):
        M.resolve_pending(feed.db, budget_rows=20)
        if feed.db.execute('SELECT resolved FROM market_series_meta').fetchone()[0]:
            break
    assert feed.db.execute('SELECT resolved,idents FROM market_series_meta').fetchone() == (1, '|"%s"|' % IID)
    assert M.load_window_typed(feed.db, key, as_of_ms=99_000, window_start_ms=40_000, instrument_id=IID)[1] == 'valid'


# ── cross-cycle cache ───────────────────────────────────────────────────────
def typed(feed, at, start=0):
    return feed.load('BTC/USDT', 'funding', as_of_ms=at, window_start_ms=start, instrument_id=IID)


def test_cache_hit_has_no_repeat_reads_and_returns_owned_copy(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000, 2000, 3000], 3000)
    first = typed(feed, 4000)
    decoded = M.STATS['payload_rows_decoded']
    again = typed(feed, 5000)
    assert M.STATS['payload_rows_decoded'] == decoded
    again.loc[:, 'value'] = -1.0
    again.attrs['x'] = 1
    third = typed(feed, 5000)
    assert third['value'].tolist() == first['value'].tolist() and 'x' not in third.attrs


def test_hit_never_renews_receipts_or_hides_staleness(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    df = M.annotate(frame([1000]), instrument_id=IID, source=SRC, kind='derivative', received_ms=1000, max_age_ms=5000)
    feed.save('BTC/USDT', 'funding', df)
    fresh = typed(feed, 2000)
    assert fresh['quality'].iloc[-1] == 'VALID'
    stale = typed(feed, 10_000)             # cache hit, same receipts, later cut
    assert stale['quality'].iloc[-1] == 'STALE' and np.isnan(stale['value'].iloc[-1])
    assert stale['observed_at_ms'].iloc[-1] == 1000


def test_commit_from_another_connection_invalidates(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000], 1000, values=[1])
    assert typed(feed, 5000)['value'].tolist() == [1]
    other = DerivFeed(tmp_path / 'd.db')
    save(other, [1000], 2000, values=[7])        # correction through a different connection
    assert typed(feed, 5000)['value'].tolist() == [7]


def test_failed_transaction_leaves_witness_and_cache_valid(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000], 1000, values=[1])
    typed(feed, 5000)
    gen = M.witness(feed.db, 'derivative:BTC/USDT:funding')
    c = sqlite3.connect(tmp_path / 'd.db')
    c.execute('BEGIN')
    c.execute("DELETE FROM market_revisions")
    c.rollback()
    assert M.witness(feed.db, 'derivative:BTC/USDT:funding') == gen
    decoded = M.STATS['payload_rows_decoded']
    typed(feed, 5000)
    assert M.STATS['payload_rows_decoded'] == decoded


@pytest.mark.parametrize('edit', ['update', 'delete'])
def test_row_edit_or_deletion_invalidates(tmp_path, edit):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000], 1000)
    typed(feed, 5000)
    c = sqlite3.connect(tmp_path / 'd.db')
    if edit == 'update':
        c.execute("UPDATE market_revisions SET record_json=replace(record_json,'\"quality\":\"VALID\"','\"quality\":\"INVALID\"')")
    else:
        c.execute('DELETE FROM market_revisions')
    c.commit()
    if edit == 'update':
        with pytest.raises(ValueError, match='corrupt'):
            typed(feed, 5000)
    else:
        assert typed(feed, 5000) is None


def test_raw_source_corruption_invalidates(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    big = 'x' * 600
    df = M.annotate(frame([1000]), instrument_id=IID, source=big, kind='derivative', received_ms=1000)
    feed.save('BTC/USDT', 'funding', df)
    plain(feed, 5000)
    c = sqlite3.connect(tmp_path / 'd.db')
    c.execute("UPDATE market_raw_sources SET raw_json='tampered'")
    c.commit()
    with pytest.raises(ValueError, match='source_reference'):
        plain(feed, 5000)


def test_future_availability_is_exact_and_cut_backwards_misses(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000], 1000, values=[1])
    save(feed, [2000], 8000, values=[2])           # committed now, visible only from 8000
    assert typed(feed, 5000)['value'].tolist() == [1]
    assert typed(feed, 7999)['value'].tolist() == [1]
    assert typed(feed, 8000)['value'].tolist() == [1, 2]
    assert typed(feed, 5000)['value'].tolist() == [1]        # cut moved backwards: exact earlier view


def test_source_instrument_and_window_isolation_in_cache_key(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000, 2000], 2000)
    a = typed(feed, 5000, start=0)
    b = typed(feed, 5000, start=2000)
    assert len(a) == 2 and len(b) == 2 and list(b.event_time_ms) == [1000, 2000]   # predecessor kept
    other = feed.load('BTC/USDT', 'funding', as_of_ms=5000, window_start_ms=0,
                      instrument_id='binance_usdm:futures:ETHUSDT')
    assert other is None


def test_cache_is_bounded(tmp_path):
    cache = M.CurrentCache(max_entries=3, max_rows=10, max_entry_rows=5)
    df = M.annotate(frame([1, 2, 3]), instrument_id=IID, source='s', kind='derivative', received_ms=10)
    for i in range(6):
        cache.put(('db', i), (1, 1, 1), 10, 5, None, df)
    assert len(cache.entries) <= 3 and cache.rows <= 10
    cache.put(('big',), (1, 1, 1), 10, 99, None, pd.concat([df] * 3))
    assert ('big',) not in cache.entries


def test_cold_start_without_witness_is_uncached_then_cache_only_reports_unproven(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000], 1000)
    feed.db.execute('DELETE FROM market_series_meta')
    feed.db.commit()
    assert typed(feed, 5000) is not None                  # exact, uncached (off the trading path)
    with M.cache_only():
        assert typed(feed, 5000) is None
        assert feed.input_reasons['derivative:BTC/USDT:funding'] == 'witness_unproven'


def test_missing_frame_is_cacheable_and_valid_until_commit(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000], 1000, symbol='ETH/USDT', iid='binance_usdm:futures:ETHUSDT')
    key = 'derivative:BTC/USDT:funding'
    M.demand(feed.db, key, True)
    feed.db.execute('INSERT OR IGNORE INTO market_series_meta(series_key,gen,resolved) VALUES (?,0,1)', (key,))
    feed.db.commit()
    assert typed(feed, 5000) is None and typed(feed, 6000) is None
    save(feed, [1000], 1000)
    assert typed(feed, 6000) is not None


def test_uncommitted_writer_never_populates_shared_cache_and_rollback_aba(tmp_path):
    M.CURRENT.clear()
    path = tmp_path / 'd.db'
    writer, reader = DerivFeed(path), DerivFeed(path)
    save(writer, [1000], 1000, values=[1])
    typed(reader, 5000)
    writer.db.execute('BEGIN IMMEDIATE')
    M.append(writer.db, 'derivative:BTC/USDT:funding', M.annotate(frame([1000], [3]), instrument_id=IID, source=SRC,
             kind='derivative', received_ms=2000))
    assert typed(writer, 5000) is not None             # reads its own txn, bypasses the shared cache
    writer.db.rollback()
    other = DerivFeed(path)
    save(other, [1000], 2500, values=[4])              # same generation number as the rolled-back write
    assert typed(reader, 5000)['value'].tolist() == [4]
    assert typed(writer, 5000)['value'].tolist() == [4]


def test_database_replacement_with_equal_generation_is_a_different_epoch(tmp_path):
    M.CURRENT.clear()
    one, two = tmp_path / 'a.db', tmp_path / 'b.db'
    for path, value in ((one, 1), (two, 2)):
        save(DerivFeed(path), [1000], 1000, values=[value])
    feed = DerivFeed(one)
    assert typed(feed, 5000)['value'].tolist() == [1]
    other = DerivFeed(two)
    assert M.witness(feed.db, 'derivative:BTC/USDT:funding') != M.witness(other.db, 'derivative:BTC/USDT:funding')
    assert typed(other, 5000)['value'].tolist() == [2]


def test_cache_only_miss_queues_warm_refill_and_producer_commit_refreshes(tmp_path):
    M.CURRENT.clear()
    M._WARM.clear()
    feed = DerivFeed(tmp_path / 'd.db', clock_ms=lambda: 5000)
    save(feed, [1000], 1000, values=[1])
    with M.cache_only():
        assert typed(feed, 5000) is None                  # cold: no load inside the scan
        assert feed.input_reasons['derivative:BTC/USDT:funding'] == 'warming'
        assert M.STATS['payload_rows_decoded'] == M.STATS['payload_rows_decoded']
    assert M.run_warm() == 1
    with M.cache_only():
        assert typed(feed, 5000)['value'].tolist() == [1]  # warmed
        decoded = M.STATS['payload_rows_decoded']
        save(feed, [1000], 2000, values=[9])               # producer commit refreshes in its own thread
        assert M.STATS['payload_rows_decoded'] > decoded
        assert typed(feed, 5000)['value'].tolist() == [9]


def test_cache_byte_bound(tmp_path):
    cache = M.CurrentCache(max_entries=10, max_rows=10**6, max_entry_rows=10**6, max_bytes=20_000, max_entry_bytes=15_000)
    df = M.annotate(frame(range(1, 30)), instrument_id=IID, source='s', kind='derivative', received_ms=100)
    cache.put(('a',), (1, 1, 1), 100, 50, None, df)
    cache.put(('b',), (1, 1, 1), 100, 50, None, df)
    assert cache.bytes <= 20_000
    cache.put(('c',), (1, 1, 1), 100, 50, None, pd.concat([df] * 40))
    assert ('c',) not in cache.entries and cache.put(('d',), (1, 1, 1), 100, 5, None, None) is None


# ── committed change log and incremental refresh ───────────────────────────
KEY = 'derivative:BTC/USDT:funding'


def plain(feed, at, limit=30):
    return M.cached_current(feed.db, KEY, as_of_ms=at, limit=limit)


@pytest.mark.parametrize('seed', range(25))
def test_incremental_refresh_equals_fresh_read_under_random_commits(tmp_path, seed):
    M.CURRENT.clear()
    rnd = random.Random(seed)
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, list(range(0, 80000, 1000)), 80_000)
    for step in range(12):
        plain(feed, 200_000)
        events = sorted(rnd.sample(range(0, 120), rnd.randint(1, 4)))
        save(feed, [e * 1000 for e in events], 80_000 + step * 100 + rnd.randint(0, 50),
             values=[rnd.random() for _ in events])
        if step == 7:
            feed.db.execute("DELETE FROM market_revisions WHERE rowid=(SELECT MAX(rowid) FROM market_revisions)")
            feed.db.commit()
        got = plain(feed, 200_000)
        fresh = M.load(feed.db, KEY, as_of_ms=200_000, limit=30)
        norm = lambda d: d.reset_index(drop=True).astype(object).where(d.reset_index(drop=True).notna(), None)
        pd.testing.assert_frame_equal(norm(got), norm(fresh), check_dtype=False)


def test_incremental_refresh_reads_only_the_changed_tail(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, list(range(0, 200_000, 1000)), 200_000)
    plain(feed, 300_000, 100)
    save(feed, [199_000, 200_000], 250_000)                  # overlap rewrite + one new bar
    rows, decoded = M.STATS['incremental_rows_read'], M.STATS['payload_rows_decoded']
    got = plain(feed, 300_000, 100)
    assert 2 <= M.STATS['incremental_rows_read'] - rows <= 20     # bucket-granular floor, not the 100-row window
    assert M.STATS['payload_rows_decoded'] - decoded <= 20
    assert got.event_time_ms.iloc[-1] == 200_000 and got.observed_at_ms.iloc[-1] == 250_000


def test_late_old_correction_and_destructive_changes_use_exact_reads(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, list(range(0, 100_000, 1000)), 100_000, values=[1.0] * 100)
    plain(feed, 300_000, 100)
    save(feed, [5000], 150_000, values=[9.0])                 # correction far below the tail
    before = M.STATS['incremental_rows_read']
    got = plain(feed, 300_000, 100)
    assert got.loc[got.event_time_ms == 5000, 'value'].iloc[0] == 9.0 and M.STATS['incremental_refreshes'] > 0
    c = sqlite3.connect(tmp_path / 'd.db')
    c.execute('DELETE FROM market_revisions WHERE event_ms=5000 AND observed_ms=150000')
    c.commit()
    full_before = M.STATS['cache_misses']
    got = plain(feed, 300_000, 100)
    assert got.loc[got.event_time_ms == 5000, 'value'].iloc[0] == 1.0     # deletion -> full exact read


def test_future_visible_rows_end_incremental_reuse(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, [1000, 2000], 2000, values=[1, 2])
    save(feed, [3000], 9000, values=[3])
    assert plain(feed, 5000)['value'].tolist() == [1, 2]
    save(feed, [2000], 6000, values=[7])
    assert plain(feed, 5000)['value'].tolist() == [1, 2]       # committed but not yet visible
    assert plain(feed, 6000)['value'].tolist() == [1, 7]
    assert plain(feed, 9000)['value'].tolist() == [1, 7, 3]


def test_raw_source_replace_invalidates(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    df = M.annotate(frame([1000]), instrument_id=IID, source='x' * 600, kind='derivative', received_ms=1000)
    feed.save('BTC/USDT', 'funding', df)
    plain(feed, 5000)
    c = sqlite3.connect(tmp_path / 'd.db')
    h = c.execute('SELECT content_hash FROM market_raw_sources').fetchone()[0]
    c.execute("INSERT OR REPLACE INTO market_raw_sources VALUES (?,?,?)", (h, 'immutable-text.v1', 'tampered'))
    c.commit()
    with pytest.raises(ValueError, match='source_reference'):
        plain(feed, 5000)


def test_demand_is_scoped_to_physical_database_and_prioritised(tmp_path):
    M._DEMAND.clear()
    candles, derivs = DerivFeed(tmp_path / 'c.db'), DerivFeed(tmp_path / 'd.db')
    save(derivs, [1000], 1000)
    derivs.db.execute('DELETE FROM market_series_meta')
    derivs.db.commit()
    for i in range(30):                                    # unrelated unresolved broad universe
        candles.db.execute('INSERT INTO market_series_meta(series_key,gen,resolved) VALUES (?,0,0)', (f'candle:{i}:15m',))
    candles.db.commit()
    typed(derivs, 5000)                                    # records demand for derivs.db only
    M.resolve_pending(candles.db, budget_rows=1000)        # wrong database first: must not consume it
    assert candles.db.execute('SELECT COUNT(*) FROM market_series_meta WHERE series_key=?', (KEY,)).fetchone()[0] == 0
    assert candles.db.execute('SELECT COUNT(*) FROM market_series_meta WHERE resolved=0').fetchone()[0] == 30
    before = M.STATS['resolver_rows_scanned']
    M.resolve_pending(derivs.db, budget_rows=1000)
    assert derivs.db.execute('SELECT resolved FROM market_series_meta WHERE series_key=?', (KEY,)).fetchone() is not None
    M.resolve_pending(derivs.db, budget_rows=1000)
    assert derivs.db.execute('SELECT resolved FROM market_series_meta WHERE series_key=?', (KEY,)).fetchone()[0] == 1
    assert M.STATS['resolver_rows_scanned'] - before <= 3          # only the demanded series was scanned


# ── versioned store initialisation ─────────────────────────────────────────
MUTATING = ('CREATE', 'DROP', 'ALTER', 'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'BEGIN IMMEDIATE', 'BEGIN EXCLUSIVE', 'COMMIT', 'PRAGMA JOURNAL')


def traced_connect(monkeypatch, log):
    real = sqlite3.connect

    def connect(*a, **k):
        conn = real(*a, **k)
        conn.set_trace_callback(log.append)
        return conn
    monkeypatch.setattr(sqlite3, 'connect', connect)


def test_initialised_stores_open_and_look_up_without_any_mutation(tmp_path, monkeypatch):
    from trader.data.feed import DataFeed
    from trader.data.references import RefStore
    from tests.test_feed_pagination import FakeEx
    M.CURRENT.clear()
    derivs, candles = tmp_path / 'd.db', tmp_path / 'c.db'
    producer = DerivFeed(derivs, clock_ms=lambda: 5000)
    save(producer, [1000], 1000)
    DataFeed(exchange=FakeEx(total=50), db_path=candles).fetch_ohlcv('BTC/USDT', '15m', limit=40, force=True)
    refs = RefStore(candles)
    refs.db.execute('SELECT 1')
    witness_before = M.witness(producer.db, KEY)
    version = producer.db.execute("SELECT gen FROM market_meta_global WHERE name='schema'").fetchone()[0]
    log = []
    traced_connect(monkeypatch, log)
    reader = DerivFeed(derivs, clock_ms=lambda: 5000)
    feed = DataFeed(exchange=FakeEx(total=50), db_path=candles)
    store = RefStore(candles)
    M.run_warm()
    with M.cache_only():
        typed(reader, 5000)
        feed.latest_ohlcv('BTC/USDT', '15m', 40)
        store.load('spx', as_of_ms=5000, window_start_ms=0)
    reader.load('BTC/USDT', 'funding', as_of_ms=5000, window_start_ms=0, instrument_id=IID)
    feed.latest_ohlcv('BTC/USDT', '15m', 40)
    bad = [q for q in log if q.lstrip().upper().startswith(MUTATING)]
    assert not bad, bad[:5]
    assert not any('DROP TRIGGER' in q.upper() or 'CREATE TRIGGER' in q.upper() for q in log)
    assert M.witness(reader.db, KEY) == witness_before
    assert reader.db.execute("SELECT gen FROM market_meta_global WHERE name='schema'").fetchone()[0] == version


def test_default_dependency_stores_are_reused_not_reopened_per_lookup(tmp_path, monkeypatch):
    from trader.strategy import spec_evidence
    opened = []
    real = sqlite3.connect
    (tmp_path / 'data').mkdir()
    producer = DerivFeed(tmp_path / 'data/derivs.db')
    producer.db.execute('SELECT 1')
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: opened.append(a) or real(*a, **k))
    monkeypatch.setattr(spec_evidence, '_DEFAULT_STORES', {})
    monkeypatch.setattr('trader.core.config.ROOT', tmp_path)
    for _ in range(5):
        spec_evidence.load_derivs('BTC/USDT', ['funding'], as_of_ms=5000, window_start_ms=0)
    assert len(opened) == 1
    assert spec_evidence._default_store('derivs').read_only


def test_old_draft_schema_is_migrated_once_by_a_writer(tmp_path):
    path = tmp_path / 'd.db'
    feed = DerivFeed(path)
    save(feed, [1000], 1000)
    feed.db.execute("DELETE FROM market_meta_global WHERE name='schema'")
    for name in ('market_witness_ins2', 'market_witness_upd2', 'market_witness_del2'):
        feed.db.execute(f'DROP TRIGGER {name}')
    feed.db.commit()
    assert not M.initialised(feed.db, 'derivs')
    DerivFeed(path).db                       # a writer open performs the single migration
    c = sqlite3.connect(path)
    assert c.execute("SELECT gen FROM market_meta_global WHERE name='schema'").fetchone()[0] == M.WITNESS_SCHEMA_VERSION
    assert len(c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'market_witness_%2'").fetchall()) == 3
    assert M.initialised(c, 'derivs')


def test_current_derivative_source_is_asserted_per_series(tmp_path):
    M.CURRENT.clear()
    feed = DerivFeed(tmp_path / 'd.db', clock_ms=lambda: 5000)
    save(feed, [1000], 1000, source='https://example.invalid/other-provider')
    assert typed(feed, 5000) is None and feed.input_reasons[KEY] == 'source_mismatch'
    save(feed, [2000], 2000)                                    # the specified endpoint is accepted
    good = DerivFeed(tmp_path / 'e.db', clock_ms=lambda: 5000)
    save(good, [1000], 1000)
    assert typed(good, 5000) is not None
    assert DerivFeed.current_source('oi').endswith('/openInterestHist')
    assert DerivFeed.current_source('basis') == 'binance:spot+usdm:basis'
    assert DerivFeed.current_source('ls_account_ratio') is None          # no adapter: explicit, unspecified


def test_pinned_held_and_current_windows_survive_eviction_pressure_and_report_capacity():
    cache = M.CurrentCache(max_entries=3, max_rows=10**6, max_entry_rows=10**6)
    df = M.annotate(frame([1, 2]), instrument_id=IID, source=SRC, kind='derivative', received_ms=10)
    cache.set_pins(symbols={'HELD/USDT'}, keys={'reference:spx'})
    for key in ('derivative:HELD/USDT:funding', 'reference:spx'):
        cache.put(('db', key), (1, 1, 1), 10, 5, None, df)
    for i in range(6):
        cache.put(('db', f'derivative:C{i}/USDT:funding'), (1, 1, 1), 10, 5, None, df)
    keys = {ck[1] for ck in cache.entries}
    assert {'derivative:HELD/USDT:funding', 'reference:spx'} <= keys and len(cache.entries) == 3
    cap = cache.capacity()
    assert cap['pinned_entries'] == 2 and cap['evictions'] >= 5 and cap['pinned_evictions'] == 0
    # pins beyond the bound are evicted, never allowed to exceed it, and are reported
    cache.set_pins(symbols={f'C{i}/USDT' for i in range(6)} | {'HELD/USDT'})
    for i in range(6, 9):
        cache.put(('db', f'derivative:C{i}/USDT:funding'), (1, 1, 1), 10, 5, None, df)
    assert len(cache.entries) <= 3


# ── exact incremental frontier: differential against the deployed reader ───
def _ops(rnd, feed, step):
    kind = rnd.choice(['append', 'append', 'correct', 'future', 'late_old', 'delete', 'edit'])
    if kind == 'append':
        e = 1000 * rnd.randint(60 + step, 70 + step)
        save(feed, [e], e + rnd.randint(0, 40) * 1000, values=[rnd.random()])
    elif kind == 'correct':
        e = 1000 * rnd.randint(30, 70)
        save(feed, [e], 1000 * rnd.randint(40, 120), values=[rnd.random()])
    elif kind == 'future':
        e = 1000 * rnd.randint(40, 80)
        save(feed, [e], 1000 * rnd.randint(130, 400), values=[rnd.random()])
    elif kind == 'late_old':
        e = 1000 * rnd.randint(0, 20)
        save(feed, [e], 1000 * rnd.randint(60, 200), values=[rnd.random()])
    elif kind == 'delete':
        feed.db.execute('DELETE FROM market_revisions WHERE rowid=(SELECT rowid FROM market_revisions ORDER BY RANDOM() LIMIT 1)')
        feed.db.commit()
    else:
        row = feed.db.execute('SELECT rowid FROM market_revisions ORDER BY RANDOM() LIMIT 1').fetchone()
        feed.db.execute('UPDATE market_revisions SET observed_ms=observed_ms WHERE rowid=?', (row[0],))   # touch: witness only
        feed.db.commit()
    return kind


@pytest.mark.parametrize('seed', range(40))
def test_frontier_incremental_state_equals_deployed_reader_under_changing_cuts_and_writes(tmp_path, seed):
    rnd = random.Random(seed)
    M.CURRENT.clear(); M.FRONTIERS.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    for _ in range(6):
        e = sorted(rnd.sample(range(0, 60), 4))
        save(feed, [x * 1000 for x in e], rnd.randint(0, 70) * 1000, values=[rnd.random() for _ in e])
    kinds, cut_now = [], 100_000
    for step in range(25):
        kinds.append(_ops(rnd, feed, step))
        for _ in range(2):
            # mostly advancing cuts (the live pattern), occasionally backwards
            cut_now = max(0, cut_now + rnd.choice([0, 1, 5, 20, 60, -40]) * 1000)
            at = cut_now
            start = rnd.randint(0, at // 1000) * 1000
            limit = rnd.choice([5, 30, 200000])
            old = old_window(feed, at, start)
            if old is not None and limit != 200000:
                old = M.load(feed.db, KEY, as_of_ms=at, window_start_ms=start, limit=limit)
            new, reason = M.load_window_typed(feed.db, KEY, as_of_ms=at, window_start_ms=start, instrument_id=IID, limit=limit)
            if old is None:
                assert new is None, (seed, step, kinds)
            else:
                assert list(new.revision_id) == list(old.revision_id), (seed, step, kinds[-3:], at, start, limit)
                assert new.event_time_ms.tolist() == old.event_time_ms.tolist()
    if seed == 39:                       # across the whole parametrised run both paths were exercised
        assert M.STATS['frontier_deltas'] > 20 and M.STATS['frontier_rebuilds'] > 20


def test_frontier_commit_costs_one_delta_and_new_payloads_only(tmp_path):
    M.CURRENT.clear(); M.FRONTIERS.clear()
    feed = DerivFeed(tmp_path / 'd.db')
    save(feed, list(range(0, 3000_000, 1000)), 3000_000)
    start = 2_900_000
    M.load_window_typed(feed.db, KEY, as_of_ms=3_100_000, window_start_ms=start, instrument_id=IID)
    rebuilds, deltas, decoded = M.STATS['frontier_rebuilds'], M.STATS['frontier_deltas'], M.STATS['payload_rows_decoded']
    save(feed, [2_999_000, 3_000_000], 3_050_000)                  # ordinary producer commit (overlap + new bar)
    df, _ = M.load_window_typed(feed.db, KEY, as_of_ms=3_100_000, window_start_ms=start, instrument_id=IID)
    assert M.STATS['frontier_rebuilds'] == rebuilds and M.STATS['frontier_deltas'] == deltas + 1
    assert M.STATS['payload_rows_decoded'] - decoded <= 4          # only the new revisions, window rows reused
    old = old_window(feed, 3_100_000, start)
    assert list(df.revision_id) == list(old.revision_id)


def test_frontier_memory_is_bounded_and_rebuilds_after_destructive_change(tmp_path, monkeypatch):
    M.CURRENT.clear(); M.FRONTIERS.clear()
    monkeypatch.setattr(M, 'FRONTIER_MAX_BYTES', 1)               # absurdly small: only the newest state survives
    a, b = DerivFeed(tmp_path / 'a.db'), DerivFeed(tmp_path / 'b.db')
    for f in (a, b):
        save(f, [1000, 2000, 3000], 3000)
        M.load_window_typed(f.db, KEY, as_of_ms=5000, window_start_ms=0, instrument_id=IID)
    assert len(M.FRONTIERS) == 0
    rebuilds = M.STATS['frontier_rebuilds']
    b.db.execute('DELETE FROM market_revisions WHERE event_ms=3000'); b.db.commit()
    df, _ = M.load_window_typed(b.db, KEY, as_of_ms=5000, window_start_ms=0, instrument_id=IID)
    assert M.STATS['frontier_rebuilds'] == rebuilds + 1 and df.event_time_ms.tolist() == [1000, 2000]


def test_typed_window_uses_one_snapshot_during_concurrent_delete(tmp_path, monkeypatch):
    M.FRONTIERS.clear()
    path = tmp_path / 'race.db'
    feed = DerivFeed(path)
    save(feed, [1000, 2000], 4000, values=[1, 2])
    save(feed, [2000], 5000, values=[3])
    before = old_window(feed, 6000, 0)
    writer = sqlite3.connect(path)
    original = M._select_rowids
    def racing_select(state, start, limit):
        rows = original(state, start, limit)
        writer.execute('DELETE FROM market_revisions WHERE rowid=?', (rows[0],))
        writer.commit()
        return rows
    monkeypatch.setattr(M, '_select_rowids', racing_select)
    actual, reason = M.load_window_typed(feed.db, KEY, as_of_ms=6000, window_start_ms=0, instrument_id=IID)
    assert reason == 'valid'
    assert actual.revision_id.tolist() == before.revision_id.tolist()
    assert not feed.db.in_transaction
    writer.close()


def test_frontier_cap_includes_decoded_payload_and_growth(tmp_path, monkeypatch):
    M.FRONTIERS.clear()
    feed = DerivFeed(tmp_path / 'cap.db')
    save(feed, [1000, 2000], 4000)
    M.load_window_typed(feed.db, KEY, as_of_ms=6000, window_start_ms=0, instrument_id=IID)
    state = next(iter(M.FRONTIERS.values()))
    assert state.nbytes() > 32 * len(state.ev)
    monkeypatch.setattr(M, 'FRONTIER_MAX_BYTES', state.nbytes() + 1)
    save(feed, [3000], 5000)
    M.load_window_typed(feed.db, KEY, as_of_ms=6000, window_start_ms=0, instrument_id=IID)
    assert sum(f.nbytes() for f in M.FRONTIERS.values()) <= M.FRONTIER_MAX_BYTES


def test_failed_metadata_seed_releases_its_transaction_before_retry(tmp_path):
    path = tmp_path / 'locked.db'
    conn = sqlite3.connect(path)
    M.init(conn); conn.commit()
    conn.execute('PRAGMA busy_timeout=0')
    writer = sqlite3.connect(path)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("UPDATE market_meta_global SET gen=gen WHERE name='raw'")
    M.demand(conn, KEY)
    with pytest.raises(sqlite3.OperationalError):
        M.resolve_pending(conn)
    assert not conn.in_transaction
    writer.rollback()
    M.resolve_pending(conn)
    assert M.witness(conn, KEY) is not None
    assert not conn.in_transaction
    writer.close(); conn.close()
