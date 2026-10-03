"""Explicit offline market receipts for live-boundary unit probes."""
import pandas as pd
from trader.core.types import Snapshot as RawSnapshot, TF_MS
from trader.data import market_provenance as P


def snapshot(*args, **kwargs):
    snap=RawSnapshot(*args,**kwargs)
    ends=[int(P.ms(df['ts'])[-1])+TF_MS.get(key.removeprefix('BTC_'),0)
          for key,df in snap.dfs.items() if df is not None and len(df)]
    at=max(ends,default=1_780_000_000_000)+1
    snap.ts=pd.Timestamp(at,unit='ms',tz='UTC').isoformat()
    snap.dfs={key:P.annotate(df,instrument_id='offline:futures:'+snap.symbol.replace('/','').split(':')[0],
                            source='offline:market-fixture',kind='candle',received_ms=at,
                            timeframe=key.removeprefix('BTC_'))
              for key,df in snap.dfs.items()}
    snap.market={key:P.annotate(df,instrument_id='ref:'+key,source='offline:reference-fixture',
                               kind='reference',received_ms=at,timeframe='4h')
                 for key,df in (snap.market or {}).items()}
    snap.derivs={key:P.annotate(df,instrument_id='offline:futures:'+snap.symbol.replace('/','').split(':')[0],
                               source='offline:derivative-fixture',kind='derivative',received_ms=at)
                 for key,df in (snap.derivs or {}).items()}
    return snap
