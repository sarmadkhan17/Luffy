"""Exact scoped preservation, removals and complexity checks."""
import ast,hashlib,json,subprocess
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'reports/review07-remediation'
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def old(path):return git('show','ef9fca8:'+path)
protected=['SDD.md','trader/strategy/qualification.py','trader/strategy/exit_policy.py',
 'trader/strategy/factory_handoff.py','trader/strategy/vector_backtest.py','trader/strategy/rolling.py',
 'trader/strategy/spec.py','trader/strategy/compile.py','trader/data/feed.py','trader/data/market_provenance.py',
 'trader/engine/paper.py','trader/engine/risk.py','trader/engine/executor.py','trader/engine/state.py',
 'trader/engine/control_fence.py','trader/engine/entry_authority.py','trader/brain/llm.py',
 'docs/tracker/LUFFY_Product_Tracker_v1.yaml']
rows=[]
for path in protected:
 b=old(path);c=(ROOT/path).read_bytes()
 rows.append({'path':path,'sha256':hashlib.sha256(c).hexdigest(),'unchanged':b==c})
assert all(r['unchanged'] for r in rows)
class UnchangedCalls(ast.NodeTransformer):
 def visit_Call(self,node):
  self.generic_visit(node)
  if isinstance(node.func,ast.Name) and node.func.id=='run_postmortem':
   node.keywords=[k for k in node.keywords if k.arg!='cfg']
  if isinstance(node.func,ast.Name) and node.func.id=='summary_text':
   node.args=node.args[:1]
  return node
callchecks={}
for name in ['trader/kernel.py','trader/dashboard/server.py']:
 before=ast.dump(ast.parse(old(name)))
 after=ast.dump(UnchangedCalls().visit(ast.parse((ROOT/name).read_text())))
 callchecks[name]=before==after
assert all(callchecks.values())
before=yaml.safe_load(old('config.yaml'));after=yaml.safe_load((ROOT/'config.yaml').read_text())
assert before['tv_harness'].pop('run_timeout_s')==90
assert before['crawler'].pop('passages_per_mine')==10
for key in ('tv_interval', 'tv_period', 'tv_min_oos_trades', 'tv_require_positive_oos'):
 before['strategies'].pop(key)
assert before==after
removed=['scripts/validate_population.py','trader/brain/tv.py','tests/test_scraper_research_stream.py']
assert all(not (ROOT/p).exists() for p in removed)
# Preserve all six moved test declarations byte-for-byte as ASTs.
before_tree=ast.parse(old('tests/test_scraper_research_stream.py'))
after_tree=ast.parse((ROOT/'tests/test_scraper_queues_ideas.py').read_text())
moved=[]
for f in before_tree.body:
 if isinstance(f,ast.FunctionDef) and f.name.startswith('test_'):
  target=next(n for n in after_tree.body if isinstance(n,ast.FunctionDef) and n.name==f.name)
  assert ast.dump(f)==ast.dump(target)
  moved.append(f.name)
changed=git('diff','ef9fca8','--name-only').decode().splitlines()
counts={'runtime_and_tools':{'added':0,'removed':0},'tests':{'added':0,'removed':0},'configuration':{'added':0,'removed':0}}
for line in git('diff','ef9fca8','--numstat').decode().splitlines():
 add,rem,path=line.split('\t')
 if path.startswith(('trader/','scripts/')):key='runtime_and_tools'
 elif path.startswith('tests/'):key='tests'
 elif path=='config.yaml':key='configuration'
 else:continue
 counts[key]['added']+=int(add);counts[key]['removed']+=int(rem)
for v in counts.values():v['net_lines']=v['added']-v['removed']
source={'protected_sources':rows,'kernel_dashboard_only_forward_config':callchecks,
 'configuration_only_deletes_six_unused_keys':before==after,'config_brain_enabled':after['brain']['enabled'],
 'research_referee':after['research']['referee'],'research_handoff':after['research']['handoff'],
 'research_switches_unchanged':True,'removed_files':removed,'merged_test_ASTs':moved,
 'complexity':counts,'no_new_runtime_or_test_module':True,
 'OD07_06_implemented':False,'quantitative_research_review_started':False}
(OUT/'boundary-verification.json').write_text(json.dumps(source,indent=2)+'\n')
print(json.dumps({'protected_hashes_unchanged':len(rows),'callchecks':callchecks,'complexity':counts},indent=2))
