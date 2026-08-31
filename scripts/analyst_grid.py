"""Analyst session: design genomes, optimize genes on stored candles,
rank by out-of-sample robustness. Output = gauntlet-ready candidates ranked
by min test PF (the same bar the internal gate uses).

CAUTION — this maximizes an out-of-sample statistic over the whole GRID
(currently 43 combos) with NO multiple-testing correction, so the winner's
PF is biased upward by selection. The trial count is printed and written
into the output so a later deflated-Sharpe / reality-check pass can price
it. Treat these as candidates, not as validated edges.
"""

import json
import sys

from trader.strategy.genome import Genome
from trader.strategy import evidence
from trader.strategy.backtest import context_frames, walk_forward
from trader.core.config import ROOT, load_config
from trader.data.feed import DataFeed

HYP = {
    "ema_trend": "Trend persistence: aligned EMA stacks signal institutional "
                 "flow direction; shallow pullbacks within strength offer "
                 "entry before continuation.",
    "ma_cross": "Moving-average crossovers proxy medium-term regime shifts "
                "with lag; crossing filters chop and rides sustained moves.",
    "breakout_retest": "Range breaks on volume mark initiative buying; the "
                       "retest confirms breakout validity before expansion.",
    "rsi_extreme": "Liquidation-driven wicks overshoot fair value; fading "
                   "fast-RSI extremes captures market-maker snap-back.",
    "sweep_reversal": "Stop-run sweeps beyond swing points trap late "
                      "positioning; reclaim reverses flow against trapped "
                      "traders.",
    "rotation_momo": "Altcoin rotation follows BTC impulse with a lag; "
                     "entering alts after BTC thrust captures spillover.",
    "vwap_fade": "Price deviates from anchored VWAP on one-sided flow; "
                 "fades target reversion as passive liquidity refills.",
    "bb_fade": "Band pierces mark volatility extremes; close back inside "
               "signals exhaustion and reversion toward the mean.",
}

GRID = {
    "ema_trend": [dict(adx_min=a, pullback_atr=p)
                  for a in (16, 22, 28) for p in (0.5, 0.9, 1.3)],
    "ma_cross": [dict(fast_len=f, slow_len=s)
                 for f in (10, 20) for s in (50, 100)],
    "breakout_retest": [dict(range_lookback=l, vol_mult=v, retest_atr=r)
                        for l in (36, 72) for v in (1.3, 2.0)
                        for r in (0.4, 0.8)],
    "rsi_extreme": [dict(rsi_len=l, os_level=o, ob_level=b)
                    for l in (7, 14) for o in (25, 30) for b in (70, 75)],
    "sweep_reversal": [dict(max_reclaim_bars=m, min_sweep_frac=f)
                       for m in (3, 6) for f in (0.002, 0.005)],
    "rotation_momo": [dict(btc_ret_1h_min=r, lag_lookback=l)
                      for r in (0.005, 0.015) for l in (4, 8)],
    "vwap_fade": [dict(z_entry=z, anchor_bars=a, max_hold_bars=h)
                  for z in (2.0, 2.8) for a in (72,) for h in (24, 48)],
    "bb_fade": [dict(bb_len=n, bb_k=k)
                for n in (20,) for k in (2.0, 2.5)],
}


def main() -> None:
    cfg = load_config()
    feed = DataFeed()
    # full cached history across the configured symbol set, with HTF/BTC
    # context attached — was 2 symbols x 2900 bars (~30d) and no context,
    # which left rotation_momo unable to emit a single signal.
    frames = evidence.load_frames(feed, cfg)
    btc_1h = frames.get("_btc_1h")
    dfs = {k: v for k, v in frames.items() if not k.startswith("_")}
    n_symbols = len(dfs)
    total_trials = sum(len(c) for c in GRID.values())
    bars = {k: len(v) for k, v in dfs.items()}
    print(f"symbols={list(dfs)} bars={bars} trials={total_trials}", flush=True)

    results = []
    for fam, combos in GRID.items():
        best = None
        for i, params in enumerate(combos):
            g = Genome(
                strategy_id=f"ana_{fam}_{i}", family=fam,
                hypothesis=HYP[fam],
                invalidation="Demote on PF<0.85 over 20 trades or 6 straight losses.",
                regime_filter=frozenset({"TRENDING_UP", "TRENDING_DOWN",
                                         "RANGING", "VOLATILE"}),
                markets=frozenset({"futures"}), params=params)
            pfs, trades = [], 0
            try:
                for sym, df in dfs.items():
                    r = walk_forward(g, df, cfg["risk"],
                                     ctx=context_frames(df, btc_1h))
                    if r["test"].trades >= 3:
                        pfs.append(r["test"].profit_factor)
                    trades += r["test"].trades
            except Exception as e:
                continue
            if len(pfs) == n_symbols and trades >= 10:
                score = min(pfs)
                if best is None or score > best[0]:
                    best = (score, params, pfs, trades)
        if best:
            score, params, pfs, trades = best
            results.append((score, fam, params, pfs, trades))
            print(f"{fam:16s} minPF={score:.3f} pfs={[round(x,2) for x in pfs]}"
                  f" trades={trades} {json.dumps(params)}", flush=True)
        else:
            print(f"{fam:16s} no combo passed floors", flush=True)

    results.sort(reverse=True)
    print("\n=== RANKED ===")
    for score, fam, params, pfs, trades in results[:6]:
        print(json.dumps({"family": fam, "params": params,
                          "min_test_pf": round(score, 3),
                          "pfs": [round(x, 3) for x in pfs],
                          "trades": trades}))
    # analyst_gauntlet.py reads data/analyst_candidates.json — the old
    # /tmp/opencode/ path meant this file was never actually consumed.
    out_path = ROOT / "data" / "analyst_candidates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump([{"family": f, "params": p,
                    "min_test_pf": round(score, 3), "trades": trades,
                    "selected_from_trials": total_trials,
                    "symbols": list(dfs)}
                   for score, f, p, _, trades in results[:6]], fh, indent=1)
    print(f"\nwrote {out_path} "
          f"(winners selected from {total_trials} uncorrected trials)")


if __name__ == "__main__":
    sys.exit(main())
