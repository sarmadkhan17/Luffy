"""Does the book's strategy work on symbols it has never been measured on?

Donchian Breakout Trail declares 16 symbols. Its evidence — median PF 1.42,
cross-symbol null consistency p=3.5e-04 — was measured on those 16, and a
mechanism's evidence is a description of the symbols it was found on until it
is shown somewhere else. `screen_mechanisms.py` learned that from
`momo_persist`, which scored p=1.1e-03 on its discovery universe and p=0.27
on nine symbols it had never seen.

The candle store now carries 32+ crypto perps at 4h, so the same test can
finally be run on the book's own strategy: sixteen symbols it has never been
scored against.

Two questions, and they are separate:

  1. EVIDENCE — does the null consistency hold on the unseen half? If it
     collapses the way momo_persist did, the strategy is a description of its
     sixteen symbols and the live book should not be widened.
  2. DEPLOYMENT — does trading the wider universe compound better? More
     symbols is more signals into the SAME eight slots, so it is not free:
     it can crowd out good trades as easily as add them.
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

EQUITY = 4900.0


def candidates(feed, tf, min_bars=3000):
    db = sqlite3.connect("data/candles.db")
    rows = [r[0] for r in db.execute(
        "select symbol, count(*) n from candles where tf=? group by symbol "
        "having n >= ? order by n desc", (tf, min_bars))]
    # tokenized equities and commodities are not crypto perps and are not
    # what this mechanism was built on; the venue also lists them recently
    bad = ("XAU", "XAG", "CL", "SOXL", "SNDK", "MU", "SPCX", "MSTR", "CRCL",
           "NVDA", "META", "QQQ", "INTC", "SKHYNIX", "KORU", "TSLA")
    return [s for s in rows if s.split("/")[0] not in bad]


def measure(spec, symbols, cfg, feed, label):
    tf = spec.timeframe
    compiled = compile_spec(spec)
    risk = spec_evidence.risk_for(cfg["risk"], tf)
    frames = {}
    for s in symbols:
        d = feed.cached_ohlcv(s, tf, limit=40000)
        if d is not None and len(d) >= 500:
            frames[s] = d
    if not frames:
        print(f"\n{label}: no history"); return
    universe = {s: {tf: d} for s, d in frames.items()}
    btc = {tf: frames["BTC/USDT"]} if "BTC/USDT" in frames else None
    t0 = min(int(pd.Timestamp(d["ts"].iloc[0]).timestamp()) for d in frames.values())
    step = 4 * 3600

    pfs, pcts, trades, fills = [], [], 0, []
    for sym, df in frames.items():
        sf = spec_evidence.frames_for(df, tf)
        ctx = dict(btc=btc, universe=universe,
                   derivs=spec_evidence.load_derivs(sym, spec.data_requires))
        fund = funding_for(sym, df, risk)
        r = vector_walk_forward(compiled, sf, risk, symbol=sym, **ctx)
        trades += r["test"].trades
        if r["test"].trades >= 8:
            a = null_baseline.assess(compiled, sf, risk, r["test"].profit_factor,
                                     symbol=sym, draws=60, seed=17, split=0.7,
                                     part="test", funding=fund, **ctx)
            if a.get("percentile") is not None:
                pfs.append(r["test"].profit_factor); pcts.append(a["percentile"])
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
    p = consistency_p(pcts)
    years = ((max(f.exit_i for f in fills) - min(f.entry_i for f in fills))
             * step) / (365.25 * 24 * 3600) if fills else 0.0
    print(f"\n{label}  ({len(frames)} symbols, {years:.1f}y)")
    print(f"  scored {len(pcts)}   median PF {st.median(pfs) if pfs else float('nan'):.2f}"
          f"   median pctile {st.median(pcts) if pcts else float('nan'):.0%}"
          f"   above median {sum(1 for v in pcts if v > 0.5)}/{len(pcts)}")
    print(f"  consistency p = " + (f"{p:.2e}" if p is not None else "n/a")
          + f"   ->  {'PASS' if p is not None and p < 0.01 else 'REFUSED'}")
    for slots in (8, 12):
        c = portfolio_curve(fills, EQUITY, 0.5, slots)
        cagr = ((c["final"] / EQUITY) ** (1.0 / years) - 1.0) * 100.0 \
            if years > 0 and c["final"] > 0 else float("nan")
        print(f"  deploy 0.5%/{slots:<2}  offered {len(fills):>4}  taken {c['taken']:>4}"
              f"  refused {100*(len(fills)-c['taken'])/len(fills):>4.1f}%"
              f"  CAGR {cagr:>5.1f}%  maxDD {c['max_dd_pct']:>5.1f}%")


def main():
    cfg, feed = load_config(), DataFeed()
    db = sqlite3.connect("data/luffy.db")
    spec = StrategySpec.from_dict(json.loads(db.execute(
        "select spec_json from strategies where id=?",
        ("auth_donchian_breakout_trail",)).fetchone()[0]))
    declared = list((spec.universe or {}).get("include") or [])
    pool = candidates(feed, spec.timeframe)
    unseen = [s for s in pool if s not in declared]
    print(f"{spec.name}: {len(declared)} declared, {len(unseen)} never scored")
    print(f"unseen: {', '.join(s.split('/')[0] for s in unseen)}")
    measure(spec, declared, cfg, feed, "DECLARED — the evidence universe")
    measure(spec, unseen, cfg, feed, "UNSEEN — never used to justify anything")
    measure(spec, sorted(set(declared) | set(unseen)), cfg, feed, "BOTH")


if __name__ == "__main__":
    main()
