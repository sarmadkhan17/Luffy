"""Rebuild the D7 delivery manifest after all verification processes finish."""
import json
import re
import subprocess
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from trader.strategy import exit_policy as E
from trader.strategy.compile import COMPILER_VERSION,FEATURE_VERSION
OUT=ROOT/'reports/review07-d7'
def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
logs=['verified-tests.txt','verified-signal-tests.txt','shared-vector-candidate-tests.txt',
      'final-dsl-sequence-tests.txt','final-regression-corrections.txt']
tests={}
for name in logs:
    text=(OUT/name).read_text()
    assert not re.search(r'\d+ failed',text),name
    results=re.findall(r'^\d+ passed[^\n]*$',text,re.M)
    assert results,name+' is not complete'
    tests[name]=results[-1]
reserved=['trader/data/feed.py','trader/data/market_provenance.py',
          'trader/strategy/rolling.py','trader/strategy/vector_backtest.py',
          'trader/strategy/spec_evidence.py','trader/research/evaluate.py']
proof={p:subprocess.run(['git','diff','--quiet','4cda84d','--',p],cwd=ROOT).returncode==0 for p in reserved}
assert all(proof.values())
for name,marker in [('synthetic-equivalence.txt','ENGINE EQUIVALENCE: PASS'),('synthetic-benchmark.txt','BENCH: PASS')]:
    assert marker in (OUT/name).read_text()
subprocess.run(['git','diff','--check'],cwd=ROOT,check=True)
subprocess.run(['git','apply','--check',str(OUT/'PENDING-D6-vector-ATR-observations.patch')],cwd=ROOT,check=True)
stat={}
for directory in ('trader','tests'):
    rows=[x.split('\t') for x in git('diff','--numstat','4cda84d','--',directory).splitlines()]
    stat[directory]={'files':len(rows),'added':sum(int(x[0]) for x in rows),'removed':sum(int(x[1]) for x in rows)}
    stat[directory]['net']=stat[directory]['added']-stat[directory]['removed']
manifest=dict(source_head=git('rev-parse','HEAD'),base=git('rev-parse','4cda84d'),
    branch=git('branch','--show-current'),compiler_version=COMPILER_VERSION,feature_version=FEATURE_VERSION,
    exit_semantics_id=E.EXIT_SEMANTICS_ID,contract=E.CONTRACT,tests=tests,code_change=stat,
    d6_reserved_unchanged=proof,shared_vector_patch_applied=False,
    signal_exit_fill_status='PENDING_D6_EXECUTABLE_PRICE_CONTRACT_NO_SAME_CLOSE_FILL',
    source_commits=git('log','--format=%h %s','4cda84d..HEAD').splitlines(),
    independent_d7_status='IMPLEMENTED_WITH_EXPLICIT_CROSS_OWNER_DEPENDENCIES',
    ready_for_standalone_deployment=False,live_support_claimed=False,
    scope='synthetic offline verification only; no historical profitability/qualification claim')
(OUT/'verification.json').write_text(json.dumps(manifest,indent=2)+'\n')
ancestry={short:subprocess.run(['git','merge-base','--is-ancestor',short,'4cda84d'],cwd=ROOT).returncode==0
          for short in ('ef9fca8','94b9000','dcb5c1a')}
(OUT/'integration-safety.json').write_text(json.dumps(dict(base=manifest['base'],worktree=str(ROOT),
    ancestry=ancestry,source_commits=manifest['source_commits'],d6_reserved_unchanged=proof,
    broader_data_research_paths_unchanged=subprocess.run(['git','diff','--quiet','4cda84d','--','trader/data','trader/research'],cwd=ROOT).returncode==0,
    operational_mutations='NONE: no deployment, restart, production DB, orders, risk/control/CI changes'),indent=2)+'\n')
print(json.dumps({'tests':tests,'code_change':stat},indent=2))
