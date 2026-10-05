import sys,sqlite3,json,math,time
from pathlib import Path
sys.path.insert(0,'/mnt/luffy-data/luffy/workspaces/runtime-observer-attention-repair-r1')
from trader.core.journal import Journal
from trader.learning import capture as C,capture_runtime as R
from trader.data import market_provenance as M
from trader.engine.outcomes import resolve_pending,pd_to_dt
from trader.cognition.outcomes import timestamp
O=Path(__file__).parent;P=Path('/mnt/luffy-data/luffy/production/data');j=Journal(O/'retained-clone/luffy.db');c=sqlite3.connect((P/'candles.db').as_uri()+'?mode=ro',uri=True)
results=[]
for meta in json.load(open(O/'nan-target-reproduction.json')):
 key=meta['decision_id'];original=dict(j.query('SELECT * FROM outcomes WHERE decision_id=?',(key,))[0]);cut=meta['measured_ms'];df=M.load(c,'candle:'+original['symbol']+':5m',as_of_ms=cut,limit=650)
 raw_targets={h:row for h,row in ((h,df[df['ts']>=pd_to_dt(timestamp(original['ts'])+ms)].iloc[0].to_dict()) for h,ms in [('1h',3600000),('4h',14400000)])}
 # Confirm the deployed serialization failure before repairing any input.
 try:json.dumps(raw_targets,allow_nan=False,default=str);original_error=None
 except ValueError as e:original_error=type(e).__name__+':nonfinite_target_metadata'
 assert original_error
 class Scoped:
  def query(self,sql,*args):return [dict(original,resolved_at=None,fwd_ret_1h=None,fwd_ret_4h=None)]
  def _tx(self):return j._tx()
 class Feed:
  def fetch_ohlcv(self,*a,**k):return df.copy()
 failed_before=j.query('SELECT rowid,* FROM learning_capture_failures');counts_before=len(j.query('SELECT outcome_id FROM learning_outcome_captures'))
 t=time.perf_counter();assert resolve_pending(Scoped(),Feed(),now_ms=cut)==1
 assert j.query('SELECT rowid,* FROM learning_capture_failures')==failed_before
 new=j.query("SELECT outcome_id,payload FROM learning_outcome_captures WHERE outcome_key LIKE 'forward:%' AND json_extract(payload,'$.registration.event_key')=?",('decision:'+key,))
 assert new
 body=json.loads(new[-1]['payload']);dep=next(x for x in body['dependencies'] if x['role']=='outcome');measurement=C.resolve(j._conn(),dep)
 assert all(bar['supersedes'] is None for bar in measurement['target_bars'].values())
 before=len(j.query('SELECT outcome_id FROM learning_outcome_captures'));assert resolve_pending(Scoped(),Feed(),now_ms=cut)==1;assert len(j.query('SELECT outcome_id FROM learning_outcome_captures'))==before
 out,retained=C.learning_outcome(j._conn(),new[-1]['outcome_id']);replay=__import__('trader.learning.foundation',fromlist=['replay']).replay(out,retained)
 r=dict(decision_id=key,original_error=original_error,fixed_path='engine.outcomes.resolve_pending -> capture.safely -> capture_runtime.forward',elapsed_s=time.perf_counter()-t,original_refusal_history_unchanged=True,logical_retry_duplicate=False,outcome_id=new[-1]['outcome_id'],replay_status=replay.status,replay_faults=replay.faults)
 results.append(r);print(json.dumps(r),flush=True)
(O/'forward-fixed-reproductions.json').write_text(json.dumps(results,indent=2))
