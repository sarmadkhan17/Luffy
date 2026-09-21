#!/usr/bin/env python3
"""Merge the nine per-kernel certificates and deterministically check the replay.

Reads primary/<id>.json and replay/<id>.json, writes primary.json, replay.json
(multi-kernel format shared with the P5/C4 and P6/C3 families) and
replay_result.json.  Interval checks use exact rational (dyadic) arithmetic.
A float Gauss-Hermite cross-check (independent of Arb) is recorded as a sanity
check only; it is not evidence.  Fails closed on any discrepancy.
"""
from __future__ import annotations

import json
import math
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import certify_remaining as cr  # noqa: E402

MAJ, DELTA, RAD = Fraction(20, 31), Fraction(1, 10**10), Fraction(1, 10**12)
FIELDS = ("conditional_hit_interval", "conditional_miss_interval", "population_interval",
          "joint_hit_interval", "joint_miss_interval", "signed_lift_interval")


def dy(d): return Fraction(int(d["mantissa"])) * Fraction(2) ** int(d["exponent"])
def iv(r): return dy(r["lower_dyadic"]), dy(r["upper_dyadic"])
def overlap(a, b):
    (al, ah), (bl, bh) = iv(a), iv(b)
    return max(al, bl) <= min(ah, bh)
def cls(rec):
    lo, hi = iv(rec)
    return 0 if lo > MAJ else 2 if hi < MAJ else None


def float_estimate(kid: str):
    """Non-rigorous float64 Gauss-Hermite estimate of (population, hit, miss)."""
    import numpy as np
    from numpy.polynomial.hermite_e import hermegauss
    erf = np.vectorize(math.erf)
    ndtr = lambda v: 0.5 * (1 + erf(np.asarray(v) / math.sqrt(2)))
    t = (float(cr.STATE_TEXT), float(cr.LINKED_TEXT))
    r = cr.R_N / cr.R_D
    omr = 1 - r
    scen, corr, hk, target, geo = cr.KERNELS[kid]
    x, w = hermegauss(90)
    w = w / w.sum()
    sv = lambda p0, p1, c: (1 - omr * p0) ** c[0] * (1 - omr * p1) ** c[1]
    def focal(p0, p1, counts):
        ph = (p0, p1)[hk]
        if target:
            rest = list(counts); rest[hk] -= 1
            base = sv(p0, p1, rest)
            return sv(p0, p1, counts), r * ph * base, (1 - ph) * base
        base = sv(p0, p1, counts)
        return base, ph * base, (1 - ph) * base
    if corr == 3:
        fc, n_other = geo
        G, Z = np.meshgrid(x, x, indexing="ij")
        f = math.sqrt(.1) * G + math.sqrt(.6) * Z
        p0, p1 = (ndtr((f - tt) / math.sqrt(.3)) for tt in t)
        cp = sv(p0, p1, (2, 4))
        if fc == (0, 0):
            fh, fm = (p0, p1)[hk], 1 - (p0, p1)[hk]
        else:
            _, fh, fm = focal(p0, p1, fc)
        Cp, Ch, Cm = ((w[None, :] * a).sum(1) for a in (cp, fh, fm))
        power = n_other + (1 if fc != (0, 0) else 0)
        pop = (w * Cp ** power).sum(); hit = (w * Ch * Cp ** n_other).sum(); miss = (w * Cm * Cp ** n_other).sum()
    else:
        sec, counts = geo
        X, Y = np.meshgrid(x, x, indexing="ij")
        W = np.outer(w, w)
        fs = (X, -X / 2 + math.sqrt(3) / 2 * Y)
        P = [[ndtr((math.sqrt(.6) * ff - tt) / math.sqrt(.4)) for tt in t] for ff in fs]
        tri = []
        for s in range(2):
            tri.append(focal(*P[s], counts[s]) if s == sec else (lambda b: (b, b, b))(sv(*P[s], counts[s])))
        pop = (W * tri[0][0] * tri[1][0]).sum(); hit = (W * tri[0][1] * tri[1][1]).sum()
        miss = (W * tri[0][2] * tri[1][2]).sum()
    return pop, hit, miss


def main() -> int:
    prim, repl, rows = {}, {}, []
    for kid in cr.REMAINING:
        prim[kid] = json.loads((HERE / "primary" / f"{kid}.json").read_text())
        repl[kid] = json.loads((HERE / "replay" / f"{kid}.json").read_text())
    ok_all = True
    for kid in cr.REMAINING:
        p, r = prim[kid], repl[kid]
        spec = cr.frozen_spec(kid)
        checks = {
            "id_matches_spec": cr.kernel_id_of(p["spec"]) == kid and p["spec"] == r["spec"] == spec,
            "both_certified": p["status"] == r["status"] == "certified",
            "no_rng_no_search": not any((p[k] or r[k]) for k in ("rng_constructed", "protocol_changed", "search_performed")),
            "classification_match": p["classification"] == r["classification"],
            "winner_triple_match": p["strict_argmax"] == r["strict_argmax"],
            "majority_triple_from_intervals": (
                [cls(p[k]) for k in ("conditional_hit_interval", "conditional_miss_interval", "population_interval")]
                == [cls(r[k]) for k in ("conditional_hit_interval", "conditional_miss_interval", "population_interval")]
                and None not in [cls(p[k]) for k in ("conditional_hit_interval", "conditional_miss_interval", "population_interval")]),
            "intervals_overlap": all(overlap(p[f], r[f]) for f in FIELDS),
            "distinct_configuration": (p["precision_bits"] < r["precision_bits"] and p["initial_axis"] != r["initial_axis"]
                                       and p["reverse_inner_traversal"] != r["reverse_inner_traversal"]),
        }
        lifts = [iv(x["signed_lift_interval"]) for x in (p, r)]
        radii = [(hi - lo) / 2 for lo, hi in lifts]
        seps = [min(abs(lo), abs(hi)) if not lo <= 0 <= hi else Fraction(0) for lo, hi in lifts]
        nonnull = p["classification"] == "non_null"
        checks["lift_radius_le_1e-12"] = all(x <= RAD for x in radii)
        checks["zero_exclusion_ge_1e-10"] = (not nonnull) or all(s >= DELTA for s in seps)
        checks["sign_agrees"] = len({(lo > 0) - (hi < 0) for lo, hi in lifts}) == 1
        # sanity only: float64 Gauss-Hermite estimate lies inside the certified intervals (tolerance 1e-7)
        est = float_estimate(kid)
        tol = Fraction(1, 10**7)
        inside = []
        for value, key in zip(est, ("population_interval", "joint_hit_interval", "joint_miss_interval")):
            lo, hi = iv(p[key])
            inside.append(lo - tol <= Fraction(float(value)) <= hi + tol)
        checks["float_gauss_hermite_sanity"] = all(inside)
        ok = all(checks.values())
        ok_all &= ok
        rows.append({"kernel_id": kid, "result": "PASS" if ok else "FAIL_CLOSED", "checks": checks,
                     "primary_lift_radius": str(float(radii[0])), "replay_lift_radius": str(float(radii[1])),
                     "primary_lift_decimal": p["signed_lift_interval"]["decimal"][:60],
                     "replay_lift_decimal": r["signed_lift_interval"]["decimal"][:60],
                     "primary_zero_separation": p["zero_separation"], "replay_zero_separation": r["zero_separation"]})
    ok_all &= len(rows) == 9
    for name, src in (("primary", prim), ("replay", repl)):
        any_k = next(iter(src.values()))
        payload = {
            "schema": f"m3.2-remaining-categorical-kernels-{name}.v1",
            "cells": ["P5/C3", "P6/C4"], "status": "certified" if all(k["status"] == "certified" for k in src.values()) else "refused",
            "rng_used": False, "protocol_changed": False, "search_performed": False,
            "precision_bits": any_k["precision_bits"], "taylor_order": any_k["taylor_order"],
            "axis": any_k["initial_axis"], "reverse_inner_traversal": any_k["reverse_inner_traversal"],
            "lift_radius_target": "1e-12", "zero_separation_target_non_null": "1e-10",
            "kernels": {k: src[k] for k in sorted(src)}}
        (HERE / f"{name}.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    result = {"schema": "m3.2-remaining-categorical-kernels-replay-check.v1",
              "result": "PASS" if ok_all else "FAIL_CLOSED", "kernels_checked": len(rows),
              "primary_precision_bits": next(iter(prim.values()))["precision_bits"],
              "replay_precision_bits": next(iter(repl.values()))["precision_bits"], "rows": rows}
    (HERE / "replay_result.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"result": result["result"], "kernels_checked": len(rows),
                      "failing": [r["kernel_id"] for r in rows if r["result"] != "PASS"]}))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
