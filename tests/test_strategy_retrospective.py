"""Offline consumers share DataFeed identities without claiming PIT evidence."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.data.feed import DataFeed
from trader.data.derivatives import DerivFeed
from trader.data.references import RefStore
from trader.data import market_provenance as mp
from trader.strategy import spec_evidence as se
from trader.strategy.compile import compile_spec
from trader.strategy.features import FeatureCtx
from trader.strategy.spec import StrategySpec, ExitSpec
from trader.strategy.vector_backtest import vector_walk_forward, funding_for
from trader.strategy.quant_contract import run_identity


@pytest.fixture
def dataset(tmp_path):
    candle = tmp_path / 'candles.db'
    deriv = tmp_path / 'derivs.db'
    ts = pd.date_range('2020-01-01', periods=650, freq='1h', tz='UTC')
    close = 100 + np.sin(np.arange(len(ts)) / 9) * 5 + np.arange(len(ts)) / 200
    rows = list(zip(mp.ms(pd.Series(ts)).tolist(), close, close+1, close-1, close, np.full(len(ts), 100.)))
    with sqlite3.connect(candle) as db:
        db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,tf,ts))')
        db.executemany("INSERT INTO candles VALUES ('BTC/USDT','1h',?,?,?,?,?,?)", rows)
    desc = dict(dataset_id='synthetic-history', cut_ms=int(ts[-1].timestamp()*1000)+3_600_000,
                paths={'candles': str(candle), 'derivs': str(deriv)})
    writer = DerivFeed(db_path=deriv)
    for series, val in [('funding', .0001), ('oi', 1000.)]:
        obs = pd.DataFrame({'ts': ts, 'value': np.full(len(ts), val)})
        obs = mp.annotate(obs, instrument_id='binanceusdm:futures:BTCUSDT',
            source='synthetic:declared-derivative', kind='derivative',
            received_ms=desc['cut_ms']+100_000, max_age_ms=3_600_000)
        writer.save('BTC/USDT', series, obs)
    writer.db.close()
    df = DataFeed(db_path=candle, retrospective=desc).cached_ohlcv('BTC/USDT','1h',limit=2000)
    return df, desc


def spec(entry='close > ema(20)'):
    return StrategySpec(id='offline', name='Offline', thesis='Synthetic offline test of a moving average strategy with explicit input identity.',
        invalidation='Retire below profit factor 1.0 over 30 out-of-sample trades.',
        provenance={'source_kind':'test'}, universe={'include':['BTC/USDT']}, timeframe='1h',
        direction='long', entry_long=entry, entry_short='', filters=[], exit=ExitSpec(time={"max_bars":12}),
        regime_filter=[], markets=['futures'])


def test_existing_compiler_vector_and_identity_reproduce(dataset):
    df, desc = dataset
    risk = dict(real_funding=True, bar_minutes=60, taker_fee_pct=.05, slippage_atr_frac=.06,
                risk_per_trade_pct=.5, funding_rate_8h=.0001)
    c = compile_spec(spec())
    result = vector_walk_forward(c, {'1h':df}, risk, symbol='BTC/USDT')
    repeated = vector_walk_forward(c, {'1h':df.copy()}, risk, symbol='BTC/USDT')
    assert result['train'] == repeated['train'] and result['test'] == repeated['test']
    assert result['train'].trades > 0 and result['test'].trades > 0
    a = run_identity(c.spec, {'BTC/USDT':df}, risk)
    assert a == run_identity(c.spec, {'BTC/USDT':df.copy()}, risk)
    assert a['retrospective_only'] and not a['factory_validated']
    other = df.copy(); other.attrs['dataset_id'] = 'another'
    assert a['identity'] != run_identity(c.spec, {'BTC/USDT':other}, risk)['identity']


def test_declared_dependency_observations_work_and_receipts_stay_late(dataset):
    df, desc = dataset
    derivs = se.load_derivs('BTC/USDT',['funding','open_interest'],frame=df)
    assert (derivs['funding'].observed_at_ms > desc['cut_ms']).all()
    ctx = FeatureCtx({'1h':df},'1h',derivs=derivs,symbol='BTC/USDT')
    assert np.isfinite(ctx.get('funding')).all()
    assert np.isfinite(ctx.get('oi')).all()
    assert np.isfinite(funding_for('BTC/USDT',df,{'real_funding':True})).all()
    for item in derivs.values():
        assert item.attrs['dataset_id'] == desc['dataset_id']


def test_missing_binding_unknown_source_and_mixed_dataset_refuse(dataset):
    df, desc = dataset
    unbound=df.copy();unbound.attrs['retrospective']={k:v for k,v in desc.items() if k!='paths'}
    with pytest.raises(ValueError,match='dataset path: derivs'):
        funding_for('BTC/USDT',unbound,{'real_funding':True})
    with pytest.raises(ValueError,match='missing trustworthy retained source'):
        se.load_derivs('BTC/USDT',['basis'],frame=df)
    dep=se.load_derivs('BTC/USDT',['funding'],frame=df)['funding']
    dep.attrs['retrospective']={**desc,'dataset_id':'wrong'}
    with pytest.raises(ValueError,match='dataset mismatch'):
        FeatureCtx({'1h':df},'1h',derivs={'funding':dep})
    writer=DerivFeed(db_path=desc['paths']['derivs'])
    writer.save('ETH/USDT','funding',pd.DataFrame({'ts':[df.ts.iloc[0]],'value':[.001]}))
    writer.db.close()
    with pytest.raises(ValueError,match='missing trustworthy retained source'):
        se.load_derivs('ETH/USDT',['funding'],frame=df)


def test_dependency_stores_are_readonly_and_provider_free(dataset):
    df, desc=dataset
    feed=DerivFeed(db_path=desc['paths']['derivs'],retrospective=desc)
    with pytest.raises(ValueError,match='read-only'):
        feed.save('X','funding',pd.DataFrame())
    with pytest.raises(ValueError,match='provider calls'):
        feed.funding('BTC/USDT')
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        feed.db.execute('DELETE FROM derivs')
    refs=RefStore(db_path=desc['paths']['candles'],retrospective=desc)
    with pytest.raises(ValueError,match='read-only'):
        refs.save('btcdom',pd.DataFrame())
    with pytest.raises(ValueError,match='world dependency'):
        compile_spec(spec('world_observation("volume_anomaly", "intraday") > 0')).entries({'1h':df})


def test_reference_observation_and_rolling_portfolio_costs(dataset):
    df, desc = dataset
    writer = RefStore(db_path=desc['paths']['candles'])
    refs = df.iloc[::4][['ts','open','high','low','close','volume']].copy()
    refs = mp.annotate(refs, instrument_id='ref:btcdom', source='synthetic:reference',
        kind='reference', timeframe='4h', received_ms=desc['cut_ms']+100_000)
    writer.save('btcdom',refs,now_ms=desc['cut_ms']+100_000)
    writer.db.close()
    market = se.load_refs(['ref:btcdom'],frame=df)
    values=FeatureCtx({'1h':df},'1h',market=market).get('close')
    assert np.isfinite(values).all()
    compiled=compile_spec(spec('close > ema(20) and ref("btcdom", close) > 0'))
    lo, sh=compiled.entries({'1h':df},market=market)
    assert lo.any()
    stale = market['btcdom'].iloc[:1].copy()
    with pytest.raises(ValueError,match='missing declared dependency: ref:btcdom'):
        compiled.entries({'1h':df}, market={'btcdom':stale})
    gap = se.missing_data(compiled.spec,['BTC/USDT'],frames={'BTC/USDT':df})
    assert not gap
    from trader.strategy.rolling import rolling_windows
    risk={'real_funding':True,'bar_minutes':60,'risk_per_trade_pct':.5}
    diagnostics=rolling_windows(compiled,{'BTC/USDT':df,'_market':market},risk,'1h',window_days=15,step_days=3,min_trades=1)
    assert diagnostics.n_windows > 0
    from trader.strategy.backtest import resample
    aggregate=resample(df,'4h')
    assert (mp.ms(aggregate.ts)+14_400_000 <= desc['cut_ms']).all()
    assert aggregate.attrs['dataset_id']==desc['dataset_id']

    from trader.strategy.portfolio_evidence import portfolio_curve
    from trader.strategy.vector_backtest import simulate
    fills=[]
    measured_funding=funding_for('BTC/USDT',df,risk)
    result=simulate(lo,sh,df,compiled.spec.exit,risk,symbol='BTC/USDT',funding=measured_funding,fills_out=fills)
    assert result.trades == len(fills) > 0
    identity=run_identity(compiled.spec,{'BTC/USDT':df,'_market':market},risk)
    curve=portfolio_curve(fills,2000.,.5,2,evaluation_identity=identity)
    assert curve == portfolio_curve(fills,2000.,.5,2,evaluation_identity=identity)
    assert diagnostics.evidence['retrospective_only']
    assert curve['taken']==len(fills)
    assert curve['accounting']['limitations']


def test_grid_result_output_is_bound_and_cannot_replace_evidence(dataset,tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts import analyst_grid
    from trader.strategy.backtest import BacktestResult
    df, desc=dataset
    target=tmp_path/'isolated-grid.json'
    descriptor={**desc,'output_path':str(target)}
    monkeypatch.setattr(analyst_grid,'GRID',{'ema_trend':[{'adx_min':22,'pullback_atr':.9}]})
    monkeypatch.setattr(analyst_grid,'ROOT',tmp_path)
    monkeypatch.setattr(analyst_grid,'load_config',lambda:{'risk':{'real_funding':False}})
    monkeypatch.setattr(se,'offline_feed',lambda *_:SimpleNamespace(retrospective=descriptor))
    monkeypatch.setattr(analyst_grid.evidence,'load_frames',lambda *_:{'BTC/USDT':df,'_btc_1h':df})
    monkeypatch.setattr(analyst_grid,'walk_forward',lambda g,*_,**kw:{'test':BacktestResult(g.strategy_id,'BTC/USDT',len(df),trades=25,gross_win=2,gross_loss=1)})
    analyst_grid.main()
    raw=target.read_bytes()
    row=json.loads(raw)[0]
    assert row['factory_validated'] is False
    assert len(row['config_sha256']) == len(row['program_sha256']) == 64
    assert row['evaluation_identity']['retrospective_only']
    with pytest.raises(FileExistsError):
        analyst_grid.main()
    assert target.read_bytes()==raw
    assert not (tmp_path/'data'/'analyst_candidates.json').exists()


@pytest.mark.parametrize('expression,missing',[('funding > 0','funding'),('oi > 0','oi'),('ref("spx_1h", close) > 0','ref:spx_1h')])
def test_direct_compiler_and_vector_refuse_omitted_dependency(dataset,expression,missing):
    from trader.strategy.vector_backtest import vector_backtest
    df, _=dataset
    compiled=compile_spec(spec(expression))
    for evaluate in (
        lambda:compiled.entries({'1h':df}),
        lambda:vector_walk_forward(compiled,{'1h':df},{'real_funding':False},symbol='BTC/USDT'),
        lambda:vector_backtest(compiled,{'1h':df},{'real_funding':False},symbol='BTC/USDT')):
        with pytest.raises(ValueError,match='missing declared dependency: '+missing):
            evaluate()


def test_retrospective_dependency_mixed_instrument_histories_refuse(dataset):
    df, desc=dataset
    writer=DerivFeed(db_path=desc['paths']['derivs'])
    other=mp.annotate(pd.DataFrame({'ts':[df.ts.iloc[0]],'value':[.001]}),
        instrument_id='another-venue:futures:BTCUSDT',source='synthetic:alternate-instrument',
        kind='derivative',received_ms=desc['cut_ms']+200_000)
    writer.save('BTC/USDT','funding',other)
    writer.db.close()
    with pytest.raises(ValueError,match='ambiguous instrument identity: derivative:BTC/USDT:funding'):
        se.load_derivs('BTC/USDT',['funding'],frame=df)
    store=RefStore(db_path=desc['paths']['candles'])
    for i,identity in enumerate(('ref:spx_1h','ref:other-spx_1h')):
        source=df.iloc[i:i+1][['ts','open','high','low','close','volume']].copy()
        source=mp.annotate(source,instrument_id=identity,source='synthetic:reference',
            kind='reference',timeframe='1h',received_ms=desc['cut_ms']+300_000+i)
        store.save('spx_1h',source,now_ms=desc['cut_ms']+300_000+i)
    store.db.close()
    with pytest.raises(ValueError,match='ambiguous instrument identity: reference:spx_1h'):
        se.load_refs(['ref:spx_1h'],frame=df)


def test_direct_vector_binds_funding_and_supplied_context(dataset):
    from trader.strategy.vector_backtest import vector_backtest
    df, desc=dataset
    compiled=compile_spec(spec())
    result=vector_backtest(compiled,{'1h':df},{'real_funding':True,'bar_minutes':60,'risk_per_trade_pct':.5},symbol='BTC/USDT',universe={'BTC/USDT':{'1h':df}})
    assert result.trades > 0
    inferred=vector_backtest(compiled,{'1h':df},{'real_funding':True,'bar_minutes':60,'risk_per_trade_pct':.5})
    assert inferred.symbol == 'BTC/USDT'
    with pytest.raises(ValueError,match='symbol binding mismatch'):
        vector_backtest(compiled,{'1h':df},{'real_funding':False},symbol='ETH/USDT')
    assert result.evidence['factory_validated'] is False
    assert result.evidence['quant_contract']['run']['retrospective_only']
    unbound=df.copy(); unbound.attrs['retrospective']={k:v for k,v in desc.items() if k!='paths'}
    with pytest.raises(ValueError,match='dataset path: derivs'):
        vector_backtest(compiled,{'1h':unbound},{'real_funding':True},symbol='BTC/USDT')
    supplied=se.load_derivs('BTC/USDT',['funding'],frame=df)
    carried=vector_backtest(compile_spec(spec('funding > 0')),{'1h':df},{'real_funding':True,'bar_minutes':60,'risk_per_trade_pct':.5},symbol='BTC/USDT',derivs=supplied)
    assert carried.trades > 0


@pytest.mark.parametrize('expression,missing',[('btc_ret(1) > 0','btc:1h'),('xs_rank(close) > 0','universe:1h'),('htf("4h", close) > 0','htf:4h')])
def test_retrospective_context_dependencies_refuse_absent_sources(dataset,expression,missing):
    df,_=dataset
    with pytest.raises(ValueError,match='missing declared dependency: '+missing):
        compile_spec(spec(expression)).entries({'1h':df})


def test_selected_btc_xs_and_htf_contexts_work_at_source_closes(dataset):
    from trader.strategy.backtest import resample
    from trader.strategy.features import _btc_aligned
    df,_=dataset
    higher=resample(df,'4h')
    higher.attrs['symbol']='BTC/USDT'
    ctx=FeatureCtx({'1h':df},'1h',btc={'1h':higher})
    aligned=_btc_aligned(ctx)
    assert aligned.iloc[:3].isna().all()
    assert aligned.iloc[3] == higher.close.iloc[0]
    peer=df.copy();peer.attrs['symbol']='ETH/USDT'
    universe={'BTC/USDT':{'1h':df},'ETH/USDT':{'1h':peer}}
    compiled=compile_spec(spec('btc_ret(1) > 0 or xs_rank(close) >= 0 or htf("4h", close) > 0'))
    long,_=compiled.entries({'1h':df,'4h':higher},btc={'1h':df},universe=universe)
    assert long.any()
    with pytest.raises(ValueError,match='symbol binding mismatch'):
        compiled.entries({'1h':df,'4h':higher},btc={'1h':df},universe=universe,symbol='ETH/USDT')


def test_direct_retrospective_sources_refuse_insufficient_coverage(dataset):
    df,_=dataset
    fund=se.load_derivs('BTC/USDT',['funding'],frame=df)['funding'].iloc[-5:].copy()
    with pytest.raises(ValueError,match='insufficient trustworthy historical coverage'):
        compile_spec(spec('funding > 0')).entries({'1h':df},derivs={'funding':fund})
    btc=df.iloc[-5:].copy()
    with pytest.raises(ValueError,match='insufficient trustworthy historical coverage'):
        compile_spec(spec('btc_ret(1) > 0')).entries({'1h':df},btc={'1h':btc})
    from trader.strategy.backtest import resample
    higher=resample(df,'4h').iloc[-5:].copy()
    with pytest.raises(ValueError,match='insufficient trustworthy historical coverage'):
        compile_spec(spec('htf("4h", close) > 0')).entries({'1h':df,'4h':higher})
    peer=btc.copy();peer.attrs['symbol']='ETH/USDT'
    universe={'BTC/USDT':{'1h':df},'ETH/USDT':{'1h':peer}}
    with pytest.raises(ValueError,match='insufficient trustworthy historical coverage'):
        compile_spec(spec('xs_rank(close) > 0')).entries({'1h':df},universe=universe)


@pytest.mark.parametrize('expression',['taker_buy > 0','taker_buy_frac > 0.5'])
def test_retrospective_raw_aggressor_dependency_refuses_unknown(dataset,expression):
    df,_=dataset
    with pytest.raises(ValueError,match='taker_buy: insufficient trustworthy retained aggressor observations'):
        compile_spec(spec(expression)).entries({'1h':df})


def test_retrospective_funding_rereads_declared_source_corrections(dataset):
    df,desc=dataset
    first=funding_for('BTC/USDT',df,{'real_funding':True})
    writer=DerivFeed(db_path=desc['paths']['derivs'])
    correction=mp.annotate(pd.DataFrame({'ts':[df.ts.iloc[-1]],'value':[.0003]}),
        instrument_id='binanceusdm:futures:BTCUSDT',source='synthetic:declared-derivative',
        kind='derivative',received_ms=desc['cut_ms']+400_000,max_age_ms=3_600_000)
    writer.save('BTC/USDT','funding',correction)
    second=funding_for('BTC/USDT',df,{'real_funding':True})
    assert first[-1] == .0001 and second[-1] == .0003
    correction=mp.annotate(pd.DataFrame({'ts':[df.ts.iloc[-1]],'value':[.0004]}),
        instrument_id='another-venue:futures:BTCUSDT',source='synthetic:alternate-instrument',
        kind='derivative',received_ms=desc['cut_ms']+500_000,max_age_ms=3_600_000)
    writer.save('BTC/USDT','funding',correction)
    writer.db.close()
    with pytest.raises(ValueError,match='ambiguous instrument identity'):
        funding_for('BTC/USDT',df,{'real_funding':True})


def test_confirmed_aggressor_values_aggregate_and_unknown_stays_missing(dataset):
    from trader.strategy.backtest import resample
    df,desc=dataset
    source=df[['ts','open','high','low','close','volume']].copy()
    source['taker_buy']=50.
    source=mp.annotate(source,instrument_id='binanceusdm:futures:BTCUSDT',
        source='synthetic:retained-candle-flow',kind='candle',timeframe='1h',
        received_ms=desc['cut_ms']+600_000)
    with sqlite3.connect(desc['paths']['candles']) as db:
        db.execute('ALTER TABLE candles ADD COLUMN taker_buy REAL')
        db.execute('UPDATE candles SET taker_buy=50')
        mp.init(db)
        mp.append(db,DataFeed._series_key('BTC/USDT','1h'),source)
    feed=DataFeed(db_path=desc['paths']['candles'],retrospective=desc)
    confirmed=feed.cached_ohlcv('BTC/USDT','1h',limit=2000)
    assert confirmed.taker_buy.eq(50.).all()
    aggregate=resample(confirmed,'4h')
    assert aggregate.taker_buy.eq(200.).all()
    compiled=compile_spec(spec('taker_buy_frac >= 0.5'))
    long,_=compiled.entries({'1h':confirmed})
    assert long.all()
    with sqlite3.connect(desc['paths']['candles']) as db:
        db.execute('UPDATE candles SET taker_buy=NULL WHERE ts=?',(int(mp.ms(df.ts)[0]),))
    unavailable=feed.cached_ohlcv('BTC/USDT','1h',limit=2000)
    assert np.isnan(unavailable.taker_buy.iloc[0])
    assert np.isnan(resample(unavailable,'4h').taker_buy.iloc[0])


def test_xs_derivative_peers_are_bound_separately_and_corrections_change_identity(dataset):
    import copy
    df,desc=dataset
    peer=df.copy();peer.attrs['symbol']='ETH/USDT'
    universe={'BTC/USDT':{'1h':df},'ETH/USDT':{'1h':peer}}
    base_derivs=se.load_derivs('BTC/USDT',['funding'],frame=df)
    compiled=compile_spec(spec('xs_rank(funding) > 0.5'))
    with pytest.raises(ValueError,match='missing trustworthy retained source: derivative:ETH/USDT:funding'):
        compiled.entries({'1h':df},derivs=base_derivs,universe=universe)
    writer=DerivFeed(db_path=desc['paths']['derivs'])
    source=mp.annotate(pd.DataFrame({'ts':df.ts,'value':np.full(len(df),.0002)}),
        instrument_id='binanceusdm:futures:ETHUSDT',source='synthetic:declared-derivative',
        kind='derivative',received_ms=desc['cut_ms']+700_000,max_age_ms=3_600_000)
    writer.save('ETH/USDT','funding',source)
    initial,_=compiled.entries({'1h':df},derivs=base_derivs,universe=universe)
    assert not initial.any()
    first=run_identity(compiled.spec,{'BTC/USDT':df,'universe':universe}, {'real_funding':False})
    saved=copy.deepcopy(first)
    correction=mp.annotate(pd.DataFrame({'ts':df.ts,'value':np.full(len(df),.00005)}),
        instrument_id='binanceusdm:futures:ETHUSDT',source='synthetic:declared-derivative',
        kind='derivative',received_ms=desc['cut_ms']+800_000,max_age_ms=3_600_000)
    writer.save('ETH/USDT','funding',correction)
    writer.db.close()
    corrected,_=compiled.entries({'1h':df},derivs=base_derivs,universe=universe)
    second=run_identity(compiled.spec,{'BTC/USDT':df,'universe':universe}, {'real_funding':False})
    assert corrected.all()
    assert first['identity'] != second['identity']
    assert first == saved
    assert second['datasets']['universe:ETH/USDT:1h']['retrospective_dependency_inputs']['funding']
