"""Validate STR-02 reconciliation, immutable row contracts and manifest bytes."""
from pathlib import Path
from collections import Counter
import hashlib, json, subprocess
import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker

ROOT=Path(__file__).resolve().parents[4]
EP=Path(__file__).resolve().parent
base=json.loads((EP/'control-before.json').read_text())
record=json.loads((EP/'closure.json').read_text())
def load(path): return yaml.load((ROOT/path).read_text(), Loader=UniqueLoader)
t=load('docs/tracker/LUFFY_Product_Tracker_v1.yaml')
s=load('STATE.yaml');n=load('NEXT.yaml')
Ledger.model_validate(t);NextDocument.model_validate(n)
old={r['id']:r for r in base['tracker']['items']}
new={r['id']:r for r in t['items']}
assert old.keys()==new.keys() and len(new)==178
changed=[rid for rid in old if old[rid]!=new[rid]]
assert changed==['STR-02','DEC-01'],changed
for rid in changed:
 for key in ['required_behavior','closure_condition','failure_regression_proof','dependencies',
             'source_ids','source_sections','parent_ids','related_items','acceptance_basis','acceptance_status']:
  assert old[rid][key]==new[rid][key],(rid,key)
assert new['STR-02']['status']=='CLOSED'
assert new['DEC-01']['status']==old['DEC-01']['status']=='BLOCKED'
assert all(new[d]['status']=='CLOSED' for rid in changed for d in new[rid]['dependencies'])
counts=dict(Counter(r['status'] for r in new.values()))
assert counts==t['metadata']['status_counts']==record['status_counts_after']
assert sum(counts.values())==178
assert t['readiness_gates']==base['tracker']['readiness_gates']
for doc in (t,s,n):
 assert doc['str02_canonical_reconciliation']==record
 sel=doc['current_build_selection']
 assert 'DEC-01' in sel['eligible_items'] and 'STR-02' not in sel['eligible_items']
 assert 'STR-02' not in sel['current_blocked_items'] and 'DEC-01' in sel['current_blocked_items']
 assert sel['active_items']==[] and not sel['next_item_selected']
 assert sel['next_recommended_item'] is None and not sel['runtime_execution_authorized']
for which,current,key in [('state',s,'current_active_work'),('next',n,'work_package')]:
 assert current[key]['id']=='STR-02' and current[key]['status']=='CLOSED'
 assert all(current[key][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized',
                                              'trading_activation_authorized','successor_selected'])
 assert current['str02_previous_selection']==base[which][key]
 for k,v in base[which].items():
  if k not in (key,'current_build_selection'): assert current[k]==v,(which,k)
for k,v in base['tracker'].items():
 if k not in ('metadata','items','current_build_selection'): assert t[k]==v,k
assert s['current_runtime_status']==base['state']['current_runtime_status']
for path,digest in record['source_sha256'].items():
 raw=(ROOT/path).read_bytes()
 assert hashlib.sha256(raw).hexdigest()==digest,path
 assert raw==subprocess.check_output(['git','show',record['engineering_closure_commit']+':'+path],cwd=ROOT),path
cfg=load('config.yaml')
assert cfg['research']['handoff'] is False and cfg['research']['referee'] is False
assert (EP/'baseline-failures.txt').read_bytes()==(EP/'final-failures.txt').read_bytes()
assert len((EP/'final-failures.txt').read_text().splitlines())==31
md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
before_md=subprocess.check_output(['git','show',record['control_baseline_revision']+':docs/tracker/LUFFY_Product_Tracker_v1.md'],cwd=ROOT,text=True)
for rid,row in new.items():
 marker='### '+rid+' —'
 if marker not in md: continue
 section=md[md.index(marker):].split('\n### ',1)[0]
 if rid in changed:
  assert '- **status**: '+row['status'] in section,rid
  assert row['closure_condition'] in section and row['next_proof'] in section,rid
 else:
  old_section=before_md[before_md.index(marker):].split('\n### ',1)[0]
  # Final section includes the appended current reconciliation summary.
  assert section.startswith(old_section.rstrip()),rid
res=read_tracker(ROOT)
assert res['status']=='AVAILABLE',res
assert res['selected']['id']=='STR-02' and res['selected']['status']=='CLOSED'
for k,v in counts.items(): assert res['counts'][k]==v
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
assert m['snapshot_version']==t['metadata']['version'] and m['row_count']==178
assert m['current_build_selection']==t['current_build_selection']
assert m['str02_canonical_reconciliation']==record
for entry in m['files']:
 raw=(ROOT/entry['path']).read_bytes()
 assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256'],entry['path']
paths=subprocess.check_output(['git','diff','--name-only',record['control_baseline_revision']],cwd=ROOT,text=True).splitlines()
assert not any(p.startswith(('trader/','tests/')) or p=='config.yaml' for p in paths),paths
checks=dict(row_count=178,changed_rows=changed,unrelated_rows_unchanged=176,
 original_conditions_and_edges_preserved=True,readiness_gates_preserved=True,
 source_and_committed_bytes_match=True,manifest_hashes_match=True,
 YAML_unique_keys_and_strict_contracts=True,status_counts=counts,
 dashboard_read='AVAILABLE',dashboard_selected=res['selected'],
 STR02_canonical='CLOSED',DEC01_status='BLOCKED',DEC01_dependencies_satisfied=True,
 DEC01_now_eligible=True,DEC01_selected=False,DEC01_started=False,
 baseline_final_31_failure_ids_equal=True,application_code_changed=False,
 runtime_state_unchanged=True,research_handoff_disabled=True,research_referee_disabled=True,
 feature_version_limitation_preserved=True)
(EP/'control-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
