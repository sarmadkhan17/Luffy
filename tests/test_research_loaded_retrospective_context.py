"""The real evaluator must use bound research inputs, never legacy global readers."""
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest

from tests.test_wrld05_normal_context import retained, NOW, SYMBOL  # noqa: F401
from trader.research import evaluate as ev
from trader.research.combo import Combination
from trader.research.vocab import Part
from trader.strategy import spec_evidence
from trader.world.context import load_context
from trader.world import WorldHistory


def bundle(retained):
    path, full = retained
    frame=full.iloc[-3:].reset_index(drop=True).copy()
    descriptor=dict(dataset_id='private-research-test', cut_ms=NOW)
    frame.attrs.update(read_mode='retrospective', retrospective=descriptor, symbol=SYMBOL, timeframe='4h')
    frame.attrs.pop('as_of_ms',None)  # retrospective readers carry historical_cut_ms, never as-of authority
    frame['observation_quality']='VALID'
    funding=pd.DataFrame(dict(ts=frame['ts'],value=.0001,quality='VALID',
        event_time_ms=frame['ts'].astype('int64') // (10**6 if frame['ts'].dtype.unit=='ns' else 1),
        max_age_ms=8*3600_000))
    funding.attrs.update(read_mode='retrospective',retrospective=descriptor)
    world=replace(load_context(path),as_of_ms=NOW,allow_reconstruction=True,dataset_id=descriptor['dataset_id'])
    return ev.Bundle(tf='4h',frames={SYMBOL:frame},sym_frames={SYMBOL:{'4h':frame}},
        universe={SYMBOL:{'4h':frame}},btc=None,market=None,derivs={SYMBOL:{'funding':funding}},
        risk=dict(real_funding=False,risk_per_trade_pct=.5,taker_fee_pct=.04,slippage_atr_frac=.015,
                  funding_rate_8h=.0001,bar_minutes=240),world=world,cut=NOW)


def combo():
    p=Part('test:bound-world-funding','event','bound-world-funding',
        'world_observation("volume_anomaly", "intraday") > 1 and funding > 0','close < 0')
    return Combination(parts=(p,),tf='4h',geo='trail')


def test_loaded_funding_and_world_reach_simulation_without_dataset_paths(retained,monkeypatch):
    b=bundle(retained)
    def forbidden(*args,**kwargs):
        pytest.fail('loaded context reopened a legacy/global dependency store')
    monkeypatch.setattr(spec_evidence,'dependency_store',forbidden)
    calls=[]
    real=ev.simulate
    def simulate(*args,**kwargs):
        calls.append(1)
        return real(*args,**kwargs)
    monkeypatch.setattr(ev,'simulate',simulate)
    result=ev.evaluate(combo(),b,draws=20)
    assert not result.get('untested') and calls


@pytest.mark.parametrize('missing',['funding','world_dataset','world_coverage'])
def test_invalid_bound_context_is_refused_before_simulation(retained,monkeypatch,missing):
    b=bundle(retained)
    if missing=='funding': b.derivs[SYMBOL]={}
    elif missing=='world_dataset': b.world=replace(b.world,dataset_id='wrong')
    else: b.world=replace(b.world,history=WorldHistory((b.world.history.get_exact(NOW),)))
    monkeypatch.setattr(ev,'simulate',lambda *a,**k:pytest.fail('unavailable inputs reached simulation'))
    result=ev.evaluate(combo(),b,draws=20)
    assert result.get('untested') and result['context_gaps']
