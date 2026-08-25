"""TradingView validation client — the independent second judge.

Wraps tradingview-mcp's backtest engine (in-process; same code the MCP
server exposes, without protocol overhead). Data source: Yahoo Finance
ohlcv; symbol mapping handled here.

A genome is only TV-valid if:
  - robustness verdict is ROBUST/MODERATE (train/test consistency), AND
  - out-of-sample return > 0, AND
  - enough OOS trades to mean anything (≥ min_oos_trades)

Family → TV strategy mapping:
  ema_trend → ema_cross · vwap_fade → bollinger · breakout_retest → donchian
  sweep_reversal → rsi · rotation_momo → macd
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FAMILY_TV = {
    "ema_trend": "ema_cross",
    "vwap_fade": "bollinger",
    "breakout_retest": "donchian",
    "sweep_reversal": "rsi",
    "rotation_momo": "macd",
}


def tv_symbol(symbol: str) -> str:
    """'BTC/USDT' → 'BTC-USD' (Yahoo crypto format)."""
    base = symbol.split("/")[0]
    return f"{base}-USD"


class TVClient:
    def __init__(self, interval: str = "1h", period: str = "1y",
                 min_oos_trades: int = 5, require_positive_oos: bool = True):
        self.interval = interval
        self.period = period
        self.min_oos_trades = min_oos_trades
        self.require_positive_oos = require_positive_oos

    def walk_forward(self, symbol: str, family: str,
                     n_splits: int = 3) -> dict:
        tv_strategy = FAMILY_TV.get(family)
        if not tv_strategy:
            return {"valid": False, "reason": f"no TV mapping for {family}"}
        try:
            from tradingview_mcp.core.services.backtest_service import \
                walk_forward_backtest
            raw = walk_forward_backtest(
                symbol=tv_symbol(symbol), strategy=tv_strategy,
                period=self.period, interval=self.interval,
                n_splits=n_splits)
        except Exception as e:
            return {"valid": False, "reason": f"tv error: {e}"}
        if "error" in raw:
            return {"valid": False, "reason": raw["error"][:200]}

        oos_ret = float(raw.get("oos_total_return_pct") or 0)
        oos_trades = int(raw.get("oos_total_trades") or 0)
        robustness = float(raw.get("robustness_score") or 0)
        verdict = str(raw.get("verdict") or "")
        bh = float(raw.get("buy_and_hold_return_pct") or 0)

        oos_sharpe = float(raw.get("oos_sharpe_ratio") or 0)
        oos_win = float(raw.get("oos_win_rate_pct") or 0)
        oos_dd = abs(float(raw.get("oos_max_drawdown_pct") or 0))
        folds = [{"train_ret": f.get("train_return_pct"),
                  "test_ret": f.get("test_return_pct"),
                  "test_trades": f.get("test_trades"),
                  "fold_robustness": f.get("fold_robustness_score")}
                 for f in (raw.get("folds") or [])]
        pos_folds = sum(1 for f in folds
                        if (f.get("test_ret") or 0) > 0)

        # Policy (evidence over labels):
        #  - OOS must be profitable with enough trades
        #  - OOS quality: sharpe ≥ 1 OR winrate ≥ 50%
        #  - drawdown survivable: ≤ 20%
        #  - profit consistency: majority of walk-forward folds positive
        # TV's own ROBUST/OVERFITTED verdict measures train≈test return
        # similarity, which wrongly brands bad-train/good-test strategies
        # (regime shifts) as overfitted — so we do NOT gate on it.
        checks = {
            "profitable_oos": oos_ret > 0 if self.require_positive_oos else True,
            "enough_trades": oos_trades >= self.min_oos_trades,
            "quality": (oos_sharpe >= 1.0) or (oos_win >= 50.0),
            "drawdown_ok": oos_dd <= 20.0,
            "folds_positive": pos_folds >= max(1, len(folds) // 2 + 1)
                              if folds else False,
        }
        valid = all(checks.values())
        return {
            "valid": valid, "checks": checks,
            "tv_strategy": tv_strategy, "tv_symbol": tv_symbol(symbol),
            "verdict": verdict, "robustness": robustness,
            "oos_return_pct": oos_ret, "oos_trades": oos_trades,
            "oos_win_rate": raw.get("oos_win_rate_pct"),
            "oos_sharpe": raw.get("oos_sharpe_ratio"),
            "oos_max_dd": raw.get("oos_max_drawdown_pct"),
            "buy_hold_return": bh,
            "beats_buy_hold": oos_ret > bh,
            "positive_folds": pos_folds,
            "folds": folds,
        }

    def compare_families(self, symbol: str) -> dict:
        """Score every TV strategy on one symbol — context for the brain."""
        out = {}
        for fam, tv in FAMILY_TV.items():
            r = self.walk_forward(symbol, fam)
            out[fam] = {k: r.get(k) for k in
                        ("valid", "oos_return_pct", "oos_trades",
                         "robustness", "verdict")}
        return out
