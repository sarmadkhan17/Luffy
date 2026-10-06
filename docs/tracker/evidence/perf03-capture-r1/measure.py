import gzip
import json,cProfile,pstats,time,hashlib,sys,importlib.util
from pathlib import Path
from trader.learning.capture_runtime import restore_frame
from trader.observability.attention import capture
from trader.data import market_provenance
p=Path('docs/tracker/evidence/perf03-capture-r1');x=json.loads(gzip.decompress((p/'retained-fixture.json.gz').read_bytes()).decode());f={s:{'4h':restore_frame(v)} for s,v in x['frames'].items()}
def compact():return {s:dict(source='broad-crypto.v1',symbol=s,quality='VALID',available_at_ms=x['cut'],observed_at_ms=x['cut']) for s in x['members']}
label = sys.argv[1] if len(sys.argv) > 1 else 'after'
if label == 'before':
 spec=importlib.util.spec_from_file_location('perf03_before',p/'baseline-attention.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 capture=module.capture
results={}
for name,receipts in [('historical_reconstruction',x['membership_receipts']),('current_membership_shape_on_historical_frames',compact())]:
 vals=[]
 for i in range(7):
  e=capture(f,x['members'],x['scan_id'],x['cfg'],x['cut'],membership_receipts=receipts);vals.append(e['capture_ms'])
 results[name]={'capture_ms':vals,'candles':len(e['input']['candles']),'histories':len(e['input']['correlation_history']),'included':e['scope']['included_count'],'input_sha256':hashlib.sha256(json.dumps(e['input'],sort_keys=True).encode()).hexdigest()}
 pr=cProfile.Profile();pr.enable();capture(f,x['members'],x['scan_id'],x['cfg'],x['cut'],membership_receipts=receipts);pr.disable()
 with (p/(label+'-'+name+'-profile.txt')).open('w') as out:pstats.Stats(pr,stream=out).sort_stats('cumulative').print_stats(25).print_callers('deepcopy')
results['limits']=['Reconstructed from later decision frame dependency; original producer packet/frame attrs unavailable.','Current membership shape uses historical 16-symbol cap, not ATT-04 full broad cohort.','No Kernel, worker, service, provider or venue execution; isolated capture calls only.']
(p/(label+'-measurements.json')).write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
