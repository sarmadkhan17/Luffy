"""Score one combination on the discovery slice.

What comes back is deliberately NOT a single number:

- `consistency_p` — the cross-symbol spread of rotation-null percentiles.
  A profit factor alone is a statement about arithmetic: exit geometry sets a
  win rate by itself and ALWAYS-LONG scores PF 1.28 on drift. A percentile
  per symbol is noise; the SHAPE across independent markets is the test.
- `portfolio.total_pct` — compounded return over ONE account at the live
  concurrency cap. Every filter ever tried on the book held its profit factor
  while halving compounded return, because the trades it cut were near
  break-even individually and carried the fat right tail. A refinement is
  judged here, never on a ratio that improves while the trade count falls.
- `projection` — whether the rule fires often enough to be JUDGED on the
  held-out markets at all. A combination too picky to be tested stops
  growing; it is never admitted on silence.

The bundle is truncated at the cut before anything is evaluated, so no code
path here can read a bar the search is not entitled to.
"""
from __future__ import annotations

import logging
import statistics as st
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..strategy import null_baseline, spec_evidence
from ..strategy.compile import compile_spec
from ..strategy.portfolio_evidence import Fill, portfolio_curve
from ..strategy.vector_backtest import funding_for, simulate
from . import slices

log = logging.getLogger(__name__)

#: a symbol carrying fewer test trades than this cannot carry a percentile —
#: the same floor Analyst._null_percentiles applies
MIN_SYMBOL_TRADES = 8
#: the held-out projection: this many trades on this many markets
MIN_PROJECTED_TRADES = 8
MIN_PROJECTED_MARKETS = 4

_TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


@dataclass
class Bundle:
    """Everything one horizon's evaluations need, already truncated."""
    tf: str
    frames: dict                     # symbol -> discovery-slice frame
    sym_frames: dict                 # symbol -> {tf: frame, ...} for htf()
    universe: dict                   # symbol -> {tf: frame}
    btc: dict | None                 # {tf: frame} — keyed by TIMEFRAME
    market: dict | None              # reference key -> frame
    derivs: dict                     # symbol -> {series: raw frame}
    risk: dict
    cut: int = 0
    heldout_bars: dict = field(default_factory=lambda: {"a": {}, "b": {}})
    equity: float = 2000.0
    risk_pct: float = 0.5
    max_open: int = 8
    # symbol -> first tradeable bar. Empty on discovery; on held-out B the
    # bars before it are warmup context from before the cut.
    first_bars: dict = field(default_factory=dict)


def load_bundle(tf: str, symbols, cfg: dict, requires=(),
                heldout_symbols=(), paths: dict | None = None) -> Bundle:
    """Read the stores, cut at the discovery boundary, build the context."""
    from ..data.derivatives import DerivFeed
    from ..data.feed import DataFeed
    from ..data.references import RefStore
    from .universe import NoExchange

    paths = paths or {}
    # NoExchange lives in universe.py and is the ONE definition: a DataFeed
    # built for research reads must never construct a ccxt client, and an
    # accidental use of it raises instead of quietly fetching — a silent
    # fetch inside a niced child would be invisible.
    feed = DataFeed(exchange=NoExchange(), db_path=paths.get("candles"))
    full = {}
    for sym in symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research load {sym} {tf}: {e}")
            continue
        if df is not None and len(df) >= 500:
            full[sym] = df

    cut = slices.cut_ms(full) or 0
    disc = slices.discovery(full, cut)

    # held-out BAR COUNTS only — never their prices. The projection needs to
    # know how much room a rule would have to fire in, which is metadata.
    held_full = {}
    for sym in heldout_symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception:                               # noqa: BLE001
            continue
        if df is not None and len(df) >= 500:
            held_full[sym] = df
    heldout_bars = {"a": slices.bar_counts(held_full),
                    "b": slices.bar_counts(slices.heldout_b(full, cut))}

    sym_frames = {}
    for sym, df in disc.items():
        try:
            sym_frames[sym] = spec_evidence.frames_for(df, tf)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research frames_for {sym} {tf}: {e}")
    disc = {s: d for s, d in disc.items() if s in sym_frames}

    derivs = {}
    if any(r != "ohlcv" and not r.startswith("ref:") for r in requires):
        dfeed = DerivFeed(db_path=paths.get("derivs"))
        for sym in disc:
            got = spec_evidence.load_derivs(sym, requires, dfeed)
            derivs[sym] = {k: slices.before(v, cut) for k, v in got.items()}
    else:
        derivs = {s: {} for s in disc}

    market = None
    ref_keys = [r.split(":", 1)[1] for r in requires
                if isinstance(r, str) and r.startswith("ref:")]
    if ref_keys:
        store = RefStore(paths.get("candles"))
        market = {}
        for k in ref_keys:
            df = store.load(k)
            if df is not None and len(df):
                market[k] = slices.before(df, cut)
        market = market or None

    btc = {tf: disc["BTC/USDT"]} if "BTC/USDT" in disc else None
    rcfg = cfg.get("risk", {}) or {}
    return Bundle(
        tf=tf, frames=disc, sym_frames=sym_frames,
        universe={s: {tf: d} for s, d in disc.items()},
        btc=btc, market=market, derivs=derivs,
        risk=spec_evidence.risk_for(rcfg, tf), cut=cut,
        heldout_bars=heldout_bars,
        equity=float(cfg.get("research", {}).get("equity", 2000.0)),
        risk_pct=float(rcfg.get("risk_per_trade_pct", 0.5)),
        max_open=int(rcfg.get("max_open_trades", 8)))


def _clock(df) -> np.ndarray:
    """Bar timestamps in SECONDS.

    `.astype("int64")` keeps the column's own resolution, and the candle
    store holds datetime64[ms] while a resampled frame may hold ns — dividing
    by a hardcoded 10**9 silently produces a clock off by 10**6.
    """
    return (pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
            .astype("datetime64[s]").astype("int64").to_numpy())


def evaluate(c, b: Bundle, draws: int = 30, seed: int = 17,
             min_symbol_trades: int = MIN_SYMBOL_TRADES) -> dict:
    """Score `c` over the bundle's discovery slice."""
    spec = c.to_spec()
    out = {"hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
           "round": c.round, "parent": c.parent, "trigger": c.trigger,
           "parts": list(c.keys), "window": c.window,
           "entry_long": c.long, "entry_short": c.short,
           "symbols": {}, "trades": 0, "scored_symbols": 0,
           "consistency_p": None, "median_pf": 0.0, "median_rate": 0.0,
           "portfolio": {"total_pct": 0.0, "max_dd_pct": 0.0, "taken": 0,
                         "order": []},
           "projection": {"markets_a": 0, "markets_b": 0},
           "testable": False, "verdict": "empty"}
    try:
        compiled = compile_spec(spec)
    except Exception as e:                              # noqa: BLE001
        out["verdict"] = "error"
        out["error"] = str(e)
        return out

    step = _TF_SECONDS.get(c.tf, 900)
    t0 = None
    for df in b.frames.values():
        first = int(_clock(df)[0])
        t0 = first if t0 is None else min(t0, first)

    fills, pcts, pfs, rates = [], [], [], []
    for sym, df in b.frames.items():
        rec: dict = {"trades": 0, "pf": 0.0, "null_pctile": None}
        out["symbols"][sym] = rec
        sf = b.sym_frames.get(sym)
        try:
            lo, sh = compiled.entries(
                sf, btc=b.btc, derivs=b.derivs.get(sym),
                universe=b.universe, market=b.market, symbol=sym)
            fund = funding_for(sym, df, b.risk)
            mine: list = []
            r = simulate(lo, sh, df, spec.exit, b.risk, symbol=sym,
                         funding=fund, fills_out=mine)
        except Exception as e:                          # noqa: BLE001
            log.debug(f"research {c.hash} {sym}: {e}")
            rec["error"] = str(e)[:200]
            continue

        rec["trades"] = int(r.trades)
        rec["pf"] = round(float(r.profit_factor), 3)
        out["trades"] += int(r.trades)
        usable = slices.usable_bars(len(df))
        rec["rate"] = (r.trades / usable) if usable else 0.0
        rates.append(rec["rate"])

        clock = _clock(df)
        for f in mine:
            fills.append(Fill(
                entry_i=int((clock[f.entry_i] - t0) // step),
                exit_i=int((clock[f.exit_i] - t0) // step),
                r_multiple=f.r_multiple, symbol=sym))

        if r.trades < min_symbol_trades:
            continue
        try:
            a = null_baseline.assess(
                compiled, sf, b.risk, r.profit_factor, btc=b.btc,
                derivs=b.derivs.get(sym), universe=b.universe,
                market=b.market, symbol=sym, draws=int(draws), seed=seed,
                split=None, funding=fund)
        except Exception as e:                          # noqa: BLE001
            log.debug(f"research null {c.hash} {sym}: {e}")
            rec["null_error"] = str(e)[:200]
            continue
        p = a.get("percentile")
        if p is None:
            continue
        rec["null_pctile"] = round(float(p), 4)
        pcts.append(float(p))
        pfs.append(float(r.profit_factor))

    if fills:
        curve = portfolio_curve(fills, b.equity, b.risk_pct, b.max_open)
        out["portfolio"] = {
            "total_pct": round(curve["total_pct"], 3),
            "max_dd_pct": round(curve["max_dd_pct"], 3),
            "taken": curve["taken"], "order": curve["order"]}
    out["scored_symbols"] = len(pcts)
    out["median_pf"] = round(st.median(pfs), 3) if pfs else 0.0
    out["median_rate"] = st.median(rates) if rates else 0.0

    # testability: would this rule fire often enough to be JUDGED later?
    # Bar counts are metadata; no held-out price is read.
    rate = out["median_rate"]
    proj = {}
    for part in ("a", "b"):
        n = 0
        for _sym, bars in (b.heldout_bars.get(part) or {}).items():
            if rate * slices.usable_bars(bars) >= MIN_PROJECTED_TRADES:
                n += 1
        proj[f"markets_{part}"] = n
    out["projection"] = proj
    out["testable"] = bool(proj["markets_a"] >= MIN_PROJECTED_MARKETS
                           and proj["markets_b"] >= MIN_PROJECTED_MARKETS)

    if out["trades"] == 0:
        out["verdict"] = "empty"
        return out
    if len(pcts) < null_baseline.MIN_SYMBOLS:
        out["verdict"] = "untestable"
        return out
    out["consistency_p"] = null_baseline.consistency_p(pcts)
    out["verdict"] = "scored"
    return out
