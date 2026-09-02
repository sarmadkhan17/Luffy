"""Broad null-baseline screen: which mechanisms beat their own rotation,
consistently, across independent symbols?

Multiple testing is the enemy here. A 90th-percentile result appears by
chance 10% of the time, so a wide sweep manufactures winners. The control is
CROSS-SECTIONAL CONSISTENCY: a real mechanism should beat its null on most
symbols it is applied to; a lucky one beats it on one or two.

Consistency is measured as a BINOMIAL TAIL, not as a fraction over a fixed
bar. The original rule — "beats the 90th percentile on >=60% of symbols" —
rejects Donchian Breakout Trail, the one strategy in the book with evidence
behind it: it clears the 90th on 7 of 15 symbols, not 9. But 7 of 15 when
1.5 are expected is p=3e-4, and its percentiles run 14/15 above the no-edge
line with a median of 88%. A rule that discards the only mechanism known to
work is measuring the wrong thing. The distribution of percentiles carries
the evidence; a single cut throws most of it away.
"""
import sys, statistics as st, dataclasses, json, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.strategy import spec_evidence, null_baseline
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec, ExitSpec
from trader.strategy.vector_backtest import vector_walk_forward

cfg=load_config(); feed=DataFeed()

# Two universes, and a candidate must survive both. Bonferroni over the
# number of looks is a weak control against a search of this shape: it
# assumes the looks are independent, and eleven mechanisms over ten
# correlated crypto majors are not. An out-of-sample UNIVERSE is a strong
# control, because a mechanism that only works where it was found is a
# description of those symbols.
#
# momo_persist is why this exists. On DISCOVERY it scored PF 1.22, median
# null percentile 87%, p=1.1e-03 over 501 trades — the single best result in
# the screen. On HELDOUT it fell to 65% and p=0.27. Nothing about the first
# number was wrong; it just was not evidence.
DISCOVERY=["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","BNB/USDT","DOGE/USDT",
           "ADA/USDT","LINK/USDT","AVAX/USDT","LTC/USDT"]
HELDOUT=["UNI/USDT","SUI/USDT","TAO/USDT","ZEC/USDT","NEAR/USDT","FIL/USDT",
         "AAVE/USDT","HYPE/USDT","TRUMP/USDT"]
TRADFI=["XAU/USDT:USDT","XAG/USDT:USDT"]

# Geometry is a screen DIMENSION, not a constant. The first pass here fixed
# it at RR 3 with no trail — and a fixed target amputates the tail a
# continuation mechanism lives on, which is exactly how Donchian Breakout
# Trail stayed invisible until the geometry sweep found it. Screening every
# mechanism under one exit shape asks "which entries pay under THIS exit",
# never "which mechanism is real".
GEOS = {
    "fixed": ExitSpec(stop={"kind": "atr", "mult": 3.0},
                      target={"kind": "rr", "v": 3.0},
                      trail={"kind": "none"}, time={"max_bars": 96}),
    # the shape the only validated strategy was admitted under
    "trail": ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "none"},
                      trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
                      time={"max_bars": 500}),
}

MECHS=[
 ("trend_pullback",  "close > ema(50) and close < ema(10)", "close < ema(50) and close > ema(10)"),
 ("breakout",        "close > donchian_hi(48)",             "close < donchian_lo(48)"),
 ("meanrev_z",       "zscore(close, 96) < -2.0",            "zscore(close, 96) > 2.0"),
 ("rsi_reclaim",     "rsi(14) < 30",                        "rsi(14) > 70"),
 ("vol_squeeze",     "realized_vol(48) < 0.4 * realized_vol(240) and close > ema(50)",
                     "realized_vol(48) < 0.4 * realized_vol(240) and close < ema(50)"),
 ("momo_persist",    "ret(24) > 0.03 and close > ema(100)", "ret(24) < -0.03 and close < ema(100)"),
 ("flow_thrust",     "taker_buy() > 0.62 * volume and close > ema(50)",
                     "taker_buy() < 0.38 * volume and close < ema(50)"),
 ("flow_divergence", "close > donchian_hi(48) and taker_buy() < 0.45 * volume",
                     "close < donchian_lo(48) and taker_buy() > 0.55 * volume"),
 ("session_us_cont", "is_session('us') and close > ema(50)",
                     "is_session('us') and close < ema(50)"),
 ("session_asia_rev","is_session('asia') and zscore(close, 96) < -1.5",
                     "is_session('asia') and zscore(close, 96) > 1.5"),
 ("gap_revert",      "ret(6) < -0.02 and close > ema(200)",  "ret(6) > 0.02 and close < ema(200)"),
]

def spec_for(name, lo, sh, tf, geo):
    return StrategySpec(
        id=f"{name}_{tf}", name=f"{name} {tf}",
        thesis=("Screen candidate evaluated against a rotation null so that "
                "market drift and exit-geometry payout odds cannot be "
                "mistaken for entry skill in this search."),
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind":"screen"}, universe={"include":[]},
        timeframe=tf, direction="both", entry_long=lo, entry_short=sh,
        filters=[], exit=geo, regime_filter=[], markets=["futures"])

def load(sym, tf):
    df = feed.cached_ohlcv(sym, tf, limit=40000)
    return df if df is not None and len(df) >= 500 else None

TFS = tuple(a for a in sys.argv[1:] if a in ("4h", "1h", "15m")) or ("4h",)
GKEYS = tuple(a for a in sys.argv[1:] if a in GEOS) or tuple(GEOS)

from trader.strategy.null_baseline import consistency_p as consistency

results=[]
for gname in GKEYS:
  geo = GEOS[gname]
  print(f"\n{'='*74}\nGEOMETRY {gname}: stop {geo.stop} target {geo.target} "
        f"trail {geo.trail} time {geo.time}\n{'='*74}", flush=True)
  for tf in TFS:
      universe = {}
      for s in DISCOVERY+TRADFI+HELDOUT:
          d=load(s,tf)
          if d is not None: universe[s]=d
      disc = {k: v for k, v in universe.items() if k not in HELDOUT}
      held = {k: v for k, v in universe.items() if k in HELDOUT}
      print(f"\n### timeframe {tf} — {len(disc)} discovery + {len(held)} "
            f"held-out symbols with >=500 bars", flush=True)
      for name, lo, sh in MECHS:
          spec=spec_for(name, lo, sh, tf, geo)
          try: c=compile_spec(spec)
          except Exception as e:
              print(f"  {name}: will not compile: {e}"); continue
          beats=0; seen=0; pfs=[]; pcts=[]; trades=0
          hpfs=[]; hpcts=[]; htrades=0
          for sym, df in universe.items():
              try: sf=spec_evidence.frames_for(df, tf)
              except Exception: continue
              risk=spec_evidence.risk_for(cfg["risk"], tf)
              try: r=vector_walk_forward(c, sf, risk, symbol=sym)
              except Exception: continue
              is_held = sym in held
              if is_held: htrades += r["test"].trades
              else: trades += r["test"].trades
              if r["test"].trades < 8: continue
              pf=r["test"].profit_factor
              a=null_baseline.assess(c, sf, risk, pf, symbol=sym, draws=60,
                                     seed=17, split=0.7, part="test")
              p=a.get("percentile")
              if p is None: continue
              if is_held:
                  hpfs.append(pf); hpcts.append(p); continue
              seen+=1; pfs.append(pf); pcts.append(p)
              if p>=0.90: beats+=1
          if seen>=4:
              results.append((gname,tf,name,st.median(pfs),st.median(pcts),
                              beats,seen,trades,list(pcts),list(hpcts),
                              list(hpfs),htrades))
              _hp = consistency(hpcts)
              hp = f"{_hp:.0e}" if _hp is not None else "n/a"
              print(f"  {name:<18} PF {st.median(pfs):>5.2f}  null-pctile "
                    f"{st.median(pcts):>4.0%}  beats-null {beats}/{seen}  "
                    f"trades {trades}  held-out p {hp}", flush=True)

print("\n" + "="*74)
print("MECHANISMS WHOSE PERCENTILES REJECT THE NO-EDGE NULL")
print("="*74)
print(f"  {'mechanism':<18} {'tf':<4} {'geo':<6} {'PF':>5} {'disc p':>9} "
      f"{'held PF':>8} {'held p':>9}")
scored = sorted(results, key=lambda r: consistency(r[8]) or 1.0)
shown = 0
for g, tf, name, pf, pc, b, n, t, pcts, hpcts, hpfs, ht in scored:
    p = consistency(pcts) or 1.0
    hp = consistency(hpcts)
    hpf = st.median(hpfs) if hpfs else float("nan")
    ok = p < 0.01 and pf > 1.0 and hp is not None and hp < 0.05 and hpf > 1.0
    flag = "  <-- ADMIT" if ok else (
        "  discovery only" if p < 0.01 and pf > 1.0 else "")
    print(f"  {name:<18} {tf:<4} {g:<6} {pf:>5.2f} {p:>9.1e} {hpf:>8.2f} "
          f"{('%.1e' % hp) if hp is not None else 'n/a':>9}{flag}")
    shown += ok
if not shown:
    print("\n  nothing survives both universes. A discovery-only result is\n"
          "  a description of the symbols it was found on.")
