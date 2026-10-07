"""Authorized PORT-02 control-plane reconciliation; no implementation changes."""
from pathlib import Path
from collections import Counter
import copy, hashlib, json, re, subprocess
import xml.etree.ElementTree as ET
import yaml
ROOT=Path(__file__).resolve().parents[4];EP=Path(__file__).resolve().parent;EVID=str(EP.relative_to(ROOT))
HEAD=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
assert HEAD=='040b271c23ef2e2c74ddedf62e166b345d8df52a'
paths={'tracker':'docs/tracker/LUFFY_Product_Tracker_v1.yaml','state':'STATE.yaml','next':'NEXT.yaml'}
docs={k:yaml.safe_load((ROOT/p).read_text()) for k,p in paths.items()}
(EP/'control-before.json').write_text(json.dumps(docs,indent=2)+'\n')
rows={r['id']:r for r in docs['tracker']['items']};old=rows['PORT-02']
assert old['status']=='EVIDENCE_TO_MAP' and all(rows[d]['status']=='CLOSED' for d in old['dependencies'])
assert rows['DEC-02']['dependencies']==['DEC-01','PORT-02']
counts=dict(Counter(r['status'] for r in rows.values()));counts['CLOSED']+=1;counts['EVIDENCE_TO_MAP']-=1
suite=ET.parse(EP/'focused.xml').getroot().find('testsuite')
assert int(suite.get('tests'))==206 and all(int(suite.get(k))==0 for k in ['failures','errors','skipped'])
by_file=dict(Counter(t.get('classname') for t in suite.findall('testcase')))
proof=json.loads((EP/'production-unavailable.json').read_text())
assert proof['economic_status']=='UNAVAILABLE' and not proof['real_calibration_present']
assert all(v==0 for v in proof['production_registries'].values())
source_paths=['trader/portfolio/economics.py','trader/portfolio/allocator.py','trader/portfolio/current.py',
 'trader/portfolio/candidate_bridge.py','trader/portfolio/opportunity_live.py','trader/engine/paper_cost_evidence.py',
 'trader/observability/funding_events.py','tests/economics_fixtures.py','tests/test_port02_economics_authority.py',
 'tests/test_expected_net_economics.py','tests/test_portfolio_allocator.py','tests/test_candidate_bridge.py',
 'tests/test_dec01_opportunity_context.py','config.yaml']
hashes={}
for p in source_paths:
 raw=(ROOT/p).read_bytes();assert raw==subprocess.check_output(['git','show',HEAD+':'+p],cwd=ROOT),p
 hashes[p]=hashlib.sha256(raw).hexdigest()
interpretation='The exact row requires an enforced calibrated-authority boundary, not populated production models now. Its explicit missing authority is UNAVAILABLE clause permits absent calibration. When authority exists, registered adapters must validate calibration and exact binding before establishing economics; registration alone is insufficient. Offline fixtures prove contract mechanics only.'
limitation='REAL CALIBRATION PRESENT: NO. Future runtime/calibration evidence limitation; production has no calibrated forward gross, fees, slippage, funding/borrow or reserve authority. The retrospective funding adapter does not establish forward funding/borrow authority. No profitability, economic readiness or production calibration claim.'
ownership={'DATA-10':'Contributes prospective decision/action/outcome and actual fees/timing/resource evidence.',
 'EXE-07':'Contributes actual execution-quality timing, prices/spread/size/depth/fees and reject/partial evidence.',
 'QNT-04':'Owns held-out/prospective evaluation discipline, not automatic calibrated economics.',
 'LRN-05':'Owns production adaptive-policy calibration, not all initial static economics models.'}
gap='No current row completely owns initial gross/reserve/forward-cost calibration.'
policy={'new_tracker_item_required':True,'reason':'PORT-02 new_issue_policy requires a linked issue with reason/reproducer rather than silently expanding this row. A linked future item should assign the initial-calibration ownership gap; not a PORT-02 closure or DEC-02 dependency blocker.',
 'policy_exact_text':old['new_issue_policy'],'policy_interpretation':'Apply linked-issue policy to newly identified future ownership gap, without altering the satisfied row contract.',
 'new_tracker_item_created':False,'creation_authorized':False,'blocks_PORT02_closure':False,'blocks_DEC02_dependency_eligibility':False,
 'future_item_intake_evidence':'Empty production registries and missing forward authority documented in production-unavailable.json; no complete owner among DATA-10/EXE-07/QNT-04/LRN-05.'}
mapping={'no_authority':'Production GROSS_MODELS / RESERVE_MODELS / COST_SCOPE_MODELS are empty. Gross, commission/fee, slippage, forward funding/borrow, reserve and expected net are UNAVAILABLE with null values. Retrospective funding evidence is separate. Missing/unknown economics and capacity never become zero/defaults or allocation; CASH stays available and selected under unavailable/non-positive economics. DEC-01 normal path blocks before allocator candidate admission.',
 'registered_authority_mechanics':'TEST-ONLY adapters validate mechanics: full Binding equality covers exact instrument/market/direction/horizon/quantity/capital/units/context/cut/strategy-version/spec and treatment semantics. Source method/version registration, evidence period, calibration fields, provenance, freshness and reserve linkage are checked. Costs replay existing paper protocol and validate forward scope through registered adapter; wrong/mismatched/corrupt sources or obsolete references refuse authority. Material source/model revisions change content-addressed receipt identity; immutable saved receipts/restart replay stable with the same registered validator contract. Revoked authority blocks revalidation without rewriting stored bytes.',
 'cash_reserve':'Allocator re-verifies economics receipt and context, preserves explicit CASH, rejects unavailable/unknown cost/capacity, and requires strictly positive comparable net to beat CASH. Positive gross cannot conceal non-positive net; omitted reserve makes net UNAVAILABLE.',
 'authority_boundary':'Economics receipt establishes evidence only. It grants no allocation, owner/Risk approval, capital or order authority; existing independent gates remain authoritative.'}
record=dict(item='PORT-02',canonical_status='CLOSED',scope='ARCHITECTURE_OFFLINE',engineering_commit=HEAD,engineering_closure_commit=HEAD,control_baseline_revision=HEAD,
 control_plane_commit_resolution='Enclosing Git commit containing this reconciliation; no self-referential hash.',
 required_behavior=old['required_behavior'],closing_condition=old['closure_condition'],failure_regression_proof=old['failure_regression_proof'],closing_condition_satisfied=True,
 exact_contract_interpretation=interpretation,closure_mapping=mapping,no_authority_semantics='PASS',registered_authority_binding_mechanics='PASS',
 real_calibration_present=False,real_calibration_classification='FUTURE_RUNTIME_CALIBRATION_EVIDENCE_LIMITATION',real_calibration_limitation=limitation,
 production_forward_authority={k:'UNAVAILABLE' for k in ['gross','fee','slippage','funding_borrow','reserve']},retrospective_funding_is_forward_authority=False,
 calibration_ownership=ownership,calibration_ownership_gap=gap,future_item_policy=policy,
 validation={'retained_independent_verification_passed':206,'by_file':by_file,'failures':0,'errors':0,'skipped':0,
 'verification_timestamp':suite.get('timestamp'),'runner':'/home/sarmad/.local/bin/luffy-pytest',
 'provenance':'Exact focused.txt/xml retained from previous closure adjudication at the same 040b271 HEAD; copied unchanged, not rerun or relabelled fresh during reconciliation.',
 'earlier_broader_run_reported_passed':211,'earlier_broader_run_fully_repeated':False,'full_suite_claim':False,
 'fresh_empty_production_registry_probe':EVID+'/production-unavailable.json','production_models_added':False,'provider_live_calls':0},
 dependency_status={d:rows[d]['status'] for d in old['dependencies']},DEC02_dependencies_satisfied=True,DEC02_now_eligible=True,
 DEC02_selected=False,DEC02_started=False,DEC02_own_proof='UNMAPPED; exact deterministic comparison/CASH/reasons/confidence/economics condition remains open',
 original_conditions_and_edges_preserved=True,status_counts_after=counts,source_sha256=hashes,
 application_code_changed=False,runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False)
(EP/'closure.json').write_text(json.dumps(record,indent=2)+'\n')
row=copy.deepcopy(old);row.update(status='CLOSED',implementation_evidence='Calibrated-authority economics boundary and refusal/identity/replay/CASH contract verified at 040b271; fixtures only. '+EVID+'/closure.json',
 runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; REAL_CALIBRATION_PRESENT_NO; no production economic readiness claimed',
 latest_evidence='206 relevant verification tests passed at 040b271; earlier 211-test run broader and not fully repeated. Production forward gross/fee/slippage/funding-borrow/reserve authority absent, null values and expected net UNAVAILABLE; no allocation, CASH preserved. Registered-authority mechanics use TEST-ONLY fixtures, not calibrated production economics.',
 next_proof='None for exact PORT-02 architecture/offline boundary condition. Real calibration remains a future runtime/calibration evidence limitation. Initial gross/reserve/forward-cost calibration has no complete current row owner; linked future item needed under new_issue_policy, reported only and not created. DEC-02 dependency-eligible but unselected and not started; own comparison proof remains open.',
 resolution_at_commit=HEAD,implementation_commit=HEAD,updated='2026-10-07',closure_evidence=EVID+'/closure.json',evidence_mapping=EVID+'/REPORT.md',
 evidence_validity='Pinned architecture/offline verification at 040b271; synthetic authority mechanics only, no production calibration/profitability/economic readiness or deployed/runtime claim.',
 real_calibration_present=False,real_calibration_classification='FUTURE_RUNTIME_CALIBRATION_EVIDENCE_LIMITATION',calibration_ownership_gap=gap,
 future_calibration_item_required=True,future_calibration_item_created=False)
def replace_block(text,key,value):
 m=re.search(r'(?m)^'+re.escape(key)+r':\n',text);assert m,key
 end=re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:',text[m.end():]);stop=m.end()+end.start() if end else len(text)
 return text[:m.start()]+yaml.safe_dump({key:value},sort_keys=False,allow_unicode=True,width=105)+'\n'+text[stop:]
selection=copy.deepcopy(docs['tracker']['current_build_selection'])
for k in ['last_completed_item','last_terminal_item']:selection[k]='PORT-02'
selection['eligible_items']=[x for x in selection['eligible_items'] if x!='PORT-02']+['DEC-02']
selection['selection_reason']='PORT-02 exact economics boundary reconciled CLOSED at architecture/offline scope; production forward authority absent and calibration limitation retained. DEC-02 dependencies DEC-01 and PORT-02 CLOSED; eligible but unselected and not started. Calibration ownership gap requires linked future item under policy; reported only, not created or made a dependency.'
package=dict(id='PORT-02',status='CLOSED',result='CLOSED_ARCHITECTURE_OFFLINE',mode='ENGINEERING_ONLY',scope='ARCHITECTURE_OFFLINE',objective=row['required_behavior'],implementation_commit=HEAD,
 evidence=[EVID+'/REPORT.md',EVID+'/closure.json'],implementation_complete=True,canonical_reconciliation_pending=False,
 runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False)
p=ROOT/paths['tracker'];text=p.read_text();start=text.index('- id: PORT-02\n');end=text.index('- id: PORT-03\n',start)
text=text[:start]+yaml.safe_dump([row],sort_keys=False,allow_unicode=True,width=105)+text[end:]
# Preserve metadata byte formatting except the actual changed fields.
text=text.replace('version: 1.49-dec01-canonical-sol1','version: 1.50-port02-canonical-sol1',1).replace('current_active_item: DEC-01','current_active_item: PORT-02',1)
text=text.replace('last_terminal_item: DEC-01','last_terminal_item: PORT-02',1).replace('last_terminal_evidence: docs/tracker/evidence/dec01-canonical-sol1/REPORT.md','last_terminal_evidence: '+EVID+'/REPORT.md',1)
text=text.replace('    CLOSED: 59\n','    CLOSED: 60\n',1).replace('    EVIDENCE_TO_MAP: 80\n','    EVIDENCE_TO_MAP: 79\n',1)
text=replace_block(text,'current_build_selection',selection);text+='\n'+yaml.safe_dump({'port02_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105);p.write_text(text)
for which,key in [('state','current_active_work'),('next','work_package')]:
 p=ROOT/paths[which];text=replace_block(p.read_text(),key,package);text=replace_block(text,'current_build_selection',selection)
 text+='\n'+yaml.safe_dump({'port02_previous_selection':docs[which][key],'port02_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105);p.write_text(text)
banner='> Current [SOL-1][PORT-02]: **CLOSED at architecture/offline scope** on `040b271`; 206 relevant verification passes retained. REAL CALIBRATION PRESENT: **NO**. Production forward authority remains absent, values null and expected net UNAVAILABLE; no allocation, CASH preserved. Registered-authority mechanics proven with fixtures only. Initial calibration ownership gap needs a linked future item under policy, reported but not created. DEC-02 dependency-eligible, unselected and not started. [Evidence](evidence/port02-canonical-sol1/REPORT.md). Earlier summaries below are historical.\n\n'
p=ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md';text=p.read_text();start=text.index('### PORT-02 —');end=text.index('### PORT-03 —',start);section=text[start:end]
for k,v in row.items():
 if old.get(k)!=v:
  line='- **'+k+'**: '+str(v)
  pattern=r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$'
  if re.search(pattern,section):section=re.sub(pattern,lambda m:line,section)
  else:section=section.rstrip()+'\n'+line+'\n\n'
p.write_text(banner+text[:start]+section+text[end:]+'\nPORT-02 canonical status counts: '+', '.join(f'{k} {v}' for k,v in counts.items())+' (178 total).\n')
p=ROOT/'docs/tracker/README.md';p.write_text(banner+p.read_text())
report='# PORT-02 canonical CLOSED\n\n'+old['required_behavior']+'\n\n'+old['closure_condition']+' '+old['failure_regression_proof']+'\n\n'+interpretation+'\n\n'
for k,v in mapping.items():report+=k.replace('_',' ').capitalize()+': '+v+'\n\n'
report+=limitation+'\n\n'
for k,v in ownership.items():report+=k+': '+v+'\n\n'
report+=gap+' '+policy['reason']+' No new tracker item was created because the owner authorized reporting only. This is future work intake, not a new closing condition or dependency.\n\n'
report+='Verification: 206 tests passed, 0 failed/errors/skipped ('+', '.join(k.rsplit('.',1)[-1]+': '+str(v) for k,v in by_file.items())+'). Exact adjudication logs retained unchanged from /mnt/luffy-data/port02-adjudication at 040b271; not relabelled as a fresh reconciliation test run. Earlier 211-test run was broader and not fully repeated. A fresh interpreter probe confirms empty production registries and null UNAVAILABLE components; synthetic identity only, no production authority. No full-suite claim.\n\n'
report+='Only PORT-02 row changes; all other 177 rows and readiness gates remain unchanged. DEC-01 and PORT-02 satisfy DEC-02 dependencies; DEC-02 is eligible, unselected and not started, with its own condition still unmapped. No implementation/models/provider/live data/runtime activation changes.\n\n'
report+='Status counts: '+ '; '.join(f'{k} {v}' for k,v in counts.items())+' (178 total). Control-plane commit is the enclosing Git commit. [Closure](closure.json), [verification](focused.xml), [production absence probe](production-unavailable.json), [control checks](control-checks.json).\n'
(EP/'REPORT.md').write_text(report)
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text());m.update(snapshot_version='1.50-port02-canonical-sol1',control_baseline_revision=HEAD,current_build_selection=selection,port02_canonical_reconciliation=record)
existing={e['path'] for e in m['files']}
for p in sorted(EP.iterdir()):
 if p.is_file() and p.name!='control-checks.json' and str(p.relative_to(ROOT)) not in existing:
  m['files'].append(dict(path=str(p.relative_to(ROOT)),role='PORT02_CANONICAL_CONTROL_OR_EVIDENCE',synchronization_required=True))
for e in m['files']:
 raw=(ROOT/e['path']).read_bytes();e.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
(ROOT/'docs/tracker/bundle-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps(dict(status_counts=counts,DEC02_now_eligible=True,new_tracker_item_required=True,new_tracker_item_created=False)))
