"""Validate GUI-01 scope closure and control parity without runtime calls."""
from pathlib import Path
import sys, json, yaml, hashlib, collections
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from trader.dashboard.tracker import read_tracker
EV=Path(__file__).resolve().parent
load=lambda p:yaml.safe_load((ROOT/p).read_text())
l=load('docs/tracker/LUFFY_Product_Tracker_v1.yaml');s=load('STATE.yaml');n=load('NEXT.yaml')
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text());old=json.loads((EV/'control-before.json').read_text())
r={x['id']:x for x in l['items']};b={x['id']:x for x in old['items']};checks=[]
def check(name, condition):
    assert condition,name
    checks.append(name)
check('178 identities retained',len(r)==178 and r.keys()==b.keys())
check('only GUI-01 row changed',all(r[k]==b[k] for k in r if k!='GUI-01'))
fields=['required_behavior','why_needed','closure_condition','failure_regression_proof','dependencies','parent_ids','related_items']
check('requirements and edges unchanged',all(r[k][f]==b[k][f] for k in r for f in fields))
check('functional closure separate from runtime and visual',r['GUI-01']['status']=='CLOSED' and r['GUI-01']['runtime_evidence']=='NOT_PROVEN_FOR_THIS_SCREEN' and r['VIS-01']==b['VIS-01'])
check('counts exact',dict(collections.Counter(x['status'] for x in r.values()))==l['metadata']['status_counts'])
for key in ['current_build_selection','gui01_overview_closure']:
    check(key+' parity',l[key]==s[key]==n[key]==m[key])
check('successor unselected',l['current_build_selection']['next_recommended_item']=='GUI-02' and not l['current_build_selection']['next_item_selected'] and not l['current_build_selection']['active_items'])
check('future gates preserved',l['readiness_gates']==old['readiness_gates'] and l['future_live_activation_requirements']==old['future_live_activation_requirements'])
check('manifest hashes exact',all(hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()==f['sha256'] and (ROOT/f['path']).stat().st_size==f['bytes'] for f in m['files']))
x=read_tracker(ROOT);check('read-only tracker available',x['status']=='AVAILABLE' and x['read_only'])
source=json.loads((EV/'source-manifest.json').read_text())
check('GUI source hashes exact',all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in source['files'].items()))
(EV/'control-validation.json').write_text(json.dumps({'result':'PASS','checks':checks},indent=2)+'\n')
print('PASS',len(checks),'control checks')
