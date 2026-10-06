"""Offline OWN-03 closure/control verification; no Journal or server startup."""
from pathlib import Path
import yaml, json, hashlib, subprocess, collections, sys
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from trader.dashboard.tracker import read_tracker
EV = Path(__file__).resolve().parent
l = yaml.safe_load((ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.yaml').read_text())
s = yaml.safe_load((ROOT/'STATE.yaml').read_text())
n = yaml.safe_load((ROOT/'NEXT.yaml').read_text())
m = json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
rev = yaml.safe_load((EV/'intake.yaml').read_text())['source_revision']
b = yaml.safe_load(subprocess.check_output(['git','show',rev+':docs/tracker/LUFFY_Product_Tracker_v1.yaml'], cwd=ROOT, text=True))
br = {r['id']:r for r in b['items']}
rows = {r['id']:r for r in l['items']}
checks = []
def check(name, value):
    assert value, name
    checks.append(name)
check('all_ids_conditions_dependencies_preserved', len(rows)==178 and set(rows)==set(br) and all(rows[k]['closure_condition']==br[k]['closure_condition'] and rows[k]['dependencies']==br[k]['dependencies'] for k in rows))
check('only_own03_row_changed', all(rows[k]==br[k] for k in rows if k!='OWN-03'))
check('own03_closed_dependency_satisfied', rows['OWN-03']['status']==rows['OWN-02']['status']=='CLOSED')
check('no_other_owner_rows_or_gui_closed', all(rows[k]==br[k] for k in ['OWN-01','OWN-04','GUI-01']))
check('selection_parity', l['current_build_selection']==s['current_build_selection']==n['current_build_selection']==m['current_build_selection'])
selection = l['current_build_selection']
check('no_successor_selected', selection['active_items']==[] and not selection['next_item_selected'] and selection['next_recommended_item']=='OWN-04')
check('deferred_gate_policy_retained', all(rows[k]==br[k] and rows[k]['status']=='DEFERRED' for k in ['SEC-01','RUN-01','GOV-07']) and selection['excluded_blocker_items']==b['current_build_selection']['excluded_blocker_items'])
check('source_config_sdd_pinned', all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in json.loads((EV/'source-manifest.json').read_text())['files']))
check('snapshot_parity_retained', l['metadata']['control_snapshot']==s['control_snapshot']==n['control_snapshot']==m['control_snapshot'])
check('manifest_hashes', all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] and len((ROOT/f['path']).read_bytes())==f['bytes'] for f in m['files']))
counts = collections.Counter(r['status'] for r in l['items'])
check('status_counts', all(counts[k]==v for k,v in l['metadata']['status_counts'].items()))
p = read_tracker(ROOT)
check('existing_read_only_tracker_available', p['status']=='AVAILABLE' and p['read_only'] and len(p['items'])==178)
check('runtime_authorization_retained', all(n['work_package'][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized']))
(EV/'control-validation.json').write_text(json.dumps({'result':'PASS','checks':checks,'count':len(checks)}, indent=2)+'\n')
print('PASS', len(checks), 'checks')
