"""Real owned primary/hedge reductions under offline qualification/venue fixtures."""
import json
from dataclasses import asdict,replace
from unittest.mock import Mock
import pandas as pd
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup as recovery_setup
from tests.test_coordinated_two_instruments import pair
from tests.test_sol1_paper_cost import bars,snapshot,H4,iso
from tests.test_sol1_strategy_lifecycle import _spec
from trader.strategy.spec import ExitSpec
from trader.strategy import exit_policy as E,compile as compiler
from trader.engine import coordinated_venue as CV,coordinated_unwind as U
from trader.engine import live_signal_execution as L,live_signal_evidence as S

setup=recovery_setup


def prepared(setup,monkeypatch):
    start=1800000000000//H4*H4
    exit_=ExitSpec(stop={'kind':'pct','v':.04},target={'kind':'none'},time={'max_bars':10},signal_exit='close < 109')
    spec=_spec(direction='long',entry_long='close > 0',entry_short='',exit=exit_)
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch,exit_definition=asdict(exit_),entry_bar_ms=start+59*H4,step_ms=H4)
    result=CV.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:result['filled_quantity']})
    read=ex.fetch_positions
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(r,symbol=r['symbol']+':USDT',info={'symbol':r['symbol'].replace('/','')}) for r in read()])
    trade=next(t for t in j.open_trades() if t['symbol']=='BTC/USDT')
    root=json.loads(trade['entry_identity_json']);p,state=E.decode(root['exit_state'])
    snap=snapshot(bars(60,start,[(110.,110.2,108.5,108.8)]));at=int(pd.Timestamp(snap.ts).timestamp()*1000)
    contexts=[];compiler.compile_spec(spec).exit_signal_detail(snap.dfs,symbol=trade['symbol'],context_out=contexts)
    observation=E.Observation(start+60*H4,110.,110.2,108.5,108.8,signal_exit=True)
    with j._tx() as db:
        S.ensure(db);S.retain(db,trade,root,spec,snap,p,state,observation,context=contexts[0])
    continuing,_=S.advance(p,state,observation,float(trade['amount']))
    snap=replace(snap,ts=iso(at+1000));monkeypatch.setattr(L.time,'time',lambda:(at+1000)/1000)
    quote=Mock(return_value=dict(symbol=trade['symbol'],contract='linear',instrument_id=root['execution_binding']['instrument_id'],
        source='TEST_ONLY_public_book',started_ms=at+1000,received_ms=at+1000,bid=108.7,ask=108.9))
    return ex,j,e,trade,spec,snap,p,continuing.state,quote


@pytest.mark.parametrize('lost',[False,True])
def test_main_signal_closes_whole_position_primary_first_and_preserves_first_proof(setup,monkeypatch,lost):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    if lost:ex.close_error=RequestTimeout('accepted primary close response lost')
    result=L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert result['status']=='UNWINDING' and result['leg']=='primary'
    plan=U._plan(j.query('SELECT * FROM '+U.TABLE)[0]);proof=j.query('SELECT * FROM '+L.TABLE)[0]
    assert plan['strategy_exit_decision']['intent']=='signal'
    assert plan['strategy_exit_decision']['signal_eligibility_sha256']==proof['sha256']
    assert plan['strategy_exit_decision']['scope']=='WHOLE_POSITION'
    assert any(t['symbol']=='ETH/USDT' for t in j.open_trades())
    ex.close_error=None
    for _ in range(5):
        U.resume(e)
        if not U.active(j,'g'):break
    assert not j.open_trades() and not ex.positions and not U.active(j,'g')
    closes=[r for r in ex.sent if r[1]=='market' and r[4].get('reduceOnly') and not r[4].get('stopLossPrice')]
    assert [r[0] for r in closes]==['BTC/USDT','ETH/USDT']
    assert quote.call_count==1 and j.query('SELECT * FROM '+L.TABLE)[0]==proof
    receipts=[json.loads(r['payload']) for r in j.query('SELECT payload FROM trade_accounting_bookings')]
    closes=[r for r in receipts if r['kind']=='close:signal']
    assert len(closes)==2
    for receipt in closes:
        assert receipt['evidence']['strategy_exit']==plan['strategy_exit_decision']
        if receipt['trade_id']!=t['id']:assert receipt['evidence']['reference']['price'] is None


def test_group_eligibility_and_plan_survive_crash_before_any_reduction(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    request=U.request_close;count=len(ex.sent)
    monkeypatch.setattr(U,'request_close',Mock(side_effect=RuntimeError('synthetic crash')))
    with pytest.raises(RuntimeError):L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert len(ex.sent)==count and U.active(j,'g') and j.query('SELECT * FROM '+L.TABLE)
    monkeypatch.setattr(U,'request_close',request)
    for _ in range(4):U.resume(e)
    assert not j.open_trades() and not ex.positions and quote.call_count==1


def test_live_adapter_routes_main_signal_to_whole_group_recovery(setup,monkeypatch):
    from trader.strategy import factory_handoff as F
    from trader.engine.versioned_exits import LiveExitAdapter
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    previous=F.load_version(j,'v');version=dict(previous,spec=spec.to_dict())
    monkeypatch.setattr(F,'load_version',lambda *a:version)
    monkeypatch.setattr(F,'verify_exit_binding',lambda *a:None)
    monkeypatch.setattr(F,'_expected_exit_semantics',lambda v:json.loads(t['entry_identity_json'])['exit_semantics_id'])
    monkeypatch.setattr('trader.engine.coordinated_trials.quote',quote)
    assert LiveExitAdapter(j,e).manage(t,snap) is None
    assert U.active(j,'g') and [r['symbol'] for r in j.open_trades()]==['ETH/USDT']
    for _ in range(3):U.resume(e)
    assert not j.open_trades() and not ex.positions and quote.call_count==1


def test_hedge_cannot_borrow_main_signal_or_main_exit_policy(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    hedge=next(r for r in j.open_trades() if r['symbol']=='ETH/USDT');count=len(ex.sent)
    with pytest.raises(ValueError,match='group_primary_required'):
        L.execute(e,hedge,spec,snap,p,state,quote_reader=quote)
    assert len(ex.sent)==count and quote.call_count==0 and len(j.open_trades())==2


def test_partial_main_signal_keeps_hedge_until_main_is_confirmed_flat(setup,monkeypatch):
    from copy import deepcopy
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    submit=ex.create_order;partial=[True]
    def create(symbol,typ,side,amount,params=None):
        before=deepcopy(ex.positions)
        order=submit(symbol,typ,side,amount,params)
        if symbol=='BTC/USDT' and typ=='market' and (params or {}).get('reduceOnly') and not (params or {}).get('stopLossPrice') and partial[0]:
            order['filled']=amount*.5
            ex.positions=[dict(r,contracts=r['contracts']-amount*.5) if r['symbol']==symbol else r for r in before]
        return order
    monkeypatch.setattr(ex,'create_order',create)
    L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert {r['symbol']:r['amount'] for r in j.open_trades()}['BTC/USDT']==1.
    U.resume(e)
    assert {r['symbol']:r['amount'] for r in j.open_trades()}['BTC/USDT']==.5
    assert any(r['symbol']=='ETH/USDT' for r in ex.positions)
    partial[0]=False
    for _ in range(4):U.resume(e)
    assert not j.open_trades() and not ex.positions and quote.call_count==1
