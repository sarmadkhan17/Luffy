"""Does a filter improve the one mechanism that already has an edge?

The screen looks for NEW mechanisms and judges each against its own rotation
null. That is the right question for discovery and the wrong one for
refinement: a filter does not propose a new way to enter, it proposes that
some of the entries the book already takes are not worth taking. The test is
therefore a paired one — the same base rule, the same symbols, the same
geometry, with and without the condition.

Reported per variant, over the spec's OWN declared universe:

  PF      median out-of-sample profit factor across symbols
  p       cross-symbol consistency of the rotation-null percentiles, the
          number the admission gate uses (`select_null_max_p`)
  trades  total out-of-sample trades — a filter that improves PF by taking a
          quarter as many trades has usually just found a smaller sample
  CAGR    one compounding account over full history at 0.5% risk and 8 slots

A filter is only worth adding if it holds PF or p while keeping enough
trades to still be measurable. Cutting trade count to buy a better ratio is
how a mechanism gets curve-fitted to its own history.
"""
import json
import sqlite3
import statistics as st
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.strategy import null_baseline, spec_evidence
from trader.strategy.compile import compile_spec
from trader.strategy.null_baseline import consistency_p
from trader.strategy.portfolio_evidence import portfolio_curve
from trader.strategy.spec import StrategySpec
from trader.strategy.vector_backtest import (funding_for, simulate,
                                             vector_walk_forward)

# (name, extra long condition, extra short condition, data_requires)
VARIANTS = [
    ("baseline",     "",  "",  ()),
    # the screen's strongest held-out result: a breakout is worth less when
    # the crowd is already paying to hold that side
    ("carry",        "funding_z(360) < 1.0",  "funding_z(360) > -1.0", ("funding",)),
    ("carry_tight",  "funding_z(360) < 0.5",  "funding_z(360) > -0.5", ("funding",)),
    ("hivol",        "atr_pct_rank(200) > 0.6", "atr_pct_rank(200) > 0.6", ()),
    ("breadth",      "breadth(close > ema(50)) > 0.6",
                     "breadth(close > ema(50)) < 0.4", ()),
    ("rel_btc",      "rel_strength_btc(30) > 0", "rel_strength_btc(30) < 0", ()),
]


def build(base: StrategySpec, name, lo_x, sh_x, requires):
    d = base.to_dict()
    d["id"] = f"{base.id}__{name}"
    d["name"] = f"{base.name} [{name}]"
    if lo_x:
        d["entry_long"] = f"({base.entry_long}) and ({lo_x})"
        d["entry_short"] = f"({base.entry_short}) and ({sh_x})"
    d["data_requires"] = sorted({*base.data_requires, "ohlcv", *requires})
    return StrategySpec.from_dict(d)


def evaluate(spec, frames, cfg, feed):
    tf = spec.timeframe
    compiled = compile_spec(spec)
    risk = spec_evidence.risk_for(cfg["risk"], tf)
    universe = {s: {tf: d} for s, d in frames.items()}
    btc = {tf: frames["BTC/USDT"]} if "BTC/USDT" in frames else None
    t0 = min(int(pd.Timestamp(d["ts"].iloc[0]).timestamp()) for d in frames.values())
    step = 4 * 3600 if tf == "4h" else (3600 if tf == "1h" else 900)

    pfs, pcts, trades, fills = [], [], 0, []
    for sym, df in frames.items():
        sf = spec_evidence.frames_for(df, tf)
        derivs = spec_evidence.load_derivs(sym, spec.data_requires)
        ctx = dict(btc=btc, derivs=derivs, universe=universe)
        fund = funding_for(sym, df, risk)
        r = vector_walk_forward(compiled, sf, risk, symbol=sym, **ctx)
        trades += r["test"].trades
        if r["test"].trades >= 8:
            a = null_baseline.assess(compiled, sf, risk, r["test"].profit_factor,
                                     symbol=sym, draws=60, seed=17, split=0.7,
                                     part="test", funding=fund, **ctx)
            if a.get("percentile") is not None:
                pfs.append(r["test"].profit_factor)
                pcts.append(a["percentile"])
        lo, sh = compiled.entries(sf, symbol=sym, **ctx)
        out = []
        simulate(lo, sh, df, spec.exit, risk, symbol=sym, fills_out=out,
                 funding=fund)
        ts = (pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
                .astype("datetime64[s]").astype("int64").to_numpy())
        for f in out:
            fills.append(type(f)(entry_i=int((ts[f.entry_i] - t0) // step),
                                 exit_i=int((ts[f.exit_i] - t0) // step),
                                 r_multiple=f.r_multiple, symbol=sym))
    eq = 4900.0
    years = ((max(f.exit_i for f in fills) - min(f.entry_i for f in fills))
             * step) / (365.25 * 24 * 3600) if fills else 0.0
    c = portfolio_curve(fills, eq, 0.5, 8) if fills else {"final": eq, "max_dd_pct": 0}
    cagr = ((c["final"] / eq) ** (1.0 / years) - 1.0) * 100.0 \
        if years > 0 and c["final"] > 0 else float("nan")
    return {"pf": st.median(pfs) if pfs else float("nan"),
            "p": consistency_p(pcts), "n_sym": len(pcts), "trades": trades,
            "fills": len(fills), "cagr": cagr, "dd": c["max_dd_pct"]}


def main():
    spec_id = sys.argv[1] if len(sys.argv) > 1 else "auth_donchian_breakout_trail"
    cfg, feed = load_config(), DataFeed()
    db = sqlite3.connect("data/luffy.db")
    base = StrategySpec.from_dict(json.loads(db.execute(
        "select spec_json from strategies where id=?", (spec_id,)).fetchone()[0]))
    tf = base.timeframe
    frames = {}
    for sym in (base.universe or {}).get("include") or []:
        d = feed.cached_ohlcv(sym, tf, limit=40000)
        if d is not None and len(d) >= 500:
            frames[sym] = d
    print(f"{base.name} — {tf}, {len(frames)} symbols\n")
    print(f"  {'variant':<13} {'PF':>5} {'null p':>9} {'syms':>5} "
          f"{'OOS trades':>11} {'fills':>6} {'CAGR':>7} {'maxDD':>7}")
    for name, lo_x, sh_x, req in VARIANTS:
        spec = build(base, name, lo_x, sh_x, req)
        try:
            r = evaluate(spec, frames, cfg, feed)
        except Exception as e:
            print(f"  {name:<13} FAILED: {e}")
            continue
        p = f"{r['p']:.1e}" if r["p"] is not None else "n/a"
        print(f"  {name:<13} {r['pf']:>5.2f} {p:>9} {r['n_sym']:>5} "
              f"{r['trades']:>11} {r['fills']:>6} {r['cagr']:>6.1f}% "
              f"{r['dd']:>6.1f}%", flush=True)


if __name__ == "__main__":
    main()
