"""Complete native-side dispatch under synthetic version qualification only."""
from copy import deepcopy
import time
import pytest
pytest_plugins = ('tests.test_entry_recovery',)
from tests.test_coordinated_venue import prepared
from tests.test_strategy_hedge_stops import observed
from tests.native_hedge_venue import attach, refresh
from trader.research.compose import hedge as H
from trader.engine import coordinated_venue as V, coordinated_unwind as U


def primary(setup, monkeypatch):
    ex, j, executor, book, ins, identity, ref = prepared(setup, monkeypatch,
        account_mode='hedge', hedge_symbol='BTC/USDT')
    attach(ex, monkeypatch)
    # One economic position is permitted; a second independent position is not.
    executor.risk_manager.max_positions = 1
    executor.cfg['risk']['max_open_positions'] = 1
    executor.policy_id = executor.risk_manager.policy()['digest']
    result = V.dispatch(executor, book, ins, identity=identity, reference=ref, atr=2.)
    assert result['filled_quantity'] == 2., result
    book.apply_fills({ins.oid:result['filled_quantity']})
    return ex, j, executor, book, identity, ref


def hedge_request(setup, monkeypatch):
    ex, j, executor, book, identity, ref = primary(setup, monkeypatch)
    ex.native_price = 96.; refresh(ex, j)
    equity = 200.+2.*(96.-100.)-sum(f['commission'] for f in ex.native_fills)
    instructions = book.step(1, observed(1, p=96., h=96., equity=equity))
    assert len(instructions) == 1 and instructions[0].leg == 'hedge'
    root = deepcopy(identity); root['coordinated']['leg'] = 'hedge'; root['hedge_exit_state'] = book.hedge_exit_state
    ref = dict(ref, price=96., observed_ms=int(time.time()*1000))
    return ex, j, executor, book, identity, root, ref, instructions[0]


def both(setup, monkeypatch):
    ex, j, executor, book, identity, root, ref, instruction = hedge_request(setup, monkeypatch)
    result = V.dispatch(executor, book, instruction, identity=root, reference=ref, atr=2.)
    assert result['status'] == 'TERMINAL' and result['filled_quantity'] > 0, result
    book.apply_fills({instruction.oid:result['filled_quantity']})
    return ex, j, executor, book, identity, ref


def test_primary_then_conditional_opposite_side_share_one_economic_limit(setup, monkeypatch):
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    assert len(j.open_trades()) == len(ex.positions) == 2
    assert {p['info']['positionSide'] for p in ex.positions} == {'LONG', 'SHORT'}
    assert len(ex.stops) == 2
    assert all('reduceOnly' not in sent[4] for sent in ex.sent)


def test_primary_exit_closes_both_native_sides_once_and_keeps_opposite_protection_until_due(setup, monkeypatch):
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    result = U.close_group_once(executor, 'g', 'native-whole-close')
    assert result['status'] == 'UNWINDING' and result['leg'] == 'primary', result
    assert [p['info']['positionSide'] for p in ex.positions] == ['SHORT']
    assert len(ex.stops) == 1 and ex.stops[0]['positionSide'] == 'SHORT'
    assert U.resume(executor)[0]['leg'] == 'hedge'
    assert U.resume(executor) == [dict(group_id='g', status='CLOSED')]
    count = len(ex.sent)
    assert U.close_group_once(executor, 'g', 'native-whole-close')['status'] == 'CLOSED'
    assert len(ex.sent) == count and not ex.positions and not j.open_trades()


def test_wrong_account_mode_blocks_before_any_native_order(setup, monkeypatch):
    ex, j, executor, book, ins, identity, ref = prepared(setup, monkeypatch,
        account_mode='hedge', hedge_symbol='BTC/USDT')
    attach(ex, monkeypatch)
    monkeypatch.setattr(ex, 'fapiPrivateGetPositionSideDual', lambda:{'dualSidePosition':False})
    with pytest.raises(ValueError, match='coordinated_hedge_account_mode_required'):
        V.dispatch(executor, book, ins, identity=identity, reference=ref, atr=2.)
    assert not ex.sent


def test_lost_native_hedge_response_recovers_without_resubmitting_or_overwriting_primary(setup, monkeypatch):
    from ccxt import RequestTimeout
    ex, j, executor, book, identity, root, ref, ins = hedge_request(setup, monkeypatch)
    ex.entry_error = RequestTimeout('synthetic accepted then response lost')
    first = V.dispatch(executor, book, ins, identity=root, reference=ref, atr=2.)
    assert first['filled_quantity'] is None and executor.recovery_pending()
    count = len([s for s in ex.sent if s[1] == 'market' and 'stopLossPrice' not in s[4]])
    assert V.dispatch(executor, book, ins, identity=root, reference=ref, atr=2.) == first
    ex.entry_error = None
    executor.recover_entries(); executor.recover_entries()
    result = V.dispatch(executor, book, ins, identity=root, reference=ref, atr=2.)
    assert result['status'] == 'TERMINAL' and result['filled_quantity'] > 0, (result, (executor.recovery.pending() or {}).get('reason'))
    assert len([s for s in ex.sent if s[1] == 'market' and 'stopLossPrice' not in s[4]]) == count
    assert len(j.open_trades()) == len(ex.positions) == len(ex.stops) == 2


def test_independent_opposite_entry_cannot_borrow_existing_groups_exception(setup, monkeypatch):
    ex, j, executor, book, identity, ref = primary(setup, monkeypatch)
    refresh(ex, j)
    other = H.HedgeBook(book.spec, primary_exit_definition=book.primary_exit_definition)
    row = observed(0, signal=-1, p=100., h=100., equity=200.)
    ins = other.step(0, row)[0]
    root = deepcopy(identity); root['coordinated']['group_id'] = 'unrelated'
    root['exit_state'] = other.primary_exit_state
    count = len(ex.sent)
    with pytest.raises(ValueError, match='already_exposed_canonical_instrument'):
        V.dispatch(executor, other, ins, identity=root, reference=ref, atr=2.)
    assert len(ex.sent) == count and len(j.open_trades()) == 1


def test_panic_closes_each_native_side_using_its_actual_quantity(setup, monkeypatch):
    from trader.engine.reconcile import flatten_all
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    count = len(ex.sent)
    result = flatten_all(ex, j)
    assert result.complete and int(result) == 2
    assert not ex.positions and not ex.stops
    sent = [s for s in ex.sent[count:] if s[1] == 'market' and 'stopLossPrice' not in s[4]]
    assert len(sent) == 2
    assert {s[4]['positionSide'] for s in sent} == {'LONG', 'SHORT'}
    assert all('reduceOnly' not in s[4] for s in sent)


def test_closed_native_group_reaches_one_verified_combined_outcome(setup, monkeypatch):
    import json
    from trader.engine import coordinated_management as M, coordinated_accounting as G
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    assert U.close_group_once(executor, 'g', 'native-final-accounting')['leg'] == 'primary'
    assert U.resume(executor)[0]['leg'] == 'hedge'
    assert U.resume(executor)[0]['status'] == 'CLOSED'
    # Advance only the synthetic venue publication clock. A future official
    # funding frontier is required before a closed cashflow interval is final.
    now = time.time()+2.
    monkeypatch.setattr(time, 'time', lambda:now)
    monkeypatch.setattr(ex, 'fapiPublicGetFundingRate', lambda p:[dict(symbol=p['symbol'],
        fundingTime=t, fundingRate=0.) for t in (min(f['time'] for f in ex.native_fills)-1000, int(now*1000)-500)
        if p['startTime'] <= t <= p['endTime']])
    evidence = M.source(executor, 'g')
    close = j.query('SELECT * FROM coordinated_group_unwinds WHERE group_id=?', ('g',))[0]
    source = dict(schema_version=G.VERSION, observed_ms=int(now*1000), components=evidence['components'],
        closure=dict(plan=json.loads(close['plan_json']), plan_sha256=close['plan_sha256'], state=close['state']))
    assert all(p['capture']['assessment'].get('leg_verified') for p in source['components']), [p['capture']['assessment'] for p in source['components']]
    account = G.accounting(source)
    actual = sum(f['realizedPnl']-f['commission'] for f in ex.native_fills)
    result = G.verified_outcome(source, int(now*1000))
    assert result['actual_execution']['net_pnl'] == pytest.approx(actual)
    assert account['funding_net'] == 0. and len(account['fills']) == 4
    assert len({f['trade_id'] for f in account['fills']}) == 1


def test_current_portfolio_counts_one_trade_and_both_legs_exposure(setup, monkeypatch):
    import json
    from trader.portfolio import runtime_metrics as R
    from trader.portfolio.allocator import Source
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    refresh(ex, j)
    snapshot = json.loads(j.kv_get('venue_position_snapshot'))
    now = snapshot['observed_at_ms']
    captured = R.capture(snapshot=snapshot,
        account_margin=json.loads(j.kv_get('account_margin_observation')),
        journal_positions=j.open_trades(), risk_kv={}, config=executor.cfg,
        as_of_ms=now, closed_count=0, economic_group_requests=j.query('SELECT * FROM execution_requests'))
    result = R.project(captured, Source.freeze('venue', snapshot),
        Source.freeze('policy', {'risk':executor.cfg['risk']}), now)
    assert result['open_leg_count']['value'] == 2
    assert result['open_position_count']['value'] == 1
    assert float(result['gross_exposure']['value']) == pytest.approx(288.)
    assert float(result['per_instrument_exposure']['value']['binance_usdm:futures:BTCUSDT']) == pytest.approx(288.)
    assert result['risk_heat_pct']['status'] == 'ESTABLISHED'


def test_venue_triggered_stop_books_actual_native_fills_and_leaves_hedge_open(setup, monkeypatch):
    from types import SimpleNamespace
    from trader.kernel import Kernel
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    primary = next(t for t in j.open_trades() if t['side'] == 'long')
    ex.create_order('BTC/USDT', 'market', 'sell', primary['amount'],
        params=dict(positionSide='LONG', newClientOrderId='7000001'))
    order = ex.native_orders['7000001']; at = order['time']
    entry_time = min(f['time'] for f in ex.native_fills)
    algo = dict(algoId=primary['sl_order_id'], algoType='CONDITIONAL', orderType='STOP_MARKET',
        symbol='BTCUSDT', side='SELL', positionSide='LONG', quantity=primary['amount'],
        reduceOnly=False, algoStatus='FINISHED', actualOrderId='7000001', actualQty=primary['amount'],
        actualType='STOP_MARKET', createTime=entry_time, triggerTime=at, updateTime=at)
    ex.stops[:] = [s for s in ex.stops if s['positionSide'] != 'LONG']
    monkeypatch.setattr(ex, 'fapiPrivateGetAlgoOrder', lambda p:algo, raising=False)
    from trader.engine import native_exit_provenance as N
    proof = N.replay(N.capture(N.reference_snapshot(primary), ex))
    assert proof['leg_verified'], proof['reasons']
    state = SimpleNamespace(exchange=ex, journal=j, market_type=executor.market_type)
    count = len(ex.sent)
    assert Kernel._detect_exchange_exits.__wrapped__(state, 'BTC/USDT') == 1
    assert len(ex.sent) == count and len(j.open_trades()) == 1 and j.open_trades()[0]['side'] == 'short'
    import json
    receipt = json.loads(j.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id DESC',
        (primary['id'],))[0]['payload'])
    assert receipt['evidence']['basis'] == 'venue_order_fills'
    assert receipt['evidence']['native_exit_provenance']['assessment']['leg_verified']
    assert receipt['after']['realized_pnl'] == pytest.approx(-8.0768)


def test_reconciliation_restores_only_the_missing_native_sides_stop(setup, monkeypatch):
    import json
    from trader.engine.reconcile import reconcile_futures, REARM_KEY
    ex, j, executor, book, identity, ref = both(setup, monkeypatch)
    original = next(s['algoId'] for s in ex.stops if s['positionSide'] == 'LONG')
    ex.stops[:] = [s for s in ex.stops if s['positionSide'] == 'LONG']
    count = len(ex.sent)
    first = reconcile_futures(ex, j, verify=True)
    assert first['rearmed'] == 1 and first['naked'] == 1
    assert first['safety_issues'] == ['native_protection_awaiting_verification:BTC/USDT:SHORT']
    assert len(ex.sent) == count+1 and ex.sent[-1][4]['positionSide'] == 'SHORT'
    second = reconcile_futures(ex, j, verify=True)
    assert second['safety_issues'] == [] and second['naked'] == 0
    assert len(ex.sent) == count+1
    assert next(s['algoId'] for s in ex.stops if s['positionSide'] == 'LONG') == original
    assert json.loads(j.kv_get(REARM_KEY)) == {}
