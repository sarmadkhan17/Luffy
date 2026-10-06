"""EXE-03: a partial exit UNKNOWN resolves only by the exact order; reduction applies once.

Offline only: fake venue, no kernel/provider calls.
"""
import pytest
from tests.test_final_audit_partial_intent import Venue, book, row  # noqa: F401
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType, Position, Side
from trader.engine.executor import Executor


def _reopen(venue, j):
    e = Executor(venue, Journal(j.db_path), load_config(), MarketType.FUTURES)
    e.fill_retry_s = 0
    return e


def _intents(j):
    return j.query('SELECT trade_id,state,client_order_id FROM partial_exit_intents')


def test_missing_ack_stays_unknown_and_identity_survives_restart(book):
    venue, j, e = book
    assert e.close_partial(row(j), 5) is False                 # executed, response lost
    cid = _intents(j)[0]['client_order_id']
    assert _intents(j)[0]['state'] == 'SUBMISSION_ATTEMPTED' and row(j)['amount'] == 10
    r = _reopen(venue, j)
    assert r.recovery_pending()
    assert r.close_partial(row(j), 5) is True
    assert len(venue.calls) == 1 and venue.calls[0][1]['newClientOrderId'] == cid
    assert row(j)['amount'] == 5 and row(j)['tp1_done'] == 1


@pytest.mark.parametrize('field,value', [
    ('clientOrderId', 'lp_someone_else'), ('clientOrderId', None), ('symbol', 'ETH/USDT:USDT'),
    ('side', 'buy'), ('reduceOnly', False), ('id', None)])
def test_wrong_order_evidence_cannot_resolve_partial_unknown(book, field, value):
    venue, j, e = book
    assert e.close_partial(row(j), 5) is False
    venue.order[field] = value                                  # same fill/position, wrong identity
    for _ in range(2):
        assert _reopen(venue, j).close_partial(row(j), 5) is False
    assert row(j)['amount'] == 10 and row(j)['tp1_done'] == 0
    assert _intents(j)[0]['state'] != 'CONSUMED' and _reopen(venue, j).recovery_pending()
    assert len(venue.calls) == 1                                # never a second reduction


def test_acked_order_id_mismatch_cannot_resolve(book):
    venue, j, e = book
    venue.timeout = False
    real = venue.fetch_order
    assert e.close_partial(row(j), 5) is True                   # acked 'order-1', consumed
    # fresh trade: ack recorded, then venue echoes a different order id
    j.add_trade(Position(id='t2', symbol='BTC/USDT', side=Side.LONG, amount=10, entry_price=100,
                         notional_usdt=1000, stop_loss=95, market_type='futures', exec_mode='live'))
    venue.quantity = 10.
    venue.timeout = True
    t2 = dict(j.query("SELECT * FROM trades WHERE id='t2'")[0])
    assert e.close_partial(t2, 5) is False
    from trader.engine import partial_intent as P
    P.acknowledge(e, dict(__import__('json').loads(j.query("SELECT payload FROM partial_exit_intents WHERE trade_id='t2'")[0]['payload'])), {'id': 'order-1'})
    venue.fetch_order = lambda oid, sym, params=None: dict(venue.order, id='other-order')
    assert _reopen(venue, j).close_partial(t2, 5) is False
    assert dict(j.query("SELECT * FROM trades WHERE id='t2'")[0])['tp1_done'] == 0
    venue.fetch_order = real


def test_confirmed_reduction_applies_once_under_redelivery(book):
    venue, j, e = book
    assert e.close_partial(row(j), 5) is False
    r = _reopen(venue, j)
    assert r.close_partial(row(j), 5) is True
    pnl = row(j)['realized_pnl']
    for qty in (5, 2.5, 5):                                     # redelivery, recalculated quantity
        assert _reopen(venue, j).close_partial(row(j), qty) is True
    assert venue.quantity == 5 and row(j)['amount'] == 5 and row(j)['realized_pnl'] == pnl
    assert len(venue.calls) == 1 and len(_intents(j)) == 1


def test_distinct_trade_gets_distinct_partial_identity(book):
    venue, j, e = book
    venue.timeout = False
    assert e.close_partial(row(j), 5) is True
    j.add_trade(Position(id='t2', symbol='BTC/USDT', side=Side.LONG, amount=10, entry_price=100,
                         notional_usdt=1000, stop_loss=95, market_type='futures', exec_mode='live'))
    venue.quantity = 15.
    assert e.close_partial(dict(j.query("SELECT * FROM trades WHERE id='t2'")[0]), 5) is True
    ids = [r['client_order_id'] for r in _intents(j)]
    assert len(ids) == 2 and len(set(ids)) == 2 and len(venue.calls) == 2
    assert dict(j.query("SELECT * FROM trades WHERE id='t2'")[0])['amount'] == 5
