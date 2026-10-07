"""Authorized DEC-01 control-plane reconciliation; no implementation edits."""
from pathlib import Path
from collections import Counter
import copy, hashlib, json, re, subprocess
import xml.etree.ElementTree as ET
import yaml
ROOT=Path(__file__).resolve().parents[4]
EP=Path(__file__).resolve().parent
EVID=str(EP.relative_to(ROOT))
HEAD=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
assert HEAD=='5d66327b3a0e31a0177feb6d46b8b67fcaa4559e'
paths={'tracker':'docs/tracker/LUFFY_Product_Tracker_v1.yaml','state':'STATE.yaml','next':'NEXT.yaml'}
docs={k:yaml.safe_load((ROOT/p).read_text()) for k,p in paths.items()}
(EP/'control-before.json').write_text(json.dumps(docs,indent=2)+'\n')
rows={r['id']:r for r in docs['tracker']['items']}
old=rows['DEC-01']; assert old['status']=='BLOCKED'
assert all(rows[d]['status']=='CLOSED' for d in old['dependencies'])
assert rows['QNT-01']['status']=='CLOSED'
counts=dict(Counter(r['status'] for r in rows.values()));counts['CLOSED']+=1;counts['BLOCKED']-=1
root=ET.parse(EP/'focused.xml').getroot()
suite=root.find('testsuite'); assert int(suite.get('failures'))==int(suite.get('errors'))==0
by_file=Counter(t.get('classname') for t in suite.findall('testcase'))
assert by_file['tests.test_dec01_opportunity_context']==61
assert by_file['tests.test_stage7_replay_world_closure_r2']==14
proof=json.loads((EP/'normal-receipt-proof.json').read_text())
assert proof['exact_real_receipt_bound'] and proof['reload_identical'] and proof['zero_allocator_candidates']
source_paths=['trader/portfolio/opportunity_live.py','trader/portfolio/current.py','trader/cognition/decision_analysts.py',
 'scripts/opportunity_context_shadow.py','trader/portfolio/source_adapter.py','trader/portfolio/candidate_bridge.py',
 'trader/portfolio/economics.py','trader/cognition/opportunity_context.py','tests/test_dec01_opportunity_context.py',
 'tests/test_stage6_normal_sources.py','tests/test_stage7_replay_world_closure_r2.py','config.yaml']
hashes={}
for p in source_paths:
 raw=(ROOT/p).read_bytes();assert raw==subprocess.check_output(['git','show',HEAD+':'+p],cwd=ROOT),p
 hashes[p]=hashlib.sha256(raw).hexdigest()
mapping={
 'normal_coherent_context':'scripts.opportunity_context_shadow.capture reads persisted decision/cycle/vote measurement packets, exact StrategyVersion and compiled signal identity, Attention/WorldModel cut and complete venue book. produce(decision_grade=True) verifies these and freezes canonical candidate/opportunity identity, required roles, clocks, book digest and analyst provenance. current.freeze calls the actual bridge.build and finalize_decision before candidate admission.',
 'required_optional':'DECISION_REQUIRED is enforced even if callers omit roles. DA.roster declares required analysts; all other enabled analysts are optional by default. Missing/unavailable required analyst or missing/stale required source blocks. Optional absence/unavailability remains individually explicit and does not block the context by default.',
 'cut_coherence':'All analyst packets share the cycle cut, never future; source availability/event/freshness checks enforce the decision cut. Normal analyst expiry shares Attention freshness. WorldModel equals market/scan cut; market/world/signal/version inputs cannot be newer than analyst cut. Book provenance/completeness/freshness are checked and decision_book_binding rejects substitutions. Exact economic Binding/core source is required; costs captured after decision cut refuse, stale costs block, wrong context/cost cut refuses.',
 'production_unavailable_cost':'Production GROSS_MODELS, RESERVE_MODELS and COST_SCOPE_MODELS remain empty. bridge.build produces the real UNAVAILABLE receipt; finalize_decision binds its exact ID/status to the outer envelope, retains null cost values/status/reasons, and marks BLOCKED / REQUIRED_COST_EVIDENCE_UNAVAILABLE. current.freeze continues before candidates.append, so this candidate never reaches allocator inputs. UNKNOWN/UNAVAILABLE never becomes zero. Calibrated production economics is PORT-02, not a DEC-01 closing prerequisite.',
 'decision_identity':'Frozen core LiveReceipt/OpportunityContext is immutable and unchanged by economics. decision-context.v1 binds core receipt/context/opportunity/candidate IDs, exact economics receipt/status, decision cut, frozen book and evaluation clock. Its receipt_id is the decision-grade identity, content-derived and replay checked. Identical saved clocks/receipts reload identically; later revisions create new identities and cannot overwrite earlier core bytes. This is not a promise of identity equality at different evaluation clocks.',
 'evidence_conflict':'DA.analyze retains supporting, opposing, neutral and absent/unavailable records with record IDs, strengths, confidence, input provenance and bundle digest. Conflict flag preserves both sides. Frozen source replay recomputes the partition and refuses forged support-only summaries.',
 'authority_boundary':'Core and outer envelope state authority NONE; context creation/compilation grants no install, Governor/owner, capital or order authority. Existing independent admissions and research/activation fences remain unchanged.'}
record=dict(item='DEC-01',canonical_status='CLOSED',scope='ARCHITECTURE_OFFLINE',engineering_commits=['dcfd2c3f9a036993e5ba96d2aa2136130c04c374',HEAD],
 engineering_closure_commit=HEAD,control_baseline_revision=HEAD,control_plane_commit_resolution='Enclosing Git commit containing this reconciliation; no self-referential hash.',
 closing_condition=old['closure_condition'],failure_regression_proof=old['failure_regression_proof'],closing_condition_satisfied=True,
 closure_mapping=mapping,evidence=EVID+'/closure.json',dependency_status={d:rows[d]['status'] for d in old['dependencies']},
 validation={'fresh_focused_passes':int(suite.get('tests')),'fresh_by_file':dict(by_file),'fresh_failures':0,
 'fresh_normal_exact_receipt_and_reload_passes':1,'runner':'/home/sarmad/.local/bin/luffy-pytest',
 'retained_user_report':{'dedicated_passed':61,'affected_passed':476,'stage7_fixture_failures':3,'corrected_stage7_file_passed':14,
 'attribution':'User-supplied engineering validation; raw 476/3 log not independently retained here. Committed fixture diff and fresh full corrected file corroborate contract incompatibility correction. No combined affected-suite green claim.'},
 'production_models_added':False,'live_provider_calls':0,'full_suite_claim':False},
 production_unavailable_cost_semantics='VERIFIED: exact real UNAVAILABLE receipt -> decision-context.v1 BLOCKED / REQUIRED_COST_EVIDENCE_UNAVAILABLE; null costs; zero allocator candidates',
 normal_receipt_proof=EVID+'/normal-receipt-proof.json',original_conditions_and_edges_preserved=True,
 status_counts_after=counts,source_sha256=hashes,PORT02_now_eligible=True,PORT02_selected=False,PORT02_started=False,
 DEC02_now_eligible=False,DEC02_remaining_blockers=['PORT-02'],DEC02_own_condition_unproven=True,
 application_code_changed=False,runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False)
(EP/'closure.json').write_text(json.dumps(record,indent=2)+'\n')
row=copy.deepcopy(old)
row.update(status='CLOSED',implementation_evidence='Decision-grade coherent normal Opportunity Context and exact real economics envelope on dcfd2c3 / 5d66327; '+EVID+'/closure.json',
 runtime_evidence='ARCHITECTURE_OFFLINE_ONLY; no deployed/live/provider proof claimed',
 latest_evidence='61 dedicated passes; fresh affected normal-source and corrected Stage-7 tests passed. Independent normal-path exact UNAVAILABLE receipt binding and reload proof passed; BLOCKED / REQUIRED_COST_EVIDENCE_UNAVAILABLE, null cost values and no allocator candidate. Retained affected report: 476 passed / 3 Stage-7 fixture incompatibilities; corrected file rerun 14 passed. Production registries remain empty.',
 next_proof='None for exact DEC-01 architecture/offline condition. PORT-02 calibrated production economics is dependency-eligible but unselected and not started. DEC-02 remains gated by PORT-02 and its own unmapped deterministic-comparison proof.',
 resolution_at_commit=HEAD,implementation_commit=HEAD,updated='2026-10-07',closure_evidence=EVID+'/closure.json',evidence_mapping=EVID+'/REPORT.md',
 evidence_validity='Pinned architecture/offline verification at 5d66327; no deployment, economics calibration or later runtime revalidation claim.')
def replace_block(text,key,value):
 m=re.search(r'(?m)^'+re.escape(key)+r':\n',text);assert m,key
 end=re.search(r'(?m)^[A-Za-z_][A-Za-z0-9_]*:',text[m.end():]);stop=m.end()+end.start() if end else len(text)
 return text[:m.start()]+yaml.safe_dump({key:value},sort_keys=False,allow_unicode=True,width=105)+'\n'+text[stop:]
selection=copy.deepcopy(docs['tracker']['current_build_selection'])
for k in ['last_completed_item','last_terminal_item']:selection[k]='DEC-01'
selection['eligible_items']=[x for x in selection['eligible_items'] if x!='DEC-01']+['PORT-02']
selection['current_blocked_items']=[x for x in selection['current_blocked_items'] if x!='DEC-01']
selection['selection_reason']='DEC-01 exact coherent Context and production UNAVAILABLE-cost binding reconciled CLOSED. PORT-02 dependencies QNT-01 and DEC-01 are CLOSED; eligible but unselected and not started. DEC-02 remains gated by PORT-02 and its own comparison proof. No successor selected.'
package=dict(id='DEC-01',status='CLOSED',result='CLOSED_ARCHITECTURE_OFFLINE',mode='ENGINEERING_ONLY',scope='ARCHITECTURE_OFFLINE',objective=row['required_behavior'],
 implementation_commit=HEAD,evidence=[EVID+'/REPORT.md',EVID+'/closure.json'],implementation_complete=True,canonical_reconciliation_pending=False,
 runtime_execution_authorized=False,provider_enablement_authorized=False,trading_activation_authorized=False,successor_selected=False)
text=(ROOT/paths['tracker']).read_text();start=text.index('- id: DEC-01\n');end=text.index('- id: DEC-02\n',start)
text=text[:start]+yaml.safe_dump([row],sort_keys=False,allow_unicode=True,width=105)+text[end:]
metadata=copy.deepcopy(docs['tracker']['metadata']);metadata.update(version='1.49-dec01-canonical-sol1',current_active_item='DEC-01',status_counts=counts,last_terminal_item='DEC-01',last_terminal_evidence=EVID+'/REPORT.md')
text=replace_block(text,'metadata',metadata);text=replace_block(text,'current_build_selection',selection)
text+='\n'+yaml.safe_dump({'dec01_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105)
(ROOT/paths['tracker']).write_text(text)
for which,key in [('state','current_active_work'),('next','work_package')]:
 p=ROOT/paths[which];text=replace_block(p.read_text(),key,package);text=replace_block(text,'current_build_selection',selection)
 text+='\n'+yaml.safe_dump({'dec01_previous_selection':docs[which][key],'dec01_canonical_reconciliation':record},sort_keys=False,allow_unicode=True,width=105);p.write_text(text)
banner='> Current [SOL-1][DEC-01]: **CLOSED at architecture/offline scope** on `dcfd2c3` / `5d66327`. Normal coherent Context, exact real UNAVAILABLE economics receipt binding, explicit blocked decision, null costs and zero allocator candidates verified. Frozen identity/reload, required/optional analysts, coherent cuts and evidence conflict preserved. PORT-02 eligible, unselected and not started; DEC-02 still gated by PORT-02. [Evidence](evidence/dec01-canonical-sol1/REPORT.md). Earlier summaries below are historical.\n\n'
p=ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md';text=p.read_text();start=text.index('### DEC-01 —');end=text.index('### DEC-02 —',start);section=text[start:end]
for k,v in row.items():
 if old.get(k)!=v:
  line='- **'+k+'**: '+str(v)
  if re.search(r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$',section):section=re.sub(r'(?m)^- \*\*'+re.escape(k)+r'\*\*:.*$',lambda m:line,section)
  else:section=section.rstrip()+'\n'+line+'\n\n'
text=text[:start]+section+text[end:];p.write_text(banner+text+'\nDEC-01 canonical status counts: '+', '.join(f'{k} {v}' for k,v in counts.items())+' (178 total).\n')
p=ROOT/'docs/tracker/README.md';p.write_text(banner+p.read_text())
report='# DEC-01 canonical CLOSED\n\n'+old['closure_condition']+' '+old['failure_regression_proof']+'\n\nThe exact condition is satisfied at architecture/offline scope on `dcfd2c3` and `5d66327`; STR-02, WRLD-05 and DATA-05 dependencies are CLOSED. Original condition and edges are preserved.\n\n'
for k,v in mapping.items():report+=k.replace('_',' ').capitalize()+': '+v+'\n\n'
report+='Validation: fresh '+str(int(suite.get('tests')))+' focused tests passed ('+', '.join(k.rsplit('.',1)[-1]+': '+str(v) for k,v in by_file.items())+'), plus one independent exact normal-receipt/reload proof. All pytest runs used luffy-pytest with /mnt basetemp. The user reported 61 dedicated passes, 476 affected passes / 3 Stage-7 fixture failures, and 14 passes after correcting that fixture file. The committed correction removes obsolete spec-hash rewriting and retains decision_grade=True when rebuilding the fixture context. The fresh full corrected file corroborates its 14 passes. Raw 476/3 log was not independently retained here; no full-suite or rerun-all-affected green claim.\n\n'
report+='Production cost registries remain intentionally empty. No production cost models were added. Calibrated production economics belongs to PORT-02 and is not required for DEC-01. No implementation edits, provider/live calls, runtime activation or successor work occurred. The evidence probe observes the normal bridge return and uses empty production registries after removing fixture-only models; it verifies zero allocator candidates, actual economics receipt equality and disk reload of core/economics/envelope.\n\n'
report+='Only DEC-01 row changes; all other 177 rows and readiness gates remain unchanged. PORT-02 now dependency-eligible (QNT-01 and DEC-01 CLOSED), unselected and not started. DEC-02 is not eligible: PORT-02 remains its dependency blocker; DEC-02 own comparison/CASH/reason/confidence/economics condition also remains unmapped.\n\n'
report+='Status counts: '+ '; '.join(f'{k} {v}' for k,v in counts.items())+' (178 total). Control-plane commit: enclosing Git commit containing this reconciliation. [Closure](closure.json), [fresh focused results](focused.xml), [normal receipt proof](normal-receipt-proof.json), [control checks](control-checks.json).\n'
(EP/'REPORT.md').write_text(report)
m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text());m.update(snapshot_version=metadata['version'],control_baseline_revision=HEAD,current_build_selection=selection,dec01_canonical_reconciliation=record)
existing={e['path'] for e in m['files']}
for p in sorted(EP.iterdir()):
 if p.is_file() and p.name!='control-checks.json' and str(p.relative_to(ROOT)) not in existing:
  m['files'].append(dict(path=str(p.relative_to(ROOT)),role='DEC01_CANONICAL_CONTROL_OR_EVIDENCE',synchronization_required=True))
for e in m['files']:
 raw=(ROOT/e['path']).read_bytes();e.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
(ROOT/'docs/tracker/bundle-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps(dict(status_counts=counts,fresh_by_file=dict(by_file))))
