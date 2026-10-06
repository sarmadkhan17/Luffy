"""Verify this dependency mapping without starting services or reading venues."""
from pathlib import Path
import collections
import hashlib
import json
import sys
import yaml

ROOT = Path(__file__).resolve().parents[4]
EV = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from trader.dashboard.tracker import read_tracker

load = lambda p: yaml.safe_load((ROOT / p).read_text())
l = load('docs/tracker/LUFFY_Product_Tracker_v1.yaml')
s, n = load('STATE.yaml'), load('NEXT.yaml')
m = json.loads((ROOT / 'docs/tracker/bundle-manifest.json').read_text())
b = json.loads((EV / 'control-before.json').read_text())
r = {x['id']: x for x in l['items']}
old = {x['id']: x for x in b['items']}
checks = []

def check(name, value):
    assert value, name
    checks.append(name)

check('178 exact identities', len(r) == 178 and r.keys() == old.keys())
check('only OUT-03 and GUI-02 rows changed', all(r[k] == old[k] for k in r if k not in ['OUT-03', 'GUI-02']))
fields = ['required_behavior', 'why_needed', 'closure_condition', 'failure_regression_proof', 'dependencies', 'parent_ids', 'related_items']
check('original requirements and edges retained', all(r[k][f] == old[k][f] for k in r for f in fields))
check('dependency gating explicit', r['OUT-03']['status'] == r['GUI-02']['status'] == 'BLOCKED' and r['ACC-02']['status'] == 'CLOSED' and r['OWN-01']['status'] == 'EVIDENCE_TO_MAP')
check('unmet prerequisites unchanged', all(r[k] == old[k] and r[k]['status'] == 'EVIDENCE_TO_MAP' for k in ['OUT-01', 'DATA-03', 'WRLD-06']))
check('status counts exact', dict(collections.Counter(x['status'] for x in r.values())) == l['metadata']['status_counts'])
for key in ['current_build_selection', 'out03_replay_dependency_map']:
    check(key + ' four-way parity', l[key] == s[key] == n[key] == m[key])
check('terminal work aligned', s['current_active_work'] == n['work_package'] and n['work_package']['id'] == l['metadata']['current_active_item'] == 'OUT-03' and n['work_package']['status'] == 'BLOCKED')
check('runtime and visual evidence retained', r['GUI-02']['runtime_evidence'] == old['GUI-02']['runtime_evidence'] and r['VIS-02'] == old['VIS-02'] and l['readiness_gates'] == b['readiness_gates'] and l['future_live_activation_requirements'] == b['future_live_activation_requirements'])
check('no successor or runtime selected', not l['current_build_selection']['active_items'] and not l['current_build_selection']['next_item_selected'] and not l['current_build_selection']['runtime_execution_authorized'])
check('bundle hashes and bytes exact', all(hashlib.sha256((ROOT / f['path']).read_bytes()).hexdigest() == f['sha256'] and (ROOT / f['path']).stat().st_size == f['bytes'] for f in m['files']))
source = json.loads((EV / 'source-manifest.json').read_text())
check('inspected source bytes exact', all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in source['files'].items()))
x = read_tracker(ROOT)
check('read-only Tracker available', x['status'] == 'AVAILABLE' and x['read_only'])
(EV / 'control-validation.json').write_text(json.dumps({'result': 'PASS', 'checks': checks}, indent=2) + '\n')
print('PASS', len(checks), 'control checks')
