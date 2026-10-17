"""Offline exact-window sharing; admission and typed evidence stay independent."""
import copy
from dataclasses import replace
from unittest.mock import patch

import pytest

from trader.cognition import contracts
from trader.cognition.attention import CognitionConfig
from trader.observability.attention import evaluate
from trader.observability import attention as A, world_producer as W
from trader.world import Quality
from tests.test_attention_telemetry import frames, no_network  # noqa: F401

NOW = 100 * 14_400_000 + 60_000


def event():
    data = frames(6, NOW)
    data['S0/USDT']['4h'].loc[29, 'volume'] = 100_000
    return A.capture(data, list(data), 'wm01', A.settings({'world_model': True}), NOW)


def window(ds, sym='S0/USDT', size=21):
    anchor = NOW // ds.tf_ms * ds.tf_ms
    return [ds.bar_asof(sym, anchor - i * ds.tf_ms, NOW) for i in range(size, 0, -1)]


def test_shared_scan_computes_once_per_member_and_replays_byte_exactly():
    ev = event()
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        scan = W.evaluate(ev)
        assert calc.call_count == 6
    ds = contracts.load_input(ev['input'])
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        world = W.replay(scan, ds)
        result = evaluate(ds, NOW, CognitionConfig(max_symbols=6), ev['scan_id'], {}, world)
        assert calc.call_count == 6
        assert result['universe'] == scan['rows']
        assert result['market'] == scan['market']
        assert [contracts.to_dict(o) for o in result['observations']] == scan['observations']
    assert W.WorldModelRecord.from_model(world).to_json() == scan['world_model']['record_json']


@pytest.mark.parametrize('change', ['cut', 'symbol', 'timeframe', 'duration', 'window',
                                   'revision', 'source', 'bar_symbol', 'value', 'dataset'])
def test_cache_reuses_only_the_exact_local_candle_identity(change):
    ds = contracts.load_input(event()['input'])
    bars = window(ds)
    sym, cut = 'S0/USDT', NOW
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        value = contracts.volume_measurement(ds, sym, cut, bars)
        assert contracts.volume_measurement(ds, sym, cut, list(bars)) == value
        assert calc.call_count == 1
        if change == 'cut':
            cut += 1
        elif change == 'symbol':
            sym = 'S0/USDT:USDT'
        elif change == 'timeframe':
            ds.timeframe = '1h'
        elif change == 'duration':
            ds.tf_ms //= 4
        elif change == 'window':
            bars = bars[1:]
        elif change == 'revision':
            bars[-1] = replace(bars[-1], available_ms=NOW-1)
        elif change == 'source':
            bars[-1] = replace(bars[-1], source='different_venue')
        elif change == 'bar_symbol':
            bars[-1] = replace(bars[-1], symbol='other_contract')
        elif change == 'value':
            bars[-1] = replace(bars[-1], volume=bars[-1].volume * 2)
        elif change == 'dataset':
            ds = contracts.load_input(event()['input'])
        contracts.volume_measurement(ds, sym, cut, bars)
        assert calc.call_count == 2


def test_none_measurement_is_reused_without_fabricating_a_zero():
    ds = contracts.load_input(event()['input'])
    bars = [replace(b, volume=100.) for b in window(ds)]
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        assert contracts.volume_measurement(ds, 'S0/USDT', NOW, bars) is None
        assert contracts.volume_measurement(ds, 'S0/USDT', NOW, bars) is None
        assert calc.call_count == 1


def test_replay_cache_keeps_only_the_latest_window_per_instrument():
    raw = event()['input']
    ds = contracts.load_input(raw)
    bars = window(ds)
    for i in range(100):
        contracts.volume_measurement(ds, 'S0/USDT', NOW + i, bars)
    assert len(ds._volume_measurements) == 1
    assert ds._volume_measurements['S0/USDT'][0][0] == NOW + 99
    assert '_volume_measurements' not in repr(ds)
    assert ds == contracts.load_input(raw)


def test_world_volume_does_not_admit_an_asset_without_return_history():
    ev = event()
    first = min(b['open_ms'] for b in ev['input']['candles'])
    ev['input']['candles'] = [b for b in ev['input']['candles']
                              if not (b['symbol'] == 'S0/USDT' and b['open_ms'] == first)]
    ds = contracts.load_input(ev['input'])
    world = W.build(ds, NOW, ev['scan_id'])
    assert next(s for s in world.states if s.instrument == 'S0/USDT').get_one(W.OUTPUT_KIND).quality is Quality.VALID
    result = evaluate(ds, NOW, CognitionConfig(), ev['scan_id'], {}, world)
    row = result['rows']['S0/USDT']
    assert row['status'] == 'warmup' and not row['eligible']


def test_different_configured_window_does_not_reuse_world_default_window():
    ev = event()
    ds = contracts.load_input(ev['input'])
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        world = W.build(ds, NOW, ev['scan_id'])
        result = evaluate(ds, NOW, CognitionConfig(window=10), ev['scan_id'], {}, world)
        assert calc.call_count == 12
    legacy = evaluate(contracts.load_input(ev['input']), NOW,
                      CognitionConfig(window=10), ev['scan_id'], {})
    assert result['features'] == legacy['features']


def test_external_world_value_never_replaces_candle_measurement():
    ev = event()
    world, _ = W.produce(ev)
    state = next(s for s in world.states if s.instrument == 'S0/USDT')
    obs = state.get_one(W.OUTPUT_KIND)
    changed = replace(obs, value=obs.value + 100)
    altered = W.WorldModel(world.as_of_ms, world.nodes,
        [replace(s, observations=tuple(changed if o is obs else o for o in s.observations))
         if s.instrument == state.instrument else s for s in world.states])
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        result = A.evaluate_snapshot(ev, altered)
        assert calc.call_count == 6
    row = next(r for r in result['rows'] if r['symbol'] == 'S0/USDT')
    assert row['components']['world_volume_anomaly'] != row['components']['volume_anomaly']
    assert row['components']['volume_anomaly'] == contracts.rnd(obs.value)


def test_conflicting_candle_identity_is_still_refused_before_measurement():
    ev = event()
    conflict = copy.deepcopy(ev['input']['candles'][-1])
    conflict['source'] = 'other_venue'
    ev['input']['candles'].append(conflict)
    with patch.object(contracts, 'volume_z', wraps=contracts.volume_z) as calc:
        with pytest.raises(ValueError, match='conflicting candle revisions'):
            W.evaluate(ev)
        assert calc.call_count == 0


def test_producer_failure_does_not_leak_or_substitute_an_external_measurement():
    ev = event()
    with patch.object(W, 'build', side_effect=RuntimeError('unavailable')):
        failed = W.evaluate(ev)
    legacy = A.evaluate_snapshot(ev)
    assert failed['world_model']['reason'] == 'producer_failed:RuntimeError'
    assert failed['rows'] == legacy['rows']
