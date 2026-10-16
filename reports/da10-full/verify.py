"""Verify bundled DA-10 evidence without production files, services or providers."""
import hashlib,json,xml.etree.ElementTree as ET
from pathlib import Path
P=Path(__file__).parent

def load(name):return json.loads((P/name).read_text())
def cases(name):
    out={}
    for c in ET.parse(P/name).getroot().iter('testcase'):
        key=c.attrib['classname']+'::'+c.attrib['name']
        out[key]='failed' if c.find('failure') is not None or c.find('error') is not None else 'skipped' if c.find('skipped') is not None else 'passed'
    return out

baseline=cases('baseline.xml');new=cases('final-learning.xml');focused=cases('final-storage.xml')
assert baseline==new and len(new)==211 and set(new.values())=={'passed'}
assert len(focused)==181 and set(focused.values())=={'passed'}
controls=cases('baseline-storage.xml');assert len(controls)==30
assert list(controls.values()).count('passed')==2 and list(controls.values()).count('failed')==28
assert all(focused[k]=='passed' for k in controls)
before=load('before-measurements.json');after=load('after-measurements.json')
assert before['replay_bodies']==after['replay_bodies']
assert before['backup']['asset_identity']==after['backup']['asset_identity']
assert before['snapshot']['canonical_calls_per_call']==2 and after['snapshot']['canonical_calls_per_call']==1
assert before['duplicate_capture']['wal_growth_bytes']>0 and after['duplicate_capture']['wal_growth_bytes']==0
assert after['duplicate_capture']['open_fd_growth']==after['reader_fd_growth']==0
assert after['duplicate_capture']['rows']==before['duplicate_capture']['rows']==1
history=load('historical-source-parity.json');assert history['exact'] and history['results']['before']==history['results']['after']
assert history['results']['after']['source_blobs']==77
for label in ('before','after'):
    wal=load(label+'-wal.json');assert wal['pinned_checkpoint'][0]==1 and wal['released_checkpoint'][0]==0 and wal['released_wal_bytes']==0
space_before=load('before-deleted-space.json');space_after=load('after-deleted-space.json')
assert space_before['pending_deleted_allocated_bytes']>0 and space_after['pending_deleted_allocated_bytes']==0
scope=load('scope.json')
for key in ('unchanged_sha256','changed_source_sha256'):
    for name,sha in scope[key].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
lineage=load('lineage.json');assert all(lineage['ancestry'].values())
combined={**new,**focused}
result=dict(status='PASS',matched_main_tests=len(new),final_focused_tests=len(focused),unique_passing_tests=len(combined),
    new_controls=dict(baseline_passed=2,baseline_failed=28,new_passed=30),
    exact_portfolio_bodies=True,exact_backup_assets=True,exact_historical_source_blobs=77,
    historical_database_byte_parity=True,unchanged_scope_hashes=True,
    canonical_calls=[2,1],duplicate_wal_bytes=[before['duplicate_capture']['wal_growth_bytes'],0],
    duplicate_open_fd_growth=[before['duplicate_capture']['open_fd_growth'],0],
    reader_open_fd_growth=[before['reader_fd_growth'],0],
    deleted_file_pending_allocated_bytes=[space_before['pending_deleted_allocated_bytes'],0],
    portfolio_duplicate_ms=[before['contention']['duplicate_during_other_writer_ms'],after['contention']['duplicate_during_other_writer_ms']],
    portfolio_prepare_max_writer_wait_ms=[before['contention']['max_prepare_probe_wait_ms'],after['contention']['max_prepare_probe_wait_ms']],
    backup_peak_python_bytes={phase:[before['backup']['measurements'][phase]['peak_python_bytes'],after['backup']['measurements'][phase]['peak_python_bytes']] for phase in ('capture','verify','restore')},
    zero_space_cause='UNPROVEN; historical attribution not established by these synthetic reproductions')
(P/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
