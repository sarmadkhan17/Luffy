"""Run the same new regression cases against the untouched release baseline."""
from pathlib import Path
import os,sys,socket,shutil,json
HERE=Path(__file__).resolve().parent
BASE=Path('/mnt/luffy-data/sol1-subsystem-review-07-20261019')
CASES={
 'test_spec_writer.py':['test_factory_compatibility_and_malformed_replies_reach_repairs','test_unsupported_exit_never_returns_success_after_repair_limit','test_percentage_examples_use_fractional_contract'],
 'test_analyst.py':['test_redundancy_checks_later_symbols_and_preserves_identity','test_redundancy_incomplete_is_unavailable_not_zero','test_unavailable_redundancy_refuses_before_null'],
 'test_scraper_queues_ideas.py':['test_source_yield_records_fetched_selected_and_accepted','test_legacy_selected_only_measurement_cannot_drive_source_rank','test_pure_research_passage_reaches_queue_even_with_zero_strategy_score','test_selected_intake_does_not_count_rejected_queue_writes'],
 'test_analyst_tv_and_vault.py':['test_a_profitable_tv_run_agrees','test_a_losing_tv_run_disagrees_but_is_still_only_advice'],
 'test_tv_harness.py':['test_timeframe_setting_requires_observed_interval','test_legacy_unverified_cache_does_not_supply_evidence'],
}
CASEDIR=HERE/'baseline-tests';CASEDIR.mkdir(exist_ok=True)
args=[]
for name,nodes in CASES.items():
 shutil.copyfile(HERE.parents[1]/'tests'/name,CASEDIR/name)
 args += [str(CASEDIR/name)+'::'+node for node in nodes]
os.chdir(BASE);sys.path.insert(0,str(BASE))
attempts=[]
def blocked(*a,**kw):attempts.append('blocked');raise AssertionError('network forbidden')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=blocked
import pytest
status=pytest.main(['-q','-p','no:cacheprovider','--basetemp='+str(HERE/'baseline-tmp'),
 '--junitxml='+str(HERE/'before.xml'),*args])
(HERE/'before-summary.json').write_text(json.dumps({'baseline':str(BASE),'exit_code':status,'network_attempts':len(attempts),'cases':CASES},indent=2)+'\n')
# The baseline code did not see a native versioned paper close at all.
from tests.test_sol1_paper_cost import Run,cost_cfg
from trader.brain import postmortem as PM
r=Run(HERE/'baseline-tmp'/'paper-native',cost_cfg())
r.paper.capture=None
# PaperExecutor has no external observer installed in this isolated fixture.
r.trade('win');r.complete()
(HERE/'before-native-health.json').write_text(json.dumps({'native_paper_rows':len(r.j.query('SELECT id FROM versioned_paper_trades')), 'legacy_health':PM.book_health(r.j)},indent=2)+'\n')
