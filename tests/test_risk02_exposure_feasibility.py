"""RISK-02: exposure admission is constrained by authoritative numeric, leverage,
margin and order-feasibility facts. Offline; every refusal asserts no venue order."""
import json
import time
from dataclasses import replace

import pytest

from tests.test_entry_recovery import setup  # noqa: F401
from tests.entry_authority_fixtures import permission
from trader.core.types import Action
from trader.engine import entry_authority as A
from trader.engine.evidence_capture import margin_observation, record_margin


def _rewrite_cap(j, d, mutate, rebind=True):
    cap = json.loads(j.kv_get(A.CAP_KEY + d.symbol))
    mutate(cap)
    cap['receipt_id'] = A.digest({k: v for k, v in cap.items() if k != 'receipt_id'})
    j.kv_set(A.CAP_KEY + d.symbol, A.canonical(cap))
    if rebind:
        d.instrument_binding_json = A.proposal_binding(j, d.symbol)


def _open(e, d, kw, **ov):
    a = dict(amount=2., atr=2., stop_loss=95., take_profit=110.)
    a.update(ov)
    return e.open(d, a['amount'], a['atr'], a['stop_loss'], a['take_profit'], 'strategy', 'strategy', **kw)


BAD = [float('nan'), float('inf'), float('-inf'), 0, -1]


@pytest.mark.parametrize('price', BAD + [None, True])
def test_invalid_reference_price_cannot_authorize(setup, price):
    ex, j, e, d = setup
    with pytest.raises(ValueError):
        e.risk_manager.authorize_entry(d, 2., 2., 95., 110., 'strategy', reference=dict(price=price))
    assert not ex.sent


@pytest.mark.parametrize('price', BAD)
def test_invalid_reference_price_at_execution_refused(setup, price):
    ex, j, e, d = setup
    kw = permission(e, d)
    kw['reference'] = dict(price=price)
    assert _open(e, d, kw) is None and not ex.sent


@pytest.mark.parametrize('field', ['amount', 'atr', 'stop'])
@pytest.mark.parametrize('value', BAD)
def test_invalid_request_numbers_cannot_authorize(setup, field, value):
    ex, j, e, d = setup
    args = dict(amount=2., atr=2., stop=95.)
    args[field] = value
    with pytest.raises(ValueError):
        e.risk_manager.authorize_entry(d, args['amount'], args['atr'], args['stop'], 110., 'strategy',
                                       reference=dict(price=100.))
    assert not ex.sent


@pytest.mark.parametrize('side,stop', [(Action.BUY, 100.), (Action.BUY, 105.), (Action.SELL, 100.), (Action.SELL, 95.)])
def test_infeasible_stop_geometry_cannot_authorize(setup, side, stop):
    ex, j, e, d = setup
    d = replace(d, action=side)
    with pytest.raises(ValueError, match='entry_stop_geometry_invalid'):
        e.risk_manager.authorize_entry(d, 2., 2., stop, 110., 'strategy', reference=dict(price=100.))
    assert not ex.sent


def test_wrong_capability_short_not_proven(setup):
    ex, j, e, d = setup
    _rewrite_cap(j, d, lambda c: c['record'].__setitem__('shortability', 'UNKNOWN'), rebind=False)
    # Receipt digest of embedded registry is part of snapshot: the tamper must be caught somewhere.
    with pytest.raises(ValueError):
        e.risk_manager.authorize_entry(replace(d, action=Action.SELL), 2., 2., 105., 90., 'strategy',
                                       reference=dict(price=100.))
    assert not ex.sent


@pytest.mark.parametrize('field,value', [('quote_asset', 'BUSD'), ('settlement_asset', 'BUSD'),
                                         ('contract_multiplier', '10'), ('venue_status', 'BREAK')])
def test_wrong_capability_instrument_model(setup, field, value):
    ex, j, e, d = setup
    _rewrite_cap(j, d, lambda c: c['record'].__setitem__(field, value), rebind=False)
    with pytest.raises(ValueError):
        permission(e, d)
    assert not ex.sent


def test_capability_for_other_symbol_cannot_authorize(setup):
    ex, j, e, d = setup
    with pytest.raises(ValueError):
        e.risk_manager.authorize_entry(replace(d, symbol='ETH/USDT'), 2., 2., 95., 110., 'strategy',
                                       reference=dict(price=100.))
    assert not ex.sent


def test_stale_leverage_observation_refused(setup, monkeypatch):
    ex, j, e, d = setup
    real = time.time
    from trader.dashboard.current_truth import ACCOUNT_STALE_S
    monkeypatch.setattr(A.time, 'time', lambda: real() + ACCOUNT_STALE_S + 5)
    with pytest.raises(ValueError, match='capability_stale_or_mismatched|leverage_unverified'):
        permission(e, d)
    assert not ex.sent


def test_leverage_observation_older_than_account_bound_refused(setup):
    """Capability snapshot fresh, but the leverage receipt itself is stale."""
    ex, j, e, d = setup
    from trader.dashboard.current_truth import ACCOUNT_STALE_S
    cap = json.loads(j.kv_get(A.CAP_KEY + d.symbol)); old = cap['leverage']
    def age(c):
        c['leverage'] = A.observe_leverage(instrument_id=old['instrument_id'], account_scope=old['account_scope'],
            snapshot_id=old['snapshot_id'], symbol_config=old['symbol_config'],
            leverage_brackets=old['leverage_brackets'],
            observed_at_ms=old['observed_at_ms'] - int((ACCOUNT_STALE_S + 5) * 1000),
            valid_until_ms=old['valid_until_ms'], source=old['source'])
    _rewrite_cap(j, d, age)
    with pytest.raises(ValueError, match='leverage_unverified'):
        permission(e, d)
    assert not ex.sent


def test_leverage_observation_not_yet_valid_refused(setup):
    ex, j, e, d = setup
    def future(c):
        c['leverage']['observed_at_ms'] += 10 ** 9
    _rewrite_cap(j, d, future)
    with pytest.raises(ValueError):
        permission(e, d)
    assert not ex.sent


@pytest.mark.parametrize('desired', [1, 6, 20, 125])
def test_desired_leverage_must_equal_verified_actual(setup, desired):
    """Configured/desired leverage above (or merely different from) the venue-verified one is refused."""
    ex, j, e, d = setup
    e.risk_manager.leverage = desired
    with pytest.raises(ValueError, match='leverage_unverified'):
        permission(e, d)
    assert not ex.sent


def test_executor_desired_leverage_changed_after_permission_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    e.leverage = e.leverage + 1
    assert _open(e, d, kw) is None and not ex.sent


def test_bracket_maximum_below_leverage_refused(setup):
    ex, j, e, d = setup
    cap = json.loads(j.kv_get(A.CAP_KEY + d.symbol)); old = cap['leverage']
    lev = int(float(old['actual'])) - 1
    def lower(c):
        c['leverage'] = A.observe_leverage(instrument_id=old['instrument_id'], account_scope=old['account_scope'],
            snapshot_id=old['snapshot_id'], symbol_config=old['symbol_config'],
            leverage_brackets={'symbol': 'BTCUSDT', 'brackets': [dict(notionalFloor='0', notionalCap='100000', initialLeverage=lev)]},
            observed_at_ms=old['observed_at_ms'], valid_until_ms=old['valid_until_ms'], source=old['source'])
    _rewrite_cap(j, d, lower)
    with pytest.raises(ValueError):
        permission(e, d)
    assert not ex.sent


def _set_available(j, d, available, equity='10000'):
    now = int(time.time() * 1000)
    scope = json.loads(j.kv_get(A.CAP_KEY + d.symbol))['account_scope']
    m = margin_observation(json.dumps(dict(totalMarginBalance=equity, availableBalance=available)).encode(),
        request_url='https://demo-fapi.binance.com/fapi/v3/account', request_start_ms=now, received_ms=now,
        account_scope=scope)
    record_margin(j, m)


@pytest.mark.parametrize('available', ['0', '1', '39.99'])
def test_insufficient_available_margin_refused(setup, available):
    ex, j, e, d = setup
    _set_available(j, d, available)  # 2 BTC @100 / lev needs >> this
    with pytest.raises(ValueError):
        permission(e, d)
    assert not ex.sent


def test_margin_observation_missing_or_stale_refused(setup):
    ex, j, e, d = setup
    j.kv_set('account_margin_observation', 'null')
    with pytest.raises(ValueError, match='risk_margin_unverified'):
        permission(e, d)
    assert not ex.sent


def test_margin_equity_disagreeing_with_account_equity_refused(setup):
    ex, j, e, d = setup
    _set_available(j, d, '10000', equity='9000')
    with pytest.raises(ValueError, match='risk_margin_unverified'):
        permission(e, d)
    assert not ex.sent


@pytest.mark.parametrize('amount', [0.0005, 0.00099, 1001.])
def test_quantity_outside_venue_bounds_never_submits(setup, amount):
    ex, j, e, d = setup
    try:
        kw = permission(e, d, amount=amount)
    except ValueError:
        assert not ex.sent
        return
    assert _open(e, d, kw, amount=amount) is None and not ex.sent


@pytest.mark.parametrize('amount', [1.0005, 0.0015, 2.0000001])
def test_quantity_off_step_never_submits(setup, amount):
    ex, j, e, d = setup
    try:
        kw = permission(e, d, amount=amount)
    except ValueError:
        assert not ex.sent
        return
    assert _open(e, d, kw, amount=amount) is None and not ex.sent


def test_below_minimum_notional_never_submits(setup):
    ex, j, e, d = setup
    # 0.04 @ 100 = 4 USDT < minimum_notional 5, but step/min-qty satisfied
    try:
        kw = permission(e, d, amount=0.04)
    except ValueError:
        assert not ex.sent
        return
    assert _open(e, d, kw, amount=0.04) is None and not ex.sent


def test_venue_constraints_changed_after_permission_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    def tighten(c):
        c['record']['constraints']['market_minimum_quantity'] = '5'
        c['registry']['records'] = [c['record']]
    _rewrite_cap(j, d, tighten, rebind=False)
    assert _open(e, d, kw) is None and not ex.sent


def test_valid_baseline_still_submits(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    assert _open(e, d, kw) is not None
    assert any(not s[4].get('reduceOnly') and 'stopLossPrice' not in s[4] for s in ex.sent)


# Stop geometry and leverage freshness are enforced by two independent layers
# (intent+Risk; capability+observe_leverage), so single-guard mutants are equivalent.
# ── mutants: each removes one guard; the matching proof must fail ────────
def _mutate(monkeypatch, fn_name, old, new):
    import inspect
    code = inspect.getsource(getattr(A, fn_name))
    assert old in code, old
    scope = dict(A.__dict__)
    exec(compile(code.replace(old, new), '<risk02-mutant>', 'exec'), scope)
    monkeypatch.setattr(A, fn_name, scope[fn_name])


def _must_fail(check, *a):
    try:
        check(*a)
    except (AssertionError, pytest.fail.Exception):
        return
    raise AssertionError('mutant survived: guard removal not detected')


def test_mutant_venue_constraint_check_removed_is_detected(setup, monkeypatch):
    _mutate(monkeypatch, 'validate', "if (q % positive(constraints['market_quantity_step'])", "if (False and q % positive(constraints['market_quantity_step'])")
    _must_fail(test_quantity_off_step_never_submits, setup, 1.0005)


def test_mutant_min_notional_removed_is_detected(setup, monkeypatch):
    _mutate(monkeypatch, 'validate', "q*price < positive(constraints['minimum_notional'])", "False")
    _must_fail(test_below_minimum_notional_never_submits, setup)


def test_mutant_margin_check_removed_is_detected(setup, monkeypatch):
    _mutate(monkeypatch, 'authorize', "> available:", "> available + 10**12:")
    _must_fail(test_insufficient_available_margin_refused, setup, '1')


def test_mutant_leverage_equality_removed_is_detected(setup, monkeypatch):
    _mutate(monkeypatch, 'capability', "or positive(lev.get('actual')) != positive(leverage)", "")
    _must_fail(test_desired_leverage_must_equal_verified_actual, setup, 1)
