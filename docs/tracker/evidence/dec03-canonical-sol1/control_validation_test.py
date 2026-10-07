"""DEC-03 control-only reconciliation and reproducible pinned verification.

Run with luffy-pytest. DEC03_RECONCILE=APPLY performs the explicitly authorized
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
BASE = '37d210787c448053c563c92436d9ac17ab672e12'
ENG = '1ec9870a09868d108293b10c3417ffd14368ed2e'
TP = 'docs/tracker/LUFFY_Product_Tracker_v1.yaml'
VERSION = '1.56-dec03-canonical-closed-sol1'


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


def engineering_proof():
    subprocess.check_call(['git', 'merge-base', '--is-ancestor', BASE, ENG], cwd=ROOT)
    names = subprocess.check_output(['git', 'diff', '--name-only', BASE, ENG], cwd=ROOT, text=True).splitlines()
    application = [p for p in names if not p.startswith('docs/')]
    assert set(application) == {'trader/portfolio/current.py', 'trader/portfolio/runtime.py',
        'trader/portfolio/position_evidence.py', 'tests/test_dec03_position_evidence.py'}
    assert all(p.startswith('docs/superpowers/evidence/dec03-engineering/') for p in names if p.startswith('docs/'))
    report = json.loads((EP/'engineering-report.json').read_text())
    assert (EP/'engineering-report.json').read_bytes() == git_bytes(ENG, 'docs/superpowers/evidence/dec03-engineering/report.json')
    assert report['base'] == BASE and report['item'] == 'DEC-03'
    for retained, original in [('retained-core.xml','core.xml'), ('retained-latest-lineage.xml','latest-lineage.xml'),
            ('retained-boundaries.xml','boundaries.xml'), ('retained-baseline-provenance.xml','baseline-provenance.xml')]:
        assert (EP/retained).read_bytes() == git_bytes(ENG, 'docs/superpowers/evidence/dec03-engineering/'+original)
    suite('retained-core.xml', 208)
    suite('retained-latest-lineage.xml', 28)
    final = suite('retained-boundaries.xml', 97, 24)
    baseline = suite('retained-baseline-provenance.xml', 46, 24)
    def failures(s):
        return {c.get('classname')+'::'+c.get('name'): c.find('failure')
                for c in s.findall('testcase') if c.find('failure') is not None}
    a, b = failures(final), failures(baseline)
    assert set(a) == set(b) and len(a) == 24
    reference = 'tests.test_trade_provenance::test_kernel_population_identity_matches_the_evaluator_that_signalled'
    for name in a:
        assert a[name].get('message') == b[name].get('message'), name
        if name == reference:
            assert all('observed_at' in f.text and 'Right contains 1 more item' in f.text for f in (a[name],b[name]))
        elif a[name].get('message', '').startswith('IndexError:'):
            assert all('list index out of range' in f.text for f in (a[name],b[name]))
        elif a[name].get('message', '').startswith('AttributeError:'):
            assert all("'NoneType' object has no attribute 'id'" in f.text for f in (a[name],b[name]))
        else:
            assert all('assert None is not None' in f.text for f in (a[name],b[name]))
    unaffected = ['tests/test_trade_provenance.py', 'trader/engine/trade_provenance.py',
        'trader/engine/executor.py', 'trader/engine/entry_authority.py', 'trader/kernel.py',
        'trader/portfolio/allocator.py', 'trader/portfolio/common_factor.py', 'trader/portfolio/trade_intent.py',
        'trader/engine/risk.py', 'trader/engine/risk_intent.py', 'trader/strategy/factory_handoff.py',
        'trader/engine/versioned_exits.py', 'config.yaml', 'SDD.md']
    for path in unaffected:
        assert git_bytes(BASE, path) == git_bytes(ENG, path), path
        assert (ROOT/path).read_bytes() == git_bytes(BASE, path), path
    fresh = suite('fresh.xml', 79)
    by_file = dict(Counter(c.get('classname') for c in fresh.findall('testcase')))
    assert by_file == {'tests.test_dec03_position_evidence':21, 'tests.test_dec02_comparison':25,
        'tests.test_str04_governor_lifecycle':26, 'tests.test_stage6_normal_sources':7}
    p = git_bytes(ENG, 'trader/portfolio/position_evidence.py').decode()
    runtime = git_bytes(ENG, 'trader/portfolio/runtime.py').decode()
    current = git_bytes(ENG, 'trader/portfolio/current.py').decode()
    assert "account_scope=str(Path(journal).resolve())" in current
    for token in ["account_position_id=digest(key)", "booked_position_id=binding['trade_id']",
                  "C.evidence_groups", "POSITION_LINEAGE_UNAVAILABLE", "POSITION_EXIT_PLAN_DIFFERS",
                  "REFUSED_UNRECONCILED", "current=False", "authority='OBSERVATION_ONLY'"]:
        assert token in p, token
    assert '._tx(' not in p and '.execute(' not in p
    assert "SCHEMA = 'runtime-portfolio-checkpoint.v2'" in runtime
    assert "LEGACY_SCHEMA = 'runtime-portfolio-checkpoint.v1'" in runtime
    assert "RUNTIME_POSITION_EVIDENCE_MISSING" in runtime and "RUNTIME_POSITION_EVIDENCE_REPLAY_REFUSED" in runtime
    return dict(fresh_passed=79, fresh_by_file=by_file, retained_core_passed=208,
        retained_latest_lineage_passed=28, expanded_passed=73, expanded_failed=24,
        baseline_passed=22, baseline_failed=24, identical_failure_names=sorted(a),
        baseline_failure_categories={'legacy_entry_fixture_refusal':23, 'reference_observed_at_shape':1},
        unrelated_files_unchanged=unaffected, counts_overlap_do_not_sum=True, full_suite_green_claim=False,
        runner='/home/sarmad/.local/bin/luffy-pytest', temp_root='/mnt/luffy-data/test-tmp')


def replace_block(text, key, value):
    match = re.search(r'(?m)^'+re.escape(key)+r':\n', text)
    assert match, key
    following = re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:', text[match.end():])
    end = match.end()+following.start() if following else len(text)
    return text[:match.start()]+yaml.safe_dump({key:value}, sort_keys=False, allow_unicode=True, width=105)+'\n'+text[end:]


def refresh_manifest():
    path = ROOT/'docs/tracker/bundle-manifest.json'
    m = json.loads(path.read_text())
    existing = {e['path'] for e in m['files']}
    for file in sorted(EP.iterdir()):
        if file.is_file() and str(file.relative_to(ROOT)) not in existing:
            m['files'].append(dict(path=str(file.relative_to(ROOT)), role='DEC03_CANONICAL_CONTROL_OR_EVIDENCE', synchronization_required=True))
    for entry in m['files']:
        raw = (ROOT/entry['path']).read_bytes()
        entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    write_json(path, m)


def reconcile(proof):
    prior = old(TP)
    rows = {r['id']:r for r in prior['items']}
    row = copy.deepcopy(rows['DEC-03'])
    assert rows['DEC-02']['status'] == rows['STR-04']['status'] == 'CLOSED'
    assert row['status'] == 'EVIDENCE_TO_MAP'
    counts = dict(Counter(r['status'] for r in prior['items']))
    counts['CLOSED'] += 1; counts['EVIDENCE_TO_MAP'] -= 1
    mapping = {
        'position_identity':'Coherent journal account scope + environment + full canonical instrument + market form one stable account_position_id. Original booked trades.id is retained separately as the episode reference; candidates/cycles/confidence cannot regenerate it. Distinct account/instrument/environment scopes remain distinct. The existing account scope is the resolved journal path; relocation is not registered account-migration authority.',
        'agreeing_strategies':'Existing Portfolio selects one same-instrument expression with bounded size, not summed candidate exposure. Every exact strategy/version remains inspectable. Existing holdings bind their original approved entry plan, not an incoming candidate version. No additional comparator, weighting or vote-count authority.',
        'opposing_strategies':'Opposing version identities, directions, candidate evidence, lineage and proposal refusal reasons persist. Existing Portfolio conflict/CASH/KEEP policy remains authoritative; no consensus is fabricated and no opposite position is created.',
        'lineage_independence':'Exact duplicates contribute once. Existing common-factor root groups retain parent/child and siblings; full ancestor Research/QNT records remain visible, including shared upstream evidence across roots. Distinct registered roots/strategies remain distinct evidence. independent_evidence_count is null and confidence_aggregation is NONE.',
        'position_stability':'Confidence/evidence_quality changes do not change the held side/quantity, original booked identity or approved entry-plan receipt. Existing Portfolio admits no new size for SUPPORTS_EXISTING and TradeIntent KEEP has no requested size. The evidence module has no journal/position write API. Stops, targets, trails, partial and scale-in geometry cannot be rewritten by it; detailed execution authority remains EXE-04.',
        'exit_plan_binding':'Frozen entry identity re-verifies exact StrategyVersion, strategy/spec hash, historical install, validation, exit semantics and exact entry exit_state. Existing pure exit initialize/decode checks the frozen policy against that exact version; wrong identity/geometry refuses. No geometry redesign. Legacy/adopted positions stay UNKNOWN.',
        'cash_risk_unreconciled':'Only independent venue truth supplies position rows. CASH, invalid/rejected candidates, missing economics and Risk refusal never create holdings. Unreconciled observed holdings stay visible with REFUSED_UNRECONCILED attribution and no fabricated booked id/plan. UNKNOWN remains UNKNOWN.',
        'replay':'Reorder and a new-process replay preserve exact representation; immutable evaluations retain earlier supporting/opposing truth while later cuts append. Missing current validated lineage or corrupt/mismatched supplied lineage refuses. Historical v1 checkpoint receipts replay unchanged; v2 mandates the position receipt and verifies exact recomputation.',
        'authority':'Observation-only binding; no execution/Risk/Portfolio expansion/Governor/owner/venue/exit mutation authority. No runtime launch, provider/venue calls or live activation. Control documents reference the engineering commit; implementation has not been deployed or merged by this reconciliation.'}
    record = dict(item='DEC-03', canonical_status='CLOSED', scope='ARCHITECTURE_OFFLINE',
        engineering_commit=ENG, control_baseline_revision=BASE,
        control_plane_commit_resolution='Enclosing Git commit; no self-referential hash.',
        closing_condition=row['closure_condition'], failure_regression_proof=row['failure_regression_proof'],
        closing_condition_satisfied=True, closure_mapping=mapping, validation=proof,
        receipt_contract_versions=dict(runtime_checkpoint='runtime-portfolio-checkpoint.v2',
            historical_runtime_checkpoint='runtime-portfolio-checkpoint.v1', position_evidence='account-position-evidence.v1',
            position_bindings='account-position-bindings.v1', strategy_lineage='strategy-lineage-context.v1',
            event='portfolio-reoptimization.v1', allocator='LUFFY-PORTFOLIO-ALLOCATOR-R3',
            historical_allocator='LUFFY-PORTFOLIO-ALLOCATOR-R2', intent='portfolio-trade-intent.v1',
            risk='portfolio-risk-decision.v1', governor='strategy-governor-event.v2'),
        source_sha256={p:hashlib.sha256(git_bytes(ENG,p)).hexdigest() for p in [
            'trader/portfolio/position_evidence.py','trader/portfolio/current.py','trader/portfolio/runtime.py',
            'tests/test_dec03_position_evidence.py']},
        targeted_impact={'DEC-02':'Unchanged CLOSED; R3 exact ties choose CASH and R2 replay stays exact.',
            'STR-04':'Unchanged CLOSED; Governor implementation/events/owner binding untouched.',
            'PORT-01':'Row unchanged; no expansion or comparator authority granted. Dirty parallel allocator/shared tests were avoided; retest the position receipt when integrating PORT-01.',
            'Risk_order':'Unchanged isolated Risk handoff; no order/execution route added.',
            'EXE-04':'STR-01 and DEC-03 CLOSED: dependency satisfied and eligible for its own evidence mapping. Row remains EVIDENCE_TO_MAP; unselected, unstarted, not auto-closed.'},
        EXE04_dependency_satisfied=True, EXE04_now_eligible=True, EXE04_selected=False, EXE04_started=False,
        PORT01_auto_closed=False, next_critical_path_item='EXE-04', next_item_selected=False,
        application_code_changed=False, implementation_deployed=False,
        runtime_execution_authorized=False, provider_enablement_authorized=False,
        trading_activation_authorized=False, exit_plan_mutation_authorized=False,
        original_conditions_and_edges_preserved=True, status_counts_after=counts)
    write_json(EP/'closure.json', record)
    write_json(EP/'control-before.json', dict(tracker=prior, state=old('STATE.yaml'), next=old('NEXT.yaml')))
    row.update(status='CLOSED', implementation_commit=ENG, resolution_at_commit=ENG, updated='2026-10-08',
        implementation_evidence='Existing one-expression/KEEP/lineage semantics plus read-only account-position evidence binding at '+ENG+'. '+EVID+'/closure.json',
        runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; no deployed/live/provider proof claimed',
        latest_evidence='79 fresh targeted passes: 21 DEC03, 25 DEC02, 26 STR04 Governor, 7 normal sources. Retained 208 core and latest 28 passes overlap. Expanded 73/24 failures match 22/24 base provenance results: 23 entry fixture refusals and one observed_at shape mismatch; unrelated files unchanged. No full-suite green claim.',
        next_proof='None for exact DEC-03 architecture/offline closure. EXE-04 dependency satisfied and eligible for its own approved exit-execution mapping; recommended, unselected/unstarted. PORT-01 and Risk/order authority unchanged.',
        closure_evidence=EVID+'/closure.json', evidence_mapping=EVID+'/REPORT.md',
        evidence_validity='Pinned exact engineering commit and synthetic offline verification; no deployment, production calibration or live execution claim.')
    selection = copy.deepcopy(prior['current_build_selection'])
    selection.update(last_completed_item='DEC-03', last_terminal_item='DEC-03', next_recommended_item='EXE-04',
        selection_reason='DEC-03 exact account-position/strategy-evidence/approved-entry-plan boundary CLOSED at architecture/offline scope. EXE-04 dependencies STR-01/DEC-03 CLOSED; recommended for its own evidence mapping, unselected/unstarted. PORT-01 unchanged; no execution, Risk, capital, Governor, owner, venue or exit-mutation authority.')
    selection['eligible_items'] = [r for r in selection['eligible_items'] if r!='DEC-03']
    if 'EXE-04' not in selection['eligible_items']: selection['eligible_items'].append('EXE-04')
    metadata = copy.deepcopy(prior['metadata'])
    metadata.update(version=VERSION, current_active_item='DEC-03', status_counts=counts,
        next_recommended_item='EXE-04', last_terminal_item='DEC-03', last_terminal_evidence=EVID+'/closure.json')
    path = ROOT/TP; text = git_bytes(BASE, TP).decode()
    start = text.index('- id: DEC-03\n'); end = text.index('- id: DEC-04\n', start)
    text = text[:start]+yaml.safe_dump([row], sort_keys=False, allow_unicode=True, width=105)+text[end:]
    text = replace_block(text, 'metadata', metadata)
    text = replace_block(text, 'current_build_selection', selection)
    text += '\n'+yaml.safe_dump({'dec03_canonical_reconciliation':record}, sort_keys=False, allow_unicode=True, width=105)
    path.write_text(text)
    package = dict(id='DEC-03', status='CLOSED', result='CLOSED_ARCHITECTURE_OFFLINE', mode='ENGINEERING_ONLY',
        reconciliation_mode='CONTROL_PLANE_ONLY',
        scope='ARCHITECTURE_OFFLINE', objective=row['required_behavior'], implementation_commit=ENG,
        verification_revision=ENG, implementation_complete=True, canonical_reconciliation_pending=False,
        closing_condition_satisfied=True, evidence=[EVID+'/closure.json',EVID+'/REPORT.md',EVID+'/fresh.xml'],
        runtime_execution_authorized=False, provider_enablement_authorized=False, trading_activation_authorized=False,
        successor_selected=False, next_action='EXE-04 dependency satisfied and eligible; recommended for evidence mapping only, unselected/unstarted. No execution or exit mutation authority.')
    for path,key in [('STATE.yaml','current_active_work'), ('NEXT.yaml','work_package')]:
        text = replace_block(git_bytes(BASE, path).decode(), key, package)
        text = replace_block(text, 'current_build_selection', selection)
        text += '\n'+yaml.safe_dump({'dec03_previous_selection':old(path)[key], 'dec03_canonical_reconciliation':record}, sort_keys=False, allow_unicode=True, width=105)
        (ROOT/path).write_text(text)
    banner = '> Current [SOL-1][DEC-03]: **CLOSED at architecture/offline scope** on `1ec9870`. One canonical account/instrument holding; exact supporting/opposing strategy evidence and approved entry-plan binding retained; ancestry creates no independence authority. 79 fresh targeted passes; retained counts overlap. Same 24 unrelated provenance failures on base; no full-suite green claim. EXE-04 dependency satisfied and eligible, recommended but unselected/unstarted. PORT-01 unchanged. No activation or exit-mutation authority. [Evidence](evidence/dec03-canonical-sol1/closure.json). Earlier summaries below are historical.\n\n'
    path = ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md'; text = git_bytes(BASE, 'docs/tracker/LUFFY_Product_Tracker_v1.md').decode()
    start=text.index('### DEC-03 —'); end=text.index('### DEC-04 —',start); section=text[start:end]
    for k,v in row.items():
        if rows['DEC-03'].get(k)==v: continue
        pattern=r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$'; line='- **'+k+'**: '+str(v)
        if re.search(pattern,section): section=re.sub(pattern,lambda m:line,section)
        else: section=section.rstrip()+'\n'+line+'\n\n'
    text=text[:start]+section+text[end:]
    text=text.replace('Version: '+prior['metadata']['version'], 'Version: '+VERSION,1)
    path.write_text(banner+text+'\nDEC-03 canonical status counts: '+', '.join(f'{k} {v}' for k,v in counts.items())+' (178 total).\n')
    path=ROOT/'docs/tracker/README.md'; path.write_text(banner+git_bytes(BASE, 'docs/tracker/README.md').decode())
    report = '# DEC-03 canonical CLOSED\n\n'+row['closure_condition']+'\n\n'+row['failure_regression_proof']+'\n\n'
    report += 'Engineering `'+ENG+'`; canonical prerequisite baseline `'+BASE+'`. Only DEC-03 closes; the other 177 rows and readiness gates are unchanged.\n\n'
    for key,value in mapping.items(): report += key.replace('_',' ').capitalize()+': '+value+'\n\n'
    report += 'Fresh verification: 79 passed (21 DEC03, 25 DEC02, 26 STR04 Governor, 7 normal sources). Retained 208 core passes and latest 28 passes overlap; do not sum. Expanded run: 73 passed / 24 failed. Untouched base provenance: 22 passed / the same 24 failed. Twenty-three failures concern legacy entry fixtures: 21 direct None assertions, one empty recovered-entry list and one None position id; one is the unchanged reference observed_at shape mismatch. Exact failed-name sets/signatures and unchanged relevant source bytes are verified. No full-suite green claim.\n\n'
    report += 'Receipt contracts: runtime-portfolio-checkpoint.v2; historical runtime-portfolio-checkpoint.v1; account-position-evidence.v1; account-position-bindings.v1. Existing event contract portfolio-reoptimization.v1 and default allocator R3 remain unchanged; R2 history supported. v2 receipt verification is mandatory; no historical backfill.\n\n'
    report += 'EXE-04 dependencies STR-01 and DEC-03 are CLOSED: eligible and recommended, unselected/unstarted; its own condition remains unmapped. PORT-01 row and authority remain unchanged. Risk, order, Governor and owner authority remain unchanged. No Kernel/Dashboard launch, provider/venue calls or implementation deployment. Current runtime truth is not inferred from this offline closure.\n\n'
    report += '[Closure](closure.json), [fresh checks](fresh.xml), [control validation](control-checks.json). Status counts: '+str(counts)+', 178 total. Control-plane commit is the enclosing Git commit.\n'
    (EP/'REPORT.md').write_text(report)
    m=json.loads(git_bytes(BASE, 'docs/tracker/bundle-manifest.json'))
    m.update(snapshot_version=VERSION, control_baseline_revision=BASE, status_counts=counts,
        current_build_selection=selection, dec03_canonical_reconciliation=record)
    write_json(ROOT/'docs/tracker/bundle-manifest.json', m)


def verify_control():
    prior=old(TP); t=load(TP); s=load('STATE.yaml'); n=load('NEXT.yaml')
    Ledger.model_validate(t); NextDocument.model_validate(n)
    old_rows={r['id']:r for r in prior['items']}; rows={r['id']:r for r in t['items']}
    assert len(rows)==178 and rows.keys()==old_rows.keys()
    assert [r for r in rows if rows[r]!=old_rows[r]]==['DEC-03']
    for k,v in old_rows['DEC-03'].items():
        if k not in ['status','implementation_evidence','runtime_evidence','latest_evidence','next_proof',
            'resolution_at_commit','updated','closure_evidence','evidence_validity']:
            assert rows['DEC-03'][k]==v, k
    assert rows['DEC-03']['status']=='CLOSED'
    assert all(rows[d]['status']=='CLOSED' for d in rows['DEC-03']['dependencies'])
    assert all(rows[d]['status']=='CLOSED' for d in rows['EXE-04']['dependencies'])
    assert rows['EXE-04']==old_rows['EXE-04'] and rows['PORT-01']==old_rows['PORT-01']
    assert rows['DEC-02']==old_rows['DEC-02'] and rows['STR-04']==old_rows['STR-04']
    assert t['readiness_gates']==prior['readiness_gates']
    for k,v in prior.items():
        if k not in ['metadata','items','current_build_selection']: assert t[k]==v,k
    record=json.loads((EP/'closure.json').read_text()); counts=dict(Counter(r['status'] for r in rows.values()))
    assert record['engineering_commit']==ENG and record['control_baseline_revision']==BASE
    assert record['closing_condition_satisfied'] and record['application_code_changed'] is False
    for path,sha in record['source_sha256'].items():
        assert hashlib.sha256(git_bytes(ENG,path)).hexdigest()==sha,path
    for k,v in prior['metadata'].items():
        if k not in ['version','current_active_item','status_counts','next_recommended_item',
            'last_terminal_item','last_terminal_evidence']: assert t['metadata'][k]==v,k
    assert counts==t['metadata']['status_counts']==record['status_counts_after']
    assert counts=={'CLOSED':64,'EVIDENCE_TO_MAP':75,'DEFERRED':7,'AWAITING_EVIDENCE':9,'AWAITING_OWNER':14,'BLOCKED':5,'OPEN':4}
    assert t['dec03_canonical_reconciliation']==record
    sel=t['current_build_selection']
    assert sel['next_recommended_item']=='EXE-04' and 'EXE-04' in sel['eligible_items']
    assert sel['last_completed_item']=='DEC-03' and not sel['active_items'] and not sel['next_item_selected']
    assert not sel['runtime_execution_authorized']
    for doc,path,key in [(s,'STATE.yaml','current_active_work'),(n,'NEXT.yaml','work_package')]:
        earlier=old(path)
        for k,v in earlier.items():
            if k not in [key,'current_build_selection']: assert doc[k]==v,(path,k)
        assert doc['dec03_previous_selection']==earlier[key]
        assert doc['dec03_canonical_reconciliation']==record and doc['current_build_selection']==sel
        assert doc[key]['id']=='DEC-03' and doc[key]['status']=='CLOSED'
        assert all(doc[key][k] is False for k in ['runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'])
    md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text()
    old_md=git_bytes(BASE,'docs/tracker/LUFFY_Product_Tracker_v1.md').decode()
    for rid,row in rows.items():
        marker='### '+rid+' —'
        if marker not in old_md: continue
        section=md[md.index(marker):].split('\n### ',1)[0]
        if rid=='DEC-03':
            for k in ['status','closure_condition','failure_regression_proof','next_proof','implementation_commit']:
                assert '- **'+k+'**: '+row[k] in section,k
        else: assert section.startswith(old_md[old_md.index(marker):].split('\n### ',1)[0].rstrip()),rid
    manifest=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
    assert manifest['snapshot_version']==t['metadata']['version']==VERSION
    assert manifest['status_counts']==counts and manifest['row_count']==178
    assert manifest['current_build_selection']==sel and manifest['dec03_canonical_reconciliation']==record
    assert len({f['path'] for f in manifest['files']})==len(manifest['files'])
    for entry in manifest['files']:
        raw=(ROOT/entry['path']).read_bytes()
        assert entry['bytes']==len(raw) and entry['sha256']==hashlib.sha256(raw).hexdigest(),entry['path']
    view=read_tracker(ROOT)
    assert view['status']=='AVAILABLE' and view['selected']['id']=='DEC-03' and view['selected']['status']=='CLOSED'
    assert all(view['counts'][k]==v for k,v in counts.items())
    changed=subprocess.check_output(['git','diff','--name-only',BASE,'HEAD'],cwd=ROOT,text=True).splitlines()
    changed += subprocess.check_output(['git','diff','--name-only','--',TP,'docs/tracker/LUFFY_Product_Tracker_v1.md',
        'docs/tracker/README.md','docs/tracker/bundle-manifest.json','STATE.yaml','NEXT.yaml',EVID],cwd=ROOT,text=True).splitlines()
    assert all(p in [TP,'docs/tracker/LUFFY_Product_Tracker_v1.md','docs/tracker/README.md',
        'docs/tracker/bundle-manifest.json','STATE.yaml','NEXT.yaml'] or p.startswith(EVID+'/') for p in changed), changed
    return dict(DEC03_canonical='CLOSED',status_counts=counts,row_count=178,changed_rows=['DEC-03'],
        other_177_rows_unchanged=True,original_conditions_edges_and_readiness_gates_preserved=True,
        strict_YAML_contracts=True,manifest_counts_bytes_hashes_valid=True,dashboard_read='AVAILABLE',
        engineering_commit=ENG,DEC02_unchanged_CLOSED=True,STR04_unchanged_CLOSED=True,PORT01_unchanged=True,
        EXE04_dependency_satisfied=True,EXE04_now_eligible=True,EXE04_selected=False,EXE04_started=False,
        next_critical_path_item='EXE-04',application_code_changed=False,runtime_execution_authorized=False)


def test_dec03_canonical_reconciliation():
    proof=engineering_proof()
    if os.environ.get('DEC03_RECONCILE','VERIFY')=='APPLY':
        reconcile(proof)
        write_json(EP/'control-checks.json', {})
        refresh_manifest()
        checks=verify_control()
        write_json(EP/'control-checks.json', checks)
        refresh_manifest()
    checks=verify_control()
    assert checks==json.loads((EP/'control-checks.json').read_text())
    assert json.loads((EP/'closure.json').read_text())['validation']==proof
    print(json.dumps(checks,indent=2))
