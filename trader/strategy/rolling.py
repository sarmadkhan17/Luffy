"""Rolling evaluation — the mechanism's measuring instrument.

No edge lasts. A single train/test split over years averages the regimes an
edge worked in with the ones it did not, and reports the mean — so a strategy
that was profitable in 74% of sixty-day windows scores 0.47 and looks dead.
That is a property of the instrument, not the strategy.

Measured on Luffy's own book (1h, 60-day windows, 15-day step):

  BTC Correlation Break Reversion  median PF 1.55 over 186 windows ... and
                                   ZERO trades in the last 180 days
  VWAP Extreme Fade                median PF 1.43 over 339 windows ... and
                                   losing on 4 of 5 symbols recently

Both are real edges that died. So selection asks "is this working NOW", and a
separate monitor asks "has this stopped" — which is what makes the population
rotate instead of ossify.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .vector_backtest import simulate

log = logging.getLogger(__name__)

BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}


@dataclass
class WindowStats:
    """How a spec behaved across many independent windows."""
    n_windows: int = 0
    trades: list = field(default_factory=list)
    pfs: list = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        """Fraction of windows with profit factor above 1."""
        return float(np.mean(np.array(self.pfs) > 1.0)) if self.pfs else 0.0

    @property
    def median_pf(self) -> float:
        return float(np.median(self.pfs)) if self.pfs else 0.0

    @property
    def p25_pf(self) -> float:
        return float(np.percentile(self.pfs, 25)) if self.pfs else 0.0

    @property
    def total_trades(self) -> int:
        return int(sum(self.trades))

    def as_dict(self) -> dict:
        return {"windows": self.n_windows, "hit_rate": round(self.hit_rate, 3),
                "median_pf": round(self.median_pf, 3),
                "p25_pf": round(self.p25_pf, 3),
                "total_trades": self.total_trades}


def bars(timeframe: str, days: float) -> int:
    return int(BARS_PER_DAY.get(timeframe, 96) * days)


def rolling_windows(compiled, frames: dict, risk_cfg: dict, timeframe: str,
                    window_days: float = 60, step_days: float = 15,
                    min_trades: int = 8, btc=None,
                    derivs_for=None) -> WindowStats:
    """Slide a window across every symbol's history and score each one.

    This is DIAGNOSIS, not a gate: it says how often and how reliably a
    mechanism has paid, which calibrates how aggressively to rotate.
    """
    win, step = bars(timeframe, window_days), bars(timeframe, step_days)
    st = WindowStats()
    for sym, df in frames.items():
        if sym.startswith("_") or df is None or len(df) < win:
            continue
        derivs = derivs_for(sym) if derivs_for else None
        try:
            lo, sh = compiled.entries({timeframe: df}, btc=btc, derivs=derivs)
        except Exception as e:
            log.warning(f"rolling {compiled.spec.id} {sym}: {e}")
            continue
        for a in range(0, len(df) - win, step):
            b = a + win
            r = simulate(lo[a:b], sh[a:b], df.iloc[a:b].reset_index(drop=True),
                         compiled.spec.exit, risk_cfg, symbol=sym)
            if r.trades >= min_trades:
                st.n_windows += 1
                st.trades.append(r.trades)
                st.pfs.append(r.profit_factor)
    return st


def recent_verdict(compiled, frames: dict, risk_cfg: dict, timeframe: str,
                   recent_days: float = 90, min_trades: int = 20,
                   min_pf: float = 1.15, btc=None,
                   derivs_for=None) -> tuple[bool, dict]:
    """Is this spec working NOW? The selection gate.

    Scored on the most recent `recent_days` only, pooled across symbols. A
    per-symbol worst-case rule is wrong here: a regime-specific edge often
    lives on two or three instruments and is simply absent on the rest, and
    absence is not failure. Pooling asks the question that matters — did this
    make money recently, across the book, after costs.
    """
    n = bars(timeframe, recent_days)
    per_symbol, gross_win, gross_loss, trades, wins = {}, 0.0, 0.0, 0, 0
    for sym, df in frames.items():
        if sym.startswith("_") or df is None or len(df) < n:
            continue
        recent = df.iloc[-n:].reset_index(drop=True)
        derivs = derivs_for(sym) if derivs_for else None
        try:
            lo, sh = compiled.entries({timeframe: recent}, btc=btc,
                                      derivs=derivs)
            r = simulate(lo, sh, recent, compiled.spec.exit, risk_cfg,
                         symbol=sym)
        except Exception as e:
            log.warning(f"recent {compiled.spec.id} {sym}: {e}")
            continue
        per_symbol[sym] = {"trades": r.trades, "pf": round(r.profit_factor, 3),
                           "pnl": round(r.pnl_usdt, 2),
                           "wr": round(r.winrate, 3)}
        gross_win += r.gross_win
        gross_loss += r.gross_loss
        trades += r.trades
        wins += r.wins

    pooled_pf = (gross_win / gross_loss) if gross_loss > 0 else (
        99.0 if gross_win > 0 else 0.0)
    ev = {"stage": "recent", "recent_days": recent_days,
          "timeframe": timeframe, "per_symbol": per_symbol,
          "pooled_pf": round(pooled_pf, 3), "trades": trades,
          "winrate": round(wins / trades, 3) if trades else 0.0,
          "pnl": round(gross_win - gross_loss, 2)}
    if trades < min_trades:
        return False, {**ev, "reason": f"only {trades} trades in the last "
                                       f"{recent_days:.0f}d (<{min_trades})"}
    if pooled_pf < min_pf:
        return False, {**ev, "reason": f"recent pooled PF {pooled_pf:.2f} "
                                       f"< {min_pf}"}
    return True, ev


def has_decayed(compiled, frames: dict, risk_cfg: dict, timeframe: str,
                recent_days: float = 30, min_trades: int = 10,
                floor_pf: float = 0.85, btc=None,
                derivs_for=None) -> tuple[bool, dict]:
    """Has a deployed spec stopped working? The retirement trigger.

    Deliberately shorter and more lenient than selection: the cost of holding
    a dead strategy is continuous, the cost of retiring a live one is a
    re-test. Too few recent trades is NOT decay — a setup that has gone quiet
    is idle, and idleness is handled by the population cap, not by retirement.
    """
    ok, ev = recent_verdict(compiled, frames, risk_cfg, timeframe,
                            recent_days=recent_days, min_trades=min_trades,
                            min_pf=floor_pf, btc=btc, derivs_for=derivs_for)
    if ev["trades"] < min_trades:
        return False, {**ev, "verdict": "idle — too few trades to judge"}
    if not ok:
        return True, {**ev, "verdict": f"decayed: pooled PF "
                                       f"{ev['pooled_pf']:.2f} < {floor_pf}"}
    return False, {**ev, "verdict": "still working"}
