"""Offline proofs at the admission, acquisition and strategy-selection seams."""
import json
from types import SimpleNamespace as NS

import pytest

from tests.test_contained_background_data import _queued_feed
from tests.test_hierarchical_admission import (
    CUT, broad_kernel, cfg, full_cycle_kernel, offline, policy,
)
from trader import attention_admission as admission
from trader.core.types import Action, Snapshot, StrategySignal
from trader.data.broad_crypto import observe
from trader.engine import orchestrator as orchestration
from trader.strategy import library
from trader.strategy.plan_owner import decision_owner


def install_strategy(kernel, monkeypatch, owner, direction):
    monkeypatch.setattr(orchestration, 'classify', lambda *a: {'regime': 'RANGING', 'adx': 20})
    monkeypatch.setattr(orchestration, 'htf_trend_score', lambda *a: 0)
    monkeypatch.setitem(library.EVALUATORS, 'integration_fixture', lambda genome, snap:
        StrategySignal(owner, owner, snap.symbol, Action(direction), .6, 'offline', {}))
    kernel.population = [(NS(id=owner, is_trade_eligible=True),
                          NS(family='integration_fixture', markets=['futures'], params={}))]
    kernel.orchestrator = orchestration.Orchestrator([], kernel.journal, cfg=kernel.cfg)


def test_exact_admission_cold_queue_then_warm_selection(tmp_path, cfg, policy, monkeypatch):
    kernel = broad_kernel(tmp_path, cfg, n=3)
    monkeypatch.setattr('trader.data.broad_crypto.time.time', lambda: CUT / 1000)
    observed, _, _ = observe(kernel, [])
    receipt = admission.persist(kernel.journal, observed, policy)
    symbol = receipt['admitted_symbols'][0]
    assert observed['rows'][symbol]['instrument_id'] == 'binance_usdm:futures:' + symbol.replace('/', '')
    feed, tasks = _queued_feed(tmp_path, monkeypatch)
    kernel.feed = feed

    def snapshot(symbol, universe=None):
        frame = feed.fetch_ohlcv(symbol, '15m')
        if frame is None:
            return None
        return Snapshot(symbol, '2026-10-13T00:00:00+00:00', float(frame.close.iloc[-1]), {'15m': frame})

    kernel._snapshot_for = snapshot
    assert kernel._candidate_snapshot_for(symbol, receipt, {}) is None
    assert feed.background_acquisition_health()['pending'] == 1
    assert admission.latest(kernel.journal)['receipt_id'] == receipt['receipt_id']
    feed._acquisition_lock.release()
    feed._start_background_worker()
    tasks.pop()['target']()
    snap = kernel._candidate_snapshot_for(symbol, receipt, {})
    assert snap.admission_context['receipt_id'] == receipt['receipt_id']
    install_strategy(kernel, monkeypatch, 'held-plan', 'BUY')
    decision = kernel.orchestrator.decide(snap, kernel.population)
    assert decision.action == Action.BUY and decision.symbol == symbol
    kernel.orchestrator.journalize(snap, decision, 'futures', 'paper')
    row = kernel.journal.query('SELECT * FROM decisions WHERE id=?', (decision.id,))[0]
    assert decision_owner(decision) == 'held-plan'
    assert json.loads(row['strategy_selection_json']) == decision.strategy_selection
    assert admission.latest(kernel.journal)['receipt_id'] == receipt['receipt_id']


@pytest.mark.parametrize('owner,expected_score', [('held-plan', -.6), ('foreign-plan', None)])
def test_actual_cycle_owner_exit_with_busy_acquisition(tmp_path, cfg, monkeypatch, owner, expected_score):
    kernel = full_cycle_kernel(tmp_path, cfg, monkeypatch)
    trade = {'id': 'held', 'symbol': 'S00/USDT', 'strategy_id': 'held-plan'}
    kernel.journal.open_trades = lambda: [trade]
    feed, _ = _queued_feed(tmp_path, monkeypatch)
    kernel.feed = feed
    # Existing warm snapshots remain available while unrelated cold work waits.
    feed.fetch_ohlcv('COLD/USDT', '15m')
    install_strategy(kernel, monkeypatch, owner, 'SELL')
    stats = kernel.cycle()
    assert stats['decisions'] == 2
    assert 'S00/USDT' in kernel._admission_receipt['admitted_symbols']
    calls = [call for call in kernel._manage_one.call_args_list if call.args[0]['id'] == 'held']
    assert len(calls) == 1 and calls[0].args[2] == expected_score
    kernel._detect_exchange_exits.assert_any_call('S00/USDT')
    # DA-05 also prepares a bounded rotating history cohort during cycle().
    # The existing cold request and history requests share the same DA-02 queue.
    health = feed.background_acquisition_health()
    assert ('COLD/USDT', '15m') in feed._acquisition_queue
    history = [r for r in feed._acquisition_queue.values() if r.get('history')]
    assert len(history) <= feed.HISTORY_BATCH
    assert health['pending'] == 1 + len(history)
    feed.cancel_background_acquisition()
    feed._acquisition_lock.release()
