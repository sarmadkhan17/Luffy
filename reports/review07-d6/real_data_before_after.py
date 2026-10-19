"""Read-only D6 measurement over an isolated copy of the retained candle store.

usage: real_data_before_after.py CODE_ROOT DB_COPY SERIES_JSON OUT_JSON
CODE_ROOT selects which source tree answers (base ef9fca8-lineage vs D6); the
database is opened mode=ro through DataFeed and never written.
"""
import json, os, sys, time
root, db, series_json, out = sys.argv[1:5]
sys.path.insert(0, root)
from trader.data.feed import DataFeed
from trader.data import market_provenance as mp
from trader.core.types import TF_MS
try:
    from trader.strategy import fill_timing
except ImportError:
    fill_timing = None
WARMUP = 210
NOW = int(os.path.getmtime(db) * 1000)
feed = DataFeed(exchange=None, db_path=db, clock_ms=lambda: NOW, read_only=True)
series = json.load(open(series_json))
rows, t0 = [], time.time()
for tf in ('4h', '1h', '15m'):
    for rec in series[tf]:
        sym = rec[0].split(':', 1)[1].rsplit(':', 1)[0]
        meta = feed.replay_metadata(sym, tf)
        row = dict(symbol=sym, tf=tf, stored=meta.attrs['stored_bars'], eligible=len(meta))
        df = feed.replay_ohlcv(sym, tf)
        if df is not None and len(df):
            dec = mp.decision_ms(df, tf)
            row.update(first_decision_ms=int(dec.min()), last_decision_ms=int(dec.max()),
                       delay_ms_median=int((dec - (mp.ms(df['ts']) + TF_MS[tf])).mean() * 0 + float(sorted(dec - (mp.ms(df['ts']) + TF_MS[tf]))[len(df) // 2])))
            if fill_timing:
                row['timing'] = fill_timing.summary(df, WARMUP)
                row['timing_no_history'] = fill_timing.summary(df)
                fill, _ = fill_timing.fills(df, WARMUP)
                row['signal_capable_bars'] = int((fill[WARMUP:] >= 0).sum())
        rows.append(row)
json.dump(dict(code_root=root, now_ms=NOW, seconds=round(time.time() - t0, 1), rows=rows), open(out, 'w'))
print(len(rows), 'series in', round(time.time() - t0), 's')
