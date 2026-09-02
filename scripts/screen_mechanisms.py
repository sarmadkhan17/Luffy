"""Broad null-baseline screen: which mechanisms beat their own rotation,
consistently, across independent symbols?

Multiple testing is the enemy here. A 90th-percentile result appears by
chance 10% of the time, so a wide sweep manufactures winners. The control is
CROSS-SECTIONAL CONSISTENCY: a real mechanism should beat its null on most
symbols it is applied to; a lucky one beats it on one or two.
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

CRYPTO=["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","BNB/USDT","DOGE/USDT",
        "ADA/USDT","LINK/USDT","AVAX/USDT","LTC/USDT"]
TRADFI=["XAU/USDT:USDT","XAG/USDT:USDT"]

# geometry: what the honest sweep chose — wide stop, RR 3, long hold
GEO=ExitSpec(stop={"kind":"atr","mult":3.0}, target={"kind":"rr","v":3.0},
             trail={"kind":"none"}, time={"max_bars":96})

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

def spec_for(name, lo, sh, tf):
    return StrategySpec(
        id=f"{name}_{tf}", name=f"{name} {tf}",
        thesis=("Screen candidate evaluated against a rotation null so that "
                "market drift and exit-geometry payout odds cannot be "
                "mistaken for entry skill in this search."),
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind":"screen"}, universe={"include":[]},
        timeframe=tf, direction="both", entry_long=lo, entry_short=sh,
        filters=[], exit=GEO, regime_filter=[], markets=["futures"])

def load(sym, tf):
    df = feed.cached_ohlcv(sym, tf, limit=40000)
    return df if df is not None and len(df) >= 500 else None

results=[]
for tf in ("4h","1h"):
    universe = {}
    for s in CRYPTO+TRADFI:
        d=load(s,tf)
        if d is not None: universe[s]=d
    print(f"\n### timeframe {tf} — {len(universe)} symbols with >=500 bars", flush=True)
    for name, lo, sh in MECHS:
        spec=spec_for(name, lo, sh, tf)
        try: c=compile_spec(spec)
        except Exception as e:
            print(f"  {name}: will not compile: {e}"); continue
        beats=0; seen=0; pfs=[]; pcts=[]; trades=0
        for sym, df in universe.items():
            try: sf=spec_evidence.frames_for(df, tf)
            except Exception: continue
            risk=spec_evidence.risk_for(cfg["risk"], tf)
            try: r=vector_walk_forward(c, sf, risk, symbol=sym)
            except Exception: continue
            trades += r["test"].trades
            if r["test"].trades < 8: continue
            pf=r["test"].profit_factor
            a=null_baseline.assess(c, sf, risk, pf, symbol=sym, draws=60,
                                   seed=17, split=0.7, part="test")
            p=a.get("percentile")
            if p is None: continue
            seen+=1; pfs.append(pf); pcts.append(p)
            if p>=0.90: beats+=1
        if seen>=4:
            results.append((tf,name,st.median(pfs),st.median(pcts),beats,seen,trades))
            print(f"  {name:<18} PF {st.median(pfs):>5.2f}  null-pctile "
                  f"{st.median(pcts):>4.0%}  beats-null {beats}/{seen}  "
                  f"trades {trades}", flush=True)

print("\n" + "="*74)
print("MECHANISMS BEATING THEIR NULL ON A MAJORITY OF SYMBOLS")
print("="*74)
good=[r for r in results if r[4] >= max(3, r[5]*0.6)]
if not good: print("  none.")
for tf,name,pf,pc,b,s,t in sorted(good,key=lambda r:-r[4]/r[5]):
    print(f"  {name:<18} {tf:<4} PF {pf:.2f}  beats null on {b}/{s} symbols "
          f"({t} trades)")
