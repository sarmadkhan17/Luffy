"""The leverage the sizing math assumes must be the leverage the venue uses.

`risk.leverage` drove the margin figure (`notional / leverage`) and was
journalled on every trade, and `set_leverage` was never called. So the venue
traded at whatever the account happened to be set to, while the sizer
budgeted margin for 5x. If the venue sat at 1x, every position needed five
times the margin the risk manager thought it did — and with the position cap
now at 8 rather than 4, that gap is reachable.

Applied once per symbol and cached: Binance rejects a leverage change while a
position is open on that symbol, so re-sending it per order is both wasted
and a source of spurious errors.
"""
from trader.engine.executor import Executor


class _Ex:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
    def set_leverage(self, lev, symbol, params=None):
        if self.fail:
            raise RuntimeError("cannot change leverage with an open position")
        self.calls.append((lev, symbol))


def _exec(ex, leverage=5):
    from trader.core.types import MarketType
    e = object.__new__(Executor)
    e.ex, e.leverage = ex, leverage
    e.market_type = MarketType.FUTURES
    e._leverage_set = set()
    return e


def test_leverage_is_applied_to_the_venue():
    ex = _Ex()
    _exec(ex)._ensure_leverage("BTC/USDT")
    assert ex.calls == [(5, "BTC/USDT")]


def test_it_is_sent_once_per_symbol_not_once_per_order():
    ex = _Ex()
    e = _exec(ex)
    for _ in range(4):
        e._ensure_leverage("BTC/USDT")
    assert len(ex.calls) == 1


def test_each_symbol_gets_its_own_call():
    ex = _Ex()
    e = _exec(ex)
    e._ensure_leverage("BTC/USDT")
    e._ensure_leverage("ETH/USDT")
    assert sorted(s for _, s in ex.calls) == ["BTC/USDT", "ETH/USDT"]


def test_a_rejected_change_does_not_block_the_order():
    """Binance refuses a leverage change while a position is open on that
    symbol. That is expected, not a reason to skip the trade."""
    e = _exec(_Ex(fail=True))
    e._ensure_leverage("BTC/USDT")          # must not raise


def test_a_rejection_is_not_cached_as_success():
    ex = _Ex(fail=True)
    e = _exec(ex)
    e._ensure_leverage("BTC/USDT")
    ex.fail = False
    e._ensure_leverage("BTC/USDT")
    assert ex.calls == [(5, "BTC/USDT")], "a failed apply must be retried later"


def test_an_exchange_without_set_leverage_is_tolerated():
    class _Bare:
        pass
    _exec(_Bare())._ensure_leverage("BTC/USDT")     # must not raise
