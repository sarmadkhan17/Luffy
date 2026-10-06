"""Offline exact capture equivalence, with no provider/service execution."""
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import socket

from trader.data import market_provenance
from trader.learning.capture_runtime import restore_frame
from trader.observability.attention import capture

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('perf03_baseline', HERE / 'baseline-attention.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
x = json.loads(gzip.decompress((HERE / 'retained-fixture.json.gz').read_bytes()).decode())
f = {s: {'4h': restore_frame(v)} for s, v in x['frames'].items()}
compact = {s: dict(source='broad-crypto.v1', symbol=s, quality='VALID',
                  available_at_ms=x['cut'], observed_at_ms=x['cut']) for s in x['members']}
def denied(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect = denied
socket.create_connection = denied
results = []
for case in ('historical', 'current_shape', 'future_and_stale', 'nullable_and_nonfinite',
             'unordered', 'missing_provenance', 'unavailable_membership'):
    frames = copy.deepcopy(f)
    receipts = copy.deepcopy(x['membership_receipts'] if case == 'historical' else compact)
    symbol = x['members'][0]
    df = frames[symbol]['4h']
    if case == 'future_and_stale':
        df.loc[df.index[-1], 'available_at_ms'] = x['cut'] + 1
        df.loc[df.index[-2], 'observed_at_ms'] = x['cut'] + 1
        df.loc[df.index[-3], 'event_time_ms'] = x['cut'] + 1
        df.loc[df.index[-4], 'quality'] = 'STALE'
        df.loc[df.index[-5], 'bar_state'] = 'PARTIAL'
    if case == 'nullable_and_nonfinite':
        df.loc[df.index[-1], 'supersedes'] = float('nan')
        df.loc[df.index[-2], 'close'] = float('nan')
    if case == 'unordered':
        df.loc[df.index[-1], 'ts'] = df.loc[df.index[-2], 'ts']
    if case == 'missing_provenance':
        frames[symbol]['4h'] = df.drop(columns='revision_id')
    if case == 'unavailable_membership':
        receipts[symbol]['available_at_ms'] = x['cut'] + 1
    args = (frames, x['members'], x['scan_id'], x['cfg'], x['cut'])
    before = old.capture(*args, membership_receipts=receipts)
    after = capture(*args, membership_receipts=receipts)
    before.pop('capture_ms'); after.pop('capture_ms')
    assert before == after, case
    frozen = json.dumps(after, sort_keys=True, allow_nan=False)
    df.loc[:, 'close'] = 999
    receipts[symbol]['quality'] = 'MUTATED'
    assert json.dumps(after, sort_keys=True, allow_nan=False) == frozen
    results.append(dict(case=case, result='PASS', payload_sha256=hashlib.sha256(frozen.encode()).hexdigest()))
(HERE / 'equivalence.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
