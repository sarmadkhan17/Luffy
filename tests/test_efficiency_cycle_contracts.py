"""Small synthetic cycle parity probes. No credentials, source stores or venue."""
from contextlib import nullcontext
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace
import json
import threading

import numpy as np
import pandas as pd
import pytest

from trader import attention_admission as A
from trader.core.types import Snapshot
from trader.data.feed import DataFeed
from trader.kernel import Kernel
from trader.learning.capture_runtime import copy_snapshot, runtime_inputs
from trader.scheduling import coordination
from trader.world import (WorldModel, WorldState, Observation, Quality,
                          Scope, ScopeLevel, HierarchyNode)
from trader.world.context import WorldContext
from trader.world.replay import WorldHistory


def receipt(n=3):
    symbols = [f'COIN{i}/USDT' for i in range(n)]
    observation = dict(schema='broad-crypto.v1', selection='volume', source_cut_ms=1000,
        rows={s: dict(symbol=s, asset_class='CRYPTO', quality='VALID', available_at_ms=1000,
                       quote_volume=n-i, missing_reasons=[]) for i,s in enumerate(symbols)},
        peer_cohort=symbols, strategy_relevance={s: [] for s in symbols},
        context_anchors=[], exposure_required=[], source_identity='fixture-only', status='VALID')
    return A.admit(observation, A.AdmissionPolicy(n,0,0,0), A.initial_state())


def world():
    root = Scope(ScopeLevel.GLOBAL, 'market')
    asset = Scope(ScopeLevel.ASSET_CLASS, 'crypto')
    coin = Scope(ScopeLevel.INSTRUMENT, 'BTC/USDT')
    observation = Observation('BTC/USDT', 0, 1000, '15m', 'nested',
        {'array': [1,2]}, 'fixture', 'original', Quality.VALID, available_at_ms=0,
        uncertainty={'nested': {'widths': [1,2]}})
    model = WorldModel(1000, (HierarchyNode(root), HierarchyNode(asset,root), HierarchyNode(coin,asset)),
                       (WorldState('BTC/USDT',1000,(observation,)),))
    return WorldContext(WorldHistory((model,)), 'RETAINED', 1000), observation


def snapshot():
    context, observation = world()
    frame = pd.DataFrame({'ts': pd.to_datetime([0,1],unit='s',utc=True),
        'close': [1.,2.], 'nullable': pd.array([1,None], dtype='Int64'),
        'objects': [{'list':[1]}, {'list':[2]}]})
    frame.attrs = {'nested': {'array': np.array([1,2])}, 'world': context,
                   'proxy': MappingProxyType({'items': [1,2]}), 'observation': observation}
    snap = Snapshot('BTC/USDT', '1970-01-01T00:00:01+00:00', 2., {'15m':frame}, world=context)
    snap.market_provenance_parts = {'source': ['exact', {'revision':1}]}
    snap.extra = {'frame':frame, 'array':np.array([{'list':[1]}], dtype=object)}
    return snap


def test_batch_exact_receipt_projection_and_owned_nested_freeze():
    raw = receipt()
    expected = A.candidates(raw)
    batch = A.VerifiedAdmissionBatch(raw)
    assert A.canonical(batch.receipt) == A.canonical(raw)
    assert list(batch.candidates_by_symbol.values()) == expected
    raw['observation']['rows']['COIN0/USDT']['quote_volume'] = 9999
    assert batch.receipt['observation']['rows']['COIN0/USDT']['quote_volume'] == 3
    with pytest.raises(TypeError):
        batch.receipt['observation']['rows']['COIN0/USDT']['quote_volume'] = 4
    with pytest.raises(TypeError):
        batch.receipt['admitted_symbols'].append('BAD/USDT')
    with pytest.raises(TypeError):
        batch.receipt['admitted_symbols'].__init__(['BAD/USDT'])
    assert deepcopy(batch.receipt) is batch.receipt


def test_corrupt_restored_receipt_cannot_be_blessed():
    raw = receipt()
    raw['admitted_symbols'] = ['FOREIGN/USDT']
    with pytest.raises(ValueError,match='corrupt'):
        A.VerifiedAdmissionBatch(raw)


def test_candidate_loop_does_not_replay_whole_batch(monkeypatch):
    raw = receipt(30)
    original = A.verify_receipt
    calls = []
    def counted(value):
        calls.append(1)
        return original(value)
    monkeypatch.setattr(A, 'verify_receipt', counted)
    batch = A.VerifiedAdmissionBatch(raw)
    k = Kernel.__new__(Kernel)
    k.cfg = {}
    k._verified_admission_batch = batch
    k._snapshot_for = lambda symbol, universe: SimpleNamespace(market_provenance_parts={})
    for symbol in batch.receipt['admitted_symbols']:
        result = k._candidate_snapshot_for(symbol, batch.receipt, {})
        assert result.admission_context['receipt_id'] == raw['receipt_id']
    assert len(calls) == 1
    foreign = json.loads(A.canonical(batch.receipt))
    k._candidate_snapshot_for(foreign['admitted_symbols'][0], foreign, {})
    assert len(calls) > 1
    with pytest.raises(ValueError,match='not_deep_admitted'):
        k._candidate_snapshot_for('FOREIGN/USDT',batch.receipt,{})


def test_lossless_world_and_both_snapshot_copy_sites():
    snap = snapshot()
    # Original mappingproxy-backed World fails ordinary deepcopy before patch.
    with pytest.raises(TypeError):
        deepcopy(snap)
    first = runtime_inputs(None, snap, {}, cut_ms=1000,
        additional_original_inputs={'world':snap.world})
    second = copy_snapshot(first['snapshot'])
    assert second.world.context_id == snap.world.context_id
    assert second.world.history.to_json() == snap.world.history.to_json()
    assert second.world is not snap.world
    assert second.market_provenance_json == snap.market_provenance_json
    # The assertion helper also extracts Series via __finalize__, so compare
    # attrs-independent views; attrs themselves are checked separately below.
    pd.testing.assert_frame_equal(pd.DataFrame(second.dfs['15m']._mgr.copy(deep=True)),
                                  pd.DataFrame(snap.dfs['15m']._mgr.copy(deep=True)))
    assert second.extra['frame'] is second.dfs['15m']
    assert second.dfs['15m'].attrs['world'].context_id == snap.world.context_id
    assert second.dfs['15m'].attrs['observation'].to_json() == snap.dfs['15m'].attrs['observation'].to_json()
    snap.dfs['15m'].iloc[0,1] = 99.
    snap.dfs['15m'].attrs['nested']['array'][0] = 99
    snap.dfs['15m'].attrs['proxy']['items'][0] = 99
    snap.dfs['15m']._mgr.iget_values(3)[0]['list'][0] = 99
    snap.extra['array'][0]['list'][0] = 99
    snap.market_provenance_parts['source'][1]['revision'] = 99
    assert second.dfs['15m']._mgr.iget_values(1)[0] == 1.
    assert second.dfs['15m'].attrs['nested']['array'][0] == 1
    assert second.dfs['15m'].attrs['proxy']['items'][0] == 1
    assert second.dfs['15m']._mgr.iget_values(3)[0]['list'] == [1]
    assert second.extra['array'][0]['list'] == [1]
    assert second.market_provenance_parts['source'][1]['revision'] == 1


def test_copy_preserves_duplicate_columns_and_empty_typed_frames():
    source = pd.DataFrame([[1,2]], columns=['same','same'])
    pd.testing.assert_frame_equal(copy_snapshot(source),source)
    empty = pd.DataFrame({'category':pd.Categorical([]), 'nullable':pd.array([],dtype='Int64')})
    pd.testing.assert_frame_equal(copy_snapshot(empty),empty)


def make_feed(monkeypatch):
    feed = DataFeed.__new__(DataFeed)
    feed._local = threading.local()
    feed._db_path = 'fixture-not-a-file'
    feed._identity = lambda symbol: ('venue:BTC','source')
    feed._series_key = lambda symbol, tf: f'{symbol}:{tf}'
    connection = object()
    monkeypatch.setattr(DataFeed,'db',property(lambda self:connection))
    state = {'revision':1, 'calls':[]}
    def load(conn,key,**kw):
        state['calls'].append((key,kw['as_of_ms']))
        value = state['revision'] if kw['as_of_ms'] < 2000 else 20
        return pd.DataFrame({'value':[value]})
    monkeypatch.setattr('trader.data.feed.mp.load',load)
    monkeypatch.setattr('trader.data.feed.mp.eligible_frame',lambda frame,*a,**kw:frame)
    return feed,state


def test_exact_cut_batch_detaches_and_never_leaks_after_exit(monkeypatch):
    feed,state = make_feed(monkeypatch)
    with feed.immutable_read_batch():
        first = feed.ohlcv_asof('BTC','15m',as_of_ms=1000)
        first.iloc[0,0] = 99
        state['revision'] = 2  # external writer between reads
        with feed.immutable_read_batch():
            assert feed.ohlcv_asof('BTC','15m',as_of_ms=1000).iloc[0,0] == 1
        assert feed.ohlcv_asof('BTC','15m',as_of_ms=1001).iloc[0,0] == 2
        assert feed.ohlcv_asof('BTC','15m',as_of_ms=2000).iloc[0,0] == 20
    assert len(state['calls']) == 3
    assert feed.ohlcv_asof('BTC','15m',as_of_ms=1000).iloc[0,0] == 2
    assert len(state['calls']) == 4


def test_batch_cleared_after_exception_and_bound_overflow(monkeypatch):
    feed,state = make_feed(monkeypatch)
    feed.MAX_CACHE_SERIES = 1
    with pytest.raises(RuntimeError):
        with feed.immutable_read_batch():
            feed.ohlcv_asof('BTC','15m',as_of_ms=1000)
            feed.ohlcv_asof('BTC','15m',as_of_ms=1001)
            assert len(feed._local.immutable_reads) == 1
            state['revision'] = 5
            assert feed.ohlcv_asof('BTC','15m',as_of_ms=1000).iloc[0,0] == 1
            raise RuntimeError('stop')
    assert not hasattr(feed._local,'immutable_reads')
    assert feed.ohlcv_asof('BTC','15m',as_of_ms=1000).iloc[0,0] == 5


def test_recursive_references_keep_full_reader_and_same_cut_reuse(monkeypatch):
    k = Kernel.__new__(Kernel)
    k._spec_requires = ('ref:spx',)
    k._owned_analysis_batch = True
    k._market_cut_cache = {}
    calls = []
    state = {'prefix':1}
    def refs(reqs,**kw):
        calls.append(kw)
        # Full recursive reference input; suffix equal but prefix changes.
        return {'spx':pd.DataFrame({'close':[state['prefix'],2.,3.]})}
    monkeypatch.setattr('trader.strategy.spec_evidence.load_refs',refs)
    original = k._market_for(as_of_ms=1000)
    original['spx'].iloc[0,0] = 999
    state['prefix'] = 9
    same = k._market_for(as_of_ms=1000)
    later = k._market_for(as_of_ms=1001)
    assert same['spx'].iloc[0,0] == 1
    assert later['spx'].iloc[0,0] == 9
    assert calls == [{'as_of_ms':1000},{'as_of_ms':1001}]
    assert same['spx']['close'].ewm(span=2,adjust=False).mean().iloc[-1] != later['spx']['close'].ewm(span=2,adjust=False).mean().iloc[-1]
    del k._owned_analysis_batch
    k._market_for(as_of_ms=1000)
    assert len(calls) == 3


def test_history_limits_are_captured_with_model_not_live_root(monkeypatch):
    import trader.agents.positioning
    import trader.agents.orderbook_depth
    import trader.engine.orchestrator
    root = SimpleNamespace(population=[],_history_limits={'4h':800},_history_limit=800,
        _spec_exits={}, exchange=object(), journal=object(),cfg={},exits=SimpleNamespace(),
        _make_agent=lambda key:None)
    frozen = coordination.model_snapshot(root)
    root._history_limits = {'4h':5000}
    root._history_limit = 5000
    monkeypatch.setattr(trader.agents.positioning,'PositioningAnalyst',lambda ex:None)
    monkeypatch.setattr(trader.agents.orderbook_depth,'DepthScout',lambda:None)
    monkeypatch.setattr(trader.engine.orchestrator,'Orchestrator',lambda *a,**kw:None)
    view = coordination.analysis_context(SimpleNamespace(kernel=root,model=(1,frozen)))
    assert view._history_limits == {'4h':800}
    assert view._history_limit == 800
    view._history_limits['4h'] = 900
    assert frozen['_history_limits']['4h'] == 800


def test_kernel_owned_audit_lifecycle_once_without_ipc(monkeypatch):
    import trader.owner.service
    import trader.owner.authz
    calls = []
    service = SimpleNamespace(authorizer=object(),
        start_audit_flusher=lambda:calls.append('start'),
        stop_audit_flusher=lambda timeout_s:(calls.append(('stop',timeout_s)) or {'remaining':0}))
    monkeypatch.setattr(trader.owner.service,'OwnerService',lambda *a,**kw:service)
    monkeypatch.setattr(trader.owner.authz.Authorizer,'from_config',lambda cfg:object())
    k = Kernel.__new__(Kernel)
    k.cfg = {'owner_interface':{'enabled':False}}
    k.journal = object()
    k.state_machine = object()
    k._bind_telegram_owner = lambda auth:None
    assert k._owner() is service
    assert k._owner() is service
    k._stop_owner_audit()
    k._stop_owner_audit()
    assert calls == ['start',('stop',5.0)]


def test_active_snapshot_reuses_prepared_btc_with_advancing_clock(monkeypatch):
    import trader.kernel as kernel_module
    calls = []
    state = {'revision':'first'}
    def frame():
        return pd.DataFrame({'ts':pd.to_datetime([0,1000],unit='ms',utc=True),
            'close':[1.,2.], 'instrument_id':['fixture','fixture'],
            'revision_id':[state['revision']]*2, 'quality':['VALID','VALID']})
    def fetch(symbol,tf):
        calls.append((symbol,tf))
        return frame()
    feed = SimpleNamespace(fetch_ohlcv=fetch,
        fetch_multi=lambda symbol,tfs: {tf:frame() for tf in tfs},
        fetch_diagnostics=lambda:{}, ttl={'15m':180,'1h':600,'4h':1800},
        _identity=lambda symbol:('fixture','fixture'))
    k = Kernel.__new__(Kernel)
    k.cfg = {'timeframes':{'execution':'15m','context':['1h','4h']}}
    k.feed = feed
    k.journal = object()
    k.universe = SimpleNamespace()
    k.market_type = SimpleNamespace(value='futures')
    k._owned_analysis_batch = True
    k._derivs_for = lambda *a,**kw:None
    k._market_for = lambda **kw:None
    monkeypatch.setattr('trader.engine.entry_authority.proposal_binding',lambda *a:None)
    monkeypatch.setattr(kernel_module, 'btc_context',lambda *a:{'trend':'fixture'})
    monkeypatch.setattr('trader.data.market_provenance.usable_current',lambda df,*a,**kw:df)
    validated = []
    def qualify(df,tf,cut,ttl,**kw):
        validated.append((tf,cut,df['revision_id'].iloc[-1]))
        return df,'valid'
    monkeypatch.setattr('trader.data.market_provenance.current_frame_status',qualify)
    clock = iter(range(100,200))
    monkeypatch.setattr(kernel_module.time,'time',lambda:next(clock))
    k._refresh_btc_context()
    cut = k._btc_ctx_receipt['as_of_ms']
    state['revision'] = 'external-new'
    snapshots = [k._snapshot_for(f'ALT{i}/USDT',{}) for i in range(3)]
    assert calls == [('BTC/USDT','15m'),('BTC/USDT','1h')]
    assert all(s.market_provenance_parts['frames']['BTC_1h'] == ['first']*2 for s in snapshots)
    assert all(s.market_provenance_parts['shared_btc_read_cut_ms'] == cut for s in snapshots)
    # The same pinned observation is still checked at each later consumer cut.
    assert len({at for tf,at,rid in validated if rid == 'first'}) == 3
    k._refresh_btc_context()
    later = k._snapshot_for('ALT0/USDT',{})
    assert len(calls) == 4
    assert later.market_provenance_parts['frames']['BTC_1h'] == ['external-new']*2


@pytest.mark.parametrize('state', ['RUNNING','UNKNOWN','FAILED','SUCCEEDED'])
def test_managed_research_completion_precedes_mechanism(monkeypatch,state):
    import trader.scheduling.kernel_runtime as module
    import trader.observability.managed as managed
    cfg = {'research':{'enabled':True,'closure':{'enabled':True,'slot_budget_s':12}},
           'mechanism':{'enabled':True}}
    calls = []
    view = SimpleNamespace(cfg=cfg,
        _mechanism_once=lambda *a:calls.append('mechanism'))
    runtime = module.KernelRuntime.__new__(module.KernelRuntime)
    runtime.kernel = SimpleNamespace(_runtime_revision='a'*40, _stop=False)
    runtime.reload_model = lambda:calls.append('reload')
    monkeypatch.setattr(module,'analysis_context',lambda self:view)
    def launch(*args,**kwargs):
        calls.append('launch')
        return {'unit':'fixture-research'}
    def wait(handle,root,revision,timeout_s,should_stop):
        calls.append('verified-wait')
        assert timeout_s == 42
        assert revision == 'a'*40
        return {'state':state,'reason':'fixture'}
    monkeypatch.setattr(managed,'launch_role',launch)
    monkeypatch.setattr(managed,'wait_role',wait)
    monkeypatch.setattr(managed,'release_role',lambda handle:(calls.append('release') or True))
    if state == 'SUCCEEDED':
        runtime.research()
        assert calls == ['launch','verified-wait','release','mechanism','reload']
    else:
        with pytest.raises(ValueError,match='not_complete'):
            runtime.research()
        assert 'mechanism' not in calls and 'reload' not in calls
        assert runtime._research_managed_status['mechanism_pending'] is True
        if state in ('RUNNING','UNKNOWN'):
            # Next scheduled controller continues the same independent unit.
            with pytest.raises(ValueError):
                runtime.research()
            assert calls.count('launch') == 1
