"""Validate this assignment's retained offline evidence; no DB/provider access."""
import hashlib
import json
from pathlib import Path
import subprocess
import re
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def read(name):
    root=ET.parse(HERE/name).getroot()
    cases={}
    for c in root.iter('testcase'):
        if 'classname' not in c.attrib:
            continue  # interrupted in-progress case has no outcome
        key=(c.attrib['classname'],c.attrib['name'])
        status='error' if c.find('error') is not None else 'failed' if c.find('failure') is not None else 'skipped' if c.find('skipped') is not None else 'passed'
        cases[key]=status
    return cases

def counts(cases):
    return {k:sum(v==k for v in cases.values()) for k in ('passed','failed','error','skipped')}

assert 'ENGINE EQUIVALENCE: PASS' in (HERE/'engine-equivalence.log').read_text()
bench=(HERE/'engine-benchmark.log').read_text()
assert 'BENCH: PASS' in bench
speedup=float(re.search(r'"speedup":\s*([0-9.]+)',bench).group(1))
assert speedup >= 20
baseline=read('before-final.xml')
assert counts(baseline)==dict(passed=4,failed=37,error=0,skipped=0),counts(baseline)
initial=read('impact.xml')
merged=dict(initial)
checkpoint=read('lifecycle-checkpoint.xml')
assert counts(checkpoint)==dict(passed=36,failed=1,error=0,skipped=0)
merged.update(checkpoint)
refusal_checkpoint=read('lifecycle-refusal-checkpoint.xml')
assert counts(refusal_checkpoint)==dict(passed=1,failed=1,error=0,skipped=0)
merged.update(refusal_checkpoint)
final_suites=['compatibility-final.xml','transfer-final.xml','receipt-final.xml','lifecycle-final.xml']
summaries={}
for name in final_suites:
    cases=read(name)
    assert cases and all(v=='passed' for v in cases.values()),(name,counts(cases))
    merged.update(cases)
    summaries[name]=dict(tests=len(cases),**counts(cases))
assert all(v=='passed' for v in merged.values()),[(k,v) for k,v in merged.items() if v!='passed']
for key in baseline:
    assert merged.get(key)=='passed',key
pins=json.loads((HERE/'runtime-source-sha256.json').read_text())
for path,digest in pins.items():
    assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,path
mapping=json.loads((HERE/'defect-test-map.json').read_text())
proof={}
for defect,fragments in mapping['test_name_fragments'].items():
    positives=[name for (cls,name),v in merged.items() if cls=='tests.test_review07_quantitative' and any(f in name for f in fragments) and v=='passed']
    negatives=[name for (cls,name),v in baseline.items() if any(f in name for f in fragments) and v=='failed']
    assert positives and negatives,defect
    proof[defect]=dict(status='FIXED_VERIFIED_OFFLINE',regressions=positives,baseline_failures=negatives)
report=dict(
    mode='INCREMENTAL_OFFLINE_VERIFICATION',single_all_green_broad_run=False,
    baseline=dict(commit='81565a0',tests=len(baseline),**counts(baseline)),
    receipt_refusal_checkpoint=dict(tests=len(refusal_checkpoint),**counts(refusal_checkpoint),note='Authenticated revision test passed; receipt-refusal label regression then corrected and rerun.'),
    lifecycle_checkpoint=dict(tests=len(checkpoint),**counts(checkpoint),note='36 completed cases retained; corrected failed and remaining cases rerun without repeating completed numerical evaluations.'),
    initial_broad=dict(tests=len(initial),**counts(initial),note='Earlier source/fixtures; failures superseded below; storage-full errors rerun on data volume.'),
    final_suites=summaries,effective_unique_tests=len(merged),effective_results=counts(merged),
    excluded_operational_tests=['tests/test_research_universe_depth.py::test_every_enabled_horizon_carries_enough_discovery_symbols','tests/test_research_universe_depth.py::test_four_hour_is_deep_enough_today'],
    numerical_inputs='GENERATED_FIXTURES_ONLY',engine_equivalence='PASS',vector_speedup=speedup,
    added_refusal_regression=dict(before=counts(read('receipt-refusal-before.xml')),after='receipt-final.xml'),
    unchanged_contracts=json.loads((HERE/'unchanged-contract-checks.json').read_text()),
    source_hashes=pins,source_profiles=dict(initial_implementation='c9cfe7aa1410eddec98d0dd28ad390dcb6fd3e69',fixture_followup='868f23715721136fd0686db0b6bd3252c40e0990',final_runtime='94b9000a02966dd74447c48a26eda61033e7bfe2',later_runtime_change='Only known-refusal reason labels and duplicate-prefix formatting in runner._examine; latest receipt/runner and remaining lifecycle suites cover the change; numerical methods unchanged.'),defects=proof,od07_06='OPEN_NOT_END_TO_END_VERIFIED',
    not_implemented=['D5','D6','D7','D8'],tracker_status_changed=False,
    limits=['No real-market statistical calibration or profitability claim','No deployment, production database changes, activation, orders, provider calls or CI changes','Source-only shadow and synthetic lifecycle evidence do not close OD07-06'])
(HERE/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
(HERE/'findings.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(dict(effective_unique_tests=len(merged),results=counts(merged),final_suites=summaries),indent=2))
