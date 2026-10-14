"""Retrospective observations never become bar-close availability evidence."""
import sqlite3

import pandas as pd
import pytest

from trader.data.feed import DataFeed, COLUMNS
from trader.data import market_provenance as mp


class ForbiddenExchange:
    def __getattr__(self, key):
        raise AssertionError('provider touched: '+key)


def dataset(tmp_path):
    path = tmp_path / 'history.db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,taker_buy REAL)')
        con.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)', [
            ('BTC/USDT','1h',0,10,12,9,11,100,None),
            ('BTC/USDT','1h',3600000,11,13,10,12,100,None),
            ('BTC/USDT','1h',7200000,12,14,11,13,100,None),
            ('BTC/USDT','1h',10800000,13,15,12,14,100,None),
            ('BTC/USDT','1h',100,10,5,9,11,100,None)])
    return path


def test_explicit_readonly_projection_close_cut_and_unknown_clocks(tmp_path):
    path = dataset(tmp_path)
    before = path.read_bytes()
    feed = DataFeed(exchange=ForbiddenExchange(), db_path=path,
                    retrospective={'dataset_id':'fixture.v1','cut_ms':9000000})
    statements = []
    feed.db.set_trace_callback(statements.append)
    got = feed.cached_ohlcv('BTC/USDT:USDT','1h')
    assert got.event_time_ms.tolist() == [0,3600000]
    assert got.quality.tolist() == ['UNKNOWN','UNKNOWN']
    assert got.observed_at_ms.isna().all() and got.available_at_ms.isna().all()
    assert got.source.isna().all()
    assert got.attrs['evidence_scope'] == 'retrospective_observation'
    assert feed.retrospective_coverage('BTC/USDT','1h')['bars'] == len(got)
    assert feed.cached_ohlcv('BTC/USDT','1h').attrs['input_identity'] == got.attrs['input_identity']
    with pytest.raises(sqlite3.OperationalError):
        feed.db.execute('CREATE TABLE forbidden(x)')
    with pytest.raises(RuntimeError,match='acquisition forbidden'):
        feed.fetch_ohlcv('BTC/USDT','1h')
    with pytest.raises(ValueError,match='as-of evidence'):
        feed.cached_ohlcv('BTC/USDT','1h',as_of_ms=9000000)
    assert path.read_bytes() == before
    assert not any('CREATE' in q for q in statements[:-1] if 'forbidden' not in q)


def test_heldout_coverage_selects_metadata_only(tmp_path):
    path = dataset(tmp_path)
    feed = DataFeed(db_path=path,retrospective={'dataset_id':'fixture','cut_ms':9000000})
    queries=[]
    feed.db.set_trace_callback(queries.append)
    got = feed.retrospective_coverage('BTC/USDT','1h',start_ms=9000000,
                                     closed_before_ms=20000000,metadata_only=True)
    assert got['bars'] == 1
    assert all('open' not in q.lower() and 'close' not in q.lower() and 'record_json' not in q for q in queries)
    assert got['evidence_scope'] == 'coverage_metadata'


def test_late_receipts_preserved_but_replay_still_refuses(tmp_path):
    path = dataset(tmp_path)
    raw = pd.DataFrame([[0,10.,12.,9.,11.,100.,None]],columns=['ts','open','high','low','close','volume','taker_buy'])
    raw['ts']=pd.to_datetime(raw.ts,unit='ms',utc=True)
    receipt=mp.annotate(raw,instrument_id='binance:future:BTCUSDT',source='fixture.provider',kind='candle',received_ms=20000000,timeframe='1h')
    with sqlite3.connect(path) as con:
        mp.init(con)
        mp.append(con,'candle:BTC/USDT:1h',receipt)
    feed=DataFeed(db_path=path,retrospective={'dataset_id':'fixture','cut_ms':9000000},clock_ms=lambda:21000000)
    got=feed.cached_ohlcv('BTC/USDT','1h')
    assert got.iloc[0].observed_at_ms == 20000000
    assert got.iloc[0].available_at_ms == 20000000
    assert got.iloc[0].source == 'fixture.provider'
    assert feed.replay_ohlcv('BTC/USDT','1h') is None


def test_requires_explicit_identity_and_existing_db(tmp_path):
    with pytest.raises(ValueError,match='explicit database'):
        DataFeed(retrospective={'dataset_id':'fixture','cut_ms':1})
    with pytest.raises(ValueError):
        DataFeed(db_path=tmp_path/'missing',retrospective={'cut_ms':1})
    feed=DataFeed(db_path=tmp_path/'missing',retrospective={'dataset_id':'fixture','cut_ms':1})
    with pytest.raises(sqlite3.OperationalError):
        feed.cached_ohlcv('BTC/USDT','1h')
    assert not (tmp_path/'missing').exists()


def test_receipt_tamper_refuses(tmp_path):
    path=dataset(tmp_path)
    raw=pd.DataFrame([[0,10.,12.,9.,11.,100.]],columns=['ts','open','high','low','close','volume'])
    raw.ts=pd.to_datetime(raw.ts,unit='ms',utc=True)
    with sqlite3.connect(path) as con:
        mp.init(con)
        mp.append(con,'candle:BTC/USDT:1h',mp.annotate(raw,instrument_id='known',source='known',kind='candle',received_ms=20000000,timeframe='1h'))
        con.execute('UPDATE market_revisions SET observed_ms=1')
    feed=DataFeed(db_path=path,retrospective={'dataset_id':'fixture','cut_ms':9000000})
    with pytest.raises(ValueError,match='index_corrupt'):
        feed.cached_ohlcv('BTC/USDT','1h')


def test_unverified_flow_proxy_is_missing_and_matching_source_flow_works(tmp_path):
    path = dataset(tmp_path)
    with sqlite3.connect(path) as con:
        con.execute('UPDATE candles SET taker_buy=volume*0.5')
    descriptor = {'dataset_id':'fixture','cut_ms':9000000}
    unknown = DataFeed(db_path=path,retrospective=descriptor).cached_ohlcv('BTC/USDT','1h')
    assert unknown.taker_buy.isna().all()
    raw = pd.DataFrame([[0,10.,12.,9.,11.,100.,50.]],columns=COLUMNS+['taker_buy'])
    raw.ts = pd.to_datetime(raw.ts,unit='ms',utc=True)
    with sqlite3.connect(path) as con:
        mp.init(con)
        mp.append(con,'candle:BTC/USDT:1h',mp.annotate(raw,instrument_id='known',source='known',kind='candle',received_ms=20000000,timeframe='1h'))
    known = DataFeed(db_path=path,retrospective=descriptor).cached_ohlcv('BTC/USDT','1h')
    assert known.taker_buy.iloc[0] == 50.
    assert pd.isna(known.taker_buy.iloc[1])
