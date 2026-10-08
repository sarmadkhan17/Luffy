"""PORT-01 control-only reconciliation and reproducible pinned verification.

Run with the project venv. PORT01_RECONCILE=APPLY performs the explicitly authorized
control writes; default VERIFY is read-only. Product implementation is untouched.
"""
from collections import Counter
from pathlib import Path
import copy
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET

import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker

ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
EVID = str(EP.relative_to(ROOT))
BASE = '2b5d250c418aef3268eb53dc7d1efbd081efc44f'
ENG = '2ea4a95349062b7ec19cde6263fa8b3e8af45660'
ENG_BASE = '1426c2a70a3ee72b1e8cfc2ac7d19c505eee0c5c'
EXE04_ENG = '916de7322dbc6ad85a66d7c6252f5d672c9bfcc1'
TP = 'docs/tracker/LUFFY_Product_Tracker_v1.yaml'
VERSION = '1.57-port01-canonical-closed-sol1'
ENG_FILES = {'tests/capability_fixtures.py', 'tests/test_port01_whole_book.py', 'tests/test_portfolio_allocator.py',
             'tests/test_stage1_entry_authority.py', 'tests/test_stage6_closure.py',
             'tests/test_stage6_normal_sources.py', 'trader/portfolio/allocator.py'}


def git_bytes(revision, path):
    return subprocess.check_output(['git', 'show', revision+':'+path], cwd=ROOT)


def load(path):
    return yaml.load((ROOT/path).read_text(), Loader=UniqueLoader)


def old(path):
    return yaml.load(git_bytes(BASE, path), Loader=UniqueLoader)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def suite(name, tests, failures=0):
    s = ET.parse(EP/name).getroot().find('testsuite')
    assert int(s.get('tests')) == tests, name
    assert int(s.get('failures')) == failures, name
    assert all(int(s.get(k)) == 0 for k in ('errors', 'skipped')), name
    return s


def failed_names(name):
    return sorted(l.split()[1] for l in (EP/name).read_text().splitlines() if l.startswith('FAILED '))


def engineering_proof():
    subprocess.check_call(['git', 'cat-file', '-e', ENG+'^{commit}'], cwd=ROOT)
    subprocess.check_call(['git', 'cat-file', '-e', EXE04_ENG+'^{commit}'], cwd=ROOT)
    assert subprocess.check_output(['git', 'rev-parse', ENG+'^'], cwd=ROOT, text=True).strip() == ENG_BASE
    names = set(subprocess.check_output(['git', 'diff', '--name-only', ENG_BASE, ENG], cwd=ROOT, text=True).splitlines())
    assert names == ENG_FILES, names
    a = git_bytes(ENG, 'trader/portfolio/allocator.py').decode()
    for token in ["VERSION = 'LUFFY-PORTFOLIO-ALLOCATOR-R4'", "VERSION_R3 = 'LUFFY-PORTFOLIO-ALLOCATOR-R3'",
                  "VERSION_R2 = 'LUFFY-PORTFOLIO-ALLOCATOR-R2'", "SUPPORTED_VERSIONS = (VERSION_R2, VERSION_R3, VERSION)",
                  "CAPABILITY_PREFIX = 'entry-capability:'", "raw.get('receipt_id') != digest(rest)",
                  "'ENTRY_CAPABILITY_UNAVAILABLE'", "'ENTRY_CAPABILITY_AMBIGUOUS'", "'ENTRY_CAPABILITY_STALE'",
                  "'ENTRY_CAPABILITY_INVALID_OR_INCOMPATIBLE'", "'ENTRY_CAPABILITY_ACCOUNT_OR_INSTRUMENT_INELIGIBLE'",
                  "record['shortability'] != 'PROVEN'", "capabilities is not None and interaction == 'NEW_POSITION'",
                  "if version == VERSION else None"]:
        assert token in a, token
    for forbidden in ('ccxt', 'engine.executor', 'engine.protective', 'engine.orchestrator', 'requests'):
        assert forbidden not in ' '.join(l for l in a.splitlines() if l.startswith(('import ', 'from '))), forbidden
    unaffected = ['trader/portfolio/current.py', 'trader/portfolio/trade_intent.py', 'trader/portfolio/runtime.py',
                  'trader/engine/entry_authority.py', 'trader/engine/risk.py', 'trader/engine/risk_intent.py',
                  'trader/engine/executor.py', 'trader/kernel.py', 'config.yaml']
    for path in unaffected:
        assert git_bytes(ENG_BASE, path) == git_bytes(ENG, path), path
    fresh = suite('fresh-port01.xml', 27)
    integration = suite('integration-dec03-plus-r4.xml', 80)
    by_file = dict(Counter(c.get('classname') for c in integration.findall('testcase')))
    assert by_file == {'tests.test_dec03_position_evidence': 21, 'tests.test_dec02_comparison': 25,
                       'tests.test_port01_whole_book': 27, 'tests.test_stage6_normal_sources': 7}, by_file
    r4, base = failed_names('selected-r4.txt'), failed_names('selected-base.txt')
    assert r4 == base and len(r4) == 11, (r4, base)
    assert '11 failed, 1369 passed' in (EP/'selected-r4.txt').read_text()
    assert '11 failed, 1342 passed' in (EP/'selected-base.txt').read_text()
    old_replay = (EP/'replay-r2-r3-base.txt').read_text()
    assert old_replay == (EP/'replay-r2-r3-r4tree.txt').read_text() and old_replay.count('verify=True') == 10
    return dict(dedicated_port01_passed=27, integration_dec03_plus_r4_passed=80, integration_by_file=by_file,
                selected_r4_passed=1369, selected_r4_failed=11, selected_base_passed=1342, selected_base_failed=11,
                identical_failure_names=r4, r2_r3_replay_result_bytes_identical_across_base_and_r4=True,
                unrelated_files_unchanged=unaffected, counts_overlap_do_not_sum=True, full_suite_green_claim=False,
                engineering_reported_1813_passed_9_failed_names_enumerated=False)


def replace_block(text, key, value):
    match = re.search(r'(?m)^'+re.escape(key)+r':\n', text)
    assert match, key
    following = re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:', text[match.end():])
    end = match.end()+following.start() if following else len(text)
    return text[:match.start()]+yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, width=105)+'\n'+text[end:]


def refresh_manifest():
    path = ROOT/'docs/tracker/bundle-manifest.json'
    m = json.loads(path.read_text())
    existing = {e['path'] for e in m['files']}
    for file in sorted(EP.iterdir()):
        if file.is_file() and str(file.relative_to(ROOT)) not in existing:
            m['files'].append(dict(path=str(file.relative_to(ROOT)), role='PORT01_CANONICAL_CONTROL_OR_EVIDENCE', synchronization_required=True))
    for entry in m['files']:
        raw = (ROOT/entry['path']).read_bytes()
        entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    write_json(path, m)


MAPPING = {
    'original_gap': 'Before R4 (R3 at 1426c2a) the normal current.freeze -> Consumer.consume path with no registry capability produced a COMPLETE OPEN TradeIntent (capability_receipt_id None) that Risk APPROVED in the normal-source fixture. This is the real normal path, not a synthetic allocator fixture; see gap-probe.txt. The entry-authority gate at execution is separate and unchanged.',
    'r4_contract': 'LUFFY-PORTFOLIO-ALLOCATOR-R4 requires exactly one frozen entry-capability:<instrument> for NEW_POSITION: entry-capability.v1 schema, receipt digest, exact instrument and market, observed_at <= cut <= valid_until, account trading ENABLED, account ELIGIBLE, venue TRADING, SHORT needs shortability PROVEN. Missing -> INCOMPLETE (ENTRY_CAPABILITY_UNAVAILABLE); stale/ineligible/invalid/ambiguous -> BLOCKED. KEEP/FLAT/SUPPORTS_EXISTING are ungated. No threshold, no venue call, no UNKNOWN -> zero.',
    'whole_book': 'One frozen cut carries holdings, every eligible candidate, account state, capability sources, portfolio snapshot id and CASH. Every candidate gets an opportunity-cost comparison against the book; CASH is always available. A capability-less economics-9 candidate cannot beat a capability-valid economics-2 candidate: R4 selects the latter while R3 on the same inputs would have chosen the former.',
    'cash_fail_closed': 'Missing/UNKNOWN economics, missing bounds, missing costs, UNKNOWN portfolio, unavailable Risk policy, FROZEN control and missing capability each select CASH with proposed_size UNAVAILABLE and a NO_ACTION intent with null requested size. Unavailable never becomes zero.',
    'normal_caller': 'current.freeze reads the capability recorded by the kernel (market instrument binding, cross-checked against the entry-authority kv record) and freezes entry-capability:<instrument>; a change during the read refuses PROPOSAL_CAPABILITY_CHANGED_DURING_READ. Consumer.consume -> allocate -> trade_intent.build binds the exact receipt -> risk_intent.evaluate. No manual priming in the normal-path tests. Absent or stale capability yields CASH and no OPEN intent.',
    'authority_boundary': 'Every TradeIntent keeps risk_final_authority=true, execution_routed=false, risk_approval=NOT_REQUESTED, side_effects=NONE. Portfolio grants no Risk approval, execution, venue, order, leverage or final-quantity authority; quantity/notional/leverage stay with Risk and entry_authority, which reread the trusted capability. The allocator capability digest is the entry_authority digest. Portfolio modules import no venue/executor/exits/protective/orchestrator.',
    'versioning_replay': 'R2, R3 and R4 are distinct, non-substitutable and all supported by verify. R2/R3 result bytes are identical between the pre-R4 base and the R4 tree on five input shapes; no historical receipt rewritten. R4 ordering is stable under candidate and source permutations; a stale or swapped cut refuses.',
    'scope_limit': 'Architecture/offline scope; capability fixtures are TEST-ONLY synthetic authorities built through register_capability. No deployed, live, provider or venue proof. Engineering commit is on branch port01-whole-book (base 1426c2a) and is not merged by this reconciliation.'}


def reconcile(proof):
    prior = old(TP)
    rows = {r['id']: r for r in prior['items']}
    row = copy.deepcopy(rows['PORT-01'])
    assert row['status'] == 'EVIDENCE_TO_MAP' and rows['DEC-01']['status'] == rows['DATA-01']['status'] == 'CLOSED'
    counts = dict(Counter(r['status'] for r in prior['items']))
    counts['CLOSED'] += 1; counts['EVIDENCE_TO_MAP'] -= 1
    impact = {
        'DEC-02': 'Unchanged CLOSED; row untouched. Default allocator moves R3 -> R4; R2 replay and R3 distinct. test_dec02_comparison 25 pass on the integrated tree. HISTORICAL REPLAY ONLY.',
        'DEC-03': 'Unchanged CLOSED. R4 merges cleanly onto 1ec9870; 21 DEC-03 + 25 DEC-02 + 7 normal-source + 27 PORT-01 pass (80). Not part of this engineering base, so the integrated tree is a verification merge only. UNCHANGED.',
        'PORT-02': 'Unchanged CLOSED; economics authority consumed as before. TEST/FIXTURE UPDATE (shared allocator fixture now attaches capabilities, done in 2ea4a95); test_port02_economics_authority passes.',
        'PORT-04': 'Dependency PORT-01 satisfied (WRLD-07, QNT-03 already CLOSED): eligible, unselected, unstarted, row unchanged and not auto-closed. Separate APPLICATION GAP remains: runtime current.py attaches common_factor without relationship_sources (confirmed in source; previously reported by Opus). Does not block PORT-01.',
        'RISK-01/RISK-03': 'UNCHANGED: risk.py/risk_intent.py/entry_authority.py bytes identical to base; Risk still final authority. test_risk01/02 pass.',
        'entry_authority_execution': 'UNCHANGED application code; test_stage1_entry_authority fixture assertion adjusted (TEST/FIXTURE UPDATE). No execution route added.',
        'DEC-04': 'UNCHANGED; row not touched and not closed.',
        'Stage6_Stage8': 'Stage6 fixtures TEST/FIXTURE UPDATE (done in 2ea4a95; stage6 closure and normal-source tests pass). Stage8 portfolio-cut query test fails identically on base and R4 at owner/queries.py:205 (journal-detail wrapped payload not decoded): pre-existing APPLICATION GAP unrelated to PORT-01 that masks, but does not contradict, R4 on that consumer.'}
    record = dict(item='PORT-01', canonical_status='CLOSED', scope='ARCHITECTURE_OFFLINE',
        engineering_commit=ENG, engineering_base=ENG_BASE, control_baseline_revision=BASE,
        control_plane_commit_resolution='Enclosing Git commit; no self-referential hash.',
        closing_condition=row['closure_condition'], failure_regression_proof=row['failure_regression_proof'],
        closing_condition_satisfied=True, closure_mapping=MAPPING, validation=proof,
        allocator_versions=dict(current='LUFFY-PORTFOLIO-ALLOCATOR-R4', historical=['LUFFY-PORTFOLIO-ALLOCATOR-R3', 'LUFFY-PORTFOLIO-ALLOCATOR-R2']),
        source_sha256={p: hashlib.sha256(git_bytes(ENG, p)).hexdigest() for p in [
            'trader/portfolio/allocator.py', 'tests/test_port01_whole_book.py', 'tests/capability_fixtures.py']},
        targeted_impact=impact,
        PORT04_dependency_satisfied=True, PORT04_now_eligible=True, PORT04_selected=False, PORT04_started=False,
        PORT04_auto_closed=False, PORT04_known_separate_gap='normal checkpoint does not supply relationship_sources to common_factor.attach',
        EXE04_ENGINEERING_COMMIT=EXE04_ENG, EXE04_engineering_parked=True, EXE04_canonical_closed=False,
        EXE04_canonical_still_pending=True, next_control_plane_item='EXE-04 canonical reconciliation',
        next_item_selected=False, application_code_changed=False, implementation_deployed=False,
        runtime_execution_authorized=False, provider_enablement_authorized=False, trading_activation_authorized=False,
        original_conditions_and_edges_preserved=True, status_counts_after=counts)
    write_json(EP/'closure.json', record)
    write_json(EP/'control-before.json', dict(tracker=prior, state=old('STATE.yaml'), next=old('NEXT.yaml')))
    row.update(status='CLOSED', implementation_commit=ENG, resolution_at_commit=ENG, updated='2026-10-08',
        implementation_evidence='Allocator R4 requires the frozen account/instrument entry capability for NEW_POSITION; whole-book/CASH/proposal-only path verified at '+ENG+'. '+EVID+'/closure.json',
        runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; no deployed/live/provider proof claimed',
        latest_evidence='27 dedicated passes; 80 integrated passes on DEC-03+R4; selected 42-file run 1369 passed/11 failed with the same 11 failures (names and signatures) on the pre-R4 base. Pre-R4 normal path produced a COMPLETE OPEN TradeIntent without capability; R4 selects CASH. R2/R3 replay bytes identical. No full-suite green claim.',
        next_proof='None for exact PORT-01 architecture/offline closure. PORT-04 dependency satisfied and eligible for its own relationship-source work; unselected/unstarted. Risk/order authority unchanged.',
        closure_evidence=EVID+'/closure.json', evidence_mapping=EVID+'/REPORT.md',
        evidence_validity='Pinned exact engineering commit and synthetic TEST-ONLY capability fixtures; no deployment, production calibration or live execution claim.')
    selection = copy.deepcopy(prior['current_build_selection'])
    selection.update(last_completed_item='PORT-01', last_terminal_item='PORT-01', next_recommended_item='EXE-04',
        selection_reason='PORT-01 CLOSED at architecture/offline scope (engineering 2ea4a95). PORT-04 dependency satisfied and eligible, unselected/unstarted. EXE-04 engineering 916de73 exists but its canonical reconciliation is pending and is the next control-plane item; EXE-04 row unchanged. No execution, Risk, venue, capital or trading authority.')
    for item in ('PORT-04',):
        if item not in selection['eligible_items']: selection['eligible_items'].append(item)
    metadata = copy.deepcopy(prior['metadata'])
    metadata.update(version=VERSION, current_active_item='PORT-01', status_counts=counts,
        next_recommended_item='EXE-04', last_terminal_item='PORT-01', last_terminal_evidence=EVID+'/closure.json')
    path = ROOT/TP; text = git_bytes(BASE, TP).decode()
    start = text.index('- id: PORT-01\n'); end = text.index('- id: PORT-02\n', start)
    text = text[:start]+yaml.safe_dump([row], sort_keys=False, allow_unicode=True, width=105)+text[end:]
    text = replace_block(text, 'metadata', metadata)
    text = replace_block(text, 'current_build_selection', selection)
    text += '\n'+yaml.safe_dump({'port01_canonical_reconciliation': record}, sort_keys=False, allow_unicode=True, width=105)
    path.write_text(text)
    package = dict(id='PORT-01', status='CLOSED', result='CLOSED_ARCHITECTURE_OFFLINE', mode='ENGINEERING_ONLY',
        reconciliation_mode='CONTROL_PLANE_ONLY', scope='ARCHITECTURE_OFFLINE', objective=row['required_behavior'],
        implementation_commit=ENG, verification_revision=ENG, implementation_complete=True,
        canonical_reconciliation_pending=False, closing_condition_satisfied=True,
        evidence=[EVID+'/closure.json', EVID+'/REPORT.md', EVID+'/fresh-port01.xml'],
        runtime_execution_authorized=False, provider_enablement_authorized=False, trading_activation_authorized=False,
        successor_selected=False, next_action='EXE-04 canonical reconciliation (engineering 916de73) is the next control-plane item; PORT-04 eligible, unselected. No execution authority.')
    for path, key in [('STATE.yaml', 'current_active_work'), ('NEXT.yaml', 'work_package')]:
        text = replace_block(git_bytes(BASE, path).decode(), key, package)
        text = replace_block(text, 'current_build_selection', selection)
        text += '\n'+yaml.safe_dump({'port01_previous_selection': old(path)[key], 'port01_canonical_reconciliation': record}, sort_keys=False, allow_unicode=True, width=105)
        (ROOT/path).write_text(text)
    banner = '> Current [SOL-1][PORT-01]: **CLOSED at architecture/offline scope** on `2ea4a95` (allocator R4). Pre-R4 normal path emitted a COMPLETE OPEN TradeIntent that reached Risk without frozen entry-capability evidence; R4 requires exactly one current eligible frozen capability for NEW_POSITION, else CASH. 27 dedicated + 80 integrated passes; the same 11 unrelated failures on the pre-R4 base; no full-suite green claim. R2/R3 replay byte-identical. PORT-04 dependency satisfied, eligible, unselected (separate relationship-source gap). EXE-04 engineering `916de73` exists; its canonical reconciliation is still pending. No activation authority. [Evidence](evidence/port01-canonical-sol1/closure.json). Earlier summaries below are historical.\n\n'
    path = ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md'; text = git_bytes(BASE, 'docs/tracker/LUFFY_Product_Tracker_v1.md').decode()
    start = text.index('### PORT-01 —'); end = text.index('### PORT-02 —', start); section = text[start:end]
    for k, v in row.items():
        if rows['PORT-01'].get(k) == v: continue
        pattern = r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$'; line = '- **'+k+'**: '+str(v)
        if re.search(pattern, section): section = re.sub(pattern, lambda m: line, section)
        else: section = section.rstrip()+'\n'+line+'\n\n'
    text = text[:start]+section+text[end:]
    text = text.replace('Version: '+prior['metadata']['version'], 'Version: '+VERSION, 1)
    path.write_text(banner+text+'\nPORT-01 canonical status counts: '+', '.join(f'{k} {v}' for k, v in counts.items())+' (178 total).\n')
    (ROOT/'docs/tracker/README.md').write_text(banner+git_bytes(BASE, 'docs/tracker/README.md').decode())
    report = '# PORT-01 canonical CLOSED\n\n'+row['closure_condition']+'\n\n'+row['failure_regression_proof']+'\n\n'
    report += 'Engineering `'+ENG+'` (base `'+ENG_BASE+'`, branch port01-whole-book); control baseline `'+BASE+'`. Only PORT-01 closes; the other 177 rows and readiness gates are unchanged.\n\n'
    for key, value in MAPPING.items(): report += key.replace('_', ' ').capitalize()+': '+value+'\n\n'
    report += 'Verification: 27 dedicated PORT-01 passes; 80 passes on a verification merge of R4 onto DEC-03 engineering 1ec9870 (21 DEC-03, 25 DEC-02, 27 PORT-01, 7 normal sources); selected 42-file run: R4 tree 1369 passed / 11 failed, clean base 1342 passed / 11 failed with identical failed names and signatures (10 JSONDecodeError, 1 Kernel SimpleNamespace.state). Counts overlap; do not sum. The engineering report of 1813 passed / 9 failed used a different selection whose 9 names are not enumerated in any retrievable artifact; the 9 launch_preflight failures are consistent but that equivalence is unverified. No full-suite green claim.\n\n'
    report += 'Impact: '+' '.join(k+': '+v for k, v in impact.items())+'\n\n'
    report += 'EXE-04: engineering commit `'+EXE04_ENG+'` is parked; the row is unchanged and not canonically closed; its reconciliation is the next control-plane item. No Kernel/Dashboard action, provider/venue call, deployment or trading activation. Status counts: '+str(counts)+', 178 total. Control-plane commit is the enclosing Git commit.\n'
    (EP/'REPORT.md').write_text(report)
    m = json.loads(git_bytes(BASE, 'docs/tracker/bundle-manifest.json'))
    m.update(snapshot_version=VERSION, control_baseline_revision=BASE, status_counts=counts,
        current_build_selection=selection, port01_canonical_reconciliation=record)
    write_json(ROOT/'docs/tracker/bundle-manifest.json', m)


def verify_control():
    prior = old(TP); t = load(TP); s = load('STATE.yaml'); n = load('NEXT.yaml')
    Ledger.model_validate(t); NextDocument.model_validate(n)
    old_rows = {r['id']: r for r in prior['items']}; rows = {r['id']: r for r in t['items']}
    assert len(rows) == 178 and rows.keys() == old_rows.keys()
    assert [r for r in rows if rows[r] != old_rows[r]] == ['PORT-01']
    for k, v in old_rows['PORT-01'].items():
        if k not in ['status', 'implementation_evidence', 'runtime_evidence', 'latest_evidence', 'next_proof',
                     'resolution_at_commit', 'updated', 'closure_evidence', 'evidence_validity']:
            assert rows['PORT-01'][k] == v, k
    assert rows['PORT-01']['status'] == 'CLOSED'
    assert all(rows[d]['status'] == 'CLOSED' for d in rows['PORT-01']['dependencies'])
    assert all(rows[d]['status'] == 'CLOSED' for d in rows['PORT-04']['dependencies'])
    for rid in ('PORT-04', 'EXE-04', 'DEC-02', 'DEC-03', 'PORT-02', 'DEC-04', 'RISK-01'):
        assert rows[rid] == old_rows[rid], rid
    assert rows['EXE-04']['status'] == 'EVIDENCE_TO_MAP' and rows['PORT-04']['status'] == 'EVIDENCE_TO_MAP'
    assert t['readiness_gates'] == prior['readiness_gates']
    for k, v in prior.items():
        if k not in ['metadata', 'items', 'current_build_selection']: assert t[k] == v, k
    record = json.loads((EP/'closure.json').read_text()); counts = dict(Counter(r['status'] for r in rows.values()))
    assert record['engineering_commit'] == ENG and record['control_baseline_revision'] == BASE
    assert record['closing_condition_satisfied'] and record['application_code_changed'] is False
    assert record['EXE04_ENGINEERING_COMMIT'] == EXE04_ENG and record['EXE04_canonical_closed'] is False
    for path, sha in record['source_sha256'].items():
        assert hashlib.sha256(git_bytes(ENG, path)).hexdigest() == sha, path
    for k, v in prior['metadata'].items():
        if k not in ['version', 'current_active_item', 'status_counts', 'next_recommended_item',
                     'last_terminal_item', 'last_terminal_evidence']: assert t['metadata'][k] == v, k
    assert counts == t['metadata']['status_counts'] == record['status_counts_after']
    assert counts == {'CLOSED': 65, 'EVIDENCE_TO_MAP': 74, 'DEFERRED': 7, 'AWAITING_EVIDENCE': 9, 'AWAITING_OWNER': 14, 'BLOCKED': 5, 'OPEN': 4}
    assert t['port01_canonical_reconciliation'] == record
    sel = t['current_build_selection']
    assert sel['next_recommended_item'] == 'EXE-04' and 'PORT-04' in sel['eligible_items'] and 'EXE-04' in sel['eligible_items']
    assert sel['last_completed_item'] == 'PORT-01' and not sel['active_items'] and not sel['next_item_selected']
    assert not sel['runtime_execution_authorized']
    for doc, path, key in [(s, 'STATE.yaml', 'current_active_work'), (n, 'NEXT.yaml', 'work_package')]:
        earlier = old(path)
        for k, v in earlier.items():
            if k not in [key, 'current_build_selection']: assert doc[k] == v, (path, k)
        assert doc['port01_previous_selection'] == earlier[key]
        assert doc['port01_canonical_reconciliation'] == record and doc['current_build_selection'] == sel
        assert doc[key]['id'] == 'PORT-01' and doc[key]['status'] == 'CLOSED'
        assert all(doc[key][k] is False for k in ['runtime_execution_authorized', 'provider_enablement_authorized', 'trading_activation_authorized', 'successor_selected'])
    md = (ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
    old_md = git_bytes(BASE, 'docs/tracker/LUFFY_Product_Tracker_v1.md').decode()
    for rid, row in rows.items():
        marker = '### '+rid+' —'
        if marker not in old_md: continue
        section = md[md.index(marker):].split('\n### ', 1)[0]
        if rid == 'PORT-01':
            for k in ['status', 'closure_condition', 'failure_regression_proof', 'next_proof', 'implementation_commit']:
                assert '- **'+k+'**: '+row[k] in section, k
        else: assert section.startswith(old_md[old_md.index(marker):].split('\n### ', 1)[0].rstrip()), rid
    manifest = json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
    assert manifest['snapshot_version'] == t['metadata']['version'] == VERSION
    assert manifest['status_counts'] == counts and manifest['row_count'] == 178
    assert manifest['current_build_selection'] == sel and manifest['port01_canonical_reconciliation'] == record
    assert len({f['path'] for f in manifest['files']}) == len(manifest['files'])
    for entry in manifest['files']:
        raw = (ROOT/entry['path']).read_bytes()
        assert entry['bytes'] == len(raw) and entry['sha256'] == hashlib.sha256(raw).hexdigest(), entry['path']
    view = read_tracker(ROOT)
    assert view['status'] == 'AVAILABLE' and view['selected']['id'] == 'PORT-01' and view['selected']['status'] == 'CLOSED'
    assert all(view['counts'][k] == v for k, v in counts.items())
    allowed = [TP, 'docs/tracker/LUFFY_Product_Tracker_v1.md', 'docs/tracker/README.md',
               'docs/tracker/bundle-manifest.json', 'STATE.yaml', 'NEXT.yaml']
    changed = subprocess.check_output(['git', 'diff', '--name-only', BASE, 'HEAD'], cwd=ROOT, text=True).splitlines()
    changed += subprocess.check_output(['git', 'diff', '--name-only', '--'] + allowed + [EVID], cwd=ROOT, text=True).splitlines()
    assert all(p in allowed or p.startswith(EVID+'/') for p in changed), changed
    return dict(PORT01_canonical='CLOSED', status_counts=counts, row_count=178, changed_rows=['PORT-01'],
        other_177_rows_unchanged=True, original_conditions_edges_and_readiness_gates_preserved=True,
        strict_YAML_contracts=True, manifest_counts_bytes_hashes_valid=True, dashboard_read='AVAILABLE',
        engineering_commit=ENG, DEC02_unchanged_CLOSED=True, DEC03_unchanged_CLOSED=True, PORT04_row_unchanged=True,
        PORT04_dependency_satisfied=True, PORT04_now_eligible=True, PORT04_selected=False,
        EXE04_ENGINEERING_COMMIT=EXE04_ENG, EXE04_row_unchanged=True, EXE04_canonical_still_pending=True,
        application_code_changed=False, runtime_execution_authorized=False)


def test_port01_canonical_reconciliation():
    proof = engineering_proof()
    if os.environ.get('PORT01_RECONCILE', 'VERIFY') == 'APPLY':
        reconcile(proof)
        write_json(EP/'control-checks.json', {})
        refresh_manifest()
        checks = verify_control()
        write_json(EP/'control-checks.json', checks)
        refresh_manifest()
    checks = verify_control()
    assert checks == json.loads((EP/'control-checks.json').read_text())
    assert json.loads((EP/'closure.json').read_text())['validation'] == proof
    print(json.dumps(checks, indent=2))
