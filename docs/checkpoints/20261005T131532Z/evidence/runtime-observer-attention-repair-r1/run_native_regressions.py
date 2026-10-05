import subprocess,json,pathlib,sys
E=pathlib.Path(__file__).parent
sets=[['evidence_zlib','bounded_evidence_latency','portfolio_allocator','portfolio_allocator_shadow','portfolio_evidence','portfolio_common_factor','portfolio_common_factor_shadow','runtime_portfolio_integration','opportunity_context','opportunity_live_integration'],['candle_store_transaction_safety','candle_cache_reuse','derivatives','reference_store','ref_live','ref_sources','ref_recorder','stage1_data_provenance_pit','stage1_entry_authority','stage1_pit_engine_gates','stage1_pit_blocker_fix','journal_evidence_repair'],['exits','protective_stops','protection_snapshot','native_exit_provenance','exchange_exit_is_size_aware','exit_books_the_fill','spec_exits_are_honoured','attention_telemetry','attention_kernel_wiring','attention_selection_persistence','attention_learning','stage1_recovery_monitoring','historical_outcome_capture','observer_attention_repair']]
results=[]
for i,files in enumerate(sets,1):
 cmd=[sys.executable,'-m','pytest','-q']+['tests/test_'+f+'.py' for f in files]+['--basetemp=.pytest-native-group-'+str(i)]
 with (E/('native-regressions-'+str(i)+'.txt')).open('w') as out:r=subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT)
 results.append(dict(group=i,command=cmd,exit=r.returncode));print(results[-1],flush=True)
 if r.returncode:break
(E/'native-regressions.json').write_text(json.dumps(results,indent=2))
sys.exit(max(r['exit'] for r in results))
