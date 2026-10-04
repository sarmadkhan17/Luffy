"""Synthetic FIRST-USE receipt clocks; never infer them for production history."""
from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from trader.research.universe import NoExchange


def qualify(path,*,source='offline:synthetic-first-use',tf='4h'):
    """Retain deterministic artificial receipts for explicitly generated test bars.

    Each fixture bar was observed exactly at its close by construction. This
    helper cannot be used by normal production acquisition or migration.
    """
    feed=DataFeed(exchange=NoExchange(),db_path=path)
    db=feed.db
    rows=db.execute('SELECT symbol,tf,ts,open,high,low,close,volume,taker_buy FROM candles WHERE tf=? ORDER BY symbol,ts',(tf,)).fetchall()
    records=[]
    for symbol,timeframe,event,*numbers in rows:
        values=dict(zip(P.VALUES,numbers))
        at=event+P.TF_MS[timeframe]
        raw={'event_ms':event,**values}
        body=dict(instrument_id='offline:futures:'+symbol.replace('/','').split(':')[0],
            source=source,kind='candle',event_time_ms=event,observed_at_ms=at,available_at_ms=at,
            request_started_ms=at,request_id=P.digest([source,symbol,timeframe,event]),
            content_hash=P.digest({'raw':raw,'value':values}),supersedes=None,quality='VALID',bar_state='FINAL',
            raw_json=P.encode(raw),transform_version="TEST_ONLY_GENERATED_DATA",max_age_ms=None)
        body['revision_id']=P.digest({k:v for k,v in body.items() if k!='supersedes'})
        record=dict(body,**values)
        records.append((body['revision_id'],DataFeed._series_key(symbol,timeframe),event,at,at,P.encode(record)))
    with db:db.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)',records)
    db.close();feed._local.conn=None
