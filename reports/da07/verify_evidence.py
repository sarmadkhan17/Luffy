"""Verify retained DA-07 evidence offline; no stores, providers or writes."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

P = Path(__file__).resolve().parent
ROOT = P.parents[1]


def read(name):
    return json.loads((P / name).read_text())


def outcomes(name):
    return {(t.attrib['classname'], t.attrib['name']):
            ('failure' if t.find('failure') is not None else
             'error' if t.find('error') is not None else
             'skip' if t.find('skipped') is not None else 'pass')
            for t in ET.parse(P / name).getroot().iter('testcase')}


for name, sha in read('artifact-sha256.json').items():
    assert hashlib.sha256((P / name).read_bytes()).hexdigest() == sha, name
for name, sha in read('source-sha256.json').items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha, name
for before, after in [('baseline-tests', 'candidate-tests'),
                      ('consumer-baseline', 'consumer-candidate'),
                      ('safe-baseline', 'safe-candidate')]:
    assert outcomes(before + '.xml') == outcomes(after + '.xml')
for label in ('baseline', 'candidate'):
    cases = outcomes('safe-' + label + '.xml')
    assert len(cases) == 276 and set(cases.values()) == {'pass'}
    log = (P / ('safe-' + label + '.txt')).read_text()
    assert 'OFFLINE GUARD: 0 network attempts.' in log
    assert 'PRODUCTION STORE GUARD: 0 attempts.' in log
assert read('baseline-replay-outputs.json') == read('candidate-replay-outputs.json')
print('PASS: artifact/source hashes; matched outcomes including inherited failures; '
      '276 clean offline passes per side; retained replay equality')
