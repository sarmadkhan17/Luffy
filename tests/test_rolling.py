import numpy as np
import pandas as pd
import pytest

from trader.strategy import rolling
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec

RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
        "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
        "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
        "bar_minutes": 60}


def _spec(**kw):
    base = dict(
        id="r1", name="Rolling Probe",
        thesis="A hypothesis of sufficient length to satisfy the validator, "
               "describing a plausible short-horizon inefficiency worth "
               "testing across regimes.",
        invalidation="Retire below profit factor 1.0 over 30 trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="1h", direction="long", entry_long="close > ema(5)",
        entry_short="", filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 24}),
        regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _frame(n, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.006, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
        "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]


def test_bars_converts_days_by_timeframe():
    assert rolling.bars("1h", 60) == 1440
    assert rolling.bars("15m", 1) == 96
    assert rolling.bars("4h", 10) == 60


def test_rolling_windows_produces_many_independent_scores():
    c = compile_spec(_spec())
    st = rolling.rolling_windows(c, {"A": _frame(8000)}, RISK, "1h",
                                 window_days=60, step_days=15)
    assert st.n_windows > 5
    assert len(st.pfs) == st.n_windows == len(st.trades)
    assert 0.0 <= st.hit_rate <= 1.0


def test_window_stats_are_empty_safe():
    st = rolling.WindowStats()
    assert st.hit_rate == 0.0 and st.median_pf == 0.0 and st.total_trades == 0
    assert st.as_dict()["windows"] == 0


def test_rolling_ignores_windows_below_min_trades():
    c = compile_spec(_spec(entry_long="close > ema(200) and rsi(2) < 1"))
    st = rolling.rolling_windows(c, {"A": _frame(6000)}, RISK, "1h",
                                 min_trades=8)
    assert st.n_windows == 0, "a silent spec must score no windows, not zeros"


def test_recent_verdict_scores_only_the_recent_window():
    """A spec that worked long ago and stopped must be judged on now."""
    good = _frame(4000, drift=0.004, seed=2)
    bad = _frame(4000, drift=-0.004, seed=3)
    hist = pd.concat([good, bad]).reset_index(drop=True)
    hist["ts"] = pd.date_range("2024-01-01", periods=len(hist), freq="1h",
                               tz="UTC")
    c = compile_spec(_spec())
    ok_recent, ev = rolling.recent_verdict(c, {"A": hist}, RISK, "1h",
                                           recent_days=60, min_trades=1)
    ok_all, ev_all = rolling.recent_verdict(c, {"A": hist}, RISK, "1h",
                                            recent_days=len(hist) / 24,
                                            min_trades=1)
    assert ev["recent_days"] == 60
    assert ev["trades"] <= ev_all["trades"]


def test_recent_verdict_pools_across_symbols():
    """A regime edge often lives on two instruments and is absent on the
    rest; absence is not failure, so the gate pools rather than taking the
    worst symbol."""
    c = compile_spec(_spec())
    frames = {"A": _frame(3000, 0.004, 4), "B": _frame(3000, -0.004, 5)}
    ok, ev = rolling.recent_verdict(c, frames, RISK, "1h", recent_days=60,
                                    min_trades=1)
    assert set(ev["per_symbol"]) == {"A", "B"}
    assert ev["trades"] == sum(v["trades"] for v in ev["per_symbol"].values())


def test_recent_verdict_rejects_too_few_trades():
    c = compile_spec(_spec())
    ok, ev = rolling.recent_verdict(c, {"A": _frame(3000)}, RISK, "1h",
                                    recent_days=60, min_trades=100000)
    assert not ok and "trades" in ev["reason"]


def test_decay_flags_a_dead_strategy():
    c = compile_spec(_spec())
    dying = _frame(3000, drift=-0.004, seed=7)
    dead, ev = rolling.has_decayed(c, {"A": dying}, RISK, "1h",
                                   recent_days=60, min_trades=3)
    assert dead and "decayed" in ev["verdict"]


def test_idle_is_not_decay():
    """A setup that has gone quiet is idle, not broken. Retiring on silence
    would delete strategies waiting for their regime."""
    c = compile_spec(_spec(entry_long="close > ema(200) and rsi(2) < 1"))
    dead, ev = rolling.has_decayed(c, {"A": _frame(3000)}, RISK, "1h",
                                   recent_days=30, min_trades=10)
    assert not dead and "idle" in ev["verdict"]


def test_working_strategy_is_not_retired():
    c = compile_spec(_spec())
    dead, ev = rolling.has_decayed(c, {"A": _frame(3000, drift=0.004, seed=8)},
                                   RISK, "1h", recent_days=60, min_trades=3)
    assert not dead
