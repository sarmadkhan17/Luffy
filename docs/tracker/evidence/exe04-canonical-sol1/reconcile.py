"""Reproduce EXE-04 control-only reconciliation; APPLY requires explicit env flag."""
from pathlib import Path
from collections import Counter
from functools import lru_cache
import ast, copy, hashlib, json, os, re, subprocess
import xml.etree.ElementTree as ET
import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker

ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
EVID = str(EP.relative_to(ROOT))
BASE = 'ec2a68ed648c03d34cf99dcd9040ff14844eca92'
ENG = '916de7322dbc6ad85a66d7c6252f5d672c9bfcc1'
ENG_BASE = '1ec9870a09868d108293b10c3417ffd14368ed2e'
TP = 'docs/tracker/LUFFY_Product_Tracker_v1.yaml'
VERSION = '1.58-exe04-canonical-closed-sol1'
CONTRACT = 'factory-exit.closed-bars.v1:51758284b0b961695203b5cbc1569dfb1d5805a68506d594a40a5d5df30d3fcc'
ALLOWED = [TP, 'STATE.yaml', 'NEXT.yaml', 'docs/tracker/LUFFY_Product_Tracker_v1.md',
           'docs/tracker/README.md', 'docs/tracker/bundle-manifest.json']

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

@lru_cache(maxsize=None)
def blob(rev, path):
    return git('show', rev+':'+path)

@lru_cache(maxsize=None)
def old(path):
    return yaml.load(blob(BASE, path), Loader=UniqueLoader)

def load(path):
    return yaml.load((ROOT/path).read_bytes(), Loader=UniqueLoader)

def write(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')

def block(text, key, value):
    m = re.search(r'(?m)^'+re.escape(key)+r':\n', text)
    assert m, key
    following = re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:', text[m.end():])
    end = m.end()+following.start() if following else len(text)
    return text[:m.start()]+yaml.safe_dump({key:value}, sort_keys=False, allow_unicode=True, width=105)+'\n'+text[end:]

def suites():
    result = {}
    names = set()
    for filename in ('fresh-final.xml', 'fresh-compiler.xml', 'fresh-factory-protection.xml'):
        suite = ET.parse(EP/filename).getroot().find('testsuite')
        assert suite is not None
        expected_failures = 3 if filename == 'fresh-factory-protection.xml' else 0
        assert int(suite.get('failures')) == expected_failures
        assert all(int(suite.get(k)) == 0 for k in ('errors','skipped'))
        cases = list(suite.iter('testcase'))
        assert len(cases) == int(suite.get('tests'))
        result[filename] = dict(passed=len(cases)-expected_failures, failed=expected_failures)
        for c in cases:
            if c.find('failure') is None: names.add((c.get('classname'), c.get('name')))
    fresh = ET.parse(EP/'fresh-factory-protection.xml').getroot().find('testsuite')
    baseline = ET.parse(EP/'baseline-orphans.xml').getroot().find('testsuite')
    def failures(s):
        return {(c.get('classname'),c.get('name')):c.find('failure').text
                for c in s.iter('testcase') if c.find('failure') is not None}
    assert int(baseline.get('failures')) == 3 and failures(fresh) == failures(baseline)
    result['baseline_orphan_failures_identical_names_and_assertions'] = True
    result['distinct_tests'] = len(names)
    result['counts_overlap_do_not_sum_with_retained'] = True
    return result

def pinned():
    git('cat-file','-e',ENG+'^{commit}')
    git('merge-base','--is-ancestor',ENG_BASE,ENG)
    refs = git('for-each-ref','--contains',ENG,'--format=%(refname)').decode().splitlines()
    assert 'refs/heads/sol1/exe04-exit-authority' in refs
    tree = ast.parse(blob(ENG,'trader/strategy/exit_policy.py'))
    contract = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='CONTRACT' for t in n.targets))
    assert contract['version']+':'+hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':')).encode()).hexdigest()==CONTRACT
    for path in ('docs/superpowers/reports/2026-10-08-exe04-authority-audit.md',
                 'docs/superpowers/reports/exe04-authority-evidence/audit.xml',
                 'docs/superpowers/reports/exe04-authority-evidence/regressions.xml',
                 'docs/superpowers/reports/exe04-authority-evidence/validation.txt'):
        assert blob('0c520e23843556f3df85a642643bdfe05d8ba1de',path)==blob(ENG,path)
    for file, count in [('exe04-parity-final.xml',321),('exe04-authenticated-research.xml',123),('exe04-factory-regression.xml',47)]:
        s=ET.fromstring(blob(ENG,'docs/superpowers/reports/exe04-exit-parity-r1-evidence/'+file)).find('testsuite')
        assert int(s.get('tests'))==count and all(int(s.get(k))==0 for k in ('failures','errors','skipped'))
    paths = git('diff','--name-only',ENG_BASE,ENG).decode().splitlines()
    for path in ('trader/engine/protective.py','trader/engine/supervisor.py','trader/engine/recovery.py',
                 'trader/engine/risk.py','trader/engine/risk_intent.py','trader/engine/entry_authority.py',
                 'trader/kernel.py','trader/portfolio/position_evidence.py','trader/owner/service.py',
                 'tests/test_orphan_positions.py'):
        assert blob(ENG_BASE,path) == blob(ENG,path), path
        paths.append(path)
    return {p:{'git_blob':git('rev-parse',ENG+':'+p).decode().strip(),
               'bytes':len(blob(ENG,p)), 'sha256':hashlib.sha256(blob(ENG,p)).hexdigest()} for p in paths}

MAPPING = {
 'DEC03_BINDING': 'LiveExitAdapter.position_plan independently reads the booked episode and retained independent holdings, consumes DEC-03 capture/build, and verifies account/instrument/episode, exact version/install/spec/initial-plan hashes, side/quantity and observation cut. Executor.close independently rechecks binding and causal replay before exchange access. Original entry action/capability digest is checked where recorded. Proven through live execution code with isolated offline fixtures; no venue execution claimed.',
 'SUPPORTED_BEHAVIORS': 'ATR/% initial stops with 0.4% floor; R/ATR/% final target or explicit NONE; full remaining quantity; unconditional 1..500 consecutive completed spec bars after signal bar. Signal-bar close reference and completed ATR(14). Open gap, stop, target, then time cap; both-hit range selects stop first with explicit ambiguity.',
 'REFUSED_BEHAVIORS': 'Wrong/missing/substituted StrategyVersion, install, spec, plan, geometry, account slot or episode; stale/future/missing holdings; non-none trail, partial schedules, executable invalidation/signal exits, scale-in and undeclared fields. No exact existing admitted authority supports those positive behaviors. Direct ratchet/new partial/unsupported close reasons refuse. No default trail, TP1, confidence resize or alternate-version fallback.',
 'RESEARCH_PAPER_LIVE_PARITY': 'Authenticated compile_version/research _trade, PaperExecutor and ExitEngine/LiveExitAdapter use the same initialize/advance/verify_initial/replay semantics. 70 long/short causal matrix cases compare state, stop, target, timing, reason and full-remaining intent, including NONE and changed legacy defaults. Fill equality is outside this contract.',
 'SAFETY_AUTHORITY': 'Authenticated OwnerService close receipt and exact manual_close event independently authorize containment even with missing strategy plan/snapshot. OWNER evidence remains separate from STRATEGY; panic retains FROZEN. Protective, Supervisor, recovery and Risk source bytes unchanged from DEC-03 engineering baseline; offline protection regressions checked. No Risk bypass, exposure expansion or native venue certification.',
 'REPLAY': 'Reconstructed paper/live adapters retain frozen geometry. Live observations are replayed against DEC-03 initial plan and exact spec on reads and final close; corrupt/unbound historical state refuses. Duplicate successful locally booked action is suppressed; failed submission retains identical terminal intent for retry. Legacy unattributable rows stay UNKNOWN, no future reconstruction or receipt backfill. Existing EXE-03 durable reservation/recovery precedes new-partial admission refusal.',
 'REGRESSIONS': 'EXE-03 durable identity/intent/ack, DEC-03 position evidence, STR-03 approval binding, STR-04 Governor/historical binding, vector/null research, legacy exits and stop safety pass in fresh-final.xml. Compiler/factory and protective/supervisor/partial recovery pass separately. Three orphan-management failures have identical names and assertion text on exact DEC-03 engineering baseline; unchanged Kernel and orphan test bytes. See fresh-factory-protection.xml and baseline-orphans.xml.',
 'BASELINE_LIMITATIONS': 'Architecture/offline exact admitted path only. No actual venue/fill/native protection proof, deployment, real calibration or all-legacy certification. Three orphan-management fixture failures (_Exits lacks spec_exits) reproduce identically on 1ec9870; remain unresolved for EXE-05 mapping. Retained factory suite excludes six cases: Kernel fixtures/install helper and pre-existing global writer whitelist omitting DEC-03; fresh combined factory/protection selection excludes nine cases by the recorded filter. Compiler excludes two Kernel fixtures. Original BLOCKED audit/report/XML retained unchanged as baseline counterexamples; superseded by 916de73. No full-suite green claim.',
 'EXE05_IMPACT': 'EXE-04 and ACC-01 are CLOSED: EXE-05 dependency satisfied and eligible for separate evidence mapping, unselected/unstarted. EXE-05 row unchanged and not auto-closed. Real supported venue size/side/protective IDs, missing/orphan/partial protection and unavailable-symbol behavior still require its own exact proof.',
 'FIRST_LIVE_IMPACT': 'Exact approval, validation, install, probation, inputs, capacity, current configuration and lifecycle verification remain required. Unsupported exit semantics cannot obtain admission. live_entry_block still returns version_first_live_execution_not_enabled for ACTIVE/REACTIVATED; parity tests assert refusal. M5/REL-01 and runtime authorization unchanged; no first-live eligibility or activation inferred.',
 'SOURCE_INTEGRATION': '916de73 reachable on sol1/exe04-exit-authority; not an ancestor of canonical control HEAD. No application branch integration, merge or cherry-pick. Production application source unchanged. EXE-04 plus separately parked engineering, including PORT-01 R4, still need a separately scoped integration/verification task.'
}

def apply():
    prior = old(TP); rows = {r['id']:r for r in prior['items']}
    row = copy.deepcopy(rows['EXE-04'])
    assert all(rows[d]['status']=='CLOSED' for d in row['dependencies'])
    counts = dict(Counter(r['status'] for r in prior['items']))
    counts['CLOSED'] += 1; counts['EVIDENCE_TO_MAP'] -= 1
    proof = suites(); hashes = pinned()
    record = dict(item='EXE-04', canonical_status='CLOSED', scope='ARCHITECTURE_OFFLINE',
        engineering_commit=ENG, engineering_base=ENG_BASE, control_baseline_revision=BASE,
        control_plane_commit_resolution='Enclosing Git commit; no self-referential hash.',
        closing_condition=row['closure_condition'], failure_regression_proof=row['failure_regression_proof'],
        closing_condition_satisfied=True, exit_contract=CONTRACT, closure_mapping=MAPPING,
        fresh_test_evidence=proof, engineering_objects=hashes,
        retained_test_evidence=dict(final=321, authenticated_research=123, factory=47, distinct=408,
            overlaps=83, excluded_factory=6, excluded_compiler=2),
        status_counts_after=counts, EXE05_dependency_satisfied=True, EXE05_now_eligible=True,
        EXE05_selected=False, EXE05_started=False, EXE05_auto_closed=False,
        next_critical_path_item='EXE-05', source_integration_still_pending=True,
        application_code_changed=False, runtime_execution_authorized=False,
        provider_enablement_authorized=False, trading_activation_authorized=False)
    write(EP/'closure.json',record)
    row.update(status='CLOSED', implementation_commit=ENG, resolution_at_commit=ENG, updated='2026-10-08',
      implementation_evidence='Exact DEC-03-bound admitted causal exit execution at '+ENG+'. '+EVID+'/closure.json',
      runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; live execution code exercised with isolated offline fixtures; no venue/deployment proof',
      latest_evidence=f"{proof['distinct_tests']} distinct fresh offline tests pass; retained 408 selected tests overlap. Exact frozen plan, three-adapter parity, refusal, containment and replay verified. No full-suite green claim.",
      next_proof='None for exact EXE-04 architecture/offline closure. EXE-05 dependency satisfied and eligible for its own native-protection evidence; unselected/unstarted. Source integration and first-live gates remain separate.',
      closure_evidence=EVID+'/closure.json', evidence_mapping=EVID+'/REPORT.md',
      evidence_validity='Pinned committed engineering bytes and fresh isolated offline proof; no deployment, venue execution, native-protection verification or all-legacy certification.')
    selection = copy.deepcopy(prior['current_build_selection'])
    selection.update(last_completed_item='EXE-04', last_terminal_item='EXE-04', next_recommended_item='EXE-05',
       selection_reason='EXE-04 CLOSED at architecture/offline scope on 916de73. EXE-05 dependencies satisfied; recommended for separate native-protection evidence mapping, unselected/unstarted. Application integration pending; first-live remains fenced. No runtime/provider/trading authority.')
    selection['eligible_items'] = [x for x in selection['eligible_items'] if x!='EXE-04']
    if 'EXE-05' not in selection['eligible_items']: selection['eligible_items'].append('EXE-05')
    metadata = copy.deepcopy(prior['metadata'])
    metadata.update(version=VERSION, current_active_item='EXE-04', status_counts=counts,
        next_recommended_item='EXE-05',last_terminal_item='EXE-04',last_terminal_evidence=EVID+'/closure.json')
    text = blob(BASE,TP).decode(); start=text.index('- id: EXE-04\n'); end=text.index('- id: EXE-05\n',start)
    text=text[:start]+yaml.safe_dump([row],sort_keys=False,allow_unicode=True,width=105)+text[end:]
    text=block(block(text,'metadata',metadata),'current_build_selection',selection)
    (ROOT/TP).write_text(text+'\n'+yaml.safe_dump({'exe04_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105))
    package=dict(id='EXE-04',status='CLOSED',result='CLOSED_ARCHITECTURE_OFFLINE',mode='ENGINEERING_ONLY',
       reconciliation_mode='CONTROL_PLANE_ONLY',scope='ARCHITECTURE_OFFLINE',objective=row['required_behavior'],
       implementation_commit=ENG,verification_revision=ENG,implementation_complete=True,
       canonical_reconciliation_pending=False,closing_condition_satisfied=True,
       evidence=[EVID+'/closure.json',EVID+'/REPORT.md',EVID+'/fresh-final.xml'],
       runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,
       successor_selected=False,next_action=selection['selection_reason'])
    for path,key in [('STATE.yaml','current_active_work'),('NEXT.yaml','work_package')]:
        text=block(block(blob(BASE,path).decode(),key,package),'current_build_selection',selection)
        (ROOT/path).write_text(text+'\n'+yaml.safe_dump({'exe04_previous_selection':old(path)[key],
            'exe04_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105))
    banner='> Current [SOL-1][EXE-04]: **CLOSED at architecture/offline scope** on `916de7322dbc6ad85a66d7c6252f5d672c9bfcc1`. Exact DEC-03-bound frozen plan; `factory-exit.closed-bars.v1`; research/paper/live causal parity, refusal, independent safety and replay verified offline. '+str(proof['distinct_tests'])+' distinct fresh passes. EXE-05 dependency-eligible, unselected/unstarted; its row remains open for mapping. Source integration pending; first-live fenced; no activation authority. [Evidence](evidence/exe04-canonical-sol1/closure.json). Earlier summaries below are historical.\n\n'
    path='docs/tracker/LUFFY_Product_Tracker_v1.md'; text=blob(BASE,path).decode()
    start=text.index('### EXE-04 —'); end=text.index('### EXE-05 —',start); section=text[start:end]
    for k,v in row.items():
        if rows['EXE-04'].get(k)==v: continue
        pattern=r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$'; line='- **'+k+'**: '+str(v)
        if re.search(pattern,section): section=re.sub(pattern,lambda m:line,section)
        else: section=section.rstrip()+'\n'+line+'\n\n'
    text=(text[:start]+section+text[end:]).replace('Version: '+prior['metadata']['version'],'Version: '+VERSION,1)
    (ROOT/path).write_text(banner+text+'\nEXE-04 canonical status counts: '+str(counts)+' (178 total).\n')
    (ROOT/'docs/tracker/README.md').write_text(banner+blob(BASE,'docs/tracker/README.md').decode())
    (EP/'REPORT.md').write_text('# EXE-04 canonical CLOSED\n\nEngineering `'+ENG+'`; control baseline `'+BASE+'`. Control-plane commit is the enclosing Git commit.\n\nExact closure condition: '+row['closure_condition']+'\n\nRegression: '+row['failure_regression_proof']+'\n\nExit contract: `'+CONTRACT+'`.\n\n'+'\n\n'.join(k+': '+v for k,v in MAPPING.items())+'\n\nFresh evidence: '+json.dumps(proof)+'. Retained 408 selected passes overlap; do not sum. Only EXE-04 changes status; other 177 rows, original conditions, edges and readiness gates unchanged.\n')
    m=json.loads(blob(BASE,'docs/tracker/bundle-manifest.json'))
    m.update(snapshot_version=VERSION,control_baseline_revision=BASE,status_counts=counts,
        current_build_selection=selection,exe04_canonical_reconciliation=record)
    write(ROOT/'docs/tracker/bundle-manifest.json',m)

def refresh():
    path=ROOT/'docs/tracker/bundle-manifest.json'; m=json.loads(path.read_text())
    known={e['path'] for e in m['files']}
    for f in sorted(EP.rglob('*')):
        p=str(f.relative_to(ROOT))
        if f.is_file() and '__pycache__' not in f.parts and p not in known:
            m['files'].append(dict(path=p,role='EXE04_CANONICAL_CONTROL_OR_EVIDENCE',synchronization_required=True))
    for e in m['files']:
        raw=(ROOT/e['path']).read_bytes(); e.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    write(path,m)

def verify(committed=False):
    prior=old(TP); t=load(TP); n=load('NEXT.yaml'); s=load('STATE.yaml')
    Ledger.model_validate(t); NextDocument.model_validate(n)
    a={r['id']:r for r in prior['items']}; b={r['id']:r for r in t['items']}
    assert len(b)==178 and a.keys()==b.keys()
    assert [k for k in b if a[k]!=b[k]]==['EXE-04']
    for k in ('required_behavior','closure_condition','failure_regression_proof','dependencies','parent_ids','related_items'):
        assert a['EXE-04'][k]==b['EXE-04'][k]
    assert b['EXE-04']['status']=='CLOSED' and b['EXE-05']==a['EXE-05']
    assert all(b[d]['status']=='CLOSED' for d in b['EXE-05']['dependencies'])
    assert t['readiness_gates']==prior['readiness_gates']
    for k in prior:
        if k not in ('metadata','current_build_selection','items'): assert t[k]==prior[k],k
    counts=dict(Counter(r['status'] for r in b.values()))
    assert counts=={'CLOSED':66,'EVIDENCE_TO_MAP':73,'DEFERRED':7,'AWAITING_EVIDENCE':9,'AWAITING_OWNER':14,'BLOCKED':5,'OPEN':4}
    r=json.loads((EP/'closure.json').read_text()); assert r==t['exe04_canonical_reconciliation']
    assert r['engineering_objects']==pinned() and r['fresh_test_evidence']==suites()
    for path in r['engineering_objects']:
        if path.startswith('docs/superpowers/'):
            assert (EP/'retained'/path).read_bytes()==blob(ENG,path),path
    assert r['exit_contract']==CONTRACT and r['status_counts_after']==counts==t['metadata']['status_counts']
    sel=t['current_build_selection']; assert sel['next_recommended_item']=='EXE-05'
    assert not sel['active_items'] and not sel['next_item_selected'] and not sel['runtime_execution_authorized']
    for doc,path,key in [(s,'STATE.yaml','current_active_work'),(n,'NEXT.yaml','work_package')]:
        for k in old(path):
            if k not in (key,'current_build_selection'): assert doc[k]==old(path)[k],(path,k)
        assert doc['exe04_previous_selection']==old(path)[key]
        assert doc['exe04_canonical_reconciliation']==r and doc['current_build_selection']==sel
        assert doc[key]['id']=='EXE-04' and doc[key]['status']=='CLOSED'
        assert all(doc[key][k] is False for k in ('runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'))
    md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text(); before=blob(BASE,'docs/tracker/LUFFY_Product_Tracker_v1.md').decode()
    for rid in b:
        marker='### '+rid+' —'
        if marker not in before: continue
        section=md[md.index(marker):].split('\n### ',1)[0]
        if rid=='EXE-04':
            for k in ('status','closure_condition','failure_regression_proof','next_proof','implementation_commit'):
                assert '- **'+k+'**: '+b[rid][k] in section
        else: assert section.startswith(before[before.index(marker):].split('\n### ',1)[0].rstrip()),rid
    m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text())
    assert m['snapshot_version']==t['metadata']['version']==VERSION and m['status_counts']==counts
    assert m['row_count']==178 and m['current_build_selection']==sel and m['exe04_canonical_reconciliation']==r
    assert len({e['path'] for e in m['files']})==len(m['files'])
    for e in m['files']:
        raw=(ROOT/e['path']).read_bytes()
        assert e['bytes']==len(raw) and e['sha256']==hashlib.sha256(raw).hexdigest(),e['path']
    view=read_tracker(ROOT); assert view['status']=='AVAILABLE' and view['selected']['id']=='EXE-04'
    assert all(view['counts'][k]==v for k,v in counts.items())
    changed=git('diff','--name-only',BASE,'HEAD').decode().splitlines()
    changed+=git('diff','--name-only','--',*ALLOWED,EVID).decode().splitlines()
    assert all(p in ALLOWED or p.startswith(EVID+'/') for p in changed),changed
    assert subprocess.run(['git','merge-base','--is-ancestor',ENG,'HEAD'],cwd=ROOT).returncode==1
    if committed:
        changed=git('diff','--name-only',BASE,'HEAD').decode().splitlines()
        for path in changed: assert blob('HEAD',path)==(ROOT/path).read_bytes(),path
    return dict(EXE04_CANONICAL='CLOSED',status_counts=counts,row_count=178,changed_rows=['EXE-04'],
       other_177_rows_unchanged=True,conditions_edges_readiness_preserved=True,strict_schemas_valid=True,
       manifest_bytes_hashes_valid=True,engineering_reachable_unchanged=True,dashboard_read='AVAILABLE',
       EXE05_dependency_satisfied=True,EXE05_now_eligible=True,EXE05_not_closed=True,
       source_integration_still_pending=True,first_live_execution_still_fenced=True,
       application_code_changed=False,fresh_test_evidence=suites())

if __name__=='__main__':
    if os.environ.get('EXE04_RECONCILE')=='APPLY':
        apply(); write(EP/'control-checks.json',{}); refresh()
        write(EP/'control-checks.json',verify()); refresh()
    result=verify(os.environ.get('EXE04_COMMITTED')=='YES')
    assert result==json.loads((EP/'control-checks.json').read_text())
    print(json.dumps(result,indent=2))
