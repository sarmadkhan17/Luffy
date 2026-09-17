"""Synthetic algo-order/actual-order/fill mapping only; no exchange access."""
import json
import subprocess

import pytest

from trader.engine import native_exit_provenance as NEP
from trader.engine.accounting import digest


@pytest.mark.parametrize('value', [0, '0', -1, '-1', True, False, None, '', 'abc', 1.5, '1.5', [1]])
def test_canonical_id_rejects_non_positive_non_integer_values(value):
    with pytest.raises(ValueError, match='id_field_invalid'):
        NEP.canonical_id(value)


@pytest.mark.parametrize('value,expected', [(1, '1'), ('1', '1'), (5019283746, '5019283746'), (' 42 ', '42')])
def test_canonical_id_accepts_positive_integers_and_digit_strings(value, expected):
    assert NEP.canonical_id(value) == expected


@pytest.mark.parametrize('value', [None, 0, '0', '', '  '])
def test_has_actual_order_id_is_false_for_not_triggered_markers(value):
    assert NEP.has_actual_order_id(dict(actualOrderId=value)) is False


@pytest.mark.parametrize('value', [5019283746, '5019283746', -1, True])
def test_has_actual_order_id_is_true_for_any_present_value_malformed_or_not(value):
    # Presence alone is not validity — canonical_id() still refuses -1/True.
    assert NEP.has_actual_order_id(dict(actualOrderId=value)) is True


ALGO_ID = 3938041141
ACTUAL_ORDER_ID = 5019283746


def reference():
    return dict(id='trade1', symbol='BTC/USDT', side='long', sl_order_id=str(ALGO_ID),
                market_type='futures', exec_mode='live', decision_id='d', amount=1,
                entry_price=100, exit_price=None, realized_pnl=None, status='open',
                strategy_id='s', opened_at='1970-01-01T00:00:01+00:00', closed_at=None)


class Venue:
    def __init__(self):
        self.algo = dict(algoId=ALGO_ID, algoType='CONDITIONAL', orderType='STOP_MARKET',
                         symbol='BTCUSDT', side='SELL', positionSide='BOTH', quantity=1,
                         reduceOnly=True, algoStatus='FINISHED', actualOrderId=ACTUAL_ORDER_ID, actualQty=1,
                         actualType='STOP_MARKET', createTime=1200, triggerTime=1700, updateTime=2100)
        self.order = dict(orderId=ACTUAL_ORDER_ID, symbol='BTCUSDT', side='SELL', positionSide='BOTH',
                          status='FILLED', executedQty=1, reduceOnly=True, time=1800, updateTime=2000)
        self.trades = [dict(id='f1', order=str(ACTUAL_ORDER_ID), symbol='BTC/USDT:USDT', timestamp=1900,
                            side='sell', amount=1, price=100,
                            info={'realizedPnl': 5, 'commission': .1, 'commissionAsset': 'USDT'})]
        self.calls = []

    def fapiPrivateGetAlgoOrder(self, p):
        self.calls.append('algo')
        assert p['algoId'] == str(ALGO_ID)
        return dict(self.algo)

    def fapiPrivateGetOrder(self, p):
        self.calls.append('order')
        return dict(self.order)

    def fetch_my_trades(self, symbol, since, limit):
        self.calls.append('fills')
        return self.trades


@pytest.fixture
def artifact(monkeypatch):
    monkeypatch.setattr(NEP.time, 'time', lambda: 3)
    return NEP.capture(reference(), Venue())


def reassess(a):
    a['assessment'] = NEP.assess(a)
    a['sha256'] = digest({k: v for k, v in a.items() if k != 'sha256'})
    return NEP.replay(a)


def test_native_executed_mapping_is_a_valid_leg(artifact):
    r = NEP.replay(artifact)
    assert r['leg_verified'] is True
    assert r['status'] == 'leg_verified_whole_economics_unknown'
    assert r['whole_economics'] == 'unknown'
    assert len(r['fills']) == 1
    assert r['fills'][0]['order'] == str(ACTUAL_ORDER_ID)


@pytest.mark.parametrize('mutation', [
    'new_status', 'no_actual_id', 'wrong_symbol', 'wrong_side', 'wrong_order_id',
    'wrong_quantity', 'not_reduce_only', 'bad_clocks', 'nononeway_algo', 'nonterminal_order',
    'wrong_fill_symbol', 'wrong_fill_side', 'partial_actual_qty', 'order_not_reduce_only',
    'unsupported_order_type', 'not_conditional', 'journal_quantity_mismatch',
    'clock_inversion_algo_update_before_order_update', 'nonpositive_fill_price',
    'actual_order_id_zero_int', 'actual_order_id_zero_string', 'actual_order_id_negative',
    'actual_order_id_bool', 'algo_id_bool', 'order_id_non_numeric', 'algo_id_none'])
def test_refusal_paths(artifact, mutation):
    if mutation == 'new_status':
        artifact['algo_order']['algoStatus'] = 'NEW'
    elif mutation == 'no_actual_id':
        artifact['algo_order']['actualOrderId'] = None
    elif mutation == 'wrong_symbol':
        artifact['order']['symbol'] = 'ETHUSDT'
    elif mutation == 'wrong_side':
        artifact['order']['side'] = 'BUY'
    elif mutation == 'wrong_order_id':
        artifact['order']['orderId'] = 'other'
    elif mutation == 'wrong_quantity':
        artifact['order']['executedQty'] = 2
    elif mutation == 'not_reduce_only':
        artifact['algo_order']['reduceOnly'] = False
    elif mutation == 'bad_clocks':
        artifact['order']['updateTime'] = 100
    elif mutation == 'nononeway_algo':
        artifact['algo_order']['positionSide'] = 'LONG'
    elif mutation == 'nonterminal_order':
        artifact['order']['status'] = 'NEW'
    elif mutation == 'wrong_fill_symbol':
        artifact['fills'][0]['symbol'] = 'ETH/USDT'
    elif mutation == 'wrong_fill_side':
        artifact['fills'][0]['side'] = 'buy'
    elif mutation == 'partial_actual_qty':
        artifact['algo_order']['actualQty'] = 0.5
    elif mutation == 'order_not_reduce_only':
        artifact['order']['reduceOnly'] = False
    elif mutation == 'unsupported_order_type':
        artifact['algo_order']['orderType'] = 'LIMIT'
    elif mutation == 'not_conditional':
        artifact['algo_order']['algoType'] = 'OTHER'
    elif mutation == 'journal_quantity_mismatch':
        artifact['reference']['amount'] = 2
    elif mutation == 'clock_inversion_algo_update_before_order_update':
        artifact['algo_order']['updateTime'] = 1900  # before order.updateTime=2000
    elif mutation == 'nonpositive_fill_price':
        artifact['fills'][0]['price'] = 0
    elif mutation == 'actual_order_id_zero_int':
        artifact['algo_order']['actualOrderId'] = 0
    elif mutation == 'actual_order_id_zero_string':
        artifact['algo_order']['actualOrderId'] = '0'
    elif mutation == 'actual_order_id_negative':
        artifact['algo_order']['actualOrderId'] = -5019283746
    elif mutation == 'actual_order_id_bool':
        artifact['algo_order']['actualOrderId'] = True
    elif mutation == 'algo_id_bool':
        artifact['algo_order']['algoId'] = True
    elif mutation == 'order_id_non_numeric':
        artifact['order']['orderId'] = 'abc123'
    elif mutation == 'algo_id_none':
        artifact['algo_order']['algoId'] = None
    assert reassess(artifact)['leg_verified'] is False


def test_reference_revalidated_even_after_reseal(artifact):
    artifact['reference']['amount'] = -1
    assert reassess(artifact)['leg_verified'] is False
    artifact['reference']['amount'] = 1
    artifact['reference']['exec_mode'] = 'paper'
    assert reassess(artifact)['leg_verified'] is False


def test_no_actual_order_id_stops_before_actual_order_lookup():
    class NoBridge:
        def fapiPrivateGetAlgoOrder(self, p):
            return dict(algoId=ALGO_ID, algoType='CONDITIONAL', orderType='STOP_MARKET',
                       symbol='BTCUSDT', side='SELL', positionSide='BOTH',
                       quantity=1, reduceOnly=True, algoStatus='NEW', actualOrderId=None)
        def fapiPrivateGetOrder(self, p):
            raise AssertionError('must not look up an actual order without actualOrderId')
        def fetch_my_trades(self, *a, **kw):
            raise AssertionError('must not fetch fills without a verified actual order')
    artifact = NEP.capture(reference(), NoBridge())
    assert artifact['errors'] == []
    assert artifact['order'] is None
    r = NEP.replay(artifact)
    assert not r['leg_verified']
    assert r['reasons'] == ['algo_not_triggered_retry']


def test_fetch_error_is_redacted():
    class Offline:
        def fapiPrivateGetAlgoOrder(self, p):
            raise RuntimeError('SECRET_TOKEN')
    artifact = NEP.capture(reference(), Offline())
    assert 'SECRET_TOKEN' not in json.dumps(artifact)
    assert NEP.replay(artifact)['status'] == 'incomplete'


def test_rehashed_assessment_cannot_replay(artifact):
    artifact['assessment']['leg_verified'] = True
    artifact['assessment']['status'] = 'tampered'
    artifact['sha256'] = digest({k: v for k, v in artifact.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='replay_mismatch'):
        NEP.replay(artifact)


def test_tampered_reference_without_reseal_fails_integrity(artifact):
    artifact['reference']['sl_order_id'] = 'other-algo'
    with pytest.raises(ValueError, match='integrity'):
        NEP.replay(artifact)


def test_malformed_opened_at_yields_fixed_code_never_raw_text(artifact):
    artifact['reference']['opened_at'] = 'not-an-iso-timestamp-\x00-SECRET'
    r = reassess(artifact)
    assert r['leg_verified'] is False
    assert r['reasons'] == ['reference_clock_invalid']
    assert 'SECRET' not in json.dumps(r)


def test_reference_snapshot_requires_sl_order_id():
    row = dict(reference())
    row.pop('sl_order_id')
    with pytest.raises(ValueError, match='missing_protective_algo_id_reference'):
        NEP.reference_snapshot(row)


def test_reference_snapshot_rejects_malformed_sl_order_id():
    row = dict(reference())
    row['sl_order_id'] = 'not-numeric'
    with pytest.raises(ValueError, match='id_field_invalid'):
        NEP.reference_snapshot(row)


def test_source_free_cli_replay(artifact, tmp_path):
    path = tmp_path/'receipt.json'
    path.write_text(json.dumps(artifact))
    cmd = ['./venv/bin/python', '-m', 'scripts.capture_native_exit_provenance', '--replay', str(path)]
    out = subprocess.run(cmd, check=True, capture_output=True, cwd='/home/sarmad/trader')
    result = json.loads(out.stdout)
    assert result['leg_verified'] is True
