"""Failure injection for durable, demo-independent exposure recovery."""
import json

import pytest
from ccxt import InsufficientFunds, RequestTimeout

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Action, Decision, MarketType, Position, Side
from trader.engine.executor import Executor
from trader.engine.recovery import KEY
from trader.engine import protective


class Venue:
    def __init__(self):
        self.orders = {}
        self.positions = []
        self.stops = []
        self.sent = []
        self.entry_error = None
        self.close_error = None
        self.read_error = False
        self.stop_error = False
        self.cancel_terminal = True

    def create_order(self, symbol, typ, side, amount, params=None):
        params = params or {}
        self.sent.append((symbol, typ, side, amount, params))
        if 'stopLossPrice' in params or typ == 'STOP_MARKET':
            if self.stop_error:
                raise RequestTimeout('stop unknown')
            self.stops.append({'algoId': 'stop', 'symbol': 'BTCUSDT', 'side': side,
                               'reduceOnly': True,
                               'orderType': 'STOP_MARKET',
                               'quantity': str(amount), 'triggerPrice': str(params['stopLossPrice'])})
            return {'id': 'stop'}
        close = params.get('reduceOnly', False)
        error = self.close_error if close else self.entry_error
        if isinstance(error, InsufficientFunds):
            raise error
        oid = 'close' if close else 'entry'
        order = {'id': oid, 'status': 'closed', 'average': 100., 'filled': amount}
        self.orders[oid] = order
        self.orders[params['newClientOrderId']] = order
        if not close:
            self.positions = [{'symbol': symbol, 'side': 'long', 'contracts': amount,
                               'entryPrice': 100., 'markPrice': 100.}]
        if error:
            raise error
        return order

    def fetch_order(self, oid, symbol, params=None):
        if self.read_error:
            raise RequestTimeout('offline')
        return self.orders[oid or params['origClientOrderId']]

    def cancel_order(self, oid, symbol):
        if self.cancel_terminal:
            self.orders[oid]['status'] = 'canceled'

    def fetch_positions(self):
        if self.read_error:
            raise RequestTimeout('offline')
        return self.positions

    def fapiPrivateGetOpenAlgoOrders(self):
        if self.read_error:
            raise RequestTimeout('offline')
        return self.stops

    def fetch_open_orders(self, symbol):
        return []


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr('trader.engine.executor.time.sleep', lambda _: None)
    j = Journal(tmp_path/'journal.db')
    d = Decision('d', 'c', 'BTC/USDT', Action.BUY, 1., .5, .8, [], [])
    with j._tx() as db:
        db.execute("INSERT INTO cycles(id,ts,symbol) VALUES ('c','2026-09-16','BTC/USDT')")
    j.log_decision(d)
    ex = Venue()
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    return ex, j, e, d


def enter(e, d):
    return e.open(d, 2., 2., 95., 110., 'strategy', 'strategy')


def test_success_clears_durable_intent_only_after_journalling(setup):
    ex, j, e, d = setup
    p = enter(e, d)
    assert p.amount == 2
    assert not e.recovery_pending()
    assert len(j.open_trades()) == 1
    assert ex.sent[0][4]['newClientOrderId'].startswith('lr_')


def test_ambiguous_submission_survives_restart_and_adopts_exact_exposure(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('accepted, response lost')
    assert enter(e, d) is None
    assert e.recovery_pending()
    restarted = Executor(ex, Journal(j.db_path), load_config(), MarketType.FUTURES)
    assert enter(restarted, d) is None  # no duplicate submission
    assert len(ex.sent) == 1
    restarted.recover_entries()  # places protection, keeps gate
    assert restarted.recovery_pending()
    restarted.recover_entries()  # verifies stop and adopts
    assert not restarted.recovery_pending()
    assert j.open_trades()[0]['entry_price'] == 100
    assert len(ex.sent) == 2
    restarted.recover_entries()
    assert len(j.open_trades()) == 1


def test_entry_recovery_uses_canonical_protection_match(setup, monkeypatch):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('accepted, response lost')
    enter(e, d)
    e.recover_entries()  # submits a stop; venue lists it on the next pass
    real_match = protective.protection_match
    seen = []

    def match(*args):
        seen.append(args)
        return real_match(*args)

    monkeypatch.setattr(protective, "protection_match", match)
    e.recover_entries()
    assert seen and seen[0][3] == 2  # current venue amount
    assert not e.recovery_pending()


def test_explicit_rejection_releases_gate(setup):
    ex, j, e, d = setup
    ex.entry_error = InsufficientFunds('rejected')
    assert enter(e, d) is None
    assert not e.recovery_pending()


def test_unavailable_venue_retains_intent_and_operator_hold(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.read_error = True
    j.kv_set('control_state', 'FROZEN')
    j.kv_set('macro_guard_operator_hold', '1')
    e.recover_entries()
    assert e.recovery.pending()['reason'] == 'venue_or_recovery_unavailable'
    assert j.kv_get('control_state') == 'FROZEN'
    assert j.kv_get('macro_guard_operator_hold') == '1'
    assert len(ex.sent) == 1


def test_flat_snapshot_does_not_release_nonterminal_entry(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.orders['entry']['status'] = 'open'
    ex.positions = []
    ex.cancel_terminal = False
    e.recover_entries()
    assert e.recovery_pending()
    ex.orders['entry']['status'] = 'canceled'
    e.recover_entries()
    assert not e.recovery_pending()


def test_partial_entry_cancels_remainder_then_protects_actual_size(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('partial')
    enter(e, d)
    ex.orders['entry'].update(status='open', filled=.5)
    ex.positions[0]['contracts'] = .5
    e.recover_entries()
    assert ex.orders['entry']['status'] == 'canceled'
    assert ex.sent[-1][3] == .5
    e.recover_entries()
    assert j.open_trades()[0]['amount'] == .5


def test_failed_stop_close_is_pending_until_terminal_and_flat(setup):
    ex, j, e, d = setup
    ex.stop_error = True
    ex.close_error = RequestTimeout('close accepted response lost')
    enter(e, d)
    assert e.recovery.pending()['phase'] == 'closing'
    ex.orders['close']['status'] = 'open'
    ex.positions = []
    n = len(ex.sent)
    e.recover_entries()
    assert e.recovery_pending()
    assert len(ex.sent) == n
    ex.orders['close']['status'] = 'closed'
    e.recover_entries()
    assert not e.recovery_pending()
    assert not j.open_trades()
    assert not j.query("SELECT * FROM control_events WHERE event='entry_stop_failed_recovered'")


def test_confirmed_partial_close_retries_only_remaining_venue_size(setup):
    ex, j, e, d = setup
    ex.stop_error = True
    enter(e, d)
    ex.positions[0]['contracts'] = .25
    e.recover_entries()
    assert ex.sent[-1][3] == .25
    assert ex.sent[-1][4]['reduceOnly'] is True
    assert e.recovery_pending()


def test_existing_stop_preserved_and_no_duplicate_placement(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.stops = [{'algoId':'existing','symbol':'BTCUSDT','side':'buy',
                 'quantity':'2','triggerPrice':'105'}]  # wrong closing side
    e.recover_entries()
    assert e.recovery.pending()['reason'] == 'protection_requires_verification'
    assert len(ex.sent) == 1
    assert ex.stops[0]['algoId'] == 'existing'


def test_stop_read_failure_is_not_empty_protection(setup):
    ex, j, e, d = setup
    ex.read_error = True
    with pytest.raises(RequestTimeout):
        protective.open_stops(ex, 'BTC/USDT', strict=True)


def test_journal_write_failure_prevents_entry_submission(setup, monkeypatch):
    ex, j, e, d = setup
    monkeypatch.setattr(j, 'kv_set', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        enter(e, d)
    assert not ex.sent


def test_malformed_recovery_record_fails_closed(setup):
    ex, j, e, d = setup
    j.kv_set(KEY, '{}')
    assert enter(e, d) is None
    assert e.recovery_pending()
    e.recover_entries()  # exits in the caller remain able to run
    assert not ex.sent


def test_ambiguous_stop_attempt_is_not_repeated(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.stop_error = True
    e.recover_entries()
    n = len(ex.sent)
    e.recover_entries()
    assert len(ex.sent) == n
    assert e.recovery_pending()


def test_rejected_emergency_close_retries_without_assuming_flat(setup):
    from ccxt import InvalidOrder
    ex, j, e, d = setup
    ex.stop_error = True
    ex.close_error = InvalidOrder('rejected')
    enter(e, d)
    assert e.recovery.pending()['force_close'] is True
    ex.close_error = None
    e.recover_entries()
    assert e.recovery.pending()['phase'] == 'closing'
    assert ex.sent[-1][3] == 2


def test_crash_after_trade_insert_does_not_duplicate_trade(setup, monkeypatch):
    ex, j, e, d = setup
    original = e.recovery.finish
    monkeypatch.setattr(e.recovery, 'finish', lambda *args: (_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError):
        enter(e, d)
    assert len(j.open_trades()) == 1
    assert e.recovery_pending()
    monkeypatch.setattr(e.recovery, 'finish', original)
    e.recover_entries()
    assert not e.recovery_pending()
    assert len(j.open_trades()) == 1
    assert len(ex.sent) == 2


def test_recovery_does_not_claim_non_reduce_only_stop_is_protection(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.stops = [{'algoId':'unsafe','symbol':'BTCUSDT','side':'sell',
                 'quantity':'2','triggerPrice':'95','reduceOnly':False}]
    e.recover_entries()
    assert e.recovery_pending()
    assert not j.open_trades()


def test_side_conflict_keeps_gate_without_sending_an_order(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.positions[0]['side'] = 'short'
    e.recover_entries()
    assert e.recovery.pending()['reason'] == 'venue_side_or_hedge_conflict'
    assert len(ex.sent) == 1


def test_boot_reconciliation_does_not_adopt_recovery_owned_position(setup):
    from trader.engine.reconcile import reconcile_futures
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    report = reconcile_futures(ex, j, exclude_symbols=['BTC/USDT'])
    assert report['adopted'] == 0
    assert report['rearmed'] == 0
    assert not j.open_trades()


def test_unknown_order_is_not_replaced_by_flat_snapshot(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.orders.clear()
    ex.positions.clear()
    e.recover_entries()
    assert e.recovery_pending()
    assert len(ex.sent) == 1


def test_intervening_size_change_is_not_adopted_with_zero_accounting(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.positions[0]['contracts'] = 1
    e.recover_entries()
    assert e.recovery.pending()['reason'] == 'entry_fill_position_mismatch'
    assert not j.open_trades()


def test_flat_with_residual_stop_does_not_allow_a_new_entry(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.positions = []
    ex.stops = [{'algoId':'old','symbol':'BTCUSDT','side':'sell',
                 'quantity':'2','triggerPrice':'95','reduceOnly':True}]
    e.recover_entries()
    assert e.recovery.pending()['reason'] == 'flat_with_remaining_protection'
    assert enter(e, d) is None
    assert len(ex.sent) == 1


def test_order_price_is_not_a_confirmed_fill_average(setup):
    ex, j, e, d = setup
    order = {'id': 'entry', 'filled': 2, 'status': 'closed', 'price': 100, 'average': None}
    ex.orders['entry'] = order
    assert e._confirm_fill('BTC/USDT', 'entry', order, 2) == (None, 2)


def test_take_profit_is_not_downside_protection(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('ambiguous')
    enter(e, d)
    ex.stops = [{'algoId':'tp','symbol':'BTCUSDT','side':'sell',
                 'quantity':'2','triggerPrice':'110','reduceOnly':True,
                 'orderType':'TAKE_PROFIT_MARKET'}]
    e.recover_entries()
    assert e.recovery_pending()
    assert not j.open_trades()


def test_malformed_stop_snapshot_is_not_absence(setup, monkeypatch):
    ex, j, e, d = setup
    monkeypatch.setattr(ex, 'fapiPrivateGetOpenAlgoOrders', lambda: {'error':'unavailable'})
    with pytest.raises(ValueError):
        protective.open_stops(ex, 'BTC/USDT', strict=True)
