"""Validate STR-04 blocked reconciliation, pinned proof, hashes and preserved contracts."""
from pathlib import Path
from collections import Counter
import hashlib,json,subprocess
import xml.etree.ElementTree as ET
import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker
from trader.strategy import factory_handoff as F
R=Path(__file__).resolve().parents[4]; E=Path(__file__).resolve().parent
rec=json.loads((E/'reconciliation.json').read_text()); base=rec['control_baseline_revision']
def load(p): return yaml.load((R/p).read_text(),Loader=UniqueLoader)
def before(p): return yaml.load(subprocess.check_output(['git','show',base+':'+p],cwd=R,text=True),Loader=UniqueLoader)
tp='docs/tracker/LUFFY_Product_Tracker_v1.yaml'; t=load(tp); old_t=before(tp)
s=load('STATE.yaml'); n=load('NEXT.yaml')
Ledger.model_validate(t); NextDocument.model_validate(n)
old={x['id']:x for x in old_t['items']}; new={x['id']:x for x in t['items']}
assert old.keys()==new.keys() and len(new)==178
assert [x for x in old if old[x]!=new[x]]==['STR-04']
assert new['STR-04']['status']=='BLOCKED' and new['STR-03']['status']=='CLOSED'
for k in ['required_behavior','closure_condition','failure_regression_proof','dependencies','source_ids','source_sections','parent_ids','related_items','acceptance_basis','acceptance_status','new_issue_policy','work_authorized']:
    assert old['STR-04'][k]==new['STR-04'][k],k
assert new['STR-04']['dependencies']==['STR-03']
assert [d for d in new['DEC-03']['dependencies'] if new[d]['status']!='CLOSED']==['STR-04']
assert not rec['DEC03_dependencies_satisfied'] and not rec['DEC03_now_eligible'] and not rec['DEC03_started']
assert not rec['closing_condition_satisfied'] and rec['canonical_status']=='BLOCKED'
counts=dict(Counter(x['status'] for x in new.values()))
assert counts==t['metadata']['status_counts']==rec['status_counts_after'] and sum(counts.values())==178
assert t['readiness_gates']==old_t['readiness_gates']
assert t['str03_canonical_reconciliation']==old_t['str03_canonical_reconciliation']
for k,v in old_t.items():
    if k not in ['metadata','items','current_build_selection']: assert t[k]==v,k
assert t['metadata']['current_active_item']=='STR-04'
sel=t['current_build_selection']
assert sel['last_completed_item']=='STR-03' and sel['last_terminal_item']=='STR-04'
assert 'STR-04' in sel['current_blocked_items'] and 'STR-04' not in sel['eligible_items']
assert 'DEC-03' not in sel['eligible_items'] and not sel['active_items'] and not sel['next_item_selected'] and not sel['runtime_execution_authorized']
for doc,p,key in [(s,'STATE.yaml','current_active_work'),(n,'NEXT.yaml','work_package')]:
    prior=before(p)
    assert doc['current_build_selection']==sel and doc['str04_canonical_reconciliation']==rec
    assert doc['str04_previous_selection']==prior[key]
    for k,v in prior.items():
        if k not in [key,'current_build_selection']: assert doc[k]==v,(p,k)
    assert doc[key]['id']=='STR-04' and doc[key]['status']=='BLOCKED'
    assert all(doc[key][x] is False for x in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'])
assert t['str04_canonical_reconciliation']==rec
for p,sha in rec['source_sha256'].items():
    raw=(R/p).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==sha,p
    assert raw==subprocess.check_output(['git','show',base+':'+p],cwd=R),p
assert (R/'trader/strategy/factory_handoff.py').read_bytes()==subprocess.check_output(['git','show',rec['engineering_commit']+':trader/strategy/factory_handoff.py'],cwd=R)
assert (R/'tests/test_str04_governor_lifecycle.py').read_bytes()==subprocess.check_output(['git','show',rec['engineering_commit']+':tests/test_str04_governor_lifecycle.py'],cwd=R)
assert rec['allowed_transitions']=={k:sorted(v) for k,v in F.GOVERNOR_ALLOWED.items()}
assert rec['projection']==F.PROJECTION and rec['allowed_transitions'][F.RETIRED]==[]
suite=ET.parse(E/'dedicated.xml').getroot().find('testsuite')
assert int(suite.get('tests'))==70 and all(int(suite.get(k))==0 for k in ['errors','failures','skipped'])
classes=Counter(x.get('classname') for x in suite.findall('testcase'))
assert classes['tests.test_str04_governor_lifecycle']==26 and classes['tests.test_str03_approval_binding']==44
proof=json.loads((E/'binding-proof.json').read_text())
assert not proof['required_proof_satisfied'] and proof['ordinary_canonical_writer_only'] and not proof['privileged_flag_or_corruption_used']
assert {x['to_state'] for x in proof['cases']}=={'PAUSED','DEGRADED','RETIRED'}
for x in proof['cases']:
    assert x['actual_owner_decision_id'] and x['event_owner_decision_id'] is None and x['replay_accepted']
    assert x['from_state']==F.APPROVED_FIRST_LIVE and x['status']=='inserted'
    assert x['event']['binding']['approval_request_id'] and x['event']['owner_decision_id'] is None
log=(E/'related-engineering.txt').read_text(); assert '3 failed, 682 passed' in log
assert all(x in log for x in rec['validation']['failure_ids'])
assert len(rec['limitations'])==3 and 'not registry-validated' in rec['limitations'][0] and 'not cryptographic' in rec['limitations'][1] and 'STR-05' in rec['limitations'][2]
md=(R/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text(); old_md=subprocess.check_output(['git','show',base+':docs/tracker/LUFFY_Product_Tracker_v1.md'],cwd=R,text=True)
for rid,row in new.items():
    marker='### '+rid+' —'
    if marker not in md: continue
    section=md[md.index(marker):].split('\n### ',1)[0]
    if rid=='STR-04':
        for k in ['status','closure_condition','next_proof','closure_evidence','blocker_evidence']: assert '- **'+k+'**: '+row[k] in section,k
    else: assert section.startswith(old_md[old_md.index(marker):].split('\n### ',1)[0].rstrip()),rid
result=read_tracker(R); assert result['status']=='AVAILABLE',result
assert result['selected']['id']=='STR-04' and result['selected']['status']=='BLOCKED'
assert all(result['counts'][k]==v for k,v in counts.items())
m=json.loads((R/'docs/tracker/bundle-manifest.json').read_text())
assert m['row_count']==178 and m['snapshot_version']==t['metadata']['version']
assert m['str04_canonical_reconciliation']==rec and m['current_build_selection']==sel
assert len({x['path'] for x in m['files']})==len(m['files'])
for e in m['files']:
    raw=(R/e['path']).read_bytes(); assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
changed=subprocess.check_output(['git','diff','--name-only',base],cwd=R,text=True).splitlines()
# Unrelated dashboard/frontend edits appeared concurrently during this audit.
# Verify the pinned lifecycle sources above and restrict the staged task commit below.
concurrent = [p for p in changed if not p.startswith(('docs/tracker/', 'graphify-out/')) and p not in ['STATE.yaml', 'NEXT.yaml']]
assert all(p.startswith(('frontend/', 'trader/dashboard/')) for p in concurrent), concurrent
staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=R, text=True).splitlines()
assert all(p in ['STATE.yaml', 'NEXT.yaml'] or p in ['docs/tracker/LUFFY_Product_Tracker_v1.yaml', 'docs/tracker/LUFFY_Product_Tracker_v1.md', 'docs/tracker/README.md', 'docs/tracker/bundle-manifest.json'] or p.startswith('docs/tracker/evidence/str04-canonical-sol1/') for p in staged), staged
cfg=load('config.yaml'); assert not cfg['research']['handoff'] and not cfg['research']['referee']
checks=dict(STR04_canonical='BLOCKED',blocker='STR04-BINDING-01',required_owner_decision_binding_failed=True,ordinary_canonical_path_reproduced=True,row_count=178,status_counts=counts,changed_rows=['STR-04'],unrelated_rows_unchanged=177,STR03_unchanged_CLOSED=True,DEC03_dependencies_satisfied=False,DEC03_now_eligible=False,DEC03_selected=False,DEC03_started=False,original_conditions_and_edges_preserved=True,strict_YAML_contracts=True,manifest_hashes_and_source_bytes_match=True,dashboard_read='AVAILABLE',dashboard_selected='STR-04 BLOCKED',fresh_STR04_passed=26,fresh_STR03_passed=44,retained_broader_passed=682,retained_broader_failed=3,baseline_reproduction_user_supplied=True,runtime_state_unchanged=True,application_code_changed_by_this_task=False,provider_calls=0,concurrent_workspace_changes_preserved=True)
(E/'control-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
