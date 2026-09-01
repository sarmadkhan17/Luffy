"""The universe has to actually arrive, or cross-sectional features are NaN."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.types import Snapshot
from trader.strategy.spec import ExitSpec


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def test_snapshot_carries_a_universe_and_defaults_to_empty():
    snap = Snapshot(symbol="BTC/USDT", ts="2026-01-01T00:00:00+00:00",
                    price=100.0, dfs={"15m": _df(np.ones(5))})
    assert snap.universe in (None, {})


def test_rolling_passes_the_universe_into_entries(monkeypatch):
    """recent_verdict must hand every symbol's frame to the evaluator."""
    from trader.strategy import rolling

    seen = {}

    class _Spec:
        exit = ExitSpec()

    class _Compiled:
        spec = _Spec()

        def entries(self, frames, btc=None, derivs=None, universe=None,
                    market=None, symbol=None):
            seen["universe"] = universe
            seen["symbol"] = symbol
            n = len(next(iter(frames.values())))
            return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

    risk = {"risk_per_trade_pct": 1.0, "taker_fee_pct": 0.05,
            "slippage_atr_frac": 0.06, "funding_rate_8h": 0.0001,
            "bar_minutes": 15, "stop_loss_atr_mult": 2.5,
            "take_profit_atr_mult": 4.5}
    frames = {"BTC/USDT": _df(np.ones(400)), "ETH/USDT": _df(np.ones(400))}
    rolling.recent_verdict(_Compiled(), frames, risk, "15m",
                           recent_days=1, min_trades=0)

    assert seen["universe"] is not None
    assert set(seen["universe"]) == {"BTC/USDT", "ETH/USDT"}
    # so a cross-sectional feature can exclude the base symbol's own key
    # from its peer panel instead of assuming it is (or isn't) a member.
    assert seen["symbol"] in ("BTC/USDT", "ETH/USDT")
