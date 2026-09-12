"""What the child process does, and nothing else.

Two entry points, both module-level so `multiprocessing` spawn can import
them, both taking and returning plain dicts so everything crosses the pipe.

The child READS the candle, derivative and reference stores and returns
results. It never writes a database: the kernel thread writes through the
journal's own transaction wrapper, so the kernel remains the only writer of
truth.

It also watches a SOFT deadline of its own, ahead of the hard one
`run_child` enforces. A batch that runs long returns what it finished
instead of being killed with everything lost; the combinations it did not
reach are simply not in the ledger, and the planner offers them again.
"""
from __future__ import annotations

import time

from ..strategy.features import FeatureCtx
from . import evaluate as ev
from . import slices, thresholds
from .combo import Combination


def _ctxs(b) -> list:
    """One FeatureCtx per discovery symbol, carrying the same context an
    evaluation gets — otherwise a threshold is measured on a quantity that
    evaluates to NaN when it is actually used."""
    out = []
    for sym in b.frames:
        out.append(FeatureCtx(
            frames=b.sym_frames[sym], tf=b.tf, btc=b.btc,
            derivs=b.derivs.get(sym), universe=b.universe, market=b.market,
            symbol=sym))
    return out


def measure_job(payload: dict) -> dict:
    """Percentiles for every gauge, and how much of the slice it exists on."""
    t0 = time.monotonic()
    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(payload.get("requires") or ("ohlcv",)),
                       heldout_symbols=tuple(
                           payload.get("heldout_symbols") or ()),
                       paths=payload.get("paths"))
    exprs = payload.get("exprs")
    if not exprs:
        from .vocab import expressions
        exprs = expressions(payload["tf"])
    rcfg = (payload["cfg"].get("research") or {})
    gauges = thresholds.measure(
        exprs, _ctxs(b),
        min_samples=int(rcfg.get("min_threshold_samples",
                                 thresholds.MIN_SAMPLES)),
        min_finite_frac=float(rcfg.get("min_finite_frac",
                                       thresholds.MIN_FINITE_FRAC)))
    return {"tf": b.tf, "cut_ms": int(b.cut), "gauges": gauges,
            "counts": {"discovery": slices.bar_counts(b.frames),
                       "heldout_a": b.heldout_bars.get("a", {}),
                       "heldout_b": b.heldout_bars.get("b", {})},
            "elapsed_s": round(time.monotonic() - t0, 2)}


def evaluate_job(payload: dict) -> dict:
    """Score a batch of combinations that share one horizon and geometry.

    One combination never costs the batch. A rule that raises is recorded
    as a LOOK that failed, with its reason, and the batch carries on --
    the time axis and the failure axis get the same treatment.
    """
    t0 = time.monotonic()
    combos = [Combination.from_dict(d) for d in payload.get("combos") or []]
    soft = float(payload.get("soft_deadline_s", 600.0))
    if not combos:
        return {"results": [], "done": 0, "skipped": 0, "elapsed_s": 0.0}

    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(payload.get("requires") or ("ohlcv",)),
                       heldout_symbols=tuple(
                           payload.get("heldout_symbols") or ()),
                       paths=payload.get("paths"))
    results, skipped = [], 0
    for c in combos:
        if time.monotonic() - t0 >= soft:
            skipped += 1
            continue
        try:
            results.append(ev.evaluate(
                c, b, draws=int(payload.get("draws", 30)),
                seed=int(payload.get("seed", 17))))
        except Exception as exc:
            # A failure is a LOOK with a reason, never a deferral.
            # `skipped` means "the deadline hit, come back to it", so a
            # failure routed there would be re-offered by the planner
            # forever and one poisonous rule would stall its window,
            # re-burning the whole child timeout on every attempt.
            # `error` is the key growth.decide reads; `window` keeps the
            # row filed under its own window instead of defaulting to ohlcv.
            results.append({"hash": c.hash, "tf": c.tf, "geo": c.geo,
                            "k": c.k, "window": c.window,
                            "parts": list(c.keys), "round": c.round,
                            "parent": c.parent, "trigger": c.trigger,
                            "trades": 0, "verdict": "error",
                            "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"results": results, "done": len(results), "skipped": skipped,
            "elapsed_s": round(time.monotonic() - t0, 2)}
