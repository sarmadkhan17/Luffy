import sqlite3,json,collections
TF={'5m':300000,'15m':900000,'1h':3600000,'4h':14400000}
c=sqlite3.connect('file:/mnt/luffy-data/test-tmp/sarmad/claude-1000/-mnt-luffy-data/22c9c57f-c1c1-4218-aa46-555f77713c9c/scratchpad/candles_copy.db?mode=ro',uri=True)
res={}
for tf,t in TF.items():
    q=f"""select series_key,count(*),count(distinct event_ms),min(event_ms),max(event_ms),
      sum(available_ms=event_ms+{t}), sum(available_ms>event_ms+{t} and available_ms<=event_ms+2*{t}),
      sum(available_ms>event_ms+2*{t}), sum(available_ms<event_ms+{t})
      from market_revisions indexed by market_revision_cut where series_key like '%:{tf}' group by series_key"""
    res[tf]=c.execute(q).fetchall()
json.dump(res,open('/mnt/luffy-data/test-tmp/sarmad/claude-1000/-mnt-luffy-data/22c9c57f-c1c1-4218-aa46-555f77713c9c/scratchpad/series_scan.json','w'))
print({tf:len(v) for tf,v in res.items()})
