"""Synthetic private history recorded when the isolated venue accepts orders.

It supplies explicit fees, complete empty funding history and current marks;
the production accounting replay and budget verification remain unmocked.
"""
from copy import deepcopy
import time


def attach(ex,monkeypatch):
    if not hasattr(ex,'cashflow_fills'):ex.cashflow_fills=[];ex.cashflow_orders={}
    previous_funding_ms=int(time.time()*1000)-1000
    send=ex.create_order
    def submit(symbol,typ,side,amount,params=None):
        params=params or {};before=deepcopy(ex.positions)
        try:return send(symbol,typ,side,amount,params)
        finally:
            client=params.get('newClientOrderId');order=ex.orders.get(client)
            if (client and order and not params.get('stopLossPrice') and typ=='market'
                    and not any(f['clientOrderId']==client for f in ex.cashflow_fills)):
                at=int(time.time()*1000);oid=order['id'];qty=float(order['filled']);price=float(order['average'])
                old=next((p for p in before if p['symbol']==symbol),None)
                pnl=((price-old['entryPrice'])*qty*(1 if old['side']=='long' else -1)
                     if old and params.get('reduceOnly') else 0.)
                fill=dict(id=len(ex.cashflow_fills)+1,orderId=oid,clientOrderId=client,symbol=symbol.replace('/',''),
                    side=side.upper(),positionSide='BOTH',time=at,qty=qty,price=price,realizedPnl=pnl,
                    commission=qty*price*.0004,commissionAsset='USDT')
                ex.cashflow_fills.append(fill)
                ex.cashflow_orders[(fill['symbol'],str(oid))]=dict(orderId=oid,symbol=fill['symbol'],side=side.upper(),
                    positionSide='BOTH',status='FILLED',executedQty=qty,time=at,updateTime=at)
    monkeypatch.setattr(ex,'create_order',submit)
    def position(p):
        held=next((r for r in ex.positions if r['symbol'].replace('/','')==p['symbol']),None)
        if not held:return [dict(symbol=p['symbol'],positionSide='BOTH',positionAmt=0.,entryPrice=0.,markPrice=0.,unRealizedProfit=0.)]
        sign=1 if held['side']=='long' else -1
        return [dict(symbol=p['symbol'],positionSide='BOTH',positionAmt=sign*held['contracts'],
            entryPrice=held['entryPrice'],markPrice=held['markPrice'],
            unRealizedProfit=sign*held['contracts']*(held['markPrice']-held['entryPrice']))]
    monkeypatch.setattr(ex,'fapiPrivateGetOrder',lambda p:ex.cashflow_orders[(p['symbol'],str(p['orderId']))],raising=False)
    monkeypatch.setattr(ex,'fapiPrivateV2GetPositionRisk',position,raising=False)
    monkeypatch.setattr(ex,'fapiPrivateGetUserTrades',lambda p:[f for f in ex.cashflow_fills
        if f['symbol']==p['symbol'] and p['startTime']<=f['time']<=p['endTime']],raising=False)
    monkeypatch.setattr(ex,'fapiPrivateGetIncome',lambda p:[],raising=False)
    monkeypatch.setattr(ex,'fapiPublicGetFundingRate',lambda p:[dict(symbol=p['symbol'],
        fundingTime=previous_funding_ms,fundingRate=0.)] if p['startTime']<=previous_funding_ms<=p['endTime'] else [],raising=False)
