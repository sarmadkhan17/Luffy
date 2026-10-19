"""QR07-15 root cause on isolated synthetic receipts: usage root_cause_probe.py CODE_ROOT.

Per delay after the candle close: the FINAL receipt that annotate() produces
(available == observed == received), whether the retained-replay reader admits
it, and what the replay predicate compared.
"""
import json, sys, tempfile, pathlib
sys.path.insert(0, sys.argv[1])
import pandas as pd
from trader.core.types import TF_MS
from trader.data import market_provenance as P
from trader.data.feed import DataFeed

T0, Q = 1_780_000_000_000 // 900_000 * 900_000, TF_MS['15m']
rows = []
for delay in (0, 1, 1_000, Q // 2, Q, Q + 1, 3 * Q):
    d = pathlib.Path(tempfile.mkdtemp())
    bar = pd.DataFrame(dict(ts=pd.to_datetime([T0], unit='ms', utc=True), open=[100.], high=[101.], low=[99.], close=[100.], volume=[5.]))
    r = P.annotate(bar, instrument_id='offline:BTCUSDT', source='offline:probe', kind='candle', received_ms=T0 + Q + delay, timeframe='15m')
    f = DataFeed(exchange=None, db_path=d / 'c.db', clock_ms=lambda: T0 + 10 * Q)
    f._store_save('BTC/USDT', '15m', r, now_ms=T0 + Q + delay)
    got = f.replay_ohlcv('BTC/USDT', '15m')
    rows.append(dict(delay_ms=delay, bar_state=r.bar_state[0], quality=r.quality[0], available_minus_close=int(r.available_at_ms[0] - (T0 + Q)),
                     replay_eligible=0 if got is None else len(got)))
print(json.dumps(rows, indent=1))
