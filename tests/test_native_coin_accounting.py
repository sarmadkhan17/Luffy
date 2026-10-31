"""Synthetic inverse-contract receipts, distinct from USD-M learning truth."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from tests.test_whole_trade_accounting import source, seal
from trader.engine import trade_accounting as T
from trader.core.contract_values import Contract
from trader.data.feed import execution_account_scope
from trader.observability.portfolio_observation import trading_source


def sources(*,opened=False):
    s=source()
    def change(row):
        if row is None:return
        row.update(symbol='BTC/USD:BTC',entry_price=10000.)
        if row.get('exit_price') is not None:row['exit_price']=12000.
    change(s['trade'])
    for receipt in s['receipts']:
        change(receipt['before']);change(receipt['after']);seal(receipt)
    if opened:
        s['receipts']=s['receipts'][:2];s['trade']=deepcopy(s['receipts'][-1]['after'])
    seal(s)
    return s


class Venue:
    id='binancecoinm'
    apiKey='TEST_ONLY_NOT_A_CREDENTIAL'
    urls={'api':{'dapiPrivate':'https://demo-dapi.binance.com/dapi/v1',
                 'dapiPublic':'https://demo-dapi.binance.com/dapi/v1',
                 'dapiPrivateV2':'https://demo-dapi.binance.com/dapi/v2'}}
    def __init__(self,opened=False):
        self.sent=[];self.opened=opened;c=Contract('inverse',100.)
        self.fills=[dict(id=i,orderId=i,symbol='BTCUSD_PERP',side=side,positionSide='BOTH',time=ts,
            qty=qty,price=price,realizedPnl=pnl,commission=.00001,commissionAsset='BTC')
            for i,side,ts,qty,price,pnl in [(1,'BUY',1000,2,10000.,0.),
                (2,'SELL',3000,1,11000.,c.pnl_native(1.,10000.,11000.)),
                (3,'SELL',4000,1,12000.,c.pnl_native(1.,10000.,12000.))]]
        if opened:self.fills=self.fills[:2]
        self.events=[dict(symbol='BTCUSD_PERP',fundingTime=t,fundingRate=.01)
                     for t in ((500,2000) if opened else (500,2000,4500))]
        self.funding=[dict(tranId=42,symbol='BTCUSD_PERP',incomeType='FUNDING_FEE',time=2000,
                           income=-.00002,asset='BTC')]
    def market(self,symbol):
        assert symbol=='BTC/USD:BTC'
        return dict(symbol=symbol,id='BTCUSD_PERP',base='BTC',quote='USD',settle='BTC',
                    contract=True,inverse=True,linear=False,contractSize=100.,swap=True)
    def dapiPrivateGetOrder(self,p):
        f=self.fills[int(p['orderId'])-1]
        return dict(orderId=f['orderId'],symbol=f['symbol'],side=f['side'],positionSide='BOTH',
                    status='FILLED',executedQty=f['qty'],time=f['time'],updateTime=f['time'])
    def dapiPrivateGetPositionRisk(self,p):
        assert p==dict(pair='BTCUSD')
        q=1. if self.opened else 0.;mark=10500.
        return [dict(symbol='BTCUSD_PERP',positionSide='BOTH',positionAmt=q,
            entryPrice=10000. if q else 0.,markPrice=mark,
            unRealizedProfit=Contract('inverse',100.).pnl_native(q,10000.,mark))]
    def dapiPrivateGetUserTrades(self,p):
        return [f for f in self.fills if p['startTime']<=f['time']<=p['endTime']]
    def dapiPrivateGetIncome(self,p):
        return [f for f in self.funding if p['startTime']<=f['time']<=p['endTime']]
    def dapiPublicGetFundingRate(self,p):
        return [f for f in self.events if p['startTime']<=f['fundingTime']<=p['endTime']]


@pytest.mark.parametrize('opened',[False,True])
def test_native_fills_and_signed_funding_do_not_become_usdt_or_learning(monkeypatch,opened):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    a=T.capture(sources(opened=opened),Venue(opened),marked=opened);r=T.replay(a)
    assert r['native_complete'],r
    c=Contract('inverse',100.)
    gross=c.pnl_native(1.,10000.,11000.)
    gross+=c.pnl_native(1.,10000.,10500. if opened else 12000.)
    assert r['net_pnl_native']==pytest.approx(gross-(2 if opened else 3)*.00001-.00002)
    assert r['native_currency']=='BTC' and r['funding_native']==-.00002
    assert not r['learning_eligible'] and r['accounting'] is None
    assert r['net_trade_pnl_usdt'] is None and r['funding_usdt'] is None
    with pytest.raises(ValueError,match='incomplete_accounting'):
        T.verified_outcome(a,6000)


@pytest.mark.parametrize('case',['currency','quantity','mark','contract_size','missing_funding','side'])
def test_unknown_native_economics_never_claims_completion(monkeypatch,case):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    a=T.capture(sources(opened=True),Venue(True),marked=True)
    if case=='currency':a['funding_history']['pages'][0]['rows'][0]['asset']='USDT'
    elif case=='quantity':a['position']['positionAmt']=.5
    elif case=='mark':a['position']['unRealizedProfit']=1.
    elif case=='contract_size':a['contract_binding']['market']['contractSize']=10.
    elif case=='missing_funding':a['funding_history']['pages'][0]['rows']=[]
    elif case=='side':a['position']['positionSide']='LONG'
    r=T.assess(a,marked=True)
    assert not r.get('native_complete',False) and not r['learning_eligible']


def test_account_scope_separates_venues_with_same_credential_and_refuses_mixed_endpoints():
    coin=Venue();usd=SimpleNamespace(id='binanceusdm',apiKey=coin.apiKey,
        urls={'api':{'fapiPrivateV3':'https://demo-fapi.binance.com/fapi/v3'}})
    assert execution_account_scope(coin)!=execution_account_scope(usd)
    assert trading_source(coin)==('demo','https://demo-dapi.binance.com')
    mixed=deepcopy(coin);mixed.urls=deepcopy(coin.urls)
    mixed.urls['api']['dapiPublic']='https://dapi.binance.com/dapi/v1'
    assert execution_account_scope(mixed) is None
    with pytest.raises(ValueError):trading_source(mixed)


def test_uniform_foreign_cashflows_cannot_replace_contract_settlement(monkeypatch):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    venue=Venue()
    for fill in venue.fills:fill['commissionAsset']='USDT'
    venue.funding[0]['asset']='USDT'
    r=T.replay(T.capture(sources(),venue))
    assert not r['native_complete']
    assert r['native_currency']=='BTC'
    assert 'currency_conversion_missing_retry' in r['reasons']


@pytest.mark.parametrize('marked',[False,True])
def test_closed_native_interval_uses_owned_flat_receipt_not_a_later_position(monkeypatch,marked):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    venue=Venue()
    venue.fills.append(dict(venue.fills[0],id=4,orderId=4,time=4600,qty=7,commission=100.))
    venue.funding.append(dict(venue.funding[0],tranId=43,time=4700,income=-100.))
    def unexpected_position(_):raise AssertionError('must not inspect a later position')
    venue.dapiPrivateGetPositionRisk=unexpected_position
    from trader.core.instrument_registry import InstrumentId
    from trader.core.types import MarketType
    proof=dict(schema='coordinated-terminal-flat.v1',trade_id='trade',logical_id='close-owned',
        symbol='BTC/USD:BTC',order_id='3',flat=True,quantity=0.,
        account_scope=execution_account_scope(venue),
        instrument_id=InstrumentId('binancecoinm',MarketType.FUTURES,'BTCUSD_PERP').value,
        request_start_ms=4000,observed_ms=4050)
    a=T.capture(sources(),venue,marked=marked,terminal_flat=proof)
    r=T.replay(a)
    assert r['native_complete'],r
    assert a['end_ms']==4000 and a['observed_ms']==5000
    expected=Contract('inverse',100.).pnl_native(1.,10000.,11000.)
    expected+=Contract('inverse',100.).pnl_native(1.,10000.,12000.)
    assert r['net_pnl_native']==pytest.approx(expected-.00003-.00002)
    assert not r['learning_eligible']
    if marked:assert r['marked_notional_native']==0.
    for field in ('account_scope','instrument_id'):
        changed=deepcopy(a);changed['terminal_flat'][field]='wrong'
        refused=T.assess(changed,marked=marked)
        assert not refused['native_complete']
        assert 'native_closed_interval_scope_differs' in refused['reasons']
