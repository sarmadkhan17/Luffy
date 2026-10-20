"""Pending PAPER signal intent never suspends protection; no network or venue."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Decision
from trader.data import market_provenance as P
from trader.engine import paper_exit_evidence as X, paper_cost_evidence as C
from trader.engine.paper import PaperExecutor, iso
from trader.strategy import exit_policy as E, factory_handoff as F
from trader.strategy.compile import compile_version
from trader.strategy.spec import ExitSpec
from tests.authority_factory_fixtures import T0, DAY, _install
from tests.test_sol1_paper_cost import installed, bars, snapshot, H4, fetch_of, NOW, qualification
from tests.test_sol1_strategy_lifecycle import _spec, _source


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def forbidden(*a, **kw):
        pytest.fail('network forbidden in targeted paper tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    from trader.observability import prospective_execution
    monkeypatch.setattr(prospective_execution, 'for_journal', lambda j: None)


class Run:
    def __init__(self, path, side, *, max_bars=5, trail=False, signal='close < 109'):
        self.path, self.side = path, side
        self.cfg = load_config()
        self.spec = _spec(direction=side,
            entry_long='close > 0' if side=='long' else '',
            entry_short='close > 0' if side=='short' else '',
            exit=ExitSpec(stop={'kind':'pct','v':.04}, target={'kind':'rr','v':2.},
                trail={'kind':'atr','mult':4.,'arm_at_r':1.} if trail else {'kind':'none'},
                time={'max_bars':max_bars}, signal_exit=signal))
        self.j, out, _ = installed(path, self.cfg, self.spec)
        self.v = F.load_version(self.j, out['version_id'])
        self.start = T0//H4*H4
        self.paper = PaperExecutor(self.j, self.cfg, venue_environment='production')
        snap = self.snap([])
        sig = compile_version(self.j, self.v['version_id']).to_evaluator()(None, snap)
        self.dec = Decision('pending','pending','BTC/USDT',sig.action,1.,.5,.8,[],[])
        self.dec.strategy_signals = [dict(strategy_id=self.spec.id,action=sig.action.value,params=sig.params)]
        with pytest.raises(ValueError, match='paper_risk:'):
            self.paper.enter(self.dec,snap,self.spec.id,reference_equity=10000,state='FROZEN')
        self.tid = self.paper.enter(self.dec,snap,self.spec.id,reference_equity=10000,state='ACTIVE')
        assert self.paper.enter(self.dec,snap,self.spec.id,reference_equity=10000,state='ACTIVE')==self.tid
        self.initial = self.row()
        self.j.kv_set('control_state','FROZEN')

    def snap(self, tail, *, delay=0, partial=False):
        raw = bars(60,self.start,tail)
        parts=[]
        for i in range(len(raw)):
            at=self.start+(i+1)*H4+(delay if i==60 else 0)
            if partial and i==len(raw)-1:
                at=self.start+i*H4+100
            parts.append(P.annotate(raw.iloc[[i]],instrument_id='binance_usdm:futures:BTCUSDT',
                source='https://offline.example/fapi/v1/klines',kind='candle',timeframe='4h',received_ms=at))
        frame=pd.concat(parts,ignore_index=True)
        frame.attrs.update(timeframe='4h',read_mode='as_of')
        snap=snapshot(frame)
        snap.ts=iso(max(int(frame.observed_at_ms.max()),self.start+len(raw)*H4 if not partial else at))
        return snap

    def row(self):
        return self.j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(self.tid,))[0]

    def body(self):
        return json.loads(self.j.query(f'SELECT canonical_json FROM {X.TABLE} WHERE trade_id=?',(self.tid,))[0]['canonical_json'])

    def restart(self):
        self.j = Journal(self.path/'j.db')
        self.paper = PaperExecutor(self.j,self.cfg,venue_environment='production')

    def pending(self, *, delay=H4//3, tail=None):
        self.tail=tail or [(110,110.2,108.5,108.8)]
        self.delay=delay
        snap=self.snap(self.tail,delay=delay)
        assert self.paper.manage(snap)==[]
        state=E.decode(self.row()['exit_state_json'])[1]
        assert state.reason is None and state.bars_held==1 and self.row()['status']=='open'
        assert self.body()['signal_decision']['bar_ms']==state.last_bar_ms
        before=self.body()
        assert self.paper.manage(snap)==[] and self.body()==before
        self.restart()
        assert self.paper.manage(snap)==[] and self.body()==before
        return snap

    def verify(self, reason):
        row=self.row()
        assert row['status']=='closed' and row['close_reason']==reason
        assert row['realized_pnl']==pytest.approx((row['exit_price']-row['entry_price'])*(1 if self.side=='long' else -1)*row['amount'])
        inst=F.verify_install(self.j,self.v,current=False)
        X.verify(self.j,row,self.v,inst)
        assert row['version_id']==self.v['version_id'] and row['spec_hash']==self.v['spec_hash']
        return row


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('reason',['stop','target','time'])
def test_pending_signal_still_services_each_exit_and_books_once(tmp_path,side,reason):
    r=Run(tmp_path,side,max_bars=2)
    r.pending()
    tail={'stop':(108.8,109,100,104) if side=='long' else (108.8,120,108,115),
          'target':(108.8,121,108,120) if side=='long' else (108.8,109,99,100),
          'time':(108.8,110,108,109)}[reason]
    snap=r.snap(r.tail+[tail],delay=r.delay)
    assert r.paper.manage(snap)==[r.tid]
    row=r.verify(reason)
    assert row['bars_held']==2 and 'signal_fill' not in r.body()
    assert r.paper.complete_costs(fetch=fetch_of(),clock=lambda:NOW)==[r.tid]
    inst=F.verify_install(r.j,r.v,current=False)
    C.verify(r.j,row,r.v,inst)
    receipt=r.j.query(f'SELECT * FROM {C.TABLE}')
    r.restart()
    assert r.paper.manage(snap)==[] and r.paper.complete_costs(fetch=fetch_of(),clock=lambda:NOW)==[]
    assert r.j.query(f'SELECT * FROM {C.TABLE}')==receipt and r.row()==row
    assert r.j.query('SELECT * FROM trades')==[]


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('gap',[None,'stop','target'])
def test_first_executable_open_and_prior_gap_protection(tmp_path,side,gap):
    r=Run(tmp_path,side)
    r.pending(delay=0)
    decision=r.body()['signal_decision']
    px=109.5 if gap is None else (104 if side=='long' else 116) if gap=='stop' else (120 if side=='long' else 100)
    # The future range crosses BOTH levels, but only this observed OPEN exists
    # at execution. It must not retroactively beat the legitimate signal fill.
    snap=r.snap(r.tail+[(px,max(px,125),min(px,95),px)],partial=True)
    assert r.paper.manage(snap)==[r.tid]
    row=r.verify(gap or 'signal')
    body=r.body()
    assert body['signal_decision']==decision
    assert body['signal_fill']['decision_id']==decision['decision_id']
    assert body['signal_fill']['fill_id']!=decision['decision_id']
    assert row['exit_price']==px and row['closed_at']==iso(r.start+61*H4)
    assert row['bars_held']==1 and row['exit_fill_basis']=='recorded_D6_executable_open'
    assert r.paper.manage(snap)==[]
    assert r.paper.complete_costs(fetch=fetch_of(),clock=lambda:NOW)==[r.tid]
    C.verify(r.j,row,r.v,F.verify_install(r.j,r.v,current=False))
    assert r.paper.complete_costs(fetch=fetch_of(),clock=lambda:NOW)==[]


@pytest.mark.parametrize('side',['long','short'])
def test_receipt_delay_uses_D6_first_open_not_signal_close(tmp_path,side):
    r=Run(tmp_path,side)
    r.pending()
    second=(108.8,110,108,109)
    # Bar 61 opened before decision-input receipt availability; it cannot fill.
    snap=r.snap(r.tail+[second],delay=r.delay)
    assert r.paper.manage(snap)==[] and r.row()['bars_held']==2
    r.restart()
    third=(109.5,110,109,109.8)
    snap=r.snap(r.tail+[second,third],delay=r.delay,partial=True)
    assert r.paper.manage(snap)==[r.tid]
    row=r.verify('signal')
    assert row['exit_price']==109.5 and row['closed_at']==iso(r.start+62*H4)
    assert r.body()['signal_fill']['bar_ms']==r.start+62*H4


@pytest.mark.parametrize('side',['long','short'])
def test_pending_trail_arms_tightens_and_is_effective_next_bar(tmp_path,side):
    r=Run(tmp_path,side,trail=True,signal='close > 0')
    tail=[(110,116,109,115),(115,117,114,116)]
    if side=='short':tail=[(220-o,220-l,220-h,220-c) for o,h,l,c in tail]
    r.pending(tail=tail[:1])
    _,s1=E.decode(r.row()['exit_state_json'])
    assert s1.trail=='armed'
    assert r.paper.manage(r.snap(tail,delay=r.delay))==[]
    _,s2=E.decode(r.row()['exit_state_json'])
    assert s2.trail=='armed'
    assert s2.active_stop>s1.active_stop if side=='long' else s2.active_stop<s1.active_stop
    assert r.row()['take_profit']==r.initial['take_profit']
    stopped=(110,111,109,110)
    snap=r.snap(tail+[stopped],delay=r.delay,partial=True)
    assert r.paper.manage(snap)==[r.tid]
    r.verify('stop')


@pytest.mark.parametrize('side',['long','short'])
def test_max_bars_is_not_suspended_on_signal_decision_bar(tmp_path,side):
    r=Run(tmp_path,side,max_bars=1)
    assert r.paper.manage(r.snap([(110,110.2,108.5,108.8)]))==[r.tid]
    r.verify('time')
    assert r.body()['signal_decision'] and 'signal_fill' not in r.body()


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('field,value',[('available_at_ms',None),('observed_at_ms',None),
    ('quality','INVALID'),('available_at_ms',10**15)])
def test_unknown_future_or_invalid_fill_receipt_leaves_protection_active(tmp_path,side,field,value):
    r=Run(tmp_path,side,max_bars=2)
    r.pending(delay=0)
    snap=r.snap(r.tail+[(109.5,110,109,109.8)],partial=True)
    snap.dfs['4h'].loc[61,field]=value
    assert r.paper.manage(snap)==[]
    assert r.row()['status']=='open' and 'signal_fill' not in r.body()
    # A later final bar with valid receipts still services the time cap.
    # Keep signal decision receipts frozen; invalidate only execution quality.
    snap=r.snap(r.tail+[(109.5,110,109,109.8)])
    snap.dfs['4h'].loc[61,'quality']='INVALID'
    # Invalid completed bars cannot be consumed as trustworthy observations.
    with pytest.raises(ValueError,match='exit_observation_not_consecutive_closed_bar'):
        r.paper.manage(snap)


def test_signal_fill_retains_original_version_after_replacement(tmp_path):
    r=Run(tmp_path,'long')
    r.pending(delay=0)
    changed=copy.deepcopy(r.spec);changed.exit.target={'kind':'rr','v':3.}
    new=F.create_version(r.j,r.cfg,_source(changed,qualification(changed,r.cfg)),at_ms=T0+DAY)
    _install(r.j,new['version_id'],at_ms=T0+2*DAY)
    r.restart()
    snap=r.snap(r.tail+[(109.5,110,109,109.8)],partial=True)
    assert r.paper.manage(snap)==[r.tid]
    row=r.verify('signal')
    assert row['version_id']!=new['version_id']
    assert r.body()['version_id']==r.v['version_id']


@pytest.mark.parametrize('field',['price','bar_ms','decision_id','fill_id'])
def test_signal_fill_evidence_tampering_refused_even_after_redigest(tmp_path,field):
    r=Run(tmp_path,'long');r.pending(delay=0)
    assert r.paper.manage(r.snap(r.tail+[(109.5,110,109,109.8)],partial=True))==[r.tid]
    body=r.body()
    body['signal_fill'][field]=0 if field in ('price','bar_ms') else 'conflict'
    with r.j._tx() as db:X.record(db,body)
    with pytest.raises(ValueError,match='canonical_signal_fill_differs'):
        X.verify(r.j,r.row(),r.v,F.verify_install(r.j,r.v,current=False))


def test_concurrent_restarted_executors_close_and_finalize_once(tmp_path):
    r=Run(tmp_path,'long');r.pending(delay=0)
    snap=r.snap(r.tail+[(109.5,110,109,109.8)],partial=True)
    def manage(_):
        p=PaperExecutor(Journal(tmp_path/'j.db'),r.cfg,venue_environment='production')
        return p.manage(snap),p.complete_costs(fetch=fetch_of(),clock=lambda:NOW)
    with ThreadPoolExecutor(max_workers=2) as workers:
        calls=list(workers.map(manage,range(2)))
    assert sum((a for a,b in calls),[])==[r.tid]
    assert sum((b for a,b in calls),[])==[r.tid]
    r.verify('signal')
    assert len(r.j.query(f'SELECT * FROM {C.TABLE}'))==1


@pytest.mark.parametrize('side',['long','short'])
def test_existing_D7_terminal_pending_row_recovers_protection(tmp_path,side):
    r=Run(tmp_path,side);r.pending()
    body=r.body();policy,initial=E.decode(body['initial'])
    old=E.advance(policy,initial,E.Observation(**body['observations'][0]),r.row()['amount'])
    body.pop('paper_signal_execution');body.pop('signal_decision')
    body['final']=E.encode(policy,old.state)
    with r.j._tx() as db:
        X.record(db,body)
        db.execute('UPDATE versioned_paper_trades SET exit_state_json=? WHERE id=?',(body['final'],r.tid))
    r.restart()
    tail=(108.8,109,100,104) if side=='long' else (108.8,120,108,115)
    assert r.paper.manage(r.snap(r.tail+[tail],delay=r.delay))==[r.tid]
    r.verify('stop')
    assert r.body()['legacy_signal_evidence_sha256']==X.digest(X.canonical(body))


def test_closure_evidence_and_state_roll_back_together_then_retry(tmp_path,monkeypatch):
    r=Run(tmp_path,'long');r.pending(delay=0)
    before, evidence=r.row(),r.body()
    snap=r.snap(r.tail+[(109.5,110,109,109.8)],partial=True)
    record=X.record
    def interrupt(db,body):
        record(db,body)
        if body.get('signal_fill'):raise RuntimeError('TEST-ONLY interrupted transaction')
    with monkeypatch.context() as patch:
        patch.setattr(X,'record',interrupt)
        with pytest.raises(RuntimeError,match='interrupted transaction'):r.paper.manage(snap)
    assert r.row()==before and r.body()==evidence
    r.restart()
    assert r.paper.manage(snap)==[r.tid]
    r.verify('signal')
    assert r.paper.manage(snap)==[]


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('delay',[0,H4//3])
def test_D6_vector_and_paper_choose_identical_signal_fill(tmp_path,side,delay):
    import numpy as np
    from trader.strategy import fill_timing as D6, vector_backtest as V
    from trader.agents.indicators import atr_series
    r=Run(tmp_path,side);r.pending(delay=delay)
    tail=r.tail+[(108.8,110,108,109),(109.5,110,109,109.8)]
    # Zero delay fills at 61's open; delayed input receipt fills at 62's open.
    snap=r.snap(tail,delay=delay)
    assert r.paper.manage(snap)==[r.tid]
    row=r.verify('signal')
    df=snap.df('4h')
    df.attrs['signal_lookback_bars']=D6.lookback_bars(r.spec)
    fill,_=D6.fills(df)
    ex=E.bind_research(copy.deepcopy(r.spec)).exit
    signal=np.zeros(len(df),bool);signal[60]=True
    available=np.ones(len(df),bool)
    evidence=[]
    result=V._trade(59,side,df,df.close.to_numpy(),df.high.to_numpy(),df.low.to_numpy(),
        atr_series(df,14).to_numpy(),ex,0.,0.,5,0.,0.,signal,None,0.,240,
        exit_evidence_out=evidence,fill=fill,exit_sig_available=available)
    assert result[0]==(61 if delay==0 else 62)
    assert evidence[0]['fill_price']==row['exit_price']
    assert int(df.ts.iloc[result[0]].timestamp()*1000)==r.body()['signal_fill']['bar_ms']
    assert result[2]*row['amount']==pytest.approx(row['realized_pnl'])


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('initial_stop',[False,True])
def test_exact_original_Donchian_entries_stops_trailing_and_accounting(tmp_path,monkeypatch,side,initial_stop):
    from pathlib import Path
    from trader.strategy.spec import StrategySpec
    from tests import test_sol1_paper_cost as fixture
    from tests.test_sol1_strategy_lifecycle import _ev
    original=json.loads((Path(__file__).parents[1]/'data/authored_specs/donchian_breakout_trail.json').read_text())
    # All qualification numbers are TEST-ONLY, including all 16 declared symbols.
    monkeypatch.setattr(fixture,'_ev',lambda:_ev(trades=160,symbols=tuple(original['universe']['include'])))
    spec=StrategySpec.from_dict(original);cfg=load_config()
    j,out,_=installed(tmp_path,cfg,spec);v=F.load_version(j,out['version_id'])
    assert v['spec']['exit']==original['exit'] and v['spec']['universe']==original['universe']
    start=T0//H4*H4
    entry=(110,121,109,120) if side=='long' else (110,111,89,90)
    def snap(tail):
        df=bars(220,start,tail);df.loc[219,['open','high','low','close']]=entry
        df=P.annotate(df,instrument_id='binance_usdm:futures:BTCUSDT',source='https://offline.example/fapi/v1/klines',
                      kind='candle',timeframe='4h',received_ms=start+len(df)*H4)
        return snapshot(df)
    initial=snap([])
    sig=compile_version(j,v['version_id']).to_evaluator()(None,initial)
    assert sig is not None and sig.action.value==('BUY' if side=='long' else 'SELL')
    dec=Decision('donchian','donchian','BTC/USDT',sig.action,1.,.5,.8,[],[])
    dec.strategy_signals=[dict(strategy_id=spec.id,action=sig.action.value,params=sig.params)]
    paper=PaperExecutor(j,cfg,venue_environment='production')
    with pytest.raises(ValueError,match='paper_risk:'):paper.enter(dec,initial,spec.id,reference_equity=10000,state='FROZEN')
    tid=paper.enter(dec,initial,spec.id,reference_equity=10000,state='ACTIVE')
    j.kv_set('control_state','FROZEN')
    def row():return j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    policy,state=E.decode(row()['exit_state_json'])
    assert not policy.signal_exit and row()['take_profit']==0
    assert policy.initial_r==pytest.approx(2*policy.entry_atr)
    tail=([(120,121,100,105)] if side=='long' else [(90,110,89,105)]) if initial_stop else (
        [(120,128,119,127),(127,135,126,134),(124,125,123,124)] if side=='long' else
        [(90,91,82,83),(83,84,75,76),(89,90,88,89)])
    stops=[]
    for k in range(1,len(tail)+1):
        paper=PaperExecutor(Journal(tmp_path/'j.db'),cfg,venue_environment='production')
        closed=paper.manage(snap(tail[:k]))
        state=E.decode(row()['exit_state_json'])[1];stops.append(state.active_stop)
        assert closed==([tid] if k==len(tail) else [])
    if initial_stop:assert state.trail=='dormant' and row()['exit_price']==policy.stop
    else:
        assert state.trail=='armed'
        assert stops[1]>stops[0]>policy.stop if side=='long' else stops[1]<stops[0]<policy.stop
        assert row()['realized_pnl']>0
    assert row()['status']=='closed' and row()['close_reason']=='stop'
    X.verify(j,row(),v,F.verify_install(j,v,current=False))
    assert paper.complete_costs(fetch=fetch_of(),clock=lambda:NOW)==[tid]
    C.verify(j,row(),v,F.verify_install(j,v,current=False))
    assert paper.manage(snap(tail))==[] and paper.complete_costs(fetch=fetch_of(),clock=lambda:NOW)==[]


@pytest.mark.parametrize('side',['long','short'])
def test_legitimate_open_at_time_boundary_precedes_time_fallback(tmp_path,side):
    r=Run(tmp_path,side,max_bars=1)
    snap=r.snap([(110,110.2,108.5,108.8),(109.5,110,109,109.8)],partial=True)
    assert r.paper.manage(snap)==[r.tid]
    row=r.verify('signal')
    assert row['exit_price']==109.5 and row['bars_held']==1


@pytest.mark.parametrize('field,value',[('open',109.7),('available_at_ms',1),('revision_id','fake')])
def test_modified_fill_price_or_receipt_cannot_authorize_execution(tmp_path,field,value):
    r=Run(tmp_path,'long');r.pending(delay=0)
    snap=r.snap(r.tail+[(109.5,110,109,109.8)],partial=True)
    snap.dfs['4h'].loc[61,field]=value
    assert r.paper.manage(snap)==[] and r.row()['status']=='open'
    assert 'signal_fill' not in r.body()


def test_unrelated_later_unknown_receipt_cannot_hide_first_legitimate_open(tmp_path):
    r=Run(tmp_path,'long');r.pending(delay=0)
    snap=r.snap(r.tail+[(109.5,110,109,109.8),(109.8,110,109,109.7)])
    snap.dfs['4h'].loc[62,'available_at_ms']=None
    assert r.paper.manage(snap)==[r.tid]
    assert r.verify('signal')['exit_price']==109.5


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('case',['delayed_time','stop_gap','target_gap','time_boundary'])
def test_paper_vector_parity_at_protective_and_time_boundaries(tmp_path,side,case):
    import numpy as np
    from trader.strategy import fill_timing as D6, vector_backtest as V
    from trader.agents.indicators import atr_series
    max_bars=1 if case in ('delayed_time','time_boundary') else 5
    delay=H4//3 if case=='delayed_time' else 0
    r=Run(tmp_path,side,max_bars=max_bars)
    px=(104 if side=='long' else 116) if case=='stop_gap' else (120 if side=='long' else 100) if case=='target_gap' else 109.5
    tail=[(110,110.2,108.5,108.8),(px,max(px,125),min(px,95),px),(px,px+.1,px-.1,px)]
    snap=r.snap(tail,delay=delay)
    assert r.paper.manage(snap)==[r.tid]
    expected='time' if case=='delayed_time' else 'stop' if case=='stop_gap' else 'target' if case=='target_gap' else 'signal'
    row=r.verify(expected)
    df=snap.df('4h');df.attrs['signal_lookback_bars']=D6.lookback_bars(r.spec)
    fill,_=D6.fills(df)
    signal=np.zeros(len(df),bool);signal[60]=True
    evidence=[]
    result=V._trade(59,side,df,df.close.to_numpy(),df.high.to_numpy(),df.low.to_numpy(),
        atr_series(df,14).to_numpy(),E.bind_research(copy.deepcopy(r.spec)).exit,0.,0.,max_bars,0.,0.,
        signal,None,0.,240,exit_evidence_out=evidence,fill=fill,exit_sig_available=np.ones(len(df),bool))
    assert evidence[0]['state']['state']['reason']==expected
    assert evidence[0]['fill_price']==row['exit_price']
    assert result[2]*row['amount']==pytest.approx(row['realized_pnl'])


def test_old_pending_decision_without_receipts_cannot_invent_a_historical_fill(tmp_path):
    r=Run(tmp_path,'long',max_bars=2);r.pending(delay=0)
    body=r.body();policy,initial=E.decode(body['initial'])
    old=E.advance(policy,initial,E.Observation(**body['observations'][0]),r.row()['amount'])
    body.pop('paper_signal_execution');body.pop('signal_decision')
    body['final']=E.encode(policy,old.state)
    with r.j._tx() as db:
        X.record(db,body)
        db.execute('UPDATE versioned_paper_trades SET exit_state_json=? WHERE id=?',(body['final'],r.tid))
    r.restart()
    partial=r.snap(r.tail+[(109.5,110,109,109.8)],partial=True)
    assert r.paper.manage(partial)==[]
    assert r.body()['signal_decision']['inputs']==[] and 'signal_fill' not in r.body()
    assert r.body()['signal_decision']['availability']=='UNAVAILABLE_LEGACY_DECISION_RECEIPTS'
    assert r.paper.manage(r.snap(r.tail+[(109.5,110,109,109.8)]))==[r.tid]
    r.verify('time')
