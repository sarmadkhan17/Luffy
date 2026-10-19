"""Run existing harnesses on explicitly synthetic candles; no data stores opened."""
import runpy
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from trader.data import feed
from trader.strategy import evidence
n=1200
rng=np.random.default_rng(707)
c=100*np.exp(np.cumsum(rng.normal(.0001,.005,n)))
o=np.r_[c[0],c[:-1]]
df=pd.DataFrame(dict(ts=pd.date_range('2026-01-01',periods=n,freq='15min',tz='UTC'),
    open=o,high=np.maximum(c,o)*1.002,low=np.minimum(c,o)*.998,close=c,
    volume=rng.uniform(100,10000,n)))
feed.DataFeed=lambda *a,**k: object()
evidence.load_frames=lambda *a,**k: {'SYNTHETIC/USDT':df}
script='scripts/backtest_equivalence.py' if sys.argv[-1]=='equivalence' else 'scripts/bench_vector_backtest.py'
print('D7 OFFLINE SYNTHETIC INPUTS ONLY; existing harness, no historical-evidence claim',flush=True)
runpy.run_path(str(ROOT/script),run_name='__main__')
