#!/usr/bin/env python3
"""Rigorous isolated P7/C4 categorical PGF capability spike.

No RNG is constructed.  The 73 threshold events (72 P7 categorical targets
plus the conditioning event for non-target h=24) are reduced to the two C4
sector factors.  Composite interval boxes are integrated against exact Arb
normal cell masses.  The omitted Gaussian tails are added explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from fractions import Fraction
from pathlib import Path

import flint
from flint import arb, ctx


HERE = Path(__file__).resolve().parent
SCHEMA = "m3.2-p7-c4-categorical-pgf-arb-spike.v1"
HYPOTHESIS = 24
DELTA_TRUTH = Fraction(1, 10_000_000_000)
MAX_RADIUS = Fraction(1, 1_000_000_000_000)
T = 10


def exact_arb(value: Fraction | int) -> arb:
    value = Fraction(value)
    return arb(f"{value.numerator}/{value.denominator}")


def float_input(text: str) -> tuple[arb, dict[str, str]]:
    """Enclose the exact IEEE-754 binary64 obtained from frozen decimal text."""
    value = float(text)
    numerator, denominator = value.as_integer_ratio()
    return exact_arb(Fraction(numerator, denominator)), {
        "decimal_text": text,
        "binary64_hex": value.hex(),
        "exact_binary64_rational": f"{numerator}/{denominator}",
    }


def phi_cdf(x: arb) -> arb:
    return (1 + (x / exact_arb(2).sqrt()).erf()) / 2


def upper_tail(x: arb) -> arb:
    return (x / exact_arb(2).sqrt()).erfc() / 2


def dyadic(value: arb) -> dict[str, int]:
    mantissa, exponent = value.man_exp()
    return {"mantissa": int(mantissa), "exponent": int(exponent)}


def interval_record(value: arb) -> dict[str, object]:
    lo, hi = value.lower(), value.upper()
    return {
        "lower_dyadic": dyadic(lo),
        "upper_dyadic": dyadic(hi),
        "decimal": value.str(30, more=True),
        "radius_decimal": value.rad().str(12, more=True, radius=False),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def membership_probability(factor: arb, threshold: arb) -> arb:
    loading = (exact_arb(3) / 5).sqrt()
    residual = (exact_arb(2) / 5).sqrt()
    return phi_cdf((loading * factor - threshold) / residual)


def integrands(x: arb, y: arb, state_t: arb, linked_t: arb) -> tuple[arb, arb]:
    # C4: corr(F_A,F_B)=-1/2, represented by two independent N(0,1)s.
    factor_a = x
    factor_b = -x / 2 + exact_arb(3).sqrt() * y / 2
    p_a_state = membership_probability(factor_a, state_t)
    p_a_linked = membership_probability(factor_a, linked_t)
    p_b_state = membership_probability(factor_b, state_t)
    p_b_linked = membership_probability(factor_b, linked_t)
    one_minus_r = exact_arb(Fraction(1, 21))
    # P7 categorical targets: A=(16 state,24 linked), B=(8 state,24 linked).
    pgf_a = (1 - one_minus_r * p_a_state) ** 16 * (1 - one_minus_r * p_a_linked) ** 24
    pgf_b = (1 - one_minus_r * p_b_state) ** 8 * (1 - one_minus_r * p_b_linked) ** 24
    pgf = pgf_a * pgf_b
    condition_h24 = p_b_state
    return pgf, pgf * condition_h24


def integrate_boxes(partitions: int, state_t: arb, linked_t: arb) -> tuple[arb, arb, arb]:
    edges = [exact_arb(Fraction(-T * partitions + 2 * T * i, partitions))
             for i in range(partitions + 1)]
    masses = [phi_cdf(edges[i + 1]) - phi_cdf(edges[i]) for i in range(partitions)]
    boxes = []
    for i in range(partitions):
        midpoint = (edges[i] + edges[i + 1]) / 2
        radius = (edges[i + 1] - edges[i]) / 2
        boxes.append(arb(midpoint, radius))

    population = arb(0)
    conditioned_numerator = arb(0)
    for i, x in enumerate(boxes):
        wx = masses[i]
        for j, y in enumerate(boxes):
            weight = wx * masses[j]
            pgf, pgf_conditioned = integrands(x, y, state_t, linked_t)
            population += weight * pgf
            conditioned_numerator += weight * pgf_conditioned

    # Union bound for the complement of [-T,T]^2 under independent x,y.
    outside_square = 4 * upper_tail(exact_arb(T))
    tail_upper = outside_square.upper()
    nonnegative_tail = arb(tail_upper / 2, tail_upper / 2)
    return population + nonnegative_tail, conditioned_numerator + nonnegative_tail, outside_square


def certify(precision_bits: int, partitions: int) -> dict[str, object]:
    ctx.prec = precision_bits
    state_t, state_input = float_input("0.6744897501960817")
    linked_t, linked_input = float_input("0.8416212335729143")
    started = time.perf_counter()
    population_pgf, conditioned_num, outside_square = integrate_boxes(
        partitions, state_t, linked_t
    )
    condition_probability = upper_tail(state_t)
    conditioned_pgf = conditioned_num / condition_probability
    population_majority_margin = 1 - exact_arb(Fraction(31, 20)) * population_pgf
    conditioned_majority_margin = 1 - exact_arb(Fraction(31, 20)) * conditioned_pgf
    elapsed = time.perf_counter() - started

    majority_proved = population_majority_margin > 0 and conditioned_majority_margin > 0
    if not majority_proved:
        classification = "unclassifiable"
        final_metric = None
        tolerance_met = False
    else:
        # Both classifiers predict class 2 everywhere. Their macro-F1 values
        # are therefore symbolically identical, not merely numerically close.
        classification = "true_null"
        final_metric = arb(0)
        tolerance_met = final_metric.rad() <= exact_arb(MAX_RADIUS)

    tail_conditional = outside_square / condition_probability.lower()
    return {
        "schema": SCHEMA,
        "case": "P7/C4 categorical h=24 (non-target, sector B)",
        "classification": classification,
        "sign": None,
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "full_orthant_engine_constructed": False,
        "raw_threshold_event_dimension": 73,
        "factorized_integration_dimension": 2,
        "exact_structure": {
            "odds_ratio": "3/2",
            "pgf_argument": "20/21",
            "C4_factor_correlation": "-1/2",
            "C4_systematic_variance": "3/5",
            "C4_residual_variance": "2/5",
            "sector_A_target_counts": {"state_q_1_4": 16, "linked_q_1_5": 24},
            "sector_B_target_counts": {"state_q_1_4": 8, "linked_q_1_5": 24},
            "conditioning_event": "h24 state membership in sector B",
        },
        "frozen_binary64_inputs": {"state_threshold": state_input, "linked_threshold": linked_input},
        "arithmetic": {
            "python_flint": flint.__version__,
            "flint": flint.__FLINT_VERSION__,
            "precision_bits": precision_bits,
            "partitions_per_axis": partitions,
            "directed_rounding": "Arb midpoint-radius balls with rigorous outward error bounds",
        },
        "gaussian_tail_bound": {
            "truncation_box": f"[-{T},{T}]^2 in independent factor coordinates",
            "method": "P(outside) <= 4*Q(10), Q(x)=erfc(x/sqrt(2))/2 evaluated by Arb",
            "outside_square": interval_record(outside_square),
            "population_pgf_additive_contribution": interval_record(outside_square),
            "conditioned_pgf_additive_contribution_upper_bound": interval_record(tail_conditional),
        },
        "quadrature": {
            "method": "composite Arb interval boxes weighted by Arb-enclosed exact Gaussian cell masses",
            "population_pgf_enclosure": interval_record(population_pgf),
            "conditioned_numerator_enclosure": interval_record(conditioned_num),
            "condition_probability_enclosure": interval_record(condition_probability),
            "conditioned_pgf_enclosure": interval_record(conditioned_pgf),
            "population_enclosure_radius": population_pgf.rad().str(12, more=True, radius=False),
            "conditioned_enclosure_radius": conditioned_pgf.rad().str(12, more=True, radius=False),
        },
        "majority_proof": {
            "criterion": "class 2 is unique majority iff 1-(31/20)*E[(20/21)^K] > 0",
            "population_margin": interval_record(population_majority_margin),
            "conditioned_margin": interval_record(conditioned_majority_margin),
            "both_unique_class_2": majority_proved,
            "exact_metric_identity": "same constant class-2 classifier => macro-F1 lift = 0 exactly",
        },
        "truth_rule": {
            "delta_truth": "1e-10",
            "maximum_final_radius": "1e-12",
            "non_null_zero_exclusion_applicable": False,
            "final_signed_metric_enclosure": interval_record(final_metric) if final_metric is not None else None,
            "tolerance_met": tolerance_met,
        },
        "runtime_seconds": elapsed,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "status": "PASS" if tolerance_met else "FAIL_CLOSED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision-bits", type=int, default=128)
    parser.add_argument("--partitions", type=int, default=128)
    parser.add_argument("--output", type=Path, default=HERE / "certificate.json")
    args = parser.parse_args()
    if args.precision_bits < 96 or args.partitions < 16:
        raise SystemExit("fail closed: precision must be >=96 bits and partitions >=16")
    result = certify(args.precision_bits, args.partitions)
    result["implementation_sha256"] = sha256(Path(__file__))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
