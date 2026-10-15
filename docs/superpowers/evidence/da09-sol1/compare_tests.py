import json, sys, xml.etree.ElementTree as ET
from pathlib import Path
def cases(path):
    return {(x.get('classname'), x.get('name')): ('failed' if x.find('failure') is not None else 'error' if x.find('error') is not None else 'skipped' if x.find('skipped') is not None else 'passed') for x in ET.parse(path).iter('testcase')}
base, new = map(cases, sys.argv[1:3])
result = dict(baseline_counts={s:list(base.values()).count(s) for s in set(base.values())}, new_counts={s:list(new.values()).count(s) for s in set(new.values())}, new_failures=[list(k) for k,v in new.items() if v in ('failed','error') and base.get(k) not in ('failed','error')], resolved_failures=[list(k) for k,v in base.items() if v in ('failed','error') and new.get(k)=='passed'], additional_cases=[list(k) for k in new if k not in base])
print(json.dumps(result, indent=2))
