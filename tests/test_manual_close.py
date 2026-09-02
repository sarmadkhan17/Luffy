"""An operator must be able to close one position by hand.

/panic flattens everything and freezes the book — far too blunt when a
single trade needs to come off. This is the same channel at single-trade
granularity: the dashboard leaves an intent in `state_kv`, the kernel (the
only writer of truth) acts on it next cycle. Nothing is placed from the
browser.

Two properties carry most of the weight:

  * the drain runs BEFORE the scan loop, so a manual close reaches a
    position whose symbol has rotated out of the universe. Exit management
    used to live inside `for symbol in scan_symbols` and stranded exactly
    those positions; a button the operator presses must not inherit it.
  * a failed close is not requeued. An id retrying forever against a venue
    that keeps rejecting it is worse than one loud error.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

import pytest

from trader.api.graphql_schema import build_mutation
from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.kernel import Kernel


# ── the intent side: dashboard → state_kv ────────────────────────────────
@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.db")
    for i, sym in enumerate(["UNI/USDT", "SOL/USDT"]):
        j.add_trade(Position(
            id=f"pos_{i}", symbol=sym, side=Side.LONG, amount=10.0,
            entry_price=5.0, notional_usdt=50.0, stop_loss=4.5,
            market_type="futures", exec_mode="live",
            strategy_id="s", strategy_name="ema_trend"))
    return j


def _mutation(j):
    return build_mutation(j)()


def _queued(j):
    return json.loads(j.kv_get("close_requests", "[]"))


def test_a_close_request_is_queued_for_the_kernel(journal):
    assert _mutation(journal).close_trade("pos_0") is True
    assert _queued(journal) == ["pos_0"]


def test_two_positions_can_be_queued_at_once(journal):
    """A scalar key would drop the first — both must survive to the cycle."""
    m = _mutation(journal)
    m.close_trade("pos_0")
    m.close_trade("pos_1")
    assert _queued(journal) == ["pos_0", "pos_1"]


def test_pressing_twice_queues_one_close(journal):
    m = _mutation(journal)
    m.close_trade("pos_0")
    m.close_trade("pos_0")
    assert _queued(journal) == ["pos_0"]


def test_an_unknown_trade_is_refused(journal):
    assert _mutation(journal).close_trade("pos_nope") is False
    assert _queued(journal) == []


def test_an_already_closed_trade_is_refused(journal):
    journal.close_trade("pos_0", 5.5, 5.0, "sl_fill")
    assert _mutation(journal).close_trade("pos_0") is False
    assert _queued(journal) == []


def test_the_request_is_journalled_as_a_control_event(journal):
    _mutation(journal).close_trade("pos_0", actor="dashboard")
    rows = journal.query("SELECT * FROM control_events WHERE event='manual_close'")
    assert len(rows) == 1 and rows[0]["actor"] == "dashboard"


# ── the acting side: kernel drains and closes ────────────────────────────
class _Executor:
    def __init__(self, fail=()):
        self.closed: list[tuple] = []
        self.fail = set(fail)
    def close(self, trade, exit_price_hint=0.0, reason="signal_exit"):
        if trade["symbol"] in self.fail:
            return False
        self.closed.append((trade["id"], reason))
        return True


class _Notifier:
    def __init__(self): self.sent = []
    def send(self, msg): self.sent.append(msg)


class _Feed:
    def price(self, sym): return 5.5


def _kernel(j, executor=None):
    k = object.__new__(Kernel)
    k.journal = j
    k.executor = executor or _Executor()
    k.notifier = _Notifier()
    k.feed = _Feed()
    return k


def test_the_queued_trade_is_closed(journal):
    _mutation(journal).close_trade("pos_0")
    k = _kernel(journal)
    assert k._drain_close_requests() == 1
    assert k.executor.closed == [("pos_0", "manual")]


def test_the_queue_is_cleared_after_draining(journal):
    _mutation(journal).close_trade("pos_0")
    k = _kernel(journal)
    k._drain_close_requests()
    assert _queued(journal) == []
    assert k._drain_close_requests() == 0, "a drained id must not close twice"


def test_a_position_outside_the_universe_is_still_closed(journal):
    """The button does not care what the scan happens to be looking at."""
    _mutation(journal).close_trade("pos_1")
    k = _kernel(journal)
    k._drain_close_requests()
    assert [tid for tid, _ in k.executor.closed] == ["pos_1"]


def test_an_id_closed_in_the_meantime_is_skipped(journal):
    _mutation(journal).close_trade("pos_0")
    journal.close_trade("pos_0", 5.5, 5.0, "sl_fill")
    k = _kernel(journal)
    assert k._drain_close_requests() == 0
    assert k.executor.closed == []


def test_a_failed_close_is_reported_and_not_requeued(journal):
    _mutation(journal).close_trade("pos_0")
    k = _kernel(journal, _Executor(fail={"UNI/USDT"}))
    assert k._drain_close_requests() == 0
    assert _queued(journal) == [], "a rejection must not retry forever"
    assert any("UNI" in m for m in k.notifier.sent)


def test_a_successful_close_is_announced(journal):
    _mutation(journal).close_trade("pos_0")
    k = _kernel(journal)
    k._drain_close_requests()
    assert any("UNI" in m for m in k.notifier.sent)


def test_an_empty_queue_costs_nothing(journal):
    k = _kernel(journal)
    assert k._drain_close_requests() == 0
    assert k.executor.closed == []


def test_a_corrupt_queue_does_not_stall_the_cycle(journal):
    journal.kv_set("close_requests", "not json")
    k = _kernel(journal)
    assert k._drain_close_requests() == 0
    assert _queued(journal) == []
