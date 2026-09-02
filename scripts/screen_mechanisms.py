"""Broad null-baseline screen: which mechanisms beat their own rotation,
consistently, across independent symbols?

Multiple testing is the enemy here. A 90th-percentile result appears by
chance 10% of the time, so a wide sweep manufactures winners. The control is
CROSS-SECTIONAL CONSISTENCY: a real mechanism should beat its null on most
symbols it is applied to; a lucky one beats it on one or two.

The screen was also BLIND for its whole life. It called
`vector_walk_forward(c, sf, risk, symbol=sym)` and passed no `universe`, no
`derivs` and no `btc` — so every cross-sectional, carry and BTC-relative
feature in the registry evaluated to NaN and the mechanism silently took
ZERO trades. Measured on the discovery universe at 4h:

    price_breakout (control)   285 trades screened   285 with context
    xs_rank_momo                 0 trades screened   874 with context
    carry_funding                0 trades screened   561 with context
    btc_relative                 0 trades screened   417 with context

So "nothing survives both universes" was never a statement about the market.
It was a statement about what this harness could evaluate: single-symbol
price and volume rules, which is the most heavily mined space there is.

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
from trader.strategy.vector_backtest import funding_for, vector_walk_forward

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

# (name, long, short, requires). `requires` drives which derivative series
# must be loaded AND which symbols are skipped for want of it.
#
# The first eleven are the original single-symbol price/flow set, kept as a
# control: their numbers must not move, because passing context that they do
# not read cannot change them. Everything after is a family this screen could
# not express until the context above was plumbed through.
#
# oi / taker_ratio / ls_ratio are deliberately absent. They hold ~33 days over
# 5 symbols, far under the 75-day span a spec needs to be scored rather than
# refused, so screening them would measure noise.
MECHS=[
 ("trend_pullback",  "close > ema(50) and close < ema(10)", "close < ema(50) and close > ema(10)", ()),
 ("breakout",        "close > donchian_hi(48)",             "close < donchian_lo(48)", ()),
 ("meanrev_z",       "zscore(close, 96) < -2.0",            "zscore(close, 96) > 2.0", ()),
 ("rsi_reclaim",     "rsi(14) < 30",                        "rsi(14) > 70", ()),
 # a threshold stated as an absolute constant is a guess about a
 # distribution. Measured over 86k 4h bars on the discovery universe,
 # realized_vol(48)/realized_vol(240) has a MEDIAN of 0.91 and a 1st
 # percentile of 0.45, so the original "< 0.40" fired on 0.37% of bars and,
 # once combined with a trend filter, took zero trades on every symbol. It
 # was reported as "no result" for the life of the screen. Ranking the
 # quantity against its own recent distribution asks the intended question.
 ("vol_squeeze",     "atr_pct_rank(200) < 0.2 and close > ema(50)",
                     "atr_pct_rank(200) < 0.2 and close < ema(50)", ()),
 ("momo_persist",    "ret(24) > 0.03 and close > ema(100)", "ret(24) < -0.03 and close < ema(100)", ()),
 # same fault, larger. Aggressor imbalance mean-reverts hard once
 # aggregated to 4h: over 88k bars taker_buy/volume runs p1=0.431,
 # p50=0.492, p99=0.553. "> 0.62" is a five-sigma event that fires on
 # 0.005% of bars, so all three flow mechanisms took ~zero trades and the
 # whole flow family went untested while appearing in the results table.
 ("flow_thrust",     "pct_rank(taker_buy_frac(), 500) > 0.9 and close > ema(50)",
                     "pct_rank(taker_buy_frac(), 500) < 0.1 and close < ema(50)", ()),
 ("flow_divergence", "close > donchian_hi(48) and pct_rank(taker_buy_frac(), 500) < 0.25",
                     "close < donchian_lo(48) and pct_rank(taker_buy_frac(), 500) > 0.75", ()),
 ("session_us_cont", "is_session('us') and close > ema(50)",
                     "is_session('us') and close < ema(50)", ()),
 ("session_asia_rev","is_session('asia') and zscore(close, 96) < -1.5",
                     "is_session('asia') and zscore(close, 96) > 1.5", ()),
 ("gap_revert",      "ret(6) < -0.02 and close > ema(200)",  "ret(6) > 0.02 and close < ema(200)", ()),

 # ── cross-sectional: rank against the rest of the book, not against self.
 # Relative strength is the one documented crypto anomaly this system had
 # never been able to test, because `universe` was never supplied.
 ("xs_momo",         "xs_rank(ret(30)) > 0.8",  "xs_rank(ret(30)) < 0.2", ()),
 ("xs_momo_trend",   "xs_rank(ret(30)) > 0.8 and close > ema(100)",
                     "xs_rank(ret(30)) < 0.2 and close < ema(100)", ()),
 ("xs_revert",       "xs_rank(ret(6)) < 0.1",   "xs_rank(ret(6)) > 0.9", ()),
 ("xs_break",        "close > donchian_hi(100) and xs_rank(ret(30)) > 0.6",
                     "close < donchian_lo(100) and xs_rank(ret(30)) < 0.4", ()),
 ("breadth_break",   "close > donchian_hi(100) and breadth(close > ema(50)) > 0.6",
                     "close < donchian_lo(100) and breadth(close > ema(50)) < 0.4", ()),

 # ── carry: funding is the one derivative series deep enough to score on
 # (17 symbols, ~4 years). A crowded side pays to stay crowded.
 ("carry_crowded",   "funding_z(360) < -1.5",   "funding_z(360) > 1.5", ("funding",)),
 ("carry_break",     "close > donchian_hi(100) and funding_z(360) < 1.0",
                     "close < donchian_lo(100) and funding_z(360) > -1.0", ("funding",)),
 ("carry_pct_revert","funding_pct(720) > 0.95",  "funding_pct(720) < 0.05", ("funding",)),
 ("basis_stress",    "basis_z(360) < -2.0",     "basis_z(360) > 2.0", ("basis",)),

 # ── BTC-relative: lead-lag against the market leader
 ("rel_strength",    "rel_strength_btc(30) > 0.05", "rel_strength_btc(30) < -0.05", ()),
 ("rel_break",       "close > donchian_hi(100) and rel_strength_btc(30) > 0",
                     "close < donchian_lo(100) and rel_strength_btc(30) < 0", ()),

 # ── volatility regime: the same breakout, conditioned on where vol sits in
 # its own distribution. Never screened, though both features were registered.
 ("break_lowvol",    "close > donchian_hi(100) and atr_pct_rank(200) < 0.4",
                     "close < donchian_lo(100) and atr_pct_rank(200) < 0.4", ()),
 ("break_hivol",     "close > donchian_hi(100) and atr_pct_rank(200) > 0.6",
                     "close < donchian_lo(100) and atr_pct_rank(200) > 0.6", ()),
]

def spec_for(name, lo, sh, tf, geo, requires=()):
    return StrategySpec(
        id=f"{name}_{tf}", name=f"{name} {tf}",
        thesis=("Screen candidate evaluated against a rotation null so that "
                "market drift and exit-geometry payout odds cannot be "
                "mistaken for entry skill in this search."),
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind":"screen"}, universe={"include":[]},
        timeframe=tf, direction="both", entry_long=lo, entry_short=sh,
        filters=[], exit=geo, regime_filter=[], markets=["futures"],
        data_requires=["ohlcv", *requires])

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
      # The evaluation context, which this screen never used to build. A
      # cross-sectional feature ranks the symbol against its PEERS, so the
      # two universes are kept apart: a discovery symbol is ranked against
      # discovery symbols only. Sharing one universe would let a held-out
      # symbol inform a discovery signal and the second test would no longer
      # be independent of the first.
      btc = spec_evidence.frames_for(universe["BTC/USDT"], tf) \
          if "BTC/USDT" in universe else None
      uni_disc = {k: {tf: v} for k, v in disc.items()}
      uni_held = {k: {tf: v} for k, v in held.items()}
      print(f"    context: btc={'yes' if btc else 'NO'} "
            f"universe={len(uni_disc)}+{len(uni_held)}", flush=True)
      for name, lo, sh, requires in MECHS:
          spec=spec_for(name, lo, sh, tf, geo, requires)
          try: c=compile_spec(spec)
          except Exception as e:
              print(f"  {name}: will not compile: {e}"); continue
          beats=0; seen=0; pfs=[]; pcts=[]; trades=0
          hpfs=[]; hpcts=[]; htrades=0; skipped=[]; errs=[]
          for sym, df in universe.items():
              try: sf=spec_evidence.frames_for(df, tf)
              except Exception: continue
              risk=spec_evidence.risk_for(cfg["risk"], tf)
              is_held = sym in held
              ctx = dict(btc=btc, derivs=spec_evidence.load_derivs(sym, requires),
                         universe=uni_held if is_held else uni_disc)
              # a mechanism cannot be judged on a symbol whose series is
              # absent; that is silence, not a failing score
              if requires and any(ctx["derivs"].get(k) is None for k in requires):
                  skipped.append(sym); continue
              try: r=vector_walk_forward(c, sf, risk, symbol=sym, **ctx)
              except Exception as e:
                  errs.append(f"{sym}: {e}"); continue
              if is_held: htrades += r["test"].trades
              else: trades += r["test"].trades
              if r["test"].trades < 8: continue
              pf=r["test"].profit_factor
              # the null is charged the same signed carry as the actual —
              # otherwise the control is the more expensive of the two
              a=null_baseline.assess(c, sf, risk, pf, symbol=sym, draws=60,
                                     seed=17, split=0.7, part="test",
                                     funding=funding_for(sym, sf[tf], risk),
                                     **ctx)
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
                    f"trades {trades}  held-out p {hp}"
                    + (f"  [no data: {len(skipped)}]" if skipped else "")
                    + (f"  [ERR {len(errs)}]" if errs else ""), flush=True)
          elif errs:
              # never swallow this: a mechanism raising on every symbol reads
              # as "no result", which is how the missing-context blindness
              # survived unnoticed for the whole life of this script
              print(f"  {name:<18} {len(errs)} symbols raised, e.g. "
                    f"{errs[0]}", flush=True)
          else:
              print(f"  {name:<18} only {seen} symbols carried a percentile "
                    f"({trades} discovery trades, {len(skipped)} lacked data) "
                    f"— not scored", flush=True)

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
