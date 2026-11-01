"""Offline venue with separate native sides and actual per-order test receipts."""
import time
from copy import deepcopy


def attach(ex, monkeypatch):
    monkeypatch.setitem(ex.urls['api'], 'fapiPrivate', 'https://demo-fapi.binance.com/fapi/v1')
    ex.native_price = 100.
    ex.native_fills = []; ex.native_orders = {}
    old_event = int(time.time()*1000)-1000
    def submit(symbol, kind, side, amount, params=None):
        params = params or {}; native = params['positionSide']
        assert native in ('LONG', 'SHORT') and 'reduceOnly' not in params
        ex.sent.append((symbol, kind, side, amount, deepcopy(params)))
        if 'stopLossPrice' in params or kind == 'STOP_MARKET':
            oid = str(900000+len(ex.sent))
            ex.stops.append(dict(algoId=oid, symbol=symbol.replace('/', ''), side=side,
                positionSide=native, reduceOnly=False, orderType='STOP_MARKET', quantity=str(amount),
                triggerPrice=str(params.get('stopLossPrice', params.get('stopPrice')))))
            return dict(id=oid)
        closing = side != ('buy' if native == 'LONG' else 'sell')
        old = next((p for p in ex.positions if p['info']['positionSide'] == native), None)
        if closing:assert old is not None and amount <= old['contracts']
        remaining = old['contracts']-amount if closing else (old['contracts'] if old else 0.)+amount
        entry = old['entryPrice'] if old else ex.native_price
        rest = [p for p in ex.positions if p['info']['positionSide'] != native]
        sign = 1 if native == 'LONG' else -1
        if remaining:
            rest.append(dict(symbol=symbol+':USDT', side=native.lower(), contracts=remaining,
                hedged=True, entryPrice=entry, markPrice=ex.native_price,
                info=dict(symbol=symbol.replace('/', ''), positionSide=native,
                    positionAmt=str(sign*remaining), entryPrice=str(entry), markPrice=str(ex.native_price))))
        ex.positions = rest
        cid = params.get('newClientOrderId', 'panic_'+str(len(ex.sent))); at = int(time.time()*1000)
        order = dict(id=cid, clientOrderId=cid, symbol=symbol, side=side, status='closed',
            average=ex.native_price, filled=amount, reduceOnly=False, positionSide=native)
        ex.orders[cid] = order
        raw = dict(id=len(ex.native_fills)+1, orderId=cid, symbol=symbol.replace('/', ''),
            side=side.upper(), positionSide=native, time=at, qty=amount, price=ex.native_price,
            realizedPnl=sign*amount*(ex.native_price-entry) if closing else 0.,
            commission=amount*ex.native_price*.0004, commissionAsset='USDT')
        ex.native_fills.append(raw)
        ex.native_orders[cid] = dict(orderId=cid, symbol=raw['symbol'], side=raw['side'],
            positionSide=native, status='FILLED', executedQty=amount, time=at, updateTime=at)
        error = ex.close_error if closing else ex.entry_error
        if error:raise error
        return order
    monkeypatch.setattr(ex, 'create_order', submit)
    monkeypatch.setattr(ex, 'fapiPrivateGetPositionSideDual', lambda:{'dualSidePosition':True}, raising=False)
    monkeypatch.setattr(ex, 'fapiPrivateGetOrder', lambda p:ex.native_orders[str(p['orderId'])], raising=False)
    def positions(params):
        out = []
        for native in ('LONG', 'SHORT'):
            p = next((p for p in ex.positions if p['info']['positionSide'] == native), None)
            sign = 1 if native == 'LONG' else -1
            out.append(dict(symbol=params['symbol'], positionSide=native,
                positionAmt=sign*p['contracts'] if p else 0., entryPrice=p['entryPrice'] if p else 0.,
                markPrice=ex.native_price, unRealizedProfit=sign*p['contracts']*(ex.native_price-p['entryPrice']) if p else 0.))
        return out
    monkeypatch.setattr(ex, 'fapiPrivateV2GetPositionRisk', positions, raising=False)
    monkeypatch.setattr(ex, 'fapiPrivateGetUserTrades', lambda p:[f for f in ex.native_fills
        if p['startTime'] <= f['time'] <= p['endTime']], raising=False)
    monkeypatch.setattr(ex, 'fapiPrivateGetIncome', lambda p:[], raising=False)
    monkeypatch.setattr(ex, 'fapiPublicGetFundingRate', lambda p:[dict(symbol=p['symbol'],
        fundingTime=old_event, fundingRate=0.)] if p['startTime'] <= old_event <= p['endTime'] else [], raising=False)
    def trades(symbol, since=None, limit=None, params=None):
        return [dict(id=str(f['id']), order=f['orderId'], symbol=symbol,
            timestamp=f['time'], side=f['side'].lower(), amount=f['qty'], price=f['price'], info=f)
            for f in ex.native_fills if f['time'] >= (since or 0)]
    monkeypatch.setattr(ex, 'fetch_my_trades', trades, raising=False)
    def cancel(params):
        ex.stops[:] = [s for s in ex.stops if str(s['algoId']) != str(params['algoId'])]
        return {}
    monkeypatch.setattr(ex, 'fapiPrivateDeleteAlgoOrder', cancel, raising=False)


def refresh(ex, journal):
    from trader.observability.portfolio_observation import observe_positions
    from trader.engine.evidence_capture import position_snapshot, record_snapshot
    from trader.data.feed import execution_account_scope
    from trader.core.types import MarketType
    now = int(time.time()*1000)
    for p in ex.positions:
        p['markPrice'] = ex.native_price; p['info']['markPrice'] = str(ex.native_price)
    obs = observe_positions(ex.positions, exchange_id='binanceusdm', market_type=MarketType.FUTURES,
        environment='demo', source_ref='https://demo-fapi.binance.com', request_start_ms=now, response_received_ms=now)
    record_snapshot(journal, position_snapshot(obs, ex.positions, account_scope=execution_account_scope(ex)), at_ms=now)
