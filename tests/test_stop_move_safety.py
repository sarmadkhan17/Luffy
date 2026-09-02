"""Moving a stop must never leave the position unprotected.

`_move_stop` cancelled the existing stop and THEN placed the replacement. If
the create failed — rate limit, a momentary reject, a disconnect — the
position was left naked, and the logged message said "old stop may still
stand", which is the opposite of what happened: the cancel had already
succeeded.

Placing first and cancelling second inverts the failure: the worst case
becomes two live stops at the same reduceOnly size rather than none, and the
duplicate is cancelled on the next pass. A trailing strategy moves its stop
on almost every bar, so this window is entered constantly.
"""
import pytest

from trader.engine.exits import ExitEngine


class _Ex:
    def __init__(self, fail_create=False):
        self.fail_create = fail_create
        self.created, self.cancelled = [], []
        self._n = 0
    def create_order(self, sym, typ, side, amount, params=None):
        if self.fail_create:
            raise RuntimeError("exchange rejected the order")
        self._n += 1
        self.created.append((sym, params.get("stopLossPrice")))
        return {"id": f"new{self._n}"}
    def cancel_order(self, oid, sym):
        self.cancelled.append(oid)


class _Journal:
    def update_position_protection(self, *a, **k): pass
    def query(self, *a, **k): pass


def _engine(ex):
    e = object.__new__(ExitEngine)
    e.ex, e.journal = ex, _Journal()
    return e


def _trade():
    return {"id": "p1", "symbol": "BTC/USDT", "side": "long", "amount": 1.0,
            "sl_order_id": "old1", "stop_loss": 100.0, "take_profit": None}


def test_the_replacement_is_placed_before_the_old_one_is_cancelled():
    ex = _Ex()
    _engine(ex)._move_stop(_trade(), 105.0)
    assert ex.created and ex.cancelled == ["old1"]


def test_a_failed_placement_leaves_the_original_stop_alive():
    ex = _Ex(fail_create=True)
    t = _trade()
    _engine(ex)._move_stop(t, 105.0)
    assert ex.cancelled == [], "the working stop must not be cancelled"
    assert t["stop_loss"] == 100.0, "the trade must keep its old stop"
    assert t["sl_order_id"] == "old1"


def test_a_successful_move_updates_the_trade():
    ex = _Ex()
    t = _trade()
    _engine(ex)._move_stop(t, 105.0)
    assert t["stop_loss"] == 105.0 and t["sl_order_id"] == "new1"


def test_a_failed_cancel_still_leaves_the_new_stop_in_charge():
    """A stale duplicate is survivable; no stop is not."""
    ex = _Ex()
    ex.cancel_order = lambda oid, sym: (_ for _ in ()).throw(RuntimeError("gone"))
    t = _trade()
    _engine(ex)._move_stop(t, 105.0)
    assert t["stop_loss"] == 105.0 and t["sl_order_id"] == "new1"
