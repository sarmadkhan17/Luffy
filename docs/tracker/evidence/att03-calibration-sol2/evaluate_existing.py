"""Frozen ATT-03 values: read archived sources and execute unchanged pure evaluator."""
from pathlib import Path
import sys,json,gzip,hashlib,math,collections,socket
import numpy as np

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from trader.cognition.attention import evaluate,CognitionConfig,COMPONENTS,POSITIONING_REFERENCE
from trader.cognition.contracts import load_input,TF_MS
from trader.observability.store import restore_market_receipts
from trader.observability import world_producer
from trader import attention_admission as A
from tests.test_cognition_contracts import make_fixture,at
from tests.test_cognition_attention import broad,isolated,contested

EV=Path(__file__).resolve().parent
PROTOCOL=json.loads((EV/'protocol.json').read_text())
CFG=CognitionConfig();CUTOFF=CFG.min_salience;TF=TF_MS['4h'];CUT=1_800_000_000_000//TF*TF
COMPONENT_NAMES=(*COMPONENTS,'world_volume_anomaly','positioning_extreme','correlation_change')

def offline(*a,**k):raise AssertionError('network forbidden')
socket.socket.connect=offline;socket.create_connection=offline

def digest(v):return A.digest(v)

def compute(raw, cfg=CFG, world=False):
 ds=load_input(raw);assert not ds.rejected,ds.rejected
 model=world_producer.build(ds,raw['decision_times'][0],'calibration') if world else None
 result=evaluate(ds,raw['decision_times'][0],cfg,'calibration',{},model)
 return result

def score_projection(r):
 return {'symbol':r['symbol'],'status':r['status'],'eligible':r['eligible'],
         'salience':r.get('salience'),'components':{k:r.get('components',{}).get(k) for k in COMPONENT_NAMES},
         'rank':r.get('rank'),'selected':r.get('selected'),'reason':r.get('reason')}

def archived():
 output=[]
 with gzip.open(EV/'frozen-scans.jsonl.gz','rt') as f:
  for line in f:
   rec=json.loads(line);scan=rec['scan'];r={'source':rec['source'],'scan_id':rec['scan_id'],'as_of_ms':rec['as_of_ms'],
       'independent_anomaly_labels':False,'false_trigger_rate':None,'missed_trigger_rate':None}
   try:
    assert scan and scan['as_of_ms']==rec['as_of_ms']
    refs={x['version_id']:x for x in scan['input_versions']};assert len(refs)==len(scan['input_versions'])
    assert {v['version_id'] for v in rec['versions']}==set(refs)
    candles=[]
    for v in rec['versions']:
     content=json.loads(v['payload']);assert digest(content)==v['value_hash']
     ref=refs[v['version_id']];assert ref['first_seen_ms']==v['first_seen_ms']
     assert (content['symbol'],content['open_ms'])==(ref['symbol'],ref['open_ms'])
     candles.append(dict(content,available_ms=v['first_seen_ms']))
    restore_market_receipts(candles,scan)
    raw=dict(schema='cognition.input.v1',timeframe=scan['timeframe'],decision_times=[scan['as_of_ms']],
             candles=candles,membership=scan['membership'],participation=[])
    if 'correlation_input' in scan:raw['correlation_history']=scan['correlation_input']
    if 'positioning_input' in scan:raw['positioning']=scan['positioning_input']
    ds=load_input(raw);model=world_producer.replay(scan,ds)
    fresh=evaluate(ds,scan['as_of_ms'],CognitionConfig(**scan['config']),scan['scan_id'],{},model,
                   frozen_priorities=scan.get('governed_attention_state'),frozen_claim_states=scan.get('governed_world_claim_state'))
    original={x['symbol']:score_projection(x) for x in scan['rows']};current={x['symbol']:score_projection(x) for x in fresh['universe']}
    r.update(reference_hashes_verified=True,score_eligibility_ranking_parity=(original==current),
             symbols=len(scan['rows']),eligible=sum(x['eligible'] for x in scan['rows']),
             shared_cutoff_crossings=sum(x.get('salience') is not None and x['salience']>=CUTOFF for x in scan['rows']),
             statuses=dict(collections.Counter(x['status'] for x in scan['rows'])),
             receipt_qualified_candles=sum('source_receipt' in c for c in candles),candles=len(candles),
             components={k:{'available':sum(x.get('components',{}).get(k) is not None for x in scan['rows']),
                            'crossings':sum(x.get('components',{}).get(k) is not None and abs(x['components'][k])>=CUTOFF for x in scan['rows'])} for k in COMPONENT_NAMES})
   except Exception as exc:r.update(refused=True,reason=type(exc).__name__+':'+str(exc)[:240])
   output.append(r)
 return output

def series(rng,shape,phi=0.):
 x=rng.standard_normal(shape)
 if phi:
  for j in range(1,shape[-1]):x[...,j]=phi*x[...,j-1]+math.sqrt(1-phi*phi)*x[...,j]
 return x

def synthetic(rng,phi=0.,plant=None):
 m=PROTOCOL['cohort_size'];sigma=PROTOCOL['null_parameters']['return_sigma'];vscale=PROTOCOL['null_parameters']['log1p_volume_sigma']
 returns=sigma*series(rng,(m,150),phi)
 volumes=5.+vscale*series(rng,(m,151),phi)
 pos=series(rng,(m,2,21),phi)
 if plant=='volume':volumes[0,-1]+=3*vscale
 if plant=='volatility':returns[0,-CFG.short:]*=3
 if plant=='divergence':returns[0,-CFG.short:]+=3*sigma
 if plant=='positioning':pos[0,:,-1]+=3
 if plant=='correlation':
  common=rng.standard_normal(150);noise=rng.standard_normal((m,150))
  returns=sigma*(.8*common+math.sqrt(1-.8**2)*noise)
  returns[0,-30:]=sigma*noise[0,-30:]
 close=100*np.exp(np.concatenate([np.zeros((m,1)),np.cumsum(returns,axis=1)],axis=1));first=CUT-151*TF
 raw=dict(schema='cognition.input.v1',timeframe='4h',decision_times=[CUT],candles=[],membership=[],participation=[],positioning=[],correlation_history=[])
 for i in range(m):
  sym=f'S{i:02}/USDT';raw['membership'].append(dict(symbol=sym,from_ms=first,to_ms=None,available_ms=first,source='synthetic-offline'))
  for j in range(151-26,151):
   c=float(close[i,j]);opened=first+j*TF
   raw['candles'].append(dict(symbol=sym,open_ms=opened,available_ms=opened+TF,open=c,high=c*1.01,low=c*.99,close=c,volume=float(np.expm1(volumes[i,j])),source='synthetic-offline'))
  raw['correlation_history'].append(dict(symbol=sym,status='ok',first_open_ms=first,last_open_ms=first+150*TF,closes=close[i].tolist()))
  for k,kind in enumerate(['funding','ls_ratio']):
   step=8*3_600_000 if kind=='funding' else 900_000
   for j in range(21):
    ts=CUT-(20-j)*step
    raw['positioning'].append(dict(symbol=sym,series=kind,ts=ts,value=float(pos[i,k,j]),available_ms=ts,observed_ms=ts,source='offline',instrument_id='offline:futures:'+sym,revision_id=f'{kind}-{i}-{j}',quality='VALID'))
 return raw

def metric(result):
 rows=result['universe'];out={}
 for k in COMPONENT_NAMES:
  vs=[r.get('components',{}).get(k) for r in rows];available=sum(v is not None for v in vs)
  out[k]={'available':available,'crossings':sum(v is not None and abs(v)>=CUTOFF for v in vs)}
 crossings=sum(r.get('salience') is not None and r['salience']>=CUTOFF for r in rows)
 return {'components':out,'composite_crossings':crossings,'cohort_any':int(crossings>0),'selected':result['selected']}

def interval(values,seed):
 data=np.asarray(values,dtype=float);rng=np.random.default_rng(seed)
 means=np.mean(data[rng.integers(0,len(data),(1000,len(data)))],axis=1)
 result={'mean':float(np.mean(data)),'cohort_bootstrap_95_interval':[float(v) for v in np.quantile(means,[.025,.975])]}
 if set(data)<=set([0.,1.]):
  n=len(data);phat=float(np.mean(data));z=1.959963984540054;den=1+z*z/n
  center=(phat+z*z/(2*n))/den;radius=z*math.sqrt(phat*(1-phat)/n+z*z/(4*n*n))/den
  result['independent_trial_wilson_95_interval']=[max(0.,center-radius),min(1.,center+radius)]
 return result

def summarize(trials):
 m=PROTOCOL['cohort_size'];result={'trials':len(trials),'composite_symbol_cutoff_rate':interval([r['composite_crossings']/m for r in trials],77),'cohort_any_cutoff_rate':interval([r['cohort_any'] for r in trials],78),'components':{}}
 for k in COMPONENT_NAMES:
  avail=sum(t['components'][k]['available'] for t in trials);cross=sum(t['components'][k]['crossings'] for t in trials)
  values=[t['components'][k]['crossings']/t['components'][k]['available'] for t in trials if t['components'][k]['available']]
  result['components'][k]={'available_symbol_rows':avail,'crossings':cross,'rate':interval(values,79) if values else None}
 return result

def admission(result):
 obs=dict(schema='broad-crypto.v1',source_cut_ms=CUT,source_identity='synthetic',rows={r['symbol']:dict(symbol=r['symbol'],asset_class='CRYPTO',quality='VALID',available_at_ms=CUT,missing_reasons=[]) for r in result['universe']},
          peer_cohort=[r['symbol'] for r in result['universe']],strategy_relevance={},context_anchors=[],exposure_required=[],salience_rows=result['rows'],salience_config={'min_salience':CUTOFF},salience_anchor_ms=CUT,status='VALID')
 return A.admit(obs,A.AdmissionPolicy(12,2,2,0),A.initial_state())

def existing_fixtures():
 controls={};cfg=CFG
 for name,event,target in [('null',None,[]),('broad',broad,['AAA','BBB','CCC','DDD','EEE','FFF']),('isolated',isolated,['CCC']),('contested',contested,['AAA','BBB','CCC','DDD','EEE','FFF'])]:
  raw=make_fixture(event=event);r=compute(raw);truth=set(target);pred={x['symbol'] for x in r['universe'] if x['salience'] is not None and x['salience']>=CUTOFF}
  controls[name]={'planted_symbols':target,'cutoff_crossings':sorted(pred),'false_crossing_symbols':sorted(pred-truth),'missed_planted_symbols':sorted(truth-pred),'selected':r['selected']}
 return controls

def main():
 rng=np.random.default_rng(PROTOCOL['seed']);nulls={};raw_trials={};replay_checks=[]
 for name,phi in [('iid',0.),('ar1_phi_0.6',.6)]:
  trials=[]
  for t in range(PROTOCOL['null_trials_per_model']):
   raw=synthetic(rng,phi);r=compute(raw,world=True)
   normal=dict(raw);normal.pop('positioning');rn=compute(normal)
   trial=metric(r);trial['current_broad_profile']=metric(rn);trials.append(trial)
   assert all(row['components']['volume_anomaly']==row['components']['world_volume_anomaly'] for row in r['universe'])
   if t<3:
    second=compute(json.loads(A.canonical(raw)),world=True)
    assert [score_projection(x) for x in r['universe']]==[score_projection(x) for x in second['universe']]
    receipt=admission(r);assert receipt==admission(second);replay_checks.append(digest([score_projection(x) for x in r['universe']]))
  nulls[name]={'all_optional_diagnostic':summarize(trials),'current_broad_profile':summarize([t['current_broad_profile'] for t in trials])};raw_trials[name]=trials
 planted={};families={'volume':'volume_anomaly','volatility':'volatility_transition','divergence':'relative_return_divergence','positioning':'positioning_extreme','correlation':'correlation_change'}
 for family,component in families.items():
  trials=[]
  for t in range(PROTOCOL['plant_trials_per_family']):
   raw=synthetic(rng,plant=family);r=compute(raw,world=True);row=r['rows']['S00/USDT'];receipt=admission(r)
   normal=dict(raw);normal.pop('positioning');rn=compute(normal);normal_row=rn['rows']['S00/USDT']
   trials.append({'component_miss':int(row['components'][component] is None or abs(row['components'][component])<CUTOFF),'composite_miss':int(row['salience'] is None or row['salience']<CUTOFF),'top3_miss':int('S00/USDT' not in r['selected']),'admission_policy_miss_extended_inputs':int('S00/USDT' not in receipt['admitted_symbols']),'current_broad_composite_miss':int(normal_row['salience'] is None or normal_row['salience']<CUTOFF)})
  planted[family]={'trials':len(trials),'component':component,**{k:interval([x[k] for x in trials],80) for k in trials[0]}};raw_trials['plant_'+family]=trials
 retained=archived()
 summary={'protocol_sha256':hashlib.sha256((EV/'protocol.json').read_bytes()).hexdigest(),'current_config':CFG.__dict__,'status':'BLOCKED_NO_OPERATIONAL_CALIBRATION_ACCEPTANCE_OR_LABELS','nulls':nulls,'plants':planted,'existing_fixture_controls':existing_fixtures(),'retained':{'scans':len(retained),'verified_replayed':sum(r.get('score_eligibility_ranking_parity',False) for r in retained),'refused':sum(r.get('refused',False) for r in retained),'mismatched':sum(r.get('score_eligibility_ranking_parity') is False for r in retained),'symbol_rows':sum(r.get('symbols',0) for r in retained),'eligible_rows':sum(r.get('eligible',0) for r in retained),'cutoff_crossings':sum(r.get('shared_cutoff_crossings',0) for r in retained),'independent_labels':0,'false_trigger_rate':None,'missed_trigger_rate':None},'serialization_replay_hashes':replay_checks}
 (EV/'results.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n');(EV/'retained-results.json').write_text(json.dumps(retained,indent=2)+'\n')
 with gzip.GzipFile(filename=str(EV/'trial-results.json.gz'),mode='wb',mtime=0) as f:f.write(A.canonical(raw_trials).encode())
 print(json.dumps({'retained':summary['retained'],'null_composite_rates':{k:v['current_broad_profile']['composite_symbol_cutoff_rate']['mean'] for k,v in nulls.items()},'plant_component_miss':{k:v['component_miss']['mean'] for k,v in planted.items()}},indent=2))
if __name__=='__main__':main()
