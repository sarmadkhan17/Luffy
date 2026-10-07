"""Authorized DEC-02 control-plane reconciliation; implementation read-only."""
from pathlib import Path
from collections import Counter
import copy,hashlib,json,re,subprocess
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
import yaml
ROOT=Path(__file__).resolve().parents[4];EP=Path(__file__).resolve().parent;EVID=str(EP.relative_to(ROOT))
HEAD=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
assert HEAD=='f1bbd7a74600ce2f8355babb337854bc6693bc35'
paths={'tracker':'docs/tracker/LUFFY_Product_Tracker_v1.yaml','state':'STATE.yaml','next':'NEXT.yaml'}
docs={k:yaml.safe_load((ROOT/p).read_text()) for k,p in paths.items()}
(EP/'control-before.json').write_text(json.dumps(docs,indent=2)+'\n')
rows={r['id']:r for r in docs['tracker']['items']};old=rows['DEC-02']
assert old['status']=='EVIDENCE_TO_MAP' and all(rows[d]['status']=='CLOSED' for d in old['dependencies'])
assert all(rows[d]['status']=='CLOSED' for d in rows['STR-03']['dependencies'])
counts=dict(Counter(r['status'] for r in rows.values()));counts['CLOSED']+=1;counts['EVIDENCE_TO_MAP']-=1
suite=ET.parse(EP/'focused.xml').getroot().find('testsuite')
assert int(suite.get('tests'))==195 and all(int(suite.get(k))==0 for k in ['failures','errors','skipped'])
by_file=dict(Counter(t.get('classname') for t in suite.findall('testcase')))
source_paths=['trader/portfolio/allocator.py','trader/portfolio/comparison.py','trader/portfolio/runtime.py',
 'trader/portfolio/economics.py','trader/portfolio/opportunity_live.py','trader/portfolio/current.py',
 'scripts/portfolio_allocator_shadow.py','tests/test_dec02_comparison.py','tests/test_portfolio_allocator.py',
 'tests/test_stage6_closure.py','tests/test_expected_net_economics.py','tests/test_port02_economics_authority.py',
 'tests/test_portfolio_allocator_shadow.py','tests/economics_fixtures.py','config.yaml']
hashes={}
for p in source_paths:
 raw=(ROOT/p).read_bytes();assert raw==subprocess.check_output(['git','show',HEAD+':'+p],cwd=ROOT),p
 hashes[p]=hashlib.sha256(raw).hexdigest()
from trader.portfolio import allocator as A,economics as E
assert A.VERSION=='LUFFY-PORTFOLIO-ALLOCATOR-R3'
assert A.VERSION_R2=='LUFFY-PORTFOLIO-ALLOCATOR-R2'
assert E.GROSS_MODELS==E.RESERVE_MODELS==E.COST_SCOPE_MODELS=={}
ps=subprocess.check_output(['ps','-eo','pid,args'],text=True).splitlines()
running=[p for p in ps if 'python' in p and any(s in p for s in ['-m trader.kernel','-m trader.dashboard.server']) and '/bin/bash' not in p]
assert not running,running
observation=dict(observed_at_utc=datetime.now(timezone.utc).isoformat(),process_check='ps -eo pid,args; Python module launch patterns trader.kernel/trader.dashboard.server',matching_processes=running,
 scope='Read-only process snapshot; no boot/restart/live/provider action. Not deployed runtime proof or independent continuous monitoring.',user_validation_report='Kernel/Dashboard remained stopped throughout engineering validation.')
(EP/'stopped-process-observation.json').write_text(json.dumps(observation,indent=2)+'\n')
mapping={
 'single_comparator':'allocator.allocate is the sole deterministic decision comparator in this Portfolio boundary. runtime invokes it; comparison.decide calls it and comparison.compare derives a pure inspectable view of the frozen Proposal. No LLM or analyst vote/count performs independent candidate selection. This does not claim removal of unrelated legacy orchestrator logic.',
 'cash':'CASH is explicit and selected for no candidates, unavailable economics, expected net <= 0, all candidates rejected, or an R3 exact positive top tie among distinct expressions without a registered discriminator. CASH reasons are explicit in the comparison view. Unknown cash yield remains UNAVAILABLE, not a fabricated numeric benchmark.',
 'R3_exact_tie':'New default LUFFY-PORTFOLIO-ALLOCATOR-R3 uses exact Decimal net comparison. Positive equal top net across distinct (instrument, market_type, direction) expressions selects CASH with EXACT_ECONOMIC_TIE_NO_DOMINANT_CANDIDATE; all top tied candidates are REJECTED with that reason. Lower/rejected candidates retain their own reasons; permutations reproduce identical proposal/view identities. Equal-net duplicates of the same expression remain evidence contributors, not rival expressions. A real net difference or a strictly better untied candidate is not blocked by a lower tie.',
 'R2_compatibility':'LUFFY-PORTFOLIO-ALLOCATOR-R2 remains supported through explicit version dispatch and stored proposal version. It retains historical ascending opportunity/version identity-order tie selection; no R3 exact_tie field is added to R2 results. Actual pre-R3 source and current R2 produce byte-equal inputs/results/IDs in seven offline cases. R2 and R3 identities differ; cross-version verification refuses. Immutable saved proposals/history are not rewritten.',
 'outcomes_reasons':'comparison.compare derives PROPOSED or REJECTED for every candidate from allocator accepted/refusal result; every outcome and CASH have non-empty machine-readable reasons. No second selection authority is introduced.',
 'confidence':'Six inspectable component states/source IDs: evidence_strength, strategy_reliability, execution_cost_confidence, data_quality, regime_context_fit, economics_confidence. Each score is None; no invented weights or opaque composite. Required components gate through existing allocator contracts; unavailable descriptive components remain unavailable.',
 'economics':'PORT-02 remains enforced: UNKNOWN/UNAVAILABLE is null, never zero or allocation; unavailable/non-positive net cannot beat CASH, stale/mismatched receipts/context refuse. Production GROSS_MODELS/RESERVE_MODELS/COST_SCOPE_MODELS remain empty; REAL CALIBRATION PRESENT: NO. Synthetic fixture registration proves mechanics only; no economic readiness/profitability claim.',
 'analysts':'Exact DEC-01 frozen supporting/opposing/conflict evidence stays visible and marked decides_nothing. Majority, unanimity or reversed votes cannot replace economics/eligibility or authorize selection; full evidence/provenance remains in immutable context.',
 'authority':'Output is proposal/rejection only. It grants no Risk permission, owner approval, capital or order authority; Risk remains a separate final gate. side_effects NONE. No provider/live calls or operational activation.'}
record=dict(item='DEC-02',canonical_status='CLOSED',scope='ARCHITECTURE_OFFLINE',engineering_commits=['9f36fc7384de6ecd53b1b3f85e45f8a8c6508a81',HEAD],
 engineering_closure_commit=HEAD,control_baseline_revision=HEAD,control_plane_commit_resolution='Enclosing Git commit containing this reconciliation; no self-referential hash.',
 closing_condition=old['closure_condition'],failure_regression_proof=old['failure_regression_proof'],closing_condition_satisfied=True,closure_mapping=mapping,
 allocator_default=A.VERSION,historical_replay_supported=[A.VERSION_R2],R3_exact_tie_reason=A.TIE_REASON,R2_replay_status='PASS; actual pre-R3 bytes preserved',
 shadow_receipt_label='LUFFY-PORTFOLIO-ALLOCATOR-R1; independent shadow package label, preserved unchanged; not an allocate-supported dispatch version',
 validation={'fresh_focused_passed':195,'fresh_by_file':by_file,'failures':0,'errors':0,'skipped':0,'runner':'/home/sarmad/.local/bin/luffy-pytest',
 'reported_affected_passed':363,'reported_run_provenance':'User-supplied engineering validation; raw 363-test run not independently retained here or relabelled as a fresh run.',
 'full_affected_run_repeated':False,'full_suite_claim':False,'independent_historical_R2_cases':7,
 'replay_proof':EVID+'/replay-proof.json','no_live_provider_calls':True,'kernel_dashboard_stopped_snapshot':EVID+'/stopped-process-observation.json'},
 real_calibration_present=False,real_calibration_classification='FUTURE_RUNTIME_CALIBRATION_EVIDENCE_LIMITATION',
 calibration_ownership_gap_preserved=True,no_new_calibration_item_created=True,dependency_status={d:rows[d]['status'] for d in old['dependencies']},
 DEC03_dependency_DEC02_satisfied=True,DEC03_remaining_dependency_blockers=['STR-04'],DEC03_transitive_remaining_blockers=['STR-03'],DEC03_own_proof_unmapped=True,
 next_critical_path_item='STR-03',next_critical_path_reason='STR-03 prerequisites STR-01/QNT-08/OWN-02 are CLOSED; STR-03 closure unlocks STR-04, the remaining direct prerequisite of DEC-03. Evidence mapping first, no first-live activation.',
 next_critical_path_chain=['STR-03','STR-04','DEC-03'],next_item_selected=False,next_item_started=False,
 original_conditions_and_edges_preserved=True,status_counts_after=counts,source_sha256=hashes,application_code_changed=False,
 runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False)
(EP/'closure.json').write_text(json.dumps(record,indent=2)+'\n')
row=copy.deepcopy(old);row.update(status='CLOSED',implementation_evidence='Single allocator comparator and inspectable outcomes/CASH/confidence at 9f36fc7 / f1bbd7a; default R3 tie-CASH with exact R2 history replay. '+EVID+'/closure.json',
 runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; no deployed/live/provider proof claimed',
 latest_evidence='195 fresh relevant tests passed; engineering reported 363 affected passes, not fully repeated here. Seven actual pre-R3/current-R2 replay cases match exactly. R3 distinct-expression positive top tie selects CASH / EXACT_ECONOMIC_TIE_NO_DOMINANT_CANDIDATE; explicit candidate/CASH reasons, confidence states and non-authoritative analyst conflict preserved. Production calibration remains absent.',
 next_proof='None for exact DEC-02 architecture/offline condition. DEC-03 DEC-02 dependency satisfied; STR-04 remains open, transitively STR-03. Next critical-path recommendation STR-03 evidence mapping, then STR-04, then DEC-03; no successor selected or started.',
 resolution_at_commit=HEAD,implementation_commit=HEAD,updated='2026-10-07',closure_evidence=EVID+'/closure.json',evidence_mapping=EVID+'/REPORT.md',
 evidence_validity='Pinned architecture/offline verification at f1bbd7a; no production calibration/profitability, economic readiness or deployed/runtime claim.',
 allocator_default=A.VERSION,historical_replay_supported=[A.VERSION_R2])
def replace_block(text,key,value):
 m=re.search(r'(?m)^'+re.escape(key)+r':\n',text);assert m,key
 end=re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:',text[m.end():]);stop=m.end()+end.start() if end else len(text)
 return text[:m.start()]+yaml.safe_dump({key:value},sort_keys=False,allow_unicode=True,width=105)+'\n'+text[stop:]
selection=copy.deepcopy(docs['tracker']['current_build_selection'])
for k in ['last_completed_item','last_terminal_item']:selection[k]='DEC-02'
selection['eligible_items']=[x for x in selection['eligible_items'] if x!='DEC-02']
if 'STR-03' not in selection['eligible_items']:selection['eligible_items'].append('STR-03')
selection['next_recommended_item']='STR-03'
selection['selection_reason']='DEC-02 exact deterministic comparison condition CLOSED, default allocator R3 and historical R2 replay preserved. DEC-03 remains gated by STR-04, transitively STR-03. STR-03 dependencies CLOSED, recommended for evidence mapping only; unselected and not started. No activation authority.'
package=dict(id='DEC-02',status='CLOSED',result='CLOSED_ARCHITECTURE_OFFLINE',mode='ENGINEERING_ONLY',scope='ARCHITECTURE_OFFLINE',objective=row['required_behavior'],implementation_commit=HEAD,
 evidence=[EVID+'/REPORT.md',EVID+'/closure.json'],implementation_complete=True,canonical_reconciliation_pending=False,
 runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False)
p=ROOT/paths['tracker'];text=p.read_text();start=text.index('- id: DEC-02\n');end=text.index('- id: DEC-03\n',start)
text=text[:start]+yaml.safe_dump([row],sort_keys=False,allow_unicode=True,width=105)+text[end:]
text=text.replace('version: 1.50-port02-canonical-sol1','version: 1.51-dec02-canonical-sol1',1).replace('current_active_item: PORT-02','current_active_item: DEC-02',1)
text=text.replace('last_terminal_item: PORT-02','last_terminal_item: DEC-02',1).replace('last_terminal_evidence: docs/tracker/evidence/port02-canonical-sol1/REPORT.md','last_terminal_evidence: '+EVID+'/REPORT.md',1)
text=re.sub(r'  status_counts:\n(?:    [A-Z_]+: \d+\n)+','\n'.join('  '+line for line in yaml.safe_dump({'status_counts':counts},sort_keys=False).splitlines())+'\n',text,count=1)
text=text.replace('  next_recommended_item: null\n','  next_recommended_item: STR-03\n',1)
text=replace_block(text,'current_build_selection',selection);text+='\n'+yaml.safe_dump({'dec02_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105);p.write_text(text)
for which,key in [('state','current_active_work'),('next','work_package')]:
 p=ROOT/paths[which];text=replace_block(p.read_text(),key,package);text=replace_block(text,'current_build_selection',selection)
 text+='\n'+yaml.safe_dump({'dec02_previous_selection':docs[which][key],'dec02_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105);p.write_text(text)
banner='> Current [SOL-1][DEC-02]: **CLOSED at architecture/offline scope** on `9f36fc7` / `f1bbd7a`. Default allocator **R3** selects CASH on exact positive top ties across distinct expressions; **R2** historical replay remains exact. Explicit candidate/CASH reasons and confidence/source states; analyst evidence grants no selection authority. Production calibration absent; shadow R1 label preserved. DEC-03 still gated by STR-04 (transitively STR-03); next recommendation STR-03, unselected and not started. [Evidence](evidence/dec02-canonical-sol1/REPORT.md). Earlier summaries below are historical.\n\n'
p=ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md';text=p.read_text();start=text.index('### DEC-02 —');end=text.index('### DEC-03 —',start);section=text[start:end]
for k,v in row.items():
 if old.get(k)!=v:
  line='- **'+k+'**: '+str(v);pattern=r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$'
  if re.search(pattern,section):section=re.sub(pattern,lambda m:line,section)
  else:section=section.rstrip()+'\n'+line+'\n\n'
p.write_text(banner+text[:start]+section+text[end:]+'\nDEC-02 canonical status counts: '+', '.join(f'{k} {v}' for k,v in counts.items())+' (178 total).\n')
p=ROOT/'docs/tracker/README.md';p.write_text(banner+p.read_text())
report='# DEC-02 canonical CLOSED\n\n'+old['closure_condition']+' '+old['failure_regression_proof']+'\n\nThe exact condition is satisfied at architecture/offline scope. DEC-01 and PORT-02 dependencies are CLOSED; original closing condition and dependency edges preserved.\n\n'
for k,v in mapping.items():report+=k.replace('_',' ').capitalize()+': '+v+'\n\n'
report+='Fresh verification: 195 tests passed, zero failures/errors/skips ('+', '.join(k.rsplit('.',1)[-1]+': '+str(v) for k,v in by_file.items())+'). User reported 363 affected passes; raw full-run evidence was not independently retained or fully rerun here. No full-suite claim. Mandatory luffy-pytest runner uses unique /mnt basetemp. Independent replay-proof loads actual pre-R3 source at 47761ee and verifies seven R2 cases byte-for-byte, then checks distinct R3 ID, cross-version refusal and tie permutation stability. Fixtures are synthetic only.\n\n'
report+='scripts/portfolio_allocator_shadow.py independently labels receipts LUFFY-PORTFOLIO-ALLOCATOR-R1; this label is preserved unchanged and is not the canonical allocate dispatch version. Source hashes/committed bytes are checked. Production model registries remain empty and REAL CALIBRATION PRESENT: NO. PORT-02 future calibration limitation/ownership gap remains unchanged.\n\n'
report+='Kernel/Dashboard module processes absent in read-only process snapshot; user reports they remained stopped during engineering validation. No operational commands or live/provider calls. No implementation changes. Only DEC-02 row changes; other 177 rows and readiness gates/history unchanged.\n\n'
report+='DEC-03 DEC-02 dependency SATISFIED. Remaining direct dependency STR-04; transitive STR-03. DEC-03 own account-position/conflict/lineage/plan proof remains unmapped. Next critical-path recommendation STR-03 (its STR-01/QNT-08/OWN-02 prerequisites CLOSED), then STR-04, then DEC-03. Recommendation is not selection/start or first-live authorization.\n\n'
report+='Status counts: '+ '; '.join(f'{k} {v}' for k,v in counts.items())+' (178 total). Control-plane commit is the enclosing Git commit. [Closure](closure.json), [verification](focused.xml), [actual R2 replay proof](replay-proof.json), [control checks](control-checks.json).\n'
(EP/'REPORT.md').write_text(report)
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text());m.update(snapshot_version='1.51-dec02-canonical-sol1',control_baseline_revision=HEAD,current_build_selection=selection,dec02_canonical_reconciliation=record)
existing={e['path'] for e in m['files']}
for p in sorted(EP.iterdir()):
 if p.is_file() and p.name!='control-checks.json' and str(p.relative_to(ROOT)) not in existing:
  m['files'].append(dict(path=str(p.relative_to(ROOT)),role='DEC02_CANONICAL_CONTROL_OR_EVIDENCE',synchronization_required=True))
for e in m['files']:
 raw=(ROOT/e['path']).read_bytes();e.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
(ROOT/'docs/tracker/bundle-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps(dict(status_counts=counts,DEC03_remaining_blockers=['STR-04'],next_critical_path_item='STR-03')))
