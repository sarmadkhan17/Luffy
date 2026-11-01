"""Synthetic same-symbol cashflows; no claim of financial performance."""
from copy import deepcopy

import pytest

from tests.test_coordinated_accounting import assembled, component
from trader.engine import trade_accounting as T, coordinated_accounting as G
from trader.engine.entry_authority import digest


def seal_native(capture):
    capture['assessment'] = T.assess(capture)
    capture['sha256'] = digest({k:v for k,v in capture.items() if k != 'sha256'})


@pytest.fixture
def native_group(monkeypatch):
    parts = [component(monkeypatch), component(monkeypatch, role='hedge', offset=1200)]
    for p in parts:
        capture = p['capture']; side = 'LONG' if p['identity']['coordinated']['leg'] == 'primary' else 'SHORT'
        capture.update(schema_version=T.HEDGE_LEG_VERSION, position_side=side)
        capture['position']['positionSide'] = side
        for order in capture['orders']:
            order['positionSide'] = side
            if side == 'SHORT':order['orderId'] = str(int(order['orderId'])+10)
        for receipt in capture['bookings']['receipts']:
            if side == 'SHORT':receipt['evidence']['order_id'] = str(int(receipt['evidence']['order_id'])+10)
            receipt['sha256'] = digest({k:v for k,v in receipt.items() if k != 'sha256'})
        source = capture['bookings']; source['sha256'] = digest({k:v for k,v in source.items() if k != 'sha256'})
        for page in capture['fill_history']['pages']:
            for fill in page['rows']:
                fill['positionSide'] = side
                if side == 'SHORT':
                    fill['id'] = str(int(fill['id'])+10); fill['orderId'] = str(int(fill['orderId'])+10)
        root = p['identity']; root['native_position_side'] = side
        root['execution_binding']['position_side'] = side
        p['entry_request']['intent']['strategy_identity'] = deepcopy(root)
        p['entry_request']['intent']['position_side'] = side
        p['entry_request']['intent_hash'] = digest(p['entry_request']['intent'])
        p['entry_request']['order_id'] = '1' if side == 'LONG' else '11'
    fills = [f for p in parts for page in p['capture']['fill_history']['pages'] for f in page['rows']]
    cash = [dict(tranId=i, symbol='BTCUSDT', incomeType='FUNDING_FEE', time=t, income=value, asset='USDT')
            for i,t,value in [(42,2000,-2.), (43,3200,1.), (44,4500,.5)]]
    events = [dict(symbol='BTCUSDT', fundingTime=t, fundingRate=.01) for t in (500,2000,3200,4500,5700)]
    for p in parts:
        c = p['capture']; c['overlapping_trade_ids'] = ['primary-trade', 'hedge-trade']
        for key, rows, clock in [('fill_history', fills, 'time'), ('funding_history', cash, 'time'),
                                 ('funding_events', events, 'fundingTime')]:
            for page in c[key]['pages']:
                page['rows'] = deepcopy([r for r in rows if page['start_ms'] <= r[clock] <= page['end_ms']])
        seal_native(c)
        assert T.replay(c)['leg_verified'], c['assessment']
    result = assembled(parts)
    for member, part in zip(result['closure']['plan']['members'], parts):
        member['position_side'] = part['identity']['native_position_side']
    result['closure']['plan_sha256'] = digest(result['closure']['plan'])
    return result


def test_actual_symbol_funding_is_counted_once_and_outcome_is_combined(native_group):
    for part in native_group['components']:
        result = T.replay(part['capture'])
        assert not result['learning_eligible'] and result['funding_usdt'] is None
    account = G.accounting(native_group)
    assert account['funding_net'] == -.5
    assert len(account['fills']) == 6
    outcome = G.verified_outcome(native_group, 9000)
    assert outcome['actual_execution']['net_pnl'] == -6.5


@pytest.mark.parametrize('change', ['foreign_fill', 'cash_conflict', 'missing_cash', 'unowned_overlap', 'wrong_side'])
def test_ambiguous_native_group_never_becomes_a_financial_outcome(native_group, change):
    c = native_group['components'][0]['capture']
    if change == 'foreign_fill':
        f = deepcopy(c['fill_history']['pages'][0]['rows'][0]); f.update(id='foreign', positionSide='UNKNOWN')
        c['fill_history']['pages'][0]['rows'].append(f)
    elif change == 'cash_conflict':c['funding_history']['pages'][0]['rows'][1]['income'] = 90.
    elif change == 'missing_cash':c['funding_history']['pages'][0]['rows'].pop(1)
    elif change == 'unowned_overlap':c['overlapping_trade_ids'].append('other-owner')
    else:c['position_side'] = 'SHORT'
    seal_native(c)
    with pytest.raises(ValueError):G.accounting(native_group)


def test_combined_valuation_uses_same_actual_funding_once(native_group):
    from tests.test_managed_hedge import policy
    from trader.engine import coordinated_marked as M
    members = native_group['closure']['plan']['members']
    for member, part in zip(members, native_group['components']):
        part['identity']['coordinated'].update(allocated_equity_usdt=1000.,
            management_policy=policy(), maintenance_fraction=.01)
        part['entry_request']['intent']['strategy_identity'] = deepcopy(part['identity'])
        part['entry_request']['intent_hash'] = digest(part['entry_request']['intent'])
        member['entry_identity_sha256'] = digest(part['identity'])
    evidence = dict(schema_version=M.VERSION, group_id='economic-group',
        members=members, components=native_group['components'])
    result = M.assess(evidence, now_ms=8000)
    assert result['marked_net_pnl_usdt'] == -6.5
    assert result['marked_equity_usdt'] == 993.5
    assert not result['learning_eligible']


def test_equal_native_sides_do_not_invent_separate_funding_payments(native_group):
    # At 2500 both native sides hold two contracts. Complete venue income
    # histories have no record then; no per-leg funding amount is inferred.
    for part in native_group['components']:
        c = part['capture']
        for page in c['funding_events']['pages']:
            page['rows'].append(dict(symbol='BTCUSDT', fundingTime=2500, fundingRate=.01))
        seal_native(c)
    assert G.accounting(native_group)['funding_net'] == -.5


def test_standalone_native_position_can_reconcile_its_actual_funding(monkeypatch):
    from tests.test_whole_trade_accounting import Venue, source, seal
    from trader.engine import booking
    s = source(); venue = Venue()
    identity = {'native_position_side':'LONG'}
    s['receipts'][0]['evidence']['execution_binding'] = dict(position_side='LONG', strategy_identity=identity)
    s['receipts'][0]['assessment'] = booking.assess(s['receipts'][0]['evidence'])
    seal(s['receipts'][0]); seal(s)
    for f in venue.fills:f['positionSide'] = 'LONG'
    order = venue.fapiPrivateGetOrder
    venue.fapiPrivateGetOrder = lambda p:dict(order(p), positionSide='LONG')
    venue.fapiPrivateV2GetPositionRisk = lambda p:[dict(symbol='BTCUSDT', positionSide='LONG', positionAmt='0')]
    monkeypatch.setattr(T.time, 'time', lambda:5.)
    capture = T.capture(s, venue, position_side='LONG')
    result = T.replay(capture)
    assert result['learning_eligible'] and result['net_trade_pnl_usdt'] == 25, result
    assert result['funding_usdt'] == -2
    other = deepcopy(capture['fill_history']['pages'][0]['rows'][0])
    other.update(id='unowned', positionSide='SHORT')
    capture['fill_history']['pages'][0]['rows'].append(other)
    seal_native(capture)
    assert not T.replay(capture)['learning_eligible']
    assert T.replay(capture)['reasons'] == ['native_standalone_ownership_overlap_retry']
