"""Offline comparison of actual pre-R3 allocator bytes to current R2 replay."""
from pathlib import Path
from dataclasses import replace
import hashlib,itertools,json,subprocess,sys,types
from tests.economics_fixtures import install_models
from tests.test_portfolio_allocator import candidate,inputs,MISSING
from trader.portfolio import allocator as A,comparison as C
BASE='47761eec645c199358d6c6a725ee6b30282c57e0'
source=subprocess.check_output(['git','show',BASE+':trader/portfolio/allocator.py'])
legacy=types.ModuleType('trader.portfolio._dec02_legacy_proof');legacy.__package__='trader.portfolio'
sys.modules[legacy.__name__]=legacy
exec(compile(source,'pre-R3-allocator.py','exec'),legacy.__dict__)
install_models() # test-only adapters in this disposable proof interpreter
assert legacy.VERSION==A.VERSION_R2 and A.VERSION=='LUFFY-PORTFOLIO-ALLOCATOR-R3'
a=candidate('a','venue:futures:BTCUSDT',value='3');b=candidate('b','venue:futures:ETHUSDT',value='3')
c=candidate('c','venue:futures:SOLUSDT',value='1')
cases={'empty':inputs(),'exact_tie':inputs(b,a),'tie_with_lower':inputs(c,b,a),
 'unique_positive':inputs(a,c),'non_positive':inputs(candidate(value='0')),
 'missing_costs':inputs(replace(a,costs=MISSING)),'same_expression':inputs(a,candidate('b',value='3'))}
records={}
for name,i in cases.items():
 old=legacy.allocate(i);r2=A.allocate(i,A.VERSION_R2)
 assert old.payload()==r2.payload(),name
 assert A.verify(r2,i) and A.allocate(A.inputs_from_payload(json.loads(r2.inputs_json)),A.VERSION_R2)==r2
 records[name]={'pre_R3_proposal_id':old.proposal_id,'current_R2_proposal_id':r2.proposal_id,'exact_bytes_equal':old.inputs_json==r2.inputs_json and old.result_json==r2.result_json}
i=cases['tie_with_lower'];r2=A.allocate(i,A.VERSION_R2);r3=A.allocate(i);v=C.compare(r3)
assert r2.proposal_id!=r3.proposal_id and v['cash']['selected']
assert json.loads(r3.result_json)['reason']==A.TIE_REASON
assert all(r['outcome']=='REJECTED' and r['reasons'] for r in v['candidates'])
assert next(r for r in v['candidates'] if r['identity'][0]=='c')['reasons']==['CAPITAL_PRIORITY_NOT_SELECTED']
assert {A.allocate(inputs(*p)).proposal_id for p in itertools.permutations([a,b,c])}=={r3.proposal_id}
assert not A.verify(replace(r2,allocator_version=A.VERSION),i)
assert not A.verify(replace(r3,allocator_version=A.VERSION_R2),i)
proof=dict(pre_R3_source_revision=BASE,pre_R3_source_sha256=hashlib.sha256(source).hexdigest(),R2_actual_historical_bytes_preserved=records,
 R3_exact_tie_view=v,R2_proposal_id=r2.proposal_id,R3_proposal_id=r3.proposal_id,cross_version_refused=True,input_permutation_stable=True,
 synthetic_contract_fixtures_only=True,production_calibration_claim=False,side_effects='NONE')
(Path(__file__).parent/'replay-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
print('PASS: seven actual pre-R3/R2 comparisons, R3 distinct identity, exact-tie CASH, cross-version refusal and permutation stability')
