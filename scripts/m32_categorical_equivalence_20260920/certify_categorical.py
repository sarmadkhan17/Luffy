#!/usr/bin/env python3
"""Certify P4--P7/C1--C4 categorical truth by factor equivalence classes.

This is a pre-RNG proof program.  It constructs no generator, world, sample,
permutation, bootstrap, or optimization.  Gaussian threshold probabilities are
reduced to the frozen one-factor, nested-one-factor, and two-factor forms and
enclosed with python-flint Arb balls plus explicit Gaussian tail bounds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from collections import Counter
from fractions import Fraction
from pathlib import Path

import flint
from flint import arb, ctx


HERE = Path(__file__).resolve().parent
CERTIFICATE = HERE / "certificate.json"
SCHEMA = "m3.2-categorical-equivalence-certificate.v1"
T = 10
DELTA = Fraction(1, 10_000_000_000)
MAX_RADIUS = Fraction(1, 1_000_000_000_000)
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
INJECTIONS = {
    4: (0, 33, 130, 163, 260, 293, 326, 359),
    5: tuple(x for c in range(4) for x in
             (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)),
    6: tuple(range(0, 8)) + tuple(range(136, 144)) + tuple(range(272, 280)) + tuple(range(344, 352)),
    7: (tuple(range(0, 24)) + tuple(range(136, 160)) + tuple(range(272, 288))
        + tuple(range(288, 296)) + tuple(range(320, 336)) + tuple(range(344, 352))),
}
ODDS = {4: Fraction(2), 5: Fraction(2), 6: Fraction(2), 7: Fraction(3, 2)}
CAT_H = tuple(range(128)) + tuple(range(256, 384))


def a(value: Fraction | int) -> arb:
    value = Fraction(value)
    return arb(f"{value.numerator}/{value.denominator}")


def frozen(text: str) -> tuple[arb, dict[str, str]]:
    value = float(text)
    n, d = value.as_integer_ratio()
    return a(Fraction(n, d)), {
        "decimal_text": text,
        "binary64_hex": value.hex(),
        "exact_binary64_rational": f"{n}/{d}",
    }


def cdf(x: arb) -> arb:
    return (1 + (x / a(2).sqrt()).erf()) / 2


def qtail(x: arb) -> arb:
    return (x / a(2).sqrt()).erfc() / 2


def membership(factor: arb, threshold: arb, systematic: Fraction, residual: Fraction) -> arb:
    return cdf((a(systematic).sqrt() * factor - threshold) / a(residual).sqrt())


def pad_nonnegative(value: arb, tail: arb) -> arb:
    upper = tail.upper()
    return value + arb(upper / 2, upper / 2)


def dyadic(value: arb) -> dict[str, int]:
    m, e = value.man_exp()
    return {"mantissa": int(m), "exponent": int(e)}


def interval(value: arb) -> dict[str, object]:
    return {
        "lower_dyadic": dyadic(value.lower()),
        "upper_dyadic": dyadic(value.upper()),
        "decimal": value.str(30, more=True),
        "radius_decimal": value.rad().str(14, more=True, radius=False),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kind(h: int) -> int:
    return 0 if h < 256 else 1


def cluster(h: int) -> int:
    return h % 32


def sector(h: int) -> int:
    return 0 if cluster(h) < 16 else 1


def categorical_targets(scenario: int) -> tuple[int, ...]:
    return tuple(h for h in INJECTIONS[scenario] if h not in range(128, 256))


def composition(values: tuple[int, ...]) -> tuple[int, int]:
    return sum(kind(h) == 0 for h in values), sum(kind(h) == 1 for h in values)


def boxes(n: int) -> tuple[list[arb], list[arb]]:
    edges = [a(Fraction(-T * n + 2 * T * i, n)) for i in range(n + 1)]
    masses = [cdf(edges[i + 1]) - cdf(edges[i]) for i in range(n)]
    cells = [arb((edges[i] + edges[i + 1]) / 2,
                 (edges[i + 1] - edges[i]) / 2) for i in range(n)]
    return cells, masses


def factor_product(ps: tuple[arb, arb], counts: tuple[int, int], r: arb) -> arb:
    return ((1 - (1-r)*ps[0]) ** counts[0] *
            (1 - (1-r)*ps[1]) ** counts[1])


def remove_one(counts: tuple[int, int], which: int) -> tuple[int, int]:
    out = list(counts)
    out[which] -= 1
    if out[which] < 0:
        raise AssertionError("target missing from equivalence composition")
    return out[0], out[1]


def integrate_1d(spec: dict, thresholds: tuple[arb, arb], n: int) -> tuple[arb, arb, arb, arb]:
    cells, masses = boxes(n)
    systematic = Fraction(3, 10) if spec["correlation"] == 1 else Fraction(7, 10)
    residual = 1 - systematic
    r = a(Fraction(spec["r_n"], spec["r_d"]))
    counts = tuple(spec["target_counts"])
    pop = hit = miss = arb(0)
    for z, weight in zip(cells, masses):
        ps = (membership(z, thresholds[0], systematic, residual),
              membership(z, thresholds[1], systematic, residual))
        ph = ps[spec["h_kind"]]
        if spec["is_target"]:
            base = factor_product(ps, remove_one(counts, spec["h_kind"]), r)
            pop_i = base * (1-(1-r)*ph)
            hit_i, miss_i = ph*r*base, (1-ph)*base
        else:
            base = factor_product(ps, counts, r)
            pop_i = base
            hit_i, miss_i = ph*base, (1-ph)*base
        pop += weight*pop_i
        hit += weight*hit_i
        miss += weight*miss_i
    tail = 2*qtail(a(T))
    q = qtail(thresholds[spec["h_kind"]])
    return (pad_nonnegative(pop, tail), pad_nonnegative(hit, tail)/q,
            pad_nonnegative(miss, tail)/(1-q), tail)


def cluster_counts(targets: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    return tuple(composition(tuple(h for h in targets if cluster(h) == c)) for c in range(32))


def inner_cluster(g: arb, count: tuple[int, int], thresholds: tuple[arb, arb],
                  r: arb, cells: list[arb], masses: list[arb], h_kind: int | None,
                  target: bool | None) -> arb:
    total = arb(0)
    for z, weight in zip(cells, masses):
        mean = a(Fraction(1, 10)).sqrt()*g + a(Fraction(3, 5)).sqrt()*z
        ps = (cdf((mean-thresholds[0])/a(Fraction(3, 10)).sqrt()),
              cdf((mean-thresholds[1])/a(Fraction(3, 10)).sqrt()))
        if h_kind is None:
            value = factor_product(ps, count, r)
        else:
            ph = ps[h_kind]
            if target:
                base = factor_product(ps, remove_one(count, h_kind), r)
                value = ph*r*base
            else:
                value = ph*factor_product(ps, count, r)
        total += weight*value
    # This inner result is a function enclosure for every g in the outer box.
    return pad_nonnegative(total, 2*qtail(a(T)))


def integrate_c3(spec: dict, thresholds: tuple[arb, arb], n: int) -> tuple[arb, arb, arb, arb]:
    cells, masses = boxes(n)
    r = a(Fraction(spec["r_n"], spec["r_d"]))
    counts = tuple(tuple(x) for x in spec["cluster_counts"])
    focal = spec["h_cluster"]
    pop = hit = miss = arb(0)
    inner_tail = 2*qtail(a(T))
    outer_tail = 2*qtail(a(T))
    for g, weight in zip(cells, masses):
        base_by_count: dict[tuple[int, int], arb] = {}
        for count in set(counts):
            base_by_count[count] = inner_cluster(g, count, thresholds, r, cells, masses, None, None)
        other = arb(1)
        for idx, count in enumerate(counts):
            if idx != focal:
                other *= base_by_count[count]
        focal_base = base_by_count[counts[focal]]
        joint_hit = inner_cluster(g, counts[focal], thresholds, r, cells, masses,
                                  spec["h_kind"], spec["is_target"])
        # Direct complement integration is tighter and avoids dependent subtraction.
        focal_miss = arb(0)
        for z, wz in zip(cells, masses):
            mean = a(Fraction(1, 10)).sqrt()*g + a(Fraction(3, 5)).sqrt()*z
            ps = (cdf((mean-thresholds[0])/a(Fraction(3, 10)).sqrt()),
                  cdf((mean-thresholds[1])/a(Fraction(3, 10)).sqrt()))
            ph = ps[spec["h_kind"]]
            if spec["is_target"]:
                value = (1-ph)*factor_product(ps, remove_one(counts[focal], spec["h_kind"]), r)
            else:
                value = (1-ph)*factor_product(ps, counts[focal], r)
            focal_miss += wz*value
        focal_miss = pad_nonnegative(focal_miss, inner_tail)
        pop += weight*other*focal_base
        hit += weight*other*joint_hit
        miss += weight*other*focal_miss
    q = qtail(thresholds[spec["h_kind"]])
    # Outer complement bounds each complete integrand by one.  The inner tail
    # has already been enclosed independently in every cluster integral.
    return (pad_nonnegative(pop, outer_tail), pad_nonnegative(hit, outer_tail)/q,
            pad_nonnegative(miss, outer_tail)/(1-q), inner_tail+outer_tail)


def integrate_c4(spec: dict, thresholds: tuple[arb, arb], n: int) -> tuple[arb, arb, arb, arb]:
    cells, masses = boxes(n)
    r = a(Fraction(spec["r_n"], spec["r_d"]))
    counts = tuple(tuple(x) for x in spec["sector_counts"])
    pop = hit = miss = arb(0)
    for i, x in enumerate(cells):
        for j, y in enumerate(cells):
            factors = (x, -x/2 + a(3).sqrt()*y/2)
            ps = tuple((membership(f, thresholds[0], Fraction(3, 5), Fraction(2, 5)),
                        membership(f, thresholds[1], Fraction(3, 5), Fraction(2, 5)))
                       for f in factors)
            ph = ps[spec["h_sector"]][spec["h_kind"]]
            products = [factor_product(ps[s], counts[s], r) for s in range(2)]
            pop_i = products[0]*products[1]
            if spec["is_target"]:
                focal = factor_product(ps[spec["h_sector"]],
                                       remove_one(counts[spec["h_sector"]], spec["h_kind"]), r)
                other = products[1-spec["h_sector"]]
                hit_i, miss_i = ph*r*focal*other, (1-ph)*focal*other
            else:
                hit_i, miss_i = ph*pop_i, (1-ph)*pop_i
            weight = masses[i]*masses[j]
            pop += weight*pop_i
            hit += weight*hit_i
            miss += weight*miss_i
    tail = 4*qtail(a(T))
    q = qtail(thresholds[spec["h_kind"]])
    return (pad_nonnegative(pop, tail), pad_nonnegative(hit, tail)/q,
            pad_nonnegative(miss, tail)/(1-q), tail)


def kernel_spec(s: int, c: int, h: int) -> dict:
    targets = categorical_targets(s)
    r = Fraction(10, 9 + ODDS[s])
    spec = {"scenario": s, "correlation": c, "h_kind": kind(h),
            "is_target": h in targets, "r_n": r.numerator, "r_d": r.denominator}
    if c in (1, 2):
        spec["target_counts"] = composition(targets)
    elif c == 3:
        raw_counts = cluster_counts(targets)
        focal = cluster(h)
        # Conditional on the global factor, cluster factors are iid.  Put the
        # focal cluster first and sort the other composition multiset so
        # literal cluster labels cannot duplicate a mathematical kernel.
        spec["h_cluster"] = 0
        spec["cluster_counts"] = (raw_counts[focal],) + tuple(
            sorted(raw_counts[:focal] + raw_counts[focal+1:]))
    else:
        spec["h_sector"] = sector(h)
        spec["sector_counts"] = tuple(
            composition(tuple(x for x in targets if sector(x) == sec)) for sec in range(2))
    return spec


def key(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def classify(values: tuple[arb, arb, arb, arb]) -> tuple[str, list[arb]]:
    margins = [1-a(Fraction(31, 20))*value for value in values[:3]]
    signs = [1 if margin > 0 else -1 if margin < 0 else 0 for margin in margins]
    # Common unique majority makes the subgroup and population classifiers
    # algebraically identical.  The signed macro-F1 lift is then exactly zero.
    if signs[0] and len(set(signs)) == 1:
        return "true_null", margins
    return "unclassifiable", margins


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision-bits", type=int, default=160)
    parser.add_argument("--partitions-1d", type=int, default=512)
    parser.add_argument("--partitions-c3", type=int, default=96)
    parser.add_argument("--partitions-c4", type=int, default=128)
    parser.add_argument("--output", type=Path, default=CERTIFICATE)
    args = parser.parse_args()
    if args.precision_bits < 128 or min(args.partitions_1d, args.partitions_c3, args.partitions_c4) < 32:
        raise SystemExit("fail closed: insufficient precision or partitions")
    ctx.prec = args.precision_bits
    state, state_input = frozen(STATE_TEXT)
    linked, linked_input = frozen(LINKED_TEXT)
    thresholds = state, linked
    started = time.perf_counter()

    specs: dict[str, dict] = {}
    assignments: list[dict] = []
    for s in range(4, 8):
        for c in range(1, 5):
            for h in CAT_H:
                spec = kernel_spec(s, c, h)
                kernel_id = key(spec)
                specs[kernel_id] = spec
                assignments.append({"cell": f"P{s}/C{c}", "hypothesis": h, "kernel_id": kernel_id})

    kernels = {}
    for kernel_id, spec in sorted(specs.items()):
        c = spec["correlation"]
        n = args.partitions_1d if c in (1, 2) else args.partitions_c3 if c == 3 else args.partitions_c4
        values = (integrate_1d(spec, thresholds, n) if c in (1, 2) else
                  integrate_c3(spec, thresholds, n) if c == 3 else
                  integrate_c4(spec, thresholds, n))
        classification, margins = classify(values)
        kernels[kernel_id] = {
            "spec": spec,
            "formulation": "factorized_1d" if c in (1, 2) else "nested_1d" if c == 3 else "factorized_2d",
            "partitions_per_axis": n,
            "gaussian_tail_bound": {
                "truncation": f"[-{T},{T}]" if c != 4 else f"[-{T},{T}]^2",
                "method": "2*Q(10) per nested/1D integration" if c != 4 else "4*Q(10) union bound",
                "enclosure": interval(values[3]),
            },
            "population_pgf": interval(values[0]),
            "hit_pgf": interval(values[1]),
            "miss_pgf": interval(values[2]),
            "majority_margins": {name: interval(value) for name, value in zip(("population", "hit", "miss"), margins)},
            "classification": classification,
            "sign": None,
            "final_signed_metric_enclosure": interval(arb(0)) if classification == "true_null" else None,
            "proof": ("all population/hit/miss margins have the same strict sign; both classifiers are the same "
                      "constant majority classifier, hence macro-F1 lift equals 0 algebraically") if classification == "true_null" else
                     "fail closed: at least one majority margin is uncertified or the three majorities differ",
        }

    for row in assignments:
        result = kernels[row["kernel_id"]]
        row["classification"] = result["classification"]
        row["sign"] = result["sign"]
    refused_kernels = [k for k, v in kernels.items() if v["classification"] == "unclassifiable"]
    refused_rows = [r for r in assignments if r["classification"] == "unclassifiable"]
    refused_cells = sorted({r["cell"] for r in refused_rows})
    elapsed = time.perf_counter()-started
    payload = {
        "schema": SCHEMA,
        "scope": "categorical P4-P7/C1-C4 only; P7/C4 persistence excluded",
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "full_m32_validation_run": False,
        "delta_truth": "1e-10",
        "maximum_final_radius": "1e-12",
        "exact_null_rule": "algebraic common-majority identity only",
        "frozen_binary64_inputs": {"state_threshold": state_input, "linked_threshold": linked_input},
        "arithmetic": {"python_flint": flint.__version__, "flint": flint.__FLINT_VERSION__,
                       "precision_bits": args.precision_bits, "directed_rounding": "Arb midpoint-radius balls"},
        "counts": {"rows_attempted": len(assignments), "rows_certified": len(assignments)-len(refused_rows),
                   "rows_refused": len(refused_rows), "kernels_attempted": len(kernels),
                   "kernels_certified": len(kernels)-len(refused_kernels), "kernels_refused": len(refused_kernels),
                   "cells_attempted": 16, "cells_certified": 16-len(refused_cells),
                   "cells_refused": len(refused_cells)},
        "refused_cells": refused_cells,
        "kernels": kernels,
        "row_assignments": assignments,
        "worst_final_enclosure_radius": "0" if not refused_rows else None,
        "remaining_unresolved_categorical_cells": refused_cells,
        "only_p7c4_persistence_remains": not refused_rows,
        "runtime_seconds": elapsed,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "implementation_sha256": sha256(Path(__file__)),
        "status": "PASS" if not refused_rows else "FAIL_CLOSED",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("status", "counts", "refused_cells", "runtime_seconds", "peak_rss_kib")}, indent=2, sort_keys=True))
    if refused_rows:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
