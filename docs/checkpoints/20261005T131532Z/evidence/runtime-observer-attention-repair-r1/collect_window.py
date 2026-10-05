"""Read bounded existing metrics for an hour; never fabricate success or restart."""
import json,time,sqlite3,subprocess,hashlib,os,signal,re,urllib.request
from pathlib import Path
P=Path('/mnt/luffy-data/luffy/production');E=Path(__file__).parent;PY=str(P/'venv/bin/python');OLD=Path('/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence');STOP=E/'STOP';started=time.time();records=[];seen=set();failure=None;pidfile=json.loads((E/'pids.json').read_text());start_at=pidfile['start_requested_at'];deadline=start_at+3600
from dotenv import load_dotenv
load_dotenv(P/'.env')
def storage(name):subprocess.run([PY,str(OLD/'storage.py'),str(E/(name+'.json'))],check=True)
def state():
 with sqlite3.connect((P/'data/luffy.db').as_uri()+'?mode=ro',uri=True,timeout=1) as db:kv=dict(db.execute("SELECT key,value FROM state_kv WHERE key IN ('control_state','macro_guard_operator_hold','execution_recovery')"))
 h=json.loads((P/'data/safety_health.json').read_text());return dict(kv=kv,recovery_required=h['recovery_required'],active={k:v for k,v in h['conditions'].items() if v['status']=='ACTIVE'},watchdog_off=(P/'data/watchdog.off').exists(),provider_ledger_sha=hashlib.sha256((P/'data/brain_usage.json').read_bytes()).hexdigest())
def attention():
 with sqlite3.connect((P/'data/attention.db').as_uri()+'?mode=ro',uri=True,timeout=1) as db:
  row=db.execute('SELECT scan_id,as_of_ms FROM scans WHERE causes_complete=1 AND payload IS NOT NULL ORDER BY as_of_ms DESC LIMIT 1').fetchone()
  if row is None:return dict(status='NO_COMPLETED_SCAN')
  text=db.execute("SELECT json_group_array(json_object('symbol',json_extract(j.value,'$.symbol'),'selected',json_extract(j.value,'$.selected'),'reason',json_extract(j.value,'$.reason'),'rank',json_extract(j.value,'$.rank'),'salience',json_extract(j.value,'$.salience'),'dominant',json_extract(j.value,'$.dominant'))) FROM scans s,json_each(s.payload,'$.rows') j WHERE s.scan_id=?",(row[0],)).fetchone()[0]
  return dict(scan_id=row[0],as_of_ms=row[1],rows=json.loads(text))
def log_cycle(n):
 try:
  text=Path('/tmp/opencode/luffy_kernel.log').read_text(errors='replace')[-100000:]
  matches=[x for x in text.splitlines() if re.search(r'cycle #'+str(n)+r'\b',x)]
  if matches:
   m=re.search(r'([0-9]+\.[0-9]+)s$',matches[-1]);return dict(line=matches[-1],elapsed_s=float(m[1]) if m else None)
 except OSError:pass
 return None
while time.time()<deadline:
 if STOP.exists():failure=STOP.read_text().strip() or 'operator_stop';break
 missing=[role for role in ('kernel','dashboard') if not Path('/proc',str(pidfile[role])).exists()]
 if missing:failure='process_exited:'+','.join(missing);break
 try:
  s=state();b=json.loads((P/'data/heartbeat_luffy.json').read_text());identity=(b['instance_id'],b['sequence']);current=b['started_at']>=start_at-1
  real_active={k:v for k,v in s['active'].items() if not (k=='heartbeat:luffy' and v.get('reason')=='PRODUCER_RESTART_REQUIRES_RECOVERY') and not (k=='heartbeat:luffy' and v.get('reason')=='STALE' and (not current or v.get('context',{}).get('record',{}).get('instance_id') != b['instance_id']))}
  sample=dict(at=time.time(),safety=s,classification='CURRENT_RUNTIME' if current else 'STARTUP_PREVIOUS_INSTANCE_HEARTBEAT',kernel_threads=len(list(Path('/proc',str(pidfile['kernel']),'task').iterdir())),dashboard_alive=True)
  with (E/'samples.jsonl').open('a') as f:f.write(json.dumps(sample)+'\n')
  if s['kv'].get('control_state')!='FROZEN' or s['kv'].get('macro_guard_operator_hold')!='1' or not s['recovery_required'] or not s['watchdog_off']:failure='required_controls_changed';break
  if real_active:failure='active_safety_failure:'+','.join(real_active);break
  if s['provider_ledger_sha'] != pidfile['provider_ledger_sha']:failure='provider_ledger_changed';break
  if current and time.time()-b['context']['last_successful_cycle_at']>240:failure='current_successful_heartbeat_stale';break
  if current and identity not in seen:
   seen.add(identity)
   time.sleep(.4) # logging follows the genuine atomic heartbeat; no restamping
   cycle=log_cycle(b['sequence']);previous=records[-1]['heartbeat']['context']['last_successful_cycle_at'] if records else None
   gap=b['context']['last_successful_cycle_at']-previous if previous is not None else None
   record=dict(at=time.time(),heartbeat=b,cycle=cycle,successful_gap_s=gap,cold_start_to_first_heartbeat_s=b['timestamp']-start_at if not records else None,safety=s)
   try:record['attention_selection']=attention()
   except Exception as exc:record['attention_metadata_error']=type(exc).__name__
   try:
    with urllib.request.urlopen(urllib.request.Request('http://192.168.126.131:8080/',headers={'X-Luffy-Token':os.environ['DASH_TOKEN']}),timeout=3) as response:record['dashboard_status']=response.status
   except Exception as exc:record['dashboard_status']=type(exc).__name__
   records.append(record);(E/'cycles.json').write_text(json.dumps(records,indent=2));storage('storage-cycle-'+str(b['sequence']))
   print(json.dumps(dict(event='cycle',sequence=b['sequence'],cycle_s=(cycle or {}).get('elapsed_s'),gap=gap,attention=b['context'].get('attention',{}).get('status'),portfolio=b['context'].get('portfolio_runtime'),scan=b['context'].get('scan_boundaries'))),flush=True)
   if gap is not None and gap>240:failure='successful_cycle_gap_exceeded_240';break
   if cycle and cycle['elapsed_s'] is not None and cycle['elapsed_s']>240:failure='cycle_exceeded_240';break
 except (OSError,sqlite3.Error,ValueError,KeyError) as exc:
  print(json.dumps(dict(event='measurement_error',error=type(exc).__name__,at=time.time())),flush=True)
 time.sleep(5)
storage('storage-window-end');status=dict(start_requested_at=start_at,finished_at=time.time(),duration_s=time.time()-start_at,cycles=len(records),failure=failure,ongoing_observer=['luffy-frozen-safety-observer.timer','luffy-frozen-heartbeat-observer.timer'],pids=pidfile)
if failure:
 for role in ('dashboard','kernel'):
  try:os.kill(pidfile[role],signal.SIGTERM)
  except ProcessLookupError:pass
 status['containment']='SIGTERM sent; control remains FROZEN, no restart/recovery'
(E/'window-result.json').write_text(json.dumps(status,indent=2));print(json.dumps(status),flush=True)
