"""Controlled-clock safety proofs; no venue, kernel boot or production store."""
import inspect
import textwrap
import json

import numpy as np
import pandas as pd
import pytest

from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from trader.data.derivatives import DerivFeed
from trader.strategy.features import FeatureCtx
from trader.strategy.dsl import parse, evaluate_bool
from trader.strategy.backtest import resample

T = 1_780_000_200_000 // 3_600_000 * 3_600_000
H = 3_600_000
Q = 900_000


class Clock:
    def __init__(self, at): self.at = at
    def __call__(self): return self.at


class Venue:
    id = 'binanceusdm'
    urls = {'api': {'fapiPublic': 'https://offline.example/fapi/v1'}}
    def __init__(self, rows): self.rows, self.calls = rows, []
    def market(self, symbol):
        return {'id': 'BTCUSDT', 'contract': True}
    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        self.calls.append((symbol, tf, since, limit))
        return self.rows


def frame(at=T, n=1, step=Q, value=10):
    return pd.DataFrame(dict(ts=pd.to_datetime([at+i*step for i in range(n)], unit='ms', utc=True),
                             open=[value]*n, high=[value+1]*n, low=[value-1]*n,
                             close=[value]*n, volume=[5]*n))


def receipt(df, at, tf='15m', **kw):
    return P.annotate(df, instrument_id='binance_usdm:futures:BTCUSDT',
                      source='https://offline.example/fapi/v1/klines', kind='candle',
                      received_ms=at, timeframe=tf, **kw)


@pytest.fixture
def store(tmp_path):
    clock = Clock(T+Q+100)
    feed = DataFeed(exchange=Venue([[T,10,11,9,10,5]]), db_path=tmp_path/'candles.db',
                    clock_ms=clock)
    return feed, clock


def persist(feed, df, at, tf='15m'):
    iid, source = feed._identity('BTC/USDT')
    retained = P.annotate(df, instrument_id=iid, source=source, kind='candle',
                          received_ms=at, timeframe=tf)
    feed._store_save('BTC/USDT', tf, retained, now_ms=at)


def test_normal_feed_source_canonical_identity_and_raw(store):
    f,c = store
    df = f.fetch_ohlcv('BTC/USDT', min_bars=1)
    row=df.iloc[0]
    assert row.instrument_id == 'binance_usdm:futures:BTCUSDT'
    assert 'offline.example' in row.source
    assert json.loads(row.raw_json) == [T,10,11,9,10,5]
    assert row.event_time_ms == T and row.observed_at_ms == c.at
    assert row.available_at_ms == c.at and row.quality == 'VALID'
    assert len(row.content_hash) == len(row.revision_id) == 64


def test_receipt_availability_not_event_time(store):
    f,c=store
    persist(f,frame(), c.at)
    assert f.ohlcv_asof('BTC/USDT', as_of_ms=c.at-1) is None
    assert f.ohlcv_asof('BTC/USDT', as_of_ms=c.at).iloc[0].close == 10


@pytest.mark.parametrize('quality', ['UNKNOWN','MISSING','INVALID','STALE','INCOMPLETE'])
def test_unusable_quality_never_numeric_current(quality):
    df=receipt(frame(), T+Q)
    df['quality']=quality
    safe=P.eligible_frame(df,'15m',T+Q)
    assert safe['close'].isna().all()
    assert safe['quality'].iloc[0] == quality
    assert not evaluate_bool(parse('close == 0 or close > 1'), FeatureCtx({'15m':safe},'15m')).any()
    assert not evaluate_bool(parse('not (close > 1)'), FeatureCtx({'15m':safe},'15m')).any()
    assert not evaluate_bool(parse('close != 0'), FeatureCtx({'15m':safe},'15m')).any()


def test_unknown_identity_is_not_qualified(store):
    f,c=store
    f.ex.market=lambda _: (_ for _ in ()).throw(KeyError('unloaded'))
    out=f.fetch_ohlcv('BTC/USDT',min_bars=1)
    assert out['quality'].iloc[0]=='UNKNOWN' and out['close'].isna().all()


def test_stale_failure_does_not_restamp_current(store):
    f,c=store
    f.fetch_ohlcv('BTC/USDT',min_bars=1)
    c.at += H
    f.ex.fetch_ohlcv=lambda *a,**k: (_ for _ in ()).throw(OSError('offline'))
    out=f.fetch_ohlcv('BTC/USDT',force=True,min_bars=1)
    assert out['quality'].iloc[0]=='STALE' and out['close'].isna().all()
    assert out['observed_at_ms'].iloc[0]==T+Q+100


def test_revision_retention_pit_and_reopen(store,tmp_path):
    f,c=store
    persist(f,frame(),c.at)
    old=f.ohlcv_asof('BTC/USDT',as_of_ms=c.at).iloc[0]
    later=c.at+1000
    persist(f,frame(value=20),later)
    now=f.ohlcv_asof('BTC/USDT',as_of_ms=later).iloc[0]
    assert now.close==20 and now.supersedes==old.revision_id
    assert f.db.execute('SELECT COUNT(*) FROM market_revisions').fetchone()[0]==2
    reopened=DataFeed(exchange=f.ex,db_path=tmp_path/'candles.db',clock_ms=Clock(later))
    assert reopened.ohlcv_asof('BTC/USDT',as_of_ms=c.at).iloc[0].revision_id==old.revision_id
    assert reopened.ohlcv_asof('BTC/USDT',as_of_ms=c.at).iloc[0].close==10


def test_future_observation_and_cache_rejected(store):
    f,c=store
    out=f.fetch_ohlcv('BTC/USDT',min_bars=1)
    c.at -= 50
    # Historical calls never invoke REST or route through the memory cache.
    f.ex.fetch_ohlcv=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('network'))
    assert f.fetch_ohlcv('BTC/USDT',as_of_ms=c.at,min_bars=1) is None
    safe=P.eligible_frame(out,'15m',c.at)
    assert safe.empty
    f.latest_ohlcv=lambda *a,**kw: None
    f._store_load=lambda *a,**kw: None
    assert f.fetch_ohlcv('BTC/USDT',min_bars=1) is None


def test_htf_final_cannot_appear_early():
    base=frame(T+Q,n=1)
    higher=frame(T,step=H,value=100)
    ctx=FeatureCtx({'15m':base,'1h':higher},'15m')
    assert not evaluate_bool(parse("htf('1h', close > 50)"),ctx).any()
    base2=frame(T+H-Q)
    assert evaluate_bool(parse("htf('1h', close > 50)"),FeatureCtx({'15m':base2,'1h':higher},'15m')).all()


def test_htf_delayed_receipt_not_nominal_close():
    higher=receipt(frame(T,step=H,value=100),T+H+Q,'1h')
    base=frame(T+H-Q)
    ctx=FeatureCtx({'15m':base,'1h':higher},'15m')
    assert not evaluate_bool(parse("htf('1h', close > 50)"),ctx).any()


def test_partial_final_lineage_and_eligibility(store):
    f,c=store
    iid,source=f._identity('BTC/USDT')
    partial=P.annotate(frame(),instrument_id=iid,source=source,kind='candle',
                       received_ms=T+Q//2,timeframe='15m')
    assert partial['quality'].iloc[0]=='INCOMPLETE' and partial['bar_state'].iloc[0]=='PARTIAL'
    f._store_save('BTC/USDT','15m',partial,now_ms=T+Q//2)
    assert f.ohlcv_asof('BTC/USDT',as_of_ms=T+Q//2) is None
    p=f.ohlcv_asof('BTC/USDT',as_of_ms=T+Q//2,include_partial=True).iloc[0]
    persist(f,frame(value=12),T+Q)
    final=f.ohlcv_asof('BTC/USDT',as_of_ms=T+Q).iloc[0]
    assert final.bar_state=='FINAL' and final.supersedes==p.revision_id


def test_aggregate_constituent_availability_and_identity():
    df=receipt(frame(n=4),T+H+100)
    df.attrs['timeframe']='15m'
    agg=resample(df,'1h')
    assert agg['available_at_ms'].iloc[0]==T+H+100
    assert agg['quality'].iloc[0]=='VALID'
    assert json.loads(agg['raw_json'].iloc[0])['constituents']==df['revision_id'].tolist()
    assert P.eligible_frame(agg,'1h',T+H).empty
    partial=resample(df.iloc[:3],'1h')
    assert partial['quality'].iloc[0]=='INCOMPLETE' and partial['bar_state'].iloc[0]=='PARTIAL'


def test_latest_asof_and_replay_separate(store,monkeypatch):
    f,c=store
    persist(f,frame(),c.at)
    monkeypatch.setattr(f,'latest_ohlcv',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('latest')))
    assert f.ohlcv_asof('BTC/USDT',as_of_ms=c.at).iloc[0].close==10
    assert f.cached_ohlcv('BTC/USDT') is None  # receipt after nominal decision close
    assert f.cached_ohlcv('BTC/USDT',as_of_ms=c.at).iloc[0].close==10


def test_replay_original_revision_not_later_backfill(store):
    f,c=store
    persist(f,frame(),T+Q)
    original=f.ohlcv_asof('BTC/USDT',as_of_ms=T+Q).iloc[0].revision_id
    c.at=T+Q+200
    persist(f,frame(value=30),c.at)
    replay=f.replay_ohlcv('BTC/USDT')
    assert replay.iloc[0].close==10 and replay.iloc[0].revision_id==original


def test_feature_provenance_and_future_cache_binding():
    df=receipt(frame(n=2),T+2*Q)
    ctx=FeatureCtx({'15m':df},'15m')
    a=ctx.get('close')
    assert a.attrs['market_provenance']['as_of_ms']==T+2*Q
    assert a.attrs['market_provenance']['source_revisions']['15m']==df['revision_id'].tolist()
    older=FeatureCtx({'15m':df},'15m',_cache=ctx._cache,as_of_ms=T+2*Q-1)
    assert older.get('close').empty


def test_world_capture_rejects_future_and_preserves_lineage():
    from trader.observability.attention import capture,settings
    cfg=settings({'timeframe':'4h'})
    df=receipt(frame(at=T-40*4*H,n=40,step=4*H),T,'4h')
    future=df.copy();future['available_at_ms']=T+1
    body=capture({'BTC/USDT':{'4h':future}},['BTC/USDT'],'offline',cfg,as_of_ms=T)
    assert not body['input']['candles']
    body=capture({'BTC/USDT':{'4h':df}},['BTC/USDT'],'offline',cfg,as_of_ms=T)
    assert body['input']['candles']
    assert all('revision=' in x['source'] for x in body['input']['candles'])


def test_world_state_refuses_future_and_quality_survives():
    from trader.world.observation import Observation,Quality
    from trader.world.state import WorldState
    obs=Observation(instrument='binance_usdm:futures:BTCUSDT',timestamp_ms=T,
                    observed_at_ms=T+1,available_at_ms=T+1,timeframe='15m',kind='price',
                    value=10,source='offline',source_ref='revision:a',quality=Quality.VALID)
    with pytest.raises(ValueError,match='not yet'):
        WorldState(obs.instrument,T,(obs,))


def test_derivative_revisions_quality_and_pit(tmp_path):
    c=Clock(T+100)
    f=DerivFeed(db_path=tmp_path/'derivs.db',clock_ms=c)
    plain=pd.DataFrame({'ts':pd.to_datetime([T],unit='ms',utc=True),'value':[1.]})
    f.save('BTC/USDT','oi',plain)
    assert f.load('BTC/USDT','oi')['value'].isna().all()
    for at,value in [(T+200,2),(T+300,3)]:
        df=plain.copy();df['value']=value
        df=P.annotate(df,instrument_id='binance_usdm:futures:BTCUSDT',source='offline:oi',
                      kind='derivative',received_ms=at)
        f.save('BTC/USDT','oi',df)
    assert f.load('BTC/USDT','oi',as_of_ms=T+250).iloc[0].value==2
    assert f.load('BTC/USDT','oi',as_of_ms=T+300).iloc[0].value==3


def mutant(fn, old, new):
    source=textwrap.dedent(inspect.getsource(fn))
    assert old in source
    ns=dict(fn.__globals__)
    exec(source.replace(old,new),ns)
    return ns[fn.__name__]


def test_negative_control_event_clock_detected(store,monkeypatch):
    f,c=store;persist(f,frame(),c.at)
    broken=mutant(P.load,'AND available_ms<=? AND observed_ms<=?',
                         'AND event_ms<=? AND event_ms<=?')
    monkeypatch.setattr(P,'load',broken)
    with pytest.raises(AssertionError):
        result=P.load(f.db,f._series_key('BTC/USDT','15m'),as_of_ms=c.at-1)
        assert result is None or result.available_at_ms.le(c.at-1).all(), 'future receipt leaked from the as-of reader'


def test_negative_control_overwrite_detected(store,monkeypatch):
    broken=mutant(P.append,"        ids = tuple(",
                          "        for record in batch:\n            conn.execute('DELETE FROM market_revisions WHERE series_key=? AND event_ms=?', (key, record['event_time_ms']))\n        ids = tuple(")
    monkeypatch.setattr(P,'append',broken)
    f,c=store;persist(f,frame(),c.at);persist(f,frame(value=20),c.at+1)
    with pytest.raises(AssertionError):
        assert f.ohlcv_asof('BTC/USDT',as_of_ms=c.at) is not None, 'original revision erased'


def test_negative_control_future_cache_detected(store,monkeypatch):
    f,c=store;f.fetch_ohlcv('BTC/USDT',min_bars=1);c.at-=1
    # Strip the second safety check in the same defective cache branch.
    source=textwrap.dedent(inspect.getsource(DataFeed.fetch_ohlcv))
    source=source.replace('0 <= now_ms / 1000 - hit[0]', 'now_ms / 1000 - hit[0]').replace(
        'return mp.usable_current(hit[1], tf, now_ms, self.ttl.get(tf, 300))\n', 'return hit[1]\n',1)
    ns=dict(DataFeed.fetch_ohlcv.__globals__);exec(source,ns)
    monkeypatch.setattr(DataFeed,'fetch_ohlcv',ns['fetch_ohlcv'])
    with pytest.raises(AssertionError):
        result=f.fetch_ohlcv('BTC/USDT',min_bars=1)
        assert result is None or (result.available_at_ms.le(c.at) & result.observed_at_ms.le(c.at)).all(), 'future cache leaked'


def test_negative_control_early_htf_detected(monkeypatch):
    import trader.strategy.dsl as dsl
    broken=mutant(dsl._eval_htf,'else TF_MS[tf])','else 0)')
    monkeypatch.setattr(dsl,'_eval_htf',broken)
    with pytest.raises(AssertionError):
        test_htf_final_cannot_appear_early()


def test_kernel_snapshot_actual_boundary(store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    from trader.core.types import MarketType
    import trader.kernel as module
    f,c=store
    df=f.fetch_ohlcv('BTC/USDT',min_bars=1)
    f.fetch_multi=lambda *args: {'15m':df}
    f.fetch_ohlcv=lambda *args: None
    k=Kernel.__new__(Kernel)
    k.feed=f;k.journal=Journal(tmp_path/'journal.db')
    k.cfg={'timeframes':{'context':[],'execution':'15m'}}
    k.market_type=MarketType.FUTURES;k.universe=SimpleNamespace()
    k._derivs_for=lambda *args,**kw: None
    k._market_for=lambda **kw: None
    monkeypatch.setattr(module.time,'time',lambda: c.at/1000)
    snap=k._snapshot_for('BTC/USDT')
    assert snap.price==10
    lineage=json.loads(snap.market_provenance_json)
    assert lineage['as_of_ms']==c.at and lineage['frames']['15m']==df.revision_id.tolist()
    c.at-=1
    assert k._snapshot_for('BTC/USDT') is None
    c.at+=1
    df['quality']='INVALID'
    assert k._snapshot_for('BTC/USDT') is None


def test_opportunity_refuses_future_signal_source():
    from trader.cognition.opportunity_context import build,OpportunityContextRefused
    params={'spec_id':'s','spec_fingerprint':'f'*64,'signal_timeframe':'15m',
            'signal_bar_close_ms':T,'market_provenance':{
              'schema_version':P.SCHEMA,'as_of_ms':T,'sources':[
                {'available_at_ms':T+1,'observed_at_ms':T+1}]}}
    signal={'symbol':'BTC/USDT','action':'BUY','params':params}
    with pytest.raises(OpportunityContextRefused):
        build(as_of_ms=T,symbol='BTC/USDT',signals=[signal])
    params['market_provenance']['sources'][0].update(available_at_ms=T,observed_at_ms=T)
    assert build(as_of_ms=T,symbol='BTC/USDT',signals=[signal]).to_dict()['signal_occurrences']['items'][0]['status']=='AVAILABLE'


def test_auxiliary_receipt_durable_and_unknown_provider_clock(tmp_path):
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    k=Kernel.__new__(Kernel);k.journal=Journal(tmp_path/'journal.db')
    raw={'bids':[[10,2]],'asks':[[11,2]]}
    obs=k._retain_market_input('BTC/USDT',Venue([]),'order_book',raw,raw,
                              received_ms=T+10,attempt_ms=T)
    assert obs['event_time_ms'] is None and obs['event_time_basis']=='UNKNOWN'
    assert obs['available_at_ms']==T+10
    reopened=Journal(tmp_path/'journal.db')
    rows=reopened.query('SELECT record_json FROM market_revisions')
    assert json.loads(rows[0]['record_json'])['revision_id']==obs['revision_id']
    assert k._retain_market_input('BTC/USDT',Venue([]),'order_book',raw,raw,
                                 received_ms=T-1,attempt_ms=T) is None


def test_unqualified_snapshot_cannot_use_latest():
    from trader.core.types import Snapshot
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import StrategySpec
    # Reuse an actual registered seed spec, with an explicit invalid cut.
    from trader.strategy.seed_specs import load_seed_specs
    compiled=compile_spec(load_seed_specs()[0])
    snapshot=Snapshot(symbol='BTC/USDT',ts='',price=10,dfs={compiled.spec.timeframe:frame()})
    diagnostics=[]
    assert compiled.to_evaluator()(None,snapshot,diagnostic=diagnostics.append) is None
    assert diagnostics==['invalid_snapshot_cut']


def test_revision_content_tamper_rejected(store):
    f,c=store;persist(f,frame(),c.at)
    raw=f.db.execute('SELECT record_json FROM market_revisions').fetchone()[0]
    altered=json.loads(raw);altered['close']=999
    f.db.execute('UPDATE market_revisions SET record_json=?',(json.dumps(altered),));f.db.commit()
    with pytest.raises(ValueError,match='content_corrupt'):
        f.ohlcv_asof('BTC/USDT',as_of_ms=c.at)


def test_canonical_alias_change_does_not_reuse_old_cache(store):
    from trader.core.instrument_registry import InstrumentId
    from trader.core.types import MarketType
    f,c=store;f.fetch_ohlcv('BTC/USDT',min_bars=1)
    f.ex.market=lambda _: (_ for _ in ()).throw(KeyError('unloaded'))
    f.bind_instrument('BTC/USDT',InstrumentId('binanceusdm',MarketType.SPOT,'BTCUSDT'))
    assert f.ohlcv_asof('BTC/USDT',as_of_ms=c.at) is None


def test_listing_cache_future_receipt_rejected(monkeypatch):
    from trader.data.feed import Universe
    import trader.data.feed as module
    u=Universe.__new__(Universe);u.data_ex=Venue([]);u.min_age_days=10
    iid,source=P.venue_identity(u.ex,'BTC/USDT')
    u._listing_cache={'BTC/USDT':dict(instrument_id=iid,source=source,
                        available_at_ms=T+1,event_time_ms=T-100*24*H)}
    monkeypatch.setattr(module.time,'time',lambda:T/1000)
    u.data_ex.fetch_ohlcv=lambda *a,**k: []
    assert not u._old_enough('BTC/USDT')


def test_attention_unqualified_frame_is_explicitly_missing():
    from trader.observability.attention import capture,settings
    body=capture({'BTC/USDT':{'4h':frame(step=4*H)}},['BTC/USDT'],'unqualified',settings(),as_of_ms=T+4*H)
    assert not body['input']['candles']
    assert body['issues'][0]['reason']=='missing_timeframe'


def test_cross_section_rejects_delayed_peer_at_historical_cut():
    base=receipt(frame(n=2),T+2*Q)
    base.attrs['read_mode']='replay'
    peer=receipt(frame(n=2,value=20),T+2*Q+1)
    ctx=FeatureCtx({'15m':base},'15m',universe={'peer':{'15m':peer}},symbol='base')
    assert ctx.get('close').notna().all()
    values=evaluate_bool(parse('xs_rank(close) < 1'),ctx)
    assert not values.any()


def test_current_receipts_fenced_from_historical_engines():
    from trader.strategy.vector_backtest import _require_replay
    with pytest.raises(ValueError,match='retained bar-cut replay'):
        _require_replay({'15m':receipt(frame(),T+Q)})


def test_supplemental_raw_receipt_is_not_backdated():
    from trader.observability.supplemental import _parse
    raw=[T,'10','11','9','10','5',T+Q-1,'50',1,'2','20','0']
    msg=dict(symbol='BTCUSDT',interval='15m',limit=1,request_start_ms=T+Q,
             request_end_ms=T+Q+10,rows=[raw])
    df=_parse(json.dumps(msg).encode(),symbol='BTCUSDT',interval='15m',limit=1,
              started_ms=T+Q,ended_ms=T+Q+20)
    assert df.available_at_ms.iloc[0]==T+Q+20
    assert json.loads(df.raw_json.iloc[0])==raw
    assert P.eligible_frame(df,'15m',T+Q+19).empty


def test_request_clock_reversal_never_qualifies():
    df=P.annotate(frame(),instrument_id='binance_usdm:futures:BTCUSDT',source='offline',
                   kind='candle',timeframe='15m',received_ms=T+Q,request_started_ms=T+Q+1)
    assert df.quality.iloc[0]=='INVALID'
    assert P.eligible_frame(df,'15m',T+Q).empty


def test_funding_cost_reader_respects_revision_availability(tmp_path):
    from trader.strategy.spec_evidence import funding_series
    f=DerivFeed(tmp_path/'derivs.db',clock_ms=Clock(T+Q+100))
    obs=pd.DataFrame({'ts':pd.to_datetime([T],unit='ms',utc=True),'value':[.01]})
    obs=P.annotate(obs,instrument_id='binance_usdm:futures:BTCUSDT',source='offline:funding',
                   kind='derivative',received_ms=T+Q+100)
    f.save('BTC/USDT','funding',obs)
    base=frame(n=2);base.attrs['timeframe']='15m'
    values=funding_series('BTC/USDT',base,f)
    assert np.isnan(values[0]) and values[1]==.01


def test_reference_revisions_reopen_and_stale_quality(tmp_path):
    from trader.data.references import RefStore,REFS
    c=Clock(T+H)
    path=tmp_path/'refs.db';store=RefStore(path,clock_ms=c)
    for value,at in [(10,T+H),(20,T+H+100)]:
        df=frame(step=H,value=value)
        df.attrs.update(source='offline:yahoo:spx',raw_source_record={'close':value})
        store.save('spx_1h',df,now_ms=at)
    reopened=RefStore(path,clock_ms=c)
    old=reopened.load('spx_1h',as_of_ms=T+H).iloc[0]
    newer=reopened.load('spx_1h',as_of_ms=T+H+100).iloc[0]
    assert old.close==10 and newer.close==20 and newer.supersedes==old.revision_id
    stale=reopened.load('spx_1h',as_of_ms=T+H+REFS['spx_1h'].max_stale_ms+1)
    assert stale.quality.iloc[0]=='STALE' and stale.close.isna().all()


def test_kernel_historical_read_bypasses_future_memory_cache(monkeypatch):
    from trader.kernel import Kernel
    from trader.strategy import spec_evidence
    import trader.kernel as module
    k=Kernel.__new__(Kernel);k._spec_requires=['ohlcv','oi','ref:spx']
    k._derivs_cache={'BTC/USDT':(T/1000+1,{'future':True})}
    k._market_cache=(T/1000+1,{'future':True})
    calls=[]
    monkeypatch.setattr(module.time,'time',lambda:T/1000)
    monkeypatch.setattr(spec_evidence,'load_derivs',lambda *a,**kw:calls.append(('derivs',kw)) or {})
    monkeypatch.setattr(spec_evidence,'load_refs',lambda *a,**kw:calls.append(('refs',kw)) or {})
    assert k._derivs_for('BTC/USDT',as_of_ms=T)=={}
    assert k._market_for(as_of_ms=T) is None
    assert all(kw['as_of_ms']==T for _,kw in calls) and len(calls)==2


def test_unknown_flow_and_leader_never_become_neutral_market_values():
    from trader.agents.flow import FlowAnalyst
    from trader.agents.regime import btc_context,classify
    from trader.core.types import Snapshot
    df=frame(n=40);df['taker_buy']=np.nan
    snap=Snapshot(symbol='BTC/USDT',ts=pd.Timestamp(T,unit='ms',tz='UTC').isoformat(),price=10,dfs={'15m':df})
    vote=FlowAnalyst().evaluate(snap)
    assert vote.conviction==0 and vote.meta['quality']=='UNKNOWN'
    assert 'buy_ratio' not in vote.meta
    assert btc_context(None,None)=={'trend':'UNKNOWN','ret_1h':None,'ret_4h':None}
    assert classify(None,None)=={'regime':'UNKNOWN','adx':None,'vol_ratio':None}


def test_auxiliary_stale_and_future_book_fail_closed(tmp_path):
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    k=Kernel.__new__(Kernel);k.journal=Journal(tmp_path/'journal.db')
    for event in [T+1,T-45_001]:
        raw={'timestamp':event,'bids':[[10,1]],'asks':[[11,1]]}
        assert k._retain_market_input('BTC/USDT',Venue([]),'order_book',raw,raw,
                                      received_ms=T,attempt_ms=T) is None


def test_normal_derivative_adapter_preserves_raw_receipt(tmp_path,monkeypatch):
    import trader.data.derivatives as module
    from types import SimpleNamespace
    payload=[{'fundingTime':T,'fundingRate':'0.001'}]
    monkeypatch.setattr(module.requests,'get',lambda *a,**kw:SimpleNamespace(
        raise_for_status=lambda:None,json=lambda:payload))
    f=DerivFeed(tmp_path/'derivs.db',clock_ms=Clock(T+100))
    df=f.funding('BTC/USDT')
    assert df.source.iloc[0]=='https://fapi.binance.com/fapi/v1/fundingRate'
    assert df.instrument_id.iloc[0]=='binance_usdm:futures:BTCUSDT'
    assert json.loads(df.raw_json.iloc[0])['receipts'][0]['raw']==payload
    f.save('BTC/USDT','funding',df)
    assert f.load('BTC/USDT','funding',as_of_ms=T+99) is None
    assert f.load('BTC/USDT','funding',as_of_ms=T+100).value.iloc[0]==.001


@pytest.mark.parametrize('quality',['INVALID','UNKNOWN','MISSING','STALE'])
def test_aggregation_propagates_constituent_quality(quality):
    df=receipt(frame(n=4),T+H)
    df.attrs['timeframe']='15m'
    df.loc[0,'quality']=quality
    aggregate=resample(df,'1h')
    assert aggregate.quality.iloc[0]==quality
    assert P.eligible_frame(aggregate,'1h',T+H).close.isna().all()


def test_aggregation_cannot_mix_canonical_instruments():
    df=receipt(frame(n=4),T+H);df.attrs['timeframe']='15m'
    df.loc[0,'instrument_id']='binanceusdm:spot:BTCUSDT'
    assert resample(df,'1h').quality.iloc[0]=='INVALID'


def test_feed_uses_existing_typed_registry_identity_without_account_authority(store):
    from trader.core.instrument_registry import InstrumentId
    from trader.core.types import MarketType
    f,c=store
    f.ex.market=lambda _: (_ for _ in ()).throw(KeyError('unloaded'))
    f.bind_instrument('BTC/USDT',InstrumentId('binanceusdm',MarketType.FUTURES,'BTCUSDT'))
    obs=f.fetch_ohlcv('BTC/USDT',min_bars=1)
    assert obs.quality.iloc[0]=='VALID' and obs.instrument_id.iloc[0]=='binance_usdm:futures:BTCUSDT'


def test_current_closed_bar_survives_between_closes_with_fresh_receipt():
    at = T + 2*Q - 1000
    df = receipt(frame(), at)
    out = P.usable_current(df, '15m', at, 180)
    assert out['quality'].eq('VALID').all()
    assert out.iloc[0].close == 10
    assert P.usable_current(df, '15m', at+181_000, 180)['quality'].eq('STALE').all()


def test_fresh_receipt_cannot_make_missing_latest_closed_bar_current():
    at = T + 3*Q
    df = receipt(frame(), at)
    out = P.usable_current(df, '15m', at, 180)
    assert out['quality'].eq('STALE').all()
    assert out['close'].isna().all()


@pytest.mark.parametrize('historical', [False, True])
def test_universe_first_selection_uses_post_acquisition_cut_only_for_current(monkeypatch, historical):
    from trader.data.feed import Universe
    import trader.data.feed as module
    clock = Clock(T)
    monkeypatch.setattr(module.time, 'time', lambda: clock.at/1000)
    u = Universe.__new__(Universe)
    u.enabled, u.rescan_hours, u._last_scan = True, 4, 0
    u.majors, u._alts, u._selection_receipt = ['BTC/USDT'], [], None
    u._configured_membership = {'BTC/USDT': dict(source='config.universe.majors',
        symbol='BTC/USDT', quality='VALID', available_at_ms=T, observed_at_ms=T)}
    def rescan():
        clock.at += 1000
        u._last_scan = clock.at/1000
        u._alts = ['SOL/USDT']
        u._selection_receipt = dict(quality='VALID', available_at_ms=clock.at,
            observed_at_ms=clock.at, members=['SOL/USDT'])
    u._rescan = rescan
    result = u.symbols(as_of_ms=T) if historical else u.symbols()
    assert result == (['BTC/USDT'] if historical else ['BTC/USDT', 'SOL/USDT'])


def test_snapshot_frozen_acquisitions_reused_and_new_observations_retained(store,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    from trader.core.types import MarketType
    from trader.core import journal_evidence as E
    import trader.kernel as module
    f,c=store;df=f.fetch_ohlcv('BTC/USDT',min_bars=1)
    f.fetch_multi=lambda *args:{'15m':df};f.fetch_ohlcv=lambda *args:None
    k=Kernel.__new__(Kernel);k.feed=f;k.journal=Journal(tmp_path/'journal.db')
    k.cfg={'timeframes':{'context':[],'execution':'15m'}};k.market_type=MarketType.FUTURES
    r={'available_at_ms':c.at,'observed_at_ms':c.at,'content_hash':'same-value-hash','raw':{'price':10}}
    k.universe=SimpleNamespace(_volume_receipts={'BTC/USDT':r})
    k._derivs_for=lambda *args,**kw:None;k._market_for=lambda **kw:None
    monkeypatch.setattr(module.time,'time',lambda:c.at/1000)
    first=k._snapshot_for('BTC/USDT');a=first.market_provenance_parts['universe_selection_receipts']
    second=k._snapshot_for('BTC/USDT');assert second.market_provenance_parts['universe_selection_receipts'] is a
    c.at+=1;k.universe._volume_receipts['BTC/USDT']={**r,'available_at_ms':c.at,'observed_at_ms':c.at}
    third=k._snapshot_for('BTC/USDT');b=third.market_provenance_parts['universe_selection_receipts']
    assert b is not a
    assert json.loads(first.market_provenance_json)['universe_selection_receipts']['BTC/USDT']['observed_at_ms']==c.at-1
    assert json.loads(third.market_provenance_json)['universe_selection_receipts']['BTC/USDT']['observed_at_ms']==c.at
