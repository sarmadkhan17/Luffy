"""Offline normal producer -> retained source -> strategy/research queries."""
import json
import socket
import sqlite3
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from tests.test_attention_telemetry import frames
from tests.test_compile import _spec
from trader.core.types import Snapshot
from trader.observability import attention
from trader.observability.store import Store
from trader.research import evaluate, job, portfolio_null
from trader.strategy import dsl, spec_evidence
from trader.strategy.compile import compile_spec
from trader.strategy.features import FeatureCtx
from trader.world import Horizon, Quality, Scope, ScopeLevel, WorldHistory, WorldModelRecord
from trader.world.context import WorldContext, load_context

TF = 14_400_000
NOW = 2_000_000_000_000 // TF * TF
SYMBOL = 'S0/USDT'
EXPR = 'world_observation("volume_anomaly", "intraday")'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


@pytest.fixture
def retained(tmp_path):
    cfg = attention.settings({'world_model': True})
    path = tmp_path / 'attention.db'
    store = Store(path, cfg)
    data = None
    for i, cut in enumerate((NOW - 2*TF, NOW-TF, NOW)):
        data = frames(1, cut)
        data[SYMBOL]['4h'].loc[29, 'volume'] = 100000 if i == 2 else 100 + i
        event = attention.capture(data, [SYMBOL], 'cut-'+str(i), cfg, cut)
        store.write(dict(event, capture_settings=cfg,
                         identity=dict(schema='attention-scan-identity.v1', instance_id='1'*32, seq=i+1)))
    store.close()
    return path, data[SYMBOL]['4h']


def query(context, cut=NOW, symbol=SYMBOL, horizon=Horizon.INTRADAY):
    return context.observations(cut, Scope(ScopeLevel.INSTRUMENT, symbol), horizon,
                                kind='volume_anomaly')


def test_normal_storage_reopens_exact_typed_context(retained):
    path, _ = retained
    context = load_context(path)
    first = query(context, NOW-TF)
    latest = query(context)
    assert first.quality is latest.quality is Quality.VALID
    assert first.model_id != latest.model_id
    assert latest.observations[0].value > first.observations[0].value
    reopened = load_context(path)
    assert query(reopened, NOW-TF) == first
    frozen = WorldContext(WorldHistory.from_json(context.history.to_json()))
    assert query(frozen, NOW-TF) == first


@pytest.mark.parametrize('cut,symbol,horizon', [
    (NOW+1, SYMBOL, Horizon.INTRADAY), (NOW-3*TF, SYMBOL, Horizon.INTRADAY),
    (NOW, 'OTHER/USDT', Horizon.INTRADAY), (NOW, SYMBOL, Horizon.STRUCTURAL)])
def test_absent_exact_cut_scope_horizon_never_falls_back(retained, cut, symbol, horizon):
    context = load_context(retained[0])
    result = query(context, cut, symbol, horizon)
    assert result.quality is Quality.UNKNOWN and result.observations == ()


def test_frozen_historical_cut_rejects_future_context(retained):
    path, _ = retained
    old = load_context(path, as_of_ms=NOW-TF)
    assert query(old, NOW-TF).quality is Quality.VALID
    assert query(old).quality is Quality.UNKNOWN
    assert query(old).reason == 'QUERY_AFTER_CONTEXT_CUT'


def test_strategy_live_reader_and_research_bundle_share_exact_query(retained, monkeypatch):
    path, df = retained
    from trader.core import config
    monkeypatch.setattr(config, 'ROOT', path.parent.parent)
    # Default source location is ROOT/data/attention.db.
    source = path.parent / 'data'
    source.mkdir()
    target = source / 'attention.db'
    target.write_bytes(path.read_bytes())
    monkeypatch.setattr(config, 'ROOT', path.parent)
    compiled = compile_spec(_spec(timeframe='4h', entry_long=EXPR+' > 1', entry_short='', filters=[]))
    assert 'world' in compiled.data_requires
    snap = Snapshot(symbol=SYMBOL, ts=pd.Timestamp(NOW, unit='ms', tz='UTC').isoformat(),
                    price=float(df.close.iloc[-1]), dfs={'4h': df})
    signal = compiled.to_evaluator()(None, snap)
    assert signal is not None
    context = evaluate.load_world({'world': str(path)}, ('world',), NOW)
    bundle = evaluate.Bundle(tf='4h', frames={SYMBOL: df}, sym_frames={SYMBOL: {'4h': df}},
        universe={}, btc=None, market=None, derivs={}, risk={}, world=context)
    strategy_ctx = compiled._ctx({'4h': df}, None, None, symbol=SYMBOL)
    research_ctx = job._ctxs(bundle)[0]
    a, b = strategy_ctx.get('world_observation', ('volume_anomaly', 'intraday')), research_ctx.get(
        'world_observation', ('volume_anomaly', 'intraday'))
    np.testing.assert_allclose(a, b, equal_nan=True)
    assert a.attrs['world_queries'] == b.attrs['world_queries']
    assert a.attrs['world_queries'][-1]['model_id'] == query(context).model_id
    legs = portfolio_null.legs_for(compiled, bundle)
    assert len(legs) == 1 and legs[0].long[-1]


@pytest.mark.parametrize('expr', [EXPR+' > 0', EXPR+' != 0', 'not ('+EXPR+' > 0)'])
def test_missing_history_never_becomes_entry_through_complement(retained, expr):
    _, df = retained
    absent = WorldContext(WorldHistory(()), 'SOURCE_UNAVAILABLE')
    compiled = compile_spec(_spec(timeframe='4h', entry_long=expr, entry_short='', filters=[]))
    lo, sh = compiled.entries({'4h': df}, symbol=SYMBOL, world=absent)
    assert not lo.any() and not sh.any()
    ctx = FeatureCtx({'4h': df}, '4h', symbol=SYMBOL, world=absent)
    values = ctx.get('world_observation', ('volume_anomaly', 'intraday'))
    assert values.isna().all() and all(r['quality']=='UNKNOWN' for r in values.attrs['world_queries'])
    assert dsl.evaluate(dsl.parse(expr), ctx).isna().all()
    assert dsl.evaluate(dsl.parse('close'), ctx).notna().all()


def test_context_identity_prevents_memo_cache_rebinding(retained):
    path, df = retained
    present = load_context(path)
    cache = {}
    ctx = FeatureCtx({'4h': df}, '4h', symbol=SYMBOL, world=present, _cache=cache)
    assert np.isfinite(ctx.get('world_observation', ('volume_anomaly', 'intraday')).iloc[-1])
    other = FeatureCtx({'4h': df}, '4h', symbol=SYMBOL,
                       world=WorldContext(WorldHistory(())), _cache=cache)
    assert other.get('world_observation', ('volume_anomaly', 'intraday')).isna().all()
    assert ctx.temporal_identity() != other.temporal_identity()


@pytest.mark.parametrize('mutation', ['record', 'cut', 'identity'])
def test_corrupt_retained_source_becomes_unknown(retained, mutation):
    path, _ = retained
    with sqlite3.connect(path) as db:
        sid, text = db.execute('SELECT scan_id,payload FROM scans ORDER BY as_of_ms DESC LIMIT 1').fetchone()
        scan = json.loads(text)
        if mutation == 'record': scan['world_model']['record_json'] = '{}'
        if mutation == 'cut': scan['world_model']['as_of_ms'] += 1
        if mutation == 'identity': scan['world_model']['model_id'] = 'wrong'
        db.execute('UPDATE scans SET payload=? WHERE scan_id=?', (json.dumps(scan), sid))
    context = load_context(path)
    assert context.source_status == 'SOURCE_INVALID_OR_AMBIGUOUS'
    assert query(context).quality is Quality.UNKNOWN


def test_missing_source_is_read_only_and_not_created(tmp_path):
    path = tmp_path/'absent.db'
    assert query(load_context(path)).quality is Quality.UNKNOWN
    assert not path.exists()


def test_admission_reports_incomplete_historical_world_as_untested(retained):
    path, df = retained
    compiled = compile_spec(_spec(timeframe='4h', entry_long=EXPR+' > 0', entry_short='', filters=[]))
    gaps = spec_evidence.missing_data(compiled.spec, [SYMBOL], frames={SYMBOL: df}, world=load_context(path))
    assert 'historical context UNKNOWN' in str(gaps)


@pytest.mark.parametrize('expr', [
    'world_observation("volume_anomaly", "made-up") > 0',
    'htf("4h", '+EXPR+') > 0', 'xs_rank('+EXPR+') > 0'])
def test_unsupported_query_coordinates_and_wrappers_refused(expr):
    with pytest.raises(dsl.SpecError): dsl.parse(expr)


def test_normal_research_loader_binds_retained_reader(retained, monkeypatch):
    path, df = retained
    # Normal loader owns selection/cut; only the upstream cached candle source
    # is synthetic. No pre-populated Bundle/WorldModel input at the consumer.
    from trader.data.feed import DataFeed
    long = pd.concat([df.iloc[[0]]] * 600, ignore_index=True)
    long['ts'] = pd.to_datetime(NOW-np.arange(600, 0, -1)*TF, unit='ms', utc=True)
    long.attrs = {}
    monkeypatch.setattr(DataFeed, 'cached_ohlcv', lambda *a, **k: long.copy())
    cfg = {'risk': {}, 'research': {'predictive_split': {
        'start_ms': NOW-600*TF, 'end_ms': NOW, 'cut_ms': NOW+1}}}
    b = evaluate.load_bundle('4h', [SYMBOL], cfg, requires=('world',),
                             paths={'candles': str(path.parent/'candles.db'), 'world': str(path)})
    assert isinstance(b.world, WorldContext)
    ctx = job._ctxs(b)[0]
    values = ctx.get('world_observation', ('volume_anomaly', 'intraday'))
    assert values.iloc[-1] == query(b.world).observations[0].value
    assert values.iloc[:-3].isna().all()
    from trader.research.combo import Combination
    from trader.research.vocab import Part
    combo = Combination(parts=(Part('world-volume', 'gauge', 'world-volume', EXPR+' > 0',
                                    EXPR+' < 0'),), tf='4h', geo='trail')
    result = evaluate.evaluate(combo, b, draws=0)
    assert result['verdict']=='untested' and result['untested'] is True
    assert result['testable'] is False and 'UNKNOWN' in str(result['context_gaps'])
    payload = {'tf':'4h', 'symbols':[SYMBOL], 'cfg':cfg, 'requires':['world'],
               'paths':{'candles':str(path.parent/'candles.db'), 'world':str(path)},
               'exprs':[EXPR]}
    measured = job.measure_job(payload)
    assert measured['gauges'][EXPR]['usable'] is False


def test_future_scan_and_missing_retention_do_not_fill_old_cuts(retained):
    path, _ = retained
    old = load_context(path, as_of_ms=NOW)
    frozen = old.history.to_json()
    cfg = attention.settings({'world_model': True})
    later = NOW+TF
    event = attention.capture(frames(1, later), [SYMBOL], 'future', cfg, later)
    with_store = Store(path, cfg)
    with_store.write(dict(event, capture_settings=cfg,
        identity=dict(schema='attention-scan-identity.v1', instance_id='1'*32, seq=4)))
    with_store.close()
    assert old.history.to_json() == frozen
    assert query(load_context(path, as_of_ms=NOW)) == query(old)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM scans WHERE as_of_ms=?', (NOW-TF,))
    assert query(load_context(path), NOW-TF).quality is Quality.UNKNOWN
    assert query(old, NOW-TF).quality is Quality.VALID


def test_ambiguous_same_cut_retained_models_are_unknown(retained):
    path, _ = retained
    cfg = attention.settings({'world_model': True})
    data = frames(1, NOW)
    data[SYMBOL]['4h'].loc[29, 'volume'] = 999
    event = attention.capture(data, [SYMBOL], 'other-same-cut', cfg, NOW)
    store = Store(path, cfg)
    store.write(dict(event, capture_settings=cfg,
        identity=dict(schema='attention-scan-identity.v1', instance_id='1'*32, seq=4)))
    store.close()
    assert load_context(path).source_status == 'SOURCE_INVALID_OR_AMBIGUOUS'


@pytest.mark.parametrize('case', ['stale', 'conflict', 'nonnumeric'])
def test_record_quality_conflict_and_payload_are_never_scalar_filled(retained, case):
    model = load_context(retained[0]).history.get_exact(NOW)
    state = model.states[0]
    obs = state.get_one('volume_anomaly')
    if case == 'stale': obs = replace(obs, max_age_ms=0, timestamp_ms=NOW-1)
    if case == 'nonnumeric': obs = replace(obs, value='unverified narrative')
    items = tuple(o for o in state.observations if o.kind!='volume_anomaly') + (obs,)
    if case == 'conflict': items += (replace(obs, source_ref='opposing', value=-float(obs.value)),)
    state = replace(state, observations=items)
    changed = replace(model, states=(state,))
    context = WorldContext(WorldHistory((WorldModelRecord.from_model(changed),)))
    ctx = FeatureCtx({'4h': retained[1]}, '4h', symbol=SYMBOL, world=context)
    result = ctx.get('world_observation', ('volume_anomaly', 'intraday'))
    assert np.isnan(result.iloc[-1]) and result.attrs['world_queries'][-1]['quality']=='UNKNOWN'


def test_direct_live_model_cannot_masquerade_as_historical_input(retained):
    model = load_context(retained[0]).history.get_exact(NOW)
    with pytest.raises(TypeError, match='WorldContext'):
        FeatureCtx({'4h': retained[1]}, '4h', symbol=SYMBOL, world=model)


def test_current_anchor_uses_explicit_cut_but_replay_never_rebinds_it(retained):
    path, df = retained
    cfg = attention.settings({'world_model': True})
    cut = NOW+1
    event = attention.capture(frames(1, cut), [SYMBOL], 'current-cut', cfg, cut)
    store = Store(path, cfg)
    store.write(dict(event, capture_settings=cfg,
        identity=dict(schema='attention-scan-identity.v1', instance_id='1'*32, seq=4)))
    store.close()
    context = load_context(path, as_of_ms=cut)
    current = FeatureCtx({'4h': df}, '4h', as_of_ms=cut, symbol=SYMBOL, world=context)
    live = current.get('world_observation', ('volume_anomaly', 'intraday'))
    assert live.attrs['world_queries'][-1]['as_of_ms']==cut
    replay_df = df.copy()
    replay_df.attrs['read_mode']='replay'
    replay = FeatureCtx({'4h': replay_df}, '4h', as_of_ms=cut, symbol=SYMBOL, world=context)
    historic = replay.get('world_observation', ('volume_anomaly', 'intraday'))
    assert historic.attrs['world_queries'][-1]['as_of_ms']==NOW


def test_normal_research_evaluation_uses_same_compiled_entries(retained, monkeypatch):
    from trader.research.combo import Combination
    from trader.research.vocab import Part
    path, frame = retained
    df = frame.iloc[-3:].reset_index(drop=True)
    df.attrs = {}
    context = load_context(path)
    risk = {'risk_per_trade_pct':.5, 'real_funding':False}
    b = evaluate.Bundle(tf='4h', frames={SYMBOL:df}, sym_frames={SYMBOL:{'4h':df}},
        universe={}, btc=None, market=None, derivs={}, risk=risk, world=context)
    combo = Combination(parts=(Part('world-volume', 'gauge', 'world-volume', EXPR+' > 1',
                                    EXPR+' < -1'),), tf='4h', geo='trail')
    compiled = compile_spec(combo.to_spec())
    expected = compiled.entries({'4h':df}, symbol=SYMBOL, world=context)
    called = []
    original = evaluate.simulate
    def observed(lo, sh, *args, **kwargs):
        called.append((lo.copy(), sh.copy()))
        return original(lo, sh, *args, **kwargs)
    monkeypatch.setattr(evaluate, 'simulate', observed)
    result = evaluate.evaluate(combo, b, draws=0)
    assert 'context_gaps' not in result and len(called)==1
    np.testing.assert_array_equal(called[0][0], expected[0])
    np.testing.assert_array_equal(called[0][1], expected[1])
    assert expected[0][-1]
    # Three bars prove query routing; they cannot establish trade performance.
    assert result['trades']==0 and result['testable'] is False


def test_frozen_research_without_retained_world_never_uses_production_source(retained, monkeypatch):
    from trader.core import config
    path, _ = retained
    directory = path.parent/'data'
    directory.mkdir()
    (directory/'attention.db').write_bytes(path.read_bytes())
    monkeypatch.setattr(config, 'ROOT', path.parent)
    assert query(load_context()).quality is Quality.VALID
    frozen = evaluate.load_world({'candles': str(path.parent/'frozen-candles.db')}, ('world',), NOW)
    assert frozen.source_status == 'SOURCE_NOT_RETAINED_IN_BUNDLE'
    assert query(frozen).quality is Quality.UNKNOWN
