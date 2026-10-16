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

from copy import deepcopy

import logging
import sqlite3
import statistics as st
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..world.context import WorldContext

import numpy as np
import pandas as pd

from ..strategy import null_baseline, spec_evidence
from ..strategy.compile import compile_spec
from ..strategy.portfolio_evidence import Fill, portfolio_curve, accounting_provenance, RESEARCH_CLOCK
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
    world: WorldContext | None = None  # same contract as compiled strategies
    # requirement name -> first observation (ms) of each loaded symbol's
    # series, BEFORE the cut: how much history exists, not how much the
    # slice holds. `derivs` is already cut, so it cannot say that.
    first_obs: dict = field(default_factory=dict)
    # Why each requested symbol is absent from `frames` — diagnostic only.
    # {symbol: {"reason": ..., ...}}; nothing here feeds an evaluation.
    requested: tuple = ()
    dropped: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)


def is_transient_read_error(exc: BaseException) -> bool:
    """A store read that failed because the store was busy, not because the
    history is absent: a retry can succeed, so it must never be counted as a
    permanent missing-history verdict."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return any(k in msg for k in ("locked", "busy", "disk i/o"))


def load_bundle(tf: str, symbols, cfg: dict, requires=(),
                heldout_symbols=(), paths: dict | None = None,
                cap_ms: int | None = None,
                min_symbols: int | None = None) -> Bundle:
    """Read the stores, cut at the discovery boundary, build the context.

    With a caller's symbol floor, a short candle bundle omits auxiliary
    reads. The child still returns the existing admit_bundle diagnosis;
    this shortcut neither scores nor admits that incomplete bundle.
    """
    from ..data.derivatives import DerivFeed
    from ..data.feed import DataFeed
    from ..data.references import RefStore
    from .universe import NoExchange

    paths = paths or {}
    # NoExchange lives in universe.py and is the ONE definition: a DataFeed
    # built for research reads must never construct a ccxt client, and an
    # accidental use of it raises instead of quietly fetching — a silent
    # fetch inside a niced child would be invisible.
    retrospective = (cfg.get("research") or {}).get("retrospective")
    if retrospective is not None:
        if not isinstance(retrospective, dict) or not retrospective:
            raise ValueError('explicit retrospective research requires a nonempty dataset contract')
        return _retrospective_bundle(tf, symbols, cfg, requires,
                                     heldout_symbols, paths, cap_ms, min_symbols)
    feed = DataFeed(exchange=NoExchange(), db_path=paths.get("candles"))
    full = {}
    dropped = {}
    for sym in symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research load {sym} {tf}: {e}")
            dropped[sym] = {"reason": "read_error",
                            "transient": is_transient_read_error(e),
                            "error": f"{type(e).__name__}: {e}"[:200]}
            continue
        if df is not None and len(df) >= 500:
            full[sym] = df
        else:
            dropped[sym] = {"reason": "short_history",
                            "bars": 0 if df is None else int(len(df))}

    split = (cfg.get('research') or {}).get('predictive_split')
    if split:
        full = {s: slices.before(slices.after(d, split['start_ms']), split['end_ms'] + 1)
                for s, d in full.items()}
    cut = int(split['cut_ms']) if split else (slices.cut_ms(full) or 0)
    if cap_ms:      # a spent held-out look read bars from here on
        cut = min(cut, int(cap_ms))
    disc = slices.discovery(full, cut)
    for sym, df in full.items():
        if sym not in disc:
            dropped[sym] = {"reason": "short_before_cut",
                            "bars": int(len(slices.before(df, cut)))}

    # held-out BAR COUNTS only — never their prices. The projection needs to
    # know how much room a rule would have to fire in, which is metadata.
    held_full = {}
    for sym in heldout_symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception:                               # noqa: BLE001
            continue
        if df is not None and len(df) >= 500:
            held_full[sym] = (slices.before(slices.after(df, split['start_ms']), cut)
                              if split else df)
    heldout_bars = {"a": slices.bar_counts(held_full),
                    "b": slices.bar_counts(slices.heldout_b(full, cut))}

    sym_frames = {}
    for sym, df in disc.items():
        try:
            sym_frames[sym] = spec_evidence.frames_for(df, tf)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research frames_for {sym} {tf}: {e}")
            dropped[sym] = {"reason": "frames_error",
                            "error": f"{type(e).__name__}: {e}"[:200]}
    disc = {s: d for s, d in disc.items() if s in sym_frames}
    if min_symbols is not None and len(disc) < int(min_symbols):
        requires = ("ohlcv",)

    derivs = {}
    first_obs: dict = {}
    if any(r not in ("ohlcv", "world") and not r.startswith("ref:") for r in requires):
        dfeed = DerivFeed(db_path=paths.get("derivs"))
        for sym in disc:
            got = spec_evidence.load_derivs(sym, requires, dfeed)
            for req, series in spec_evidence._SERIES_FOR.items():
                if series in got and req in requires:
                    first_obs.setdefault(req, []).append(
                        int(slices._ms(got[series]).min()))
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
                first_obs[f"ref:{k}"] = [int(slices._ms(df).min())]
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
        max_open=int(rcfg.get("max_open_trades", 8)),
        world=load_world(paths, requires, cut), first_obs=first_obs,
        requested=tuple(symbols), dropped=dropped)


def authenticated_discovery_contract(cfg, tf, cap_ms=None):
    """Authenticate the preserved boundary using counts/cuts, without prices."""
    import hashlib
    from pathlib import Path
    contract = dict(cfg["research"]["retrospective"])
    path = contract.get("discovery_cut_source")
    if not path:
        raise ValueError("retrospective_discovery_missing_preserved_cut_source")
    digest_state = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest_state.update(chunk)
    digest = digest_state.hexdigest()
    expected = contract.get("discovery_cut_source_sha256")
    if not expected:
        raise ValueError("retrospective_discovery_missing_cut_source_hash")
    if digest != expected:
        raise ValueError("retrospective_discovery_cut_source_hash_mismatch")
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = con.execute("SELECT cut_ms FROM research_slices WHERE tf=?", (tf,)).fetchone()
    finally:
        con.close()
    if not row or int(row[0]) != int(contract["cut_ms"]):
        raise ValueError("retrospective_discovery_preserved_cut_mismatch")
    contract["discovery_cut_source_sha256"] = digest
    if cap_ms is not None:
        contract["cut_ms"] = min(int(contract["cut_ms"]), int(cap_ms))
    return contract


def _retrospective_bundle(tf, symbols, cfg, requires, heldout_symbols, paths, cap_ms,
                          min_symbols=None):
    """Discovery prices are selected before the SQL read; mirrors are counts only."""
    import hashlib
    import json
    from ..data.feed import DataFeed
    from ..data.derivatives import DerivFeed
    from ..data.references import RefStore
    from .universe import NoExchange, MIN_BARS, HELDOUT
    protected = set(HELDOUT) | set(heldout_symbols)
    if protected.intersection(symbols):
        raise ValueError('retrospective_discovery_protected_symbol_refused')
    contract = authenticated_discovery_contract(cfg, tf, cap_ms)
    cut = int(contract["cut_ms"])
    contract.update(cut_ms=cut, paths=dict(paths))
    feed = DataFeed(exchange=NoExchange(), db_path=paths.get("candles"),
                    retrospective=contract)
    frames, sym_frames, dropped = {}, {}, {}
    identities = {}
    for sym in symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
            inventory = feed.retrospective_coverage(
                sym, tf, closed_before_ms=253402300799000, metadata_only=True)
            if (inventory["bars"] < MIN_BARS or df is None
                    or len(df) < slices.MIN_SLICE_BARS):
                dropped[sym] = {"reason": "short_before_cut",
                                "bars": 0 if df is None else len(df)}
                continue
            sf = spec_evidence.frames_for(df, tf)
            frames[sym], sym_frames[sym] = df, sf
            identities[sym] = dict(df.attrs)
        except Exception as exc:
            dropped[sym] = {"reason": "read_error",
                            "transient": is_transient_read_error(exc),
                            "error": f"{type(exc).__name__}: {exc}"[:200]}
    # A uses the distinct held-out markets; B uses the later discovery era.
    # Coverage is permitted timestamp/count metadata, never a loaded frame.
    counts = {"a": {}, "b": {}}
    for part, syms in (("a", heldout_symbols), ("b", symbols)):
        for sym in syms:
            try:
                meta = feed.retrospective_coverage(
                    sym, tf, start_ms=(cut if part == "b" else contract.get("start_ms")),
                    end_ms=contract.get("end_ms"),
                    closed_before_ms=contract.get("end_ms") or 253402300799000,
                    metadata_only=True)
                if int(meta["bars"]) >= (MIN_BARS if part == "a" else slices.MIN_SLICE_BARS):
                    counts[part][sym] = int(meta["bars"])
            except (ValueError, sqlite3.Error):
                continue
    if min_symbols is not None and len(frames) < int(min_symbols):
        requires = ("ohlcv",)
    derivs, first_obs = {sym: {} for sym in frames}, {}
    dfeed, store = None, None
    derivative_requirements = [r for r in requires if r in spec_evidence._SERIES_FOR]
    if derivative_requirements:
        if not paths.get("derivs"):
            raise ValueError("retrospective_dependency_missing: derivatives dataset path")
        dfeed = DerivFeed(db_path=paths["derivs"], retrospective=contract)
        for sym in frames:
            got = spec_evidence.load_derivs(sym, derivative_requirements, dfeed)
            for req in derivative_requirements:
                series = spec_evidence._SERIES_FOR[req]
                if series not in got:
                    raise ValueError(f"retrospective_dependency_missing: {sym} {req}")
                first_obs.setdefault(req, []).append(int(slices._ms(got[series]).min()))
            derivs[sym] = got
    market = {}
    ref_keys = [r.split(":", 1)[1] for r in requires if r.startswith("ref:")]
    if ref_keys:
        store = RefStore(paths.get("refs") or paths["candles"], retrospective=contract)
        for key in ref_keys:
            df = store.load(key)
            if df is None or not len(df):
                raise ValueError(f"retrospective_dependency_missing: ref:{key}")
            market[key] = df
            first_obs[f"ref:{key}"] = [int(slices._ms(df).min())]
    identity = {"kind": "retrospective_discovery", "factory_validated": False,
                "dataset_id": contract["dataset_id"], "cut_ms": cut,
                "configuration": deepcopy(cfg), "inputs": deepcopy(identities),
                "implementation_sources": spec_evidence.quant_contract.retrospective_source_identity(),
                "dependencies": {"derivs": {sym: {key: dict(df.attrs)
                    for key, df in got.items()} for sym, got in derivs.items()},
                    "refs": {key: dict(df.attrs) for key, df in market.items()}},
                "compiler": hashlib.sha256(__import__("pathlib").Path(
                    spec_evidence.__file__).with_name("compile.py").read_bytes()).hexdigest(),
                "evaluation": hashlib.sha256(__import__("pathlib").Path(__file__).read_bytes()).hexdigest()}
    identity["identity"] = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                                      default=str).encode()).hexdigest()
    risk = cfg.get("risk") or {}
    return Bundle(tf=tf, frames=frames, sym_frames=sym_frames,
                  universe={sym: {tf: df} for sym, df in frames.items()},
                  btc=sym_frames.get("BTC/USDT"), market=market or None,
                  derivs=derivs, risk=spec_evidence.risk_for(risk, tf), cut=cut,
                  heldout_bars=counts, equity=float(cfg.get("research", {}).get("equity", 2000)),
                  risk_pct=float(risk.get("risk_per_trade_pct", .5)),
                  max_open=int(risk.get("max_open_trades", 8)),
                  world=load_world(paths, requires, cut), first_obs=first_obs,
                  requested=tuple(symbols), dropped=dropped, evidence=identity)


def load_world(paths, requires, cut=None):
    if "world" not in requires:
        return None
    from ..world.context import load_context, WorldContext
    if paths and "world" not in paths:
        # A detached/frozen dataset cannot silently acquire a source from
        # today's production directory when it did not retain that source.
        from ..world import WorldHistory
        return WorldContext(WorldHistory(()), "SOURCE_NOT_RETAINED_IN_BUNDLE", cut)
    return load_context(paths.get("world"), as_of_ms=cut)


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
                         "order": [], "accounting": accounting_provenance(
                             b.equity, b.risk_pct, b.max_open, RESEARCH_CLOCK)},
           "projection": {"markets_a": 0, "markets_b": 0},
           "testable": False, "verdict": "empty"}
    if b.evidence:
        import hashlib
        import json
        evidence = dict(b.evidence, draws=int(draws), seed=int(seed),
                        min_symbol_trades=int(min_symbol_trades), combination=c.as_dict())
        evidence["evaluation_identity"] = hashlib.sha256(json.dumps(
            evidence, sort_keys=True, default=str).encode()).hexdigest()
        evidence["cost_assumptions"] = {
            "funding": "retained_signed_funding" if b.risk.get("real_funding", True)
                       else "explicit_estimated_flat_funding",
            "funding_rate_8h": b.risk.get("funding_rate_8h"),
            "taker_fee_pct": b.risk.get("taker_fee_pct"),
            "slippage_atr_frac": b.risk.get("slippage_atr_frac")}
        out["evidence"] = evidence
    try:
        compiled = compile_spec(spec)
    except Exception as e:                              # noqa: BLE001
        out["verdict"] = "error"
        out["error"] = str(e)
        return out

    if b.evidence:
        gaps = {}
        for sym, df in b.frames.items():
            missing = [req for req in compiled.data_requires
                       if req in spec_evidence._SERIES_FOR
                       and spec_evidence._SERIES_FOR[req] not in b.derivs.get(sym, {})]
            missing += [req for req in compiled.data_requires
                        if req.startswith("ref:") and req.split(":", 1)[1] not in (b.market or {})]
            if missing:
                gaps[sym] = [f"{req}: retrospective dependency missing" for req in missing]
        if gaps:
            out.update(verdict="untested", untested=True, context_gaps=gaps)
            return out
    if b.evidence or "world" in compiled.data_requires:
        gaps = spec_evidence.missing_data(spec, list(b.frames), frames=b.frames, world=b.world)
        if gaps:
            out.update(verdict="untested", untested=True, context_gaps=gaps)
            return out
    retrospective_funding = {}
    if b.evidence:
        for sym, df in b.frames.items():
            try:
                retrospective_funding[sym] = funding_for(sym, df, b.risk)
            except (ValueError, sqlite3.Error) as exc:
                out.update(verdict="untested", untested=True,
                           context_gaps={sym: [f"funding: {exc}"]})
                return out
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
                universe=b.universe, market=b.market, symbol=sym, world=b.world)
            fund = retrospective_funding.get(sym) if b.evidence else funding_for(sym, df, b.risk)
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
                entry_i=int(clock[f.entry_i] - t0),
                exit_i=int(clock[f.exit_i] - t0),
                r_multiple=f.r_multiple, symbol=sym))

        if r.trades < min_symbol_trades:
            continue
        try:
            a = null_baseline.assess(
                compiled, sf, b.risk, r.profit_factor, btc=b.btc,
                derivs=b.derivs.get(sym), universe=b.universe, world=b.world,
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
            "taken": curve["taken"], "order": curve["order"],
            "accounting": {**curve["accounting"], "clock": RESEARCH_CLOCK}}
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
