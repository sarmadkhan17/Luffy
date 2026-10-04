#!/usr/bin/env python3
"""Opt-in metadata-only runtime attribution; configured workers stay enabled.

Wall minus thread CPU includes I/O, lock/GIL and scheduling waits; it is not an
I/O measurement. Inclusive stages nest; sum exclusive costs only within one
thread. Run from the deployed checkout with --output outside production data.
"""
def main():
 import sys,os,json,time,threading,functools,inspect
 from pathlib import Path
 import argparse
 ap=argparse.ArgumentParser()
 ap.add_argument('--output',type=Path,required=True)
 ap.add_argument('--config')
 args=ap.parse_args()
 ROOT=Path(__file__).resolve().parents[1]
 sys.path.insert(0,str(ROOT))
 OUT=args.output
 OUT.mkdir(parents=True,exist_ok=True)
 sys.argv=[sys.argv[0]]+(['--config',args.config] if args.config else [])
 local=threading.local(); lock=threading.Lock(); totals={}; counters={}
 fd=os.open(OUT/'stages.jsonl',os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
 def emit(rec):
  os.write(fd,(json.dumps(dict(at=time.time(),pid=os.getpid(),native_id=threading.get_native_id(),thread=threading.current_thread().name,**rec),sort_keys=True)+'\n').encode())
 def wrap(obj,name,label=None,meta=None):
  fn=getattr(obj,name);descriptor=inspect.getattr_static(obj,name);label=label or obj.__name__+'.'+name
  @functools.wraps(fn)
  def run(*a,**kw):
   stack=getattr(local,'stack',None)
   if stack is None: local.stack=stack=[]
   frame=[time.perf_counter(),time.thread_time(),0.,0.];stack.append(frame);error=None;result=None
   if name in ('cycle','boot','__init__','_portfolio_checkpoint','_snapshot_for'):
    emit(dict(stage=label,status='BEGIN'))
   try:
    result=fn(*a,**kw);return result
   except BaseException as exc: error=type(exc).__name__;raise
   finally:
    elapsed=time.perf_counter()-frame[0];cpu=time.thread_time()-frame[1];stack.pop()
    if stack:stack[-1][2]+=elapsed;stack[-1][3]+=cpu
    rec=dict(stage=label,status='FAIL' if error else 'COMPLETE',error_class=error,elapsed_s=elapsed,cpu_s=cpu,exclusive_elapsed_s=max(0,elapsed-frame[2]),exclusive_cpu_s=max(0,cpu-frame[3]),wall_minus_thread_cpu_s=max(0,elapsed-cpu))
    if meta:
     try:rec.update(meta(a,kw,result))
     except Exception:rec['metadata_error']=True
    with lock:
     key=(threading.current_thread().name,label);t=totals.setdefault(key,dict(calls=0,elapsed=0,cpu=0,exclusive_elapsed=0,exclusive_cpu=0,failures=0));t['calls']+=1;t['elapsed']+=elapsed;t['cpu']+=cpu;t['exclusive_elapsed']+=rec['exclusive_elapsed_s'];t['exclusive_cpu']+=rec['exclusive_cpu_s'];t['failures']+=int(error is not None)
    if label!='evidence.blob' and (elapsed>=.05 or error or name in ('cycle','boot','__init__','beat')):emit(rec)
    if label=='kernel.cycle':
     with lock:
      summary=dict(stage='cycle.summary',stats=result,timings=[dict(thread=k[0],stage=k[1],**v) for k,v in totals.items()],counters=dict(counters))
      totals.clear();counters.clear()
     emit(summary)
  setattr(obj,name,staticmethod(run) if isinstance(descriptor,staticmethod) else run)
 from trader import kernel as K
 from trader.core import journal_evidence as E
 from trader.data import market_provenance as M
 from trader.data.feed import DataFeed,Universe
 from trader.data.derivatives import DerivFeed
 from trader.core.journal import Journal
 from trader.engine.watchdog import Heartbeat
 for name,fn in list(vars(K.Kernel).items()):
  if callable(fn) and name not in ('main',):wrap(K.Kernel,name,'kernel.'+name)
 for cls,names in [(DataFeed,('fetch_multi','fetch_ohlcv','cached_ohlcv','_store_save')),(Universe,('membership_receipts','_rescan')),(DerivFeed,('save','load','record')),(Journal,('log_brain_event','query','log_decision')),(Heartbeat,('beat',))]:
  for name in names:
   if hasattr(cls,name):wrap(cls,name)
 def blobmeta(a,kw,result):
  with lock:
   counters['blob_resolutions']=counters.get('blob_resolutions',0)+1;counters['expanded_bytes_including_repeated']=counters.get('expanded_bytes_including_repeated',0)+(len(result) if result else 0)
  return {}
 wrap(E,'_blob','evidence.blob',blobmeta)
 def decodemeta(a,kw,result):
  with lock:
   counters['blob_decodes']=counters.get('blob_decodes',0)+1;counters['decoded_bytes']=counters.get('decoded_bytes',0)+(len(result) if result else 0)
  return {}
 wrap(E,'_decode_blob','evidence.decode',decodemeta)
 wrap(E.zlib,'compress','evidence.compress',lambda a,k,r:dict(expanded_bytes=len(a[0]),compressed_bytes=len(r)))
 for name in ('store','resolve'):
  wrap(E,name,'evidence.'+name,lambda a,k,r:dict(output_bytes=len(r.encode()) if isinstance(r,str) else 0))
 for name in ('load','append','eligible_frame','usable_current'):
  wrap(M,name,'market.'+name,lambda a,k,r:dict(rows_returned=len(r) if r is not None else 0))
 from trader.portfolio import current as P
 from scripts import opportunity_context_shadow as S
 from trader.learning import capture as C, capture_runtime as R, dispatch as D, runtime as L
 for obj,names in [(P,('freeze','checkpoint')),(S,('capture',)),(C,('snapshot','resolve','register')),
                   (R,('decision','runtime_inputs')),(D,('dispatch_pending',)),(L,('checkpoint',))]:
  for name in names:
   if hasattr(obj,name):wrap(obj,name)
 import requests
 wrap(requests.Session,'send','network.wait')
 from trader.brain.llm import BrainLLM
 def admissionmeta(a,k,r):
  with lock:
   counters['provider_admissions']=counters.get('provider_admissions',0)+int(r is not None)
  return dict(admitted=r is not None,enabled=a[0]._brain_config.get('enabled',True) is True)
 wrap(BrainLLM,'_admit','provider.admission',admissionmeta)
 def querymeta(a,k,r):
  return dict(rows_returned=len(r) if r is not None else 0)
 # Query results are counted, never logged.
 wrap(Journal,'query','database.rows',querymeta)
 import sqlite3
 from contextlib import contextmanager
 from trader.data import sqlite_tx as T
 original=T.write_tx
 @contextmanager
 def tx(local,conn,label=''):
  start=time.perf_counter();cpu=time.thread_time();emit(dict(stage='data.transaction.begin',label=label,database=conn.execute('PRAGMA database_list').fetchone()[2],in_transaction=conn.in_transaction))
  body_end=None
  try:
   with original(local,conn,label) as db:
    try:yield db
    finally:body_end=time.perf_counter()
  finally:
   try: active=conn.in_transaction
   except sqlite3.Error: active='CLOSED'
   end=time.perf_counter()
   emit(dict(stage='data.transaction.end',label=label,elapsed_s=end-start,cpu_s=time.thread_time()-cpu,body_s=None if body_end is None else body_end-start,commit_or_cleanup_s=None if body_end is None else end-body_end,in_transaction=active))
 T.write_tx=tx
 emit(dict(stage='profile.start',workers_disabled=[]))
 K.main()


if __name__ == '__main__':
 main()
