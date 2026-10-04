"""Read-only baseline defect probes; success means the existing defect reproduced.

These are evidence probes, not repaired-behavior regression tests. No network,
production stores, paid providers, Kernel construction or process control.
"""
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
RESULTS = {}
ATTEMPTS = {'network': [], 'production_store': []}
original_connect = sqlite3.connect


def no_network(*args, **kwargs):
    ATTEMPTS['network'].append('blocked')
    raise AssertionError('network forbidden')


def isolated_connect(database, *args, **kwargs):
    from urllib.parse import unquote, urlparse
    name = str(database)
    if name != ':memory:':
        path = Path(unquote(urlparse(name).path) if name.startswith('file:') else name).resolve()
        if not path.is_relative_to(EVIDENCE):
            ATTEMPTS['production_store'].append(str(path))
            raise AssertionError('production store forbidden')
    return original_connect(database, *args, **kwargs)


def probe(root):
    import ccxt
    from trader.core.config import Env
    from trader.core.types import MarketType
    from trader.data.feed import DataFeed
    from trader.data import market_provenance as mp
    from trader.data.binance_usdm_registry import from_binance_usdm_responses
    from trader.engine.executor import Executor
    from trader.engine.watchdog import SCHEMA
    from trader.observability.safety import SafetyHealth, SafetyObserver, HeartbeatPolicy
    from trader.agents.macro_guard import MacroGuard
    from trader.persistence.backup import critical_inventory
    from trader.brain.llm import BrainLLM

    ex = ccxt.binanceusdm()
    feed = DataFeed(exchange=ex, db_path=root / 'candles.db')
    try:
        feed.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    except ccxt.ExchangeError as exc:
        assert 'markets not loaded' in str(exc)
        RESULTS['F1'] = {'reproduced': True, 'exception': type(exc).__name__, 'message': str(exc)}
    else:
        raise AssertionError('F1 no longer reproduces')

    market = {'id': 'BTCUSDT', 'contract': True}
    loaded = SimpleNamespace(id='binanceusdm', market=lambda symbol: market,
        urls={'api': {'fapiPublic': 'https://offline.invalid'}})
    iid, _ = mp.venue_identity(loaded, 'BTC/USDT')
    registry = from_binance_usdm_responses(exchange_info={'symbols': [dict(
        symbol='BTCUSDT', baseAsset='BTC', quoteAsset='USDT', contractType='PERPETUAL')]},
        as_of_ms=1000, account_scope='offline')
    canonical = registry.records[0].instrument_id.value
    assert iid != canonical
    RESULTS['F2'] = {'reproduced': True, 'provenance': iid, 'registry': canonical,
        'scope': 'Identity boundary; Kernel end-to-end proof remains pending.'}

    class Venue:
        quantity = 10
        calls = 0
        def create_order(self, symbol, kind, side, amount, params):
            assert params == {'reduceOnly': True}
            self.calls += 1
            self.quantity -= min(self.quantity, amount)
            raise TimeoutError('response lost after venue execution')
    venue = Venue()
    executor = Executor.__new__(Executor)
    executor.ex = venue
    trade = dict(id='offline-trade', symbol='BTC/USDT', side='long', amount=10, entry_price=100)
    first = executor.close_partial(trade, 5)
    second = executor.close_partial(trade, 5)
    assert first is False and second is False and venue.quantity == 0 and venue.calls == 2
    RESULTS['F4'] = {'reproduced': True, 'initial_quantity': 10, 'intended_reduction': 5,
        'venue_quantity_after_retry': venue.quantity, 'submission_count': venue.calls,
        'scope': 'Lost responses; durable reopen/reconciliation proof remains pending.'}

    heartbeat = root / 'heartbeat.json'
    heartbeat.write_text(json.dumps(dict(schema=SCHEMA, producer='luffy', instance_id='offline',
        sequence=1, started_at=1, timestamp=1000,
        context={'last_successful_cycle_at': 2})))
    health = SafetyHealth(root / 'health.json', clock=lambda:1000, sink=lambda event:None)
    observation = SafetyObserver(health, clock=lambda:1000).heartbeat(
        heartbeat, 'luffy', HeartbeatPolicy(10, 'injected_test_policy'))
    assert observation['status'] == 'FRESH' and health.entry_block() is None
    RESULTS['F5'] = {'reproduced': True, 'publication_age': 0, 'successful_cycle_age': 998,
        'injected_stale_after_s': 10, 'status': observation['status'], 'entry_block': health.entry_block()}

    guard = MacroGuard({'scouts': {'macro_guard': {'finnhub_token': ''}}})
    guard._token = ''
    guard._cache_path = root / 'macro_calendar.json'
    guard._cache_path.write_text(json.dumps(dict(fetched=2000, events=[dict(
        event='future-acquired-event', when=datetime.fromtimestamp(1000, timezone.utc).isoformat())])))
    guard._now = lambda: datetime.fromtimestamp(1000, timezone.utc)
    failed_refresh = []
    def unavailable():
        failed_refresh.append(True)
        return []
    guard._from_forexfactory = unavailable
    with patch('trader.agents.macro_guard.time.time', return_value=1000):
        guard._load_cached()
        guard._cal_ttl = 0  # force refresh, including the failed-refresh fallback variant
        guard._fetch_calendar()
        assessment = guard.check()
    assert not failed_refresh and assessment['active'] and guard._cal_fetched == 2000
    RESULTS['F6'] = {'reproduced': True, 'received_at': guard._cal_fetched,
        'observation_time': 1000, 'failed_refreshes': len(failed_refresh), 'active': assessment['active']}

    source = root / 'source'
    (source / 'data').mkdir(parents=True)
    with sqlite3.connect(source / 'data/luffy.db') as db:
        db.execute('CREATE TABLE offline_sentinel(value TEXT)')
    for name in ('SDD.md', 'STATE.yaml', 'NEXT.yaml', 'config.yaml'):
        (source / name).write_text('offline: true\n')
    (source / 'data/ewa_state.json').write_text('{"agents":{"expert":{"w":2,"n":10}}}')
    inventory = critical_inventory(source)
    paths = [asset.path for asset in inventory.assets]
    assert 'data/ewa_state.json' not in paths
    RESULTS['F7'] = {'reproduced': True, 'retained_ewa_present': True, 'inventory_contains_ewa': False}

    with patch.object(Env, 'deepseek_key', return_value='offline-fake'):
        brain = BrainLLM({'brain': dict(model_fast='offline', model_deep='offline',
            max_tokens_per_call=4000, daily_token_budget=100, purpose_budgets={'research':100})})
    brain._usage_path = root / 'brain_usage.json'
    brain._spend(99, 'research')
    admitted = []
    def respond(**kwargs):
        admitted.append(kwargs['max_tokens'])
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=20), choices=[SimpleNamespace(
            message=SimpleNamespace(content='offline-response'), finish_reason='stop')])
    brain._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=respond)))
    assert brain.chat('offline', purpose='research') == 'offline-response'
    assert admitted == [4000] and brain._tokens_today() == 119
    RESULTS['F9'] = {'reproduced': True, 'budget_remaining_before_call': 1,
        'requested_max_tokens': admitted[0], 'actual_usage_recorded': 119, 'provider': 'injected_fake'}


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    with patch.object(socket.socket, 'connect', no_network), patch.object(socket, 'create_connection', no_network), \
            patch.object(sqlite3, 'connect', isolated_connect), \
            tempfile.TemporaryDirectory(dir=EVIDENCE, prefix='offline-') as directory:
        probe(Path(directory))
    assert not any(ATTEMPTS.values()), ATTEMPTS
    output = dict(kind='BASELINE_DEFECT_REPRODUCTIONS_NOT_REPAIR_PASS', head='53e7c8567c259b2326315a0acdd4b838af030998',
        results=RESULTS, F3='PENDING_COMPLETE_AUDIT_SOURCE_AND_ACTUAL_REVISION_PROBE', attempts=ATTEMPTS)
    (EVIDENCE / 'baseline-results.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))
