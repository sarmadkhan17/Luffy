"""DATA-05: retained revisions establish latest and exact historical truth."""
import json
import sqlite3

import pytest

from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from tests.test_stage1_data_provenance_pit import Clock, Venue, frame, T, Q


def retained(tmp_path):
    clock = Clock(T + Q)
    feed = DataFeed(exchange=Venue([]), db_path=tmp_path / 'candles.db', clock_ms=clock)
    iid, source = feed._identity('BTC/USDT')
    key = feed._series_key('BTC/USDT', '15m')
    def append(at, value):
        receipt = P.annotate(frame(value=value), instrument_id=iid, source=source,
                             kind='candle', received_ms=at, timeframe='15m')
        feed._store_save('BTC/USDT', '15m', receipt, now_ms=at)
        return receipt.revision_id.iloc[0]
    original = append(clock.at, 10)
    same_value = append(clock.at + 100, 10)
    corrected = append(clock.at + 200, 20)
    future = append(clock.at + 300, 30)
    lineage = dict(schema_version=P.SCHEMA, as_of_ms=clock.at, instrument_id=iid,
                   frames={'15m': [original]})
    return feed, clock, key, lineage, [original, same_value, corrected, future]


def test_exact_cuts_latest_lineage_and_restart(tmp_path):
    feed, clock, key, lineage, ids = retained(tmp_path)
    at = clock.at
    before = feed.db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?', (ids[0],)).fetchone()[0]
    assert feed.ohlcv_asof('BTC/USDT', as_of_ms=at - 1) is None
    for offset, expected in [(0, 0), (99, 0), (100, 1), (199, 1), (200, 2), (299, 2), (300, 3)]:
        clock.at = at + offset
        historical = feed.ohlcv_asof('BTC/USDT', as_of_ms=clock.at).iloc[0]
        latest = feed.latest_ohlcv('BTC/USDT').iloc[0]
        assert historical.revision_id == latest.revision_id == ids[expected]
        assert historical.close == [10, 10, 20, 30][expected]
        assert historical.available_at_ms <= clock.at
    records = [json.loads(r[0]) for r in feed.db.execute('SELECT record_json FROM market_revisions ORDER BY rowid')]
    assert len(records) == 4
    assert records[0]['content_hash'] == records[1]['content_hash']
    assert [r['supersedes'] for r in records] == [None, *ids[:-1]]
    assert json.dumps(json.loads(before), sort_keys=True) == json.dumps(records[0], sort_keys=True)
    reconstructed = P.resolve_lineage([feed.db], lineage)
    assert reconstructed[ids[0]]['content_hash'] == records[0]['content_hash']
    assert reconstructed[ids[0]]['raw_json'] == records[0]['raw_json']
    assert reconstructed[ids[0]]['source'] == records[0]['source']
    feed.db.close()
    reopened = DataFeed(exchange=Venue([]), db_path=tmp_path / 'candles.db', clock_ms=clock)
    assert reopened.latest_ohlcv('BTC/USDT').revision_id.tolist() == [ids[3]]
    assert reopened.ohlcv_asof('BTC/USDT', as_of_ms=at).revision_id.tolist() == [ids[0]]
    assert reopened.replay_ohlcv('BTC/USDT').revision_id.tolist() == [ids[0]]
    assert P.resolve_lineage([reopened.db], lineage) == reconstructed


@pytest.mark.parametrize('mode', ['as_of', 'latest', 'stream', 'replay', 'window', 'lineage'])
@pytest.mark.parametrize('column', ['event_ms', 'available_ms', 'observed_ms', 'revision_id'])
def test_selection_index_tamper_fails_closed(tmp_path, mode, column):
    feed, clock, key, lineage, ids = retained(tmp_path)
    # Corrupt only unhashed SQL selection metadata; signed JSON stays intact.
    with sqlite3.connect(tmp_path / 'candles.db') as db:
        value = 'f' * 64 if column == 'revision_id' else clock.at - 1
        db.execute(f'UPDATE market_revisions SET {column}=? WHERE revision_id=?', (value, ids[0]))
    options = {'as_of_ms': clock.at}
    if mode == 'stream': options['revision_stream'] = True
    if mode == 'replay': options['replay_tf'] = '15m'
    if mode == 'window': options['window_start_ms'] = T
    reason = 'lineage_receipt_missing' if mode == 'lineage' and column == 'revision_id' else 'index_corrupt'
    with pytest.raises(ValueError, match=reason):
        if mode == 'lineage':
            P.resolve_lineage([feed.db], lineage)
        elif mode == 'latest':
            feed.latest_ohlcv('BTC/USDT')
        else:
            P.load(feed.db, key, **options)


def test_future_index_cannot_rebase_receipt_into_past(tmp_path):
    feed, clock, key, lineage, ids = retained(tmp_path)
    with sqlite3.connect(tmp_path / 'candles.db') as db:
        db.execute('UPDATE market_revisions SET available_ms=?,observed_ms=? WHERE revision_id=?',
                   (clock.at - 1, clock.at - 1, ids[0]))
    with pytest.raises(ValueError, match='index_corrupt'):
        P.load(feed.db, key, as_of_ms=clock.at - 1)


def test_owner_read_marks_corrupted_index_unavailable(tmp_path):
    from tests.test_data02_decision_lineage import read, reason
    feed, clock, key, lineage, ids = retained(tmp_path)
    with sqlite3.connect(tmp_path / 'candles.db') as db:
        db.execute('UPDATE market_revisions SET observed_ms=? WHERE revision_id=?', (clock.at - 1, ids[0]))
    assert 'market_revision_index_corrupt' in reason(read(tmp_path, lineage))


@pytest.mark.parametrize('case,reason', [('missing', 'receipt_missing'), ('hash', 'content_corrupt'),
    ('instrument', 'instrument_mismatch'), ('transform', 'transform_mismatch'),
    ('schema', 'schema_incompatible'), ('future', 'not_yet_available')])
def test_historical_lineage_negative_controls_after_correction(tmp_path, case, reason):
    feed, clock, key, lineage, ids = retained(tmp_path)
    if case == 'missing': lineage['frames']['15m'] = ['0' * 64]
    if case == 'instrument': lineage['instrument_id'] = 'binance_usdm:futures:ETHUSDT'
    if case == 'schema': lineage['schema_version'] = 'market.receipt.v0'
    if case == 'future': lineage['frames']['15m'] = [ids[3]]
    if case == 'hash':
        with sqlite3.connect(tmp_path / 'candles.db') as db:
            raw = json.loads(db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?', (ids[0],)).fetchone()[0])
            raw['content_hash'] = '0' * 64
            db.execute('UPDATE market_revisions SET record_json=? WHERE revision_id=?', (P.encode(raw), ids[0]))
        with pytest.raises(ValueError, match='content_corrupt'):
            feed.ohlcv_asof('BTC/USDT', as_of_ms=clock.at)
    feed.db.close()
    with sqlite3.connect(tmp_path / 'candles.db') as db:
        with pytest.raises(ValueError, match=reason):
            P.resolve_lineage([db], lineage, transforms={'candle': 'candle.v2'} if case == 'transform' else None)
