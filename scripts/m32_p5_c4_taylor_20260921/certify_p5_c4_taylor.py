#!/usr/bin/env python3
"""Rigorous deterministic P5/C4 reduced Taylor/moment certificate."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from flint import acb, arb, arb_series, ctx


T = 16
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
R = Fraction(10, 11)
COUNTS = (8, 16)
MAJORITY = Fraction(20, 31)
MAX_RADIUS = arb("1e-12")
MIN_ZERO_SEPARATION = arb("1e-10")
SPECS = (
    ("2ca6beeb42d57d3f72d6", 0, 1, False),
    ("6f3f08bc43774f2f2c2d", 0, 0, True),
    ("80c0bb2935ef7accbdc2", 1, 0, True),
    ("927c037efde9d39f01ec", 1, 0, False),
    ("cda53e68321634ca8982", 0, 0, False),
    ("dcc1c4cae778c6d4e914", 1, 1, False),
)


def A(value: Fraction | int | str) -> arb:
    if isinstance(value, str):
        return arb(value)
    value = Fraction(value)
    return arb(f"{value.numerator}/{value.denominator}")


def frozen(text: str) -> arb:
    numerator, denominator = float(text).as_integer_ratio()
    return A(Fraction(numerator, denominator))


THRESHOLDS = (frozen(STATE_TEXT), frozen(LINKED_TEXT))


def cdf(value):
    return (1 + (value / A(2).sqrt()).erf()) / 2


def qtail(value: arb) -> arb:
    return (value / A(2).sqrt()).erfc() / 2


def density(value):
    return (-value * value / 2).exp() / (2 * arb.pi()).sqrt()


def member(value, threshold: arb, systematic: Fraction, residual: Fraction):
    return cdf((A(systematic).sqrt() * value - threshold) / A(residual).sqrt())


def hull(lo: arb, hi: arb) -> arb:
    return arb((lo + hi) / 2, (hi - lo) / 2)


def symmetric(radius: arb) -> arb:
    return arb(0, max(A(0), radius.upper()))


def abs_upper(value: arb) -> arb:
    return max(abs(value.lower()), abs(value.upper()))


def intersect(left: arb, right: arb) -> arb:
    lo, hi = max(left.lower(), right.lower()), min(left.upper(), right.upper())
    if lo > hi:
        raise ArithmeticError("independent rigorous enclosures are disjoint")
    return hull(lo, hi)


def unit_interval(value: arb) -> arb:
    lo, hi = max(A(0), value.lower()), min(A(1), value.upper())
    if lo > hi:
        raise ArithmeticError("probability enclosure misses [0,1]")
    return hull(lo, hi)


def pad_tail(value: arb, tail: arb) -> arb:
    return unit_interval(value + arb(tail.upper() / 2, tail.upper() / 2))


def dyadic(value: arb) -> dict[str, int]:
    mantissa, exponent = value.man_exp()
    return {"mantissa": int(mantissa), "exponent": int(exponent)}


def enclosure(value: arb) -> dict[str, object]:
    return {
        "lower_dyadic": dyadic(value.lower()),
        "upper_dyadic": dyadic(value.upper()),
        "decimal": value.str(55, more=True),
        "radius_decimal": value.rad().str(35, more=True, radius=False),
        "width_decimal": (value.upper() - value.lower()).str(35, more=True, radius=False),
    }


def centered_moments(lo_value: Fraction, hi_value: Fraction,
                     center_value: Fraction, order: int) -> list[arb]:
    lo, hi, center = A(lo_value), A(hi_value), A(center_value)
    out = [cdf(hi) - cdf(lo)]
    plo, phi = density(lo), density(hi)
    for degree in range(1, order + 1):
        prior = A(degree - 1) * out[degree - 2] if degree >= 2 else arb(0)
        edge = ((lo - center) ** (degree - 1) * plo
                - (hi - center) ** (degree - 1) * phi)
        out.append(prior - center * out[degree - 1] + edge)
    return out


def sector_parts(value):
    p0 = member(value, THRESHOLDS[0], Fraction(3, 5), Fraction(2, 5))
    p1 = member(value, THRESHOLDS[1], Fraction(3, 5), Fraction(2, 5))
    omr = 1 - A(R)
    d0, d1 = 1 - omr * p0, 1 - omr * p1
    base = d0 ** COUNTS[0] * d1 ** COUNTS[1]
    return p0, p1, d0, d1, base


def kernel_vector(value):
    p0, p1, d0, d1, base = sector_parts(value)
    values = [base]
    for _kid, hkind, sector, target in SPECS:
        ph = (p0, p1)[hkind]
        if sector == 0:
            if target:
                reduced = (d0 ** (COUNTS[0] - int(hkind == 0))
                           * d1 ** (COUNTS[1] - int(hkind == 1)))
                hit, miss = A(R) * ph * reduced, (1 - ph) * reduced
            else:
                hit, miss = ph * base, (1 - ph) * base
        else:
            # The empty opposite sector is integrated exactly.  Conditional
            # membership has systematic/residual variance 3/20 and 17/20.
            ph = member(-value, THRESHOLDS[hkind], Fraction(3, 20), Fraction(17, 20))
            hit, miss = base * ph, base * (1 - ph)
        values.extend((hit, miss))
    return tuple(values)


def initial_intervals(count: int) -> list[tuple[Fraction, Fraction]]:
    return [(Fraction(-T) + Fraction(2 * T * index, count),
             Fraction(-T) + Fraction(2 * T * (index + 1), count))
            for index in range(count)]


def taylor_band(iv: tuple[Fraction, Fraction], order: int) -> tuple[arb, ...]:
    lo_value, hi_value = iv
    center_value = (lo_value + hi_value) / 2
    lo, hi, center = A(lo_value), A(hi_value), A(center_value)
    radius = A((hi_value - lo_value) / 2)
    moments = centered_moments(lo_value, hi_value, center_value, order)
    center_series = kernel_vector(arb_series([center, A(1)], order + 1))
    span_series = kernel_vector(arb_series([hull(lo, hi), A(1)], order + 1))
    values = []
    for centered, bounded in zip(center_series, span_series):
        estimate = sum((centered[k] * moments[k] for k in range(order)), arb(0))
        remainder = abs_upper(bounded[order]) * radius ** order * moments[0]
        values.append(estimate + symmetric(remainder))
    return tuple(values)


def taylor_integrals(axis: int, order: int, checkpoint) -> tuple[arb, ...]:
    totals = [arb(0) for _ in range(1 + 2 * len(SPECS))]
    intervals = initial_intervals(axis)
    for index, iv in enumerate(intervals, 1):
        values = taylor_band(iv, order)
        for which, value in enumerate(values):
            totals[which] += value
        if index % 10 == 0 or index == axis:
            checkpoint({"phase": "taylor", "completed_bands": index,
                        "total_bands": axis, "order": order})
    tail = 2 * qtail(A(T))
    return tuple(pad_tail(value, tail) for value in totals)


def validated_integrals(abs_tol: arb, replay: bool, checkpoint) -> tuple[arb, ...]:
    tail = 2 * qtail(A(T))
    out = []
    for index in range(1 + 2 * len(SPECS)):
        def integrand(value: acb, _analytic: int, which=index):
            return density(value) * kernel_vector(value)[which]
        result = acb.integral(
            integrand, -T, T, abs_tol=abs_tol, rel_tol=abs_tol,
            deg_limit=140 if replay else 110,
            eval_limit=900000 if replay else 600000,
            depth_limit=800 if replay else 640,
            use_heap=not replay,
        ).real
        out.append(pad_tail(result, tail))
        checkpoint({"phase": "validated_integral", "primitive": index,
                    "primitive_count": 1 + 2 * len(SPECS)})
    return tuple(out)


def class_probs(survival: arb) -> tuple[arb, arb, arb]:
    return A(Fraction(13, 20)) * survival, A(Fraction(1, 4)) * survival, 1 - A(Fraction(9, 10)) * survival


def winner(probabilities: tuple[arb, arb, arb]) -> int | None:
    candidates = [index for index in range(3)
                  if all(index == other or probabilities[index].lower() > probabilities[other].upper()
                         for other in range(3))]
    return candidates[0] if len(candidates) == 1 else None


def macro(ph: tuple[arb, ...], pm: tuple[arb, ...], q: arb,
          pred_hit: int, pred_miss: int) -> arb:
    population = [q * ph[c] + (1 - q) * pm[c] for c in range(3)]
    terms = []
    for c in range(3):
        tp = ((q * ph[c] if pred_hit == c else 0)
              + ((1 - q) * pm[c] if pred_miss == c else 0))
        predicted = ((q if pred_hit == c else 0)
                     + ((1 - q) if pred_miss == c else 0))
        terms.append(2 * tp / (2 * tp + (predicted - tp) + (population[c] - tp)))
    return sum(terms, arb(0)) / 3


def classify(population: arb, joint_hit: arb, joint_miss: arb, hkind: int):
    q = qtail(THRESHOLDS[hkind])
    population = intersect(population, joint_hit + joint_miss)
    hit, miss = unit_interval(joint_hit / q), unit_interval(joint_miss / (1 - q))
    ph, pm, pp = class_probs(hit), class_probs(miss), class_probs(population)
    wins = winner(ph), winner(pm), winner(pp)
    if None in wins:
        return {"accepted": False, "reason": "possible_interval_argmax_tie",
                "hit": hit, "miss": miss, "population": population,
                "winners": wins, "lift": None}
    if wins[0] == wins[1] == wins[2]:
        lift, kind = arb(0), "true_null"
    else:
        lift = macro(ph, pm, q, wins[0], wins[1]) - macro(ph, pm, q, wins[2], wins[2])
        kind = "non_null"
    separation = (lift.lower() if lift.lower() > 0 else
                  -lift.upper() if lift.upper() < 0 else A(0))
    accepted = lift.rad() <= MAX_RADIUS
    if kind == "non_null":
        accepted = accepted and separation >= MIN_ZERO_SEPARATION
    return {"accepted": bool(accepted), "reason": "accepted" if accepted else "lift_threshold_failed",
            "classification": kind, "hit": hit, "miss": miss,
            "population": population, "winners": wins, "lift": lift,
            "zero_separation": separation}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision", type=int, default=224)
    parser.add_argument("--order", type=int, default=14)
    parser.add_argument("--axis", type=int, default=80)
    parser.add_argument("--abs-tol", default="1e-16")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.precision < 192 or A(args.abs_tol) > A("1e-14"):
        raise SystemExit("fail closed: weak precision or tolerance")

    ctx.prec = args.precision
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with args.checkpoint.open("w", encoding="utf-8") as handle:
        sequence = 0
        def checkpoint(record):
            nonlocal sequence
            sequence += 1
            handle.write(json.dumps({"schema": "m32.p5-c4-checkpoint.v1",
                                     "sequence": sequence, "replay": args.replay,
                                     "precision_bits": args.precision, **record},
                                    sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
        taylor = taylor_integrals(args.axis, args.order, checkpoint)
        validated = validated_integrals(A(args.abs_tol), args.replay, checkpoint)

    # The hull is a fail-closed outward guard over two independently evaluated
    # rigorous enclosures; it also exposes any practically relevant drift.
    guarded = tuple(hull(min(x.lower(), y.lower()), max(x.upper(), y.upper()))
                    for x, y in zip(taylor, validated))
    common_population = guarded[0]
    results = {}
    accepted = True
    for index, (kernel_id, hkind, sector, target) in enumerate(SPECS):
        result = classify(common_population, guarded[1 + 2 * index], guarded[2 + 2 * index], hkind)
        accepted = accepted and result["accepted"]
        results[kernel_id] = {
            "spec": {"scenario": 5, "correlation": 4, "h_kind": hkind,
                     "h_sector": sector, "is_target": target,
                     "r_n": 10, "r_d": 11,
                     "sector_counts": [[8, 16], [0, 0]]},
            "status": "certified" if result["accepted"] else "refused",
            "classification": result.get("classification", "refused"),
            "reason": result["reason"],
            "strict_argmax": {"hit": result["winners"][0],
                              "miss": result["winners"][1],
                              "population": result["winners"][2]},
            "conditional_hit_interval": enclosure(result["hit"]),
            "conditional_miss_interval": enclosure(result["miss"]),
            "population_interval": enclosure(result["population"]),
            "signed_lift_interval": enclosure(result["lift"]) if result["lift"] is not None else None,
            "signed_lift_radius": result["lift"].rad().str(35, more=True, radius=False) if result["lift"] is not None else None,
            "zero_separation": result.get("zero_separation", A(0)).str(35, more=True, radius=False),
        }
    elapsed = time.perf_counter() - started
    payload = {
        "schema": "m32.p5-c4-reduced-taylor-certificate.v1",
        "cell": "P5/C4", "status": "certified" if accepted else "refused",
        "method": "rigorous_1d_Arb_centered_Taylor_moments_with_validated_integral_guard",
        "rng_used": False, "broader_kernel_run": False,
        "p6_c3_touched": False, "protocol_changed": False,
        "analytic_reduction_preserved": True,
        "precision_bits": args.precision, "taylor_order": args.order,
        "axis": args.axis, "validated_abs_tol": args.abs_tol,
        "replay": args.replay, "runtime_seconds": elapsed,
        "lift_radius_target": "1e-12",
        "zero_separation_target_non_null": "1e-10",
        "kernels": results,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
