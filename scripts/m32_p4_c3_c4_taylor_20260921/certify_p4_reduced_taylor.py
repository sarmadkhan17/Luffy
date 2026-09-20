#!/usr/bin/env python3
"""Rigorous 1D Taylor/moment certificates for the P4/C3 and P4/C4 kernels."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from flint import arb, arb_series, ctx


STATE_THRESHOLD = "0.6744897501960817"
LINKED_THRESHOLD = "0.8416212335729143"
R_N = 10
R_D = 11
TAIL_T = 16
MAJORITY_N = 20
MAJORITY_D = 31
LIFT_RADIUS_TARGET = arb("1e-12")
ZERO_SEPARATION_TARGET = arb("1e-10")
KERNEL_IDS = {"c3": "bc9047a9a11203b3b29e", "c4": "b5e19762bc239f1aa844"}
CORRELATIONS = {
    "c3": (arb(1) / 10, arb(9) / 10),
    "c4": (arb(3) / 5, arb(2) / 5),
}


def exact_binary64(text: str) -> arb:
    numerator, denominator = float(text).as_integer_ratio()
    return arb(numerator) / denominator


def exact(value: Fraction | int) -> arb:
    value = Fraction(value)
    return arb(f"{value.numerator}/{value.denominator}")


def cdf(value):
    return (1 + (value / arb(2).sqrt()).erf()) / 2


def density(value):
    return (-value * value / 2).exp() / (2 * arb.pi()).sqrt()


def hull(lower, upper):
    lo = lower.lower()
    hi = upper.upper()
    return arb((lo + hi) / 2, (hi - lo) / 2)


def symmetric(radius):
    return arb(0, max(arb(0), radius.upper()))


def lower(value):
    return value.lower()


def upper(value):
    return value.upper()


def abs_upper(value):
    return max(abs(lower(value)), abs(upper(value)))


def radius(value):
    return (upper(value) - lower(value)) / 2


def midpoint(value):
    return (upper(value) + lower(value)) / 2


def enclosure(value):
    lo_mantissa, lo_exponent = lower(value).man_exp()
    hi_mantissa, hi_exponent = upper(value).man_exp()
    return {
        "lower": str(lower(value)),
        "upper": str(upper(value)),
        "lower_dyadic": {"mantissa": int(lo_mantissa), "exponent": int(lo_exponent)},
        "upper_dyadic": {"mantissa": int(hi_mantissa), "exponent": int(hi_exponent)},
        "decimal": value.str(55, more=True),
    }


def gaussian_moments(lo_value: Fraction, hi_value: Fraction, center_value: Fraction, degree: int) -> list[arb]:
    lo, hi, center = exact(lo_value), exact(hi_value), exact(center_value)
    moments = [cdf(hi) - cdf(lo)]
    for order in range(1, degree + 1):
        prior = (order - 1) * moments[order - 2] if order >= 2 else arb(0)
        edge = (lo - center) ** (order - 1) * density(lo) - (hi - center) ** (order - 1) * density(hi)
        moments.append(prior - center * moments[order - 1] + edge)
    return moments


def member(value, threshold: arb, systematic: arb, residual: arb):
    return cdf((systematic.sqrt() * value - threshold) / residual.sqrt())


def kernels(value, systematic: arb, residual: arb):
    r = arb(R_N) / R_D
    state = member(value, exact_binary64(STATE_THRESHOLD), systematic, residual)
    linked = member(value, exact_binary64(LINKED_THRESHOLD), systematic, residual)
    d_state = 1 - (1 - r) * state
    d_linked = 1 - (1 - r) * linked
    others = d_state**2 * d_linked**3
    return (
        d_state**2 * d_linked**4,
        r * linked * others,
        (1 - linked) * others,
    )


def integrate_band(lo_value: Fraction, hi_value: Fraction, degree: int, systematic: arb, residual: arb):
    lo, hi = exact(lo_value), exact(hi_value)
    center_value = (lo_value + hi_value) / 2
    center = (lo + hi) / 2
    band_radius = (hi - lo) / 2
    moments = gaussian_moments(lo_value, hi_value, center_value, degree)
    centered = arb_series([center, 1], degree + 1)
    span = arb_series([hull(lo, hi), 1], degree + 1)
    center_series = kernels(centered, systematic, residual)
    bound_series = kernels(span, systematic, residual)
    direct_ranges = kernels(hull(lo, hi), systematic, residual)
    values = []
    for center_value, bound_value, direct_range in zip(center_series, bound_series, direct_ranges):
        estimate = sum((center_value[k] * moments[k] for k in range(degree)), arb(0))
        remainder = abs_upper(bound_value[degree]) * band_radius**degree * moments[0]
        taylor_value = estimate + symmetric(remainder)
        direct_value = direct_range * moments[0]
        # The direct range integral is a separate rigorous guard.  Taking the
        # hull makes the result fail closed even if a high-order dependency in
        # the Taylor derivative range is unexpectedly optimistic.
        values.append(hull(min(lower(taylor_value), lower(direct_value)),
                           max(upper(taylor_value), upper(direct_value))))
    return tuple(values)


@dataclass
class Band:
    lo: Fraction
    hi: Fraction
    values: tuple[arb, arb, arb]

    def priority(self) -> float:
        return max(float(radius(value)) for value in self.values)


def clipped_probability(value: arb):
    return hull(max(arb(0), lower(value)), min(arb(1), upper(value)))


def evaluate(bands: list[Band]):
    population = sum((band.values[0] for band in bands), arb(0))
    joint_hit = sum((band.values[1] for band in bands), arb(0))
    joint_miss = sum((band.values[2] for band in bands), arb(0))
    tail = 1 - (cdf(arb(TAIL_T)) - cdf(arb(-TAIL_T)))
    population = clipped_probability(hull(population, population + tail))
    joint_hit = clipped_probability(hull(joint_hit, joint_hit + tail))
    joint_miss = clipped_probability(hull(joint_miss, joint_miss + tail))
    population = population.intersection(joint_hit + joint_miss)
    q = cdf(-exact_binary64(LINKED_THRESHOLD))
    conditional_hit = joint_hit / q
    conditional_miss = joint_miss / (1 - q)
    majority = arb(MAJORITY_N) / MAJORITY_D

    def side(value):
        if upper(value) < lower(majority):
            return "below_20_over_31"
        if lower(value) > upper(majority):
            return "above_20_over_31"
        return "unresolved"

    sides = {
        "hit": side(conditional_hit),
        "miss": side(conditional_miss),
        "population": side(population),
    }
    strict = all(value != "unresolved" for value in sides.values())
    lift = None
    classification = "refused"
    if strict and len(set(sides.values())) == 1:
        lift = arb(0)
        classification = "true_null"
    elif strict and sides == {
        "hit": "below_20_over_31",
        "miss": "above_20_over_31",
        "population": "above_20_over_31",
    }:
        f10 = 2 * (arb(13) / 20) * joint_miss / ((1 - q) + (arb(13) / 20) * population)
        f12 = 2 * (q - (arb(9) / 10) * joint_hit) / (q + 1 - (arb(9) / 10) * population)
        score = (f10 + f12) / 3
        baseline = (2 * (arb(13) / 20) * population / (1 + (arb(13) / 20) * population)) / 3
        lift = score - baseline
        classification = "non_null"

    lift_radius = radius(lift) if lift is not None else None
    zero_separation = None
    if lift is not None:
        if lower(lift) > 0:
            zero_separation = lower(lift)
        elif upper(lift) < 0:
            zero_separation = -upper(lift)
        else:
            zero_separation = arb(0)
    accepted = strict and lift is not None and lift_radius <= LIFT_RADIUS_TARGET
    if classification == "non_null":
        accepted = accepted and zero_separation >= ZERO_SEPARATION_TARGET
    return {
        "population": population,
        "joint_hit": joint_hit,
        "joint_miss": joint_miss,
        "conditional_hit": conditional_hit,
        "conditional_miss": conditional_miss,
        "sides": sides,
        "classification": classification,
        "lift": lift,
        "lift_radius": lift_radius,
        "zero_separation": zero_separation,
        "accepted": bool(accepted),
    }


def checkpoint_payload(family: str, iteration: int, bands: list[Band], result, elapsed: float):
    return {
        "schema": "m32.p4-reduced-taylor-checkpoint.v1",
        "family": family,
        "kernel_id": KERNEL_IDS[family],
        "iteration": iteration,
        "band_count": len(bands),
        "elapsed_seconds": elapsed,
        "accepted": result["accepted"],
        "classification": result["classification"],
        "majority_sides": result["sides"],
        "population_interval": enclosure(result["population"]),
        "conditional_hit_interval": enclosure(result["conditional_hit"]),
        "conditional_miss_interval": enclosure(result["conditional_miss"]),
        "signed_lift_interval": enclosure(result["lift"]) if result["lift"] is not None else None,
        "signed_lift_radius": str(result["lift_radius"]) if result["lift_radius"] is not None else None,
        "zero_separation": str(result["zero_separation"]) if result["zero_separation"] is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("c3", "c4"), required=True)
    parser.add_argument("--precision", type=int, default=224)
    parser.add_argument("--degree", type=int, default=14)
    parser.add_argument("--initial-axis", type=int, default=96)
    parser.add_argument("--max-bands", type=int, default=4096)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ctx.prec = args.precision
    systematic, residual = CORRELATIONS[args.family]
    start = time.perf_counter()
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bands = []
    for index in range(args.initial_axis):
        lo = Fraction(-TAIL_T) + Fraction(2 * TAIL_T * index, args.initial_axis)
        hi = Fraction(-TAIL_T) + Fraction(2 * TAIL_T * (index + 1), args.initial_axis)
        bands.append(Band(lo, hi, integrate_band(lo, hi, args.degree, systematic, residual)))

    iteration = 0
    while True:
        result = evaluate(bands)
        elapsed = time.perf_counter() - start
        payload = checkpoint_payload(args.family, iteration, bands, result, elapsed)
        with args.checkpoint.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        if result["accepted"] or len(bands) >= args.max_bands or elapsed >= args.max_seconds:
            break
        index = max(range(len(bands)), key=lambda position: bands[position].priority())
        old = bands.pop(index)
        middle = (old.lo + old.hi) / 2
        bands.extend(
            [
                Band(old.lo, middle, integrate_band(old.lo, middle, args.degree, systematic, residual)),
                Band(middle, old.hi, integrate_band(middle, old.hi, args.degree, systematic, residual)),
            ]
        )
        iteration += 1

    elapsed = time.perf_counter() - start
    certificate = {
        "schema": "m32.p4-reduced-taylor-certificate.v1",
        "family": args.family,
        "cell": f"P4/{args.family.upper()}",
        "kernel_id": KERNEL_IDS[args.family],
        "status": "certified" if result["accepted"] else "refused",
        "classification": result["classification"],
        "method": "rigorous_1d_arb_taylor_moment_enclosure_with_direct_range_guard",
        "deterministic": True,
        "rng_used": False,
        "protocol_changed": False,
        "full_kernel_run": False,
        "analytic_reduction_preserved": True,
        "precision_bits": args.precision,
        "taylor_degree": args.degree,
        "initial_axis": args.initial_axis,
        "final_band_count": len(bands),
        "iterations": iteration,
        "tail_limit": TAIL_T,
        "population_interval": enclosure(result["population"]),
        "joint_hit_interval": enclosure(result["joint_hit"]),
        "joint_miss_interval": enclosure(result["joint_miss"]),
        "conditional_hit_interval": enclosure(result["conditional_hit"]),
        "conditional_miss_interval": enclosure(result["conditional_miss"]),
        "majority_boundary": {"numerator": MAJORITY_N, "denominator": MAJORITY_D},
        "majority_sides": result["sides"],
        "signed_lift_interval": enclosure(result["lift"]) if result["lift"] is not None else None,
        "signed_lift_midpoint": str(midpoint(result["lift"])) if result["lift"] is not None else None,
        "signed_lift_radius": str(result["lift_radius"]) if result["lift_radius"] is not None else None,
        "zero_separation": str(result["zero_separation"]) if result["zero_separation"] is not None else None,
        "lift_radius_target": "1e-12",
        "zero_separation_target_non_null": "1e-10",
        "runtime_seconds": elapsed,
        "checkpoint": str(args.checkpoint),
    }
    args.output.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(certificate, sort_keys=True))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
