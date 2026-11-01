"""COIN-M conditional stops stay on DAPI and preserve instrument identity."""
import pytest

from trader.engine import protective as P
from tests.test_protective_stops import FakeEx,_algo


class Coin(FakeEx):
    id='binancecoinm'
    def __init__(self,**kw):
        super().__init__(**kw);self.calls=[];self.delete_error=None
    def fapiPrivateGetOpenAlgoOrders(self):raise AssertionError('wrong venue API')
    def fapiPrivateDeleteAlgoOrder(self,params):raise AssertionError('wrong venue API')
    def dapiPrivateGetOpenAlgoOrders(self):
        self.calls.append('list-native');return {'orders':self.algo}
    def dapiPrivateDeleteAlgoOrder(self,params):
        self.calls.append(('delete-native',params['algoId']))
        if self.delete_error:raise self.delete_error
        self.algo=[o for o in self.algo if str(o['algoId'])!=str(params['algoId'])]
        return dict(algoId=params['algoId'])


@pytest.mark.parametrize('unified,raw',[
    ('BTC/USD:BTC','BTCUSD_PERP'),('ETH/USD:ETH','ETHUSD_PERP'),
    ('BTC/USD:BTC-261225','BTCUSD_261225'),('btc/usd:btc','BTCUSD_PERP'),
])
def test_contract_aliases_match_exact_perpetual_or_delivery(unified,raw):
    assert P.venue_key(unified)==P.venue_key(raw)
    assert P.venue_key('BTC/USD:BTC')!=P.venue_key('BTC/USD:BTC-261225')
    assert P.venue_key('BTC/USD:BTC')!=P.venue_key('BTC/USDT:USDT')
    assert P.venue_key('BTC/USD')!=P.venue_key('BTCUSD_PERP')


def test_native_list_filter_and_cancel_never_use_inherited_usdm_endpoints():
    ex=Coin(algo=[_algo(1,'BTCUSD_PERP'),_algo(2,'BTCUSD_261225')])
    snapshot=P.open_stops(ex,'BTC/USD:BTC',strict=True)
    assert snapshot.complete and [o['id'] for o in snapshot]==['1']
    assert P.cancel_stop(ex,'1','BTC/USD:BTC')
    assert [o['algoId'] for o in ex.algo]==[2] and not ex.cancelled_plain
    assert ex.calls==['list-native',('delete-native',1)]


def test_missing_native_algo_listing_cannot_be_replaced_by_an_empty_ordinary_list():
    ex=Coin();ex.dapiPrivateGetOpenAlgoOrders=None
    with pytest.raises(P.ProtectiveSnapshotIncomplete):P.open_stops(ex,'BTC/USD:BTC',strict=True)
    assert not P.open_stops(ex,'BTC/USD:BTC').complete
    ex.dapiPrivateDeleteAlgoOrder=None
    assert not P.cancel_stop(ex,'1','BTC/USD:BTC') and not ex.cancelled_plain


def test_unknown_native_algo_delete_does_not_cancel_same_numbered_ordinary_order():
    ex=Coin();ex.delete_error=TimeoutError('synthetic unknown response')
    assert not P.cancel_stop(ex,'1','BTC/USD:BTC')
    assert not ex.cancelled_plain
    ex.delete_error=RuntimeError('-2011 Unknown order')
    assert P.cancel_stop(ex,'1','BTC/USD:BTC')
    assert not ex.cancelled_plain


def test_known_ordinary_stop_does_not_cancel_same_numbered_algo():
    ex=Coin(algo=[_algo(1,'BTCUSD_PERP')])
    assert P.cancel_stop(ex,'1','BTC/USD:BTC',kind='order')
    assert ex.calls==[] and ex.algo[0]['algoId']==1
    assert ex.cancelled_plain==[('1','BTC/USD:BTC')]


def test_unknown_owned_stop_is_retained_during_native_orphan_sweep():
    ex=Coin(algo=[_algo(1,'BTCUSD_PERP'),_algo(2,'BTCUSD_261225')])
    assert P.sweep_orphans(ex,set(),{'BTC/USD:BTC'},strict=True)==(1,0)
    assert [o['algoId'] for o in ex.algo]==[1]


def test_installed_ccxt_places_coin_stop_on_native_conditional_endpoint_without_network():
    import ccxt
    ex=ccxt.binancecoinm()
    ex.set_markets([dict(id='BTCUSD_PERP',symbol='BTC/USD:BTC',base='BTC',quote='USD',settle='BTC',
        baseId='BTC',quoteId='USD',settleId='BTC',type='swap',spot=False,margin=False,swap=True,
        future=False,option=False,contract=True,linear=False,inverse=True,contractSize=100.,
        active=True,precision=dict(amount=1.,price=.1),limits=dict(amount=dict(min=1.)),
        info=dict(orderTypes=['MARKET','STOP_MARKET']))])
    sent=[]
    def request(path,api,method,params,**kw):
        sent.append((path,api,method,params))
        order=dict(algoId=123,symbol='BTCUSD_PERP',algoType='CONDITIONAL',orderType='STOP_MARKET',
                    side='SELL',quantity='2',triggerPrice='49000',algoStatus='NEW',reduceOnly=True)
        if path=='openAlgoOrders':return [order]
        if path=='openOrders':return []
        return order
    ex.request=request
    assert P.place_stop(ex,'BTC/USD:BTC','sell',2.,49000.)=='123'
    assert len(sent)==1
    path,api,method,params=sent[0]
    assert (path,api,method)==('algoOrder','dapiPrivate','POST')
    assert params['reduceOnly'] is True and params['symbol']=='BTCUSD_PERP'
    assert params['quantity']=='2' and params['type']=='STOP_MARKET'
    assert params['triggerPrice']=='49000'
    listed=P.open_stops(ex,'BTC/USD:BTC',strict=True)
    assert listed.complete and len(listed)==1 and listed[0]['id']=='123'
    assert P.protection_match(ex,'BTC/USD:BTC','long',2.,49000.,listed[0]).matches
    assert P.cancel_stop(ex,'123','BTC/USD:BTC')
    assert [(path,method) for path,api,method,params in sent]==[
        ('algoOrder','POST'),('openAlgoOrders','GET'),('openOrders','GET'),('algoOrder','DELETE')]
    assert all(api=='dapiPrivate' for path,api,method,params in sent)
