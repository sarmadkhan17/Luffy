"""Native fill facts cannot become legacy USDT profit or complete learning."""
from copy import deepcopy

import pytest

from trader.engine import booking as B


def evidence():
    iid='binancecoinm:futures:BTCUSD_PERP'
    fill=dict(id='fill',order='order',symbol='BTC/USD:BTC',venue_symbol='BTCUSD_PERP',
        side='sell',timestamp=2000,amount=2.,price=11000.,realized_pnl=.001,
        commission=.00001,commission_asset='BTC')
    return dict(basis='venue_order_fills',order_id='order',symbol='BTC/USD:BTC',
        side='sell',quantity=2.,since_ms=1000,observed_ms=3000,fills=[fill],
        execution_binding=dict(instrument_id=iid,capability=dict(record=dict(
            instrument_id=dict(venue='binancecoinm',market_type='futures',venue_symbol='BTCUSD_PERP'),
            base_asset='BTC',quote_asset='USD',settlement_asset='BTC',contract_multiplier='100'))))


def test_native_fee_subtotal_needs_declared_currency_and_keeps_usdt_unknown():
    e=evidence();r=B.assess(e)
    assert r['status']=='verified_leg_fills_only' and r['native_currency']=='BTC'
    assert r['leg_fill_net_excluding_funding_native']==pytest.approx(.00099)
    assert r['leg_fill_net_excluding_funding_usdt'] is None
    assert not r['learning_eligible'] and r['net_economic_pnl_usdt'] is None
    assert r['reasons']==['funding_unattributed']
    assert B.monetary_total(e['fills']) is None
    assert B.monetary_total(e['fills'],currency='BTC')==pytest.approx(.00099)


@pytest.mark.parametrize('case',['asset','symbol','venue_symbol','identity','multiplier','settlement','quote','quantity','missing_binding'])
def test_native_booking_refuses_wrong_units_or_attribution(case):
    e=evidence();record=e['execution_binding']['capability']['record']
    if case=='asset':e['fills'][0]['commission_asset']='USDT'
    elif case=='symbol':e['fills'][0]['symbol']='BTC/USDT'
    elif case=='venue_symbol':e['fills'][0]['venue_symbol']='BTCUSD_261225'
    elif case=='identity':e['execution_binding']['instrument_id']='other'
    elif case=='multiplier':record['contract_multiplier']='0'
    elif case=='settlement':record['settlement_asset']='ETH'
    elif case=='quote':record['quote_asset']='USDT'
    elif case=='quantity':e['quantity']=1.
    elif case=='missing_binding':e.pop('execution_binding')
    r=B.assess(e)
    assert r['status']=='unverified' and not r['learning_eligible']
    assert r['leg_fill_net_excluding_funding_usdt'] is None


def test_replayed_native_booking_retains_exact_currency_without_authorizing_trade():
    e=evidence()
    receipt=dict(schema_version='trade-booking.v1',trade_id='trade',kind='close:signal',
        before=None,after=dict(id='trade'),observed_ms=3000,evidence=e,assessment=B.assess(e))
    receipt['sha256']=B.digest(receipt)
    assert B.replay(receipt)==receipt['assessment']
    changed=deepcopy(receipt);changed['assessment']['leg_fill_net_excluding_funding_usdt']=.00099
    changed['sha256']=B.digest({k:v for k,v in changed.items() if k!='sha256'})
    with pytest.raises(ValueError,match='booking_assessment_mismatch'):B.replay(changed)


@pytest.mark.parametrize('binding',['corrupt',{'capability':'corrupt'},
    {'capability':{'record':['corrupt']}},{'capability':{'record':{'instrument_id':['corrupt']}}}])
def test_malformed_binding_stays_unverified_instead_of_breaking_booking(binding):
    e=evidence();e['execution_binding']=binding
    r=B.assess(e)
    assert r['status']=='unverified' and not r['learning_eligible']
