"""Synthetic evidence only; Binance order status cannot prove cashflow attribution."""
from copy import deepcopy
import json
import subprocess

import pytest

from trader.engine import conversion_evidence as C, trade_accounting as A
from tests.test_whole_trade_accounting import artifact, reassess, seal


class Venue:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def fapiPrivateGetConvertOrderStatus(self, params):
        self.calls.append(params)
        return self.response


def setup(artifact, kind='commission', native='-0.01', currency='BNB'):
    if kind == 'commission':
        row = artifact['fill_history']['pages'][0]['rows'][0]
        row.update(commission=str(-C.amount(native)), commissionAsset=currency)
        identity = str(row['id'])
    else:
        row = artifact['funding_history']['pages'][0]['rows'][0]
        row.update(income=native, asset=currency)
        identity = str(row['tranId'])
    reassess(artifact)
    positive = C.amount(native) > 0
    response = dict(orderId=99, orderStatus='SUCCESS', fromAsset=currency if positive else 'USDT',
                    toAsset='USDT' if positive else currency,
                    fromAmount=str(abs(C.amount(native))) if positive else '3.01',
                    toAmount='3.01' if positive else str(abs(C.amount(native))),
                    ratio='1', inverseRatio='1', createTime=4500)
    return [dict(kind=kind, cashflow_id=identity, order_id='99')], Venue(response)


@pytest.mark.parametrize('kind,native,currency', [
    ('commission','-0.01','BNB'), ('commission','0.01','BNB'),
    ('funding','-0.01','BNB'), ('funding','0.01','BTC')])
def test_actual_order_observed_but_never_inferred_attribution(artifact,kind,native,currency):
    items, ex = setup(artifact,kind,native,currency)
    original = deepcopy(artifact)
    a = C.capture(artifact,ex,items)
    r = C.replay(json.loads(json.dumps(a)))
    assert ex.calls == [{'orderId':'99'}]
    assert r['cashflows'][0]['transaction_observed']
    assert r['cashflows'][0]['attributed_usdt'] is None
    assert r['cashflows'][0]['cashflow']['signed_native_amount'] == native
    assert 'conversion_cashflow_attribution_unproven_retry' in r['cashflows'][0]['reasons']
    assert not r['learning_eligible'] and r['retry_required'] and r['net_trade_pnl_usdt'] is None
    assert artifact == original and A.replay(artifact)['net_trade_pnl_usdt'] is None
    with pytest.raises(ValueError,match='incomplete_accounting_retry'):
        A.verified_outcome(artifact,6000)


@pytest.mark.parametrize('field,value', [
    ('orderId',100),('orderStatus','PROCESS'),('fromAsset','BTC'),('toAsset','USDT'),
    ('fromAmount','NaN'),('fromAmount',None),('fromAmount',True),('fromAmount','0'),
    ('toAmount','0.010000000000000001'),('createTime',5001),('createTime',True)])
def test_invalid_conversion_cannot_be_observed_or_accepted(artifact,field,value):
    items, ex = setup(artifact)
    ex.response[field] = value
    r = C.replay(C.capture(artifact,ex,items))
    assert not r['cashflows'][0]['transaction_observed']
    assert not r['learning_eligible']


def test_bounded_unique_requests_before_network(artifact):
    items, ex = setup(artifact)
    for requests in ([],items*2,items*9,[dict(items[0],cashflow_id='absent')],
                     [dict(items[0],order_id='secret/key')], [dict(items[0],order_id='099')]):
        with pytest.raises(ValueError): C.capture(artifact,ex,requests)
    assert ex.calls == []


def test_missing_history_is_not_a_manual_override(artifact):
    items, ex = setup(artifact)
    artifact['fill_history']['pages'] = []
    reassess(artifact)
    with pytest.raises(ValueError,match='coverage_gap'):
        C.capture(artifact,ex,items)
    assert not ex.calls


def test_error_redaction_and_allowlisted_raw_fields(artifact):
    items, ex = setup(artifact)
    ex.response['secret'] = 'TOKEN'
    a = C.capture(artifact,ex,items)
    assert 'TOKEN' not in json.dumps(a)
    def fail(params): raise RuntimeError('PRIVATE TOKEN')
    ex.fapiPrivateGetConvertOrderStatus = fail
    a = C.capture(artifact,ex,items)
    assert 'PRIVATE' not in json.dumps(a)
    assert a['observations'][0]['error_type'] == 'RuntimeError'
    assert not C.replay(a)['cashflows'][0]['transaction_observed']


def test_rehash_cannot_promote_or_change_frozen_cashflow(artifact):
    items, ex = setup(artifact)
    a = C.capture(artifact,ex,items)
    a['assessment']['learning_eligible'] = True
    with pytest.raises(ValueError,match='replay_mismatch'): C.replay(seal(a))
    a = C.capture(artifact,ex,items)
    a['whole_trade_capture']['assessment']['net_trade_pnl_usdt'] = 123
    with pytest.raises(ValueError): C.replay(seal(a))


def test_multiple_currencies_stay_separate_and_duplicate_order_rejected(artifact):
    items, ex = setup(artifact)
    more, _ = setup(artifact,'funding','0.001','BTC')
    with pytest.raises(ValueError,match='reused_order'):
        C.capture(artifact,ex,items+more)
    more[0]['order_id']='100'
    r = C.replay(C.capture(artifact,ex,items+more))
    assert {f['cashflow']['currency'] for f in r['cashflows']} == {'BNB','BTC'}
    assert not r['learning_eligible']


def test_source_free_cli_replay(artifact,tmp_path):
    items, ex = setup(artifact)
    path=tmp_path/'conversion.json'
    path.write_text(json.dumps(C.capture(artifact,ex,items)))
    r=subprocess.run(['./venv/bin/python','-m','scripts.capture_trade_conversions',
                      '--replay',str(path)],check=True,capture_output=True,text=True)
    assert json.loads(r.stdout)['learning_eligible'] is False
    r=subprocess.run(['./venv/bin/python','-m','scripts.capture_trade_conversions',
                      '--whole-trade',str(path),'--request','commission:1:99',
                      '--output',str(path)],capture_output=True,text=True)
    assert r.returncode != 0 and 'output exists' in r.stderr


def test_native_decimal_source_is_not_rounded(artifact):
    items, ex = setup(artifact)
    exact = '0.123456789012345678901234567890123456'
    artifact['fill_history']['pages'][0]['rows'][0]['commission'] = exact
    reassess(artifact)
    ex.response['toAmount'] = exact
    r = C.replay(C.capture(artifact,ex,items))
    assert r['cashflows'][0]['cashflow']['signed_native_amount'] == '-' + exact
    assert r['cashflows'][0]['transaction_observed']
