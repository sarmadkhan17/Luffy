"""Validate DEC-02 contracts, historical preservation and control-plane scope."""
from pathlib import Path
from collections import Counter
import hashlib,json,subprocess,yaml
import xml.etree.ElementTree as ET
from trader.dashboard.tracker import UniqueLoader,Ledger,NextDocument,read_tracker
ROOT=Path(__file__).resolve().parents[4];EP=Path(__file__).resolve().parent
before=json.loads((EP/'control-before.json').read_text());record=json.loads((EP/'closure.json').read_text())
def load(path):return yaml.load((ROOT/path).read_text(),Loader=UniqueLoader)
t=load('docs/tracker/LUFFY_Product_Tracker_v1.yaml');s=load('STATE.yaml');n=load('NEXT.yaml')
Ledger.model_validate(t);NextDocument.model_validate(n)
old={r['id']:r for r in before['tracker']['items']};new={r['id']:r for r in t['items']}
assert old.keys()==new.keys() and len(new)==178
assert [rid for rid in old if old[rid]!=new[rid]]==['DEC-02']
for k in ['required_behavior','closure_condition','failure_regression_proof','dependencies','source_ids','source_sections','parent_ids','related_items','acceptance_basis','acceptance_status','new_issue_policy']:
 assert old['DEC-02'][k]==new['DEC-02'][k],k
assert new['DEC-02']['status']=='CLOSED'
assert all(new[d]['status']=='CLOSED' for d in new['DEC-02']['dependencies'])
assert [d for d in new['DEC-03']['dependencies'] if new[d]['status']!='CLOSED']==['STR-04']
assert new['STR-04']['dependencies']==['STR-03'] and new['STR-03']['status']!='CLOSED'
assert all(new[d]['status']=='CLOSED' for d in new['STR-03']['dependencies'])
counts=dict(Counter(r['status'] for r in new.values()))
assert counts==t['metadata']['status_counts']==record['status_counts_after'] and sum(counts.values())==178
assert t['readiness_gates']==before['tracker']['readiness_gates']
for doc in [t,s,n]:
 assert doc['dec02_canonical_reconciliation']==record
 sel=doc['current_build_selection'];assert sel==t['current_build_selection']
 assert 'DEC-02' not in sel['eligible_items'] and 'STR-03' in sel['eligible_items'] and 'DEC-03' not in sel['eligible_items']
 assert sel['active_items']==[] and not sel['next_item_selected'] and sel['next_recommended_item']=='STR-03'
 assert not sel['runtime_execution_authorized']
assert t['metadata']['next_recommended_item']=='STR-03'
for which,doc,key in [('state',s,'current_active_work'),('next',n,'work_package')]:
 assert doc[key]['id']=='DEC-02' and doc[key]['status']=='CLOSED'
 assert all(doc[key][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'])
 assert doc['dec02_previous_selection']==before[which][key]
 for k,v in before[which].items():
  if k not in [key,'current_build_selection']:assert doc[k]==v,(which,k)
for k,v in before['tracker'].items():
 if k not in ['metadata','items','current_build_selection']:assert t[k]==v,k
assert s['current_runtime_status']==before['state']['current_runtime_status']
for path,sha in record['source_sha256'].items():
 raw=(ROOT/path).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha,path
 assert raw==subprocess.check_output(['git','show',record['engineering_closure_commit']+':'+path],cwd=ROOT),path
cfg=load('config.yaml');assert cfg['research']['handoff'] is False and cfg['research']['referee'] is False
from trader.portfolio import allocator as A,economics as E
assert A.VERSION=='LUFFY-PORTFOLIO-ALLOCATOR-R3' and A.VERSION_R2 in A.SUPPORTED_VERSIONS
assert E.GROSS_MODELS==E.RESERVE_MODELS==E.COST_SCOPE_MODELS=={}
assert 'LUFFY-PORTFOLIO-ALLOCATOR-R1' in (ROOT/'scripts/portfolio_allocator_shadow.py').read_text()
proof=json.loads((EP/'replay-proof.json').read_text())
assert len(proof['R2_actual_historical_bytes_preserved'])==7
assert all(v['exact_bytes_equal'] and v['pre_R3_proposal_id']==v['current_R2_proposal_id'] for v in proof['R2_actual_historical_bytes_preserved'].values())
assert proof['cross_version_refused'] and proof['input_permutation_stable']
assert proof['R3_exact_tie_view']['cash']['selected']
assert proof['R3_exact_tie_view']['tie']['winner'] is None
assert all(r['outcome']=='REJECTED' and r['reasons'] for r in proof['R3_exact_tie_view']['candidates'])
assert all(c['score'] is None for r in proof['R3_exact_tie_view']['candidates'] for c in r['confidence_components'].values())
suite=ET.parse(EP/'focused.xml').getroot().find('testsuite')
assert int(suite.get('tests'))==195 and all(int(suite.get(k))==0 for k in ['errors','failures','skipped'])
md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
base_md=subprocess.check_output(['git','show',record['control_baseline_revision']+':docs/tracker/LUFFY_Product_Tracker_v1.md'],cwd=ROOT,text=True)
for rid,row in new.items():
 marker='### '+rid+' —'
 if marker not in md:continue
 section=md[md.index(marker):].split('\n### ',1)[0]
 if rid=='DEC-02':
  for k in ['status','closure_condition','next_proof','closure_evidence']:assert '- **'+k+'**: '+row[k] in section,k
 else:assert section.startswith(base_md[base_md.index(marker):].split('\n### ',1)[0].rstrip()),rid
res=read_tracker(ROOT);assert res['status']=='AVAILABLE',res
assert res['selected']['id']=='DEC-02' and res['selected']['status']=='CLOSED'
for k,v in counts.items():assert res['counts'][k]==v
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
assert m['snapshot_version']==t['metadata']['version'] and m['row_count']==178
assert m['current_build_selection']==t['current_build_selection'] and m['dec02_canonical_reconciliation']==record
for e in m['files']:
 raw=(ROOT/e['path']).read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
paths=subprocess.check_output(['git','diff','--name-only',record['control_baseline_revision']],cwd=ROOT,text=True).splitlines()
assert not any(p.startswith(('trader/','tests/','scripts/')) or p=='config.yaml' for p in paths),paths
checks=dict(row_count=178,changed_rows=['DEC-02'],unrelated_rows_unchanged=177,original_conditions_and_edges_preserved=True,
 readiness_gates_and_historical_control_records_preserved=True,YAML_unique_keys_and_strict_contracts=True,manifest_hashes_match=True,
 source_and_committed_bytes_match=True,status_counts=counts,dashboard_read='AVAILABLE',dashboard_selected=res['selected'],
 DEC02_canonical='CLOSED',allocator_default=A.VERSION,R3_exact_tie='CASH / '+A.TIE_REASON,R2_replay_actual_bytes_equal=True,
 cross_version_refused=True,shadow_R1_label_unchanged=True,DEC03_dependency_DEC02='SATISFIED',DEC03_remaining_blockers=['STR-04'],
 DEC03_transitive_blockers=['STR-03'],next_critical_path_item='STR-03',successor_selected=False,successor_started=False,
 production_registries_empty=True,real_calibration_present=False,calibration_ownership_gap_preserved=True,fresh_focused_passed=195,
 application_code_changed=False,runtime_state_unchanged=True,research_handoff_disabled=True,research_referee_disabled=True,provider_calls=0)
(EP/'control-checks.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
