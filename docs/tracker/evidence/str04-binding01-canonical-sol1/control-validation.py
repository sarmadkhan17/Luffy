"""Validate STR-04-only canonical reconciliation and immutable evidence identity."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker
from trader.strategy import factory_handoff as F

ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
BASE = '9814fddce8f655ce73bd79050b536e6668dba55d'
TP = 'docs/tracker/LUFFY_Product_Tracker_v1.yaml'


def load(path):
    return yaml.load((ROOT/path).read_text(),Loader=UniqueLoader)


def before(path):
    return yaml.load(subprocess.check_output(['git','show',BASE+':'+path],cwd=ROOT,text=True),Loader=UniqueLoader)


def committed(path):
    assert (ROOT/path).read_bytes()==subprocess.check_output(['git','show',BASE+':'+path],cwd=ROOT),path


t = load(TP); old_t = before(TP)
s = load('STATE.yaml'); n = load('NEXT.yaml')
rec = json.loads((EP/'closure.json').read_text())
Ledger.model_validate(t); NextDocument.model_validate(n)
old = {r['id']:r for r in old_t['items']}; new = {r['id']:r for r in t['items']}
assert len(new)==178 and old.keys()==new.keys()
assert [rid for rid in old if old[rid]!=new[rid]]==['STR-04']
for key in ['required_behavior','closure_condition','failure_regression_proof','dependencies',
            'source_ids','source_sections','parent_ids','related_items','acceptance_basis',
            'acceptance_status','new_issue_policy','work_authorized']:
    assert old['STR-04'][key]==new['STR-04'][key],key
assert old['STR-04']['status']=='BLOCKED' and new['STR-04']['status']=='CLOSED'
assert new['STR-03']==old['STR-03'] and new['STR-03']['status']=='CLOSED'
assert new['STR-04']['dependencies']==['STR-03']
assert rec['canonical_status']=='CLOSED' and rec['closing_condition_satisfied']
assert rec['engineering_commit']==BASE and rec['application_code_changed'] is False
assert rec['allowed_transitions']=={k:sorted(v) for k,v in F.GOVERNOR_ALLOWED.items()}
assert rec['projection']==F.PROJECTION
assert t['readiness_gates']==old_t['readiness_gates']
counts=dict(Counter(r['status'] for r in new.values()))
assert counts==t['metadata']['status_counts']==rec['status_counts_after']
assert counts['CLOSED']==63 and counts['BLOCKED']==5 and sum(counts.values())==178
for key,value in old_t.items():
    if key not in ['metadata','items','current_build_selection','str04_canonical_reconciliation']:
        assert t[key]==value,key
assert t['str04_binding01_prior_canonical_reconciliation']==old_t['str04_canonical_reconciliation']
assert t['str04_canonical_reconciliation']==rec
sel=t['current_build_selection']
assert sel['last_completed_item']=='STR-04' and sel['last_terminal_result']=='CLOSED_ARCHITECTURE_OFFLINE'
assert sel['next_recommended_item']=='DEC-03' and 'DEC-03' in sel['eligible_items']
assert 'STR-04' not in sel['current_blocked_items']
assert not sel['active_items'] and not sel['next_item_selected'] and not sel['runtime_execution_authorized']
assert all(new[d]['status']=='CLOSED' for d in new['DEC-03']['dependencies'])
assert new['DEC-03']==old['DEC-03'] and new['DEC-03']['status']=='EVIDENCE_TO_MAP'
assert new['GUI-04']==old['GUI-04'] and new['GUI-04']['status']=='AWAITING_EVIDENCE'
assert all(new[d]['status']=='CLOSED' for d in new['GUI-04']['dependencies'])
assert new['REL-01']==old['REL-01'] and new['REL-01']['status']=='AWAITING_OWNER'
assert [d for d in new['REL-01']['dependencies'] if new[d]['status']!='CLOSED']==['RISK-03','EXE-06','LRN-06']
for doc,path,key in [(s,'STATE.yaml','current_active_work'),(n,'NEXT.yaml','work_package')]:
    prior=before(path)
    assert doc['current_build_selection']==sel and doc['str04_canonical_reconciliation']==rec
    assert doc['str04_binding01_previous_selection']==prior[key]
    assert doc['str04_binding01_prior_canonical_reconciliation']==prior['str04_canonical_reconciliation']
    for k,v in prior.items():
        if k not in [key,'current_build_selection','str04_canonical_reconciliation','updated_at']:
            assert doc[k]==v,(path,k)
    assert doc[key]['id']=='STR-04' and doc[key]['status']=='CLOSED'
    assert doc[key]['implementation_complete'] and doc[key]['closing_condition_satisfied']
    assert all(doc[key][x] is False for x in ['runtime_execution_authorized','provider_enablement_authorized',
                                           'trading_activation_authorized','successor_selected'])
assert not rec['DEC03_started'] and not rec['DEC03_selected']
for path,sha in rec['source_sha256'].items():
    committed(path)
    assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha,path
for path in rec['engineering_evidence_paths']: committed(path)
for path in ['config.yaml','SDD.md','trader/dashboard/glacier_truth.py',
             'tests/test_strategy_factory_handoff.py']:
    committed(path)
proof=json.loads((EP/'five-state-binding-proof.json').read_text())
assert proof['ordinary_canonical_writer'] and not proof['privileged_corruption_used']
assert {e['to_state'] for e in proof['events']}=={'ACTIVE','REACTIVATED','PAUSED','DEGRADED','RETIRED'}
for event in proof['events']:
    assert event['schema']=='strategy-governor-event.v2'
    assert dict(event['binding'],version_id=event['version_id'],owner_decision_id=event['owner_decision_id'])==proof['expected_binding']
suite=ET.parse(EP/'dedicated.xml').getroot().find('testsuite')
assert int(suite.get('tests'))==109 and all(int(suite.get(k))==0 for k in ['errors','failures','skipped'])
classes=Counter(c.get('classname') for c in suite.findall('testcase'))
assert classes['tests.test_str04_historical_binding']==38
assert classes['tests.test_str04_governor_lifecycle']==26
assert classes['tests.test_str03_approval_binding']==44
baseline=ET.parse(EP/'glacier-baseline.xml').getroot().find('testsuite')
assert int(baseline.get('tests'))==2 and int(baseline.get('failures'))==2
assert all('trader/dashboard/glacier_truth.py' in c.find('failure').text for c in baseline.findall('testcase'))
assert rec['validation']['counts_overlap_do_not_sum'] and not rec['validation']['full_suite_green_claim']
md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
old_md=subprocess.check_output(['git','show',BASE+':docs/tracker/LUFFY_Product_Tracker_v1.md'],cwd=ROOT,text=True)
for rid,row in new.items():
    marker='### '+rid+' —'
    if marker not in old_md: continue
    part=md[md.index(marker):].split('\n### ',1)[0]
    if rid=='STR-04':
        for key in ['status','closure_condition','next_proof','closure_evidence','implementation_commit']:
            assert '- **'+key+'**: '+row[key] in part,key
    else:
        assert part.startswith(old_md[old_md.index(marker):].split('\n### ',1)[0].rstrip()),rid
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
assert m['snapshot_version']==t['metadata']['version'] and m['row_count']==178
assert m['status_counts']==counts and m['str04_canonical_reconciliation']==rec and m['current_build_selection']==sel
assert len({f['path'] for f in m['files']})==len(m['files'])
for entry in m['files']:
    raw=(ROOT/entry['path']).read_bytes()
    assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256'],entry['path']
view=read_tracker(ROOT)
assert view['status']=='AVAILABLE' and view['selected']['id']=='STR-04' and view['selected']['status']=='CLOSED'
assert all(view['counts'][k]==v for k,v in counts.items())
cfg=load('config.yaml'); assert not cfg['research']['handoff'] and not cfg['research']['referee']
checks=dict(STR04_canonical='CLOSED',row_count=178,status_counts=counts,changed_rows=['STR-04'],
    unrelated_rows_unchanged=177,original_conditions_and_edges_preserved=True,strict_YAML_contracts=True,
    source_committed_bytes_and_manifest_hashes_match=True,dashboard_read='AVAILABLE',
    fresh_dedicated_passed=109,all_five_event_bindings_verified=True,STR03_unchanged_CLOSED=True,
    DEC03_dependencies_satisfied=True,DEC03_now_eligible=True,DEC03_selected=False,DEC03_started=False,
    GUI04_row_unchanged=True,GUI04_dependencies_satisfied=True,GUI04_functional_runtime_closure=False,
    REL01_row_unchanged=True,REL01_remaining_direct_dependencies=['RISK-03','EXE-06','LRN-06'],
    Glacier_static_guard_unchanged_unrelated=True,application_code_changed=False,
    runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False)
print(json.dumps(checks,indent=2))
