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
  rsi_extreme → rsi · ma_cross → ema_cross · bb_fade → bollinger
  (crawler-era families reuse the closest built-in as prefilter proxy;
  the real-TV harness tests the family's actual Pine template)
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)

FAMILY_TV = {
    "ema_trend": "ema_cross",
    "vwap_fade": "bollinger",
    "breakout_retest": "donchian",
    "sweep_reversal": "rsi",
    "rotation_momo": "macd",
    "rsi_extreme": "rsi",
    "ma_cross": "ema_cross",
    "bb_fade": "bollinger",
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
        """Score every TV strategy on one symbol — context for the brain.

        Only VALID results are returned: transient upstream failures must
        not leak as all-families-invalid context (it biases the extractor
        into skipping every idea)."""
        out = {}
        for i, (fam, tv) in enumerate(FAMILY_TV.items()):
            if i:
                time.sleep(1.0)          # dodge upstream rate limits
            r = self.walk_forward(symbol, fam)
            if r.get("valid"):
                out[fam] = {k: r.get(k) for k in
                            ("valid", "oos_return_pct", "oos_trades",
                             "robustness", "verdict")}
            else:
                log.warning(f"compare_families {fam}: {r.get('reason')}")
        return out


def validate_population(journal, tv: TVClient, notifier=None,
                        symbol: str = "BTC/USDT") -> dict:
    """Run every live population strategy through the TV judge.

    FAIL → demoted (ineligible for entries; reversible if market regime
    changes and a future validation passes). PASS → stays eligible.
    Duplicate genomes (same family+params) collapse to the oldest.
    """
    results = {"passed": [], "demoted": [], "duplicates": []}
    seen_genomes: dict[str, str] = {}
    for row in journal.list_strategies(["paper", "active", "demoted"]):
        fam, params = row["kind"], json.dumps(row["params"], sort_keys=True)
        key = f"{fam}:{params}"
        if key in seen_genomes:
            journal.query("UPDATE strategies SET state='retired', "
                          "retire_reason=? WHERE id=?",
                          (f"duplicate of {seen_genomes[key]}", row["id"]))
            journal.log_brain_event("seed_tv_demoted", row["id"],
                                    {"reason": "duplicate genome",
                                     "of": seen_genomes[key]})
            results["duplicates"].append(row["name"])
            continue
        seen_genomes[key] = row["id"]

        r = tv.walk_forward(symbol, fam)
        passed = bool(r.get("valid"))
        evidence = {k: r.get(k) for k in
                    ("tv_strategy", "verdict", "checks", "oos_return_pct",
                     "oos_trades", "oos_win_rate", "oos_sharpe",
                     "oos_max_dd", "positive_folds", "buy_hold_return",
                     "beats_buy_hold")}
        if passed:
            journal.log_brain_event("seed_tv_passed", row["id"], evidence)
            results["passed"].append(row["name"])
        else:
            journal.query(
                "UPDATE strategies SET state='demoted', retire_reason=? "
                "WHERE id=?",
                (f"TV validation: {json.dumps(evidence)[:200]}", row["id"]))
            journal.log_brain_event("seed_tv_demoted", row["id"], evidence)
            results["demoted"].append(
                {"name": row["name"],
                 "oos_ret": evidence.get("oos_return_pct"),
                 "checks": evidence.get("checks")})

    journal.log_brain_event("population_validated", "tv",
                            {k: len(v) if isinstance(v, list) else v
                             for k, v in results.items()})
    if notifier and results["demoted"]:
        names = ", ".join(d["name"] for d in results["demoted"])
        notifier.send(f"⚖️ TV validation: demoted {names}. "
                      f"Passed: {len(results['passed'])}.")
    return results
