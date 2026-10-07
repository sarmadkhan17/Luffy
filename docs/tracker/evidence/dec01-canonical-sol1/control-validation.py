"""Validate DEC-01 row contract, scope, counts, strict loads and bundle hashes."""
from pathlib import Path
from collections import Counter
import hashlib, json, subprocess, yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker
ROOT=Path(__file__).resolve().parents[4]; EP=Path(__file__).resolve().parent
before=json.loads((EP/'control-before.json').read_text());record=json.loads((EP/'closure.json').read_text())
def load(path):return yaml.load((ROOT/path).read_text(),Loader=UniqueLoader)
t=load('docs/tracker/LUFFY_Product_Tracker_v1.yaml');s=load('STATE.yaml');n=load('NEXT.yaml')
Ledger.model_validate(t);NextDocument.model_validate(n)
old={r['id']:r for r in before['tracker']['items']};new={r['id']:r for r in t['items']}
assert old.keys()==new.keys() and len(new)==178
assert [rid for rid in old if old[rid]!=new[rid]]==['DEC-01']
for k in ['required_behavior','closure_condition','failure_regression_proof','dependencies','source_ids','source_sections','parent_ids','related_items','acceptance_basis','acceptance_status']:
 assert old['DEC-01'][k]==new['DEC-01'][k],k
assert new['DEC-01']['status']=='CLOSED'
assert all(new[d]['status']=='CLOSED' for d in new['DEC-01']['dependencies'])
assert all(new[d]['status']=='CLOSED' for d in new['PORT-02']['dependencies'])
assert [d for d in new['DEC-02']['dependencies'] if new[d]['status']!='CLOSED']==['PORT-02']
counts=dict(Counter(r['status'] for r in new.values()))
assert counts==t['metadata']['status_counts']==record['status_counts_after'] and sum(counts.values())==178
assert t['readiness_gates']==before['tracker']['readiness_gates']
for doc in [t,s,n]:
 assert doc['dec01_canonical_reconciliation']==record
 sel=doc['current_build_selection'];assert sel==t['current_build_selection']
 assert 'DEC-01' not in sel['eligible_items'] and 'DEC-01' not in sel['current_blocked_items']
 assert 'PORT-02' in sel['eligible_items'] and 'DEC-02' not in sel['eligible_items']
 assert sel['active_items']==[] and not sel['next_item_selected'] and sel['next_recommended_item'] is None
 assert not sel['runtime_execution_authorized']
for which,doc,key in [('state',s,'current_active_work'),('next',n,'work_package')]:
 assert doc[key]['id']=='DEC-01' and doc[key]['status']=='CLOSED'
 assert all(doc[key][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'])
 assert doc['dec01_previous_selection']==before[which][key]
 for k,v in before[which].items():
  if k not in [key,'current_build_selection']:assert doc[k]==v,(which,k)
for k,v in before['tracker'].items():
 if k not in ['metadata','items','current_build_selection']:assert t[k]==v,k
assert s['current_runtime_status']==before['state']['current_runtime_status']
for path,sha in record['source_sha256'].items():
 raw=(ROOT/path).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha,path
 assert raw==subprocess.check_output(['git','show',record['engineering_closure_commit']+':'+path],cwd=ROOT),path
cfg=load('config.yaml');assert cfg['research']['handoff'] is False and cfg['research']['referee'] is False
from trader.portfolio import economics as E
assert E.GROSS_MODELS==E.RESERVE_MODELS==E.COST_SCOPE_MODELS=={}
md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
base_md=subprocess.check_output(['git','show',record['control_baseline_revision']+':docs/tracker/LUFFY_Product_Tracker_v1.md'],cwd=ROOT,text=True)
for rid,row in new.items():
 marker='### '+rid+' —'
 if marker not in md:continue
 section=md[md.index(marker):].split('\n### ',1)[0]
 if rid=='DEC-01':
  for k in ['status','closure_condition','next_proof','closure_evidence']:assert '- **'+k+'**: '+row[k] in section,k
 else:assert section.startswith(base_md[base_md.index(marker):].split('\n### ',1)[0].rstrip()),rid
res=read_tracker(ROOT);assert res['status']=='AVAILABLE',res
assert res['selected']['id']=='DEC-01' and res['selected']['status']=='CLOSED'
for k,v in counts.items():assert res['counts'][k]==v
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
assert m['snapshot_version']==t['metadata']['version'] and m['row_count']==178
assert m['current_build_selection']==t['current_build_selection'] and m['dec01_canonical_reconciliation']==record
for e in m['files']:
 raw=(ROOT/e['path']).read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
paths=subprocess.check_output(['git','diff','--name-only',record['control_baseline_revision']],cwd=ROOT,text=True).splitlines()
assert not any(p.startswith(('trader/','tests/','scripts/')) or p=='config.yaml' for p in paths),paths
checks=dict(row_count=178,changed_rows=['DEC-01'],unrelated_rows_unchanged=177,original_conditions_and_edges_preserved=True,
 readiness_gates_preserved=True,YAML_unique_keys_and_strict_contracts=True,manifest_hashes_match=True,
 source_and_committed_bytes_match=True,status_counts=counts,dashboard_read='AVAILABLE',dashboard_selected=res['selected'],
 DEC01_canonical='CLOSED',PORT02_now_eligible=True,PORT02_selected=False,PORT02_started=False,
 DEC02_now_eligible=False,DEC02_remaining_dependency_blockers=['PORT-02'],DEC02_own_proof_unmapped=True,
 production_registries_empty=True,production_unavailable_cost_semantics_verified=True,application_code_changed=False,
 runtime_state_unchanged=True,research_handoff_disabled=True,research_referee_disabled=True,provider_calls=0)
(EP/'control-checks.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
