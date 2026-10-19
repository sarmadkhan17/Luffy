import ast,hashlib,json,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'reports/review07-remediation'
runs=[];latest={}
for name in ['focused','consumers','redundancy','impact','factory-reader','final-boundary']:
 p=OUT/(name+'.xml');tree=ET.parse(p).getroot();suites=tree.findall('testsuite')
 result={'name':name,'suites':[s.attrib for s in suites]}
 for case in tree.iter('testcase'):
  key=case.attrib.get('classname','')+'::'+case.attrib['name']
  state='failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
  latest[key]={'status':state,'last_run':name}
 runs.append(result)
unresolved={k:v for k,v in latest.items() if v['status']=='failed'}
assert not unresolved,unresolved
network={}
for name in ['focused','consumers','redundancy','impact','factory-reader','final-boundary','before']:
 p=OUT/(name+'-summary.json')
 if p.exists():network[name]=json.loads(p.read_text())['network_attempts']
assert not any(network.values())
result={'runs':runs,'unique_final_cases':len(latest),'unique_final_status_counts':{s:sum(v['status']==s for v in latest.values()) for s in ['passed','failed','skipped']},'unresolved_failures':unresolved,'network_attempts':network,'scope':'Focused/impact suites only; overlapping runs are not summed; the stale import-audit failure is superseded by strict reader-guard reruns.'}
(OUT/'test-results.json').write_text(json.dumps(result,indent=2)+'\n')
removed={'active_set','_null_percentiles','repair_params','evaluate_manifest','_find_tester_tab','test_symbol_for','TRUSTED_AGGREGATORS'}
references=[];pm_calls=[];research_readers=[]
for scope in ['trader','scripts','tests']:
 for p in (ROOT/scope).rglob('*.py'):
  tree=ast.parse(p.read_text());rel=str(p.relative_to(ROOT))
  for node in ast.walk(tree):
   if isinstance(node,ast.Name) and node.id in removed or isinstance(node,ast.Attribute) and node.attr in removed:
    references.append({'path':rel,'line':node.lineno,'node':ast.unparse(node)})
   if isinstance(node,ast.Call):
    call=ast.unparse(node.func)
    if call in ('run_postmortem','summary_text') and scope=='trader':pm_calls.append({'path':rel,'line':node.lineno,'call':ast.unparse(node)})
    if scope=='trader' and any(call.endswith('.'+x) for x in ('pending','next_idea')):
     for kw in node.keywords:
      if kw.arg=='stream' and (isinstance(kw.value,ast.Constant) and kw.value.value=='research' or isinstance(kw.value,ast.Attribute) and kw.value.attr=='RESEARCH'):
       research_readers.append({'path':rel,'line':node.lineno,'call':ast.unparse(node)})
assert not references,references
assert len(pm_calls)==3,pm_calls
assert not research_readers,research_readers
(OUT/'consumer-checks.json').write_text(json.dumps({'removed_symbol_executable_references':references,'postmortem_calls':pm_calls,'research_stream_application_readers':research_readers,'source_producers':['trader/brain/scraper.py:Scraper.harvest_once','trader/brain/crawler.py:DeepCrawler.crawl_once (strategy only)'],'research_requirement':'Designate the existing question/Bank/experiment intake reader; preserve idea ID/URL/text lineage and per-stream accepted/refused/deferred consumption. No parallel engine or scheduling activation.','retained_historical_consumers':['dashboard.owner_reads._postmortem','dashboard.server.last_autopsy','frontend Evidence historical postmortem view'],'deleted_legacy_dependency':'trader/brain/tv.py: CLI was sole application importer; no source references remain'},indent=2)+'\n')
d=json.loads((OUT/'owner-decisions.json').read_text())
findings={'01':['B07-01','B07-02','B07-03','B07-04'],'02':['B07-05','B07-08'],'03':['B07-07'],'04':['B07-06','B07-09','B07-10'],'05':['B07-11']}
for row in d['decisions']:
 suffix=row['id'][-2:]
 if suffix=='06':continue
 row.update(status='IMPLEMENTED_OFFLINE_VERIFIED',owner_response='Authorized by the current OD07-01 through OD07-05 implementation assignment',implementation_authorized=True,findings=findings[suffix],evidence='RECONCILIATION.md; test-results.json; boundary-verification.json; consumer-checks.json',deployed=False)
 if suffix=='02':row['remaining_cross_owner_requirement']='No application research-stream reader exists. Exact intake/lineage/consumption requirement recorded for the next review; queue correctness is fixed without inventing that reader.'
d['OD07_06_started']=False;d['quantitative_research_started']=False
d['next_review_readiness']='Authorized brain corrections reconciled; ready for the owner to initiate the next subsystem review with recorded dependencies. No next review or OD07-06 implementation performed.'
(OUT/'owner-decisions.json').write_text(json.dumps(d,indent=2)+'\n')
print(json.dumps({'unique_final_cases':len(latest),'unresolved_failures':unresolved,'network_attempts':network,'postmortem_callers':len(pm_calls)},indent=2))
