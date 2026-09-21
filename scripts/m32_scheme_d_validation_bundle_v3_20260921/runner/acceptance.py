"""Pure acceptance evaluators for the M3.2 Scheme D validation (protocol section 12).

No random generator is constructed here.  The single bootstrap function takes an
injected generator object, so a caller decides (and is gated on) RNG use.
"""
from __future__ import annotations

import math
from fractions import Fraction

Z = 1.959964
B_RESAMPLES = 10_000
UPPER_INDEX = 9_749            # the 9,750th order statistic, zero-based
MARGINAL_T = (Fraction(1, 100), Fraction(5, 100), Fraction(1, 10))
CP_ALPHA = Fraction(1, 6000)   # 0.01 / 60 comparisons (3 t-values x 20 null cells)
WORLD_REFUSAL_LIMIT = Fraction(1, 100)
HYPOTHESIS_REFUSAL_LIMIT = Fraction(1, 100)
POWER_RULES = {                # scenario -> ((count threshold, Wilson lower minimum), ...)
    101: ((1, 0.80),), 102: ((1, 0.80),), 103: ((1, 0.80),),
    104: ((1, 0.90), (4, 0.75)),
    105: ((1, 0.95), (16, 0.75)), 106: ((1, 0.95), (16, 0.75)),
}
FDR_SCENARIOS = (101, 102, 103, 104, 105, 106)


def wilson(k: int, n: int) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def type_one_pass(rejecting_worlds: int, worlds: int) -> bool:
    """Both the point estimate and the Wilson upper bound gates (section 12.1)."""
    if worlds <= 0:
        return False
    return rejecting_worlds / worlds <= 0.05 and wilson(rejecting_worlds, worlds)[1] <= 0.06


def fdr_pass(fdps: list[float], bootstrap_upper_bound: float) -> bool:
    if not fdps:
        return False
    return sum(fdps) / len(fdps) <= 0.05 and bootstrap_upper_bound <= 0.06


def bootstrap_upper(fdps: list[float], integers) -> float:
    """World bootstrap: 10,000 resamples, 9,750th order statistic of the means."""
    import numpy as np
    values = np.asarray(fdps, dtype=np.float64)
    n = len(values)
    means = np.empty(B_RESAMPLES, dtype=np.float64)
    for start in range(0, B_RESAMPLES, 500):
        index = integers(0, n, size=(500, n))
        means[start:start + 500] = values[index].mean(axis=1)
    return float(np.sort(means)[UPPER_INDEX])


def _cdf_numerator_le(k: int, n: int, t: Fraction) -> tuple[int, int]:
    a, b = t.numerator, t.denominator
    total = sum(math.comb(n, i) * a ** i * (b - a) ** (n - i) for i in range(k + 1))
    return total, b ** n


def cp_band_contains(k: int, n: int, t: Fraction, alpha: Fraction = CP_ALPHA) -> bool:
    """Exact two-sided Clopper-Pearson band (alpha/2 per tail) contains t."""
    half = alpha / 2
    if k > 0:
        num, den = _cdf_numerator_le(k - 1, n, t)
        if Fraction(den - num, den) < half:      # P_t(X >= k) < alpha/2: t below the band
            return False
    if k < n:
        num, den = _cdf_numerator_le(k, n, t)
        if Fraction(num, den) < half:            # P_t(X <= k) < alpha/2: t above the band
            return False
    return True


def calibration_pass(marginal_p: list[Fraction | None]) -> dict[str, bool]:
    """One entry per non-refused world; None = h(w) was refused, so the world is excluded from the ECDF (owner decision I6)."""
    marginal_p = [p for p in marginal_p if p is not None]
    n = len(marginal_p)
    out = {}
    for t in MARGINAL_T:
        k = sum(1 for p in marginal_p if p <= t)
        out[str(t)] = n > 0 and cp_band_contains(k, n, t)
    return out


def power_pass(scenario: int, true_discoveries: list[int]) -> bool:
    """One entry per world (refused worlds count as 0): section 12.4."""
    n = len(true_discoveries)
    if n == 0 or scenario not in POWER_RULES:
        return False
    return all(wilson(sum(1 for d in true_discoveries if d >= need), n)[0] >= minimum
               for need, minimum in POWER_RULES[scenario])


def refusal_blockers(worlds: int, refused_worlds: int, refused_pairs: int) -> dict:
    tested = worlds - refused_worlds
    world_rate = Fraction(refused_worlds, worlds) if worlds else Fraction(1)
    pair_rate = Fraction(refused_pairs, 384 * tested) if tested else Fraction(1)
    return {"world_refusal_rate": float(world_rate), "hypothesis_refusal_rate": float(pair_rate),
            "blocks": world_rate > WORLD_REFUSAL_LIMIT or pair_rate > HYPOTHESIS_REFUSAL_LIMIT}


def evaluate_cell(kind: str, scenario: int | None, worlds: list[dict], bootstrap_integers=None) -> dict:
    """worlds: per-world summaries (keys: refused, refused_hypotheses, R, V, true_discoveries, marginal_p)."""
    refused = sum(1 for w in worlds if w["refused"])
    pairs = sum(w["refused_hypotheses"] for w in worlds if not w["refused"])
    valid = [w for w in worlds if not w["refused"]]
    result = {"worlds": len(worlds), "non_refused": len(valid),
              "refusal": refusal_blockers(len(worlds), refused, pairs)}
    gates = {"refusal_blocker_absent": not result["refusal"]["blocks"]}
    if kind == "null":
        rejecting = sum(1 for w in valid if w["R"] > 0)
        gates["type_one"] = type_one_pass(rejecting, len(valid))
        cal = calibration_pass([w["marginal_p"] for w in valid])
        gates["marginal_calibration"] = all(cal.values())
        result["type_one_rate"] = rejecting / len(valid) if valid else None
        result["calibration"] = cal
        result["calibration_worlds"] = sum(1 for w in valid if w["marginal_p"] is not None)
    else:
        if scenario in FDR_SCENARIOS:
            fdps = [w["V"] / max(w["R"], 1) for w in valid]
            upper = bootstrap_upper(fdps, bootstrap_integers) if bootstrap_integers else None
            gates["fdr"] = upper is not None and fdr_pass(fdps, upper)
            result["mean_fdp"] = sum(fdps) / len(fdps) if fdps else None
            result["fdp_bootstrap_upper"] = upper
            gates["power"] = power_pass(scenario, [0 if w["refused"] else w["true_discoveries"] for w in worlds])
        result["attribution_errors"] = sum(w.get("attribution_errors", 0) for w in valid)
    result["gates"] = gates
    result["passes"] = all(gates.values())
    return result


def overall(cells: dict[str, dict], integrity_stop: bool = False, truth_refusal: bool = False) -> dict:
    """Section 12.5: every acceptance cell must pass; P7 is descriptive only."""
    failing = sorted(name for name, c in cells.items()
                     if not (c["gates"]["refusal_blocker_absent"] if name.startswith("P7") else c["passes"]))
    return {"passes": not failing and not integrity_stop and not truth_refusal,
            "failing_cells": failing, "integrity_stop": integrity_stop,
            "truth_table_refusal": truth_refusal, "cells_evaluated": len(cells)}
