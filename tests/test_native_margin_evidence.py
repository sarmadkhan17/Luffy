"""Observed native balances stay asset/account scoped and never become USDT."""
import json

import pytest

from trader.core.journal import Journal
from trader.engine import evidence_capture as E

SCOPE='execution-account.v1:'+'a'*64


def body():
    return dict(canTrade=True,totalMarginBalance='999999',assets=[
        dict(asset='BTC',availableBalance='0.12500000',marginBalance='0.15000000',
             initialMargin='0.02500000',maintMargin='0.00100000',walletBalance='0.14000000'),
        dict(asset='ETH',availableBalance='1.00000000',marginBalance='2.00000000',
             initialMargin='1.00000000',maintMargin='0.01000000',walletBalance='1.50000000')])


def observed(payload=None,**kw):
    args=dict(request_url='https://demo-dapi.binance.com/dapi/v1/account',
        request_start_ms=1000,received_ms=1100,account_scope=SCOPE,asset='BTC')
    args.update(kw)
    return E.margin_observation(json.dumps(body() if payload is None else payload).encode(),**args)


def test_native_margin_uses_exact_named_asset_not_global_dollar_total():
    r=observed()
    assert E.verify_margin(r)==r and r['status']=='AVAILABLE'
    assert r['venue']=='binancecoinm' and r['asset']=='BTC' and r['environment']=='demo'
    assert r['value']==.125 and r['value_text']=='0.12500000'
    assert r['equity_field']=='marginBalance' and r['equity_value_text']=='0.15000000'
    assert r['initial_margin_text']=='0.02500000' and r['can_trade'] is True
    assert observed(asset='ETH')['value']==1.


@pytest.mark.parametrize('case,reason',[
    ('missing_asset','NATIVE_SETTLEMENT_ASSET_REQUIRED'),
    ('absent_asset','NATIVE_ASSET_NOT_PRESENT'),('duplicate','NATIVE_ASSET_ROW_AMBIGUOUS'),
    ('scope','NATIVE_ACCOUNT_SCOPE_REQUIRED'),('url','ACCOUNT_REQUEST_URL_UNRECOGNISED'),
    ('clock','ACCOUNT_RESPONSE_TIME_INVALID'),('available','AVAILABLE_BALANCE_MALFORMED'),
    ('equity','NATIVE_EQUITY_MALFORMED')])
def test_unknown_native_margin_is_not_imputed_or_converted(case,reason):
    p=body();args={}
    if case=='missing_asset':args['asset']=None
    elif case=='absent_asset':args['asset']='BNB'
    elif case=='duplicate':p['assets'].append(dict(p['assets'][0]))
    elif case=='scope':args['account_scope']=None
    elif case=='url':args['request_url']='https://untrusted.invalid/dapi/v1/account'
    elif case=='clock':args['received_ms']=999
    elif case=='available':p['assets'][0]['availableBalance']='NaN'
    elif case=='equity':p['assets'][0]['marginBalance']=None
    r=observed(p,**args)
    assert r['status']=='UNAVAILABLE' and r['value'] is None and r['reason']==reason


def test_native_balance_truth_does_not_infer_account_trade_permission():
    p=body();p['canTrade']=False
    r=observed(p)
    assert r['status']=='AVAILABLE' and r['can_trade'] is False
    p['canTrade']='true'
    assert observed(p)['can_trade'] is None


def test_native_margin_storage_cannot_overwrite_usdm_or_another_coin(tmp_path):
    j=Journal(tmp_path/'journal.db')
    usd=E.margin_observation(b'{"availableBalance":"12","totalMarginBalance":"20"}',
        request_url='https://demo-fapi.binance.com/fapi/v3/account',request_start_ms=1000,received_ms=1100)
    E.record_margin(j,usd)
    btc,eth=observed(),observed(asset='ETH')
    E.record_margin(j,btc);E.record_margin(j,eth)
    assert json.loads(j.kv_get(E.MARGIN_KV))==usd
    root=E.MARGIN_KV+':binancecoinm:'+SCOPE+':'
    assert json.loads(j.kv_get(root+'BTC'))==btc
    assert json.loads(j.kv_get(root+'ETH'))==eth
    bad=dict(btc,value=999.)
    with pytest.raises(ValueError,match='identity'):E.record_margin(j,bad)


def test_usdm_cannot_satisfy_a_native_asset_request():
    r=observed(request_url='https://demo-fapi.binance.com/fapi/v3/account')
    assert r['status']=='UNAVAILABLE' and r['reason']=='SETTLEMENT_ASSET_UNSUPPORTED'
