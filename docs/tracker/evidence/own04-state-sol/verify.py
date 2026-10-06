"""Offline OWN-04 source/control verification; no service or Journal startup."""
from pathlib import Path
import collections, hashlib, json, subprocess, sys, yaml
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from trader.dashboard.tracker import read_tracker
EV=Path(__file__).resolve().parent
l=yaml.safe_load((ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.yaml').read_text())
s=yaml.safe_load((ROOT/'STATE.yaml').read_text()); n=yaml.safe_load((ROOT/'NEXT.yaml').read_text())
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
rev=yaml.safe_load((EV/'intake.yaml').read_text())['control_base_revision']
b=yaml.safe_load(subprocess.check_output(['git','show',rev+':docs/tracker/LUFFY_Product_Tracker_v1.yaml'],cwd=ROOT,text=True))
br={r['id']:r for r in b['items']}; rows={r['id']:r for r in l['items']}; checks=[]
def check(name,yes):
    assert yes,name
    checks.append(name)
check('all_ids_conditions_dependencies_preserved',len(rows)==178 and set(rows)==set(br) and all(rows[k]['closure_condition']==br[k]['closure_condition'] and rows[k]['dependencies']==br[k]['dependencies'] for k in rows))
check('only_own04_row_changed',all(rows[k]==br[k] for k in rows if k!='OWN-04'))
check('own04_dependency_blocked_not_fake_closed',rows['OWN-04']['status']=='BLOCKED' and rows['OWN-04']['dependencies']==['MON-01','MON-05'])
check('monitoring_gui_and_economics_rows_unchanged',all(rows[k]==br[k] for k in ['MON-01','MON-05','GUI-01','GUI-06','GUI-07','GUI-09','GUX-02','GUX-04','ACC-03']))
check('deferral_and_future_activation_conditions_preserved',l['current_release_deferral']==b['current_release_deferral'] and all(rows[k]==br[k] for k in ['SEC-01','RUN-01','GOV-07']))
check('selection_parity',l['current_build_selection']==s['current_build_selection']==n['current_build_selection']==m['current_build_selection'])
selection=l['current_build_selection']
check('acc03_recommendation_only',selection['active_items']==[] and not selection['next_item_selected'] and selection['next_recommended_item']=='ACC-03' and not selection['runtime_execution_authorized'])
check('terminal_parity',l['own04_state_terminal']==s['own04_state_terminal']==n['own04_state_terminal']==m['own04_state_terminal'])
check('historical_control_snapshot_retained',l['metadata']['control_snapshot']==s['control_snapshot']==n['control_snapshot']==m['control_snapshot'])
check('manifest_hashes',all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] and len((ROOT/f['path']).read_bytes())==f['bytes'] for f in m['files']))
counts=collections.Counter(r['status'] for r in l['items'])
check('status_counts',all(counts[k]==v for k,v in l['metadata']['status_counts'].items()))
source=json.loads((EV/'source-manifest.json').read_text())
check('source_manifest_matches',all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in source['files']))
check('configuration_roster_sdd_unchanged',all(subprocess.check_output(['git','show',rev+':'+p],cwd=ROOT)==(ROOT/p).read_bytes() for p in ['config.yaml','org.yaml','SDD.md']))
check('owner_tracker_read_available',read_tracker(ROOT)['status']=='AVAILABLE' and read_tracker(ROOT)['read_only'])
check('no_runtime_provider_trading_authority',all(n['work_package'][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized']))
(EV/'control-validation.json').write_text(json.dumps({'result':'PASS','checks':checks,'count':len(checks)},indent=2)+'\n')
print('PASS',len(checks),'checks')
