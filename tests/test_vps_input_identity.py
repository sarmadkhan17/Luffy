import ccxt
import pytest
from trader.data.feed import DataFeed
from trader.data import market_provenance as mp


def exchange(strict=False):
    ex = ccxt.binanceusdm({'options': {'defaultType': 'future'}})
    ex.set_markets([dict(id='BTCUSDT', symbol='BTC/USDT:USDT', base='BTC', quote='USDT',
        settle='USDT', type='swap', contract=True, linear=True, inverse=False, swap=True,
        future=False, spot=False, option=False, active=True, precision={}, limits={})])
    if strict:
        lookup=ex.market
        def settled_only(symbol):
            if ':' not in symbol:
                raise ccxt.BadSymbol('settled catalogue requires explicit settlement')
            return lookup(symbol)
        ex.market=settled_only
    return ex


def test_loaded_settled_catalogue_resolves_alias_and_preserves_explicit_identity():
    ex = exchange()
    expected = mp.venue_identity(ex, 'BTC/USDT:USDT')
    assert expected[0] == 'binance_usdm:futures:BTCUSDT'
    assert mp.venue_identity(ex, 'BTC/USDT') == expected
    assert mp.venue_identity(ex, 'MISSING/USDT') == (None, None)


def test_cold_catalogue_is_never_inferred_from_alias():
    assert mp.venue_identity(ccxt.binanceusdm(), 'BTC/USDT') == (None, None)


@pytest.mark.parametrize('bad', [{'linear':False}, {'swap':False}, {'contract':False}, {'settle':'USDC'}])
def test_alias_cannot_select_foreign_contract(bad):
    ex = exchange(strict=True)
    ex.markets['BTC/USDT:USDT'].update(bad)
    assert mp.venue_identity(ex, 'BTC/USDT') == (None, None)


def test_new_observation_is_valid_without_promoting_older_unknown_receipts(tmp_path, monkeypatch):
    now = 1800000900000
    cold=ccxt.binanceusdm()
    raw=lambda p:[[now-1800000,10,11,9,10,5]]
    monkeypatch.setattr(cold, 'fapiPublicGetKlines', raw)
    old=DataFeed(exchange=cold,db_path=tmp_path/'candles.db',clock_ms=lambda:now-1000)
    unknown=old.fetch_ohlcv('BTC/USDT',limit=1,min_bars=1)
    rid=unknown.iloc[-1].revision_id
    before=old.db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?',(rid,)).fetchone()[0]
    ex = exchange(strict=True)
    monkeypatch.setattr(ex, 'fapiPublicGetKlines', raw)
    feed = DataFeed(exchange=ex, db_path=tmp_path/'candles.db', clock_ms=lambda:now)
    frame = feed.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    assert frame.iloc[-1].quality == 'VALID'
    assert frame.iloc[-1].instrument_id == 'binance_usdm:futures:BTCUSDT'
    assert mp.current_frame_status(frame, '15m', now, 120)[1] == 'valid'
    assert feed.ohlcv_asof('BTC/USDT', as_of_ms=now-1, limit=1) is None
    assert feed.db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?',(rid,)).fetchone()[0]==before
