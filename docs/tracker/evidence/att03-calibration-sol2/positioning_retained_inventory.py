"""Bounded exact-cut inventory for retained anchor derivatives; no label inference."""
from pathlib import Path
import json,gzip,sqlite3,sys,socket
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT))
from trader.data import market_provenance as mp
from trader.cognition.contracts import load_input
from trader.cognition.attention import _positioning
EV=Path(__file__).resolve().parent
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
with gzip.open(EV/'frozen-scans.jsonl.gz','rt') as f:
 scan=next(json.loads(line) for line in f if json.loads(line)['source']=='data/attention.db')
cut=scan['as_of_ms'];path=ROOT/'data/derivs.db';db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.execute('BEGIN')
records=[];inventory=[]
for symbol in ['BTC/USDT','ETH/USDT']:
 for series in ['funding','ls_ratio']:
  key='derivative:'+symbol+':'+series
  stats=db.execute('SELECT COUNT(*),MIN(event_ms),MAX(event_ms),MIN(available_ms),MAX(available_ms) FROM market_revisions WHERE series_key=?',(key,)).fetchone()
  df=mp.load(db,key,as_of_ms=cut,limit=21)
  row={'symbol':symbol,'series':series,'record_count':stats[0],'event_range_ms':list(stats[1:3]),'receipt_range_ms':list(stats[3:]),'cut_ms':cut,'selected_revisions':[]}
  if df is not None:
   for _,r in df.iterrows():
    records.append(dict(symbol=symbol,series=series,ts=int(r.event_time_ms),value=float(r.value) if r.quality=='VALID' else None,available_ms=int(r.available_at_ms),observed_ms=int(r.observed_at_ms),source=r.source,instrument_id=r.instrument_id,revision_id=r.revision_id,quality=r.quality));row['selected_revisions'].append(r.revision_id)
  inventory.append(row)
db.close()
raw={'schema':'cognition.input.v1','timeframe':'4h','decision_times':[cut],'candles':[],'membership':[],'positioning':records}
ds=load_input(raw)
def add(kind,symbol,status,value,detail,*args,**kwargs):return SimpleNamespace(status=status,value=value,detail=detail)
for row in inventory:
 result=_positioning(ds,row['symbol'],row['series'],cut,add);row['component_status']=result.status;row['signed_z']=result.value;row['detail']=result.detail;row['false_trigger_rate']=None;row['missed_trigger_rate']=None
(EV/'retained-positioning-results.json').write_text(json.dumps({'source':'data/derivs.db','read_mode':'mode=ro transaction; existing hash/index/cut-qualified reader; limit21 per anchor series','scope':'Additional exploratory availability inventory only, not a new confirmation set; never added to archived scans or normal admission.','as_of_ms':cut,'independent_labels':0,'inventory':inventory,'input':raw,'rejected_inputs':ds.rejected},indent=2)+'\n')
print([(r['symbol'],r['series'],r['component_status']) for r in inventory])
