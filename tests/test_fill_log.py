"""`simulate` can hand back the individual trades it took.

`portfolio_evidence.portfolio_curve` has existed with zero callers because
nothing could produce its input: `BacktestResult` reports only aggregates, so
there was no way to walk one mechanism's trades across symbols against a
single compounding balance. Per-symbol arithmetic divides the profit across N
notional accounts and an uncapped shared account ignores the concurrency cap,
and neither is the number a real account earns.

The sink is opt-in and append-only, so a run that does not ask for it is
byte-identical to before.
"""
import numpy as np
import pandas as pd

from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate

RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.05,
        "slippage_atr_frac": 0.06, "funding_rate_8h": 0.0001,
        "bar_minutes": 240}
GEO = ExitSpec(stop={"kind": "atr", "mult": 2.0}, target={"kind": "none"},
               trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
               time={"max_bars": 60})


def _frame(n=800):
    rng = np.random.default_rng(11)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.005, n))
    return pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
        "open": close, "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": np.full(n, 1000.0)})


def _signals(n):
    lo = np.zeros(n, dtype=bool); sh = np.zeros(n, dtype=bool)
    lo[np.arange(60, n - 90, 50)] = True
    sh[np.arange(85, n - 90, 50)] = True
    return lo, sh


def test_one_record_per_trade():
    df = _frame(); lo, sh = _signals(len(df))
    fills = []
    r = simulate(lo, sh, df, GEO, RISK, fills_out=fills)
    assert r.trades > 0
    assert len(fills) == r.trades


def test_a_fill_carries_the_bars_it_spanned_and_its_r_multiple():
    df = _frame(); lo, sh = _signals(len(df))
    fills = []
    simulate(lo, sh, df, GEO, RISK, symbol="ETH/USDT", fills_out=fills)
    for f in fills:
        assert 0 <= f.entry_i < f.exit_i < len(df)
        assert f.symbol == "ETH/USDT"
        assert np.isfinite(f.r_multiple)
    # a 2-ATR stop with a 4-ATR trail cannot lose much beyond 1R per trade
    assert min(f.r_multiple for f in fills) > -1.6


def test_r_multiples_reconstruct_the_reported_pnl():
    """Each fill's R times the equity risked at its entry must sum to the
    account's total, or the portfolio walk is measuring something else."""
    df = _frame(); lo, sh = _signals(len(df))
    fills = []
    r = simulate(lo, sh, df, GEO, RISK, equity=2000.0, fills_out=fills)
    eq, total = 2000.0, 0.0
    for f in fills:
        pnl = eq * (RISK["risk_per_trade_pct"] / 100.0) * f.r_multiple
        total += pnl
        eq += pnl
    assert abs(total - r.pnl_usdt) < 0.01 * max(1.0, abs(r.pnl_usdt))


def test_the_sink_is_opt_in():
    df = _frame(); lo, sh = _signals(len(df))
    a = simulate(lo, sh, df, GEO, RISK)
    fills = []
    b = simulate(lo, sh, df, GEO, RISK, fills_out=fills)
    assert (a.trades, a.pnl_usdt, a.gross_win) == \
           (b.trades, b.pnl_usdt, b.gross_win)
