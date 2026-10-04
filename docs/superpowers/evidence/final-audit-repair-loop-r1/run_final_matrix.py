"""Offline frozen regression matrix; no kernel, venue, provider or deployment invocation."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,subprocess,sys,time,shutil
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
GROUPS={
 'Stage1_data':['stage1_data_provenance_pit','stage1_pit_blocker_fix','stage1_pit_engine_gates','derivatives','feed_pagination','spec_signals_on_closed_bars'],
 'Stage1_entry':['stage1_entry_authority','entry_recovery'],
 'Stage1_exit_accounting':['execution_accounting','reconcile','reconcile_alignment_commits','protective_stops','exits','spec_exits_are_honoured'],
 'Stage2_spine':['intelligence_spine','opportunity_context','cognition_attention','cognition_replay','decision_sources'],
 'Stage3_research':['external_research_router','research_source_registry','investigation_research_family','research_evidence_source_binding','research_control','predictive_research_bridge','research_runner'],
 'Stage4_referee':['research_referee','research_fdr','research_slices','research_calibration'],
 'Stage5_authority':['strategy_factory_handoff'],
 'Stage6_portfolio':['portfolio_allocator','portfolio_common_factor','stage6_closure','stage6_normal_sources','runtime_portfolio_integration','opportunity_live_integration'],
 'Stage7_learning':['learning_application','normal_learning_loop_closure','stage7_replay_world_closure_r2','historical_outcome_capture','legacy_learning_replay_gate'],
 'Stage8_owner':['stage8_owner_os','owner_interface_boundary','owner_reads_m2','owner_frontend_api'],
}
for suites in GROUPS.values():
    for name in suites:assert (ROOT/'tests'/('test_'+name+'.py')).is_file(),name

def run(item):
    name,suites=item
    temporary=ROOT/'.audit-tmp'/name
    command=[sys.executable,'-m','pytest','--basetemp',str(temporary),'-p','tests.stage1_pit_offline_plugin',
             *['tests/test_'+s+'.py' for s in suites],'-q']
    started=time.monotonic()
    path=OUT/('final-'+name+'.txt')
    with path.open('w') as log:
        result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    lines=path.read_text().splitlines()
    summary=next((x for x in reversed(lines) if ' passed' in x or ' failed' in x),'NO_SUMMARY')
    record=dict(group=name,command=command,exit_code=result.returncode,summary=summary,
                elapsed_s=round(time.monotonic()-started,2),evidence=path.relative_to(ROOT).as_posix(),
                network_guard_zero='OFFLINE GUARD: 0 network attempts.' in lines,
                production_store_guard_zero='PRODUCTION STORE GUARD: 0 attempts.' in lines)
    shutil.rmtree(temporary,ignore_errors=True)  # task-owned synthetic fixtures only
    print(json.dumps(record),flush=True)
    return record

with ThreadPoolExecutor(2) as pool:
    results=list(pool.map(run,GROUPS.items()))
(OUT/'final-regression-matrix.json').write_text(json.dumps(results,indent=2)+'\n')
raise SystemExit(any(r['exit_code'] or not r['network_guard_zero'] or not r['production_store_guard_zero'] for r in results))
