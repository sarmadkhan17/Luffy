"""EXE-01: retries, timeouts, redelivery and restart never duplicate new exposure.

Offline only: fake venue, no kernel/provider calls.
"""
import sqlite3
import pytest
from ccxt import RequestTimeout, InsufficientFunds
from tests.test_entry_recovery import setup, Venue  # noqa: F401
from tests.entry_authority_fixtures import permission
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Decision, Action, MarketType
from trader.engine.executor import Executor
from trader.engine.entry_authority import proposal_binding


def _open(e, d, kw):
    return e.open(d, 2, 2, 95, 110, 'strategy', 'strategy', **kw)


def _entries(ex):
    return [s for s in ex.sent if not s[4].get('reduceOnly') and 'stopLossPrice' not in s[4]
            and s[1] == 'market']


def _rows(j):
    return j.query('SELECT logical_id,decision_id,client_order_id,state FROM execution_requests')


def _restart(ex, j, e):
    return Executor(ex, Journal(j.db_path), load_config(), MarketType.FUTURES,
                    risk_manager=e.risk_manager)


def test_lost_response_same_permission_retry_and_restart_never_resubmit(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    ex.entry_error = RequestTimeout('accepted, response lost')
    assert _open(e, d, kw) is None
    row = _rows(j)[0]
    assert row['state'] == 'UNKNOWN_OUTCOME'  # lost response is not non-submission
    cid = row['client_order_id']
    ex.entry_error = None
    assert _open(e, d, kw) is None                       # same-process retry
    r = _restart(ex, j, e)
    assert _open(r, d, kw) is None                       # after restart, same permission
    assert len(_entries(ex)) == 1
    assert _entries(ex)[0][4]['newClientOrderId'] == cid
    assert _rows(j)[0]['client_order_id'] == cid          # identity preserved


def test_lost_response_then_recovery_then_retry_still_no_second_entry(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    ex.entry_error = RequestTimeout('accepted, response lost')
    assert _open(e, d, kw) is None
    r = _restart(ex, j, e)
    r.recover_entries(); r.recover_entries()
    assert not r.recovery_pending()
    assert _rows(j)[0]['state'] == 'TERMINAL'
    ex.entry_error = None
    assert _open(r, d, kw) is None
    assert _open(_restart(ex, j, e), d, kw) is None
    assert len(_entries(ex)) == 1
    assert len(j.open_trades()) == 1


def test_refusal_retry_same_action_blocked_across_restart(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    ex.entry_error = InsufficientFunds('rejected')
    assert _open(e, d, kw) is None
    assert _rows(j)[0]['state'] == 'REFUSED'
    ex.entry_error = None
    assert _open(e, d, kw) is None
    assert _open(_restart(ex, j, e), d, kw) is None
    assert len(_entries(ex)) == 1


def test_crash_after_reservation_before_venue_call_is_not_replayed(setup, monkeypatch):
    ex, j, e, d = setup
    kw = permission(e, d)
    real = e.recovery.save

    def crash(intent, reason, **o):
        if reason == 'entry_submission_ambiguous':
            raise sqlite3.OperationalError('crash')
        return real(intent, reason, **o)
    monkeypatch.setattr(e.recovery, 'save', crash)
    ex.entry_error = RequestTimeout('lost')
    with pytest.raises(sqlite3.OperationalError):
        _open(e, d, kw)
    assert len(_entries(ex)) == 1
    ex.entry_error = None
    r = _restart(ex, j, e)
    assert _open(r, d, kw) is None
    assert len(_entries(ex)) == 1
    assert _rows(j)[0]['state'] != 'REFUSED'  # never reclassified as not-submitted


def test_redelivered_decision_under_other_logical_id_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    with j._tx() as db:  # durable prior request for this decision under another logical id
        db.execute("INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,"
                   "client_order_id,state) VALUES ('other',?,'h','{}','lr_other','UNKNOWN_OUTCOME')", (d.id,))
    assert _open(e, d, kw) is None
    assert d.skip_reason == 'logical_action_already_reserved'
    assert not _entries(ex)


def test_same_logical_id_under_other_decision_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    lid = kw['risk_permission'].payload()['logical_id']
    with j._tx() as db:
        db.execute("INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,"
                   "client_order_id,state) VALUES (?,'other-decision','h','{}','lr_other','UNKNOWN_OUTCOME')", (lid,))
    assert _open(e, d, kw) is None
    assert d.skip_reason == 'logical_action_already_reserved'
    assert not _entries(ex)


def test_forged_client_order_id_collision_cannot_insert_second_request(setup):
    ex, j, e, d = setup
    assert _open(e, d, permission(e, d)) is not None
    row = _rows(j)[0]
    with pytest.raises(sqlite3.IntegrityError):
        with j._tx() as db:
            db.execute("INSERT INTO execution_requests(logical_id,decision_id,intent_hash,"
                       "intent_json,client_order_id,state) VALUES ('x','y','h','{}',?,?)",
                       (row['client_order_id'], 'RISK_AUTHORIZED'))


def test_new_logical_action_has_distinct_identity(setup):
    ex, j, e, d = setup
    d2 = Decision('d2', 'c', 'BTC/USDT', Action.BUY, 1., .5, .8, [], [])
    j.log_decision(d2)
    d2.instrument_binding_json = proposal_binding(j, d2.symbol)
    kw = permission(e, d)
    lid1 = kw['risk_permission'].payload()['logical_id']
    kw2 = permission(e, d2)
    lid2 = kw2['risk_permission'].payload()['logical_id']
    assert lid1 != lid2
    # Prior unrelated request does not trip the dedup gate for a new action.
    with j._tx() as db:
        db.execute("INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,"
                   "client_order_id,state) VALUES ('prior','prior-d','h','{}','lr_prior','TERMINAL')")
    _open(e, d2, kw2)
    assert d2.skip_reason != 'logical_action_already_reserved'
