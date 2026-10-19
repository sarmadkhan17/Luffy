import sqlite3,json,collections
TF={'5m':300000,'15m':900000,'1h':3600000,'4h':14400000}
c=sqlite3.connect('file:/mnt/luffy-data/test-tmp/sarmad/claude-1000/-mnt-luffy-data/22c9c57f-c1c1-4218-aa46-555f77713c9c/scratchpad/candles_copy.db?mode=ro',uri=True)
out=collections.defaultdict(lambda: collections.Counter())
rows=c.execute("select series_key,event_ms,available_ms,observed_ms from market_revisions indexed by market_revision_cut")
n=0
for k,e,a,o in rows:
    tf=k.rsplit(':',1)[1]; t=TF.get(tf)
    if t is None: continue
    if a is None: b='available_null'
    else:
        d=a-(e+t)
        b=('before_close' if d<0 else 'exact_close' if d==0 else '<=1s' if d<=1000 else '<=1m' if d<=60000 else '<=1tf' if d<=t else '<=1d' if d<=86400000 else '>1d')
    out[tf][b]+=1; n+=1
json.dump({k:dict(v) for k,v in out.items()},open('/mnt/luffy-data/test-tmp/sarmad/claude-1000/-mnt-luffy-data/22c9c57f-c1c1-4218-aa46-555f77713c9c/scratchpad/delay_scan.json','w'),indent=1)
print(n)
