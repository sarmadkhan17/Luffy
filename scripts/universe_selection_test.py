"""Was the declared universe chosen, or does the mechanism just work?

Donchian Breakout Trail scores p=3.5e-04 across its 16 declared symbols and
p=8.8e-01 across 19 comparable, liquid, multi-year perps it has never been
scored on. Two explanations fit that:

  A. the mechanism is real and the 16 are simply the markets it suits
  B. the 16 ARE the result — a set on which the mechanism happened to work,
     and evidence measured on them is evidence about the choosing

They are distinguishable. Score every liquid symbol in the pool ONCE, then
ask where the declared 16 sits in the distribution of random 16-symbol draws
from that same pool. Under (A) it is an ordinary draw. Under (B) it sits in
the extreme tail, because that is what selecting on the outcome does.

This is the same logic as the rotation null one level up: instead of asking
whether the entries beat their own rotation, it asks whether the UNIVERSE
beats a random universe of the same size.
"""
import json
import random
import sqlite3
import statistics as st
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.strategy import null_baseline, spec_evidence
from trader.strategy.compile import compile_spec
from trader.strategy.null_baseline import consistency_p
from trader.strategy.spec import StrategySpec
from trader.strategy.vector_backtest import funding_for, vector_walk_forward

DRAWS = 4000


def main():
    cfg, feed = load_config(), DataFeed()
    db = sqlite3.connect("data/luffy.db")
    spec = StrategySpec.from_dict(json.loads(db.execute(
        "select spec_json from strategies where id=?",
        ("auth_donchian_breakout_trail",)).fetchone()[0]))
    declared = list((spec.universe or {}).get("include") or [])
    # the pool: every symbol that clears the same liquidity and age bar the
    # declared universe clears. Drawing from a pool the declared set could
    # not have been drawn from would prove nothing.
    pool = sorted(set(declared) | {
        "ADA/USDT", "LTC/USDT", "TRX/USDT", "DOT/USDT", "BCH/USDT",
        "XLM/USDT", "XMR/USDT", "CRV/USDT", "ARB/USDT", "OP/USDT",
        "APT/USDT", "INJ/USDT", "ICP/USDT", "DASH/USDT", "FET/USDT",
        "WLD/USDT", "T/USDT", "1000PEPE/USDT", "1000SHIB/USDT"})

    tf = spec.timeframe
    compiled = compile_spec(spec)
    risk = spec_evidence.risk_for(cfg["risk"], tf)
    frames = {}
    for s in pool:
        d = feed.cached_ohlcv(s, tf, limit=40000)
        if d is not None and len(d) >= 500:
            frames[s] = d
    universe = {s: {tf: d} for s, d in frames.items()}
    btc = {tf: frames["BTC/USDT"]} if "BTC/USDT" in frames else None

    print(f"scoring {len(frames)} symbols once...", flush=True)
    score = {}
    for sym, df in frames.items():
        sf = spec_evidence.frames_for(df, tf)
        ctx = dict(btc=btc, universe=universe,
                   derivs=spec_evidence.load_derivs(sym, spec.data_requires))
        r = vector_walk_forward(compiled, sf, risk, symbol=sym, **ctx)
        if r["test"].trades < 8:
            continue
        a = null_baseline.assess(compiled, sf, risk, r["test"].profit_factor,
                                 symbol=sym, draws=60, seed=17, split=0.7,
                                 part="test", funding=funding_for(sym, df, risk),
                                 **ctx)
        if a.get("percentile") is not None:
            score[sym] = (r["test"].profit_factor, a["percentile"])

    have = [s for s in declared if s in score]
    others = [s for s in score if s not in declared]
    print(f"scored {len(score)}: {len(have)} declared, {len(others)} not\n")

    def stats(syms):
        pcts = [score[s][1] for s in syms]
        pfs = [score[s][0] for s in syms]
        return st.median(pfs), consistency_p(pcts) or 1.0, st.median(pcts)

    d_pf, d_p, d_pct = stats(have)
    print(f"DECLARED {len(have)}   median PF {d_pf:.2f}   median pctile "
          f"{d_pct:.0%}   consistency p {d_p:.2e}")

    rng = random.Random(7)
    keys = list(score)
    better_p = better_pf = 0
    ps, pfs = [], []
    for _ in range(DRAWS):
        pick = rng.sample(keys, len(have))
        pf, p, _ = stats(pick)
        ps.append(p); pfs.append(pf)
        better_p += p <= d_p
        better_pf += pf >= d_pf
    print(f"\n{DRAWS} random {len(have)}-symbol draws from the same "
          f"{len(keys)}-symbol pool:")
    print(f"  median of their consistency p : {st.median(ps):.2e}")
    print(f"  median of their median PF     : {st.median(pfs):.2f}")
    print(f"  draws at least as significant as the declared set: "
          f"{better_p}/{DRAWS} = {better_p/DRAWS:.4f}")
    print(f"  draws with at least the declared median PF        : "
          f"{better_pf}/{DRAWS} = {better_pf/DRAWS:.4f}")
    print("\n  A declared universe that is an ordinary draw sits near 0.5.")
    print("  One near 0 was chosen on the outcome, and its evidence is")
    print("  evidence about the choosing.")


if __name__ == "__main__":
    main()
