"""Installed-CCXT cold boundary and real Kernel cycle, fully offline."""
from types import SimpleNamespace
import ccxt
import numpy as np
import pytest
from trader.data.feed import DataFeed
from trader.core.types import MarketType, ControlState
from trader.engine.watchdog import Heartbeat, read_heartbeat
from trader.kernel import Kernel

NOW = 1800000900000


def cold_feed(tmp_path, monkeypatch):
    ex = ccxt.binanceusdm({'apiKey': '', 'secret': ''})
    calls = []
    def transport(params):
        calls.append(params)
        return [[NOW - 900000, 10, 11, 9, 10, 5]]
    monkeypatch.setattr(ex, 'fapiPublicGetKlines', transport)
    feed = DataFeed(exchange=ex, db_path=tmp_path/'candles.db', clock_ms=lambda: NOW)
    return feed, calls


def test_installed_ccxt_cold_fetch_retains_unknown_receipt(tmp_path, monkeypatch):
    feed, calls = cold_feed(tmp_path, monkeypatch)
    with pytest.raises(ccxt.ExchangeError, match='markets not loaded'):
        feed.ex.market('BTC/USDT')
    result = feed.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    assert calls and len(result) == 1
    assert result.iloc[0].quality == 'UNKNOWN'
    assert np.isnan(result.iloc[0].close)
    assert result.iloc[0].observed_at_ms == NOW
    assert feed.ohlcv_asof('BTC/USDT', as_of_ms=NOW-1) is None


def test_feed_initialization_is_independent_and_failure_is_contained(tmp_path, monkeypatch):
    feed, _ = cold_feed(tmp_path, monkeypatch)
    def unavailable():
        raise ccxt.ExchangeError('offline initialization failure')
    monkeypatch.setattr(feed.ex, 'load_markets', unavailable)
    assert feed.initialize_markets() is False
    assert feed.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1).iloc[0].quality == 'UNKNOWN'


def test_frozen_kernel_cycle_reaches_exit_and_successful_heartbeat(tmp_path, monkeypatch):
    import trader.kernel as module
    from trader.core.journal import Journal
    feed, calls = cold_feed(tmp_path, monkeypatch)
    k = Kernel.__new__(Kernel)
    k.feed = feed
    k.journal = Journal(tmp_path/'journal.db')
    k.journal.kv_set('control_state', 'FROZEN')
    k.state_machine = SimpleNamespace(refresh=lambda:ControlState.FROZEN, can_enter=lambda:False)
    k.market_type = MarketType.FUTURES
    k.cfg = {'timeframes': {'execution':'15m'}}
    k.executor = SimpleNamespace(recover_entries=lambda:None, recovery_pending=lambda:False)
    k._risk_step = lambda:(100, {'equity':100, 'drawdown_pct':0})
    k._drain_close_requests = lambda:0
    k._drain_panic = lambda:None
    k.macro_guard = SimpleNamespace(check=lambda:{})
    k.news_guard = SimpleNamespace(check=lambda:{})
    k._macro_step = lambda _:None
    k._publish_news_guard = lambda _:None
    k._record_risk_assessment = lambda *a:None
    k._funding_map = k._oi_map = lambda *a, **kw:{}
    from tests.admission_cycle_fixture import install
    install(k, [])
    k._universe_frames = lambda _:{}
    k._attention_call = lambda *a:None
    exits = []
    k._service_exposure = lambda scanned, peers:exits.append('managed') or 1
    k._maybe_resolve_outcomes = k._record_excursions = lambda:None
    k._equity_provenance = lambda _:{}
    k._portfolio_checkpoint = lambda:{}
    k.heartbeat = Heartbeat(path=tmp_path/'heartbeat.json', clock=lambda:NOW/1000)
    monkeypatch.setattr(module.time, 'time', lambda:NOW/1000)
    result = k.cycle()
    assert calls and exits == ['managed'] and result['entries'] == 0
    beat = read_heartbeat(k.heartbeat.path, 'luffy', NOW/1000)
    assert beat['context']['state'] == 'FROZEN'
    assert beat['context']['last_successful_cycle_at'] == NOW/1000


def test_provenance_registry_kernel_snapshot_one_identity(tmp_path, monkeypatch):
    import json
    from trader.core.config import load_config
    from trader.core.journal import Journal
    from trader.core.instrument_registry import InstrumentId, is_canonical_instrument_id
    from tests.entry_authority_fixtures import bind
    from trader.data.binance_usdm_registry import from_binance_usdm_responses
    from tests.registry_observation_fixtures import _symbol
    import trader.kernel as module
    monkeypatch.setattr(module.time, 'time', lambda: NOW/1000)
    feed, calls = cold_feed(tmp_path, monkeypatch)
    journal = Journal(tmp_path/'journal.db')
    _, capability = bind(journal, feed.ex, load_config())
    registry = from_binance_usdm_responses(exchange_info={'symbols':[_symbol()]},
        as_of_ms=NOW, account_scope='offline')
    iid = registry.records[0].instrument_id
    assert iid.value == capability['record']['instrument_id']['venue']+':futures:BTCUSDT'
    df = feed.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    feed.fetch_multi = lambda *a:{'15m':df}
    original_fetch = feed.fetch_ohlcv
    feed.fetch_ohlcv = lambda *a, **kw:None  # no auxiliary BTC frame in this snapshot
    k = Kernel.__new__(Kernel)
    k.feed, k.journal = feed, journal
    k.cfg = {'timeframes': {'context':[], 'execution':'15m'}}
    k.market_type = MarketType.FUTURES
    k.universe = SimpleNamespace()
    k._derivs_for = lambda *a, **kw:None
    k._market_for = lambda **kw:None
    snapshot = k._snapshot_for('BTC/USDT')
    assert snapshot is not None, 'canonical Registry binding rejected its own data identity'
    lineage = json.loads(snapshot.market_provenance_json)
    assert df.instrument_id.iloc[0] == lineage['instrument_id'] == iid.value
    assert is_canonical_instrument_id(iid.value)
    assert not is_canonical_instrument_id('binanceusdm:futures:BTCUSDT')
    assert InstrumentId('binanceusdm', MarketType.FUTURES, 'BTCUSDT') == iid
    # Nearby variant: loaded markets disappear but exact Registry binding survives.
    feed.fetch_ohlcv = original_fetch
    feed.ex.market = lambda _: (_ for _ in ()).throw(ccxt.ExchangeError('markets not loaded'))
    assert feed._identity('BTC/USDT')[0] == iid.value
    # The exact binding cannot qualify another instrument or another venue.
    assert feed._identity('ETH/USDT')[0] is None
    feed.ex.id = 'another-venue'
    assert feed._identity('BTC/USDT')[0] is None
