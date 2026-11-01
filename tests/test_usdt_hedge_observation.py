"""A same-instrument hedge retains two native sides and separate evidence."""
from copy import deepcopy

import pytest

from trader.core.types import MarketType
from trader.observability import portfolio_observation as P
from trader.engine import evidence_capture as E
from trader.engine import position_mode as M


def row(side='LONG', quantity=2.):
    return dict(symbol='BTC/USDT:USDT', contracts=quantity,
                side=side.lower() if quantity else None, hedged=True,
                info=dict(symbol='BTCUSDT', positionSide=side,
                          positionAmt=str(quantity if side == 'LONG' else -quantity),
                          entryPrice='100' if side == 'LONG' else '102',
                          markPrice='101'))


def observe(rows):
    return P.observe_positions(rows, exchange_id='binanceusdm', market_type=MarketType.FUTURES,
        environment='demo', source_ref='https://demo-fapi.binance.com',
        request_start_ms=900, response_received_ms=1000)


def test_both_native_sides_survive_observation_and_price_projection():
    rows = [row('LONG', 2.), row('SHORT', 1.)]
    obs = observe(rows)
    assert obs.schema == P.HEDGE_SCHEMA and len(obs.positions) == 2
    iid = obs.positions[0].instrument_id
    assert obs.get(iid, 'LONG').absolute_quantity == 2.
    assert obs.get(iid, 'SHORT').absolute_quantity == 1.
    with pytest.raises(ValueError, match='side required'):
        obs.get(iid)
    snap = E.position_snapshot(obs, rows, account_scope='account')
    assert E.verify_snapshot(snap) == snap
    parts = {p['position_side']:p for p in snap['positions']}
    assert parts['LONG']['entry_price_text'] == '100'
    assert parts['SHORT']['entry_price_text'] == '102'
    assert parts['SHORT']['quantity'] == 1.
    changed = deepcopy(rows); changed[1]['info']['positionAmt'] = '-3'
    with pytest.raises(ValueError):
        E.position_snapshot(obs, changed, account_scope='account')


def test_one_way_receipt_shape_and_identity_are_unchanged():
    r = row(); r['hedged'] = False; r['info']['positionSide'] = 'BOTH'
    obs = observe([r])
    assert obs.schema == P.SCHEMA
    assert len(P.record_payload(obs.positions[0])) == 4
    snap = E.position_snapshot(obs, [r])
    assert snap['schema'] == E.SNAPSHOT_SCHEMA
    assert 'position_side' not in snap['positions'][0]
    assert snap['positions'][0]['entry_price_text'] == '100'


@pytest.mark.parametrize('edit', ['duplicate', 'mixed', 'unsigned', 'side', 'hedged'])
def test_ambiguous_or_contradictory_native_positions_refuse(edit):
    rows = [row()]
    if edit == 'duplicate':
        rows.append(row())
    elif edit == 'mixed':
        r = row(); r['hedged'] = False; r['info']['positionSide'] = 'BOTH'; rows.append(r)
    elif edit == 'unsigned':
        rows = [row('SHORT')]; rows[0]['info']['positionAmt'] = '2'
    elif edit == 'side':
        rows[0]['side'] = 'short'
    else:
        rows[0]['hedged'] = False
    with pytest.raises(ValueError):
        observe(rows)


def test_native_close_parameters_name_held_side_and_do_not_change_account_mode():
    root = dict(native_position_side='LONG')
    assert M.order_params(root, reduce_only=True, client_id='close') == dict(positionSide='LONG', newClientOrderId='close')
    assert M.order_params({}, reduce_only=True) == {'reduceOnly': True}
    with pytest.raises(ValueError, match='binding_differs'):
        M.position_side(dict(native_position_side='LONG', execution_binding={'position_side':'SHORT'}))


def test_portfolio_projection_keeps_gross_exposure_of_both_sides():
    from trader.portfolio import common_factor as C
    from trader.portfolio.allocator import Source
    rows = [row('LONG', 2.), row('SHORT', 1.)]
    snap = E.position_snapshot(observe(rows), rows, account_scope='account')
    book = C.snapshot(Source.freeze('native', snap), 1000)
    assert [(p['direction'], p['entry_basis_notional']) for p in book['positions']] == [
        ('LONG', '200.0'), ('SHORT', '102.0')]
    changed = deepcopy(snap)
    changed['positions'][1]['position_side'] = 'LONG'
    changed['snapshot_id'] = M.digest({k:v for k,v in changed.items() if k != 'snapshot_id'})
    with pytest.raises(ValueError, match='PROJECTION'):
        C.snapshot(Source.freeze('native', changed), 1000)


def test_group_venue_read_does_not_overwrite_one_leg_with_the_other(monkeypatch):
    from types import SimpleNamespace
    from trader.engine import coordinated_unwind as U
    obs = observe([row('LONG', 2.), row('SHORT', 1.)])
    monkeypatch.setattr(U, '_position_observation', lambda _:obs)
    assert U._positions(SimpleNamespace()) == {
        ('binance_usdm:futures:BTCUSDT', 'LONG'):2.,
        ('binance_usdm:futures:BTCUSDT', 'SHORT'):1.}


def test_position_mode_receipt_cannot_be_reused_for_another_account_or_stale_clock():
    body = dict(schema=M.MODE_SCHEMA, account_scope='account', environment='demo',
        source='https://demo-fapi.binance.com/fapi/v1/positionSide/dual',
        request_start_ms=900, observed_ms=1000, hedged=True)
    receipt = dict(body, receipt_id=M.digest(body))
    assert M.verify(receipt, 'account', hedged=True, now_ms=1000) == receipt
    for scope, hedged, now in [('other', True, 1000), ('account', False, 1000),
                               ('account', True, 31_000)]:
        with pytest.raises(ValueError, match='receipt_unverified'):
            M.verify(receipt, scope, hedged=hedged, now_ms=now)


def test_order_receipt_match_requires_the_exact_native_side():
    from trader.engine.recovery import exact_order_match
    order = dict(id='exit', clientOrderId='owned', symbol='BTC/USDT', side='sell',
                 info={'positionSide':'LONG'}, reduceOnly=False)
    args = dict(client_order_id='owned', symbol='BTC/USDT', side='sell', reduce_only=True)
    assert exact_order_match(order, **args, position_side='LONG')
    assert not exact_order_match(order, **args, position_side='SHORT')
    assert not exact_order_match(order, **args)


def test_stop_for_other_native_side_never_proves_protection():
    from trader.engine.protective import protection_match
    from types import SimpleNamespace
    venue = SimpleNamespace(price_to_precision=lambda symbol, price:str(price))
    stop = dict(id='stop', symbol='BTCUSDT', side='sell', amount=2., stop_price=95.,
                order_type='STOP_MARKET', position_side='LONG', reduce_only=False)
    assert protection_match(venue, 'BTC/USDT', 'long', 2., 95., stop, native_side='LONG').matches
    assert not protection_match(venue, 'BTC/USDT', 'long', 2., 95., stop).matches
    stop['position_side'] = 'SHORT'
    assert not protection_match(venue, 'BTC/USDT', 'long', 2., 95., stop, native_side='LONG').matches


def protection_observation():
    import json
    from trader.engine import protection_snapshot as S
    rows = [dict(row('LONG', 2.), entryPrice=100., updateTime=1790000000000),
            dict(row('SHORT', 1.), entryPrice=102., updateTime=1790000000000)]
    trades = [dict(id=native, symbol='BTC/USDT', side=native.lower(), amount=qty,
        stop_loss=stop, sl_order_id=native, market_type='futures',
        entry_identity_json=json.dumps({'native_position_side':native}))
        for native, qty, stop in [('LONG', 2., 95.), ('SHORT', 1., 105.)]]
    facts = dict(trades=trades, kv={S.REARM_KEY:'{}', 'control_state':'FROZEN'}, control_event_id=0)
    stops = [dict(algoId=native, symbol='BTCUSDT', side=side, positionSide=native,
        reduceOnly=False, quantity=str(qty), triggerPrice=str(stop), orderType='STOP_MARKET')
        for native, side, qty, stop in [('LONG', 'SELL', 2., 95.), ('SHORT', 'BUY', 1., 105.)]]
    return dict(started_wall=1790000000., positions_before=rows, positions_after=deepcopy(rows),
        journal_before=facts, journal_after=deepcopy(facts), algo=stops, algo_after=deepcopy(stops),
        requests=[], window_ms=1, supervisor_busy_after=False, ordinary={}, ordinary_unread=[])


def test_protection_monitor_verifies_each_native_side_separately():
    from trader.engine import protection_snapshot as S
    from trader.engine.venue_reads import TickPrecision
    obs = protection_observation()
    result = S.evaluate(obs, TickPrecision({'BTCUSDT':'0.1'}), boot=1, seq=1)
    assert result['status'] == 'VERIFIED', result['reasons']
    assert result['position_count'] == 2
    assert {(r['position_side'], r['stop_id']) for r in result['symbols']} == {
        ('LONG', 'LONG'), ('SHORT', 'SHORT')}
    obs['algo'] = obs['algo'][:1]; obs['algo_after'] = deepcopy(obs['algo'])
    result = S.evaluate(obs, TickPrecision({'BTCUSDT':'0.1'}), boot=1, seq=2)
    assert result['status'] == 'PARTIAL'
    assert [r['verified'] for r in result['symbols']] == [True, False]


def test_protection_monitor_detects_stop_switching_native_sides_between_reads():
    from trader.engine import protection_snapshot as S
    from trader.engine.venue_reads import TickPrecision
    obs = protection_observation()
    obs['algo_after'][0]['positionSide'] = 'SHORT'
    result = S.evaluate(obs, TickPrecision({'BTCUSDT':'0.1'}), boot=1, seq=1)
    assert result['status'] == 'PARTIAL'
    assert 'observation_inconsistent:stop_listing_changed' in result['reasons']


def native_owned_fixture(setup, monkeypatch):
    """Real Risk/entry/recovery receipt with an offline native-side venue."""
    import time
    from trader.data.feed import execution_account_scope
    ex, journal, executor, decision = setup
    executor.fill_retry_s = 0
    create = ex.create_order
    def entry(symbol, type_, side, amount, params=None):
        order = create(symbol, type_, side, amount, params)
        order['positionSide'] = params['positionSide']
        for p in ex.positions:
            p.update(symbol='BTC/USDT:USDT', hedged=True)
            p['info'] = dict(symbol='BTCUSDT', positionSide='LONG', positionAmt=str(p['contracts']))
        for stop in ex.stops:
            stop.update(positionSide='LONG', reduceOnly=False)
        return order
    monkeypatch.setattr(ex, 'create_order', entry)
    now = int(time.time() * 1000)
    mode = dict(schema=M.MODE_SCHEMA, account_scope=execution_account_scope(ex), environment='demo',
        source='https://demo-fapi.binance.com/fapi/v1/positionSide/dual',
        request_start_ms=now, observed_ms=now, hedged=True)
    mode['receipt_id'] = M.digest(mode)
    identity = dict(coordinated={'group_id':'group-fixture', 'leg':'primary'},
                    native_position_side='LONG', position_mode=mode)
    reference = dict(price=100.)
    permission = executor.risk_manager.authorize_entry(decision, 2., 2., 95., 110.,
        'strategy', identity=identity, reference=reference)
    position = executor.open(decision, 2., 2., 95., 110., 'strategy', 'strategy',
        entry_identity=identity, reference=reference, risk_permission=permission)
    assert position is not None, decision.skip_reason
    ex.positions = [dict(row('LONG', 2.), entryPrice=100., markPrice=101.),
                    dict(row('SHORT', 1.), entryPrice=102., markPrice=101.)]
    orders = []
    def submit(symbol, type_, side, amount, params=None):
        assert type_ == 'market' and side == 'sell' and params['positionSide'] == 'LONG'
        assert 'reduceOnly' not in params
        orders.append(dict(params))
        ex.positions = [p for p in ex.positions if p['info']['positionSide'] != 'LONG']
        order = dict(id='native-exit', clientOrderId=params['newClientOrderId'],
            symbol=symbol, side=side, type=type_, filled=amount, average=100.,
            status='closed', reduceOnly=False, info={'positionSide':'LONG'})
        ex.orders[order['id']] = order; ex.orders[order['clientOrderId']] = order
        return order
    monkeypatch.setattr(ex, 'create_order', submit)
    return ex, journal, executor, position.id, orders


# Reuse the established Risk/Executor fixture but never enable a new version.
pytest_plugins = ('tests.test_entry_recovery',)


def test_native_close_leaves_opposite_side_intact_and_books_once(setup, monkeypatch):
    from trader.engine import coordinated_close as C
    ex, journal, executor, tid, sent = native_owned_fixture(setup, monkeypatch)
    result = C.close_once(executor, tid, 'group-fixture', 'native-owned-close')
    assert result['status'] == 'BOOKED', result
    assert len(sent) == 1
    assert [p['info']['positionSide'] for p in ex.positions] == ['SHORT']
    assert journal.query('SELECT status FROM trades WHERE id=?', (tid,))[0]['status'] == 'closed'
    assert C.close_once(executor, tid, 'group-fixture', 'native-owned-close')['status'] == 'BOOKED'
    assert len(sent) == 1


def test_native_close_refuses_quantity_larger_than_current_held_side(setup, monkeypatch):
    from trader.engine import coordinated_close as C
    ex, journal, executor, tid, sent = native_owned_fixture(setup, monkeypatch)
    ex.positions[0] = dict(row('LONG', 1.), entryPrice=100., markPrice=101.)
    result = C.close_once(executor, tid, 'group-fixture', 'native-too-large')
    assert result['status'] == 'RESERVED', result
    assert not sent
    assert journal.open_trades()[0]['amount'] == 2.


def test_global_reconciliation_never_confuses_an_untracked_opposite_side(setup, monkeypatch):
    from trader.engine.reconcile import reconcile_futures
    ex, journal, executor, tid, sent = native_owned_fixture(setup, monkeypatch)
    result = reconcile_futures(ex, journal, verify=True)
    assert result['positions_readable'] and result['position_count'] == 2
    assert result['safety_issues'] == ['untracked_native_position:binance_usdm:futures:BTCUSDT:SHORT']
    assert journal.open_trades()[0]['amount'] == 2. and not sent
    ex.positions = ex.positions[:1]
    result = reconcile_futures(ex, journal, verify=True)
    assert not result['safety_issues'], result
    assert not sent


def test_native_protective_exit_requires_matching_algo_order_and_fill_sides(monkeypatch):
    import json
    from tests.test_native_exit_provenance import Venue, reference
    from trader.engine import native_exit_provenance as N
    monkeypatch.setattr(N.time, 'time', lambda:3.)
    venue = Venue(); trade = reference()
    trade['entry_identity_json'] = json.dumps({'native_position_side':'LONG'})
    venue.algo.update(positionSide='LONG', reduceOnly=False)
    venue.order.update(positionSide='LONG', reduceOnly=False)
    venue.trades[0]['info']['positionSide'] = 'LONG'
    result = N.replay(N.capture(N.reference_snapshot(trade), venue))
    assert result['leg_verified'], result
    venue.trades[0]['info']['positionSide'] = 'SHORT'
    result = N.replay(N.capture(N.reference_snapshot(trade), venue))
    assert not result['leg_verified']
    assert result['reasons'] == ['native_exit_fill_side_differs']


def test_native_exit_reference_keeps_identity_hash_without_copying_large_context():
    import json
    from tests.test_native_exit_provenance import reference
    from trader.engine import native_exit_provenance as N
    identity = dict(native_position_side='LONG', diagnostic_context='x'*100_000)
    trade = dict(reference(), entry_identity_json=json.dumps(identity))
    result = N.reference_snapshot(trade)
    assert result['native_position_side'] == 'LONG'
    assert result['entry_identity_sha256'] == M.digest(identity)
    assert len(json.dumps(result)) < 2000 and 'entry_identity_json' not in result
