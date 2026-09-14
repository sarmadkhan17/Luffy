"""A window cut from a longer history warms on the bars before it.

WARMUP is a bar count. Handing `simulate` a bare window lost its first 210
bars, so a 4h 30-day decay window (180 bars) could never see a trade and a
live 4h strategy could never be retired for decay.
"""
import numpy as np
import pandas as pd

from trader.strategy import rolling
from trader.strategy.null_baseline import null_pfs
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import WARMUP, simulate, warm_window

RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
        "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
        "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
        "bar_minutes": 240}


def _ramp(n, drift=0.004):
    close = 100 * np.cumprod(np.full(n, 1 + drift))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


def test_warm_window_takes_up_to_warmup_bars_before_the_window():
    assert warm_window(1000, 1180) == (slice(790, 1180), WARMUP)
    assert warm_window(50, 230) == (slice(0, 230), 50)     # short history
    assert warm_window(0, 500) == (slice(0, 500), 0)


def test_score_from_opens_the_window_and_nothing_before_it():
    df = _ramp(600)
    n = len(df)
    before = np.zeros(n, bool); before[300] = True
    inside = np.zeros(n, bool); inside[420] = True
    none = np.zeros(n, bool)
    assert simulate(before, none, df, ExitSpec(), RISK, score_from=400).trades == 0
    assert simulate(inside, none, df, ExitSpec(), RISK, score_from=400).trades == 1


def test_score_from_never_goes_below_warmup():
    df = _ramp(400)
    lo = np.zeros(len(df), bool); lo[5] = True
    assert simulate(lo, np.zeros(len(df), bool), df, ExitSpec(), RISK,
                    score_from=0).trades == 0


def test_a_4h_30_day_decay_window_can_see_trades():
    df = _ramp(1200)

    class Compiled:
        class spec:
            id, exit = "t", ExitSpec()

        @staticmethod
        def entries(frames, **kw):
            n = len(frames["4h"])
            lo = np.zeros(n, bool)
            lo[-150::40] = True                  # inside the last 180 bars
            return lo, np.zeros(n, bool)

    ev = rolling._score_window(Compiled, {"X/USDT": df}, RISK, "4h", 30)
    assert rolling.bars("4h", 30) <= WARMUP + 1
    assert ev["trades"] > 0, "a 180-bar window scored nothing"


def test_a_null_with_fewer_rotations_than_draws_is_refused():
    df = _ramp(400)
    lo = np.zeros(len(df), bool); lo[380] = True
    none = np.zeros(len(df), bool)
    # 190 scored bars under a real prefix: 189 distinct rotations < 200 draws
    assert null_pfs(lo, none, df, ExitSpec(), RISK, draws=200,
                    score_from=WARMUP) == []
    # enough distinct rotations: the null is computed, one PF per trading draw
    pfs = null_pfs(lo, none, df, ExitSpec(), RISK, draws=50, score_from=WARMUP)
    assert 0 < len(pfs) <= 50
