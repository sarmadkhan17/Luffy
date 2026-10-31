"""Replay actual compiler contexts without fetching history or current context."""
from copy import deepcopy
import json
import numpy as np
import pandas as pd
import pytest
from tests.test_compile import _spec
from tests.test_dsl_ref import _bars,T0,HOUR
from tests.test_wrld05_normal_context import retained,NOW,SYMBOL,offline  # noqa: F401
from trader.engine import exit_signal_context as K
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec


@pytest.mark.parametrize('expression',['funding > .005','ref("btcdom", close) > 900','htf("1h", close) > 0'])
def test_exit_context_replays_derivatives_references_and_higher_timeframe(expression):
    base=_bars(T0,80,HOUR//4)
    higher=_bars(T0,20,HOUR)
    derivative=pd.DataFrame({'ts':base['ts'].iloc[::10].reset_index(drop=True),'value':.01})
    market=_bars(T0,80,HOUR//4,close0=1000.)
    spec=_spec(filters=[],entry_long='close > 0',entry_short='',exit=ExitSpec(signal_exit=expression))
    compiled=compile_spec(spec);contexts=[]
    before,available=compiled.exit_signal_detail({'15m':base,'1h':higher},derivs={'funding':derivative},
        market={'btcdom':market},symbol='BTC/USDT',context_out=contexts)
    assert before[-1] and available[-1]
    bundle=K.capture(contexts[0],spec)
    bar=int(base['ts'].iloc[-1].timestamp()*1000)
    assert K.replay(json.loads(json.dumps(bundle)),bar)=={'signal':True,'available':True}
    base.loc[base.index[-1],'close']=1e6
    derivative['value']=-1.
    market['close']=0.
    assert K.replay(bundle,bar)=={'signal':True,'available':True}  # fully detached inputs


def test_world_exit_replays_exact_retained_model_cuts_without_default_store(retained,monkeypatch):
    from trader.world.context import load_context
    path,df=retained
    spec=_spec(timeframe='4h',filters=[],entry_long='close > 0',entry_short='',
        exit=ExitSpec(signal_exit='world_observation("volume_anomaly", "intraday") > 0'))
    context=load_context(path,as_of_ms=NOW)
    df=df.copy();df.attrs['as_of_ms']=NOW
    compiled=compile_spec(spec);contexts=[]
    before,available=compiled.exit_signal_detail({'4h':df},symbol=SYMBOL,world=context,context_out=contexts)
    assert before[-1] and available[-1]
    bundle=K.capture(contexts[0],spec)
    monkeypatch.setattr('trader.world.context.load_context',lambda *a,**k:pytest.fail('must use retained WorldModel'))
    bar=int(df['ts'].iloc[-1].timestamp()*1000)
    assert K.replay(bundle,bar)=={'signal':True,'available':True}
    assert bundle['world']['queries']
    assert all('observations' in q for q in bundle['world']['queries'])


def test_frame_roundtrip_preserves_exact_floats_nan_index_and_receipt_attrs():
    f=pd.DataFrame({'ts':pd.to_datetime([T0,T0+1],unit='ms',utc=True),
        'value':[np.nextafter(1.,2.),np.nan]},index=[8,9])
    f.attrs=dict(as_of_ms=T0+10,cut=np.int64(T0),data=np.array([1.,np.nan]))
    restored=K.unpack(json.loads(json.dumps(K.pack(f))))
    pd.testing.assert_frame_equal(f,restored)
    assert restored.attrs['as_of_ms']==f.attrs['as_of_ms']
    np.testing.assert_equal(restored.attrs['data'],f.attrs['data'])


def test_compiler_version_mismatch_refuses_context_replay():
    base=_bars(T0,20,HOUR//4);spec=_spec(exit=ExitSpec(signal_exit='close > 0'))
    out=[];compile_spec(spec).exit_signal_detail({'15m':base},context_out=out)
    bundle=deepcopy(K.capture(out[0],spec));bundle['compiler']['feature_version']='different'
    with pytest.raises(ValueError,match='compiler_differs'):K.replay(bundle,T0)
