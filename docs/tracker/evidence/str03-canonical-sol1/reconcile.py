"""Authorized STR-03 control-plane reconciliation; implementation remains read-only."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import copy, hashlib, json, re, shutil, subprocess
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
EVID = str(EP.relative_to(ROOT))
HEAD = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
assert HEAD == '2b119a318bdca34599eb8620c9f1f1cc62a78f7e'
paths = {'tracker': 'docs/tracker/LUFFY_Product_Tracker_v1.yaml', 'state': 'STATE.yaml', 'next': 'NEXT.yaml'}
docs = {k: yaml.safe_load((ROOT / p).read_text()) for k, p in paths.items()}
(EP / 'control-before.json').write_text(json.dumps(docs, indent=2) + '\n')
rows = {r['id']: r for r in docs['tracker']['items']}
old = rows['STR-03']
assert old['status'] == 'EVIDENCE_TO_MAP'
assert all(rows[d]['status'] == 'CLOSED' for d in old['dependencies'])
suite = ET.parse(EP / 'dedicated.xml').getroot().find('testsuite')
assert int(suite.get('tests')) == 43
assert all(int(suite.get(k)) == 0 for k in ['failures', 'errors', 'skipped'])
retained = Path('/tmp/claude-1000/-mnt-luffy-data-luffy-production/2e2bb811-57dd-4d57-892c-fd6f087659e2/scratchpad')
assert '1 failed, 642 passed' in (retained / 'reg.txt').read_text()
for source, dest in [('reg.txt', 'related-engineering.txt'), ('files.txt', 'related-suite-list.txt')]:
    shutil.copyfile(retained / source, EP / dest)
counts = dict(Counter(r['status'] for r in rows.values()))
counts['CLOSED'] += 1
counts['EVIDENCE_TO_MAP'] -= 1
source_paths = ['trader/strategy/factory_handoff.py', 'trader/core/journal.py', 'trader/owner/approvals.py',
                'tests/test_str03_approval_binding.py', 'tests/test_strategy_factory_handoff.py',
                'trader/strategy/compile.py', 'tests/authority_factory_fixtures.py', 'config.yaml']
hashes = {}
for path in source_paths:
    raw = (ROOT / path).read_bytes()
    assert raw == subprocess.check_output(['git', 'show', HEAD + ':' + path], cwd=ROOT), path
    hashes[path] = hashlib.sha256(raw).hexdigest()
bank = 'research_bank_path is asserted at the eligibility/governor call boundary rather than stored as a permanent path authority.'
guard = 'SQLite writer guard can theoretically be bypassed by code deliberately enabling the internal write flag (journal._local.approval_write); it is an application writer boundary, not isolation from malicious trusted Python or direct database administration.'
replay = 'Fresh fixture rebuild IDs vary because a research look contains wall-clock data; replay is proven against the same stored chain.'
mapping = {
    'exact_binding': 'Request binds strategy_id, version_id, spec hash, STR-01 lineage hash, research/quantitative hashes, exact install and validation/probation receipts. Request ID is re-derived from these bindings including FIRST_LIVE_EXACT_VERSION scope. Owner decision binds request identity, exact version/evidence, owner actor/decision identity and configuration hash.',
    'narrow_scope': 'Scope explicitly excludes by-name, latest and descendants; sibling/derived versions do not inherit receipts or approval. ACTIVE, grandfather receipts and unversioned/legacy authority cannot replace exact first-live approval.',
    'integrity': 'Factory writer triggers refuse ordinary raw-SQL request/decision inserts. Requests are re-derived, decisions re-hashed and rebound; APPROVED_FIRST_LIVE must name the exact owner decision. Changed/copy-forged bindings refuse even in the test that deliberately enables the internal flag.',
    'staleness': 'Material spec/version, research status/revision, quantitative, installed logic, probation trades, risk/config/approval limits or required data changes refuse use. Frozen rows cannot be updated/deleted; old owner artifacts remain inspectable with STALE validity/reasons. Approval scope alteration fails exact request verification.',
    'predictive_research': 'Eligibility re-authenticates predictive lineage against the Research Bank; a superseding result refuses, and absent research_bank_path gives research_bank_not_asserted. Governor forwards this required call-boundary assertion.',
    'governor': 'Governor loads/authenticates the exact approved version, install and evidence; event names exact spec hash/version and owner decision. Unapproved sibling, changed probation/install/research/config refuse before any Governor event is written. Successful offline activation still preserves the live execution fence.',
    'authority': 'Owner approval alone changes no strategy live state, control state, trade count or Governor allocation. It grants no capital, orders, Risk bypass or Kernel start. Capacity, Risk and live/deployment/security gates remain downstream/deferred.',
    'replay': 'Same stored chain re-derives request identity, survives byte-copy restore/restart and identical retry; conflicting decision refuses. Derived versions have new identity and no inherited authority.'}
record = dict(item='STR-03', canonical_status='CLOSED', scope='ARCHITECTURE_OFFLINE',
    engineering_closure_commit=HEAD, control_baseline_revision=HEAD,
    control_plane_commit_resolution='Enclosing Git commit containing this reconciliation; no self-referential hash.',
    closing_condition=old['closure_condition'], failure_regression_proof=old['failure_regression_proof'],
    closing_condition_satisfied=True, closure_mapping=mapping,
    research_bank_path_limitation=bank, write_guard_limitation=guard, replay_limitation=replay,
    write_guard_classification='NON_BLOCKING_FOR_EXACT_STR03_ARCHITECTURE_OFFLINE_CONDITION',
    write_guard_reasoning='The exact row requires authenticated artifact handoff and refusal of mismatch/stale context. Ordinary SQL insertion is refused and copied/forged identities are reverified. Deliberately privileged in-process code can enable the guard and fabricate self-consistent records; cryptographic owner attestation or isolation from hostile trusted code is not proven. This remains a downstream security limitation, not a claim of tamper-proof owner authority.',
    validation=dict(fresh_dedicated_passed=43, runner='/home/sarmad/.local/bin/luffy-pytest',
        retained_related_passed=642, retained_related_failed=1, retained_related_seconds=1385.72,
        retained_related_includes_dedicated=True, counts_overlap_do_not_sum=True,
        baseline_failure='tests/test_stage8_owner_os.py::test_portfolio_exact_cut_candidates_rejection_intent_risk',
        baseline_error='json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)',
        baseline_classification='Unrelated baseline failure per user-supplied engineering validation; retained final log confirms failure ID/error. Before-change baseline log is not independently retained here.',
        retained_log=EVID + '/related-engineering.txt', retained_suite_list=EVID + '/related-suite-list.txt',
        full_related_run_repeated=False, full_suite_green_claim=False, provider_calls=0,
        independent_immutable_restart_proof=EVID + '/offline-proof.json'),
    dependency_status={d: rows[d]['status'] for d in old['dependencies']},
    STR04_dependency_satisfied=True, STR04_now_eligible=True, STR04_selected=False, STR04_started=False,
    STR04_status=rows['STR-04']['status'], DEC03_remaining_dependency_blockers=['STR-04'],
    original_conditions_and_edges_preserved=True, unrelated_rows_unchanged=177,
    status_counts_after=counts, source_sha256=hashes, application_code_changed=False,
    runtime_execution_authorized=False, provider_enablement_authorized=False, trading_activation_authorized=False)
(EP / 'closure.json').write_text(json.dumps(record, indent=2) + '\n')
row = copy.deepcopy(old)
row.update(status='CLOSED', implementation_evidence='Exact-version approval request/decision and Research Bank re-authentication at 2b119a3; ' + EVID + '/closure.json',
    runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; no deployed/live/provider proof claimed',
    latest_evidence='43 fresh dedicated passes; retained related run 642 passes / 1 unrelated baseline failure (overlapping counts). Exact chain/scope, raw-SQL refusal, stale/forged refusal, Governor no-write mismatch and stored-chain restart/retry verified. ' + bank + ' ' + guard + ' ' + replay,
    next_proof='None for exact STR-03 architecture/offline condition. Preserve documented bank-path, privileged writer and replay limitations. STR-04 dependency satisfied and eligible, unselected and not started; its own lifecycle proof remains open.',
    resolution_at_commit=HEAD, implementation_commit=HEAD, updated='2026-10-07',
    closure_evidence=EVID + '/closure.json', evidence_mapping=EVID + '/REPORT.md',
    evidence_validity='Pinned architecture/offline verification at 2b119a3; no later deployed/runtime revalidation claim.')

def block(text, key, value):
    match = re.search(r'(?m)^' + re.escape(key) + r':\n', text)
    assert match, key
    end = re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:', text[match.end():])
    stop = match.end() + end.start() if end else len(text)
    return text[:match.start()] + yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, width=105) + '\n' + text[stop:]

sel = copy.deepcopy(docs['tracker']['current_build_selection'])
sel.update(last_completed_item='STR-03', last_terminal_item='STR-03', next_recommended_item='STR-04',
    selection_reason='STR-03 exact first-live artifact approval condition CLOSED at architecture/offline scope. STR-04 dependency satisfied, eligible for its own lifecycle evidence mapping; unselected and not started. DEC-03 remains gated by STR-04. No activation authority.')
sel['eligible_items'] = [x for x in sel['eligible_items'] if x != 'STR-03'] + ['STR-04']
package = dict(id='STR-03', status='CLOSED', result='CLOSED_ARCHITECTURE_OFFLINE', mode='ENGINEERING_ONLY',
    scope='ARCHITECTURE_OFFLINE', objective=row['required_behavior'], implementation_commit=HEAD,
    evidence=[EVID + '/REPORT.md', EVID + '/closure.json'], implementation_complete=True,
    canonical_reconciliation_pending=False, runtime_execution_authorized=False, provider_enablement_authorized=False,
    trading_activation_authorized=False, successor_selected=False)
tp = ROOT / paths['tracker']
text = tp.read_text()
start = text.index('- id: STR-03\n'); end = text.index('- id: STR-04\n', start)
text = text[:start] + yaml.safe_dump([row], sort_keys=False, allow_unicode=True, width=105) + text[end:]
meta = copy.deepcopy(docs['tracker']['metadata'])
meta.update(version='1.52-str03-canonical-sol1', current_active_item='STR-03', last_terminal_item='STR-03',
    last_terminal_evidence=EVID + '/REPORT.md', next_recommended_item='STR-04', status_counts=counts)
text = block(text, 'metadata', meta)
text = block(text, 'current_build_selection', sel)
text += '\n' + yaml.safe_dump({'str03_canonical_reconciliation': record}, sort_keys=False, allow_unicode=True, width=105)
tp.write_text(text)
for which, active in [('state', 'current_active_work'), ('next', 'work_package')]:
    p = ROOT / paths[which]
    text = block(p.read_text(), active, package)
    text = block(text, 'current_build_selection', sel)
    text += '\n' + yaml.safe_dump({'str03_previous_selection': docs[which][active], 'str03_canonical_reconciliation': record}, sort_keys=False, allow_unicode=True, width=105)
    p.write_text(text)
banner = '> Current [SOL-1][STR-03]: **CLOSED at architecture/offline scope** on `2b119a3`. Exact first-live approval binding, staleness and Governor handoff verified; 43 dedicated passes, retained 642 related passes / 1 unrelated baseline failure. Bank path is asserted by caller; privileged internal write-flag bypass and stored-chain replay limitations remain explicit. STR-04 dependency satisfied and eligible, unselected and not started. [Evidence](evidence/str03-canonical-sol1/REPORT.md). Earlier summaries below are historical.\n\n'
mp = ROOT / 'docs/tracker/LUFFY_Product_Tracker_v1.md'
text = mp.read_text(); start = text.index('### STR-03 —'); end = text.index('### STR-04 —', start)
section = text[start:end]
for key, value in row.items():
    if old.get(key) != value:
        marker = '- **' + key + '**: '
        if marker.rstrip() in section:
            section = re.sub(r'(?m)^' + re.escape(marker.rstrip()) + r'.*$', lambda m, v=value, mark=marker: mark + str(v), section)
        else:
            section = section.rstrip() + '\n' + marker + str(value) + '\n\n'
mp.write_text(banner + text[:start] + section + text[end:] + '\nSTR-03 canonical status counts: ' + ', '.join(f'{k} {v}' for k, v in counts.items()) + ' (178 total).\n')
p = ROOT / 'docs/tracker/README.md'; p.write_text(banner + p.read_text())
report = '# STR-03 canonical CLOSED\n\nThe exact closing condition is satisfied at `2b119a3`: ' + old['closure_condition'] + '. Original conditions, source references and dependency edges are preserved.\n\n'
for key, value in mapping.items():
    report += '**' + key.replace('_', ' ').capitalize() + '.** ' + value + '\n\n'
report += '**Limitations.** ' + bank + ' ' + replay + '\n\n' + guard + ' This is non-blocking for the exact STR-03 architecture/offline condition: ordinary SQL is guarded and artifact identities are authenticated on use. Deliberate privileged code could fabricate self-consistent records. Hostile trusted-code isolation or cryptographic owner attestation is not proven; downstream security/live/deployment gates remain deferred.\n\n'
report += '**Validation.** 43 dedicated tests freshly passed through luffy-pytest. Retained related log: 642 passed / 1 failed in 1385.72s across 24 listed suites, including dedicated tests; counts overlap and are not summed. The failure is `tests/test_stage8_owner_os.py::test_portfolio_exact_cut_candidates_rejection_intent_risk` with JSONDecodeError, classified as unrelated baseline by the supplied engineering validation. Its before-change baseline log is not independently retained here. No full-suite green claim. No live/provider calls; Kernel and Dashboard remained stopped, watchdog.off present.\n\n'
report += 'STR-04 dependency is satisfied and STR-04 is now eligible for its own proof. Its row is unchanged, unselected and not started. DEC-03 remains gated by STR-04. Approval and reconciliation grant no trading, capital, orders, Risk bypass or Kernel start. All 177 other rows and runtime state are preserved.\n\nStatus counts: ' + '; '.join(f'{k} {v}' for k, v in counts.items()) + ' (178 total). Control-plane commit: enclosing Git commit containing this report. See [closure](closure.json), [fresh dedicated results](dedicated.xml), [related retained log](related-engineering.txt) and [control checks](control-checks.json).\n'
(EP / 'REPORT.md').write_text(report)
manifest_path = ROOT / 'docs/tracker/bundle-manifest.json'
m = json.loads(manifest_path.read_text())
m.update(snapshot_version=meta['version'], control_baseline_revision=HEAD, current_build_selection=sel, str03_canonical_reconciliation=record)
known = {f['path'] for f in m['files']}
for p in sorted(EP.iterdir()):
    rel = str(p.relative_to(ROOT))
    if p.is_file() and rel not in known and p.name != 'control-checks.json':
        m['files'].append(dict(path=rel, role='STR03_CANONICAL_CONTROL_OR_EVIDENCE', synchronization_required=True))
for entry in m['files']:
    raw = (ROOT / entry['path']).read_bytes()
    entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
manifest_path.write_text(json.dumps(m, indent=2) + '\n')
print(json.dumps({'canonical_status': 'CLOSED', 'status_counts': counts}, indent=2))
