"""What sets the return of a mechanism that already has an edge?

`portfolio_expectation` on Donchian Breakout Trail reads 9.6%/yr at a 15.8%
peak drawdown, and that number has been treated as a property of the
strategy. It is not. It is a property of a DEPLOYMENT: 16 symbols, one
timeframe, 0.5% of equity per trade, 8 concurrent slots. Three of those four
are configuration.

This walks the mechanism's real fills against one compounding balance under
the concurrency cap the live risk manager applies, and varies the deployment
around it. The question it answers is which knob the return is actually
pinned by:

  * if the cap REFUSES a large share of signals, the book is saturated and
    more symbols buy nothing — the cap is the binding constraint
  * if refusals are rare, the book is starved of signals and breadth is the
    lever, because a trend mechanism earns its Sharpe from the number of
    independent draws rather than from size per trade

Fills are taken over full history, not the walk-forward test half. That is
deliberate and it is NOT an edge claim: the edge was established elsewhere,
out of sample. Here the mechanism's trade stream is held fixed and only the
deployment around it changes, so every row is affected by any in-sample
optimism identically and the COMPARISON between rows stays honest.
"""
import json
import sqlite3
import statistics as st
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.strategy import spec_evidence
from trader.strategy.compile import compile_spec
from trader.strategy.portfolio_evidence import portfolio_curve
from trader.strategy.spec import StrategySpec
from trader.strategy.vector_backtest import funding_for, simulate

BAR_MS = {"4h": 4 * 3600, "1h": 3600, "15m": 900}


def load_spec(spec_id: str) -> StrategySpec:
    db = sqlite3.connect("data/luffy.db")
    row = db.execute("select spec_json from strategies where id=?",
                     (spec_id,)).fetchone()
    if not row:
        raise SystemExit(f"no spec {spec_id}")
    return StrategySpec.from_dict(json.loads(row[0]))


def collect(spec: StrategySpec, symbols: list, cfg: dict, feed: DataFeed):
    """Every fill the mechanism takes, on ONE shared clock.

    `Fill.entry_i` is a per-symbol bar index and means nothing across
    symbols; the concurrency cap needs a common time axis or two trades on
    different symbols cannot be known to overlap. Each index is remapped onto
    a global grid of `timeframe` bars measured from the earliest bar seen.
    """
    tf = spec.timeframe
    compiled = compile_spec(spec)
    risk = spec_evidence.risk_for(cfg["risk"], tf)
    frames = {}
    for sym in symbols:
        df = feed.cached_ohlcv(sym, tf, limit=40000)
        if df is not None and len(df) >= 500:
            frames[sym] = df
    if not frames:
        raise SystemExit("no history")
    universe = {s: {tf: d} for s, d in frames.items()}
    btc = {tf: frames["BTC/USDT"]} if "BTC/USDT" in frames else None
    t0 = min(int(pd.Timestamp(d["ts"].iloc[0]).timestamp()) for d in frames.values())
    step = BAR_MS[tf]

    fills, per_symbol = [], {}
    for sym, df in frames.items():
        sf = spec_evidence.frames_for(df, tf)
        derivs = spec_evidence.load_derivs(sym, spec.data_requires)
        lo, sh = compiled.entries(sf, btc=btc, derivs=derivs,
                                  universe=universe, symbol=sym)
        out = []
        simulate(lo, sh, df, spec.exit, risk, symbol=sym, fills_out=out,
                 funding=funding_for(sym, df, risk))
        # `.astype("int64")` keeps the column's OWN resolution, and the
        # candle store holds datetime64[ms] while a 15m frame elsewhere may
        # hold ns. Dividing by a hardcoded 10**9 therefore silently produces
        # a clock that is off by 10^6 and a span that comes out negative.
        # Drop to a stated second resolution instead of guessing the unit.
        ts = (pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
                .astype("datetime64[s]").astype("int64").to_numpy())
        for f in out:
            fills.append(type(f)(
                entry_i=int((ts[f.entry_i] - t0) // step),
                exit_i=int((ts[f.exit_i] - t0) // step),
                r_multiple=f.r_multiple, symbol=sym))
        per_symbol[sym] = len(out)
    span = max(f.exit_i for f in fills) - min(f.entry_i for f in fills)
    span_years = (span * step) / (365.25 * 24 * 3600)
    assert span_years > 0, f"bad clock: span {span} bars"
    return fills, per_symbol, span_years


def row(fills, years, equity, risk_pct, max_open):
    c = portfolio_curve(fills, equity, risk_pct, max_open)
    offered = len(fills)
    cagr = ((c["final"] / equity) ** (1.0 / years) - 1.0) * 100.0 \
        if years > 0 and c["final"] > 0 else float("nan")
    # a curve that reaches the halt breaker is not a return, it is a stop

    return {"risk": risk_pct, "slots": max_open, "offered": offered,
            "taken": c["taken"],
            "refused_pct": 100.0 * (offered - c["taken"]) / offered if offered else 0.0,
            "cagr": cagr, "max_dd": c["max_dd_pct"], "final": c["final"]}


def main():
    spec_id = sys.argv[1] if len(sys.argv) > 1 else "auth_donchian_breakout_trail"
    cfg = load_config()
    feed = DataFeed()
    spec = load_spec(spec_id)
    declared = list((spec.universe or {}).get("include") or [])
    equity = 4900.0

    print(f"{spec.name} — {spec.timeframe}, {len(declared)} declared symbols\n")
    fills, per_symbol, years = collect(spec, declared, cfg, feed)
    print(f"{len(fills)} fills over {years:.1f} years "
          f"({len(fills)/years:.0f}/yr, {len(fills)/years/len(per_symbol):.1f} "
          f"per symbol per year)")
    wins = sum(1 for f in fills if f.r_multiple > 0)
    print(f"win rate {wins/len(fills):.1%}  mean R {st.mean(f.r_multiple for f in fills):+.3f}"
          f"  median R {st.median(f.r_multiple for f in fills):+.3f}"
          f"  best R {max(f.r_multiple for f in fills):.1f}\n")

    print("DEPLOYMENT FRONTIER — one compounding account, concurrency capped")
    print(f"  {'risk%':>6} {'slots':>6} {'offered':>8} {'taken':>7} "
          f"{'refused':>8} {'CAGR':>8} {'maxDD':>7} {'ret/DD':>7} {'final$':>9}")
    for risk_pct in (0.5, 0.75, 1.0):
        for slots in (4, 8, 12, 16, 24):
            r = row(fills, years, equity, risk_pct, slots)
            rd = r["cagr"] / r["max_dd"] if r["max_dd"] > 0 else float("nan")
            print(f"  {r['risk']:>6.2f} {r['slots']:>6} {r['offered']:>8} "
                  f"{r['taken']:>7} {r['refused_pct']:>7.1f}% {r['cagr']:>7.1f}% "
                  f"{r['max_dd']:>6.1f}% {rd:>7.2f} {r['final']:>9,.0f}")

    print("\nBREADTH — the same mechanism, the same risk, more symbols")
    print(f"  {'symbols':>8} {'offered':>8} {'taken':>7} {'refused':>8} "
          f"{'CAGR':>8} {'maxDD':>7} {'ret/DD':>7}")
    order = sorted(per_symbol, key=lambda s: -per_symbol[s])
    for k in (4, 8, 12, len(order)):
        keep = set(order[:k])
        sub = [f for f in fills if f.symbol in keep]
        if not sub:
            continue
        r = row(sub, years, equity, 0.5, 8)
        rd = r["cagr"] / r["max_dd"] if r["max_dd"] > 0 else float("nan")
        print(f"  {k:>8} {r['offered']:>8} {r['taken']:>7} "
              f"{r['refused_pct']:>7.1f}% {r['cagr']:>7.1f}% "
              f"{r['max_dd']:>6.1f}% {rd:>7.2f}")


if __name__ == "__main__":
    main()
