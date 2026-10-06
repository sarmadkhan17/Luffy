"""Reproduce control checks without runtime or external calls."""
from pathlib import Path
import collections
import hashlib
import json
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from trader.dashboard.tracker import read_tracker

EV = Path(__file__).resolve().parent
def load(path):
    return yaml.safe_load((ROOT / path).read_text())

ledger = load('docs/tracker/LUFFY_Product_Tracker_v1.yaml')
base = yaml.safe_load(subprocess.check_output(
    ['git', 'show', '29d093d:docs/tracker/LUFFY_Product_Tracker_v1.yaml'], cwd=ROOT))
state, next_ = load('STATE.yaml'), load('NEXT.yaml')
manifest = json.loads((ROOT / 'docs/tracker/bundle-manifest.json').read_text())
rows = {r['id']: r for r in ledger['items']}
old = {r['id']: r for r in base['items']}
checks = []
def check(name, value):
    assert value, name
    checks.append(name)

check('178 exact identities preserved', len(rows) == 178 and rows.keys() == old.keys())
fields = ['required_behavior', 'why_needed', 'closure_condition', 'failure_regression_proof',
          'dependencies', 'parent_ids', 'related_items']
check('requirements and dependency edges unchanged', all(
    rows[k][f] == old[k][f] for k in rows for f in fields))
check('only accounting and GUI-01 evidence rows changed', all(rows[k] == old[k] for k in rows
      if k not in ['ACC-01', 'ACC-02', 'ACC-03', 'GUI-01']))
check('dependency closures satisfied', all(rows[k]['status'] == 'CLOSED' for k in
      ['DATA-01', 'EXE-01', 'ACC-01', 'ACC-02', 'ACC-03', 'OWN-03', 'OWN-04']))
check('GUI-01 remains awaiting evidence', rows['GUI-01']['status'] == 'AWAITING_EVIDENCE'
      and not rows['GUI-01']['dependency_evaluation']['current_build_phase']['remaining_for_closure'])
check('counts exact', dict(collections.Counter(r['status'] for r in rows.values()))
      == ledger['metadata']['status_counts'])
for key in ['current_build_selection', 'acc03_economics_closure']:
    check(key + ' parity', ledger[key] == state[key] == next_[key] == manifest[key])
check('no successor selected', not ledger['current_build_selection']['next_item_selected']
      and not ledger['current_build_selection']['active_items'])
check('future activation gates preserved', ledger['readiness_gates'] == base['readiness_gates']
      and ledger['future_live_activation_requirements'] == base['future_live_activation_requirements'])
check('manifest hashes exact', all(hashlib.sha256((ROOT / f['path']).read_bytes()).hexdigest()
      == f['sha256'] and (ROOT / f['path']).stat().st_size == f['bytes'] for f in manifest['files']))
source = json.loads((EV / 'source-manifest.json').read_text())
check('source hashes match pinned implementation', all(hashlib.sha256(subprocess.check_output(
      ['git', 'show', source['revision'] + ':' + p], cwd=ROOT)).hexdigest() == h
      for p, h in source['files'].items()))
read = read_tracker(ROOT)
check('read-only tracker available', read['status'] == 'AVAILABLE' and read['read_only'])
(EV / 'control-validation.json').write_text(json.dumps(
    dict(result='PASS', baseline='29d093d', checks=checks), indent=2) + '\n')
print('PASS', len(checks), 'control checks')
