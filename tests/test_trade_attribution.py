"""A trade belongs to the strategy that proposed its direction.

`max(strategy_signals, key=confidence)` ignored which way each signal
pointed, so a higher-confidence signal on the OPPOSITE side took the credit —
and with it the P&L that drives that strategy's decay gate, its promotion
arithmetic and its weight in the next score.

Inert while one strategy is in the book; wrong the moment a second arrives,
which is exactly what the book is being built to accept.
"""
from trader.core.types import Action
from trader.kernel import Kernel


class _D:
    def __init__(self, action, sigs):
        self.action, self.strategy_signals = action, sigs


def _sig(sid, action, conf):
    return {"strategy_id": sid, "action": action, "confidence": conf}


_top = Kernel._top_strategy


def test_the_only_agreeing_signal_wins():
    d = _D(Action.BUY, [_sig("donchian", "BUY", 0.6)])
    assert _top(None, d) == "donchian"


def test_a_louder_signal_on_the_other_side_takes_no_credit():
    """The regression: 0.9 SELL outranked 0.6 BUY on a BUY trade."""
    d = _D(Action.BUY, [_sig("fader", "SELL", 0.9),
                        _sig("donchian", "BUY", 0.6)])
    assert _top(None, d) == "donchian"


def test_the_loudest_of_the_agreeing_signals_wins():
    d = _D(Action.SELL, [_sig("quiet", "SELL", 0.4),
                         _sig("loud", "SELL", 0.8),
                         _sig("wrong_way", "BUY", 0.99)])
    assert _top(None, d) == "loud"


def test_no_agreeing_signal_attributes_to_nobody():
    """The gate should have refused this entry; if one arrives anyway it is
    not silently filed under a strategy that never proposed it."""
    d = _D(Action.BUY, [_sig("fader", "SELL", 0.9)])
    assert _top(None, d) == ""


def test_no_signals_at_all_attributes_to_nobody():
    assert _top(None, _D(Action.BUY, [])) == ""
    assert _top(None, _D(Action.BUY, None)) == ""
