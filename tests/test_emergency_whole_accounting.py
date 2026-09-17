"""Synthetic venue receipts only; never observed trading performance."""
from copy import deepcopy
import json
import subprocess

import pytest

from trader.engine import emergency_whole_accounting as EWA
from trader.engine.accounting import digest


def seal(value):
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return value


def intent():
    return dict(schema_version='emergency-accounting-intent.v1', id='intent', symbol='BTC/USDT',
                created_ms=100, flat_verified_ms=4900, code_hash='x', status='fills_pending_funding_unknown',
                position=dict(id='intent', symbol='BTC/USDT', side='long', decision_id='d',
                             market_type='futures', exec_mode='live', amount=2,
                             opened_at='1970-01-01T00:00:00+00:00'),
                entry_observation=dict(id='1', status='closed', filled=2),
                close_observation=dict(id='3', status='closed', filled=1.5),
                close_orders=[dict(id='2', status='canceled', filled=.5),
                             dict(id='3', status='closed', filled=1.5)])


class Venue:
    def __init__(self):
        self.fills = [dict(id=i, orderId=i, symbol='BTCUSDT', side=side, positionSide='BOTH',
            time=ts, qty=qty, price=price, realizedPnl=pnl, commission=1, commissionAsset='USDT')
            for i, side, ts, qty, price, pnl in
            [(1, 'BUY', 1000, 2, 100, 0), (2, 'SELL', 3000, .5, 90, -5), (3, 'SELL', 4000, 1.5, 110, 15)]]
        self.funding = [dict(tranId=42, symbol='BTCUSDT', incomeType='FUNDING_FEE', time=2000, income=-1, asset='USDT')]
        self.events = [dict(symbol='BTCUSDT', fundingTime=ts, fundingRate=.01) for ts in (500, 2000, 4500)]
        self.order_calls = 0

    def fapiPrivateGetOrder(self, p):
        self.order_calls += 1
        f = self.fills[int(p['orderId'])-1]
        return dict(orderId=f['orderId'], symbol=f['symbol'], side=f['side'], positionSide='BOTH',
                    status='FILLED', executedQty=f['qty'], time=f['time'], updateTime=f['time'])

    def fapiPrivateV2GetPositionRisk(self, p):
        return [dict(symbol='BTCUSDT', positionSide='BOTH', positionAmt='0')]

    def fapiPrivateGetUserTrades(self, p):
        return [f for f in self.fills if p['startTime'] <= f['time'] <= p['endTime']]

    def fapiPrivateGetIncome(self, p):
        return [f for f in self.funding if p['startTime'] <= f['time'] <= p['endTime']]

    def fapiPublicGetFundingRate(self, p):
        return [f for f in self.events if p['startTime'] <= f['fundingTime'] <= p['endTime']]


def clean_scan():
    return dict(trades_scanned=0, intents_scanned=0, truncated=False,
                malformed_intent_ids=[], malformed_trade_ids=[], active_recovery_conflict=False)


@pytest.fixture
def artifact(monkeypatch):
    monkeypatch.setattr(EWA.time, 'time', lambda: 5)
    return EWA.capture(intent(), Venue(), overlap_scan=clean_scan())


def reassess(a):
    a['assessment'] = EWA.assess(a)
    return EWA.replay(seal(a))


def test_exact_partial_and_final_close_arithmetic(artifact):
    r = EWA.replay(artifact)
    assert r['status'] == 'derived_complete_as_of_venue_history'
    assert r['gross_realized_usdt'] == 10
    assert r['funding_usdt'] == -1
    assert r['net_trade_pnl_usdt'] == pytest.approx(6.0)  # 10 gross - 3 fees - 1 funding
    assert r['learning_eligible'] is False
    assert r['accounting']['derived'] is True
    assert r['accounting']['schema_version'] == 'emergency-whole-accounting-derived.v1'


def test_signed_funding_both_directions(artifact):
    artifact['funding_history']['pages'][0]['rows'][0]['income'] = 1
    assert reassess(artifact)['net_trade_pnl_usdt'] == pytest.approx(8.0)


def test_zero_funding_requires_complete_event_and_income_history(artifact):
    artifact['funding_history']['pages'][0]['rows'] = []
    assert not reassess(artifact)['learning_eligible']
    artifact['funding_events']['pages'][0]['rows'].pop(1)
    assert reassess(artifact)['funding_usdt'] == 0
    artifact['funding_history']['pages'] = []
    assert reassess(artifact)['funding_usdt'] is None


@pytest.mark.parametrize('mutation', [
    'missing_close', 'mismatched_id', 'qty_mismatch', 'bad_timestamp', 'wrong_side',
    'overlap_trade', 'overlap_intent', 'reopened_venue', 'nonterminal_order', 'duplicate_order_id',
    'scan_truncated', 'scan_malformed_intent', 'scan_active_recovery', 'scan_missing_attestation',
    'flat_verified_after_observation', 'flat_verified_missing', 'entry_exceeds_requested',
    'requested_amount_missing', 'position_id_missing', 'symbol_conflict'])
def test_refusal_paths(artifact, mutation):
    if mutation == 'missing_close':
        artifact['orders'].pop()
        artifact['intent'] = intent()
        artifact['intent']['close_orders'].pop()
        seal(artifact)
    elif mutation == 'mismatched_id':
        artifact['orders'][0]['orderId'] = 99
    elif mutation == 'qty_mismatch':
        artifact['orders'][0]['executedQty'] = 3
    elif mutation == 'bad_timestamp':
        artifact['orders'][0]['time'] = 1
        artifact['orders'][0]['updateTime'] = 1
    elif mutation == 'wrong_side':
        artifact['fill_history']['pages'][0]['rows'][0]['side'] = 'SELL'
    elif mutation == 'overlap_trade':
        artifact['overlapping_trade_ids'] = ['other-trade']
    elif mutation == 'overlap_intent':
        artifact['overlapping_intent_ids'] = ['other-intent']
    elif mutation == 'reopened_venue':
        artifact['position']['positionAmt'] = '1'
    elif mutation == 'nonterminal_order':
        artifact['intent']['close_orders'][0]['status'] = 'open'
        seal(artifact)
    elif mutation == 'duplicate_order_id':
        artifact['intent']['close_orders'][1]['id'] = '2'
        seal(artifact)
    elif mutation == 'scan_truncated':
        artifact['overlap_scan']['truncated'] = True
    elif mutation == 'scan_malformed_intent':
        artifact['overlap_scan']['malformed_intent_ids'] = ['bad-row']
    elif mutation == 'scan_active_recovery':
        artifact['overlap_scan']['active_recovery_conflict'] = True
    elif mutation == 'scan_missing_attestation':
        artifact['overlap_scan'] = {}
    elif mutation == 'flat_verified_after_observation':
        artifact['intent']['flat_verified_ms'] = 999999
        seal(artifact)
    elif mutation == 'flat_verified_missing':
        artifact['intent']['flat_verified_ms'] = None
        seal(artifact)
    elif mutation == 'entry_exceeds_requested':
        artifact['intent']['position']['amount'] = 1  # less than the 2 actually filled
        seal(artifact)
    elif mutation == 'requested_amount_missing':
        artifact['intent']['position']['amount'] = 0
        seal(artifact)
    elif mutation == 'position_id_missing':
        artifact['intent']['position']['id'] = ''
        seal(artifact)
    elif mutation == 'symbol_conflict':
        artifact['intent']['position']['symbol'] = 'ETH/USDT'
        seal(artifact)
    assert not reassess(artifact)['learning_eligible']


def test_rehashed_assessment_cannot_replay(artifact):
    artifact['assessment']['net_trade_pnl_usdt'] = 999
    seal(artifact)
    with pytest.raises(ValueError, match='replay_mismatch'):
        EWA.replay(artifact)


def test_tampered_intent_without_reseal_fails_integrity(artifact):
    artifact['intent']['entry_observation']['filled'] = 999
    with pytest.raises(ValueError, match='integrity'):
        EWA.replay(artifact)


def test_malformed_or_budget_exceeded_intent_rejected_before_network():
    bad = intent()
    bad['entry_observation']['id'] = ''
    venue = Venue()
    artifact = EWA.capture(bad, venue, overlap_scan=clean_scan())
    assert venue.order_calls == 0
    assert 'capture_failed:ValueError' in artifact['errors'][0]
    assert not EWA.replay(artifact)['learning_eligible']

    oversized = intent()
    oversized['close_orders'] = [dict(id=str(i), status='closed', filled=0) for i in range(40)]
    venue2 = Venue()
    artifact2 = EWA.capture(oversized, venue2, overlap_scan=clean_scan())
    assert venue2.order_calls == 0


def test_fetch_error_is_redacted():
    class Offline:
        def fapiPrivateGetOrder(self, p):
            raise RuntimeError('SECRET_TOKEN')
    artifact = EWA.capture(intent(), Offline(), overlap_scan=clean_scan())
    assert 'SECRET_TOKEN' not in json.dumps(artifact)
    assert EWA.replay(artifact)['status'] == 'incomplete'


def test_omitted_overlap_scan_is_never_all_clear():
    """A caller that forgets (or fails) to compute the overlap scan must
    never see this default to "no conflict" — that would fabricate proof
    of a clean journal that was never actually checked."""
    venue = Venue()
    artifact = EWA.capture(intent(), venue)  # overlap_scan omitted entirely
    assert venue.order_calls == 0  # gated before any network call, not just after a wasted round trip
    assert artifact['overlap_scan'] is None  # stored exactly as given, never fabricated
    r = EWA.replay(artifact)
    assert not r['learning_eligible']
    assert r['reasons'] == ['overlap_scan_missing_retry']


@pytest.mark.parametrize('bad_scan', [
    {},
    dict(clean_scan(), trades_scanned=None),
    dict(clean_scan(), trades_scanned=True),            # bool is not int
    dict(clean_scan(), intents_scanned=-1),             # negative count
    dict(clean_scan(), trades_scanned=1.0),             # float, not int
    dict(clean_scan(), truncated=1),                    # non-bool flag
    dict(clean_scan(), active_recovery_conflict='no'),  # non-bool flag
    dict(clean_scan(), malformed_intent_ids='none'),    # non-list
    dict(clean_scan(), malformed_trade_ids=None),       # non-list
    {k: v for k, v in clean_scan().items() if k != 'active_recovery_conflict'},  # missing key
])
def test_malformed_overlap_scan_metadata_refuses_before_network(bad_scan):
    venue = Venue()
    artifact = EWA.capture(intent(), venue, overlap_scan=bad_scan)
    assert venue.order_calls == 0
    assert not EWA.replay(artifact)['learning_eligible']


def test_source_free_cli_replay(artifact, tmp_path):
    path = tmp_path/'receipt.json'
    path.write_text(json.dumps(artifact))
    cmd = ['./venv/bin/python', '-m', 'scripts.capture_emergency_whole_accounting', '--replay', str(path)]
    out = subprocess.run(cmd, check=True, capture_output=True, cwd='/home/sarmad/trader')
    result = json.loads(out.stdout)
    assert result['net_trade_pnl_usdt'] == pytest.approx(6.0)
    assert result['learning_eligible'] is False
