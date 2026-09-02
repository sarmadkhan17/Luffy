"""The fixed Donchian-breakout-trail mechanism, run on arbitrary OHLC frames.

Entry / exit / null are the repo's own code paths:
  entries  - identical to trader.strategy.features.donchian_hi/lo
             (rolling(n).max().shift(1): the bar's own high cannot define
             the level it breaks)
  fills    - trader.strategy.vector_backtest.simulate
  null     - trader.strategy.null_baseline.null_pfs / consistency_p
Nothing here is tuned; the geometry is read off auth_donchian_breakout_trail.
"""
import sys
sys.path.insert(0, "/home/sarmad/trader")

import numpy as np
import pandas as pd

from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate, WARMUP
from trader.strategy import null_baseline

# geometry, verbatim from the live spec's exit block
EXIT = ExitSpec(stop={"kind": "atr", "mult": 2.0},
                target={"kind": "none"},
                trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
                time={"max_bars": 500},
                signal_exit="")


def entries(df, n):
    hi = df["high"].rolling(int(n)).max().shift(1)
    lo = df["low"].rolling(int(n)).min().shift(1)
    c = df["close"]
    return (c > hi).to_numpy(bool), (c < lo).to_numpy(bool)


def risk_cfg(fee_pct, slip_atr_frac, bar_minutes, funding_8h=0.0):
    return {"taker_fee_pct": fee_pct, "slippage_atr_frac": slip_atr_frac,
            "risk_per_trade_pct": 0.5, "funding_rate_8h": funding_8h,
            "bar_minutes": bar_minutes, "real_funding": False}


def run_one(df, n, cfg, draws=200, seed=0, symbol="X", funding=None):
    lo, sh = entries(df, n)
    r = simulate(lo, sh, df, EXIT, cfg, symbol=symbol, funding=funding)
    if r.trades == 0:
        return {"trades": 0, "pf": float("nan"), "pctile": None,
                "wr": float("nan"), "pnl": 0.0, "null_median": float("nan")}
    pfs = null_baseline.null_pfs(lo, sh, df, EXIT, cfg, draws=draws, seed=seed,
                                 symbol=symbol, funding=funding)
    pct = null_baseline.edge_percentile(float(r.profit_factor), pfs) \
        if len(pfs) >= null_baseline.MIN_DRAWS else None
    return {"trades": r.trades, "pf": float(r.profit_factor),
            "wr": r.wins / r.trades, "pnl": float(r.pnl_usdt),
            "pctile": pct, "draws": len(pfs),
            "null_median": float(np.median(pfs)) if pfs else float("nan"),
            "maxdd": float(r.max_dd_pct)}
