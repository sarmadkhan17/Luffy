import numpy as np
import pandas as pd
import pytest

from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate

RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
        "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
        "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
        "bar_minutes": 15}


def _ramp(n=400, drift=0.004):
    close = 100 * np.cumprod(np.full(n, 1 + drift))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


def _flat(n=400):
    close = np.full(n, 100.0)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.0005, "low": close * 0.9995,
        "close": close, "volume": np.full(n, 100.0)})


def test_no_entries_means_no_trades():
    df = _ramp()
    n = len(df)
    r = simulate(np.zeros(n, bool), np.zeros(n, bool), df, ExitSpec(), RISK)
    assert r.trades == 0 and r.pnl_usdt == 0.0


def test_long_in_uptrend_hits_target():
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    r = simulate(lo, np.zeros(n, bool), df,
                 ExitSpec(stop={"kind": "atr", "mult": 2.0},
                          target={"kind": "atr", "mult": 3.0},
                          trail={"kind": "none"}, time={"max_bars": 100}),
                 RISK)
    assert r.trades == 1 and r.wins == 1 and r.pnl_usdt > 0


def test_short_in_uptrend_hits_stop():
    df = _ramp()
    n = len(df)
    sh = np.zeros(n, bool); sh[250] = True
    r = simulate(np.zeros(n, bool), sh, df, ExitSpec(time={"max_bars": 100}),
                 RISK)
    assert r.trades == 1 and r.losses == 1 and r.pnl_usdt < 0


def test_only_one_position_at_a_time():
    """30 consecutive entry signals, and an exit geometry that keeps the
    first trade open across all of them, must produce exactly one trade."""
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250:280] = True
    r = simulate(lo, np.zeros(n, bool), df,
                 ExitSpec(target={"kind": "none"}, trail={"kind": "none"},
                          time={"max_bars": 100}), RISK)
    assert r.trades == 1, "overlapping entry bars must not stack positions"


def test_signals_after_an_exit_do_open_a_new_trade():
    """The flip side: once flat again, the next signal must be taken."""
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True; lo[300] = True
    r = simulate(lo, np.zeros(n, bool), df,
                 ExitSpec(target={"kind": "none"}, trail={"kind": "none"},
                          time={"max_bars": 10}), RISK)
    assert r.trades == 2


def test_time_exit_closes_the_trade():
    df = _flat()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(time={"max_bars": 10}),
                 RISK)
    assert r.trades == 1


def test_exit_spec_changes_the_result():
    """The whole point of putting exits in the spec: they must matter."""
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    tight = simulate(lo, np.zeros(n, bool), df,
                     ExitSpec(target={"kind": "atr", "mult": 1.0},
                              time={"max_bars": 100}), RISK)
    wide = simulate(lo, np.zeros(n, bool), df,
                    ExitSpec(target={"kind": "atr", "mult": 8.0},
                             time={"max_bars": 100}), RISK)
    assert tight.pnl_usdt != wide.pnl_usdt


def test_fees_and_funding_are_charged():
    """A zero-drift round trip must lose money to costs, never break even."""
    df = _flat()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(time={"max_bars": 20}),
                 RISK)
    assert r.trades == 1 and r.pnl_usdt < 0


def test_signal_exit_closes_early():
    df = _flat()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    ex = np.zeros(n, bool); ex[255] = True
    with_sig = simulate(lo, np.zeros(n, bool), df,
                        ExitSpec(time={"max_bars": 100}), RISK, exit_sig=ex)
    without = simulate(lo, np.zeros(n, bool), df,
                       ExitSpec(time={"max_bars": 100}), RISK)
    assert abs(with_sig.pnl_usdt) < abs(without.pnl_usdt)


def test_warmup_signals_are_ignored():
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[5] = True
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(), RISK)
    assert r.trades == 0


def _rise_then_crash(n=400, up_to=300):
    """Rises, then gives it all back. A monotonic ramp never retraces, so it
    can never trigger a trailing stop — the trail must be tested on a path
    that actually reverses."""
    close = np.empty(n)
    close[:up_to] = 100 * np.cumprod(np.full(up_to, 1.004))
    close[up_to:] = close[up_to - 1] * np.cumprod(
        np.full(n - up_to, 0.99))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


def test_trailing_stop_locks_in_profit():
    df = _rise_then_crash()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    no_trail = simulate(lo, np.zeros(n, bool), df,
                        ExitSpec(target={"kind": "none"},
                                 trail={"kind": "none"},
                                 time={"max_bars": 100}), RISK)
    trailed = simulate(lo, np.zeros(n, bool), df,
                       ExitSpec(target={"kind": "none"},
                                trail={"kind": "atr", "mult": 1.0,
                                       "arm_at_r": 0.5},
                                time={"max_bars": 100}), RISK)
    assert trailed.trades == 1 and no_trail.trades == 1
    assert trailed.pnl_usdt > no_trail.pnl_usdt, \
        "a trail must exit the reversal better than riding it to the stop"
