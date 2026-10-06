#!/usr/bin/env python3
"""Opt-in metadata-only runtime attribution; configured workers stay enabled.

Wall minus thread CPU includes I/O, lock/GIL and scheduling waits; it is not an
I/O measurement. Inclusive stages nest; sum exclusive costs only within one
thread. Run from the deployed checkout with --output outside production data.
"""
def main():
 import sys,os,json,time,threading,functools,inspect,hashlib
 from pathlib import Path
 import argparse
 ap=argparse.ArgumentParser()
 ap.add_argument('--execute-authorized',action='store_true')
 ap.add_argument('--output',type=Path,required=True)
 ap.add_argument('--config')
 ap.add_argument('--cycles',type=int,help='Stop cleanly at this completed cycle boundary; stop on a failed/over-240-second cycle too.')
 args=ap.parse_args()
 if not args.execute_authorized: ap.error("explicit later execution authorization required")
 ROOT=Path('/mnt/luffy-data/luffy/production')
 sys.path.insert(0,str(ROOT))
 OUT=args.output
 OUT.mkdir(parents=True,exist_ok=True)
 sys.argv=[sys.argv[0]]+(['--config',args.config] if args.config else [])
 local=threading.local(); lock=threading.RLock(); totals={}; counters={}; completed_cycles=0
 fd=os.open(OUT/'stages.jsonl',os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
 def emit(rec):
  record=dict(at=time.time(),monotonic_s=time.monotonic(),pid=os.getpid(),native_id=threading.get_native_id(),thread=threading.current_thread().name,**rec)
  raw=(json.dumps(record,sort_keys=True)+'\n').encode()
  with lock:
   if len(raw)>65536:
    raw=(json.dumps(dict(at=time.time(),stage=rec.get('stage'),status='METADATA_BOUND',original_bytes=len(raw)))+'\n').encode()
   if os.fstat(fd).st_size+len(raw)>64*1024**2:
    (OUT/'EVIDENCE_LIMIT').touch();return
   view=memoryview(raw)
   while view: view=view[os.write(fd,view):]

 def wrap(obj,name,label=None,meta=None):
  fn=getattr(obj,name);descriptor=inspect.getattr_static(obj,name);label=label or obj.__name__+'.'+name
  @functools.wraps(fn)
  def run(*a,**kw):
   nonlocal completed_cycles
   stack=getattr(local,'stack',None)
   if stack is None: local.stack=stack=[]
   frame=[time.perf_counter(),time.thread_time(),0.,0.];stack.append(frame);error=None;result=None
   if name in ('cycle','boot','__init__','_portfolio_checkpoint','_snapshot_for','_scan_symbols','log_decision','begin','capture','step','harvest_once','refresh_all','record_all','backfill'):
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
    if label!='evidence.blob' and (elapsed>=.05 or error or label in ('market.load','portfolio.sources_load') or name in ('cycle','boot','__init__','beat','_snapshot_for','_scan_symbols','log_decision','begin','capture','step','harvest_once','refresh_all','record_all','backfill')):emit(rec)
    if label=='kernel.cycle':
     with lock:
      summary=dict(stage='cycle.summary',stats=result,timings=[dict(thread=k[0],stage=k[1],**v) for k,v in totals.items()],counters=dict(counters))
      totals.clear();counters.clear()
     emit(summary)
     completed_cycles+=1
     if args.cycles is not None and (completed_cycles>=args.cycles or error or elapsed>240):
      # At a completed boundary no producer transaction is interrupted, and
      # the controller cannot race the next cycle while recording storage.
      a[0]._graceful(15,None)
    elif label=='kernel.boot':
     with lock:
      startup=dict(stage='startup.summary',timings=[dict(thread=k[0],stage=k[1],**v) for k,v in totals.items()],counters=dict(counters))
      totals.clear();counters.clear()
     emit(startup)
  setattr(obj,name,staticmethod(run) if isinstance(descriptor,staticmethod) else run)
 emit(dict(stage='kernel.import',status='BEGIN'))
 imported_wall=time.perf_counter();imported_cpu=time.process_time()
 try:
  from trader import kernel as K
 except BaseException as exc:
  emit(dict(stage='kernel.import',status='FAIL',error_class=type(exc).__name__,elapsed_s=time.perf_counter()-imported_wall,cpu_s=time.process_time()-imported_cpu));raise
 emit(dict(stage='kernel.import',status='COMPLETE',elapsed_s=time.perf_counter()-imported_wall,cpu_s=time.process_time()-imported_cpu))
 setup_wall=time.perf_counter();setup_cpu=time.process_time()

 from trader.core import journal_evidence as E
 from trader.data import market_provenance as M
 from trader.data.feed import DataFeed,Universe
 from trader.data.derivatives import DerivFeed
 from trader.data.references import RefStore
 from trader.core.journal import Journal
 from trader.engine.watchdog import Heartbeat
 for name,fn in list(vars(K.Kernel).items()):
  if callable(fn) and name not in ('main',):
   meta=None
   if name=='__init__': meta=lambda a,k,r:dict(heartbeat_instance_id=a[0].heartbeat.instance_id,heartbeat_started_at=a[0].heartbeat.started_at)
   if name=='_snapshot_for': meta=lambda a,k,r:dict(symbol=a[1],snapshot_constructed=r is not None)
   if name=='_scan_symbols': meta=lambda a,k,r:dict(candidate_count=len(r))
   wrap(K.Kernel,name,'kernel.'+name,meta)
 for cls,names in [(DataFeed,('fetch_multi','fetch_ohlcv','cached_ohlcv','_store_save')),(Universe,('membership_receipts','_rescan')),(DerivFeed,('save','load','record','backfill','record_all')),(RefStore,('save','load')),(Journal,('log_brain_event','query','log_decision')),(Heartbeat,('beat',))]:
  for name in names:
   if hasattr(cls,name):
    meta=None
    if cls is Journal and name=='log_decision':
     meta=lambda a,k,r:dict(decision_id=a[1].id,symbol=a[1].symbol,action=a[1].action.value,
                          executed=a[1].executed,skip_reason=str(a[1].skip_reason or '')[:1024])
    wrap(cls,name,meta=meta)
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
 wrap(E.evidence_zlib,'compress','evidence.compress',lambda a,k,r:dict(expanded_bytes=len(a[0]),compressed_bytes=len(r),native=E.evidence_zlib.NATIVE))
 for name in ('store','resolve'):
  wrap(E,name,'evidence.'+name,lambda a,k,r:dict(output_bytes=len(r.encode()) if isinstance(r,str) else 0))
 def loadmeta(a,k,r):
  rows=len(r) if r is not None else 0
  with lock:
   counters['market_detail_rows_decoded']=counters.get('market_detail_rows_decoded',0)+rows
  return dict(series_key=a[1],rows_returned=rows,read_selection={key:value for key,value in k.items() if key in ('as_of_ms','limit','replay_tf','include_partial','revision_stream','instrument_id','source','window_start_ms')})
 wrap(M,'load','market.load',loadmeta)
 for name in ('append','eligible_frame','usable_current'):
  wrap(M,name,'market.'+name,lambda a,k,r:dict(rows_returned=len(r) if r is not None else 0))
 from trader.portfolio import current as P
 from trader.portfolio import allocator as A
 from trader.portfolio import runtime as PR, reoptimization as G
 from trader.observability import scan_source as SS
 from trader.observability.collector import Collector
 from scripts import opportunity_context_shadow as S
 from trader.learning import capture as C, capture_runtime as R, dispatch as D, runtime as L, foundation as F
 for obj,names in [(P,('freeze','checkpoint')),(S,('capture',)),(C,('snapshot','resolve','register')),
                   (R,('decision','runtime_inputs','exact_frame','freeze')),(D,('dispatch_pending',)),(L,('checkpoint',))]:
  for name in names:
   if hasattr(obj,name):wrap(obj,name)
 wrap(F,'canonical','evidence.serialize')
 wrap(F,'digest','evidence.hash')
 wrap(A,'canonical','portfolio.serialize')
 wrap(A,'digest','portfolio.hash')
 wrap(A,'_source_digest','portfolio.source_verify')
 wrap(P,'capture','portfolio.sources_load',lambda a,k,r:dict(scan_id=r[4].get('scan_id'),current_attention=r[4].get('current_attention'),missing_sources=r[4].get('missing_sources'),requests=len(r[0])))
 wrap(SS,'read','portfolio.attention_read',lambda a,k,r:dict(scan_id=a[1],members=len(r.get('membership',())) if r else 0))
 wrap(PR.Consumer,'consume','portfolio.evaluate_store')
 wrap(G,'evaluate','portfolio.event_evaluate')
 wrap(G,'replay','portfolio.event_replay')
 wrap(Collector,'_run','attention.worker',lambda a,k,r:dict(scan_id=a[1].get('scan_id'),kind=a[1].get('kind'),proof=r))
 wrap(Collector,'_job','attention.serialize',lambda a,k,r:dict(expanded_bytes=len(r.encode())))
 wrap(M,'encode','provenance.serialize')
 wrap(M,'digest','provenance.hash')
 import requests
 wrap(requests.Session,'send','network.wait')
 from trader.brain.llm import BrainLLM
 def admissionmeta(a,k,r):
  with lock:
   counters['provider_admissions']=counters.get('provider_admissions',0)+int(r is not None)
  return dict(admitted=r is not None,enabled=a[0]._brain_config.get('enabled',True) is True)
 wrap(BrainLLM,'_admit','provider.admission',admissionmeta)
 def querymeta(a,k,r):
  caller=sys._getframe(2)
  return dict(rows_returned=len(r) if r is not None else 0,sql_sha256=hashlib.sha256(a[1].encode()).hexdigest(),caller=caller.f_code.co_name,caller_file=Path(caller.f_code.co_filename).name,caller_line=caller.f_lineno)
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
 from trader.observability import collector as AT
 wrap(AT,'capture','attention.capture',lambda a,k,r:dict(capture_ms=r['capture_ms'],symbols=r['scope']['included_count'],issues=[x['reason'] for x in r['issues']]))
 wrap(Collector,'begin','attention.begin')
 from trader.research.runner import ResearchRunner
 wrap(ResearchRunner,'step','research.step',lambda a,k,r:dict(skipped=r.get('skipped'),reason=r.get('reason'),cycle_seconds=k.get('cycle_seconds')))
 from trader.brain.scraper import Scraper
 wrap(Scraper,'harvest_once','scraper.harvest',lambda a,k,r:dict(counts=r))
 from trader.data import ref_sources
 wrap(ref_sources,'refresh_all','references.refresh',lambda a,k,r:dict(successes=sum(not isinstance(v,str) for v in r.values()),errors=sum(isinstance(v,str) for v in r.values())))
 emit(dict(stage='profile.setup',status='COMPLETE',elapsed_s=time.perf_counter()-setup_wall,cpu_s=time.process_time()-setup_cpu))
 emit(dict(stage='profile.start',workers_disabled=[]))
 try: K.main()
 finally:
  emit(dict(stage='profile.exit',status='COMPLETE'))
  os.fsync(fd);os.close(fd)


if __name__ == '__main__':
 main()
