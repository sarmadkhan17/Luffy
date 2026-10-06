"""GOV-07 read-only evidence mapping; never launch, signal, fetch or alter runtime."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, subprocess, yaml
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
R3=Path('/home/sarmad/Documents/Codex/2026-10-05/task-5/perf01-owner-measurement-r3')
def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(*args):
 p=subprocess.run(['git','--no-optional-locks','-C',str(ROOT),*args],capture_output=True,text=True,timeout=20)
 return p.stdout.strip() if p.returncode==0 else None
start=now();head=git('rev-parse','HEAD')
tracked=git('status','--porcelain','--untracked-files=no')
runtime_dirty=git('status','--porcelain','--untracked-files=no','--','trader','config.yaml','org.yaml','restart.sh','frontend/src')
untracked_runtime=git('ls-files','--others','--exclude-standard','--','trader','config.yaml','org.yaml','restart.sh','frontend/src')
config=ROOT/'config.yaml';cfg=yaml.safe_load(config.read_text())
files=(git('ls-files','--','trader','config.yaml','org.yaml','restart.sh','frontend/src') or '').splitlines()
source_files=[{'path':p,'sha256':sha(ROOT/p),'bytes':(ROOT/p).stat().st_size} for p in files if (ROOT/p).is_file()]
source_digest=hashlib.sha256(json.dumps(source_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
# Read-only remote reference query. No fetch/push or deployment action.
remote_started=now()
try:
 remote=subprocess.run(['git','-C',str(ROOT),'ls-remote','origin','refs/heads/main'],capture_output=True,text=True,timeout=20)
 remote_head=remote.stdout.split()[0] if remote.returncode==0 and remote.stdout.split() else None
except subprocess.TimeoutExpired:remote_head=None
remote_done=now()
# Hash existing external artifacts; never execute the retained launcher/preflight.
manifest=json.loads((R3/'preparation-manifest.json').read_text())
hashes=json.loads((R3/'run/evidence-hashes.json').read_text())
def map_hashes(base,entries):
 result=[]
 for p,expected in entries.items():
  path=base/p;actual=sha(path) if path.is_file() else None
  result.append({'path':str(path),'expected_sha256':expected,'actual_sha256':actual,'status':'MATCH' if actual==expected else ('MISSING' if actual is None else 'UNVERIFIED_HASH_MISMATCH')})
 return result
pre=json.loads((R3/'run/preflight.json').read_text());pids=json.loads((R3/'run/pids.json').read_text());final=json.loads((R3/'run/production-final-proof.json').read_text())
events=[json.loads(x) for x in (R3/'run/controller-events.jsonl').read_text().splitlines() if x]
rev=pre['head']
config_blob=subprocess.run(['git','-C',str(ROOT),'show',rev+':config.yaml'],capture_output=True,timeout=20)
oldcfg=hashlib.sha256(config_blob.stdout).hexdigest() if config_blob.returncode==0 else None
remote_refs=(git('for-each-ref','--format=%(refname) %(objectname)','refs/remotes') or '').splitlines()
branches=(git('for-each-ref','--format=%(refname) %(objectname)','refs/heads') or '').splitlines()
def process(pid,expected_ticks=None):
 base=Path('/proc')/str(pid)
 try:
  ticks=int((base/'stat').read_text().rsplit(')',1)[1].split()[19])
  argv=(base/'cmdline').read_bytes().split(b'\0')
  module=argv[2].decode() if len(argv)>2 and argv[1]==b'-m' else None
  return {'pid':pid,'start_ticks':ticks,'expected_start_ticks':expected_ticks,'pid_start_matches':ticks==int(expected_ticks) if expected_ticks is not None else None,'module':module,'cwd':str((base/'cwd').resolve()),'executable':str((base/'exe').resolve()),'loaded_revision':None,'loaded_configuration_sha256':None,'quality':'UNVERIFIED_LOADED_CODE_AND_CONFIG'}
 except (OSError,ValueError):return {'pid':pid,'expected_start_ticks':expected_ticks,'state':'ABSENT_OR_UNREADABLE','loaded_revision':None,'loaded_configuration_sha256':None}
processes=[process(v['pid'],v['start_ticks']) for v in final['remaining_processes']]
retained=[]
for name in ['kernel_instance.json','heartbeat_luffy.json']:
 p=ROOT/'data'/name
 if p.exists():
  value=json.loads(p.read_text());selected={k:value.get(k) for k in ['schema','instance_id','pid','start_ticks','revision','dirty_code','started_at','publication_ts','last_successful_work_ts']}
  retained.append({'path':str(p),'sha256':sha(p),'fields':selected,'quality':'RETAINED_ONLY_NOT_CURRENT_PROCESS_IDENTITY'})
 else:retained.append({'path':str(p),'status':'ABSENT'})
finish_head=git('rev-parse','HEAD');finished=now()
result={'schema':'luffy-gov07-identity-map.v1','started_at_utc':start,'finished_at_utc':finished,'mapping_process':{'pid':os.getpid(),'start_ticks':int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'kind':'OFFLINE_EVIDENCE_MAPPER_NOT_KERNEL'},'local_checkout':{'root':str(ROOT),'head':head,'head_at_end':finish_head,'head_stable_during_capture':head==finish_head,'branch':git('branch','--show-current'),'tracked_dirty':tracked,'tracked_runtime_config_dirty':runtime_dirty,'untracked_runtime_paths':untracked_runtime,'quality':'UNVERIFIED_DIRTY_CHECKOUT' if tracked or runtime_dirty or untracked_runtime or head!=finish_head else 'CLEAN_LOCAL_SOURCE_ONLY_NOT_DEPLOYMENT','configuration':{'path':'config.yaml','sha256':sha(config),'bytes':config.stat().st_size,'declared_version':cfg.get('version'),'declared_schema':cfg.get('schema'),'version_quality':'CONTENT_HASH_IDENTITY_NO_DECLARED_VERSION' if cfg.get('version') is None else 'DECLARED_VERSION_AND_CONTENT_HASH','last_change_commit':git('log','-1','--format=%H','--','config.yaml')},'source_files':source_files,'source_manifest_sha256':source_digest},'remote_repository':{'remote_name':'origin','ref':'refs/heads/main','head':remote_head,'queried_at_start_utc':remote_started,'queried_at_end_utc':remote_done,'matches_local_head':remote_head==head if remote_head else None,'quality':'READ_ONLY_REF_QUERY_NOT_DEPLOYED_PROCESS_PROOF','cached_remote_refs':remote_refs},'historical_branches':{'local_refs':branches,'policy':'Historical/checkpoint refs remain reference evidence; not merged or selected as current truth. Git ancestry alone does not transfer run proof.'},'retained_r3':{'scope':'HISTORICAL_RUN_ONLY; cannot prove current checkout or loaded Dashboard','run_id':R3.name,'root':str(R3),'revision':rev,'config_sha256':pre['config_sha256'],'config_hash_at_recorded_git_revision':oldcfg,'config_matches_recorded_revision':oldcfg==pre['config_sha256'],'config_matches_current_local':sha(config)==pre['config_sha256'],'preflight_at_epoch':pre['at'],'preflight_at_utc':datetime.fromtimestamp(pre['at'],timezone.utc).isoformat(),'kernel':{k:v for k,v in pids['kernel'].items() if k!='command'},'launch_at_epoch':pids['launch_requested_at'],'launch_at_utc':datetime.fromtimestamp(pids['launch_requested_at'],timezone.utc).isoformat(),'last_event_at_utc':datetime.fromtimestamp(events[-1]['at'],timezone.utc).isoformat(),'production_final_head':final['production_head'],'preflight_matches_final_head':pre['head']==final['production_head'],'final_source_config_unchanged_reported':final['config_unchanged'] and not final['changed_non_graphify_tracked_files'],'preparation_required_head':manifest['required_production_head'],'preparation_revision':manifest['revision'],'preparation_files':map_hashes(R3,manifest['files']),'run_artifacts':map_hashes(R3/'run',hashes)},'retained_process_observation':{'observed_at_utc':finished,'dashboard_processes':processes,'kernel_records':retained,'policy':'PID/start ticks prove process continuity only; cwd HEAD does not identify already-loaded modules/config. No process launch/signal or HTTP call.'},'blocking_facts':['Current loaded Dashboard revision and config hash are UNKNOWN; matching historical PID/start ticks does not establish current local/deployed consistency.','Local HEAD differs from remotely queried main; remote ref and historical run identity are separate from current local build identity.','Whole checkout is dirty; any variant remains UNVERIFIED, with scoped runtime/config dirt captured separately.'],'operational_actions':{'kernel_launched':False,'dashboard_launched':False,'provider_calls':0,'signals_sent':0,'fetch_push_merge':False}}
(OUT/'identity-map.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'local_head':head,'head_stable':head==finish_head,'remote_head':remote_head,'runtime_dirty':bool(runtime_dirty),'run_hashes':{s:sum(x['status']==s for x in result['retained_r3']['run_artifacts']) for s in ['MATCH','MISSING','UNVERIFIED_HASH_MISMATCH']},'prep_hashes':{s:sum(x['status']==s for x in result['retained_r3']['preparation_files']) for s in ['MATCH','MISSING','UNVERIFIED_HASH_MISMATCH']},'historical_config_matches':oldcfg==pre['config_sha256'],'current_config_matches':sha(config)==pre['config_sha256']},indent=2))
