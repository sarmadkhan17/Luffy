"""Pinned PORT-04 control reconciliation; apply, verify, or committed-byte verification."""
from pathlib import Path
from collections import Counter
from functools import lru_cache
import copy, hashlib, json, re, subprocess, sys
import xml.etree.ElementTree as ET
import yaml
from trader.dashboard.tracker import UniqueLoader, Ledger, NextDocument, read_tracker
ROOT = Path(__file__).resolve().parents[4]
EP = Path(__file__).resolve().parent
EVID = str(EP.relative_to(ROOT))
BASE = 'b6b6e6016f76d0df4869312b1b7c1a4551ba0918'
ENG = '387d130dedfe259e707ddda77b5eab83aa493aa7'
PARENT = '2ea4a95349062b7ec19cde6263fa8b3e8af45660'
TP = 'docs/tracker/LUFFY_Product_Tracker_v1.yaml'
VERSION = '1.60-port04-canonical-closed-sol1'
ALLOWED = [TP, 'STATE.yaml', 'NEXT.yaml', 'docs/tracker/LUFFY_Product_Tracker_v1.md', 'docs/tracker/README.md', 'docs/tracker/bundle-manifest.json']
def git(*a): return subprocess.check_output(['git',*a],cwd=ROOT)
@lru_cache(maxsize=None)
def blob(rev,p): return git('show',rev+':'+p)
@lru_cache(maxsize=None)
def old(p): return yaml.load(blob(BASE,p),Loader=UniqueLoader)
def load(p): return yaml.load((ROOT/p).read_bytes(),Loader=UniqueLoader)
def write(p,v): p.write_text(json.dumps(v,indent=2)+'\n')
def block(text,key,value):
    m=re.search(r'(?m)^'+re.escape(key)+r':\n',text); assert m,key
    following=re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:',text[m.end():])
    end=m.end()+following.start() if following else len(text)
    return text[:m.start()]+yaml.safe_dump({key:value},sort_keys=False,allow_unicode=True,width=105)+'\n'+text[end:]
def suites():
    out={}; names=set()
    for name in ['fresh-targeted','fresh-metrics','fresh-independent']:
        s=ET.parse(EP/(name+'.xml')).getroot().find('testsuite')
        assert all(int(s.get(k))==0 for k in ('failures','errors','skipped'))
        by=Counter(c.get('classname') for c in s.iter('testcase'))
        cases={(c.get('classname'),c.get('name')) for c in s.iter('testcase')}
        assert len(cases)==int(s.get('tests'))
        names |= cases
        out[name]={'passed':int(s.get('tests')),'failed':0,'by_file':dict(by)}
    out['distinct_fresh_passes']=len(names)
    out['reported_engineering_passes']=327
    out['reported_327_exact_command_and_artifact']='NOT_RETAINED; not independently certified or summed with fresh evidence'
    out['full_suite_green_claim']=False
    return out
PROTECTED = ['trader/portfolio/allocator.py','trader/portfolio/runtime.py','trader/portfolio/runtime_metrics.py',
 'trader/portfolio/candidate_bridge.py','trader/portfolio/trade_intent.py','trader/engine/risk.py',
 'trader/engine/risk_intent.py','trader/engine/entry_authority.py','trader/engine/executor.py','trader/kernel.py',
 'trader/world/relationship.py','trader/world/replay.py','trader/research/dependence.py','config.yaml']
def pinned():
    assert git('rev-parse',ENG+'^').decode().strip()==PARENT
    assert git('rev-parse','port04-relationship-sources').decode().strip()==ENG
    assert subprocess.run(['git','merge-base','--is-ancestor','ec2a68e',BASE],cwd=ROOT).returncode==0
    assert old(TP)['port01_canonical_reconciliation']['canonical_status']=='CLOSED'
    changed=git('diff','--name-only',PARENT,ENG).decode().splitlines()
    assert set(changed)=={'tests/test_port04_relationship_sources.py','trader/portfolio/current.py','trader/portfolio/common_factor.py'}
    for p in PROTECTED: assert blob(ENG,p)==blob(PARENT,p),p
    # DEC-03's separate engineering is not integrated; the affected exit/position consumer files are untouched.
    dec_changes=git('diff','--name-only','37d2107','1ec9870').decode().splitlines()
    relevant=[p for p in dec_changes if p.startswith('trader/')]
    assert set(changed)&set(relevant)=={'trader/portfolio/current.py'}
    delta=git('diff','--unified=0',PARENT,ENG,'--','trader/portfolio/current.py').decode()
    assert 'position_capture' not in delta and "SELECT * FROM trades" not in delta
    def obj(p):
        raw=blob(ENG,p)
        return dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),git_blob=git('rev-parse',ENG+':'+p).decode().strip())
    return {'changed':{p:obj(p) for p in changed},'unchanged_authority_and_contracts':{p:obj(p) for p in PROTECTED},
        'DEC03_separate_engineering_source_files':relevant,'DEC03_overlap':'current.py contains disjoint source-wiring changes; PORT-04 parent never included DEC-03 position capture. runtime.py unchanged from parent; separate position_evidence.py not present on this engineering base.'}
MAPPING={
 'NORMAL_CALLER':'current.checkpoint -> current.freeze -> relationship_sources -> common_factor.attach -> Consumer/allocator R4/from_allocator. Real persisted Store.write scan and joined bars are consumed without downstream source injection; independent actual checkpoint test proves delivery and zero order submissions.',
 'ATTENTION_RELATIONSHIP':'Exact attention-relationship-input.v1 scan, input-version joins, bars, method, window, peer membership, original as_of_ms, bounded valid_until_ms and evidence/source identities retained. adapt re-evaluates observations at the original cut and refuses evaluator mismatch. Basket measurements are CONTEXT_ONLY.',
 'WORLD_MODEL_LIMITATION':'B: world-model capture currently disabled (attention settings default false; config has no enabling key). WORLD_RELATIONSHIP_SOURCE_NOT_CONFIGURED is explicit. No normal production world pair measurement is claimed. Existing WorldModelRecord replay and WRLD-07 measurements remain supported when valid evidence exists.',
 'CONCENTRATION_MEASUREMENT':'Exact verified complete venue book plus independently bound runtime metrics supply current gross/per-instrument exposure, heat, per-symbol risk and margin where established. Existing max_open_positions, portfolio_heat_cap_pct, per_symbol_risk_cap_pct, max_position_margin_pct and max_total_margin_pct are checked; missing metrics remain unavailable and excess limits block. Shared proven lineage deduplicates contributors; independence is NOT_ESTABLISHED.',
 'PAIRWISE_ATTRIBUTION_FIX':'Pair measurement requires two explicitly attributed instruments, current freshness and exact endpoint set. A one-instrument leave-one-out basket cannot supply pair measurement_ids. Parent counterexample reproduced offline; invalid old attribution receipts refuse on replay and are never rewritten.',
 'MISSING_SOURCE_SEMANTICS':'Missing/stale optional scan produces no measurement and explicit unavailability. Unproven pair relationship remains UNKNOWN unless independent exact same exposure/shared lineage establishes EXACT_SHARED_EXPOSURE. NO_ESTABLISHED_RELATIONSHIP for distinct proven lineage is not statistical independence (independent_evidence_count=null); no optional absence establishes a measured pair. Malformed initial optional evidence is omitted/refused; changed frozen sources or replay mismatch refuse exposure authority.',
 'REPLAY':'Frozen source hashes/identity, original-cut input joins, observations, expiry and exact receipt reconstruction are checked. Mutated values, another scan, expired or replaced frozen source refuse. Restart reconstruction is deterministic. Invalid pre-fix attribution is refused, not migrated.',
 'R3_R4_COMPATIBILITY':'R3 remains explicit historical replay; R4 remains the current allocator and refuses absent/ineligible/stale/mismatched capability. Allocator source byte-identical to PORT-01 parent. Fresh PORT-01 and PORT-04 replay/capability tests pass. No blanket acceptance of invalid historical evidence is claimed.',
 'RISK_AUTHORITY':'Risk, risk_intent, entry_authority, executor and Kernel bytes unchanged from engineering parent. Proposal-only/context-only evidence grants no sizing, leverage, order, venue or Risk approval authority. No numeric factor model, penalty, assumed correlation or threshold invented.',
 'PORT01_IMPACT':'UNCHANGED CLOSED at ec2a68e. Whole-book/R4 capability tests pass and relevant allocator/metric/authority bytes remain identical to 2ea4a95. The previously separate relationship-source gap is resolved on PORT-04 engineering only; source integration remains pending.',
 'DEC03_IMPACT':'UNCHANGED CLOSED. One-expression/conflicting-strategy/lineage behavior passes targeted DEC-02/common-factor tests. DEC-03 is separate engineering 1ec9870, not present in this engineering base; current.py is shared, with disjoint relationship wiring changes; the parent never contained DEC-03 position capture, so this commit does not remove it. Separate DEC-03 runtime/position capture must be retained during future integration. Prior PORT-01 evidence includes 80 DEC-03+R4 integrated verification passes; no new combined-tree certification or application integration claimed.',
 'WRLD07_IMPACT':'UNCHANGED CLOSED. Fresh WRLD-07 exact metadata, freshness, invalid evidence, measured endpoints and historical replay checks pass. Capture is still disabled; no runtime upgrade.',
 'QNT03_IMPACT':'UNCHANGED CLOSED. Fresh dependence/lineage tests pass; research/dependence.py unchanged. Distinct strategy lineage does not establish statistical independence; no numeric factor adjustment or pseudo-replication introduced.',
 'RISK03_IMPACT':'PORT-04 and RISK-01 dependencies CLOSED; RISK-03 now eligible for its own exact evidence mapping, unselected/unstarted. Row unchanged EVIDENCE_TO_MAP; no automatic closure. Whole-account period losses/drawdown and pre-catastrophe preservation remain its independent contract.',
 'SOURCE_INTEGRATION':'YES pending. Control-only commit on separate branch; 387d130 is not integrated into canonical application source. No Kernel/Dashboard operation, provider/venue call, configuration activation or live action.'}
LIMITATIONS=[
 dict(limitation='WRLD-07 world-model capture disabled',classification='B',reason='Truthful unavailable optional evidence; existing Attention basket measurement and shared-lineage/exact-book evidence identify supported risks. No world pair capability established.'),
 dict(limitation='Asset/cluster concentration policy not configured',classification='A',reason='No supported owner limit exists to consume; exact condition requires measurement/lineage for deterministic constraints, not invention of owner policy. Existing configured constraints stay enforced.'),
 dict(limitation='Per-instrument exposure measured but not consumed as a new limit',classification='A',reason='Exact measurement is retained; per-symbol risk policy remains consumed. A new exposure ceiling has no owner policy authority.'),
 dict(limitation='Historical held-position lineage may remain UNKNOWN',classification='B',reason='Unattributed held positions remain in the complete book and same-instrument exposure checks. No reconstructed provenance or independent vote is invented.'),
 dict(limitation='No production runtime observation',classification='A',reason='Architecture/offline closure only; runtime unproven and source integration pending.')]
def apply():
    t=old(TP); rows={r['id']:r for r in t['items']}; row=copy.deepcopy(rows['PORT-04'])
    assert row['closure_condition']=='Exact whole-book measurement and lineage identify concentration/common-factor risk for deterministic constraints.'
    assert all(rows[d]['status']=='CLOSED' for d in row['dependencies'])
    row.update(status='CLOSED',updated='2026-10-08',implementation_commit=ENG,resolution_at_commit=ENG,closure_scope='ARCHITECTURE_OFFLINE',
        implementation_evidence='Persisted Attention relationship source delivery and two-instrument attribution fix at '+ENG,
        runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; no production observation or activation',
        latest_evidence=str(suites()['distinct_fresh_passes'])+' distinct fresh offline passes; exact persisted checkpoint, whole-book metrics, lineage, replay, refusal and R3/R4 compatibility. Reported 327 engineering passes have no retained exact selection; no full-suite green claim.',
        next_proof='None for exact PORT-04 architecture/offline closure. Source integration remains pending. RISK-03 now dependency-eligible for its own exact mapping; unselected/unstarted, not auto-closed.',
        closure_evidence=EVID+'/closure.json',evidence_mapping=EVID+'/REPORT.md',
        evidence_validity='Pinned committed engineering bytes and exact fresh offline evidence. Optional absence remains unavailable; no production/deployment or numeric factor-model certification.')
    counts=dict(Counter(r['status'] for r in t['items'])); counts['CLOSED']+=1; counts['EVIDENCE_TO_MAP']-=1
    record=dict(item='PORT-04',canonical_status='CLOSED',scope='ARCHITECTURE_OFFLINE',engineering_commit=ENG,engineering_parent=PARENT,
        engineering_branch='port04-relationship-sources',control_baseline_revision=BASE,control_plane_commit_resolution='Enclosing Git commit; no self-referential hash.',
        closure_condition=row['closure_condition'],failure_regression_proof=row['failure_regression_proof'],exact_closure_condition_satisfied=True,
        closure_mapping=MAPPING,limitations_adjudication=LIMITATIONS,unmet_closing_conditions=[],fresh_test_evidence=suites(),engineering_objects=pinned(),
        status_counts_after=counts,RISK03_dependency_satisfied=True,RISK03_now_eligible=True,RISK03_auto_closed=False,
        unproven_capabilities=['Production normal-caller behavior/deployment','Enabled normal world-model pair measurements','Numeric factor model/calibrated correlation penalty','Asset/cluster ceilings','Complete historical held-position provenance','Combined PORT-04 plus separate DEC-03 integration'],
        baseline_limitations=['Reported 327 test selection/artifact unavailable; independently retained fresh exact tests are authoritative.','PORT-01 retained wider selection had 11 baseline failures; not rerun and no full-suite green claim.','Invalid pre-fix basket-to-pair receipts are refused, not rewritten.'],
        source_integration_pending=True,next_critical_path_item='RISK-03',application_code_changed=False,
        runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False)
    write(EP/'closure.json',record)
    selection=copy.deepcopy(t['current_build_selection']); selection.update(last_completed_item='PORT-04',last_terminal_item='PORT-04',
        next_recommended_item='RISK-03',selection_reason='PORT-04 CLOSED at architecture/offline scope on 387d130. RISK-03 dependencies satisfied; recommended for its own whole-account survival evidence mapping, unselected/unstarted and not auto-closed. EXE-05 remains eligible. Source integration pending; no runtime/provider/execution authority.')
    selection['eligible_items']=[x for x in selection['eligible_items'] if x!='PORT-04']
    if 'RISK-03' not in selection['eligible_items']: selection['eligible_items'].append('RISK-03')
    metadata=copy.deepcopy(t['metadata']); metadata.update(version=VERSION,status_counts=counts,current_active_item='PORT-04',
        next_recommended_item='RISK-03',last_terminal_item='PORT-04',last_terminal_evidence=EVID+'/closure.json')
    text=blob(BASE,TP).decode(); start=text.index('- id: PORT-04\n'); end=text.index('- id: PORT-05\n',start)
    text=text[:start]+yaml.safe_dump([row],sort_keys=False,allow_unicode=True,width=105)+text[end:]
    text=block(block(text,'metadata',metadata),'current_build_selection',selection)
    (ROOT/TP).write_text(text+'\n'+yaml.safe_dump({'port04_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105))
    package=dict(id='PORT-04',status='CLOSED',result='CLOSED_ARCHITECTURE_OFFLINE',mode='ENGINEERING_ONLY',reconciliation_mode='CONTROL_PLANE_ONLY',scope='ARCHITECTURE_OFFLINE',
        objective=row['required_behavior'],implementation_commit=ENG,verification_revision=ENG,implementation_complete=True,canonical_reconciliation_pending=False,closing_condition_satisfied=True,
        evidence=[EVID+'/closure.json',EVID+'/REPORT.md',EVID+'/fresh-targeted.xml',EVID+'/fresh-independent.xml'],runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False,next_action=selection['selection_reason'])
    for p,key in [('STATE.yaml','current_active_work'),('NEXT.yaml','work_package')]:
        text=block(block(blob(BASE,p).decode(),key,package),'current_build_selection',selection)
        (ROOT/p).write_text(text+'\n'+yaml.safe_dump({'port04_previous_selection':old(p)[key],'port04_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105))
    banner='> Current [SOL-1][PORT-04]: **CLOSED at architecture/offline scope** on `387d130`. '+str(suites()['distinct_fresh_passes'])+' distinct fresh offline passes. Persisted Attention evidence delivered by the normal checkpoint; exact whole-book/lineage/owner constraints retained; basket-to-pair attribution fixed. Optional world capture unavailable. RISK-03 now dependency-eligible, unselected/unstarted and not closed. Source integration pending; no activation authority. [Evidence](evidence/port04-canonical-sol1/closure.json). Earlier summaries below are historical.\n\n'
    p='docs/tracker/LUFFY_Product_Tracker_v1.md'; text=blob(BASE,p).decode(); start=text.index('### PORT-04 —'); end=text.index('### PORT-05 —',start); section=text[start:end]
    for k,v in row.items():
        if rows['PORT-04'].get(k)==v: continue
        pattern=r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$'; line='- **'+k+'**: '+str(v)
        if re.search(pattern,section): section=re.sub(pattern,lambda m:line,section)
        else: section=section.rstrip()+'\n'+line+'\n\n'
    text=(text[:start]+section+text[end:]).replace('Version: '+t['metadata']['version'],'Version: '+VERSION,1)
    (ROOT/p).write_text(banner+text+'\nPORT-04 canonical status counts: '+str(counts)+' (179 total).\n')
    (ROOT/'docs/tracker/README.md').write_text(banner+blob(BASE,'docs/tracker/README.md').decode())
    (EP/'REPORT.md').write_text('# PORT-04 canonical CLOSED\n\nEngineering `'+ENG+'`, parent `'+PARENT+'`; control baseline `'+BASE+'`.\n\nExact contract: '+row['closure_condition']+'\n\n'+ '\n\n'.join(k+': '+v for k,v in MAPPING.items())+'\n\nLimitations adjudication:\n\n'+'\n'.join('- '+v['classification']+': '+v['limitation']+'. '+v['reason'] for v in LIMITATIONS)+'\n\nNo C limitation found. Fresh exact evidence: '+json.dumps(suites())+'. Only PORT-04 changes; other 178 rows, contracts, dependencies and readiness gates unchanged.\n')
    m=json.loads(blob(BASE,'docs/tracker/bundle-manifest.json')); m.update(snapshot_version=VERSION,control_baseline_revision=BASE,status_counts=counts,row_count=179,current_build_selection=selection,port04_canonical_reconciliation=record)
    write(ROOT/'docs/tracker/bundle-manifest.json',m)
def refresh():
    p=ROOT/'docs/tracker/bundle-manifest.json'; m=json.loads(p.read_text()); known={v['path'] for v in m['files']}
    for f in sorted(EP.rglob('*')):
        if f.is_file() and '__pycache__' not in f.parts and str(f.relative_to(ROOT)) not in known:
            m['files'].append(dict(path=str(f.relative_to(ROOT)),role='PORT04_CANONICAL_CONTROL_OR_EVIDENCE',synchronization_required=True))
    for e in m['files']:
        raw=(ROOT/e['path']).read_bytes(); e.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    write(p,m)
def verify(committed=False):
    t=load(TP); prior=old(TP); Ledger.model_validate(t); NextDocument.model_validate(load('NEXT.yaml'))
    a={r['id']:r for r in prior['items']}; b={r['id']:r for r in t['items']}
    assert len(b)==179 and a.keys()==b.keys()
    assert [k for k in b if a[k]!=b[k]]==['PORT-04']
    for k in a['PORT-04']:
        if k not in ('status','updated','implementation_evidence','runtime_evidence','latest_evidence','next_proof','resolution_at_commit','closure_evidence','evidence_validity'): assert a['PORT-04'][k]==b['PORT-04'][k],k
    assert all(b[d]['status']=='CLOSED' for d in b['PORT-04']['dependencies']+b['RISK-03']['dependencies'])
    assert b['RISK-03']['status']=='EVIDENCE_TO_MAP'
    assert t['readiness_gates']==prior['readiness_gates']
    for k in prior:
        if k not in ('metadata','current_build_selection','items'): assert t[k]==prior[k],k
    counts=dict(Counter(r['status'] for r in b.values())); assert counts==t['metadata']['status_counts']
    assert counts=={'CLOSED':67,'EVIDENCE_TO_MAP':72,'DEFERRED':7,'AWAITING_EVIDENCE':9,'AWAITING_OWNER':15,'BLOCKED':5,'OPEN':4}
    record=json.loads((EP/'closure.json').read_text()); assert record==t['port04_canonical_reconciliation']
    assert record['fresh_test_evidence']==suites() and record['engineering_objects']==pinned()
    sel=t['current_build_selection']; assert sel['next_recommended_item']=='RISK-03' and 'RISK-03' in sel['eligible_items'] and 'PORT-04' not in sel['eligible_items']
    assert not sel['active_items'] and not sel['next_item_selected'] and not sel['runtime_execution_authorized']
    for p,key in [('STATE.yaml','current_active_work'),('NEXT.yaml','work_package')]:
        doc=load(p)
        for k in old(p):
            if k not in (key,'current_build_selection'): assert doc[k]==old(p)[k],(p,k)
        assert doc['port04_previous_selection']==old(p)[key] and doc['port04_canonical_reconciliation']==record
        assert doc['current_build_selection']==sel and doc[key]['id']=='PORT-04' and doc[key]['status']=='CLOSED'
        assert all(doc[key][k] is False for k in ('runtime_execution_authorized','provider_enablement_authorized','trading_activation_authorized','successor_selected'))
    md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text(); before=blob(BASE,'docs/tracker/LUFFY_Product_Tracker_v1.md').decode()
    for rid in b:
        marker='### '+rid+' —'
        if marker not in before: continue
        section=md[md.index(marker):].split('\n### ',1)[0]
        if rid=='PORT-04':
            for k in ('status','closure_condition','failure_regression_proof','implementation_commit'): assert '- **'+k+'**: '+b[rid][k] in section
        else: assert section.startswith(before[before.index(marker):].split('\n### ',1)[0].rstrip()),rid
    m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text()); assert m['row_count']==179 and m['status_counts']==counts and m['snapshot_version']==VERSION
    assert m['current_build_selection']==sel and m['port04_canonical_reconciliation']==record
    assert len({v['path'] for v in m['files']})==len(m['files'])
    for e in m['files']:
        raw=(ROOT/e['path']).read_bytes(); assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
    view=read_tracker(ROOT); assert view['status']=='AVAILABLE' and view['selected']['id']=='PORT-04'
    assert all(view['counts'][k]==v for k,v in counts.items())
    assert subprocess.run(['git','merge-base','--is-ancestor',ENG,'HEAD'],cwd=ROOT).returncode==1
    changes=git('diff','--name-only',BASE,'HEAD').decode().splitlines()+git('diff','--name-only').decode().splitlines()
    assert all(p in ALLOWED or p.startswith(EVID+'/') for p in changes),changes
    if committed:
        for p in git('diff','--name-only',BASE,'HEAD').decode().splitlines(): assert blob('HEAD',p)==(ROOT/p).read_bytes(),p
        assert not git('status','--porcelain','--untracked-files=no')
    return dict(PORT04_CANONICAL='CLOSED',status_counts=counts,row_count=179,changed_rows=['PORT-04'],other_178_rows_unchanged=True,
        original_contracts_dependencies_readiness_preserved=True,manifest_bytes_hashes_valid=True,strict_schemas_valid=True,
        canonical_read='AVAILABLE',committed_bytes_verified=committed,engineering_integration_pending=True,
        RISK03_dependency_satisfied=True,RISK03_now_eligible=True,RISK03_status='EVIDENCE_TO_MAP',fresh_test_evidence=suites())
if __name__=='__main__':
    if sys.argv[1:] == ['apply']:
        apply(); write(EP/'control-checks.json',{}); refresh(); write(EP/'control-checks.json',verify()); refresh()
    else: print(json.dumps(verify('committed' in sys.argv),indent=2))
