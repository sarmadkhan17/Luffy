"""Verify fixed policy, frozen observations and fresh-process replay; no live data."""
from pathlib import Path
import sys,json,hashlib,gzip
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import evaluate_existing as E
P=E.EV
checks={}
manifest=json.loads((P/'source-manifest.json').read_text())
checks['production_values_and_source_bytes_unchanged']=all(hashlib.sha256((E.ROOT/f).read_bytes()).hexdigest()==h for f,h in manifest['files'].items())
protocol=json.loads((P/'protocol.json').read_text());results=json.loads((P/'results.json').read_text());initial=json.loads((P/'initial-results.json').read_text())
checks['protocol_identity']=results['protocol_sha256']==hashlib.sha256((P/'protocol.json').read_bytes()).hexdigest()
checks['fixed_trial_counts']=all(r['current_broad_profile']['trials']==400 for r in results['nulls'].values()) and all(r['trials']==200 for r in results['plants'].values())
checks['profile_addendum_preserves_initial_measurement']=all(results['nulls'][m]['all_optional_diagnostic']['composite_symbol_cutoff_rate']==initial['nulls'][m]['composite_symbol_cutoff_rate'] for m in initial['nulls'])
inventory=json.loads((P/'retained-inventory.json').read_text())
checks['frozen_archive_hash']=inventory['frozen_archive_sha256']==hashlib.sha256((P/'frozen-scans.jsonl.gz').read_bytes()).hexdigest()
records=E.archived()
checks['59_scan_reference_and_score_replays']=len(records)==59 and all(r.get('reference_hashes_verified') and r.get('score_eligibility_ranking_parity') for r in records)
checks['no_real_false_or_missed_labels_fabricated']=all(r['false_trigger_rate'] is None and r['missed_trigger_rate'] is None for r in records)
# Independent process reconstructs the exact original RNG sequence. Evaluation
# uses six bounded cases, not a new parameter search or a widened market sample.
rng=np.random.default_rng(protocol['seed']);hashes=[];cases=[]
for name,phi in [('iid',0.),('ar1_phi_0.6',.6)]:
 for t in range(protocol['null_trials_per_model']):
  raw=E.synthetic(rng,phi)
  if t<3:
   serialized=E.A.canonical(raw);result=E.compute(json.loads(serialized),world=True)
   hashes.append(E.digest([E.score_projection(r) for r in result['universe']]))
   receipt=E.admission(result)
   assert E.A.canonical(receipt)==E.A.canonical(E.A.verify_receipt(json.loads(E.A.canonical(receipt))))
   cases.append({'model':name,'trial':t,'input':raw,'projection_hash':hashes[-1],'receipt_hash':receipt['receipt_id']})
checks['fresh_process_six_case_ranking_eligibility_replay']=hashes==results['serialization_replay_hashes']
checks['world_component_exact_duplicate']=all(results['nulls'][m]['all_optional_diagnostic']['components']['world_volume_anomaly']==results['nulls'][m]['all_optional_diagnostic']['components']['volume_anomaly'] for m in results['nulls'])
checks['zero_sample_error_not_proven_zero_probability']=results['plants']['divergence']['component_miss']['independent_trial_wilson_95_interval'][1]>0
fixtures={}
for name,event,truth in [('null',None,False),('broad',E.broad,True),('isolated',E.isolated,False),('contested',E.contested,True)]:
 r=E.compute(E.make_fixture(event=event))
 fixtures[name]={'planted_broad_move':truth,'market_broad':r['market']['broad'],'classification_matches_fixture':r['market']['broad']==truth}
checks['four_retained_fixture_breadth_classifications']=all(v['classification_matches_fixture'] for v in fixtures.values())
(P/'breadth-fixture-results.json').write_text(json.dumps(fixtures,indent=2)+'\n')
with gzip.GzipFile(filename=str(P/'determinism-cases.json.gz'),mode='wb',mtime=0) as f:f.write(E.A.canonical(cases).encode())
assert all(checks.values()),checks
(P/'verification.json').write_text(json.dumps({'result':'PASS','checks':checks},indent=2)+'\n')
print('PASS',len(checks),'evidence checks')
