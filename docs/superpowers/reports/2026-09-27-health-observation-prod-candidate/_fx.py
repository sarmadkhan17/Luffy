import numpy as np
import pandas as pd
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(sid="h1", name="Health Probe", entry="close > ema(5)", **kw):
    base = dict(
        id=sid, name=name,
        thesis="A hypothesis of sufficient length for the validator, naming a "
               "plausible short-horizon inefficiency that a rolling gate can "
               "confirm or refute on recent data.",
        invalidation="Retire below profit factor 0.85 over the last 30 days.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="1h", direction="long", entry_long=entry, entry_short="",
        filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 24}),
        regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _frame(n=4000, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.006, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
        "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]


CFG = {"risk": {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
                "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
                "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
                "bar_minutes": 60},
       "strategies": {"decay_recent_days": 60, "decay_min_trades": 3,
                      "decay_floor_pf": 0.85}}
THRESHOLDS = {"decay_recent_days": 60.0, "decay_min_trades": 3,
              "decay_floor_pf": 0.85}

UP = _frame(4000, drift=0.004, seed=2)
DOWN = _frame(4000, drift=-0.004, seed=9)
RARE = "close > ema(200) and rsi(2) < 1"
