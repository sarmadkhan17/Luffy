"""D1-D4 counterexamples and compatibility; generated inputs only."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from trader.core.journal import Journal
from trader.core.child import ChildResult
from trader.data.feed import DataFeed
from trader.research import evaluate, growth, planner, slices, thresholds, universe
from trader.research.combo import Combination
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner
from trader.research.vocab import Part
from trader.strategy.portfolio_evidence import accounting_provenance, RESEARCH_CLOCK

TF=14_400_000
PARTS=[Part(k,'state',k,'close > ema(20)','close < ema(20)') for k in ('A','B','C')]

def result(c,p=.01,pct=20):
    return dict(hash=c.hash,tf=c.tf,geo=c.geo,k=c.k,parts=list(c.keys),trades=100,
        verdict='scored',testable=True,consistency_p=p,
        portfolio=dict(accounting=accounting_provenance(2000,.5,8,RESEARCH_CLOCK),total_pct=pct))

@pytest.fixture
def led(tmp_path):
    l=Ledger(Journal(tmp_path/'journal.db'));l.ensure();return l

def test_large_receipts_retain_cut_and_valid_json(led):
    detail=dict(padding='x'*21000,cut_ms=123)
    led.record_test('large','4h','fixed','gate1',1.,led.next_alpha(.1,.05)[1],False,detail)
    assert json.loads(led.tests()[0]['detail'])==detail
    assert led.spent_cut('4h')==123
    c=Combination(tuple(PARTS[:2]),'4h','fixed')
    r=dict(result(c),padding='y'*210000)
    led.record_result(r,'grow','test',{})
    assert led.result(c.hash)['padding']==r['padding']
    led.record_control('4h','ohlcv',detail,False)
    assert json.loads(led.journal.query('SELECT detail FROM research_controls')[0]['detail'])==detail

@pytest.mark.parametrize('payload',['{broken','[]','null'])
def test_malformed_historical_evidence_refuses_resume(led,payload):
    with led.journal._tx() as db:
        db.execute("INSERT INTO research_tests(hash,tf,gate,detail) VALUES('h','4h','gate1',?)",(payload,))
    with pytest.raises(ValueError):led.spent_cut('4h')
    with pytest.raises(ValueError):led.next_alpha(.1,.05)
    with led.journal._tx() as db:db.execute("INSERT INTO research_combos(hash,tf,geo,k,result) VALUES('c','4h','fixed',1,?)",(payload,))
    with pytest.raises(ValueError):led.result('c')

@pytest.mark.parametrize('failure',['returned','raised'])
def test_uncertain_look_is_durable_before_worker_and_never_retried(tmp_path,monkeypatch,failure):
    monkeypatch.setattr('trader.research.referee.rotation_capacity',lambda *a,**k:None)
    monkeypatch.setattr(universe,'coverage',lambda *a,**k:{'4h':{s:11000 for s in universe.DISCOVERY+universe.HELDOUT}})
    calls=[]
    def worker(fn,payload,**kw):
        # This is checked inside the worker boundary, before any possible access.
        persisted=Ledger(Journal(tmp_path/'j.db'))
        assert persisted.spent(c.hash)
        assert persisted.spent_cut('4h')==123
        calls.append(payload)
        if failure=='raised':raise RuntimeError('uncertain child termination')
        return ChildResult(ok=True,value=dict(looked=True,error='after protected read'))
    r=ResearchRunner(Journal(tmp_path/'j.db'),dict(research=dict(enabled=True,referee=True,horizons=['4h'],geometries=['fixed'])),run=worker)
    c=Combination(tuple(PARTS[:2]),'4h','fixed')
    r.ledger.record_result(result(c),'survivor','fixture',{})
    r.ledger.set_candidate(c.hash,'4h','fixed','queued');r._combo=lambda *a:c;r._brake=lambda:1.
    try:r._examine('4h','fixed',dict(hash=c.hash),123)
    except RuntimeError:pass
    assert len(calls)==1
    assert len(r.ledger.tests())==1 and not r.ledger.tests()[0]['rejected']
    again=ResearchRunner(Journal(tmp_path/'j.db'),r.cfg,run=worker)
    assert again._examine('4h','fixed',dict(hash=c.hash),999)['state']=='refused'
    assert len(calls)==1 and again.ledger.spent_cut('4h')==123


def test_growth_reaches_deeper_pending_children():
    one=Combination((PARTS[0],),'4h','fixed');pair=Combination(tuple(PARTS[:2]),'4h','fixed')
    known={Combination((PARTS[0],p),'4h','fixed').hash for p in PARTS[1:]}
    class L:
        def rows(self,tf,geo,k=None,verdict=None):
            c={1:one,2:pair}.get(k)
            return [dict(hash=c.hash,parts=json.dumps(list(c.keys)))] if c else []
        def known(self,*a):return known
    children=planner._grow_children(L(),'4h','fixed',{p.key:p for p in PARTS},12,6)
    assert children and {c.k for c in children}=={3}

@pytest.mark.parametrize('verdict',['error','untested','untestable'])
def test_unavailable_ablation_never_certifies_survivor(verdict):
    c=Combination(tuple(PARTS[:2]),'4h','fixed')
    bad=dict(verdict=verdict,portfolio=result(c)['portfolio'])
    assert growth.decide(result(c),subset_results={k:bad for k in c.keys})[0]!='survivor'


def test_late_ablation_refreshes_disposition_without_rescoring(tmp_path):
    r=ResearchRunner(Journal(tmp_path/'j.db'),dict(research=dict(enabled=True,referee=False)))
    from trader.research.vocab import BOOL_PARTS
    pair=BOOL_PARTS[:2]
    c=Combination(tuple(pair),'4h','fixed')
    a=Combination((pair[0],),'4h','fixed');b=Combination((pair[1],),'4h','fixed',round='ablation',parent=c.hash)
    r.ledger.record_control('4h','ohlcv',dict(consistency_p=.001),True)
    r.ledger.record_result(result(a,.1,10),'grow','fixture',{})
    r._record(result(c),c,planner.Batch('evaluate','4h','fixed','grow',[c]))
    stored=r.ledger.result(c.hash)
    r._record(result(b,.1,10),b,planner.Batch('evaluate','4h','fixed','ablation',[b]))
    assert r.ledger.rows('4h','fixed',k=2)[0]['verdict']=='survivor'
    assert r.ledger.result(c.hash)==stored


def test_failed_context_counts_against_threshold_coverage(monkeypatch):
    contexts=[SimpleNamespace(symbol='good',index=np.arange(10)),SimpleNamespace(symbol='bad',index=np.arange(90))]
    def values(tree,ctx):
        if ctx.symbol=='bad':raise ValueError('unavailable')
        return np.arange(10,dtype=float)
    monkeypatch.setattr(thresholds,'_values',values)
    m=thresholds.measure(['ret(6)'],contexts,min_samples=5,min_finite_frac=.5)['ret(6)']
    assert m['finite_frac']==.1 and not m['usable']

@pytest.mark.parametrize('unit',['s','ms','us','ns'])
def test_slice_units(unit):
    ts=pd.Series(pd.to_datetime(['2026-01-01','2026-01-02'],utc=True)).astype('datetime64['+unit+', UTC]')
    assert slices._ms(pd.DataFrame({'ts':ts})).tolist()==[1767225600000,1767312000000]

@pytest.mark.parametrize('which',['candles','derivs','refs'])
def test_readonly_stores_do_not_initialize_or_create(tmp_path,which):
    from trader.data.derivatives import DerivFeed
    from trader.data.references import RefStore
    p=tmp_path/(which+'.db')
    constructors={'candles':lambda:DataFeed(exchange=universe.NoExchange(),db_path=p,read_only=True),
                  'derivs':lambda:DerivFeed(db_path=p,read_only=True),
                  'refs':lambda:RefStore(p,read_only=True)}
    reader=constructors[which]()
    with pytest.raises(sqlite3.OperationalError):reader.db
    assert not p.exists()
    with sqlite3.connect(p) as db:db.execute('CREATE TABLE sentinel(x)')
    before=p.read_bytes()
    reader=constructors[which]()
    assert reader.db.execute('PRAGMA query_only').fetchone()[0]==1
    with pytest.raises(sqlite3.OperationalError):reader.db.execute('CREATE TABLE forbidden(x)')
    reader.db.close();assert p.read_bytes()==before


def test_coverage_uses_selected_readonly_source(tmp_path,monkeypatch):
    r=ResearchRunner(Journal(tmp_path/'j.db'),dict(research=dict(paths=dict(candles=str(tmp_path/'chosen.db')))))
    seen=[]
    def coverage(tfs,feed=None):
        assert feed is not None and feed._db_path==str(tmp_path/'chosen.db')
        assert feed.read_only
        seen.append(feed);return {'4h':{}}
    monkeypatch.setattr(universe,'coverage',coverage)
    assert r._symbols('4h')==([],[]) and len(seen)==1


def test_discovery_never_requests_heldout_price_frames(monkeypatch):
    frame=pd.DataFrame(dict(ts=pd.date_range('2020',periods=1000,freq='4h',tz='UTC'),open=100.,high=101.,low=99.,close=100.,volume=1.))
    class Feed:
        def __init__(self,*a,**kw):pass
        def cached_ohlcv(self,sym,*a,**kw):
            assert sym=='BTC/USDT';return frame
        def replay_metadata(self,sym,*a,**kw):
            return frame[['ts']].copy()
    monkeypatch.setattr('trader.data.feed.DataFeed',Feed)
    b=evaluate.load_bundle('4h',['BTC/USDT'],dict(risk={}),heldout_symbols=['ETH/USDT'])
    assert b.heldout_bars['a']['ETH/USDT']==1000


def test_status_does_not_register_budget(led,capsys):
    from trader.research.__main__ import _referee_status
    _referee_status(led,dict(research={}))
    assert not led.journal.query('SELECT * FROM research_budget')


def test_top_labels_cumulative_return(led,capsys):
    from trader.research.__main__ import _top
    c=Combination((PARTS[0],),'4h','fixed');led.record_result(result(c),'grow','fixture',{})
    _top(led,{'research':{'horizons':['4h']}},1)
    text=capsys.readouterr().out
    assert 'CAGR' not in text and 'total%' in text


def test_pending_receipt_cannot_be_rewritten_as_another_cut_or_finalized_twice(led):
    detail=dict(look_state='pending',cut_ms=100,evaluated={'identity':'frozen'})
    led.record_test('pending','4h','fixed','gate1',1.,led.next_alpha(.1,.05)[1],False,detail)
    with pytest.raises(ValueError,match='identity_changed'):
        led.complete_look('pending',0.,dict(detail,cut_ms=101),'referee_passed')
    assert led.spent_cut('4h')==100 and not led.tests()[0]['rejected']
    led.complete_look('pending',1.,dict(detail,look_state='complete'),'gate1_fail')
    before=led.tests()
    with pytest.raises(ValueError,match='not_pending'):
        led.complete_look('pending',0.,detail,'referee_passed')
    assert led.tests()==before


def test_duplicate_look_across_journals_is_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'race.db';l=Ledger(Journal(path));l.ensure();alpha=l.next_alpha(.1,.05)[1]
    def spend(_):
        local=Ledger(Journal(path))
        try:local.record_test('same','4h','fixed','gate1',1.,alpha,False,dict(cut_ms=100));return 'spent'
        except ValueError as e:return str(e)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(spend,range(2)))==['holdout_already_spent','spent']
    assert len(l.tests())==1


def test_replay_metadata_matches_authenticated_reader_and_distinguishes_storage(tmp_path):
    from trader.data import market_provenance as mp
    path=tmp_path/'c.db';writer=DataFeed(exchange=universe.NoExchange(),db_path=path)
    def bar(event,received,price=100):
        f=pd.DataFrame(dict(ts=pd.to_datetime([event],unit='ms',utc=True),open=[price],high=[price+1],low=[price-1],close=[price],volume=[1.]))
        return mp.annotate(f,instrument_id='offline:BTCUSDT',source='offline:synthetic',kind='candle',received_ms=received,timeframe='4h')
    with writer.db:
        # Exact-close, later revision, late backfill (beyond one bar), and partial at receipt.
        for event,received,price in [(0,TF,100),(0,TF+1,200),(TF,3*TF+1,100),(2*TF,2*TF+1,100)]:
            writer._store_save('BTC/USDT','4h',bar(event,received,price))
    writer.db.close()
    reader=DataFeed(exchange=universe.NoExchange(),db_path=path,read_only=True,clock_ms=lambda:4*TF)
    before=path.read_bytes();metadata=reader.replay_metadata('BTC/USDT','4h');prices=reader.cached_ohlcv('BTC/USDT','4h')
    assert metadata.ts.tolist()==prices.ts.tolist()==[pd.Timestamp(0,unit='ms',tz='UTC')]
    assert metadata.revision_id.tolist()==prices.revision_id.tolist()
    assert not {'open','high','low','close','volume','raw_json'} & set(metadata)
    assert metadata.attrs['stored_bars']==3 and metadata.attrs['replay_eligible_bars']==1
    assert prices.close.tolist()==[100.]
    reader.db.close();assert path.read_bytes()==before
    with sqlite3.connect(path) as db:db.execute('UPDATE market_revisions SET observed_ms=observed_ms+1 WHERE event_ms=0 AND available_ms=?',(TF,))
    corrupt=DataFeed(exchange=universe.NoExchange(),db_path=path,read_only=True,clock_ms=lambda:4*TF)
    for read in [corrupt.replay_metadata,corrupt.cached_ohlcv]:
        with pytest.raises(ValueError,match='index_corrupt'):read('BTC/USDT','4h')
    corrupt.db.close()


def test_retrospective_control_refused_before_reader_or_worker():
    fake=SimpleNamespace(cfg={'research':{'retrospective':{'dataset_id':'existing'}}})
    out=ResearchRunner._evaluate(fake,planner.Batch('evaluate','4h','trail','control'),[],[])
    assert out['skipped']=='retrospective_control_incompatible' and not out['ok']


def test_declared_timeframe_risk_reaches_heldout_costs(tmp_path,monkeypatch):
    from trader.research import referee
    frame=pd.DataFrame(dict(ts=pd.date_range('2020',periods=800,freq='4h',tz='UTC'),open=100.,high=101.,low=99.,close=100.,volume=1.))
    class Feed:
        def __init__(self,*a,**k):pass
        def cached_ohlcv(self,*a,**k):return frame
    monkeypatch.setattr('trader.data.feed.DataFeed',Feed)
    b=referee.load_heldout('4h','a',['BTC/USDT'],int(frame.ts.iloc[-1].timestamp()*1000),{'risk':{'risk_per_trade_pct':.5,'real_funding':False}})
    assert b.risk['bar_minutes']==240
    from copy import deepcopy
    from trader.strategy.vector_backtest import simulate, trade_table, walk_table
    from trader.strategy.geometries import GEOS
    ex=deepcopy(GEOS['trail']);ex.time={'max_bars':4}
    risk=dict(b.risk,taker_fee_pct=0.,slippage_atr_frac=0.,funding_rate_8h=.01)
    signals=np.zeros(len(frame),bool);signals[211]=True
    fills=[]
    actual=simulate(signals,np.zeros(len(frame),bool),frame,ex,risk,fills_out=fills)
    table=trade_table(frame,ex,risk)
    null_walk=walk_table(table,signals,np.zeros(len(frame),bool),risk)
    assert actual.trades==1 and len(null_walk)==1
    assert fills[0].r_multiple==pytest.approx(-.5)
    assert null_walk[0][2]==pytest.approx(fills[0].r_multiple)


def test_status_main_reads_existing_store_without_migration(tmp_path,monkeypatch,capsys):
    import sys
    from trader.research import __main__ as cli
    (tmp_path/'data').mkdir();path=tmp_path/'data'/'luffy.db'
    lg=Ledger(Journal(path));lg.ensure()
    lg.journal._conn().close();before=path.read_bytes()
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr(cli,'load_config',lambda:{'research':{'horizons':['4h']}})
    monkeypatch.setattr(cli,'_journal',lambda:pytest.fail('writable Journal constructed'))
    monkeypatch.setattr(sys,'argv',['trader.research','--status'])
    assert cli.main()==0
    assert path.read_bytes()==before
    with sqlite3.connect(path) as db:assert db.execute('SELECT COUNT(*) FROM research_budget').fetchone()[0]==0
    path.unlink()
    assert cli.main()==1 and not path.exists()


def test_funding_dependency_is_bound_to_selected_readonly_store(monkeypatch):
    from trader.strategy import spec_evidence, vector_backtest
    frame=pd.DataFrame({'ts':pd.date_range('2020',periods=3,freq='4h',tz='UTC')})
    frame.attrs['research_read_paths']={'derivs':'/tmp/selected-derivatives.db'}
    feed=spec_evidence.dependency_store(frame,'derivs')
    assert feed.read_only and feed._db_path=='/tmp/selected-derivatives.db'
    calls=[]
    def measured(sym,df):calls.append(df.attrs['research_read_paths']);return np.ones(3)*.01
    monkeypatch.setattr(spec_evidence,'funding_series',measured)
    assert np.all(vector_backtest.funding_for('BTC/USDT',frame,{})==.01)
    frame.attrs['research_read_paths']={'derivs':'/tmp/second.db'}
    vector_backtest.funding_for('BTC/USDT',frame,{})
    assert len(calls)==2  # never reuse a candle-only funding cache across studies


def test_calibration_unknown_dependence_is_not_printed_as_one(monkeypatch,capsys):
    import runpy,sys
    from trader.research import referee
    monkeypatch.setattr(referee,'consistency',lambda *a,**k:
                        dict(consistency_p=None,consistency_p_dep=None,rho_bar=None))
    monkeypatch.setattr(sys,'argv',['calibrate_null_dependence','null','1','.1','2'])
    runpy.run_path(str(Path(__file__).parents[1]/'scripts/calibrate_null_dependence.py'),run_name='__main__')
    text=capsys.readouterr().out
    assert 'rho_bar med=None known=0/2' in text and 'dep 0.000' in text


def test_source_shadow_does_not_claim_quantitative_pass_or_unmeasured_maturity(tmp_path,monkeypatch):
    import importlib.util
    path=Path(__file__).parents[1]/'scripts/predictive_research_shadow.py'
    spec=importlib.util.spec_from_file_location('review07_source_shadow',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'source.db'
    with sqlite3.connect(source):pass
    monkeypatch.setattr(module,'inventory',lambda *a:dict(counts={},mutations=0))
    monkeypatch.setattr(module.B,'cycle',lambda *a,**k:dict(counts={'eligible_research_results':1}))
    out=module.run(source,source,source,{},as_of_ms=123,max_sources=1)
    assert out['status']=='SOURCE_SHADOW_ONLY'
    assert 'submit=False' in out['real_evidence_blocked'][0]
    assert 'not yet mature' not in str(out)


def test_finalization_failure_rolls_back_to_consumed_pending_look(led,monkeypatch):
    detail=dict(look_state='pending',cut_ms=123,evaluated={'hash':'h'})
    led.record_test('h','4h','fixed','gate1',1.,led.next_alpha(.1,.05)[1],False,detail)
    def fail(*a,**k):raise RuntimeError('disposition write failed')
    monkeypatch.setattr(led,'set_candidate',fail)
    with pytest.raises(RuntimeError):led.complete_look('h',.00001,detail,'referee_passed')
    again=Ledger(Journal(led.journal.db_path))
    assert json.loads(again.spent('h')['detail'])==detail
    assert again.spent('h')['p']==1 and not again.spent('h')['rejected']
    assert again.spent_cut('4h')==123


@pytest.mark.parametrize('receipt',[dict(looked=True,hash='wrong',cut_ms=123),
                                  dict(looked=True,hash=None,cut_ms=123),
                                  dict(looked=True,hash='correct')])
def test_invalid_worker_identity_cannot_create_support(tmp_path,monkeypatch,receipt):
    monkeypatch.setattr('trader.research.referee.rotation_capacity',lambda *a,**k:None)
    monkeypatch.setattr(universe,'coverage',lambda *a,**k:{'4h':{s:11000 for s in universe.DISCOVERY+universe.HELDOUT}})
    c=Combination(tuple(PARTS[:2]),'4h','fixed')
    r=ResearchRunner(Journal(tmp_path/'j.db'),dict(research=dict(enabled=True,referee=True)),
        run=lambda *a,**k:ChildResult(ok=True,value=dict(receipt,gate1=dict(p=.000001,reason='fake'),gate3=dict(passed=True))))
    r.ledger.record_result(result(c),'survivor','fixture',{})
    r._combo=lambda *a:c;r._brake=lambda:1.
    assert r._examine('4h','fixed',dict(hash=c.hash),123)['state']=='gate1_fail'
    assert r.ledger.spent(c.hash)['p']==1 and not r.ledger.spent(c.hash)['rejected']


def test_top_preserves_unknown_metrics(led,capsys):
    from trader.research import __main__ as cli
    c=Combination(tuple(PARTS[:2]),'4h','fixed')
    r=result(c);r['portfolio']['total_pct']=None
    led.record_result(r,'grow','fixture',{})
    cli._top(led,{'research':{'horizons':['4h']}},10)
    text=capsys.readouterr().out
    assert text.count('n/a')==2 and 'CAGR' not in text


def test_malformed_history_is_not_discardable_legacy_accounting(led):
    with led.journal._tx() as db:
        db.execute("INSERT INTO research_combos(hash,tf,geo,k,result) VALUES('broken','4h','fixed',1,'{broken')")
    with pytest.raises(ValueError):led.discard_legacy_accounting(apply=True)
    assert led.journal.query("SELECT hash FROM research_combos")==[{'hash':'broken'}]


def test_rotation_metadata_resolves_the_existing_default_source(tmp_path,monkeypatch):
    from trader.core import config
    from trader.research import referee
    (tmp_path/'data').mkdir();path=tmp_path/'data'/'candles.db'
    writer=DataFeed(exchange=universe.NoExchange(),db_path=path)
    writer.db.close()
    monkeypatch.setattr(config,'ROOT',tmp_path)
    seen=[]
    def metadata(self,sym,*a,**k):
        assert self.read_only and Path(self._db_path)==path
        seen.append(sym)
        return pd.DataFrame({'ts':pd.date_range('2020',periods=1500,freq='4h',tz='UTC')})
    monkeypatch.setattr(DataFeed,'replay_metadata',metadata)
    cut=1577836800000+1000*TF
    assert referee.rotation_capacity('4h',cut,['A'],['B'],{},None)=={'a':{'A':1000},'b':{'B':500}}
    assert set(seen)=={'A','B'}


def test_restart_refreshes_committed_subset_without_repeating_measurements(tmp_path):
    from trader.research.vocab import BOOL_PARTS
    r=ResearchRunner(Journal(tmp_path/'j.db'),dict(research=dict(enabled=True,referee=False)))
    a,b=[Combination((p,),'4h','fixed') for p in BOOL_PARTS[:2]]
    c=Combination(tuple(BOOL_PARTS[:2]),'4h','fixed')
    r.ledger.record_result(result(a,.1,10),'grow','fixture',{})
    r._record(result(c),c,planner.Batch('evaluate','4h','fixed','grow',[c]))
    snapshot=r.journal.query('SELECT result,created_at FROM research_combos WHERE hash=?',(c.hash,))
    # Crash after subset commit, before its affected dispositions refresh.
    r.ledger.record_result(result(b,.1,10),'grow','fixture',{})
    restarted=ResearchRunner(Journal(tmp_path/'j.db'),r.cfg,run=lambda *a,**k:pytest.fail('numerical rescore'))
    restarted._refresh_dispositions('4h','fixed')
    assert restarted.ledger.rows('4h','fixed',k=2)[0]['verdict']=='survivor'
    assert restarted.journal.query('SELECT result,created_at FROM research_combos WHERE hash=?',(c.hash,))==snapshot


def test_readonly_identity_uses_injected_binding_without_provider(monkeypatch):
    from trader.data import market_provenance as mp
    ex=SimpleNamespace(id='binanceusdm')
    feed=DataFeed(exchange=ex,read_only=True)
    feed._instruments['BTC/USDT']=SimpleNamespace(venue='binance',value='retained-identity')
    monkeypatch.setattr(mp,'venue_identity',lambda exchange,sym:(None,None))
    monkeypatch.setattr('trader.data.feed.canonical_venue',lambda venue:'binance')
    monkeypatch.setattr(mp,'venue_source',lambda exchange:'injected-source')
    assert feed._identity('BTC/USDT')==('retained-identity','injected-source')


def test_refused_worker_receipt_remains_untested_and_consumed(tmp_path,monkeypatch):
    from trader.research import predictive_bridge as bridge
    monkeypatch.setattr(universe,'coverage',lambda *a,**k:{'4h':{s:11000 for s in universe.DISCOVERY+universe.HELDOUT}})
    monkeypatch.setattr('trader.research.referee.rotation_capacity',lambda *a,**k:None)
    c=Combination(tuple(PARTS[:2]),'4h','fixed',evaluation_scope='existing-scope')
    def worker(*a,**k):
        return ChildResult(ok=True,value=dict(hash=c.hash,cut_ms=123,looked=True,
            quantitative_receipt_refused='quantitative_wrong_cut'))
    r=ResearchRunner(Journal(tmp_path/'j.db'),dict(research=dict(enabled=True,referee=True)),run=worker)
    r.ledger.record_result(result(c),'survivor','fixture',{})
    r._combo=lambda *a:c;r._brake=lambda:1.
    assert r._examine('4h','fixed',dict(hash=c.hash),123)['state']=='gate1_fail'
    monkeypatch.setattr(bridge.P,'parts',lambda *a:c.parts)
    classification,_,_=bridge.classify(r,SimpleNamespace(hypothesis_id='source'),{'experiment_id':'existing-scope'})
    assert classification=='DATA_INSUFFICIENT'
    assert len(r.ledger.tests())==1 and r.ledger.spent(c.hash)['p']==1
    assert not r.ledger.spent(c.hash)['rejected'] and r.ledger.spent_cut('4h')==123
