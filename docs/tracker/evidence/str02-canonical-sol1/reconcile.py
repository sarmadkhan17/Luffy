"""One-shot authorized STR-02 control-plane reconciliation, no implementation edits."""
from pathlib import Path
from collections import Counter
import copy, hashlib, json, re, subprocess
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
EVID = str(EP.relative_to(ROOT))
TP = ROOT / 'docs/tracker/LUFFY_Product_Tracker_v1.yaml'
MP = ROOT / 'docs/tracker/LUFFY_Product_Tracker_v1.md'
MAN = ROOT / 'docs/tracker/bundle-manifest.json'
HEAD = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
assert HEAD.startswith('843a0a3')
docs = {k: yaml.safe_load((ROOT / p).read_text()) for k, p in
        [('tracker', str(TP.relative_to(ROOT))), ('state', 'STATE.yaml'), ('next', 'NEXT.yaml')]}
(EP / 'control-before.json').write_text(json.dumps(docs, indent=2) + '\n')
rows = {r['id']: r for r in docs['tracker']['items']}
assert rows['STR-02']['status'] == 'BLOCKED'
assert all(rows[d]['status'] == 'CLOSED' for d in rows['STR-02']['dependencies'])
assert all(rows[d]['status'] == 'CLOSED' for d in ['WRLD-05', 'DATA-05'])
counts = dict(Counter(r['status'] for r in rows.values()))
counts['CLOSED'] += 1
counts['BLOCKED'] -= 1

def result(name):
    e = ET.parse(EP / name).getroot().find('testsuite')
    return {k: int(e.get(k, '0')) for k in ('tests', 'failures', 'errors', 'skipped')}

dedicated = result('dedicated.xml')
scope_world = result('scope-world.xml')
assert dedicated == dict(tests=42, failures=0, errors=0, skipped=0)
assert scope_world == dict(tests=37, failures=0, errors=0, skipped=0)
assert (EP / 'baseline-failures.txt').read_bytes() == (EP / 'final-failures.txt').read_bytes()
assert len((EP / 'final-failures.txt').read_text().splitlines()) == 31
paths = ['trader/strategy/compile.py', 'trader/kernel.py', 'trader/strategy/features.py',
         'trader/strategy/features_deriv.py', 'trader/strategy/dsl.py',
         'trader/strategy/factory_handoff.py', 'trader/engine/orchestrator.py',
         'trader/world/context.py', 'tests/test_str02_compile_binding.py',
         'tests/test_strategy_factory_handoff.py', 'tests/test_declared_universe_is_traded.py',
         'tests/test_wrld05_normal_context.py', 'config.yaml']
hashes = {}
for path in paths:
    raw = (ROOT / path).read_bytes()
    assert raw == subprocess.check_output(['git', 'show', HEAD + ':' + path], cwd=ROOT), path
    hashes[path] = hashlib.sha256(raw).hexdigest()
limitation = ('A pure implementation-only feature-code change is not automatically detected '
              'without a FEATURE_VERSION bump.')
record = dict(
    item='STR-02', canonical_status='CLOSED', scope='ARCHITECTURE_OFFLINE',
    engineering_commits=['d1ddc1826587b8accb4c36049d78743766056e5f', HEAD],
    engineering_closure_commit=HEAD, control_baseline_revision=HEAD,
    control_plane_commit_resolution='The enclosing Git commit containing this reconciliation; no self-referential hash.',
    evidence=EVID + '/closure.json', closing_condition=rows['STR-02']['closure_condition'],
    closing_condition_satisfied=True,
    closure_mapping={
        'exact_version': 'compile_version re-authenticates stored version/schema/typed lineage, exact spec hash, compiler/feature versions, validation and current install; absent/legacy/uninstalled/drifted/retired/rejected versions refuse. Kernel versioned population uses this path. Bare legacy research compile_spec remains supported without version provenance or authority.',
        'scope_logic': 'Frozen universe/include scope is bound into identity and normal Kernel genome; orchestrator symbol_allows enforces membership. Declared timeframe, long/short/filter/exit AST and derived data requirements execute in FeatureCtx. Exact kind/horizon/instrument queries use WorldContext. Dedicated and retained scope/WorldModel tests cover execution and cut refusals.',
        'exact_dependencies': 'AST-derived concrete derivative keys, ref keys and WorldModel kind/horizon are checked by shared missing_inputs. funding with OI only, OI with funding only, partial multi-derivative, absent required ref with unrelated refs, and absent exact world kind/horizon each yield named required_input_missing diagnostics.',
        'quality_availability': 'Closed eligible frames, point-in-time provenance/alignment, VALID quality and finite exact WorldModel dependencies gate evaluation. Missing timeframe and unsupported DSL/timeframe/horizon raise or diagnose explicitly; no fabricated zero inputs.',
        'current_historical': 'Live/replay/truncated history share FeatureCtx, entries_detail and missing_inputs. No future-cut WorldModel fallback; implicit WorldModel loading requires as_of_ms and calls load_context(as_of_ms=cut). Current latest row uses decision cut, replay rows their bar closes. Equal-cut parity and restart identity/output tests pass.',
        'identity': 'Compiled object binds version_id, spec_sha256, COMPILER_VERSION, FEATURE_VERSION, feature contract SHA and compile_identity (identity field; signal compile_identity). Material spec/dependency changes alter identity; spec/compiler/feature/registry drift refuses reuse.',
        'authority': 'Compilation is read-only; no install, lifecycle/Governor, owner approval, capital or order grant. Signals carry provenance only. Existing first-live fence and research controls remain authoritative.'},
    verification={
        'mandatory_runner': '/home/sarmad/.local/bin/luffy-pytest',
        'fresh_dedicated': dedicated, 'fresh_scope_world': scope_world,
        'fresh_commands': [
            '/home/sarmad/.local/bin/luffy-pytest tests/test_str02_compile_binding.py -q --junitxml=' + EVID + '/dedicated.xml',
            '/home/sarmad/.local/bin/luffy-pytest tests/test_declared_universe_is_traded.py tests/test_wrld05_normal_context.py -q --junitxml=' + EVID + '/scope-world.xml'],
        'retained_affected_run': {'passed': 565, 'failed': 31, 'existing_suites': 23,
                                 'dedicated_suite_also_included': True, 'seconds': 536.92},
        'baseline_revision': 'a3dfd82', 'baseline_final_failure_ids_equal': True,
        'new_failures_attributed_to_STR02': 0,
        'retained_validation_provenance': EVID + '/engineering-validation.json',
        'retained_failure_evidence': [EVID + '/baseline-failures.txt', EVID + '/final-failures.txt', EVID + '/affected-final.txt'],
        'all_pytest_runs_used_luffy_pytest': True,
        'pytest_temp_route': '/mnt/luffy-data/test-tmp/sarmad/pytest-run-*/base',
        'new_root_tmp_pytest_writes': False,
        'temp_proof_basis': 'Required runner creates unique /mnt basetemp and unsets temp env vars; retained engineering report/user evidence states no new root /tmp pytest writes. Existing historical /tmp pytest directory is not new STR-02 output.',
        'source_and_committed_bytes_match': True, 'full_suite_claim': False},
    feature_version_limitation=limitation,
    limitation_classification='NON_BLOCKING_FOR_EXACT_STR02_CONDITION',
    limitation_reasoning='The row requires reproducible feature/dependency semantics and explicit errors, fulfilled by exact frozen spec plus versioned compiler/feature contract identity. It does not require source/bytecode hashing or automatic detection of unversioned implementation changes. FEATURE_VERSION must be bumped when feature code changes; no hashing redesign authorized or performed.',
    dependency_status={d: rows[d]['status'] for d in rows['STR-02']['dependencies']},
    DEC01_dependencies_satisfied=True, DEC01_now_eligible=True,
    DEC01_dependency_status={'STR-02': 'CLOSED', 'WRLD-05': 'CLOSED', 'DATA-05': 'CLOSED'},
    DEC01_status='BLOCKED; own coherent Context proof remains open, eligible but unselected and not started',
    remaining_DEC01_conditions=[rows['DEC-01']['closure_condition'], rows['DEC-01']['failure_regression_proof'],
        'Complete normal required/optional analyst and supporting/opposing coherent Context binding; retained partial evidence is not full acceptance.'],
    original_condition_and_edges_preserved=True, unrelated_rows_unchanged=176,
    DEC01_status_unchanged=True, application_code_changed=False, DEC01_started=False,
    runtime_execution_authorized=False, provider_enablement_authorized=False,
    trading_activation_authorized=False, provider_calls=0, successor_selected=False,
    status_counts_after=counts, source_sha256=hashes)
(EP / 'closure.json').write_text(json.dumps(record, indent=2) + '\n')
row = copy.deepcopy(rows['STR-02'])
row.update(status='CLOSED', implementation_evidence='Exact authenticated StrategyVersion compiler/dependency identity at d1ddc18 and 843a0a3; ' + EVID + '/closure.json',
    runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; deployed/runtime proof not claimed',
    latest_evidence='42 dedicated tests passed; affected existing 23 suites plus dedicated suite: 565 passed / 31 failed, all 31 baseline failures reproduced and no new STR-02 failures. Fresh 42 dedicated and 37 scope/WorldModel tests passed. Exact dependency refusals and current/replay/historical parity verified. ' + limitation,
    next_proof='None for exact STR-02 architecture/offline condition. Preserve FEATURE_VERSION bump discipline. DEC-01 is dependency-eligible but unselected; its own normal coherent Context proof remains open.',
    resolution_at_commit=HEAD, implementation_commit=HEAD, updated='2026-10-07',
    closure_evidence=EVID + '/closure.json', evidence_mapping=EVID + '/REPORT.md',
    evidence_validity='Pinned architecture/offline verification at 843a0a3; no later deployed/runtime revalidation claim.')
dec = copy.deepcopy(rows['DEC-01'])
dec.update(implementation_evidence='Retained Opportunity Context identity/version/source-clock/book/cut/freshness controls mapped; required/optional analyst and supporting/opposing binding still needs proof. STR-02, WRLD-05 and DATA-05 are CLOSED.',
    latest_evidence='STR-02 canonical compiler/dependency closure removes the last direct dependency gate. DEC-01 remains BLOCKED on its own full normal source-path coherent Context proof. Dependency-eligible, unselected and not started by this reconciliation.',
    next_proof='Complete normal source-path canonical identity, exact spec, required/optional analysts, clocks, costs, book and supporting/opposing evidence binding; missing required blocks, optional absent does not block by default, mixed cuts refuse. DEC-01 is eligible but not selected or started.', updated='2026-10-07')

def replace_block(text, key, value):
    match = re.search(r'(?m)^' + re.escape(key) + r':\n', text)
    assert match, key
    end = re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:', text[match.end():])
    stop = match.end() + end.start() if end else len(text)
    return text[:match.start()] + yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, width=105) + '\n' + text[stop:]

selection = copy.deepcopy(docs['tracker']['current_build_selection'])
for key in ['last_completed_item', 'last_terminal_item']: selection[key] = 'STR-02'
selection['eligible_items'] = [x for x in selection['eligible_items'] if x != 'STR-02'] + ['DEC-01']
selection['current_blocked_items'] = [x for x in selection['current_blocked_items'] if x != 'STR-02']
selection['selection_reason'] = 'STR-02 exact compiler/dependency and parity condition reconciled CLOSED. DEC-01 direct dependencies satisfied; own Context proof remains open. No successor selected or started.'
package = dict(id='STR-02', status='CLOSED', result='CLOSED_ARCHITECTURE_OFFLINE', mode='ENGINEERING_ONLY', scope='ARCHITECTURE_OFFLINE',
    objective=row['required_behavior'], implementation_commit=HEAD, evidence=[EVID + '/REPORT.md', EVID + '/closure.json'],
    implementation_complete=True, canonical_reconciliation_pending=False, runtime_execution_authorized=False,
    provider_enablement_authorized=False, trading_activation_authorized=False, successor_selected=False)
text = TP.read_text()
for rid, nxt, new in [('STR-02', 'STR-03', row), ('DEC-01', 'DEC-02', dec)]:
    start=text.index('- id: '+rid+'\n'); end=text.index('- id: '+nxt+'\n', start)
    text=text[:start]+yaml.safe_dump([new],sort_keys=False,allow_unicode=True,width=105)+text[end:]
metadata = copy.deepcopy(docs['tracker']['metadata'])
metadata.update(version='1.48-str02-canonical-sol1', current_active_item='STR-02', status_counts=counts,
                last_terminal_item='STR-02', last_terminal_evidence=EVID+'/REPORT.md')
text=text.replace('version: 1.47-str01-canonical-sol1','version: 1.48-str02-canonical-sol1',1)
text=text.replace('current_active_item: STR-01','current_active_item: STR-02',1)
text=text.replace('last_terminal_item: STR-01','last_terminal_item: STR-02',1)
text=text.replace('last_terminal_evidence: docs/tracker/evidence/str01-canonical-sol1/REPORT.md',
                  'last_terminal_evidence: '+EVID+'/REPORT.md',1)
text=re.sub(r'  status_counts:\n(?:    [A-Z_]+: \d+\n)+',
            '\n'.join('  '+line for line in yaml.safe_dump({'status_counts':counts},sort_keys=False).splitlines())+'\n',text,count=1)
text=replace_block(text,'current_build_selection',selection)
text+='\n'+yaml.safe_dump({'str02_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105)
TP.write_text(text)
for which, path, active in [('state','STATE.yaml','current_active_work'),('next','NEXT.yaml','work_package')]:
    p=ROOT/path; text=p.read_text()
    text=replace_block(text,active,package)
    text=replace_block(text,'current_build_selection',selection)
    text+='\n'+yaml.safe_dump({'str02_previous_selection':docs[which][active], 'str02_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105)
    p.write_text(text)
banner='> Current [SOL-1][STR-02]: **CLOSED at architecture/offline scope** on `d1ddc18` / `843a0a3`. Exact StrategyVersion, declared dependency refusals, identity and current/historical parity verified. '+limitation+' DEC-01 dependencies are satisfied; its own Context proof remains open, unselected and not started. [Evidence](evidence/str02-canonical-sol1/REPORT.md). Earlier summaries below are historical.\n\n'
text=MP.read_text()
for rid,nxt,new in [('STR-02','STR-03',row),('DEC-01','DEC-02',dec)]:
    start=text.index('### '+rid+' —'); end=text.index('### '+nxt+' —',start); section=text[start:end]
    for key,value in new.items():
        if rows[rid].get(key)!=value:
            section=re.sub(r'(?m)^- \*\*'+re.escape(key)+r'\*\*:.*$',lambda m,k=key,v=value:'- **'+k+'**: '+str(v),section)
    text=text[:start]+section+text[end:]
text=banner+text+'\nSTR-02 canonical status counts: '+', '.join(f'{k} {v}' for k,v in counts.items())+' (178 total).\n'
MP.write_text(text)
p=ROOT/'docs/tracker/README.md';p.write_text(banner+p.read_text())
(EP/'REPORT.md').write_text('''# STR-02 canonical CLOSED

The exact condition is satisfied on engineering commits `d1ddc18` and `843a0a3`: “Compiler executes declared data/world dependencies and scope with reproducible parity and explicit unsupported/missing errors.” STR-01 and WRLD-05 are CLOSED. The condition, failure-regression requirement, source references and dependency edges are preserved.

`compile_version` authenticates the stored typed version, exact spec hash, schema, compiler/feature versions, validation receipt and current install. Missing, legacy, uninstalled, drifted, retired and rejected versions refuse. Kernel versioned population uses this path; bare legacy research specs can still use `compile_spec` without version provenance or authority. The compiled object binds version_id, spec_sha256, compiler/feature versions, feature contract hash and compile_identity. Spec/dependency or version changes alter identity or refuse stale reuse.

Entry/short/filter/exit AST, declared timeframe, concrete derivative/reference keys and exact WorldModel kind/horizon execute in the shared FeatureCtx. Frozen universe/include scope is attached to the normal Kernel genome and enforced by orchestrator symbol membership; this is also covered by the scope suite. Closed eligible frames, provenance cuts, quality and availability are preserved. Funding with only OI, OI with only funding, partial multi-derivative input, missing required reference while unrelated references exist, and missing exact world kind/horizon each refuse explicitly. Live/replay/truncated history use the same dependency check. World loading requires an explicit cut, queries refuse unavailable future cuts and there is no latest/uncut fallback. Replay/restart identity and outputs are deterministic for identical cuts/inputs; independently ingested provenance receipts are not claimed identical.

Fresh reconciliation runs through `/home/sarmad/.local/bin/luffy-pytest`: **42 dedicated passes**, plus **37 scope/WorldModel passes**. Retained engineering evidence records **565 passed / 31 failed** for 23 existing affected suites plus the dedicated STR-02 suite. All 31 final failure IDs exactly equal the before-change baseline list; no new STR-02 failures. The baseline was pre-STR-02 HEAD a3dfd82 with working changes stashed. Session command/result excerpts, final full log and both failure lists are retained here. These counts overlap and are not summed; no full-suite green claim. Every pytest command uses luffy-pytest, whose unique basetemp routes to /mnt/luffy-data/test-tmp/sarmad. No new root /tmp pytest writes; existing historical /tmp artifacts are not this work's output.

'''+limitation+''' This is **non-blocking for the exact STR-02 condition**: reproducibility uses the frozen spec plus versioned compiler/feature contract. The row does not require automatic source/bytecode hashing. Feature implementation changes require a FEATURE_VERSION bump. No redesign or implementation edit was performed.

Compilation/evaluation grants no install, Governor, owner, capital or order authority. Existing admission/first-live fences and disabled research.handoff/referee remain unchanged. This is architecture/offline proof, not deployment, profitability or live trading proof.

STR-02 changes BLOCKED to CLOSED. DEC-01's dependency notes are updated; its BLOCKED status and exact condition remain unchanged. All other 176 rows, readiness gates and runtime state are preserved. DEC-01 direct dependencies STR-02, WRLD-05 and DATA-05 are CLOSED, so it is eligible but unselected and not started. Remaining DEC-01 conditions: normal source path binds canonical identity, exact spec, required/optional analysts, clocks, costs, book and supporting/opposing evidence; missing required input blocks, optional absence does not block by default, mixed cuts refuse. Retained partial Context evidence is not full acceptance.

Status counts: CLOSED 58; EVIDENCE_TO_MAP 80; DEFERRED 7; AWAITING_EVIDENCE 9; AWAITING_OWNER 14; BLOCKED 6; OPEN 4 (178 total). The control-plane commit is the enclosing Git commit containing this report. See [closure](closure.json), [dedicated results](dedicated.xml), [scope/world results](scope-world.xml), [baseline comparison provenance](engineering-validation.json) and [control checks](control-checks.json).
''')
m=json.loads(MAN.read_text())
m.update(snapshot_version=metadata['version'],control_baseline_revision=HEAD,current_build_selection=selection,str02_canonical_reconciliation=record)
existing={f['path'] for f in m['files']}
for p in sorted(EP.iterdir()):
    rel=str(p.relative_to(ROOT))
    if p.is_file() and rel not in existing and p.name!='control-checks.json':
        m['files'].append(dict(path=rel,role='STR02_CANONICAL_CONTROL_OR_EVIDENCE',synchronization_required=True))
for entry in m['files']:
    raw=(ROOT/entry['path']).read_bytes();entry.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
MAN.write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps({'status_counts':counts,'DEC01_dependencies_satisfied':True,'DEC01_started':False}))
