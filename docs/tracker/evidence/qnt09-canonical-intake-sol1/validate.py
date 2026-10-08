"""Offline control validation; --committed verifies enclosing Git bytes."""
from pathlib import Path
from collections import Counter
import hashlib,json,subprocess,sys,re
import yaml
from trader.dashboard.tracker import UniqueLoader,Ledger,NextDocument
ROOT=Path(__file__).resolve().parents[4]
BASE='a355486900371f6e5ba9605e2fb84da661139489'
TP='docs/tracker/LUFFY_Product_Tracker_v1.yaml'
E='docs/tracker/evidence/qnt09-canonical-intake-sol1'
CONTROL=[TP,'STATE.yaml','NEXT.yaml','docs/tracker/LUFFY_Product_Tracker_v1.md','docs/tracker/README.md','docs/tracker/bundle-manifest.json']
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def prior(p):return git('show',BASE+':'+p)
def load(p):return yaml.load((ROOT/p).read_bytes(),Loader=UniqueLoader)
def verify(committed=False):
 old=yaml.load(prior(TP),Loader=UniqueLoader);now=load(TP)
 Ledger.model_validate(now);NextDocument.model_validate(load('NEXT.yaml'))
 a={r['id']:r for r in old['items']};b={r['id']:r for r in now['items']}
 assert 'QNT-09' not in a and set(b)-set(a)=={'QNT-09'} and len(b)==179
 assert all(b[k]==v for k,v in a.items())
 def raw_rows(text):
  section=text.split('items:\n',1)[1].split('\nsources:',1)[0]
  return {m.group(1):m.group(0) for m in re.finditer(r'(?m)^- id: ([A-Z]+-[0-9]{2})\n.*?(?=^- id: |\Z)',section,re.S)}
 before=raw_rows(prior(TP).decode());after=raw_rows((ROOT/TP).read_text());assert all(after[k]==v for k,v in before.items())
 assert b['PORT-02']['status']=='CLOSED'
 q=b['QNT-09'];assert q['dependencies']==['STR-02','PORT-02','RISK-02'] and all(b[d]['status']=='CLOSED' for d in q['dependencies'])
 assert q['parent_ids']==['PORT-02'] and q['related_items']==['PORT-05','EXE-07','DATA-10','QNT-04','LRN-05','REL-01']
 visited=set();visiting=set()
 def visit(k):
  assert k not in visiting,('cycle',k)
  if k in visited:return
  visiting.add(k)
  for d in b[k]['dependencies']:visit(d)
  visiting.remove(k);visited.add(k)
 for k in b:visit(k)
 counts=dict(Counter(r['status'] for r in b.values()));assert counts==now['metadata']['status_counts']
 assert now['readiness_gates']==old['readiness_gates'] and now['current_build_selection']==old['current_build_selection']
 for k in old:
  if k not in ['metadata','items','sources']:assert now[k]==old[k],k
 for k,v in old['metadata'].items():
  if k not in ['version','row_count','status_counts']:assert now['metadata'][k]==v,k
 assert now['sources'][:-1]==old['sources'] and now['sources'][-1]['id']=='S19'
 record=json.loads((ROOT/E/'intake.json').read_text())
 for f in ['STATE.yaml','NEXT.yaml']:
  d=load(f);p=yaml.load(prior(f),Loader=UniqueLoader)
  assert set(d)-set(p)=={'qnt09_canonical_intake'} and all(d[k]==v for k,v in p.items())
  assert d['qnt09_canonical_intake']==record
 assert now['qnt09_canonical_intake']==record
 assert q['status']=='AWAITING_OWNER' and q['release_scope']=='REAL_MONEY_RELEASE'
 assert q['numerical_policy_status']=='NOT_APPROVED' and not q['production_registration_authorized'] and not q['first_live_permission'] and not q['profitability_required_for_closure']
 md=(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.md').read_text();assert md.count('### QNT-09 —')==1
 # All old Markdown tracker row sections retain their bytes.
 def sections(text):return {m.group(1):m.group(0) for m in re.finditer(r'(?m)^### ([A-Z]+-[0-9]{2}) —.*?(?=^### |\Z)',text,re.S)}
 oldmd=sections(prior('docs/tracker/LUFFY_Product_Tracker_v1.md').decode());newmd=sections(md)
 for k,v in oldmd.items():assert newmd[k].startswith(v),k
 m=json.loads((ROOT/'docs/tracker/bundle-manifest.json').read_text());assert m['row_count']==179 and m['status_counts']==counts and m['qnt09_canonical_intake']==record
 for e in m['files']:
  raw=(ROOT/e['path']).read_bytes();assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'],e['path']
 allowed=set(CONTROL+[str(p.relative_to(ROOT)) for p in (ROOT/E).iterdir() if p.is_file()])
 if committed:
  changed=set(git('diff','--name-only',BASE,'HEAD').decode().splitlines());assert changed<=allowed,changed-allowed
  assert set(CONTROL)<=changed
  for p in changed:assert git('show','HEAD:'+p)==(ROOT/p).read_bytes(),p
  git('merge-base','--is-ancestor',BASE,'HEAD')
 assert prior('SDD.md')==(ROOT/'SDD.md').read_bytes()
 assert prior('docs/tracker/LUFFY_Product_Tracker_v1.xlsx')==(ROOT/'docs/tracker/LUFFY_Product_Tracker_v1.xlsx').read_bytes()
 return dict(result='PASS',canonical_base=BASE,qnt09_absent_before=True,tracker_total=179,status_counts=counts,all_178_existing_rows_semantically_and_byte_preserved=True,all_existing_dependency_edges_preserved=True,dependency_graph_acyclic=True,dependencies={d:b[d]['status'] for d in q['dependencies']},port02_status='CLOSED',schema_validation='PASS',manifest_files_verified=len(m['files']),existing_selection_preserved=True,committed_bytes_verified=committed)
if __name__=='__main__':print(json.dumps(verify('--committed' in sys.argv),indent=2))
