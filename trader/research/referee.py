"""The referee — held-out gates for the search's discovery survivors.

Spec Part 4. Cheapest first, first failure recorded with its reason:

- select   the survivors worth a look: testable, ranked by discovery
           compounded return, and not a near-copy of one already looked at
           (a twin costs no budget — without this the budget is spent forty
           times on one regime)
- gate 1   held-out A (unseen markets, same era) AND held-out B (every
           market, the later era): cross-symbol consistency on both, plus
           the common-rotation null on B. One candidate spends ONE test —
           its p is the max of the three, valid for "works on both".
- gate 3   the book with the candidate beats the book without it on
           compounded return over B, return/drawdown no worse. Not a
           hypothesis test; it spends nothing.

This module and `job.referee_job` are the ONLY code that reads held-out
prices. Discovery (`evaluate.load_bundle`) still reads held-out bar counts
and nothing else.
"""
from __future__ import annotations

import json
import logging
import statistics as st

import numpy as np

from ..strategy import null_baseline, spec_evidence
from ..strategy.portfolio_evidence import portfolio_curve
from ..strategy.vector_backtest import WARMUP, funding_for, simulate
from . import portfolio_null as pn
from . import slices
from .evaluate import Bundle

log = logging.getLogger(__name__)

#: bars of context kept before the cut on held-out B, so a B slice's first
#: tradeable bar is the cut and not the cut plus the engine's warmup
B_CONTEXT_BARS = WARMUP + 400
#: pooled trades a held-out slice must carry to be judged at all
MIN_TRADES = 60
#: per-symbol null draws for the consistency reading on held-out slices
CONSISTENCY_DRAWS = 60
#: a candidate whose entries overlap an earlier look's by more than this is
#: the same question — the Analyst's own redundancy cut
MAX_OVERLAP = 0.6


# ── held-out slices ──────────────────────────────────────────────────────
def load_heldout(tf: str, part: str, symbols, cut: int, cfg: dict,
                 requires=(), paths: dict | None = None) -> Bundle:
    """Held-out A (`symbols` before the cut) or B (from the cut on)."""
    from ..data.derivatives import DerivFeed
    from ..data.feed import DataFeed
    from ..data.references import RefStore
    from .universe import NoExchange

    if part not in ("a", "b"):
        raise ValueError(f"held-out part must be 'a' or 'b', got {part!r}")
    paths = paths or {}
    feed = DataFeed(exchange=NoExchange(), db_path=paths.get("candles"))
    frames, first_bars = {}, {}
    for sym in symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"referee load {sym} {tf}: {e}")
            continue
        if df is None or not len(df):
            continue
        ms = slices._ms(df).to_numpy()
        at = int(np.searchsorted(ms, int(cut), side="left"))
        if part == "a":
            sl = df.iloc[:at].reset_index(drop=True)
            first = 0
        else:
            start = max(0, at - B_CONTEXT_BARS)
            sl = df.iloc[start:].reset_index(drop=True)
            first = at - start
            if first < WARMUP:
                continue            # listed after the cut: no context, no B
        if len(sl) - first < slices.MIN_SLICE_BARS - WARMUP:
            continue
        frames[sym], first_bars[sym] = sl, first

    sym_frames = {}
    for sym, df in frames.items():
        try:
            sym_frames[sym] = spec_evidence.frames_for(df, tf)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"referee frames_for {sym} {tf}: {e}")
    frames = {s: d for s, d in frames.items() if s in sym_frames}

    keep = slices.before if part == "a" else (lambda df, _cut: df)
    derivs = {s: {} for s in frames}
    if any(r != "ohlcv" and not r.startswith("ref:") for r in requires):
        dfeed = DerivFeed(db_path=paths.get("derivs"))
        for sym in frames:
            got = spec_evidence.load_derivs(sym, requires, dfeed)
            derivs[sym] = {k: keep(v, cut) for k, v in got.items()}

    market = None
    ref_keys = [r.split(":", 1)[1] for r in requires
                if isinstance(r, str) and r.startswith("ref:")]
    if ref_keys:
        store = RefStore(paths.get("candles"))
        market = {}
        for k in ref_keys:
            df = store.load(k)
            if df is not None and len(df):
                market[k] = keep(df, cut)
        market = market or None

    btc = {tf: frames["BTC/USDT"]} if "BTC/USDT" in frames else None
    rcfg = cfg.get("risk", {}) or {}
    b = Bundle(tf=tf, frames=frames, sym_frames=sym_frames,
               universe={s: {tf: d} for s, d in frames.items()},
               btc=btc, market=market, derivs=derivs, risk=rcfg, cut=int(cut),
               equity=float(cfg.get("research", {}).get("equity", 2000.0)),
               risk_pct=float(rcfg.get("risk_per_trade_pct", 0.5)),
               max_open=int(rcfg.get("max_open_trades", 8)),
               first_bars=first_bars)
    return b


# ── gate 1 ───────────────────────────────────────────────────────────────
def consistency(legs, exit_spec, risk: dict, draws: int = CONSISTENCY_DRAWS,
                seed: int = 17,
                min_symbol_trades: int = 8) -> dict:
    """Per-symbol rotation percentiles and their cross-symbol p, respecting
    each leg's `first_bar` (a rotation is taken, THEN masked)."""
    pcts, trades, per = [], 0, {}
    for l in legs:
        lo = pn._mask(l.long, l.first_bar)
        sh = pn._mask(l.short, l.first_bar)
        r = simulate(lo, sh, l.df, exit_spec, risk, symbol=l.symbol,
                     funding=l.funding)
        trades += int(r.trades)
        per[l.symbol] = {"trades": int(r.trades),
                         "pf": round(float(r.profit_factor), 3)}
        if r.trades < min_symbol_trades:
            continue
        n = len(l.df) - l.first_bar
        lo_off, hi_off = WARMUP + 1, n - WARMUP - 1
        if hi_off <= lo_off:
            continue
        rng = np.random.default_rng(seed)
        null = []
        for off in rng.integers(lo_off, hi_off, size=int(draws)):
            nr = simulate(pn._mask(np.roll(l.long, off), l.first_bar),
                          pn._mask(np.roll(l.short, off), l.first_bar),
                          l.df, exit_spec, risk, symbol=l.symbol,
                          funding=l.funding)
            if nr.trades > 0:
                null.append(float(nr.profit_factor))
        pct = null_baseline.edge_percentile(float(r.profit_factor), null) \
            if len(null) >= null_baseline.MIN_DRAWS else None
        if pct is not None:
            per[l.symbol]["null_pctile"] = round(pct, 4)
            pcts.append(pct)
    return {"consistency_p": null_baseline.consistency_p(pcts),
            "scored_symbols": len(pcts), "trades": trades,
            "median_pf": round(st.median([v["pf"] for v in per.values()]), 3)
            if per else 0.0,
            "symbols": per}


def gate1(a: dict, b: dict, rot: dict,
          min_trades: int = MIN_TRADES) -> dict:
    """Combine the three readings into ONE p-value and a reason.

    `a`/`b` are `consistency()` results, `rot` a `common_rotation()` on B.
    Untestable is a p of 1.0, not a pass and not a skip: the held-out prices
    were read, so the look has happened and it is charged.
    """
    for name, r in (("A", a), ("B", b)):
        if r.get("trades", 0) < min_trades:
            return {"p": 1.0, "reason": f"untestable: held-out {name} carried "
                                        f"{r.get('trades', 0)} trades "
                                        f"(< {min_trades})"}
        if r.get("consistency_p") is None:
            return {"p": 1.0, "reason": f"untestable: held-out {name} scored "
                                        f"{r.get('scored_symbols', 0)} "
                                        f"symbols"}
    if rot.get("p") is None:
        return {"p": 1.0, "reason": "untestable: common rotation on B "
                                    f"({rot.get('reason', 'no fills')})"}
    ps = {"A consistency": float(a["consistency_p"]),
          "B consistency": float(b["consistency_p"]),
          "B common rotation": float(rot["p"])}
    worst = max(ps, key=ps.get)
    return {"p": ps[worst], "worst": worst, "ps": ps,
            "reason": f"{worst} p={ps[worst]:.2e}"}


# ── gate 3 ───────────────────────────────────────────────────────────────
def gate3(candidate_fills: list, book_fills: list, equity: float,
          risk_pct: float, max_open: int) -> dict:
    """Does the book make more money with the candidate in it?"""
    def curve(fills):
        if not fills:
            return {"total_pct": 0.0, "max_dd_pct": 0.0}
        c = portfolio_curve(fills, equity, risk_pct, max_open)
        return {"total_pct": round(float(c["total_pct"]), 3),
                "max_dd_pct": round(float(c["max_dd_pct"]), 3)}

    without = curve(book_fills)
    with_ = curve(list(book_fills) + list(candidate_fills))

    def ratio(c):
        dd = max(float(c["max_dd_pct"]), 1e-9)
        return float(c["total_pct"]) / dd

    passed, reason = True, "adds compounded return"
    if with_["total_pct"] <= without["total_pct"]:
        passed, reason = False, (f"book return {without['total_pct']}% -> "
                                 f"{with_['total_pct']}% with it")
    elif book_fills and ratio(with_) < ratio(without):
        passed, reason = False, (f"return/drawdown {ratio(without):.2f} -> "
                                 f"{ratio(with_):.2f} with it")
    return {"passed": passed, "reason": reason, "without": without,
            "with": with_}


# ── selection ────────────────────────────────────────────────────────────
def entry_set(legs) -> set:
    """(symbol, bar) of every entry — the unit overlap is measured in."""
    out = set()
    for l in legs:
        idx = np.flatnonzero(np.asarray(l.long, bool) | np.asarray(l.short,
                                                                   bool))
        ts = slices._ms(l.df).to_numpy()
        out.update((l.symbol, int(ts[i])) for i in idx)
    return out


def overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pick(survivors: list, looked: dict, m: int) -> tuple[list, list]:
    """Choose who gets a look.

    `survivors` are ledger rows carrying an `entries` set (computed in the
    child), best first; `looked` maps hash -> entries of every candidate
    already queued or examined. Returns (queued, twins) where twins are
    (hash, twin_of).
    """
    queued, twins = [], []
    seen = dict(looked)
    for row in survivors:
        if len(queued) >= m:
            break
        h, ent = row["hash"], row["entries"]
        twin = next((o for o, e in seen.items()
                     if overlap(ent, e) > MAX_OVERLAP), None)
        if twin is not None:
            twins.append((h, twin))
            continue
        queued.append(h)
        seen[h] = ent
    return queued, twins


def encode_entries(ent: set) -> str:
    by = {}
    for sym, ts in ent:
        by.setdefault(sym, []).append(ts)
    return json.dumps({s: sorted(v) for s, v in by.items()})


def decode_entries(s: str | None) -> set:
    if not s:
        return set()
    return {(sym, int(t)) for sym, v in json.loads(s).items() for t in v}
