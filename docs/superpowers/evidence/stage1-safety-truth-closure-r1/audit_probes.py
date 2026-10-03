"""Offline characterization of closure blockers; passes document defects.

These are not acceptance tests and grant no real execution authority. All
orders use the existing test-only Venue and all journals are temporary.
"""
import math

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType
from trader.engine.executor import Executor
from trader.engine.risk import RiskManager
from tests.test_entry_recovery import enter, setup  # noqa: F401


def test_executor_accepts_without_risk_decision(setup):
    venue, journal, executor, decision = setup
    # No RiskManager is constructed or called by this fixture or Executor.
    assert enter(executor, decision) is not None
    assert len([o for o in venue.sent if not o[4].get('reduceOnly')]) == 1


def test_completed_request_repeats_after_restart(setup):
    venue, journal, executor, decision = setup
    assert enter(executor, decision) is not None
    restarted = Executor(venue, Journal(journal.db_path), load_config(), MarketType.FUTURES)
    assert enter(restarted, decision) is not None
    entries = [o for o in venue.sent if not o[4].get('reduceOnly')]
    assert len(entries) == 2
    assert entries[0][4]['newClientOrderId'] != entries[1][4]['newClientOrderId']
    assert len(journal.open_trades()) == 2
    assert {r['decision_id'] for r in journal.open_trades()} == {decision.id}


def test_non_authoritative_equity_can_still_size(tmp_path):
    journal = Journal(tmp_path / 'risk.db')
    risk = RiskManager(load_config(), journal)
    risk.update_equity(1000.0)
    assert risk.update_equity(1000.0, authoritative=False)['risk_state'] == 'ok'
    answer = risk.check_entry(ControlState.ACTIVE, 'BTC/USDT', 100.0, 2.0,
                              .05, [], 1000.0, 100, 'futures')
    assert answer.ok


def test_nonfinite_entry_price_is_not_refused(tmp_path):
    journal = Journal(tmp_path / 'risk.db')
    risk = RiskManager(load_config(), journal)
    risk.update_equity(1000.0)
    answer = risk.check_entry(ControlState.ACTIVE, 'BTC/USDT', float('nan'),
                              2.0, .05, [], 1000.0, 100, 'futures')
    assert answer.ok and not math.isfinite(answer.amount)


def test_stale_feed_fallback_has_no_quality_envelope(tmp_path, monkeypatch):
    from trader.data.feed import DataFeed
    from ccxt import RequestTimeout
    class Offline:
        def fetch_ohlcv(self, *args, **kwargs):
            raise RequestTimeout('fixture offline')
    now_ms = 1_800_000_000_000
    monkeypatch.setattr('trader.data.feed.time.time', lambda: now_ms / 1000)
    feed = DataFeed(exchange=Offline(), db_path=tmp_path / 'candles.db')
    frame = feed._frame([[now_ms - 86_400_000, 100, 101, 99, 100, 10]])
    feed._store_save('BTC/USDT', '15m', frame, now_ms)
    returned = feed.fetch_ohlcv('BTC/USDT', '15m', limit=1, min_bars=1)
    assert len(returned) == 1
    assert int(returned.ts.iloc[-1].timestamp() * 1000) == now_ms - 86_400_000
    assert not {'quality', 'received_at', 'source'} & set(returned.columns)
    feed.db.close()


def test_future_cached_observation_is_returned_after_clock_step(tmp_path, monkeypatch):
    from trader.data.feed import DataFeed
    now_ms = 1_800_000_000_000
    monkeypatch.setattr('trader.data.feed.time.time', lambda: now_ms / 1000)
    feed = DataFeed(exchange=object(), db_path=tmp_path / 'candles.db')
    frame = feed._frame([[now_ms + 900_000, 100, 101, 99, 100, 10]])
    feed._cache[('BTC/USDT', '15m')] = (now_ms / 1000, frame)
    returned = feed.fetch_ohlcv('BTC/USDT', '15m', min_bars=1)
    assert returned.ts.iloc[-1].timestamp() * 1000 > now_ms


def test_htf_reads_hour_close_before_hour_available():
    import pandas as pd
    from trader.strategy.dsl import parse, evaluate_bool
    from trader.strategy.features import FeatureCtx
    # Feed timestamps identify bar opens (core.types.closed_bars).
    base = pd.DataFrame({'ts': pd.to_datetime(['2026-01-01T00:15:00Z']),
                         'close': [100.0]})
    hourly = pd.DataFrame({'ts': pd.to_datetime(['2026-01-01T00:00:00Z']),
                           'close': [200.0]})
    ctx = FeatureCtx(frames={'15m': base, '1h': hourly}, tf='15m')
    # At 00:30 (base close), the 01:00 hourly close is not available.
    assert bool(evaluate_bool(parse("htf('1h', close > 150)"), ctx)[0])
