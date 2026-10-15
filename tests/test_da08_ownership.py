"""DA-08: admission, peer and telemetry limits have distinct ownership.

Synthetic fixture data only. DA-09's receipt tests are retained unchanged from
58ffe821b277af91e7f1da75ebdadb74eb1707b7 in the owner's offline closure bundle.
"""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from trader import attention_admission as A
from trader.core.journal import Journal
from trader.kernel import Kernel
from trader.observability.collector import Collector
from tests.test_hierarchical_admission import (
    CUT, cfg, offline, observation, full_cycle_kernel,
)  # noqa: F401


def test_baseline_receipt_and_rotation_bytes():
    expected = json.loads(Path('tests/fixtures/da08-admission-parity.json').read_text())
    policy = A.AdmissionPolicy(12, 2, 2, 0)
    actual = []
    for n in (0, 1, 12, 30, 439):
        for scores in (False, True):
            obs = observation(n, scores=scores)
            state = A.initial_state()
            for generation in range(3):
                r = A.admit(obs, policy, state)
                actual.append(dict(n=n, scores=scores, generation=generation,
                    receipt_id=r['receipt_id'], state_sha256=r['next_state']['sha256']))
                state = r['next_state']
    assert actual == expected


@pytest.mark.parametrize('telemetry_cap', [2, 16])
def test_peer_and_telemetry_caps_never_trim_deep_or_holdings(
        tmp_path, cfg, monkeypatch, telemetry_cap):
    from tests.test_da05_history_coverage import feed_at
    k = full_cycle_kernel(tmp_path, cfg, monkeypatch)
    k.cfg = deepcopy(k.cfg)
    k.cfg['universe']['auto_scan']['top_n'] = 1
    k.cfg['attention']['max_symbols'] = telemetry_cap
    k._attention = Collector(tmp_path/'telemetry', k.cfg['attention'], start=False,
                             clock=lambda: CUT)
    f = feed_at(tmp_path/'synthetic-candles.db')
    f.ex.now = CUT
    frames = {}
    for symbol in k.exchange.markets:
        rows = f.ex.rows(symbol)
        rows[-1][-1] *= 100  # explicit synthetic anomaly, closed at the cut
        frames[symbol] = f._received_frame(rows, symbol, '4h')
    k.feed.cached_ohlcv.side_effect = lambda symbol, tf, **kw: frames.get(symbol)
    k._try_enter = Mock(side_effect=AssertionError('FROZEN entry forbidden'))
    stats = k.cycle()
    receipt = A.latest(Journal(k.journal.db_path))
    admitted = set(receipt['admitted_symbols'])
    held = set(k._exposure_symbols())
    assert stats['admission']['broad_observed'] == 30
    assert stats['admission']['peer_cohort'] == 1
    assert len(admitted) == stats['decisions'] == stats['deep_processed'] == 12
    assert len(admitted - set(receipt['roles']['peers'])) >= 11
    assert set(stats['portfolio_runtime']['candidate_symbols']) == admitted
    assert set(stats['portfolio_runtime']['held_symbols']) == held and len(held) == 15
    assert k._manage_one.call_count == 15
    assert {c.args[0].symbol for c in k.orchestrator.decide.call_args_list} == admitted
    scan = k._attention.queue.get_nowait()
    causes = k._attention.queue.get_nowait()
    assert scan['scope']['cap'] == telemetry_cap
    assert scan['scope']['included_count'] == min(telemetry_cap, 30)
    assert len(causes['items']) == min(telemetry_cap, 12)
    assert len(receipt['rows']) == 30  # full authoritative evidence survives
    assert A.canonical(receipt) == A.canonical(k._admission_receipt)
    k._try_enter.assert_not_called()
    f.db.close()


def test_retired_executors_are_absent():
    from trader.observability import attention
    for name in ('_scan_symbols', '_universe_frames', '_manage_orphan_positions', '_attention_supplemental_setup'):
        assert not hasattr(Kernel, name)
    for name in ('RegistryAttention', 'RegistryRefresher', 'supplemental_mode', 'registry_settings'):
        assert not hasattr(attention, name)
    assert not Path('trader/observability/supplemental.py').exists()
    assert not Path('trader/observability/_supplemental_child.py').exists()
    assert 'observability/_supplemental_child.py' not in attention.code_manifest()
    assert attention.MAX_SNAPSHOT_AGE_MS == 28_800_000  # active capability protection


def test_historical_supplemental_events_replay_exactly_after_restart(tmp_path):
    from trader.observability.attention import evaluate_snapshot
    fixtures = json.loads(Path('tests/fixtures/da08-historical-supplemental.json').read_text())
    for fixture in fixtures:
        frozen = A.canonical(fixture['event'])
        path = tmp_path / (fixture['event']['scan_id'].split(':')[-1] + '.json')
        path.write_text(frozen)
        restored = json.loads(path.read_text())
        assert evaluate_snapshot(restored) == fixture['result']
        assert A.canonical(restored) == frozen
        assert restored['scope']['supplemental']['provenance']['selection_id'] == 'baseline-selection'


def test_auxiliary_collection_requires_explicit_admission_targets():
    from types import SimpleNamespace
    from trader.core.types import MarketType
    k = Kernel.__new__(Kernel)
    k.market_type = MarketType.FUTURES
    k._funding_cache = None
    k._oi_cache = (0, {})
    k.exchange = SimpleNamespace(fetch_funding_rate=Mock(side_effect=AssertionError('unexpected collection')))
    k.universe = SimpleNamespace(symbols=Mock(side_effect=AssertionError('secondary selector')))
    with pytest.raises(TypeError):
        k._funding_map()
    with pytest.raises(TypeError):
        k._oi_map()
    assert k._funding_map([]) == {}
    assert k._oi_map(symbols=[]) == {}
    k.universe.symbols.assert_not_called()
    k.exchange.fetch_funding_rate.assert_not_called()
