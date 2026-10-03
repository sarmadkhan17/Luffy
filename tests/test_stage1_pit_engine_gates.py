"""Run repository engine gates on synthetic offline data, never production DBs."""
import runpy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def offline_engine(monkeypatch):
    from trader.core import config
    from trader.data import feed
    from trader.strategy import evidence
    from tests.test_vector_backtest import RISK
    n=1200
    rng=np.random.default_rng(20261003)
    close=100*np.exp(np.cumsum(rng.normal(.00015,.006,n)))
    df=pd.DataFrame(dict(ts=pd.date_range('2026-01-01',periods=n,freq='15min',tz='UTC'),
        open=close,high=close*1.005,low=close*.995,close=close,volume=rng.uniform(100,1000,n)))
    df.attrs.update(timeframe='15m',fixture='synthetic numerical engine parity only')
    monkeypatch.setattr(config,'load_config',lambda:{'risk':dict(RISK,real_funding=False)})
    monkeypatch.setattr(feed,'DataFeed',lambda:object())
    monkeypatch.setattr(evidence,'load_frames',lambda *args:{'SYNTHETIC/USDT':df})


@pytest.mark.parametrize('script',['backtest_equivalence.py','bench_vector_backtest.py'])
def test_repository_engine_gate_offline(script,offline_engine,capsys):
    root=Path(__file__).resolve().parents[1]
    with pytest.raises(SystemExit) as finished:
        runpy.run_path(str(root/'scripts'/script),run_name='__main__')
    output=capsys.readouterr().out
    print(output)
    assert finished.value.code==0,output
