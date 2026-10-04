"""Publish engineering evidence/control plane only after every final check passes."""
from pathlib import Path
import hashlib,json,re,subprocess,yaml
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
BASE='53e7c8567c259b2326315a0acdd4b838af030998'
PREFIX=OUT.relative_to(ROOT).as_posix()
def write(name,value): (OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def verified_log(name,count):
    lines=(OUT/name).read_text().splitlines()
    assert 'OFFLINE GUARD: 0 network attempts.' in lines,name
    assert 'PRODUCTION STORE GUARD: 0 attempts.' in lines,name
    summary=next(x for x in reversed(lines) if re.search(r'\d+ passed',x))
    assert re.match(str(count)+r' passed',summary) and not re.search(r'\b(?:failed|error)\b',summary),summary
    return summary
matrix=json.loads((OUT/'final-regression-matrix.json').read_text())
assert len(matrix)==10
r3=verified_log('final-Stage3_research-r5.txt',267)
old=next(r for r in matrix if r['group']=='Stage3_research')
write('superseded-stage3-guard-result.json',old)
old.update(exit_code=0,summary=r3,evidence=PREFIX+'/final-Stage3_research-r5.txt',network_guard_zero=True,production_store_guard_zero=True,command=[str(ROOT/'venv/bin/python'),'-m','pytest','--basetemp','.audit-tmp/Stage3_research_r5','-p','tests.stage1_pit_offline_plugin',*['tests/test_'+s+'.py' for s in ('external_research_router','research_source_registry','investigation_research_family','research_evidence_source_binding','research_control','predictive_research_bridge','research_runner')],'-q'])
old.pop('elapsed_s',None)
assert all(r['exit_code']==0 and r['network_guard_zero'] and r['production_store_guard_zero'] for r in matrix),matrix
verified_log('final-original-and-safety-r3.txt',209)
verified_log('f13-final-review-r2.txt',5)
manifest=json.loads((OUT/'final-review-r5-source.json').read_text())
assert all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h for f,h in manifest['reviewed_sources'].items()),'source changed during read-only review'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==BASE
assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='luffy-final-audit-repair-loop-r1'
ledger=json.loads((OUT/'defect-ledger.json').read_text())
assert all(d['status']=='CLOSED' for d in ledger['defects'].values())
write('final-regression-matrix.json',matrix)
manifest.update(status='PASS',source_mutations_during_review=False)
write('final-review-r5-source.json',manifest)
caller=json.loads((OUT/'final-caller-audit.json').read_text());caller.update(status='PASS',review_round=5)
write('final-caller-audit.json',caller)
flags=dict(NEW_P0_FINDINGS='NONE',NEW_P1_FINDINGS='NONE',REGRESSION_FOUND='NO',AUTHORITY_BYPASS_FOUND='NO',PIT_OR_REPLAY_LEAK_FOUND='NO',DUPLICATE_NEW_EXPOSURE_PATH_FOUND='NO',RECOVERY_BYPASS_FOUND='NO',LIVE_LLM_DEPENDENCY='ZERO',BUILD_IMPLEMENTATION_REVIEW='PASS')
ledger.update(final_review='PASS_READ_ONLY_R5',final_flags=flags,final_regression_matrix=matrix,F8='OPERATIONAL_HANDOFF_PENDING')
write('defect-ledger.json',ledger)
record=dict(package='LUFFY-FINAL-AUDIT-REPAIR-LOOP-R1',result='PASS',iterations=ledger['iterations'],branch='luffy-final-audit-repair-loop-r1',base_head=BASE,reviewed_code_head='UNCOMMITTED',review_mode='ADVERSARIAL_SELF_REVIEW_READ_ONLY',source_manifest=PREFIX+'/final-review-r5-source.json',caller_audit=PREFIX+'/final-caller-audit.json',defect_ledger=PREFIX+'/defect-ledger.json',original_defects={k:'CLOSED' for k in ('F1','F2','F3','F4','F5','F6','F7','F9')},new_defects={k:'CLOSED' for k in ('F10','F11','F12','F13')},regression_matrix='CLEAN',regression_evidence=PREFIX+'/final-regression-matrix.json',final_flags=flags,F8='OPERATIONAL_HANDOFF_PENDING',ready_for_runtime_handoff=True,ready_for_first_controlled_startup=False,luffy_started_on_audited_revision=False,existing_production_processes_untouched=True,uncommitted=True,undeployed=True,venue_requests=0,order_submissions=0,paid_calls=0,next_package='LUFFY-PRESTART-RUNTIME-HANDOFF-R1')
p=ROOT/'STATE.yaml';s=p.read_text();s=s.replace('  reviewed_branch: luffy-stage8-owner-os-closure-r1','  reviewed_branch: luffy-final-audit-repair-loop-r1',1).replace('  reviewed_head: 57ad32332a98518c083b750f9df9d43596d0d531','  reviewed_head: UNCOMMITTED\n  reviewed_base_head: '+BASE+'\n  reviewed_source_manifest: '+PREFIX+'/final-review-r5-source.json',1)
s=s.replace('Stage1 MI-1 through MI-7 are independently reviewed and CLOSED; build_complete remains false pending formal Stage1 closure R2.','Stage1 MI-1 through MI-7 remain CLOSED; final engineering repair/review passes with F1-F7/F9 and F10-F13 CLOSED. F8 runtime handoff remains pending.',1)
s=s.replace('  stage_1_safety_and_truth:\n    state: TESTED\n    build_complete: false','  stage_1_safety_and_truth:\n    state: TESTED\n    build_complete: true\n    latest_build_review: LUFFY-FINAL-AUDIT-REPAIR-LOOP-R1\n    build_implementation_review: PASS',1).replace('    formal_closure: AWAITING_R2\n    pre_start_closure: AWAITING_FORMAL_STAGE1_CLOSURE_R2','    formal_closure: FINAL_ENGINEERING_REVIEW_PASS\n    pre_start_closure: F8_OPERATIONAL_HANDOFF_PENDING',1)
start=s.index('  stage_2_world_model:');end=s.index('  stage_3_attention_and_research:')
section=s[start:end]
section=re.sub(r'      - tests/test_world_\w+\.py\n','',section)
section=section.replace('    proven_facts:', ''.join('      - '+f+'\n' for f in ('tests/test_intelligence_spine.py','tests/test_cognition_attention.py','tests/test_cognition_replay.py','tests/test_stage7_replay_world_closure_r2.py','tests/test_final_audit_learning_time.py',PREFIX+'/final-regression-matrix.json'))+'    proven_facts:',1)
s=s[:start]+section+s[end:]
s+='\n'+yaml.safe_dump({'final_audit_repair_loop_r1':record},sort_keys=False)
yaml.safe_load(s);p.write_text(s)
p=ROOT/'NEXT.yaml';s=p.read_text();s=s.replace('  branch: luffy-stage1-recovery-monitoring-foundation-r1','  branch: luffy-final-audit-repair-loop-r1',1).replace('  working_tree_base_head: fcfe9e0359874b18d65a0b1e1ccfd156d18115e1','  working_tree_base_head: '+BASE,1).replace('  reviewed_code_head: 57ad32332a98518c083b750f9df9d43596d0d531','  reviewed_code_head: UNCOMMITTED\n  reviewed_source_manifest: '+PREFIX+'/final-review-r5-source.json',1)
wp=dict(id='LUFFY-PRESTART-RUNTIME-HANDOFF-R1',type='OPERATIONAL_RUNTIME_HANDOFF',status='SELECTED_NOT_STARTED',mode='CONTROLLED_RUNTIME_HANDOFF',implementation_started=False,purpose='Resolve F8 by establishing one controlled handoff from older production processes while preserving FROZEN containment.',engineering_package=record['package'],engineering_review='PASS',engineering_evidence=PREFIX+'/final-review-r5-source.json',F8='OPERATIONAL_HANDOFF_PENDING',blocking_engineering_defects=[],ready_for_runtime_handoff=True,ready_for_first_controlled_startup=False,acceptance=['Establish one controlled handoff from the existing older runtime; do not launch a second kernel alongside it.','Preserve FROZEN containment and verify intended code revision, producer identity and successful-cycle chronology.','Close F8 separately before considering controlled-startup readiness.'],execution_in_this_package='NOT_PERFORMED',constraints=['This selection does not perform a deployment, shutdown, replacement or LUFFY startup.','Do not claim venue exposure, operational maturity or real-money readiness from offline engineering evidence.'])
start=s.index('work_package:\n');end=s.index('\ncompleted_stage8_owner_os_closure_r1:',start)
s=s[:start]+yaml.safe_dump({'work_package':wp},sort_keys=False)+s[end:]
s+='\n'+yaml.safe_dump({'completed_final_audit_repair_loop_r1':record},sort_keys=False)
yaml.safe_load(s);p.write_text(s)
write('engineering-closure.json',record)
print('ENGINEERING REVIEW PASS; STATE/NEXT UPDATED; F8 PENDING; NO STARTUP')
