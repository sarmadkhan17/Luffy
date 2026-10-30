"""Two distinct canonical instruments through real Risk/execution/recovery.

Offline venue and qualification fixtures only. This is neither a private-API
probe nor evidence of a qualified or profitable strategy.
"""
from copy import deepcopy
import json
import time
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_venue import prepared
from tests.test_strategy_hedge_stops import observed
from tests.entry_authority_fixtures import bind
from trader.engine import coordinated_venue as V


def transport(ex,monkeypatch):
    def submit(symbol,typ,side,amount,params=None):
        params=params or {};ex.sent.append((symbol,typ,side,amount,params))
        if 'stopLossPrice' in params or typ=='STOP_MARKET':
            if ex.stop_error:raise RequestTimeout('synthetic stop timeout')
            oid='stop-'+symbol
            ex.stops.append(dict(algoId=oid,symbol=symbol.replace('/',''),side=side,
                reduceOnly=True,orderType='STOP_MARKET',quantity=str(amount),triggerPrice=str(params['stopLossPrice'])))
            return {'id':oid}
        closing=params.get('reduceOnly',False);oid=params['newClientOrderId']
        price=100. if symbol=='BTC/USDT' else 50.
        order=dict(id=oid,status='closed',average=price,filled=amount,clientOrderId=oid,
                   symbol=symbol,side=side,reduceOnly=closing)
        ex.orders[oid]=order
        matching=[p for p in ex.positions if p['symbol']==symbol]
        rest=[p for p in ex.positions if p['symbol']!=symbol]
        if closing:
            remaining=max(0.,matching[0]['contracts']-amount) if matching else 0.
            ex.positions=rest+([dict(matching[0],contracts=remaining)] if remaining else [])
        else:
            assert not matching,'this fixture opens distinct legs; it cannot silently replace a held leg'
            ex.positions=rest+[dict(symbol=symbol,side='long' if side=='buy' else 'short',contracts=amount,
                                   entryPrice=price,markPrice=price)]
        error=ex.close_error if closing else ex.entry_error
        if error:raise error
        return order
    monkeypatch.setattr(ex,'create_order',submit)
    def cancel(oid,symbol):
        ex.stops[:]=[s for s in ex.stops if not (s['algoId']==oid and s['symbol']==symbol.replace('/',''))]
        if oid in ex.orders:ex.orders[oid]['status']='canceled'
        return {}
    monkeypatch.setattr(ex,'cancel_order',cancel)


def snapshot(ex,j):
    from trader.observability.portfolio_observation import observe_positions
    from trader.engine.evidence_capture import position_snapshot,record_snapshot
    from trader.core.types import MarketType
    from trader.data.feed import execution_account_scope
    now=int(time.time()*1000)
    raw=[dict(p,symbol=p['symbol']+':USDT',info={'symbol':p['symbol'].replace('/','')}) for p in ex.positions]
    observed=observe_positions(raw,exchange_id='binanceusdm',market_type=MarketType.FUTURES,
        environment='demo',source_ref='https://demo-fapi.binance.com',request_start_ms=now,response_received_ms=now)
    record_snapshot(j,position_snapshot(observed,raw,account_scope=execution_account_scope(ex)),at_ms=now)


def pair(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    bind(j,ex,e.cfg,'ETH/USDT');transport(ex,monkeypatch)
    primary=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert primary['filled_quantity']==2.
    book.apply_fills({ins.oid:primary['filled_quantity']});snapshot(ex,j)
    emitted=book.step(1,observed(1,p=97.,h=50.,equity=194.))
    assert len(emitted)==1 and emitted[0].leg=='hedge' and emitted[0].reason=='risk_management_activation'
    root=deepcopy(identity);root['coordinated']['leg']='hedge';root['hedge_exit_state']=book.hedge_exit_state
    cap=json.loads(V.A.proposal_binding(j,'ETH/USDT'))
    reference=dict(ref,price=50.,instrument_id=cap['instrument_id'],observed_ms=int(time.time()*1000))
    return ex,j,e,book,emitted[0],root,reference


@pytest.mark.parametrize('lost_response',[False,True])
def test_conditional_opposite_leg_keeps_both_instruments_and_their_protection(setup,monkeypatch,lost_response):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    if lost_response:ex.entry_error=RequestTimeout('synthetic accepted response lost')
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    if lost_response:
        assert result['filled_quantity'] is None and e.recovery_pending()
        assert len(j.open_trades())==1
        assert any(s['symbol']=='BTCUSDT' for s in ex.stops)
        ex.entry_error=None;e.recover_entries();e.recover_entries()
        result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert result['filled_quantity']==pytest.approx(ins.qty),result
    assert not e.recovery_pending()
    assert {(r['symbol'],r['side']) for r in j.open_trades()}=={('BTC/USDT','long'),('ETH/USDT','short')}
    assert {s['symbol'] for s in ex.stops}=={'BTCUSDT','ETHUSDT'}
    assert len(ex.positions)==2
    count=len(ex.sent)
    assert V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)==result
    assert len(ex.sent)==count
    book.apply_fills({ins.oid:result['filled_quantity']})
    assert book.pos['primary']==2. and book.pos['hedge']==pytest.approx(-ins.qty)
    assert len([s for s in ex.sent if s[1]=='market' and not s[4].get('reduceOnly')])==2


@pytest.mark.parametrize('lost_close_response',[False,True])
def test_main_strategy_stop_unwinds_both_actual_legs_without_duplicate_close(setup,monkeypatch,lost_close_response):
    from trader.engine import coordinated_unwind as U
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:result['filled_quantity']})
    emitted=book.step(2,observed(2,p=94.,h=50.,equity=188.))
    assert len(emitted)==2 and all(x.kind=='UNWIND' for x in emitted)
    read=ex.fetch_positions
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(p,symbol=p['symbol']+':USDT',
        info={'symbol':p['symbol'].replace('/','')}) for p in read()])
    root=json.loads(next(r for r in j.open_trades() if json.loads(r['entry_identity_json'])['coordinated']['leg']==emitted[0].leg)['entry_identity_json'])
    if lost_close_response:ex.close_error=RequestTimeout('synthetic accepted close response lost')
    V.dispatch(e,book,emitted[0],identity=root,reference=None,atr=None)
    ex.close_error=None
    for _ in range(5):
        status=U.resume(e)
        if not U.active(j,'g'):break
    assert not U.active(j,'g'),status
    assert not j.open_trades() and not ex.positions and not ex.stops
    closes=[s for s in ex.sent if s[1]=='market' and s[4].get('reduceOnly') and not s[4].get('stopLossPrice')]
    assert len(closes)==2 and {s[0] for s in closes}=={'BTC/USDT','ETH/USDT'}
    count=len(ex.sent)
    assert V.dispatch(e,book,emitted[0],identity=root,reference=None,atr=None)['status']=='CLOSED'
    assert len(ex.sent)==count


def test_missing_second_instrument_permission_preserves_main_and_its_stop(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    before=deepcopy(ex.positions);stops=deepcopy(ex.stops);count=len(ex.sent)
    j.kv_set('entry_capability:ETH/USDT','null')
    with pytest.raises(ValueError):V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert len(ex.sent)==count and ex.positions==before and ex.stops==stops
    assert len(j.open_trades())==1 and j.open_trades()[0]['symbol']=='BTC/USDT'


def test_unprotected_second_leg_rolls_back_without_touching_main_stop(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    primary_stops=deepcopy(ex.stops);ex.stop_error=True
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    for _ in range(4):
        if not e.recovery_pending():break
        e.recover_entries()
    assert not e.recovery_pending()
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert result['filled_quantity']==0.,result
    assert ex.stops==primary_stops
    assert len(j.open_trades())==1 and j.open_trades()[0]['symbol']=='BTC/USDT'
    assert len(ex.positions)==1 and ex.positions[0]['symbol']=='BTC/USDT'
    closes=[s for s in ex.sent if s[1]=='market' and s[4].get('reduceOnly') and not s[4].get('stopLossPrice')]
    assert len(closes)==1 and closes[0][0]=='ETH/USDT'
