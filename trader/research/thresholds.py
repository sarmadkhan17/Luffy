"""Measure every gauge on the discovery slice: where its percentiles sit,
and whether it exists there at all.

The second half is the part that keeps the search honest. A derivative or
reference series that begins after the discovery cut evaluates to NaN on
every bar, and a NaN comparison is False — so a mechanism built on it takes
zero trades and reads in a results table as "no edge" rather than "never
tested". `finite_frac` turns that silence into a number, and a gauge below
`min_finite_frac` contributes no parts at all.

Measured 2026-09-11: open interest and the account ratio begin 2025-10-03
while the 4h discovery cut lands ~2025-05, so the positioning family drops
out of the 4h vocabulary by measurement rather than by a hard-coded rule.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..strategy import dsl
from .vocab import QUANTILES

log = logging.getLogger(__name__)

#: pooled samples below this cannot place a 10th/90th percentile worth using
MIN_SAMPLES = 5000
#: a gauge finite over less of the slice than this is not measurable here
MIN_FINITE_FRAC = 0.5


def _values(tree, ctx) -> np.ndarray:
    v = dsl.evaluate(tree, ctx)
    if isinstance(v, pd.Series):
        return v.to_numpy(dtype=float)
    return np.full(len(ctx.index), float(v), dtype=float)


def measure(exprs, ctxs, min_samples: int = MIN_SAMPLES,
            min_finite_frac: float = MIN_FINITE_FRAC) -> dict:
    """{expr: {p10, p25, p75, p90, n, finite_frac, usable}}.

    `ctxs` are ready-built FeatureCtx objects — one per discovery symbol,
    already carrying the universe, the leader, the derivatives and the
    reference frames, all truncated at the cut. Samples POOL across symbols:
    a threshold that differs per symbol could not be written into an
    expression that trades a universe.
    """
    out: dict = {}
    for expr in exprs:
        try:
            tree = dsl.parse(expr)
        except Exception as e:                          # noqa: BLE001
            out[expr] = {"usable": False, "error": str(e), "n": 0,
                         "finite_frac": 0.0,
                         **{f"p{int(round(q * 100))}": None for q in QUANTILES}}
            continue
        total, vals = 0, []
        for ctx in ctxs:
            try:
                arr = _values(tree, ctx)
            except Exception as e:                      # noqa: BLE001
                log.debug(f"threshold {expr} on {ctx.symbol}: {e}")
                continue
            total += int(arr.size)
            vals.append(arr[np.isfinite(arr)])
        finite = np.concatenate(vals) if vals else np.array([], dtype=float)
        n = int(finite.size)
        frac = (n / total) if total else 0.0
        rec = {"n": n, "finite_frac": round(frac, 4)}
        usable = n >= int(min_samples) and frac >= float(min_finite_frac)
        for q in QUANTILES:
            key = f"p{int(round(q * 100))}"
            rec[key] = (round(float(np.quantile(finite, q)), 8)
                        if usable else None)
        # the observed range: a `<` cut at or below it, or a `>` cut at or
        # above it, can never fire — see vocab.parts_for's `_attainable`.
        rec["min"] = round(float(finite.min()), 8) if usable else None
        rec["max"] = round(float(finite.max()), 8) if usable else None
        # a constant quantity has no percentiles worth comparing against
        if usable and rec["p10"] == rec["p90"]:
            usable = False
            for q in QUANTILES:
                rec[f"p{int(round(q * 100))}"] = None
            rec["min"] = None
            rec["max"] = None
        rec["usable"] = bool(usable)
        out[expr] = rec
    return out
