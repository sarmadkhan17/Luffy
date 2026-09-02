"""_try_enter must hand the executor the spec's stop, not the config's.

The unit tests pin _protection_for. This one walks the path that actually
runs live: decision -> attribution -> spec lookup -> ATR on the spec's frame
-> stop -> sizing -> executor. Every link in that chain was in place before;
the one that was wrong was invisible from either end.
"""
import numpy as np
import pandas as pd
import pytest

from trader.core.types import Action, MarketType
from trader.engine.exits import SpecExit
from trader.kernel import Kernel


def _bars(n, atr_pts, price=100.0):
    """Flat closes with a fixed true range, so ATR is exactly atr_pts."""
    return pd.DataFrame({
        "timestamp": np.arange(n) * 60000,
        "open": price, "close": price,
        "high": price + atr_pts / 2, "low": price - atr_pts / 2,
        "volume": 1.0,
    })


class _Snap:
    price = 100.0
    symbol = "BTC/USDT"

    def __init__(self, frames):
        self._f = frames

    def df(self, tf):
        return self._f.get(tf)


class _Sizing:
    ok = True
    reason = ""
    amount = 1.0
    size_usdt = 100.0
    risk_usdt = 1.0


class _Risk:
    sl_atr_mult = 2.5

    def __init__(self):
        self.seen_stop_frac = None

    def protection_levels(self, price, atr, side, tp):
        d = max(atr * self.sl_atr_mult, price * 0.004)
        return ((price - d, price + atr * tp) if side == "long"
                else (price + d, price - atr * tp))

    def check_entry(self, *a, **kw):
        self.seen_stop_frac = a[4] if len(a) > 4 else kw["side_risk_frac"]
        return _Sizing()


class _Exec:
    tp_atr_mult = 4.5

    def __init__(self):
        self.opened = None

    def open(self, d, amount, atr, sl, tp, **kw):
        self.opened = {"sl": sl, "tp": tp, "atr": atr, **kw}
        return object()


class _D:
    id = "d1"
    symbol = "BTC/USDT"
    skip_reason = ""
    meta_size = 1.0

    def __init__(self, action, sigs):
        self.action, self.strategy_signals = action, sigs


def _kernel(spec_exits, min_notional=10):
    k = Kernel.__new__(Kernel)
    k.cfg = {"timeframes": {"execution": "15m"},
             "risk": {"min_notional_usdt": min_notional}}
    k.risk, k.executor = _Risk(), _Exec()
    k._spec_exits = spec_exits
    k.market_type = MarketType.FUTURES

    class _J:
        def open_trades(self): return []
    class _SM:
        state = "ACTIVE"
    k.journal, k.state_machine = _J(), _SM()
    return k


_DONCHIAN = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                     has_target=False, stop_atr_mult=2.0)
_SIG = [{"strategy_id": "donchian", "action": "BUY", "confidence": 0.6}]


def test_the_stop_handed_to_the_executor_comes_off_the_4h_frame():
    k = _kernel({"donchian": _DONCHIAN})
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    assert k._try_enter(_D(Action.BUY, _SIG), snap, 5000.0, 0) is True
    # 2.0 x 8.0 = 16.0 below 100, not 2.5 x 0.4 = 1.0
    assert k.executor.opened["sl"] == pytest.approx(84.0)
    assert k.risk.seen_stop_frac == pytest.approx(0.16)


def test_a_no_target_spec_reaches_the_executor_with_no_target():
    k = _kernel({"donchian": _DONCHIAN})
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    k._try_enter(_D(Action.BUY, _SIG), snap, 5000.0, 0)
    assert k.executor.opened["tp"] == 0.0


def test_the_trade_is_filed_under_the_strategy_that_proposed_it():
    k = _kernel({"donchian": _DONCHIAN})
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    k._try_enter(_D(Action.BUY, _SIG), snap, 5000.0, 0)
    assert k.executor.opened["strategy_id"] == "donchian"


def test_a_trade_no_spec_owns_still_uses_the_execution_frame():
    k = _kernel({})                       # legacy genome, no spec
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    k._try_enter(_D(Action.BUY, _SIG), snap, 5000.0, 0)
    assert k.executor.opened["sl"] == pytest.approx(99.0)   # 2.5 x 15m ATR


def test_a_missing_spec_frame_refuses_the_entry_rather_than_guessing():
    """Sizing a 4h stop off 15m bars is how this broke. Do not fall back."""
    k = _kernel({"donchian": _DONCHIAN})
    d = _D(Action.BUY, _SIG)
    assert k._try_enter(d, _Snap({"15m": _bars(60, 0.4)}), 5000.0, 0) is False
    assert "4h" in d.skip_reason
    assert k.executor.opened is None


def test_a_short_gets_its_stop_above_the_price_on_the_spec_frame():
    k = _kernel({"donchian": _DONCHIAN})
    sig = [{"strategy_id": "donchian", "action": "SELL", "confidence": 0.6}]
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    assert k._try_enter(_D(Action.SELL, sig), snap, 5000.0, 0) is True
    assert k.executor.opened["sl"] == pytest.approx(116.0)
