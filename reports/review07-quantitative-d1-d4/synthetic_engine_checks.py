"""Run the existing engine gates with generated data and no store/provider access."""
import runpy
import sys
from copy import deepcopy
from pathlib import Path
import numpy as np
import pandas as pd
from trader.core import config
from trader.data import feed
from trader.strategy import evidence

ROOT=Path(__file__).resolve().parents[2]
cfg=deepcopy(config.load_config())
cfg['risk']['real_funding']=False
n=1200
rng=np.random.default_rng(707)
close=100*np.exp(np.cumsum(rng.normal(.0001,.008,n)))
open_=np.r_[close[0],close[:-1]]
frame=pd.DataFrame(dict(ts=pd.date_range('2020-01-01',periods=n,freq='15min',tz='UTC'),
    open=open_,high=np.maximum(open_,close)*1.005,low=np.minimum(open_,close)*.995,
    close=close,volume=rng.lognormal(5,1,n)))
frame.attrs['timeframe']='15m'
config.load_config=lambda:deepcopy(cfg)
feed.DataFeed=lambda:None
evidence.load_frames=lambda *a,**k:{'SYNTHETIC/USDT':frame.copy()}
print('SYNTHETIC ONLY; existing harness; seed=707; real_funding=False',flush=True)
runpy.run_path(str(ROOT/'scripts'/sys.argv[1]),run_name='__main__')
