"""Review-03 accounting observer: out-of-scope trades must not retry forever.

Synthetic only; no venue, no production DB, no observed-performance claim.
"""
import sqlite3

from tests.test_accounting_worker import setup, run, attempts  # noqa: F401
from tests.test_whole_trade_accounting import Venue


def test_non_live_trade_is_terminal_out_of_scope_not_retried(setup):  # noqa: F811
    with sqlite3.connect(setup[0]) as db:
        db.execute("UPDATE trades SET exec_mode='paper'")
    # Receipts still carry exec_mode 'live' in their snapshot; trade row is the scope authority.
    first = run(setup, Venue, now=5000)
    assert first['jobs'] == {'unsupported_scope': 1}
    n = len(attempts(setup))
    assert n == 0
    # Never re-attempted, however long we wait (no venue construction either).
    later = run(setup, lambda: (_ for _ in ()).throw(AssertionError('fetched')), now=10**12)
    assert later['attempted'] == 0
    assert later['jobs'] == {'unsupported_scope': 1}
    assert len(attempts(setup)) == 0


def test_live_closed_trade_unchanged(setup):  # noqa: F811
    assert run(setup)['jobs'] == {'complete': 1}
