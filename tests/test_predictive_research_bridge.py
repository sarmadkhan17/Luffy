"""Normal consumer acceptance and authority refusals. All evidence is synthetic.

Numerical child receipts are deterministic protocol fixtures; they exercise the
real planner, controls, growth/ablations, LORD and referee disposition writer.
Separate tests run the actual numerical jobs against frozen synthetic candles.
"""
from copy import deepcopy
from dataclasses import asdict, replace, FrozenInstanceError
import json
import sqlite3
from pathlib import Path

import pytest

from trader.cognition import predictive as P, investigation as I
from trader.observability import investigation_research as R
from trader.research import predictive_bridge as B, predictive_experiment as E
from trader.research import vocab, slices, referee, job
from trader.research.combo import Combination
from trader.research.universe import DISCOVERY, HELDOUT
from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from trader.strategy.portfolio_evidence import Fill
from trader.learning import capture as Lc, foundation as L
from tests.test_investigation_state_feedback import paths
from tests.test_investigation_research_family import measured

CFG = {'risk': {'risk_per_trade_pct': .5, 'max_open_trades': 8}, 'research': {
    'enabled': True, 'referee': True, 'handoff': False, 'horizons': ['4h'],
    'geometries': ['fixed'], 'min_discovery_symbols': 16, 'batch_seconds': 10,
    'batch_combos': 4, 'referee_max_draws': 19999}}


@pytest.fixture
def population(paths, tmp_path):
    from trader.research.ledger import Ledger
    Ledger(Journal(tmp_path/'luffy.db')).ensure()
    _, source, _ = paths
    iid, end = measured(paths, 'same_direction')
    result = R.run(source, iid, recorded_at_ms=end+2)
    assert result['status']=='OK'
    chain = R.chain(source, iid)
    s = P.source_from_chain(chain,chain['runs'][-1],end+2)
    candles = tmp_path/'candles.db'
    start=(end//I.TF+1)*I.TF
    with sqlite3.connect(candles) as db:
        db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,taker_buy REAL,PRIMARY KEY(symbol,tf,ts))')
        for sym in DISCOVERY+HELDOUT:
            db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)',
                [(sym,'4h',start+i*I.TF,100+i*.01,101+i*.01,99+i*.01,100.1+i*.01,
                  100+(i%37)**2,50) for i in range(1800)])
    return source, tmp_path/'bridge.db', candles, end+3, start+1800*I.TF, s


class NumericalFixture:
    """Synthetic measured readings, never claimed to be real profitable edge."""
    def __init__(self, disposition='SUPPORTED', crash=False):
        self.disposition=disposition
        self.calls=[]
        self.crash=crash

    def __call__(self, fn, payload, **_):
        self.calls.append((fn.__name__,deepcopy(payload)))
        if fn is job.measure_job:
            out=dict(tf='4h', cut_ms=payload['cfg']['research']['predictive_split']['cut_ms'],
                     gauges={'volume_z(96)':dict(p10=-1,p25=-.5,p75=.5,p90=1,min=-5,max=5,n=20000,finite_frac=1,usable=True)},
                     counts={'discovery':{s:1200 for s in DISCOVERY},'heldout_a':{s:1200 for s in HELDOUT},'heldout_b':{s:600 for s in DISCOVERY+HELDOUT}})
        elif fn is job.evaluate_job:
            results=[]
            for raw in payload['combos']:
                c=Combination.from_dict(raw)
                results.append(dict(hash=c.hash,tf=c.tf,geo=c.geo,k=c.k,window=c.window,
                    parts=list(c.keys),round=c.round,parent=c.parent,trigger=c.trigger,
                    entry_long=c.long,entry_short=c.short,verdict='scored',testable=True,
                    trades=200,scored_symbols=16,consistency_p=.001 if c.k==2 or c.round=='control' else .02,
                    median_pf=1.4,portfolio=dict(total_pct=20 if c.k==2 else 10,max_dd_pct=5),
                    projection=dict(markets_a=8,markets_b=8)))
            out=dict(results=results,loaded_symbols=len(payload['symbols']))
        elif fn is job.select_job:
            out=dict(entries={Combination.from_dict(c).hash:json.dumps({'BTC/USDT':[payload['cfg']['research']['predictive_split']['start_ms']]}) for c in payload['combos']})
        elif fn is job.referee_job:
            n=60 if self.disposition!='DATA_INSUFFICIENT' else 1
            p=.5 if self.disposition=='UNSUPPORTED' else .00001
            a=b=dict(trades=n,scored_symbols=16,consistency_p=p,consistency_p_dep=p,rho_bar=0)
            rotation=dict(p=p)
            gate=referee.gate1(a,b,rotation)
            fills=[Fill(i*2,i*2+1,1 if i%3 else -.5,'BTC/USDT') for i in range(60)]
            g3=referee.gate3(fills,[],2000,.5,8)
            out=dict(hash=Combination.from_dict(payload['combo']).hash,looked=True,
                     a=a,b=b,rotation=rotation,gate1=gate,gate3=g3)
        else:
            pytest.fail('unexpected quantitative job')
        return ChildResult(ok=True,value=out,elapsed_s=0)


def consume(population, fake, now, **kw):
    source,bank,candles,*_=population
    return B.cycle(source,bank,candles,CFG,now_ms=now,run=fake,**kw)


def saved(bank, table):
    with B.store(bank) as db:
        return B.records(db,table)


@pytest.mark.parametrize('disposition',['SUPPORTED','UNSUPPORTED','DATA_INSUFFICIENT'])
def test_normal_bank_consumer_to_planner_referee_feedback(population, disposition):
    source,bank,_,created,later,_=population
    fake=NumericalFixture(disposition)
    original=source.read_bytes()
    first=consume(population,fake,created)
    assert first['counts']['eligible_research_results']==1
    assert first['counts']['hypotheses_proposed']==2
    assert first['counts']['data_insufficient']==1
    assert first['counts']['not_testable']==1
    assert fake.calls==[] # descriptive support did not create predictive evidence
    assert all(r['classification']!='SUPPORTED' for r in saved(bank,'bridge_bank_results'))
    for _ in range(6):
        consume(population,fake,later)
    outcomes=saved(bank,'bridge_bank_results')
    final=next(r for r in outcomes if r['classification']==disposition and r['experiment_id'])
    from trader.cognition import predictive_bank
    attachments=predictive_bank.predictive_extensions(bank,final['bank_id'])
    assert final in attachments['results'] and attachments['authority']=='RESEARCH_ONLY'
    assert final['bank_id']==saved(bank,'bridge_sources')[0]['bank_id']
    assert final['hypothesis_id']==saved(bank,'bridge_experiments')[0]['source_hypothesis_id']
    assert final['referee_disposition']['state']=='referee_passed' if disposition=='SUPPORTED' else final['referee_disposition']['state']=='gate1_fail'
    jobs=[name for name,_ in fake.calls]
    assert jobs==['measure_job','evaluate_job','evaluate_job','evaluate_job','select_job','referee_job']
    calls=len(fake.calls)
    consume(population,fake,later+1)
    assert len(fake.calls)==calls
    assert source.read_bytes()==original
    assert len(saved(bank,'bridge_tests'))==1
    if disposition=='SUPPORTED':
        candidate=final['referee_disposition']['factory_candidate']
        j=Journal(candidate['quantitative_ledger'])
        assert F.gate_evidence(j,candidate['hash'])['candidate']['state']=='referee_passed'
        # Factory remains the only writer of StrategyVersion; bridge didn't create one.
        assert not j.query("SELECT name FROM sqlite_master WHERE name='strategy_versions'")
        version=F.create_version(j,CFG,dict(kind='research_candidate',hash=candidate['hash']),at_ms=later)
        assert version['version_id'] # no install/paper/approval/activation invoked
    else:
        assert any('normalization' in q['text'] for q in saved(bank,'bridge_questions')) if disposition=='UNSUPPORTED' else True


def test_hypothesis_immutable_and_registered_only(population):
    *_,s=population
    h=P.propose(s,'volume_breakout_context.v1',s.available_ms+1)
    with pytest.raises(FrozenInstanceError): h.stage=P.SUPPORTED_RESULT
    with pytest.raises(ValueError,match='unregistered_transformation'): P.propose(s,'invent_alpha',s.available_ms)
    with pytest.raises(ValueError,match='proposal_stage'): P.validate(replace(h,stage=P.SUPPORTED_RESULT))
    with pytest.raises(ValueError,match='integrity'): P.validate(replace(h,target_definition='already observed return'))
    assert h.direction is None and json.loads(h.provenance_json)['llm'] is None


def test_exact_duplicates_related_and_contradicted_prior(population):
    *_,s=population
    h=P.propose(s,'volume_breakout_context.v1',s.available_ms+1)
    with B.store(population[1]) as db:
        assert B.prior_classification(db,h)[0]=='NEW'
        B.append(db,'bridge_hypotheses',h.hypothesis_id,dict(hypothesis=asdict(h),novelty='NEW',related_prior=[]))
        assert B.prior_classification(db,h)[0]=='EXACT_DUPLICATE'
        changed=replace(s,context_json=P.canonical({'world_model':{'status':'OTHER'},'attention':{}}))
        next_h=P.propose(changed,'volume_breakout_context.v1',s.available_ms+1)
        assert B.prior_classification(db,next_h)[0]=='RELATED_PRIOR'
        B.feedback(db,h,None,'UNSUPPORTED',{},dict(state='gate1_fail'),s.available_ms+2)
        assert B.prior_classification(db,next_h)[0]=='CONTRADICTED_BY_PRIOR'
        # Prior contradiction is context, not suppression.
        assert P.next_questions(s,'UNSUPPORTED',[dict(classification='UNSUPPORTED')])


@pytest.mark.parametrize('classification',P.CLASSIFICATIONS+('REFUTED',))
def test_bounded_next_question_generator_all_outcomes(population,classification):
    *_,s=population
    q=P.next_questions(s,classification,[dict(classification='UNSUPPORTED')])
    assert 1<=len(q)<=P.MAX_HYPOTHESES
    assert all(r['stage']==P.QUESTION and r['suppression'] is None for r in q)


def test_shape_leakage_and_factory_bypass_refused(population):
    source,bank,candles,created,later,s=population
    h=P.propose(s,'exact_volume_path.v1',created)
    assert E.translate(h,candles,CFG,later)['status']=='UNSUPPORTED_EXPERIMENT_SHAPE'
    h=P.propose(s,'volume_breakout_context.v1',created)
    ex=E.translate(h,candles,CFG,later)
    assert ex['status']=='READY'
    leaky=deepcopy(ex); leaky['split']['start_ms']=s.as_of_ms
    leaky['experiment_id']=P.digest({k:v for k,v in leaky.items() if k!='experiment_id'})
    with pytest.raises(ValueError,match='leakage'): E.validate(leaky)
    j=Journal(bank.parent/'factory.db')
    from trader.research.ledger import Ledger
    Ledger(j).ensure()
    with pytest.raises(F.HandoffRefused): F.create_version(j,CFG,dict(kind='investigation_research',record={'predictive_edge_established':False}),at_ms=later)
    with pytest.raises(F.HandoffRefused): F.create_version(j,CFG,dict(kind='research_candidate',hash=h.hypothesis_id),at_ms=later)


def test_source_missing_provenance_and_stale_protocol(population):
    source,*_=population
    with E.readonly(source) as db:
        iid=db.execute('SELECT investigation_id FROM investigation_research_records LIMIT 1').fetchone()[0]
    chain=R.chain(source,iid)
    raw=deepcopy(chain)
    case=json.loads(raw['runs'][-1]['evidence']['frozen']['case_payload'])
    case['state']['evidence_ids']=[]
    raw['runs'][-1]['evidence']['frozen']['case_payload']=P.canonical(case)
    with pytest.raises(ValueError,match='missing_provenance'): P.source_from_chain(raw,raw['runs'][-1],population[3])
    raw=deepcopy(chain);raw['question']['source']['catalog_id']='stale'
    with pytest.raises(ValueError,match='stale'): P.source_from_chain(raw,raw['runs'][-1],population[3])
    raw=deepcopy(chain);raw['runs'][-1]['result']['predictive_edge_established']=True
    with pytest.raises(ValueError,match='descriptive'): P.source_from_chain(raw,raw['runs'][-1],population[3])


def test_feedback_retains_stage7_replay_and_no_truth_from_priority(population):
    _,bank,_,created,_,_=population
    consume(population,NumericalFixture(),created)
    with sqlite3.connect(bank) as db:
        ids=[r[0] for r in db.execute('SELECT outcome_id FROM learning_outcome_captures')]
        assert len(ids)==2
        for oid in ids:
            outcome,retained=Lc.learning_outcome(db,oid)
            assert outcome.kind==L.Kind.RESEARCH
            assert L.replay(outcome,retained).status=='COMPLETE'
            assert all(support!=L.Support.ESTABLISHED for _,support,_ in L.attribute(outcome).dimensions)


def test_snapshot_scores_exclude_generation_and_search_cannot_read_protected(population):
    _,_,candles,created,later,s=population
    h=P.propose(s,'volume_breakout_context.v1',created)
    ex=E.translate(h,candles,CFG,later)
    cfg=deepcopy(CFG);cfg['research']['predictive_split']=ex['split']
    copy=candles.parent/'copy.db'
    E.freeze_candles(candles,copy,ex)
    from trader.research.evaluate import load_bundle
    discovery=load_bundle('4h',DISCOVERY,cfg,requires=('ohlcv',),heldout_symbols=HELDOUT,paths={'candles':str(copy)})
    assert discovery.frames
    for frame in discovery.frames.values():
        assert int(slices._ms(frame).min())>created
        assert int(slices._ms(frame).max())<ex['split']['cut_ms']
    protected=referee.load_heldout('4h','b',DISCOVERY,cfg=cfg,cut=ex['split']['cut_ms'],paths={'candles':str(copy)})
    for sym,frame in protected.frames.items():
        assert int(slices._ms(frame).iloc[protected.first_bars[sym]])>=ex['split']['cut_ms']
    # Real numerical job, no mocked child and no protected prices consumed.
    output=job.measure_job(dict(tf='4h',symbols=DISCOVERY,heldout_symbols=HELDOUT,
        cfg=cfg,requires=['ohlcv'],paths={'candles':str(copy)},exprs=['volume_z(96)']))
    assert output['cut_ms']==ex['split']['cut_ms']
    assert output['gauges']['volume_z(96)']['n']>0


def test_normal_path_actual_numerical_jobs_no_direct_planner_injection(population):
    _,bank,_,created,later,_=population
    calls=[]
    def numerical(fn,payload,**_):
        calls.append(fn.__name__)
        return ChildResult(ok=True,value=fn(payload),elapsed_s=0)
    consume(population,numerical,created)
    for _ in range(4):
        consume(population,numerical,later)
    assert calls==['measure_job','evaluate_job']
    outcomes=saved(bank,'bridge_bank_results')
    measured=[o for o in outcomes if o['measured_result'] and o['experiment_id']]
    assert measured and measured[-1]['measured_result']['step_receipt']['value']['results'][0]['trades']==0
    assert measured[-1]['classification'] in ('INCONCLUSIVE','DATA_INSUFFICIENT')
    assert all(o['classification']!='SUPPORTED' for o in outcomes)


def test_crash_after_spent_look_recovers_global_budget(population,monkeypatch):
    _,bank,_,created,later,_=population
    fake=NumericalFixture()
    consume(population,fake,created)
    for _ in range(5): consume(population,fake,later)
    original=B.feedback
    def crash(*args,**kw):
        if args[3]=='SUPPORTED': raise RuntimeError('crash_after_quantitative_commit')
        return original(*args,**kw)
    monkeypatch.setattr(B,'feedback',crash)
    with pytest.raises(RuntimeError,match='crash_after'): consume(population,fake,later)
    assert len(saved(bank,'bridge_tests'))==0
    monkeypatch.setattr(B,'feedback',original)
    consume(population,fake,later)
    assert len(saved(bank,'bridge_tests'))==1
    assert [name for name,_ in fake.calls].count('referee_job')==1
    assert any(o['classification']=='SUPPORTED' for o in saved(bank,'bridge_bank_results'))


def test_source_alias_budget_and_immutable_checkpoint_refused(population):
    source,bank,candles,created,_,_=population
    with pytest.raises(ValueError,match='alias'): B.cycle(source,source,candles,CFG,now_ms=created)
    with pytest.raises(ValueError,match='source_budget'): consume(population,NumericalFixture(),created,max_sources=0)
    with pytest.raises(ValueError,match='experiment_budget'): consume(population,NumericalFixture(),created,max_experiments=5)
    consume(population,NumericalFixture(),created)
    with B.store(bank) as db:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'): db.execute("UPDATE bridge_hypotheses SET payload='{}'")
    second=consume(population,NumericalFixture(),created+1)
    assert second['counts']['eligible_research_results']==0 and second['counts']['hypotheses_proposed']==0
    assert len(saved(bank,'bridge_hypotheses'))==2


def test_global_existing_error_budget_is_inherited(population):
    source,bank,_,created,later,_=population
    from trader.research.ledger import Ledger
    led=Ledger(Journal(source.parent/'luffy.db'))
    led.ensure()
    led.record_test('prior-protected-look','4h','fixed','gate1',.8,.001,False,{'reason':'TEST_ONLY'})
    fake=NumericalFixture()
    consume(population,fake,created)
    for _ in range(6): consume(population,fake,later)
    tests=saved(bank,'bridge_tests')
    assert len(tests)==2 and [r['seq'] for r in tests]==[1,2]
    assert tests[1]['alpha_t']!=tests[0]['alpha_t']


def test_malformed_stored_bank_refused_by_normal_consumer(population):
    source,_,_,created,_,_=population
    with sqlite3.connect(source) as db:
        db.execute('DROP TRIGGER investigation_research_records_no_update')
        db.execute("UPDATE investigation_research_records SET canonical_json='{}' WHERE record_type=?",(R.BANK_SCHEMA,))
    out=consume(population,NumericalFixture(),created)
    assert out['counts']['hypotheses_proposed']==0
    assert out['refusals'][0]['reason']=='source_row_integrity'


def test_rehashed_experiment_cannot_remove_registered_null_or_leakage_guard(population):
    _,_,candles,created,later,source=population
    h=P.propose(source,'volume_breakout_context.v1',created)
    original=E.translate(h,candles,CFG,later)
    for field,value in [('baseline_null','description proves edge'),('leakage_guard','none'),
                        ('metrics',['profitable']),('direction','long')]:
        forged=deepcopy(original)
        forged[field]=value
        forged['experiment_id']=P.digest({k:v for k,v in forged.items() if k!='experiment_id'})
        with pytest.raises(ValueError,match='leakage_or_experiment_contract'):
            E.validate(forged)
