"""Stored compiled specs supply actual selected-symbol snapshot/peer timeframe keys and bounded lengths."""
from types import SimpleNamespace

import pandas as pd

from trader.core.journal import Journal
from trader.kernel import Kernel
from trader.strategy.fill_timing import history_requirements
from tests.test_compile import _spec


def test_population_requirements_reach_actual_kernel_fetches_including_nondefault_timeframes(tmp_path, monkeypatch):
    spec = _spec(timeframe='4h', entry_long="htf('1d', zscore(close, 450)) > 0", filters=[], regime_filter=[])
    j = Journal(tmp_path / 'journal.db')
    j.upsert_spec(spec, state='paper')
    k = Kernel.__new__(Kernel)
    k.journal = j
    k.cfg = {'timeframes': {'context': ['15m', '1h'], 'execution': '15m'}}
    monkeypatch.setattr('trader.strategy.library.register_evaluator', lambda *args: None)
    population = k._load_spec_population()
    assert len(population) == 1
    required = history_requirements(spec)
    assert {'4h', '1d'} <= k._history_limits.keys()
    assert k._history_limits['1d'] >= required['htf']['1d'] > 400
    main, peers = [], []
    class Feed:
        _local = SimpleNamespace(multi_diagnostics={})
        ttl = {}
        def fetch_multi(self, symbol, tfs, limit=400):
            main.extend((symbol, tf, limit) for tf in tfs)
            return {}
        def fetch_ohlcv(self, *args, **kwargs):
            return None
        def cached_ohlcv(self, symbol, tf, limit, as_of_ms):
            peers.append((symbol, tf, limit))
            return pd.DataFrame({'close': [1.]})
    k.feed = Feed()
    monkeypatch.setattr('trader.engine.entry_authority.proposal_binding', lambda *args: None)
    assert k._snapshot_for('S0/USDT') is None  # intentionally empty fixture: measure requests, not a fabricated valid snapshot
    assert {s for s, _, _ in main} == {'S0/USDT'}
    assert ('S0/USDT', '1d', k._history_limits['1d']) in main
    assert ('S0/USDT', '4h', k._history_limits['4h']) in main
    k._admission_receipt = None
    result = k._peer_frames(['S0/USDT', 'S1/USDT'])
    assert set(result) == {'S0/USDT', 'S1/USDT'}
    assert {s for s, _, _ in peers} == {'S0/USDT', 'S1/USDT'}
    assert all((s, '1d', k._history_limits['1d']) in peers for s in result)
    assert all((s, '4h', k._history_limits['4h']) in peers for s in result)
