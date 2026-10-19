"""Synthetic review counterexamples. No production inputs or worker evaluation."""
import json, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
from trader.core.journal import Journal
from trader.core.child import ChildResult
from trader.data import market_provenance as mp
from trader.research import planner, thresholds, slices
from trader.research.combo import Combination
from trader.research.vocab import Part
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner
from trader.strategy.portfolio_evidence import accounting_provenance, RESEARCH_CLOCK
OUT = Path(__file__).parent

def run():
    results = {}
    with tempfile.TemporaryDirectory(prefix='review07-quant-probes-') as temp:
        root = Path(temp)
        import sqlite3
        db = sqlite3.connect(':memory:'); mp.init(db)
        step=14_400_000; n=600
        frame=pd.DataFrame(dict(ts=pd.to_datetime(np.arange(n)*step,unit='ms',utc=True),open=100.,high=101.,low=99.,close=100.,volume=1.))
        annotated=mp.annotate(frame,instrument_id='binance:usdm:BTCUSDT',source='binance:public',kind='candle',received_ms=n*step+1,timeframe='4h')
        mp.append(db,'candle:BTC/USDT:4h',annotated)
        asof=mp.load(db,'candle:BTC/USDT:4h',as_of_ms=n*step+1)
        replay=mp.load(db,'candle:BTC/USDT:4h',as_of_ms=n*step+1,replay_tf='4h')
        results['receipt_close_mismatch']=dict(stored=n,asof=len(asof),replay=0 if replay is None else len(replay),last_receipt_lag_ms=1)
        # An exact-on-close synthetic receipt is a control, never historical fabrication.
        exact=mp.annotate(frame.tail(1),instrument_id='binance:usdm:ETHUSDT',source='binance:public',kind='candle',received_ms=n*step,timeframe='4h')
        mp.append(db,'candle:ETH/USDT:4h',exact)
        control=mp.load(db,'candle:ETH/USDT:4h',as_of_ms=n*step,replay_tf='4h')
        results['receipt_close_mismatch']['exact_close_synthetic_control']=len(control)
        # Planner has exhausted pairs, yet a growing pair can make an unseen triple.
        parts=[Part(x,'state',x,'close > ema(20)','close < ema(20)') for x in ('A','B','C')]
        one=Combination((parts[0],),'4h','fixed'); pair=Combination(tuple(parts[:2]),'4h','fixed'); triple=Combination(tuple(parts),'4h','fixed')
        pairs=[Combination((parts[0],p),'4h','fixed') for p in parts[1:]]
        class FakeLedger:
            def rows(self,tf,geo,k=None,verdict=None):
                return [dict(hash=c.hash,parts=json.dumps(list(c.keys)))] if (c := {1:one,2:pair}.get(k)) else []
            def known(self,tf,geo):return {p.hash for p in pairs}
        led=FakeLedger(); children=planner._grow_children(led,'4h','fixed',{p.key:p for p in parts},12,6)
        results['growth_depth_starvation']=dict(returned_depths=sorted({c.k for c in children}),unknown_returned=len(planner._pending(children,led,'4h','fixed')),unseen_triple=triple.hash not in led.known('4h','fixed'))
        # Errors do not contribute to finite_frac's denominator.
        ctxs=[SimpleNamespace(symbol='good',index=np.arange(10)),SimpleNamespace(symbol='broken',index=np.arange(90))]
        def values(tree,ctx):
            if ctx.symbol=='broken':raise ValueError('synthetic unavailable context, 90 bars')
            return np.arange(10,dtype=float)
        with patch.object(thresholds,'_values',values):
            measured=thresholds.measure(['ret(6)'],ctxs,min_samples=5,min_finite_frac=.5)
        results['threshold_failed_context_omitted']=measured['ret(6)']
        # Durable ledger JSON is sliced as text.
        ledger=Ledger(Journal(root/'json.db'));ledger.ensure()
        ledger.record_test('long','4h','fixed','gate1',1.,ledger.next_alpha(.1,.05)[1],False,dict(padding='x'*21000,cut_ms=123))
        raw=ledger.tests()[0]['detail']
        try:json.loads(raw);valid=True
        except ValueError:valid=False
        results['truncated_look_receipt']=dict(stored_bytes=len(raw),valid_json=valid,spent_cut=ledger.spent_cut('4h'))
        # Actual runner accepts a looked=True failed evaluation without charging it.
        cfg=dict(research=dict(enabled=True,referee=True,horizons=['4h'],geometries=['fixed']))
        runner=ResearchRunner(Journal(root/'retry.db'),cfg,run=lambda *a,**k:ChildResult(ok=True,value=dict(looked=True,error='synthetic error AFTER protected read')))
        c=Combination(tuple(parts[:2]),'4h','fixed')
        result=dict(hash=c.hash,tf=c.tf,geo=c.geo,k=c.k,parts=list(c.keys),portfolio=dict(accounting=accounting_provenance(2000,.5,8,RESEARCH_CLOCK)),testable=True)
        runner.ledger.record_result(result,'survivor','fixture',{})
        runner.ledger.set_candidate(c.hash,'4h','fixed','queued')
        runner._combo=lambda *a:c;runner._symbols=lambda tf:(['BTC/USDT'],['ETH/USDT']);runner._brake=lambda:1.
        r1=runner._examine('4h','fixed',dict(hash=c.hash),100)
        r2=runner._examine('4h','fixed',dict(hash=c.hash),100)
        results['failed_look_retried_free']=dict(attempts=2,spent_looks=len(runner.ledger.tests()),candidate_state=runner.ledger.candidate(c.hash)['state'],errors=[r1['error'],r2['error']])
        # Supported reader uses no explicit path while refusing/stamping against one.
        with patch('trader.research.universe.coverage',return_value={'4h':{}}) as coverage:
            runner.cfg['research']['paths']={'candles':str(root/'chosen.db')}
            runner._symbols=ResearchRunner._symbols.__get__(runner)
            runner._symbols('4h')
            results['coverage_ignores_selected_path']=dict(args=list(coverage.call_args.args),kwargs=coverage.call_args.kwargs,selected=str(root/'chosen.db'))
        # Child discovery reads held-out prices just to count them.
        from trader.research import evaluate
        class Feed:
            def __init__(self,*a,**k):pass
            def cached_ohlcv(self,sym,*a,**k):
                calls.append(sym);return frame
        calls=[]
        with patch('trader.data.feed.DataFeed',Feed):
            b=evaluate.load_bundle('4h',['BTC/USDT'],dict(risk={}),heldout_symbols=['ETH/USDT'],paths={'candles':str(root/'unused.db')})
        results['discovery_loads_heldout_prices']=dict(frame_reader_calls=calls,heldout_counts=b.heldout_bars)
        # ns/ms are correct, seconds timestamps are silently mis-scaled.
        seconds=pd.DataFrame({'ts':pd.Series(pd.to_datetime(['2026-01-01'],utc=True)).astype('datetime64[s, UTC]')})
        results['seconds_slice_clock']=dict(got=int(slices._ms(seconds).iloc[0]),expected=int(seconds.ts.iloc[0].timestamp()*1000))
        from trader.research import growth, control
        accounting=accounting_provenance(2000,.5,8,RESEARCH_CLOCK)
        def scored(combo,p,pct):
            return dict(hash=combo.hash,tf=combo.tf,geo=combo.geo,k=combo.k,parts=list(combo.keys),
                        trades=100,verdict='scored',testable=True,consistency_p=p,
                        portfolio=dict(accounting=accounting,total_pct=pct))
        child=scored(c,.01,20)
        bad=dict(verdict='error',error='input unreadable',portfolio=dict(accounting=accounting))
        results['errored_ablations_count_as_beaten']=dict(verdict=growth.decide(child,subset_results={key:bad for key in c.keys})[0])
        rr=ResearchRunner(Journal(root/'ablation.db'),dict(research=dict(enabled=True,referee=False)))
        sub1=Combination((parts[0],),'4h','fixed');sub2=Combination((parts[1],),'4h','fixed')
        rr.ledger.record_control('4h','ohlcv',dict(consistency_p=.001),True)
        batch=planner.Batch('evaluate','4h','fixed','grow',[c])
        rr.ledger.record_result(scored(sub1,.1,10),'grow','synthetic',{})
        rr._record(child,c,batch)
        before=rr.ledger.rows('4h','fixed',k=2)[0]['verdict']
        abl=Combination((parts[1],),'4h','fixed',parent=c.hash,round='ablation')
        rr._record(scored(abl,.1,10),abl,planner.Batch('evaluate','4h','fixed','ablation',[abl]))
        after=rr.ledger.rows('4h','fixed',k=2)[0]['verdict']
        results['late_ablations_do_not_refresh_child']=dict(before=before,after=after,
             verdict_with_completed_evidence=growth.decide(child,subset_results={parts[0].key:rr.ledger.result(sub2.hash),parts[1].key:rr.ledger.result(sub1.hash)})[0])
        try:
            evaluate._retrospective_bundle('4h',control.INCUMBENT_UNIVERSE,
                dict(research=dict(retrospective={'dataset_id':'synthetic'})),('ohlcv',),(),{},None)
        except ValueError as exc:
            results['retrospective_control_conflict']=dict(reason=str(exc),
                protected_control_symbols=sorted(set(control.INCUMBENT_UNIVERSE)&set(__import__('trader.research.universe',fromlist=['HELDOUT']).HELDOUT)))
        from trader.strategy.vector_backtest import _trade
        from trader.strategy.geometries import GEOS
        x=np.full(20,100.);a=np.ones(20)
        def cost(minutes):
            return _trade(0,'long',None,x,x,x,a,GEOS['trail'],0.,0.,4,4.,1.,None,None,.01,minutes)[2]
        results['trail_funding_timeframe']=dict(default_15m_unit_pnl=cost(15),declared_4h_unit_pnl=cost(240))
        from trader.research.__main__ import _referee_status
        import contextlib,io
        status=Ledger(Journal(root/'status.db'));status.ensure()
        before=status.journal.query('SELECT * FROM research_budget')
        with contextlib.redirect_stdout(io.StringIO()):_referee_status(status,{'research':{}})
        results['status_registers_budget']=dict(before=len(before),after=len(status.journal.query('SELECT * FROM research_budget')))
        from trader.data.feed import DataFeed
        from trader.research.universe import NoExchange
        source=root/'read-source.db'
        db=sqlite3.connect(source);db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,taker_buy REAL)');db.commit();db.close()
        feed=DataFeed(exchange=NoExchange(),db_path=source)
        names={r[0] for r in feed.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        results['normal_read_initializes_source_schema']=dict(new_tables=sorted(names-{'candles'}))
        feed.db.close()
    (OUT/'probes.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))

if __name__=='__main__':run()
