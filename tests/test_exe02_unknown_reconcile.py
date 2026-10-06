"""EXE-02: UNKNOWN entry outcomes resolve only by exact venue/order/fill evidence.

Offline only: fake venue, no kernel/provider calls.
"""
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup, Venue, enter  # noqa: F401
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType
from trader.engine.executor import Executor


def _state(j):
    return j.query('SELECT state,client_order_id FROM execution_requests')[0]


def _restart(ex, j, e):
    return Executor(ex, Journal(j.db_path), load_config(), MarketType.FUTURES,
                    risk_manager=e.risk_manager)


def _unknown(setup):
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout('accepted, response lost')
    assert enter(e, d) is None
    return ex, j, e, d, _state(j)['client_order_id']


def test_missing_ack_and_lookup_failure_stay_unknown_without_reissue(setup):
    ex, j, e, d, cid = _unknown(setup)
    assert _state(j)['state'] == 'UNKNOWN_OUTCOME'
    ex.read_error = True
    e.recover_entries()
    assert _state(j)['state'] == 'UNKNOWN_OUTCOME'
    assert e.recovery_pending() and enter(e, d) is None
    r = _restart(ex, j, e)
    assert r.recovery.pending()['client_order_id'] == cid   # identity survives restart
    r.recover_entries()
    assert _state(j)['state'] == 'UNKNOWN_OUTCOME'
    assert len(ex.sent) == 1


def test_venue_has_no_such_order_stays_unknown(setup):
    ex, j, e, d, cid = _unknown(setup)
    ex.orders.clear(); ex.positions.clear()
    e.recover_entries()
    assert _state(j)['state'] == 'UNKNOWN_OUTCOME' and e.recovery_pending()
    assert len(ex.sent) == 1


@pytest.mark.parametrize('field,value', [
    ('clientOrderId', 'lr_someone_else'), ('symbol', 'ETH/USDT:USDT'),
    ('side', 'sell'), ('clientOrderId', None)])
def test_wrong_order_evidence_cannot_resolve_unknown(setup, field, value):
    ex, j, e, d, cid = _unknown(setup)
    ex.orders[cid] = dict(ex.orders[cid], status='canceled', filled=0., **{field: value})
    ex.orders['entry'] = ex.orders[cid]
    ex.positions.clear()                                    # flat + terminal, but not OUR order
    e.recover_entries()
    assert _state(j)['state'] == 'UNKNOWN_OUTCOME'
    assert e.recovery_pending()
    assert len(ex.sent) == 1


def test_exact_evidence_resolves_unknown(setup):
    ex, j, e, d, cid = _unknown(setup)
    o = ex.orders[cid]
    assert o['clientOrderId'] == cid and o['side'] == 'buy'
    e.recover_entries(); e.recover_entries()
    assert not e.recovery_pending()
    assert _state(j)['state'] == 'TERMINAL'
    assert len(j.open_trades()) == 1


def test_exact_terminal_cancel_resolves_after_restart(setup):
    ex, j, e, d, cid = _unknown(setup)
    ex.orders[cid]['status'] = 'canceled'; ex.orders[cid]['filled'] = 0.
    ex.positions.clear()
    r = _restart(ex, j, e)
    r.recover_entries()
    assert not r.recovery_pending() and _state(j)['state'] == 'TERMINAL'


def test_acked_order_id_mismatch_cannot_resolve(setup):
    ex, j, e, d, cid = _unknown(setup)
    intent = e.recovery.pending()
    intent['order_id'] = 'entry'                       # ack was received for order 'entry'
    e.recovery.save(intent, 'entry_submitted')
    ex.orders['entry'] = dict(ex.orders['entry'], id='other-order', status='canceled', filled=0.)
    ex.positions.clear()
    e.recover_entries()
    assert _state(j)['state'] == 'UNKNOWN_OUTCOME' and e.recovery_pending()
    assert e.recovery.pending()['reason'] == 'order_evidence_mismatch'
