"""Deterministic offline snapshot faults; no Kernel boot or real providers."""
import json
import sqlite3
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pandas as pd
import pytest

from trader.core.journal import Journal
from trader.core.types import MarketType
from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from trader.kernel import Kernel
from tests.test_stage1_data_provenance_pit import Clock, Venue, T, Q, frame


class Symbols(Venue):
    def market(self, symbol):
        return dict(id=symbol.split(':')[0].replace('/', ''), contract=True)


def feed_at(tmp_path):
    clock = Clock(T + Q + 100)
    return DataFeed(exchange=Symbols([[T, 10, 11, 9, 10, 5]]),
                    db_path=tmp_path/'candles.db', clock_ms=clock), clock


def kernel_at(tmp_path, feed, clock, monkeypatch):
    k = Kernel.__new__(Kernel)
    k.feed, k.journal = feed, Journal(tmp_path/'journal.db')
    k.cfg = {'timeframes': {'context': [], 'execution': '15m'}}
    k.market_type, k.universe = MarketType.FUTURES, NS()
    k._derivs_for = lambda *a, **kw: None
    k._market_for = lambda **kw: None
    monkeypatch.setattr('trader.kernel.time.time', lambda: clock.at/1000)
    return k


def test_recovered_source_not_hidden_by_recent_failed_refresh(tmp_path):
    f, c = feed_at(tmp_path)
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1) is not None
    c.at += 181_000  # receipt TTL expires, still inside the same candle interval
    provider = f.ex.fetch_ohlcv
    f.ex.fetch_ohlcv = Mock(side_effect=TimeoutError('fixture'))
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)['quality'].eq('STALE').all()
    f.ex.fetch_ohlcv = provider
    recovered = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    assert recovered['quality'].eq('VALID').all()
    assert recovered.observed_at_ms.iloc[-1] == c.at


@pytest.mark.parametrize('case,reason', [
    ('missing', 'missing'), ('stale', 'stale'), ('invalid', 'invalid'),
    ('identity', 'identity_mismatch'), ('mixed_identity', 'identity_mismatch'),
    ('future_available', 'unavailable_at_cut'), ('future_observed', 'unavailable_at_cut'),
    ('partial', 'incomplete'), ('metadata_missing', 'invalid'), ('read', 'read_failure'),
])
def test_precise_refusals_and_no_availability_leak(tmp_path, monkeypatch, case, reason):
    f, c = feed_at(tmp_path)
    data = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    if case == 'stale': c.at += 181_000
    elif case == 'invalid': data['quality'] = 'INVALID'
    elif case == 'identity': data['instrument_id'] = 'binance_usdm:futures:ETHUSDT'
    elif case == 'mixed_identity':
        other = data.copy(); other['instrument_id'] = 'binance_usdm:futures:ETHUSDT'
        data = pd.concat([data, other], ignore_index=True)
    elif case == 'future_available': data['available_at_ms'] = c.at + 1
    elif case == 'future_observed': data['observed_at_ms'] = c.at + 1
    elif case == 'partial': data['bar_state'] = 'PARTIAL'
    elif case == 'metadata_missing': data = data.drop(columns=['revision_id'])
    f.fetch_multi = Mock(side_effect=sqlite3.OperationalError('fixture')) if case == 'read' else Mock(
        return_value={} if case == 'missing' else {'15m': data})
    f.fetch_ohlcv = Mock(return_value=None)
    k = kernel_at(tmp_path, f, c, monkeypatch)
    k._last_snapshot_feed_available = True
    assert k._snapshot_for('BTC/USDT') is None
    assert k._last_snapshot_feed_available is False
    d = k._last_snapshot_diagnostic
    assert d == dict(status='REFUSED', reason=reason, symbol='BTC/USDT',
                    timeframe='15m', as_of_ms=c.at, **({'acquisition': None} if case != 'read' else {}))
    assert not k.journal.query('SELECT * FROM decisions')


def test_same_symbol_context_is_not_acquired_twice(tmp_path, monkeypatch):
    f, c = feed_at(tmp_path)
    data = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    c.at = T + 3_600_000 + 100
    iid, source = f._identity('BTC/USDT')
    context = P.annotate(frame(step=3_600_000), instrument_id=iid, source=source,
                         kind='candle', received_ms=c.at, timeframe='1h')
    data = P.annotate(frame(at=T+2_700_000), instrument_id=iid, source=source,
                      kind='candle', received_ms=c.at, timeframe='15m')
    f.fetch_multi = Mock(return_value={'15m': data, '1h': context})
    f.fetch_ohlcv = Mock(side_effect=AssertionError('duplicate acquisition'))
    k = kernel_at(tmp_path, f, c, monkeypatch)
    k.cfg['timeframes']['context'] = ['1h']
    snap = k._snapshot_for('BTC/USDT')
    assert snap is not None
    assert snap.dfs['BTC_1h'].revision_id.tolist() == context.revision_id.tolist()
    f.fetch_ohlcv.assert_not_called()


def test_fetch_multi_deduplicates_timeframes(tmp_path, monkeypatch):
    f, c = feed_at(tmp_path)
    actual = f.fetch_ohlcv
    f.fetch_ohlcv = Mock(wraps=actual)
    f.fetch_multi('BTC/USDT', ['15m', '15m'])
    assert f.fetch_ohlcv.call_count == 1


def test_real_exclusive_read_lock_refuses_then_recovers(tmp_path, monkeypatch):
    f, c = feed_at(tmp_path)
    f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    f.background_acquisition = True
    f.db.execute('PRAGMA busy_timeout=10')
    holder = sqlite3.connect(f._db_path)
    holder.execute('BEGIN EXCLUSIVE')
    try:
        assert f.fetch_multi('BTC/USDT', ['15m']) == {}
        assert f.fetch_diagnostics()['15m']['reason'] == 'read_failure'
        assert not f.db.in_transaction
    finally:
        holder.rollback(); holder.close()
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)['quality'].eq('VALID').all()


def test_corrupt_read_is_invalid_without_reacquisition(tmp_path):
    f, c = feed_at(tmp_path)
    f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    f.db.execute("UPDATE market_revisions SET record_json=json_set(record_json,'$.close',999)"); f.db.commit()
    f.background_acquisition = True
    count = len(f.ex.calls)
    assert f.fetch_multi('BTC/USDT', ['15m']) == {}
    assert f.fetch_diagnostics()['15m']['reason'] == 'invalid'
    assert len(f.ex.calls) == count


def test_failed_refresh_keeps_only_previous_committed_receipt(tmp_path):
    f, c = feed_at(tmp_path)
    old = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    f.db.execute('PRAGMA busy_timeout=10')
    holder = sqlite3.connect(f._db_path); holder.execute('BEGIN IMMEDIATE')
    statements = []; f.db.set_trace_callback(statements.append)
    c.at += 1
    try:
        result = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1, force=True)
        assert result.revision_id.tolist() == old.revision_id.tolist()
        assert result.observed_at_ms.tolist() == old.observed_at_ms.tolist()
        assert not f.db.in_transaction
        # Initial retained read only; failed persistence never triggers a reread.
        rollback = statements.index('ROLLBACK')
        assert not any(s.startswith('SELECT') for s in statements[rollback+1:])
    finally:
        holder.rollback(); holder.close()
    assert f.db.execute('SELECT count(*) FROM market_revisions').fetchone()[0] == 1


def test_failed_persistence_is_atomic_and_not_completed(tmp_path, monkeypatch):
    f, c = feed_at(tmp_path)
    f.db.execute('PRAGMA busy_timeout=10')
    f.background_acquisition = True
    tasks = []
    monkeypatch.setattr('trader.data.feed.threading.Thread', lambda **kw: NS(start=lambda: tasks.append(kw)))
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1) is None
    holder = sqlite3.connect(f._db_path)
    holder.execute('BEGIN IMMEDIATE')
    # Worker runs deterministically on this thread, using the same short timeout.
    try:
        tasks.pop()['target']()
        health = f.background_acquisition_health()
        assert health['failed'] == 1 and health['completed'] == 0
        assert health['last_outcome'] == 'persistence_unavailable:sqlite_busy'
        assert not f.db.in_transaction
        for table in ('candles', 'market_revisions', 'market_raw_sources', 'candle_floor'):
            assert f.db.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0
    finally:
        holder.rollback(); holder.close()
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1) is None
    tasks.pop()['target']()
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)['quality'].eq('VALID').all()


def test_warm_worker_does_not_report_failed_refresh_as_completed(tmp_path, monkeypatch):
    f, c = feed_at(tmp_path)
    original = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    f.db.execute('PRAGMA busy_timeout=10')
    f.background_acquisition = True
    tasks = []
    monkeypatch.setattr('trader.data.feed.threading.Thread', lambda **kw: NS(start=lambda: tasks.append(kw)))
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1, force=True) is not None
    holder = sqlite3.connect(f._db_path); holder.execute('BEGIN IMMEDIATE')
    try:
        tasks.pop()['target']()
        health = f.background_acquisition_health()
        assert health['failed'] == 1 and health['completed'] == 0
        assert health['last_outcome'] == 'persistence_unavailable:sqlite_busy'
        current = f.latest_ohlcv('BTC/USDT', limit=1)
        assert current.revision_id.tolist() == original.revision_id.tolist()
    finally:
        holder.rollback(); holder.close()


def test_delayed_commit_does_not_rewrite_refused_decision(tmp_path, monkeypatch):
    from trader.learning.capture_runtime import missed_snapshot
    from trader.learning import capture as C
    f, c = feed_at(tmp_path)
    f.background_acquisition = True
    entered, release = threading.Event(), threading.Event()
    append = P.append
    def delayed(conn, *a, **kw):
        result = append(conn, *a, **kw)
        entered.set()
        assert release.wait(10)
        return result
    # Initialize schema before worker; only receipts/projection are delayed.
    f.db
    monkeypatch.setattr(P, 'append', delayed)
    assert f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1) is None
    assert entered.wait(10)
    try:
        assert f.latest_ohlcv('BTC/USDT', limit=1) is None
        k = kernel_at(tmp_path, f, c, monkeypatch)
        # Use actual feed reads; both requested series remain unavailable.
        assert k._snapshot_for('BTC/USDT') is None
        d = dict(k._last_snapshot_diagnostic)
        with k.journal._tx() as db:
            C.ensure(db)
            missed_snapshot(db, 'fixture-scan', 'BTC/USDT', c.at, {'snapshot_refusal': d})
        before = k.journal.query('SELECT sha256,payload FROM learning_source_blobs ORDER BY sha256')
    finally:
        release.set()
    assert f._acquisition_lock.acquire(timeout=10)
    f._acquisition_lock.release()
    after = k.journal.query('SELECT sha256,payload FROM learning_source_blobs ORDER BY sha256')
    assert before == after
    assert any('snapshot_refusal' in row['payload'] for row in after)
    c.at += 1
    resumed = DataFeed(exchange=f.ex, db_path=f._db_path, clock_ms=c)
    current = resumed.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    assert current['quality'].eq('VALID').all()
    lineage = dict(schema_version=P.SCHEMA, as_of_ms=c.at, instrument_id=current.instrument_id.iloc[0],
                   frames={'15m':current.revision_id.tolist()})
    assert P.resolve_lineage([resumed.db], lineage)
    # New receipt cannot qualify a cut before it was received.
    assert resumed.ohlcv_asof('BTC/USDT', as_of_ms=T+Q+99) is None


def test_multisymbol_refusal_reset_and_replay(tmp_path, monkeypatch):
    f, c = feed_at(tmp_path)
    data = f.fetch_ohlcv('BTC/USDT', limit=1, min_bars=1)
    k = kernel_at(tmp_path, f, c, monkeypatch)
    f.fetch_multi = Mock(side_effect=[sqlite3.OperationalError('private detail'), {'15m':data}, {}])
    f.fetch_ohlcv = Mock(return_value=None)
    assert k._snapshot_for('ETH/USDT') is None
    first = dict(k._last_snapshot_diagnostic)
    snap = k._snapshot_for('BTC/USDT')
    assert snap is not None and k._last_snapshot_feed_available
    P.resolve_lineage([f.db], json.loads(snap.market_provenance_json))
    assert k._snapshot_for('SOL/USDT') is None
    assert k._last_snapshot_diagnostic['symbol'] == 'SOL/USDT'
    assert k._last_snapshot_diagnostic['reason'] == 'missing'
    assert first['reason'] == 'read_failure' and 'private' not in json.dumps(first)


def test_cycle_retains_precise_refusal_without_blocking_later_symbols(tmp_path, monkeypatch):
    from tests.test_hierarchical_admission import full_cycle_kernel
    from trader.core.config import load_config
    k = full_cycle_kernel(tmp_path, load_config(), monkeypatch)
    original = k._snapshot_for.side_effect
    seen = []
    def snapshot(symbol, **kw):
        seen.append(symbol)
        if len(seen) == 1:
            k._last_snapshot_feed_available = False
            k._last_snapshot_diagnostic = dict(status='REFUSED',reason='read_failure',
                symbol=symbol,timeframe='15m',as_of_ms=1_800_000_000_000)
            return None
        return original(symbol, **kw)
    k._snapshot_for.side_effect = snapshot
    result = k.cycle()
    assert len(seen) > 1 and result['deep_processed'] == len(k._symbol_roles.deep)-1
    assert result['snapshot_refusals'] == {'read_failure':1}
    assert result['entries'] == 0
    blobs = k.journal.query('SELECT payload FROM learning_source_blobs')
    assert any('snapshot_refusal' in row['payload'] and 'read_failure' in row['payload'] for row in blobs)


@pytest.mark.parametrize('stage', ['register', 'record_action'])
def test_refusal_capture_failure_is_atomic(tmp_path, monkeypatch, stage):
    from trader.learning import capture as C, capture_runtime as R
    journal = Journal(tmp_path/'refusal.db')
    with journal._tx() as db:
        C.ensure(db)
    tables = ('learning_source_blobs', 'learning_registrations', 'learning_actions',
              'learning_outcome_captures', 'decisions')
    before = {t:journal.query('SELECT * FROM '+t) for t in tables}
    original = getattr(C, stage)
    def failed(*a, **kw):
        original(*a, **kw)
        raise sqlite3.OperationalError('deterministic refusal evidence failure')
    monkeypatch.setattr(C, stage, failed)
    with journal._tx() as db:
        result = C.safely(db, 'fixture-refusal', R.missed_snapshot,
            'scan', 'BTC/USDT', T+Q, {'snapshot_refusal':dict(reason='stale',as_of_ms=T+Q)})
        assert result is None
    assert {t:journal.query('SELECT * FROM '+t) for t in tables} == before
    assert len(journal.query('SELECT * FROM learning_capture_failures')) == 1
    assert journal._conn().execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
