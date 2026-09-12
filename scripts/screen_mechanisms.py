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
# Sized for POWER, not for convenience. `consistency_p` reads a binomial
# tail over per-symbol null percentiles, so the number of symbols sets what
# the test can detect at all. At n=8, clearing the 0.50 cut on ALL EIGHT
# still only reaches p=1.2e-02 — the gate at 0.01 is unreachable — and 5 of
# 8 must clear the 90th. At n=16 a broad-but-modest edge is detectable:
# 14/16 above the median, or 6/16 above the 90th.
#
# That is not academic. Donchian Breakout Trail, the one strategy in the
# book with evidence, was measured through this screen's own universe:
#
#   the spec's own 16 symbols        15 scored  p = 3.5e-04   PASS
#   screen DISCOVERY as configured   10 scored  p = 2.9e-03   PASS
#   screen DISCOVERY as it LOADED     8 scored  p = 1.2e-02   REFUSED
#
# The third row was the real one. ADA and LTC were named in the list but
# absent from the candle store, so they dropped silently, and XAU/XAG made
# up the count while firing ~20 trades each across the whole period. The
# screen was running on 8 usable symbols and would have rejected the one
# mechanism that works. Every "nothing survives" verdict it ever printed was
# produced at that power.
#
# The split rule is fixed so it cannot be chosen to flatter a result:
# original members keep their original half, and every symbol added since is
# assigned by alternating down the alphabetical list.
DISCOVERY=["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","BNB/USDT","DOGE/USDT",
           "ADA/USDT","LINK/USDT","AVAX/USDT","LTC/USDT",
           "1000PEPE/USDT","APT/USDT","BCH/USDT","DASH/USDT","FET/USDT",
           "INJ/USDT","T/USDT","WLD/USDT","XMR/USDT"]
HELDOUT=["UNI/USDT","SUI/USDT","TAO/USDT","ZEC/USDT","NEAR/USDT","FIL/USDT",
         "AAVE/USDT","HYPE/USDT","TRUMP/USDT",
         "1000SHIB/USDT","ARB/USDT","CRV/USDT","DOT/USDT","ICP/USDT",
         "OP/USDT","TRX/USDT","XLM/USDT"]
# Tokenized commodities are NOT in either universe. They are recent listings
# on which the mechanisms fire ~20 trades across the whole period, so they
# never carry a percentile — but they were counted into the discovery total,
# which is how a universe of 8 read as 10.
TRADFI=[]

# Geometry is a screen DIMENSION, not a constant. The first pass here fixed
# it at RR 3 with no trail — and a fixed target amputates the tail a
# continuation mechanism lives on, which is exactly how Donchian Breakout
# Trail stayed invisible until the geometry sweep found it. Screening every
# mechanism under one exit shape asks "which entries pay under THIS exit",
# never "which mechanism is real".
# Geometry is a screen DIMENSION, not a constant. The first pass here fixed
# it at RR 3 with no trail — and a fixed target amputates the tail a
# continuation mechanism lives on, which is exactly how Donchian Breakout
# Trail stayed invisible until the geometry sweep found it. Screening every
# mechanism under one exit shape asks "which entries pay under THIS exit",
# never "which mechanism is real".
#
# Defined in trader/strategy/geometries.py so the research search scores
# candidates under the same two shapes this screen reports.
from trader.strategy.geometries import GEOS

# (name, long, short, requires). `requires` drives which derivative series
# must be loaded AND which symbols are skipped for want of it.
#
# The first eleven are the original single-symbol price/flow set, kept as a
# control: their numbers must not move, because passing context that they do
# not read cannot change them. Everything after is a family this screen could
# not express until the context above was plumbed through.
#
# taker_ratio / ls_ratio are deliberately absent: they hold ~33 days, far
# under the 75-day span a spec needs to be scored rather than refused.
# oi and ls_account_ratio are NOT absent any more: since 2026-09-11 both span
# 333 days on all 36 symbols (Coinalyze, 4hour), so they are screened below.
MECHS=[
 # CONTROL, and it must stay first. A screen that prints "nothing survives"
 # is making a claim about its own power before it makes one about the
 # market, so the known-good input runs through the same gate every time.
 # Measured 2026-09-02, this row is REFUSED: discovery p=2.3e-01, held-out
 # p=8.1e-02 against a gate of 0.01/0.05. That is not the gate being too
 # strict — split by whether the spec NAMED the symbol, in one run with
 # identical geometry, period, split, cost model and null:
 #
 #   declared by the spec   15 scored  median PF 1.42  pctile 87%  p=3.5e-04
 #   never declared         20 scored  median PF 0.93  pctile 52%  p=9.7e-01
 #
 # 10 of 20 above the median is the coin flip. The gate has power — it hands
 # the declared set 3.5e-04 — so "nothing survives" includes the incumbent,
 # and the book has zero mechanisms that generalise, not one.
 ("CONTROL_donchian100", "close > donchian_hi(100)", "close < donchian_lo(100)", ()),

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

 # ── 2026-09-02: the registry was never the ceiling. 71 features are
 # registered and the block above reaches about twenty of them, so
 # "nothing but breakout survives" was a statement about which combinations
 # had been written down, not about the space. efficiency_ratio, corr_btc,
 # vol_of_vol, streak, bars_since, swing_high/low, the wick/body shape
 # family and rel_volume had never appeared in a single screened mechanism.
 #
 # Every threshold below is placed at a MEASURED percentile of its own
 # quantity over 183k discovery bars at 4h, not at a round number — the
 # fault that silently zeroed the flow and vol-squeeze families:
 #
 #   efficiency_ratio(30)  p10 0.029  p50 0.162  p90 0.384
 #   corr_btc(90)          p10 0.448  p50 0.725  p90 0.891
 #   vol_of_vol(90)        p10 0.0018 p50 0.0038 p90 0.0094
 #   upper/lower_wick()    p10 0.05   p50 0.25   p90 0.58
 #   body_frac()           p10 0.087  p50 0.412  p90 0.759
 #   rel_volume(50)        p10 0.424  p50 0.814  p90 1.788
 #   dd_from_high(100)     p10 -0.261 p50 -0.111 p90 -0.027
 #   streak(up)            p90 3      p99 6
 #   bars_since(break)     p10 9      p50 107
 #
 # Donchian-plus-a-filter is deliberately NOT re-tested here: the paired
 # filter test already showed every such variant holds PF while halving
 # compounded return. These are different ENTRIES, not refinements.

 # path quality: is the move direct, or is it chop covering the same ground?
 ("er_trend",        "efficiency_ratio(30) > 0.38 and close > ema(100)",
                     "efficiency_ratio(30) > 0.38 and close < ema(100)", ()),
 ("er_revert",       "efficiency_ratio(30) < 0.03 and bb_pctb(20, 2.0) < 0.05",
                     "efficiency_ratio(30) < 0.03 and bb_pctb(20, 2.0) > 0.95", ()),
 # consecutive-bar runs, both signs — which way does the market pay?
 ("streak_exhaust",  "streak(close < prev(close, 1)) >= 5",
                     "streak(close > prev(close, 1)) >= 5", ()),
 ("streak_follow",   "streak(close > prev(close, 1)) >= 4 and close > ema(50)",
                     "streak(close < prev(close, 1)) >= 4 and close < ema(50)", ()),
 # idiosyncratic move: the alt is not simply wearing BTC's beta
 ("decoupled_trend", "corr_btc(90) < 0.45 and ret(30) > 0.05",
                     "corr_btc(90) < 0.45 and ret(30) < -0.05", ()),
 # absorption: a long tail rejected from one side inside a trend
 ("wick_reject",     "lower_wick() > 0.6 and close > ema(100)",
                     "upper_wick() > 0.6 and close < ema(100)", ()),
 # volatility-of-volatility as a regime switch, never screened
 ("lowvov_trend",    "vol_of_vol(90) < 0.0018 and close > ema(100)",
                     "vol_of_vol(90) < 0.0018 and close < ema(100)", ()),
 ("highvov_revert",  "vol_of_vol(90) > 0.0094 and bb_pctb(20, 2.0) < 0.05",
                     "vol_of_vol(90) > 0.0094 and bb_pctb(20, 2.0) > 0.95", ()),
 # deep pullback inside a long trend, measured from the range extreme
 ("deep_pullback",   "dd_from_high(100) < -0.20 and close > ema(200)",
                     "runup_from_low(100) > 0.30 and close < ema(200)", ()),
 # range expansion: a wide directional bar on real volume
 ("range_expand",    "body_frac() > 0.76 and rel_volume(50) > 1.8 and close > ema(50)",
                     "body_frac() > 0.76 and rel_volume(50) > 1.8 and close < ema(50)", ()),
 # pivot structure rather than a rolling extreme
 ("swing_break",     "close > swing_high(10) and close > ema(100)",
                     "close < swing_low(10) and close < ema(100)", ()),
 # accelerating trend: slope rising, not merely positive
 ("slope_accel",     "slope(close, 20) > 0.004 and slope(close, 20) > prev(slope(close, 20), 5)",
                     "slope(close, 20) < -0.004 and slope(close, 20) < prev(slope(close, 20), 5)", ()),
 # riding the band instead of fading it
 ("bb_ride",         "bb_pctb(20, 2.0) > 0.92 and close > ema(100)",
                     "bb_pctb(20, 2.0) < 0.08 and close < ema(100)", ()),
 # the continuation WINDOW after a break, not the break itself
 ("post_break",      "bars_since(close > donchian_hi(100)) < 10 and close > ema(50)",
                     "bars_since(close < donchian_lo(100)) < 10 and close < ema(50)", ()),

 # ── 2026-09-11: positioning. Open interest and the global long/short
 # ACCOUNT ratio now span 333 days on all 36 symbols (Coinalyze, 4hour) and
 # had never been screened: first the series were ~31 days deep, then this
 # screen's skip check and spec_evidence's series map each made them
 # invisible. Thresholds at measured percentiles over the 19 discovery
 # symbols at 4h (31-38k bars each):
 #
 #   oi_ret(6)               p10 -0.045  p50 -0.001  p90 +0.049
 #   oi_ret(24)              p10 -0.090  p50 +0.002  p90 +0.103
 #   oi_price_div(24)        p10 -0.108  p50 +0.006  p90 +0.145
 #   ls_account_ratio_z(360) p10 -1.41   p50 -0.19   p90 +1.51
 #
 # Both signs of each idea are screened: which side pays is the question,
 # not an assumption. 333 days is ONE regime, and the cross-symbol
 # consistency test is the only control that survives that.
 # CONTROL for this family. Positioning signals can only fire in the last
 # ~333 days, less a 360-bar z warm-up, so their test slice is a fraction
 # of what CONTROL_donchian100 is judged on and its power does not transfer.
 # This row is the known-good rule confined to the same window — oi_z is NaN
 # before open interest begins, so the conjunction cannot fire there. If
 # the gate cannot see Donchian here, "no positioning mechanism survives"
 # is a statement about the window's power, not about the market.
 ("CONTROL_oi_window", "close > donchian_hi(100) and oi_z(360) > -99",
                       "close < donchian_lo(100) and oi_z(360) > -99", ("open_interest",)),
 # new money entering with the trend
 ("oi_build_trend",  "oi_ret(24) > 0.103 and close > ema(50)",
                     "oi_ret(24) > 0.103 and close < ema(50)", ("open_interest",)),
 # a liquidation flush: open interest collapses into a sharp move — fade it
 ("oi_flush_revert", "oi_ret(6) < -0.045 and ret(6) < -0.02",
                     "oi_ret(6) < -0.045 and ret(6) > 0.02", ("open_interest",)),
 # positioning building AGAINST price: the crowded side gets squeezed ...
 ("oi_div_squeeze",  "oi_price_div(24) > 0.145",
                     "oi_price_div(24) < -0.108", ("open_interest",)),
 # ... or the crowd is right and the move continues
 ("oi_div_follow",   "oi_price_div(24) < -0.108",
                     "oi_price_div(24) > 0.145", ("open_interest",)),
 # the crowd of ACCOUNTS leans hard one way: fade it ...
 ("acct_contrarian", "ls_account_ratio_z(360) < -1.41",
                     "ls_account_ratio_z(360) > 1.51", ("ls_account_ratio",)),
 # ... or follow it
 ("acct_follow",     "ls_account_ratio_z(360) > 1.51",
                     "ls_account_ratio_z(360) < -1.41", ("ls_account_ratio",)),
 # leverage building on the crowded side: accounts lean one way AND open
 # interest grows, so the side that is both crowded and levered is faded
 ("acct_oi_crowded", "ls_account_ratio_z(360) < -1.41 and oi_ret(24) > 0.103",
                     "ls_account_ratio_z(360) > 1.51 and oi_ret(24) > 0.103",
                     ("ls_account_ratio", "open_interest")),
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
# Naming mechanisms on the command line screens only those. The control row
# always runs first regardless, so a narrowed screen still states its power.
PICK = tuple(a for a in sys.argv[1:] if a in {m[0] for m in MECHS})
if PICK:
    MECHS = [m for m in MECHS if m[0] == "CONTROL_donchian100" or m[0] in PICK]

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
              # `derivs` is keyed by SERIES name ("oi"), `requires` by the
              # feature's requirement name ("open_interest"). Looking the
              # requirement up directly only ever worked because funding and
              # basis happen to share both names; every OI mechanism would
              # have been skipped on every symbol as "lacked data".
              if requires and any(
                      ctx["derivs"].get(spec_evidence._SERIES_FOR.get(k, k)) is None
                      for k in requires):
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
