"""The referee — held-out gates for the search's discovery survivors.

Spec Part 4. Cheapest first, first failure recorded with its reason:

- select   the survivors worth a look: testable, ranked by discovery
           compounded return, and not a near-copy of one already looked at
           (a twin costs no budget — without this the budget is spent forty
           times on one regime)
- gate 1   held-out A (unseen markets, same era) AND held-out B (every
           market, the later era): cross-symbol consistency on both, plus
           the common-rotation null on B. Consistency is read corrected
           for cross-symbol dependence. One candidate spends ONE test —
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
from ..strategy.vector_backtest import WARMUP, walk_table
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
#: matched-offset draws for the cross-symbol dependence estimate
DEPENDENCE_DRAWS = 200
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
def _pf(walk, risk: dict, equity: float = 2000.0) -> float:
    """Profit factor of a `walk_table` result, in USDT against the leg's own
    running balance — the arithmetic `simulate` does, so the same number."""
    frac = float(risk["risk_per_trade_pct"]) / 100.0
    eq, win, loss = float(equity), 0.0, 0.0
    for _i, _e, r in walk:
        pnl = r * eq * frac
        if pnl > 0:
            win += pnl
        else:
            loss += -pnl
        eq += pnl
    if loss == 0:
        return float("inf") if win > 0 else 0.0
    return win / loss


def null_dependence(legs, risk: dict, draws: int = DEPENDENCE_DRAWS,
                    seed: int = 29) -> dict:
    """Mean pairwise rank correlation of the legs' null profit factors when
    every leg is rotated by the SAME offset.

    Under no edge each symbol's actual is one draw from its rotation null,
    and the symbols' actuals are one JOINT draw — so how their null
    statistics move together at matched offsets is how their percentiles
    move together. Legs must already be `_prepare`d. Offsets come from the
    shortest leg's range, so every draw is a legal rotation of every leg.
    """
    legs = [l for l in legs if getattr(l, "table", None) is not None]
    if len(legs) < 2:
        return {"rho_bar": None, "pairs": 0, "draws": 0}
    n = min(len(l.df) - l.first_bar for l in legs)
    lo_off, hi_off = WARMUP + 1, n - WARMUP - 1
    if hi_off <= lo_off:
        return {"rho_bar": None, "pairs": 0, "draws": 0}
    offs = np.random.default_rng(seed).integers(lo_off, hi_off,
                                                size=int(draws))
    mat = np.full((len(offs), len(legs)), np.nan)
    for j, l in enumerate(legs):
        for k, off in enumerate(offs):
            nw = walk_table(l.table,
                            pn._mask(np.roll(l._long, off), l.first_bar),
                            pn._mask(np.roll(l._short, off), l.first_bar),
                            risk)
            if nw:
                mat[k, j] = min(_pf(nw, risk), 1e6)
    import pandas as pd
    corr = pd.DataFrame(mat).corr(method="spearman",
                                  min_periods=null_baseline.MIN_DRAWS)
    c = corr.to_numpy()
    iu = np.triu_indices(len(legs), 1)
    vals = c[iu]
    vals = vals[np.isfinite(vals)]
    if not len(vals):
        return {"rho_bar": None, "pairs": 0, "draws": len(offs)}
    return {"rho_bar": round(float(vals.mean()), 4), "pairs": int(len(vals)),
            "draws": int(len(offs))}


def consistency(legs, exit_spec, risk: dict, tf: str,
                draws: int = CONSISTENCY_DRAWS, seed: int = 17,
                min_symbol_trades: int = 8,
                dependence_draws: int = DEPENDENCE_DRAWS) -> dict:
    """Per-symbol rotation percentiles and their cross-symbol p, respecting
    each leg's `first_bar` (a rotation is taken, THEN masked). Walks the
    legs' trade tables, so the null costs a walk per draw, not an engine
    run."""
    pn._prepare(legs, exit_spec, risk, tf)
    pcts, trades, per, scored = [], 0, {}, []
    for l in legs:
        lo, sh = pn._mask(l._long, l.first_bar), pn._mask(l._short,
                                                          l.first_bar)
        w = walk_table(l.table, lo, sh, risk)
        pf = _pf(w, risk)
        trades += len(w)
        per[l.symbol] = {"trades": len(w),
                         "pf": round(pf, 3) if np.isfinite(pf) else None}
        if len(w) < min_symbol_trades:
            continue
        n = len(l.df) - l.first_bar
        lo_off, hi_off = WARMUP + 1, n - WARMUP - 1
        if hi_off <= lo_off:
            continue
        rng = np.random.default_rng(seed)
        null = []
        for off in rng.integers(lo_off, hi_off, size=int(draws)):
            nw = walk_table(l.table,
                            pn._mask(np.roll(l._long, off), l.first_bar),
                            pn._mask(np.roll(l._short, off), l.first_bar),
                            risk)
            if nw:
                null.append(_pf(nw, risk))
        pct = null_baseline.edge_percentile(pf, null) \
            if len(null) >= null_baseline.MIN_DRAWS else None
        if pct is not None:
            per[l.symbol]["null_pctile"] = round(pct, 4)
            pcts.append(pct)
            scored.append(l)
    pfs = [v["pf"] for v in per.values() if v["pf"] is not None]
    dep = null_dependence(scored, risk, draws=dependence_draws, seed=seed) \
        if len(scored) >= null_baseline.MIN_SYMBOLS else {"rho_bar": None}
    rho = dep.get("rho_bar")
    return {"consistency_p": null_baseline.consistency_p(pcts),
            "consistency_p_dep": null_baseline.consistency_p_dependent(
                pcts, rho),
            "rho_bar": rho,
            "n_eff": round(null_baseline.effective_n(len(pcts), rho), 2)
            if rho is not None else None,
            "scored_symbols": len(pcts), "trades": trades,
            "median_pf": round(st.median(pfs), 3) if pfs else 0.0,
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
    # the dependence-corrected p, never the raw one: the raw binomial counts
    # one market-wide effect once per symbol (2026-09-14: Donchian on B read
    # 9.2e-05 raw, 0.24 corrected). A missing estimate is untestable.
    for name, r in (("A", a), ("B", b)):
        if r.get("consistency_p_dep") is None:
            return {"p": 1.0, "reason": f"untestable: held-out {name} has no "
                                        f"dependence estimate"}
    ps = {"A consistency": float(a["consistency_p_dep"]),
          "B consistency": float(b["consistency_p_dep"]),
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


# ── the child's work ─────────────────────────────────────────────────────
def discovery_entries(combos, bundle) -> dict:
    """{hash: encoded entry set} over the discovery bundle — what twin
    detection compares. Discovery only: no held-out price is read."""
    from ..strategy.compile import compile_spec
    out = {}
    for c in combos:
        try:
            legs = pn.legs_for(compile_spec(c.to_spec()), bundle)
            out[c.hash] = encode_entries(entry_set(legs))
        except Exception as e:                          # noqa: BLE001
            log.warning(f"referee entries {c.hash}: {e}")
    return out


def _bar_minutes(tf: str) -> float:
    from ..core.types import TF_MS
    return TF_MS.get(tf, 900_000) / 60_000


def book_fills(specs, cut: int, grid_tf: str, t0: int, cfg: dict,
               paths: dict | None, default_symbols) -> tuple[list, dict]:
    """The live book's fills over held-out B, on the candidate's bar grid.

    Each spec on its OWN declared universe and timeframe; a spec whose frame
    has no history before the cut contributes nothing, and says so."""
    from ..strategy.compile import compile_spec
    fills, notes = [], {}
    for spec in specs:
        syms = list((spec.universe or {}).get("include") or default_symbols)
        risk = {**(cfg.get("risk") or {}),
                "bar_minutes": _bar_minutes(spec.timeframe)}
        try:
            b = load_heldout(spec.timeframe, "b", syms, cut,
                             {**cfg, "risk": risk},
                             requires=tuple(spec.data_requires or ()),
                             paths=paths)
            legs = pn.legs_for(compile_spec(spec), b)
            if not legs:
                notes[spec.id] = f"no {spec.timeframe} history before the cut"
                continue
            f, n = pn.fills_for(legs, spec.exit, risk, grid_tf, t0=t0)
            fills.extend(f)
            notes[spec.id] = f"{n} trades on {len(legs)} symbols"
        except Exception as e:                          # noqa: BLE001
            notes[spec.id] = f"error: {e}"[:200]
    return fills, notes


def current_cut(tf: str, discovery_symbols, paths: dict | None,
                recorded: int) -> int:
    """The LATEST cut any discovery evaluation can have used.

    `evaluate.load_bundle` recomputes the cut from the store on every batch,
    and the store grows, so the cut drifts forward: recorded 2025-03-08,
    recomputed 2025-03-09 a week later. Held-out B must start after every
    bar discovery could have seen, so it takes the later of the two."""
    from ..data.feed import DataFeed
    from .universe import NoExchange
    feed = DataFeed(exchange=NoExchange(), db_path=(paths or {}).get("candles"))
    frames = {}
    for sym in discovery_symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception:                               # noqa: BLE001
            continue
        if df is not None and len(df) >= 500:
            frames[sym] = df
    now = slices.cut_ms(frames) or 0
    return max(int(recorded), int(now))


def examine(c, payload: dict) -> dict:
    """Gate 1 and gate 3 for one candidate. `looked` flips to True the
    moment a held-out price has been read; from then on the look counts."""
    from ..strategy.compile import compile_spec
    from ..strategy.spec import StrategySpec
    cfg, paths, tf = payload["cfg"], payload.get("paths"), c.tf
    cut = current_cut(tf, payload["discovery_symbols"], paths,
                      int(payload["cut_ms"]))
    out = {"hash": c.hash, "looked": False}
    compiled = compile_spec(c.to_spec())
    risk = cfg.get("risk") or {}

    a_b = load_heldout(tf, "a", payload["heldout_symbols"], cut, cfg,
                       requires=c.requires, paths=paths)
    out["looked"] = True
    b_b = load_heldout(tf, "b", list(payload["discovery_symbols"])
                       + list(payload["heldout_symbols"]), cut, cfg,
                       requires=c.requires, paths=paths)
    legs_a, legs_b = pn.legs_for(compiled, a_b), pn.legs_for(compiled, b_b)
    seed = int(payload.get("seed", 17))
    a = consistency(legs_a, compiled.spec.exit, risk, tf, seed=seed) \
        if legs_a else {"trades": 0, "consistency_p": None,
                        "scored_symbols": 0}
    b = consistency(legs_b, compiled.spec.exit, risk, tf, seed=seed) \
        if legs_b else {"trades": 0, "consistency_p": None,
                        "scored_symbols": 0}
    rot = pn.common_rotation(legs_b, compiled.spec.exit, risk, tf,
                             b_b.equity, b_b.risk_pct, b_b.max_open,
                             draws=int(payload["draws"]), seed=seed) \
        if legs_b else {"p": None, "reason": "no B legs"}
    for r in (a, b):
        r.pop("symbols", None)
    out.update(a=a, b=b, rotation=rot, gate1=gate1(a, b, rot), cut_ms=cut)

    if legs_b:
        t0 = min(int(pn._clock(l.df)[0]) for l in legs_b)
        cand, _ = pn.fills_for(legs_b, compiled.spec.exit, risk, tf, t0=t0)
        specs = [StrategySpec.from_dict(d) for d in payload.get("book") or []]
        book, notes = book_fills(specs, cut, tf, t0, cfg, paths,
                                 payload["discovery_symbols"])
        out["gate3"] = {**gate3(cand, book, b_b.equity, b_b.risk_pct,
                                b_b.max_open), "book": notes}
    else:
        out["gate3"] = {"passed": False, "reason": "no B legs", "book": {}}
    return out
