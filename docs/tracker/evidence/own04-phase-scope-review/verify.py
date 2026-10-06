"""Requirement-scope/control checks only; no service, trading or provider calls."""
from pathlib import Path
import collections,hashlib,json,subprocess,sys,yaml
ROOT=Path(__file__).resolve().parents[4];EV=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from trader.dashboard.tracker import read_tracker
intake=yaml.safe_load((EV/'intake.yaml').read_text());rev=intake['revision']
b=yaml.safe_load(subprocess.check_output(['git','show',rev+':docs/tracker/LUFFY_Product_Tracker_v1.yaml'],cwd=ROOT,text=True))
l=yaml.safe_load((ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.yaml').read_text());s=yaml.safe_load((ROOT/'STATE.yaml').read_text());n=yaml.safe_load((ROOT/'NEXT.yaml').read_text());m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
br={r['id']:r for r in b['items']};r={v['id']:v for v in l['items']};checks=[]
def check(name,ok):
    assert ok,name
    checks.append(name)
check('all_178_ids_preserved_no_new_requirement',len(r)==178 and set(r)==set(br))
fields=['required_behavior','why_needed','closure_condition','failure_regression_proof','dependencies','parent_ids','related_items']
check('all_substantive_requirements_and_dependency_edges_preserved',all(r[k][f]==br[k][f] for k in r for f in fields))
changed={'OWN-04','MON-01','MON-05','GUI-01'}
check('only_four_scope_evidence_rows_changed',all(r[k]==br[k] for k in r if k not in changed))
check('original_readiness_gates_preserved',l['readiness_gates']==b['readiness_gates'])
check('own04_current_phase_closed_offline_only',r['OWN-04']['status']=='CLOSED' and r['OWN-04']['release_scope']=='CURRENT_BUILD_PHASE' and r['OWN-04']['closure_scope']=='ARCHITECTURE_OFFLINE_GUI' and r['OWN-04']['runtime_evidence']==br['OWN-04']['runtime_evidence'])
check('own04_future_requirements_mandatory_unproven',r['OWN-04']['dependencies']==['MON-01','MON-05'] and r['OWN-04']['proof_state']['future_live_activation']['status']=='REQUIRED_NOT_PROVEN' and r['OWN-04']['proof_state']['future_live_activation']['must_satisfy_original_conditions_before_activation'])
check('both_monitoring_rows_deferred_not_closed',all(r[k]['status']=='DEFERRED' and r[k]['release_scope']=='NOT_REQUIRED_FOR_CURRENT_RELEASE' and r[k]['required_before'].startswith('FUTURE_LIVE_ACTIVATION') and r[k]['proof_state']['future_live_activation']['status']=='REQUIRED_NOT_PROVEN' for k in ['MON-01','MON-05']))
check('monitoring_existing_evidence_and_gaps_retained',all(r[k]['latest_evidence'].startswith(br[k]['latest_evidence']) and r[k]['implementation_evidence']==br[k]['implementation_evidence'] for k in ['MON-01','MON-05']))
check('existing_sec_run_gov_deferral_rows_unchanged',all(r[k]==br[k] for k in ['SEC-01','RUN-01','GOV-07']))
check('release_deferral_parity',l['current_release_deferral']==s['current_release_deferral']==n['current_release_deferral']==m['current_release_deferral'])
check('both_monitoring_requirements_in_explicit_phase_deferral',all(k in l['current_release_deferral']['items'] and k in l['current_release_deferral']['critical_path_excluded_items'] for k in ['MON-01','MON-05']))
check('future_activation_gates_parity_mandatory',l['future_live_activation_requirements']==s['future_live_activation_requirements']==n['future_live_activation_requirements']==m['future_live_activation_requirements'] and l['future_live_activation_requirements']['blocks_future_live_activation'] and not l['future_live_activation_requirements']['runtime_proof_complete'])
check('selection_and_terminal_parity',l['current_build_selection']==s['current_build_selection']==n['current_build_selection']==m['current_build_selection'] and l['own04_state_terminal']==s['own04_state_terminal']==n['own04_state_terminal']==m['own04_state_terminal'])
check('gui01_eligible_not_closed_acc03_unresolved',r['GUI-01']['status']==br['GUI-01']['status']=='AWAITING_EVIDENCE' and r['GUI-01']['dependency_evaluation']['current_build_phase']['eligible_for_functional_evidence_work'] and r['GUI-01']['dependency_evaluation']['current_build_phase']['remaining_for_closure']==['ACC-03'] and r['ACC-03']==br['ACC-03'] and r['ACC-03']['status']!='CLOSED')
check('counts_exact',dict(collections.Counter(v['status'] for v in r.values()))=={k:v for k,v in l['metadata']['status_counts'].items() if v})
check('manifest_hashes_exact',all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] and len((ROOT/f['path']).read_bytes())==f['bytes'] for f in m['files']))
check('prior_own04_evidence_untouched',all(subprocess.check_output(['git','show',rev+':'+str(p.relative_to(ROOT))],cwd=ROOT)==p.read_bytes() for p in (ROOT/'docs/tracker/evidence/own04-state-sol').iterdir() if p.is_file()))
check('existing_read_only_tracker_contract_available',read_tracker(ROOT)['status']=='AVAILABLE' and read_tracker(ROOT)['read_only'])
check('no_runtime_or_activation_authority_added',not l['current_release_deferral']['activation_authorized'] and not l['future_live_activation_requirements']['activation_authorized'] and l['current_build_selection']['active_items']==[] and not l['current_build_selection']['next_item_selected'] and all(n['work_package'][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized']))
check('historical_control_snapshot_unchanged',l['metadata']['control_snapshot']==b['metadata']['control_snapshot']==s['control_snapshot']==n['control_snapshot']==m['control_snapshot'])
(EV/'validation.json').write_text(json.dumps({'result':'PASS','review_revision':rev,'checks':checks,'count':len(checks)},indent=2)+'\n')
print('PASS',len(checks),'control/requirement scope checks')
