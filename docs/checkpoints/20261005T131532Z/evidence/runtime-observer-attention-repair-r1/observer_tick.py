"""Existing monitor entry points, independently scheduled; metadata only."""
import sys,time,json,os
from pathlib import Path
P=Path('/mnt/luffy-data/luffy/production');O=Path(__file__).parent
started=time.perf_counter();cpu=time.process_time();sys.path.insert(0,str(P))
from scripts.monitor import safety_check
from trader.core.config import load_config
imported=time.perf_counter();mode=sys.argv[1];cfg=load_config(P/'config.yaml')
result=safety_check(P,cfg,heartbeat_only=mode=='heartbeat',sink=lambda e:print(json.dumps(dict(event='safety_transition',**{k:e.get(k) for k in ('condition','incident_id','status','reason','detected_at','resolved_at')})),file=sys.stderr,flush=True))
components=[]
for component in result['components']:
 row={k:v for k,v in component.items() if k!='record'}
 if component.get('record'):
  r=component['record'];row['heartbeat']=dict(instance_id=r['instance_id'],sequence=r['sequence'],timestamp=r['timestamp'],last_successful_cycle_at=r['context']['last_successful_cycle_at'])
 components.append(row)
record=dict(at=time.time(),mode=mode,pid=os.getpid(),elapsed_s=time.perf_counter()-started,cpu_s=time.process_time()-cpu,import_s=imported-started,components=components,recovery_required=result['health']['recovery_required'],active_conditions={k:v.get('reason') for k,v in result['health']['conditions'].items() if v['status']=='ACTIVE'},integrity=result['health'].get('integrity',{}))
# A bounded rotating host-side log; no evidence payloads or trading authority.
path=O/('observer-'+mode+'.jsonl')
if path.exists() and path.stat().st_size>2*1024**2:path.replace(path.with_suffix('.jsonl.1'))
with path.open('a') as out:out.write(json.dumps(record)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(int(bool(result['health']['recovery_required'])))
