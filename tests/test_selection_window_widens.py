"""The selection window widens until it holds enough trades; decay never does.

90 days is 8,640 bars of 15m and 540 of 4h. A fixed calendar window therefore
asks a different question of each timeframe, and it asked the wrong one of the
only mechanism the system has measured as real: Donchian Breakout Trail takes
15 trades in 90 days across 16 symbols and was refused for "only 15 trades",
while a 15m spec with the same edge per bar clears the same bar easily.

Retirement must NOT widen. "Has this stopped working" is a question about the
recent past; pulling in last year's trades to answer it is the opposite of
what the gate is for.
"""
import pandas as pd

from trader.strategy import rolling


class _Compiled:
    """Stands in for a compiled spec; the window is what is under test."""
    class _Spec:
        id = "probe"
        exit = None
    spec = _Spec()

    def entries(self, *a, **kw):
        return None, None


def _frames(n=6000):
    return {"SYM/USDT": pd.DataFrame({"close": [1.0] * n})}


def _patch(monkeypatch, trades_by_window):
    """Report `trades_by_window[days]` trades for whatever window is asked."""
    seen = []

    def fake(compiled, frames, risk, tf, recent_days, btc=None,
             derivs_for=None):
        seen.append(recent_days)
        return {"stage": "recent", "recent_days": recent_days,
                "timeframe": tf, "per_symbol": {},
                "trades": trades_by_window(recent_days),
                "pooled_pf": 1.40, "winrate": 0.4, "pnl": 10.0}

    monkeypatch.setattr(rolling, "_score_window", fake)
    return seen


def test_a_window_holding_enough_trades_is_not_widened(monkeypatch):
    seen = _patch(monkeypatch, lambda d: 40)
    ok, ev = rolling.recent_verdict(_Compiled(), _frames(), {}, "15m")
    assert ok and seen == [90]
    assert ev["window_days"] == 90 and ev["window_widened"] is False


def test_it_doubles_until_the_trades_are_there(monkeypatch):
    """Donchian's shape: 15 trades at 90d, enough once the window doubles."""
    seen = _patch(monkeypatch, lambda d: 15 if d <= 90 else 33)
    ok, ev = rolling.recent_verdict(_Compiled(), _frames(), {}, "4h")
    assert ok is True
    assert seen == [90, 180]
    assert ev["window_days"] == 180 and ev["window_widened"] is True


def test_widening_stops_at_the_cap(monkeypatch):
    seen = _patch(monkeypatch, lambda d: 1)
    ok, ev = rolling.recent_verdict(_Compiled(), _frames(), {}, "4h")
    assert ok is False
    assert seen == [90, 180, 360, 365]
    assert ev["window_days"] == 365
    assert "365d" in ev["reason"]


def test_the_cap_keeps_this_a_recency_gate(monkeypatch):
    """A lifetime gate scored all 27 candidates at zero. 365 days is not one."""
    seen = _patch(monkeypatch, lambda d: 0)
    rolling.recent_verdict(_Compiled(), _frames(), {}, "4h", max_days=365)
    assert max(seen) == 365


def test_a_wider_window_still_has_to_clear_the_profit_factor(monkeypatch):
    def fake(compiled, frames, risk, tf, recent_days, btc=None,
             derivs_for=None):
        return {"stage": "recent", "recent_days": recent_days, "timeframe": tf,
                "per_symbol": {}, "trades": 5 if recent_days <= 90 else 30,
                "pooled_pf": 0.90, "winrate": 0.3, "pnl": -5.0}
    monkeypatch.setattr(rolling, "_score_window", fake)
    ok, ev = rolling.recent_verdict(_Compiled(), _frames(), {}, "4h")
    assert ok is False and "0.90" in ev["reason"]


def test_retirement_never_widens_its_window(monkeypatch):
    """The regression this guards: last year's trades cannot answer whether a
    spec has stopped working this month."""
    seen = _patch(monkeypatch, lambda d: 2 if d <= 30 else 50)
    decayed, ev = rolling.has_decayed(_Compiled(), _frames(), {}, "4h")
    assert seen == [30], "decay widened its window"
    assert decayed is False, "too few recent trades is idle, not decayed"
