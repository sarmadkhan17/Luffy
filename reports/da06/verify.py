"""Verify retained bytes, exact historical Attention and matched replay evidence."""
import gzip, hashlib, importlib.util, json, sys
from dataclasses import asdict
from pathlib import Path
from trader.cognition.contracts import INPUT_SCHEMA,load_input
from trader.observability.store import restore_market_receipts
from trader.learning.foundation import digest
P=Path(__file__).parent
f=json.loads(gzip.decompress((P/'retained.json.gz').read_bytes()))
assert all(digest(raw)==sha for sha,raw in f['blobs'].items())
scan=f['attention']
wanted=scan['code_manifest']['cognition/attention.py']
assert hashlib.sha256((P/'historical-attention.py').read_bytes()).hexdigest()==wanted
candles=[dict(b) for entries in f['attention_bars'].values() for b in entries]
restore_market_receipts(candles,scan)
raw=dict(schema=INPUT_SCHEMA,timeframe='4h',decision_times=[scan['as_of_ms']],candles=candles,
         membership=scan['membership'],correlation_history=scan['correlation_input'])
spec=importlib.util.spec_from_file_location('trader.cognition.da06_historical',P/'historical-attention.py')
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
r=mod.evaluate(load_input(raw),scan['as_of_ms'],mod.CognitionConfig(**scan['config']),scan['scan_id'],{},
               frozen_priorities=scan.get('governed_attention_state'))
assert r['universe']==scan['rows']
assert r['market']==scan['market']
assert [asdict(o) for o in r['observations']]==scan['observations']
a=json.loads((P/'before-outputs.json').read_text());b=json.loads((P/'after-outputs.json').read_text())
assert a==b
before=json.loads((P/'before-measurements.json').read_text());after=json.loads((P/'after-measurements.json').read_text())
assert before['csv_calls']==2*after['csv_calls']==224
assert before['csv_bytes']==2*after['csv_bytes']==53915820
assert len(a['analyst_votes'])==16 and all(len(v)==7 for v in a['analyst_votes'])
assert before['historical_no_eligible_strategy']==['MOVR/USDT','QNT/USDT']
assert before['historical_selected']==['RLC/USDT']
base=(P/'matched-baseline-tests.txt').read_text();new=(P/'new-tests.txt').read_text()
assert '2 failed, 135 passed' in base and '137 passed' in new
proof=dict(retained_blobs_verified=len(f['blobs']),historical_attention='EXACT rows, observations, market, selection',
    matched_parity={k:True for k in a},baseline_tests='135 pass + 2 expected duplicate-work failures',new_tests='137 pass',
    csv_calls=[before['csv_calls'],after['csv_calls']],csv_bytes=[before['csv_bytes'],after['csv_bytes']],
    median_ms=[before['median_ms'],after['median_ms']],speedup=before['median_ms']/after['median_ms'],
    historical_no_eligible_strategy=before['historical_no_eligible_strategy'],
    historical_selected=before['historical_selected'],
    no_eligible_analyst_median_ms={label:sum(v['per_symbol_median_ms'][s] for s in before['historical_no_eligible_strategy']) for label,v in [('before',before),('after',after)]},
    limitations=before['limits'])
(P/'verification.json').write_text(json.dumps(proof,indent=2))
print(json.dumps(proof,indent=2))
