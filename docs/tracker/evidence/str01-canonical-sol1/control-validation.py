from pathlib import Path
from collections import Counter
import hashlib,json,subprocess,yaml
from trader.dashboard.tracker import read_tracker,UniqueLoader,Ledger,NextDocument
ROOT=Path('/mnt/luffy-data/luffy/production')
EVID='docs/tracker/evidence/str01-canonical-sol1'
base=json.loads((ROOT/EVID/'control-before.json').read_text())

def load(p): return yaml.load((ROOT/p).read_text(),Loader=UniqueLoader)
t=load('docs/tracker/LUFFY_Product_Tracker_v1.yaml'); state=load('STATE.yaml'); nex=load('NEXT.yaml')
Ledger.model_validate(t); NextDocument.model_validate(nex)
old={r['id']:r for r in base['tracker']['items']}; new={r['id']:r for r in t['items']}
assert len(old)==len(new)==178 and old.keys()==new.keys()
changed=[rid for rid in old if old[rid]!=new[rid]]
assert changed==['STR-01'],changed
for k in ['required_behavior','closure_condition','failure_regression_proof','dependencies','source_ids','source_sections','parent_ids','related_items','acceptance_basis','acceptance_status']:
 assert old['STR-01'][k]==new['STR-01'][k],k
assert old['STR-02']==new['STR-02'] and old['DEC-01']==new['DEC-01']
assert new['STR-01']['status']=='CLOSED'
assert all(new[d]['status']=='CLOSED' for d in new['STR-02']['dependencies'])
assert new['STR-02']['status']=='BLOCKED' and new['DEC-01']['status']=='BLOCKED'
summary=json.loads((ROOT/EVID/'closure.json').read_text())
counts=dict(Counter(r['status'] for r in new.values()))
assert counts==summary['status_counts_after']==t['metadata']['status_counts']
assert sum(counts.values())==178
for doc in (t,state,nex):
 assert doc['str01_canonical_reconciliation']==summary
 assert 'STR-02' in doc['current_build_selection']['eligible_items']
 assert not doc['current_build_selection']['next_item_selected']
for sel in [state['current_active_work'],nex['work_package']]:
 assert sel['id']=='STR-01' and sel['status']=='CLOSED'
 assert all(sel[k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'])
assert state['current_runtime_status']==base['state']['current_runtime_status']
for k,v in base['tracker'].items():
 if k not in ('metadata','items','current_build_selection'): assert t[k]==v,k
for which,current in [('state',state),('next',nex)]:
 for k,v in base[which].items():
  if k not in ('current_active_work','work_package','current_build_selection'): assert current[k]==v,k
assert t['readiness_gates']==base['tracker']['readiness_gates']
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
assert m['str01_canonical_reconciliation']==summary and m['snapshot_version']==t['metadata']['version']
assert m['row_count']==178 and m['current_build_selection']==t['current_build_selection']
for entry in m['files']:
 raw=(ROOT/entry['path']).read_bytes()
 assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256'],entry['path']
for p,digest in summary['source_sha256'].items():
 raw=(ROOT/p).read_bytes(); assert hashlib.sha256(raw).hexdigest()==digest,p
 assert raw==subprocess.check_output(['git','show',summary['engineering_closure_commit']+':'+p],cwd=ROOT),p
cfg=load('config.yaml'); assert cfg['research']['handoff'] is False and cfg['research']['referee'] is False
res=read_tracker(ROOT)
assert res['status']=='AVAILABLE' and res['selected']['id']=='STR-01' and res['selected']['status']=='CLOSED'
assert res['source']['sha256']==hashlib.sha256((ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.yaml').read_bytes()).hexdigest()
for k,v in counts.items(): assert res['counts'][k]==v,k
md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text(); start=md.index('### STR-01 —'); end=md.index('### STR-02 —',start)
assert '- **status**: CLOSED' in md[start:end] and new['STR-01']['closure_condition'] in md[start:end]
before_md=subprocess.check_output(['git','show',summary['engineering_closure_commit']+':docs/tracker/LUFFY_Product_Tracker_v1.md'],cwd=ROOT,text=True)
assert md[md.index('### STR-02 —'):md.index('### STR-03 —')]==before_md[before_md.index('### STR-02 —'):before_md.index('### STR-03 —')]
checks=dict(row_count=178,unchanged_unrelated_rows=177,changed_rows=changed,original_condition_and_edges_preserved=True,readiness_gates_preserved=True,STR02_and_DEC01_rows_unchanged=True,STR02_dependencies_satisfied=True,STR02_unselected=True,DEC01_status=new['DEC-01']['status'],status_counts=counts,source_and_committed_bytes_match=True,manifest_hashes_match=True,YAML_unique_keys_and_strict_contracts=True,dashboard_read='AVAILABLE',dashboard_selected=res['selected'],runtime_status_unchanged=True,research_handoff_disabled=True,research_referee_disabled=True,application_code_changed=False)
(ROOT/EVID/'control-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
