"""Accounting correction consistency; no runtime/provider operations."""
from pathlib import Path
import sys,json,yaml,hashlib,subprocess,collections
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT))
from trader.dashboard.tracker import read_tracker
EV=Path(__file__).resolve().parent
load=lambda p:yaml.safe_load((ROOT/p).read_text())
l=load('docs/tracker/LUFFY_Product_Tracker_v1.yaml');s=load('STATE.yaml');n=load('NEXT.yaml');m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text());old=json.loads((EV/'control-before.json').read_text())
r={x['id']:x for x in l['items']};b={x['id']:x for x in old['items']};checks=[]
def check(name,ok):
 assert ok,name
 checks.append(name)
check('178 unchanged identities',len(r)==178 and r.keys()==b.keys())
check('only accounting impact rows changed',all(r[k]==b[k] for k in r if k not in ['ACC-01','ACC-03','GUI-01']))
fields=['required_behavior','why_needed','closure_condition','failure_regression_proof','dependencies','parent_ids','related_items']
check('original conditions and edges preserved',all(r[k][f]==b[k][f] for k in r for f in fields))
check('three closures retained only at offline scope',all(r[k]['status']=='CLOSED' for k in ['ACC-01','ACC-03','GUI-01']) and l['accounting_truth_followup']['original_acc01_condition_satisfied'])
canonical=l['accounting_truth_followup']['canonical_acc01_revision'];revision=l['accounting_truth_followup']['consumer_implementation_revision']
check('ACC-01 pinned to canonical correction',r['ACC-01']['resolution_at_commit']==canonical=='88677941115a138258af261e37a6a488ac7a844c')
opus=load('docs/tracker/evidence/acc01-correction-opus/closure.yaml')
check('all four Opus gaps retained exactly',r['ACC-01']['evidence_correction']['gaps_missed_by_earlier_closure']==opus['gaps_missed_by_earlier_closure'] and len(opus['gaps_missed_by_earlier_closure'])==4)
check('consumer correction pinned',r['ACC-03']['resolution_at_commit']==r['GUI-01']['resolution_at_commit']==revision)
for key in ['accounting_truth_followup','acc01_venue_authority_correction','acc03_economics_closure','gui01_overview_closure','current_build_selection']:
 check(key+' parity',l[key]==s[key]==n[key]==m[key])
check('status counts exact',dict(collections.Counter(x['status'] for x in r.values()))==l['metadata']['status_counts'])
check('future runtime and visual gates preserved',l['readiness_gates']==old['readiness_gates'] and l['future_live_activation_requirements']==old['future_live_activation_requirements'] and r['VIS-01']==b['VIS-01'] and r['GUI-01']['runtime_evidence']=='NOT_PROVEN_FOR_THIS_SCREEN')
check('GUI-02 unselected',l['current_build_selection']['next_recommended_item']=='GUI-02' and not l['current_build_selection']['next_item_selected'] and not l['current_build_selection']['active_items'])
check('manifest byte counts and hashes exact',all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] and (ROOT/f['path']).stat().st_size==f['bytes'] for f in m['files']))
source=json.loads((EV/'source-manifest.json').read_text())
check('source hashes pinned to correction commits',all(hashlib.sha256(subprocess.check_output(['git','show',(canonical if p in ['trader/engine/reconcile.py','tests/test_acc01_venue_authority.py'] else revision)+':'+p],cwd=ROOT)).hexdigest()==h for p,h in source['files'].items()))
x=read_tracker(ROOT);check('read-only Tracker contract available',x['status']=='AVAILABLE' and x['read_only'])
(EV/'control-validation.json').write_text(json.dumps({'result':'PASS','checks':checks},indent=2)+'\n')
print('PASS',len(checks),'control checks')
