"""Validate canonical contracts, exact preserved rows, hashes and offline boundaries."""
from pathlib import Path
from collections import Counter
import hashlib, json, subprocess
import xml.etree.ElementTree as ET
import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker

ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
before = json.loads((EP / 'control-before.json').read_text())
record = json.loads((EP / 'closure.json').read_text())
def load(path):
    return yaml.load((ROOT / path).read_text(), Loader=UniqueLoader)
t = load('docs/tracker/LUFFY_Product_Tracker_v1.yaml')
s = load('STATE.yaml'); n = load('NEXT.yaml')
Ledger.model_validate(t); NextDocument.model_validate(n)
old = {r['id']: r for r in before['tracker']['items']}
new = {r['id']: r for r in t['items']}
assert len(new) == 178 and old.keys() == new.keys()
assert [rid for rid in old if old[rid] != new[rid]] == ['STR-03']
for k in ['required_behavior', 'closure_condition', 'failure_regression_proof', 'dependencies',
          'source_ids', 'source_sections', 'parent_ids', 'related_items', 'acceptance_basis',
          'acceptance_status', 'new_issue_policy', 'work_authorized']:
    assert new['STR-03'][k] == old['STR-03'][k], k
assert new['STR-03']['status'] == 'CLOSED'
assert all(new[d]['status'] == 'CLOSED' for d in new['STR-03']['dependencies'])
assert new['STR-04'] == old['STR-04'] and new['STR-04']['dependencies'] == ['STR-03']
assert [d for d in new['DEC-03']['dependencies'] if new[d]['status'] != 'CLOSED'] == ['STR-04']
counts = dict(Counter(r['status'] for r in new.values()))
assert counts == t['metadata']['status_counts'] == record['status_counts_after']
assert sum(counts.values()) == 178
assert t['readiness_gates'] == before['tracker']['readiness_gates']
for doc in [t, s, n]:
    assert doc['str03_canonical_reconciliation'] == record
    sel = doc['current_build_selection']; assert sel == t['current_build_selection']
    assert sel['eligible_items'] == [x for x in before['tracker']['current_build_selection']['eligible_items'] if x != 'STR-03'] + ['STR-04']
    assert not sel['active_items'] and not sel['next_item_selected'] and not sel['runtime_execution_authorized']
    assert sel['next_recommended_item'] == 'STR-04'
assert t['metadata']['next_recommended_item'] == 'STR-04'
for which, doc, key in [('state', s, 'current_active_work'), ('next', n, 'work_package')]:
    assert doc[key]['id'] == 'STR-03' and doc[key]['status'] == 'CLOSED'
    assert all(doc[key][k] is False for k in ['runtime_execution_authorized', 'provider_enablement_authorized', 'trading_activation_authorized', 'successor_selected'])
    assert doc['str03_previous_selection'] == before[which][key]
    for k, v in before[which].items():
        if k not in [key, 'current_build_selection']:
            assert doc[k] == v, (which, k)
for k, v in before['tracker'].items():
    if k not in ['metadata', 'items', 'current_build_selection']:
        assert t[k] == v, k
assert s['current_runtime_status'] == before['state']['current_runtime_status']
for path, sha in record['source_sha256'].items():
    raw = (ROOT / path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sha, path
    assert raw == subprocess.check_output(['git', 'show', record['source_revisions'][path] + ':' + path], cwd=ROOT), path
cfg = load('config.yaml')
assert not cfg['research']['handoff'] and not cfg['research']['referee']
suite = ET.parse(EP / 'dedicated.xml').getroot().find('testsuite')
assert int(suite.get('tests')) == 44
assert all(int(suite.get(k)) == 0 for k in ['errors', 'failures', 'skipped'])
clean = ET.parse(EP / 'clean-2b119a3.xml').getroot().find('testsuite')
assert int(clean.get('tests')) == 44
assert all(int(clean.get(k)) == 0 for k in ['errors', 'failures', 'skipped'])
assert record['validation']['clean_without_STR04']
clean_proof = json.loads((EP / 'clean-source-proof.json').read_text())
assert clean_proof['passed'] == 44 and not clean_proof['STR04_present']
assert clean_proof['verified_file_count'] == len(clean_proof['file_hashes'])
for path, sha in clean_proof['file_hashes'].items():
    revision = clean_proof['test_supplement_revision'] if path == clean_proof['only_supplement'] else clean_proof['implementation_revision']
    assert hashlib.sha256(subprocess.check_output(['git', 'show', revision + ':' + path], cwd=ROOT)).hexdigest() == sha, path
subprocess.run(['git', 'merge-base', '--is-ancestor', record['STR04_engineering_commit'], record['verification_revision']], cwd=ROOT, check=True)
assert set(subprocess.check_output(['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', record['STR04_engineering_commit']], cwd=ROOT, text=True).splitlines()) == {'trader/strategy/factory_handoff.py', 'tests/test_str04_governor_lifecycle.py'}
assert '1 failed, 642 passed' in (EP / 'related-engineering.txt').read_text()
assert record['validation']['baseline_failure'] in (EP / 'related-engineering.txt').read_text()
assert 'wall-clock' in record['replay_limitation']
assert 'call boundary' in record['research_bank_path_limitation']
assert 'internal write flag' in record['write_guard_limitation']
assert record['write_guard_classification'] == 'NON_BLOCKING_FOR_EXACT_STR03_ARCHITECTURE_OFFLINE_CONDITION'
proof = json.loads((EP / 'offline-proof.json').read_text())
assert len(proof['approval_row_update_delete_refusals']) == 4
assert proof['actual_row_values_equal_after_restart_retry'] and proof['request_identity_rederived']
assert proof['exact_decision_identity_equal'] and proof['retry_status'] == 'duplicate'
assert proof['kernel_dashboard_processes'] == [] and proof['watchdog_off_present']
md = (ROOT / 'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
base_md = subprocess.check_output(['git', 'show', record['control_baseline_revision'] + ':docs/tracker/LUFFY_Product_Tracker_v1.md'], cwd=ROOT, text=True)
for rid, row in new.items():
    marker = '### ' + rid + ' —'
    if marker not in md:
        continue
    section = md[md.index(marker):].split('\n### ', 1)[0]
    if rid == 'STR-03':
        for k in ['status', 'closure_condition', 'next_proof', 'closure_evidence']:
            assert '- **' + k + '**: ' + row[k] in section, k
    else:
        assert section.startswith(base_md[base_md.index(marker):].split('\n### ', 1)[0].rstrip()), rid
res = read_tracker(ROOT)
assert res['status'] == 'AVAILABLE', res
assert res['selected']['id'] == 'STR-03' and res['selected']['status'] == 'CLOSED'
for k, v in counts.items():
    assert res['counts'][k] == v
m = json.loads((ROOT / 'docs/tracker/bundle-manifest.json').read_text())
assert m['snapshot_version'] == t['metadata']['version'] and m['row_count'] == 178
assert m['current_build_selection'] == t['current_build_selection'] and m['str03_canonical_reconciliation'] == record
for entry in m['files']:
    raw = (ROOT / entry['path']).read_bytes()
    assert len(raw) == entry['bytes'] and hashlib.sha256(raw).hexdigest() == entry['sha256'], entry['path']
paths = subprocess.check_output(['git', 'diff', '--name-only', record['control_refresh_baseline_revision']], cwd=ROOT, text=True).splitlines()
assert not any(p.startswith(('trader/', 'tests/', 'scripts/')) or p == 'config.yaml' for p in paths), paths
checks = dict(row_count=178, changed_rows=['STR-03'], unrelated_rows_unchanged=177,
    original_conditions_and_edges_preserved=True, strict_YAML_contracts=True,
    source_committed_bytes_and_manifest_hashes_match=True, status_counts=counts,
    dashboard_read='AVAILABLE', dashboard_selected=res['selected'], STR03_canonical='CLOSED',
    STR04_dependency_satisfied=True, STR04_now_eligible=True, STR04_row_unchanged=True, STR04_engineering_commit_valid=True,
    STR04_selected=False, STR04_started=False, DEC03_remaining_blockers=['STR-04'],
    runtime_state_unchanged=True, research_handoff_disabled=True, research_referee_disabled=True,
    application_code_changed=False, provider_calls=0, fresh_dedicated_passed=44, clean_2b119a3_passed=44,
    independent_approval_immutability_restart_retry=True, kernel_dashboard_stopped=True,
    retained_related_passed=642, retained_related_failed=1,
    write_guard_classification=record['write_guard_classification'])
(EP / 'control-checks.json').write_text(json.dumps(checks, indent=2) + '\n')
print(json.dumps(checks, indent=2))
