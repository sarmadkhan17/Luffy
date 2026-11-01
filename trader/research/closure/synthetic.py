"""Explicitly synthetic candle snapshots for tests and calibration only.

Bars carry artificial first-close receipts (observed at close by construction).
Nothing here may be used on real history: the receipt source is labelled
`offline:synthetic` and the exposure ledger for these files is declared by the
caller, never inferred.
"""
import sqlite3
from pathlib import Path

import numpy as np

from trader.data import market_provenance as P

TF_MS = 14_400_000
SOURCE = 'offline:synthetic-closure'
EPOCH_MS = 1_600_000_000_000 // TF_MS * TF_MS


def symbols(n):
    return [f'S{i}/USDT' for i in range(n)]


def write_snapshot(path, *, n_symbols=8, n_bars=3600, seed=1, edge=0.0,
                   common=0.0, vol=0.009, start_ms=EPOCH_MS, block=60, lever=None,tf='4h', include_btc_reference=False):
    """Candles with an optional planted continuation `edge` (drift per bar in
    alternating regime blocks shared by all markets) and a `common` factor
    loading that makes the markets move together (correlated no-edge when
    edge == 0). Returns the symbol list."""
    step=P.TF_MS[tf]
    path = Path(path)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute(
        'CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, '
        'ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, '
        'volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))')
    rng0 = np.random.default_rng(seed)
    factor = rng0.normal(0.0, vol, n_bars)
    names = symbols(n_symbols)
    rets = {}                      # `lever={i: (j, m)}`: symbol i follows symbol j's returns times m (a leveraged twin), planted
    # The reference is additional context, never an extra discovery/held-out
    # target. Existing market seeds and values stay identical.
    generated = names + (['BTC/USDT'] if include_btc_reference and 'BTC/USDT' not in names else [])
    for i, name in enumerate(generated):
        rng = np.random.default_rng(seed * 1009 + i)
        px, rows = 100.0, []
        rets[i] = np.zeros(n_bars)
        for b in range(n_bars):
            drift = (edge if (b // block) % 2 == 0 else -edge / 7.0) if edge else 0.0
            ret = drift + common * factor[b] + rng.normal(0.0, vol * (1.0 - 0.5 * common))
            if lever and i in lever:
                ret = lever[i][1] * rets[lever[i][0]][b]
            rets[i][b] = ret
            px *= 1.0 + ret
            volume = 10.0 * (1.0 + abs(rng.normal(0, 0.3)))
            rows.append((name, tf, start_ms + b * step, px, px * 1.004, px * 0.996, px, volume, volume / 2))
        con.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()
    _receipts(path)
    return names


def _receipts(path):
    from trader.data.feed import DataFeed
    from trader.research.universe import NoExchange
    feed = DataFeed(exchange=NoExchange(), db_path=path)
    db = feed.db
    rows = db.execute('SELECT symbol,tf,ts,open,high,low,close,volume,taker_buy FROM candles '
                      'ORDER BY symbol,tf,ts').fetchall()
    records = []
    for symbol, timeframe, event, *numbers in rows:
        values = dict(zip(P.VALUES, numbers))
        at = event + P.TF_MS[timeframe]
        raw = {'event_ms': event, **values}
        body = dict(instrument_id='offline:futures:' + symbol.replace('/', '').split(':')[0],
                    source=SOURCE, kind='candle', event_time_ms=event, observed_at_ms=at,
                    available_at_ms=at, request_started_ms=at,
                    request_id=P.digest([SOURCE, symbol, timeframe, event]),
                    content_hash=P.digest({'raw': raw, 'value': values}), supersedes=None,
                    quality='VALID', bar_state='FINAL', raw_json=P.encode(raw),
                    transform_version='TEST_ONLY_GENERATED_DATA', max_age_ms=None)
        body['revision_id'] = P.digest({k: v for k, v in body.items() if k != 'supersedes'})
        record = dict(body, **values)
        records.append((body['revision_id'], DataFeed._series_key(symbol, timeframe), event, at, at,
                        P.encode(record)))
    with db:
        db.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)', records)
    db.close()
    feed._local.conn = None
