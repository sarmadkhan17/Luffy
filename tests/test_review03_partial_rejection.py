"""P-2: a definitive TP1 refusal releases the intent; an ambiguous one never does."""
import ccxt
from trader.core.journal import Journal
from tests.test_final_audit_partial_intent import Venue, book, row  # noqa: F401


class Refusing(Venue):
    def __init__(self, exc):
        super().__init__(timeout=False)
        self.exc = exc

    def create_order(self, symbol, kind, side, amount, params):
        self.calls.append((amount, dict(params)))
        raise self.exc


def _swap(book, exc):
    venue, j, e = book
    v = Refusing(exc)
    e.ex = v
    return v, j, e


def test_definitive_reject_releases_the_barrier_and_allows_retry(book):
    v, j, e = _swap(book, ccxt.InvalidOrder("ReduceOnly Order is rejected"))
    assert e.close_partial(row(j), 5) is False
    assert j.query("SELECT * FROM partial_exit_intents") == []
    assert not e.recovery_pending()
    ev = j.query("SELECT * FROM control_events WHERE event='partial_exit_rejected'")
    assert len(ev) == 1
    # a later, valid attempt is possible again (same deterministic client id)
    assert e.close_partial(row(j), 5) is False and len(v.calls) == 2
    assert v.calls[0][1]["newClientOrderId"] == v.calls[1][1]["newClientOrderId"]


def test_ambiguous_error_keeps_the_intent_pending(book):
    v, j, e = _swap(book, ccxt.RequestTimeout("no ack"))
    assert e.close_partial(row(j), 5) is False
    rows = j.query("SELECT * FROM partial_exit_intents")
    assert len(rows) == 1 and rows[0]["state"] == "SUBMISSION_ATTEMPTED"
    assert e.recovery_pending()
    assert j.query("SELECT * FROM control_events WHERE event='partial_exit_rejected'") == []


def test_unknown_exception_type_is_treated_as_ambiguous(book):
    v, j, e = _swap(book, RuntimeError("???"))
    assert e.close_partial(row(j), 5) is False
    assert len(j.query("SELECT * FROM partial_exit_intents")) == 1
