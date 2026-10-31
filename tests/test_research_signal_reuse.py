"""Reusing evaluated signals must preserve the original rotation experiment."""
import numpy as np
import pytest

from tests.test_research_evaluate import _bundle, _combo, DONCH, EMA
from tests.test_null_baseline import _trending_frame
from trader.research import evaluate as E
from trader.strategy import null_baseline as N
from trader.strategy.compile import compile_spec, CompiledStrategy
from trader.strategy.spec import StrategySpec, ExitSpec


@pytest.mark.parametrize('split',[None,.65])
@pytest.mark.parametrize('signal_exit',[False,True])
def test_raw_signal_reuse_preserves_signed_funding_masks_slices_and_exits(monkeypatch,split,signal_exit):
    df=_trending_frame()
    spec=StrategySpec(id='reuse-probe',name='Signal reuse probe',
        thesis='A diagnostic of identical input reuse across actual and null calculations, without admission authority.',
        invalidation='Refuse any difference in the registered null comparison.',provenance={},universe={},
        timeframe='15m',direction='both',entry_long='close > ema(20)',entry_short='close < ema(20)',
        filters=[],exit=ExitSpec(signal_exit='rsi(14) < 30' if signal_exit else ''),
        regime_filter=[],markets=['futures'])
    compiled=compile_spec(spec);frames={'15m':df}
    raw=compiled.entries(frames)
    detail=compiled.exit_signal_detail(frames) if signal_exit else (None,None)
    funding=np.sin(np.arange(len(df))*.1)*.0001
    allow=np.ones(len(df),dtype=bool);allow[::3]=False;allow[300:340]=False
    kwargs=dict(draws=25,seed=19,split=split,funding=funding,entry_eligibility=allow)
    expected=N.assess(compiled,frames,{'risk_per_trade_pct':1.,'real_funding':True},1.1,**kwargs)
    def forbidden(*a,**kw):raise AssertionError('signals were computed again')
    monkeypatch.setattr(compiled,'entries',forbidden)
    monkeypatch.setattr(compiled,'exit_signal_detail',forbidden)
    actual=N.assess(compiled,frames,{'risk_per_trade_pct':1.,'real_funding':True},1.1,
                    raw_entries=raw,exit_details=detail,**kwargs)
    assert actual==expected


def test_full_evaluation_is_identical_to_recomputing_null_signals(monkeypatch):
    bundle=_bundle(n_symbols=4,n=1200)
    for sym,df in bundle.frames.items():
        allow=np.ones(len(df),dtype=bool);allow[::3]=False
        bundle.entry_eligibility[sym]=allow
    calls=[];original=CompiledStrategy.entries
    def counted(self,*a,**kw):
        calls.append(1);return original(self,*a,**kw)
    monkeypatch.setattr(CompiledStrategy,'entries',counted)
    fast=E.evaluate(_combo([DONCH,EMA]),bundle,draws=20,min_symbol_trades=1)
    fast_calls=len(calls);calls.clear()
    assess=N.assess
    def recompute(*args,**kwargs):
        kwargs.pop('raw_entries',None);kwargs.pop('exit_details',None)
        return assess(*args,**kwargs)
    monkeypatch.setattr(N,'assess',recompute)
    reference=E.evaluate(_combo([DONCH,EMA]),bundle,draws=20,min_symbol_trades=1)
    assert fast==reference
    assert fast_calls==len(bundle.frames)
    assert len(calls)>fast_calls
