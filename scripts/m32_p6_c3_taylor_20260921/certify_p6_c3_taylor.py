#!/usr/bin/env python3
"""Rigorous deterministic P6/C3 Taylor/moment certificate.

This preserves the frozen C3 global/cluster Gaussian reduction.  Singleton
and empty-cluster factors are collapsed analytically; the four remaining
focal joint-membership primitives use nested centered Taylor moments.  No RNG
or synthetic world is constructed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from flint import arb, arb_series, ctx


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "m32_categorical_arb_batch_20260920" / "certificate.json"
ESTIMAND = HERE.parent / "m32_categorical_equivalence_20260920" / "estimand_spec.json"
T = 16
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
R = Fraction(10, 11)
MAJORITY = Fraction(20, 31)
MAX_RADIUS = Fraction(1, 10**12)
MIN_ZERO_SEPARATION = Fraction(1, 10**10)
EXPECTED_COUNTS = Counter({(0, 0): 8, (0, 1): 16, (1, 0): 8})
Interval = tuple[Fraction, Fraction]


class Deadline(Exception):
    pass


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


def density(value):
    return (-value * value / 2).exp() / (2 * arb.pi()).sqrt()


def hull(lo: arb, hi: arb) -> arb:
    return arb((lo + hi) / 2, (hi - lo) / 2)


def symmetric(radius: arb) -> arb:
    return arb(0, max(A(0), radius.upper()))


def abs_upper(value: arb) -> arb:
    return max(abs(value.lower()), abs(value.upper()))


def unit_interval(value: arb) -> arb:
    lo, hi = max(A(0), value.lower()), min(A(1), value.upper())
    if lo > hi:
        raise ArithmeticError("probability enclosure misses [0,1]")
    return hull(lo, hi)


def intersect(left: arb, right: arb) -> arb:
    lo, hi = max(left.lower(), right.lower()), min(left.upper(), right.upper())
    if lo > hi:
        raise ArithmeticError("independent rigorous enclosures are disjoint")
    return hull(lo, hi)


def enclosure(value: arb) -> dict[str, object]:
    lm, le = value.lower().man_exp()
    um, ue = value.upper().man_exp()
    return {
        "lower_dyadic": {"mantissa": int(lm), "exponent": int(le)},
        "upper_dyadic": {"mantissa": int(um), "exponent": int(ue)},
        "decimal": value.str(55, more=True),
        "radius_decimal": value.rad().str(35, more=True, radius=False),
        "width_decimal": (value.upper() - value.lower()).str(35, more=True, radius=False),
    }


def initial_intervals(count: int) -> list[Interval]:
    return [(Fraction(-T) + Fraction(2 * T * index, count),
             Fraction(-T) + Fraction(2 * T * (index + 1), count))
            for index in range(count)]


def bisect(iv: Interval) -> tuple[Interval, Interval]:
    mid = (iv[0] + iv[1]) / 2
    return (iv[0], mid), (mid, iv[1])


def mass(iv: Interval) -> arb:
    return cdf(A(iv[1])) - cdf(A(iv[0]))


def moments(iv: Interval, center: Fraction, order: int) -> list[arb]:
    out = [mass(iv)]
    lo, hi, cc = A(iv[0]), A(iv[1]), A(center)
    plo, phi = density(lo), density(hi)
    for degree in range(1, order + 1):
        prior = A(degree - 1) * out[degree - 2] if degree >= 2 else arb(0)
        edge = ((lo - cc) ** (degree - 1) * plo
                - (hi - cc) ** (degree - 1) * phi)
        out.append(prior - cc * out[degree - 1] + edge)
    return out


def load_specs() -> dict[str, dict]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    specs = {
        kernel_id: record["spec"]
        for kernel_id, record in source["kernels"].items()
        if record["spec"].get("scenario") == 6
        and record["spec"].get("correlation") == 3
    }
    if len(specs) != 8:
        raise SystemExit(f"fail closed: expected 8 P6/C3 kernels, got {len(specs)}")
    for kernel_id, spec in specs.items():
        counts = Counter(tuple(value) for value in spec["cluster_counts"])
        focal = tuple(spec["cluster_counts"][int(spec["h_cluster"])])
        if (counts != EXPECTED_COUNTS or focal not in EXPECTED_COUNTS
                or Fraction(spec["r_n"], spec["r_d"]) != R):
            raise SystemExit(f"fail closed: unexpected frozen P6/C3 spec {kernel_id}")
        if spec["is_target"] and focal[int(spec["h_kind"])] != 1:
            raise SystemExit(f"fail closed: absent focal target {kernel_id}")
    return dict(sorted(specs.items()))


def member_outer(value, which: int):
    return cdf((A(Fraction(1, 10)).sqrt() * value - THRESHOLDS[which])
               / A(Fraction(9, 10)).sqrt())


def base_outer(value, which: int):
    return 1 - A(Fraction(1, 11)) * member_outer(value, which)


def population_kernel(value):
    return base_outer(value, 0) ** 8 * base_outer(value, 1) ** 16


def left_kernel(value, focal: tuple[int, int]):
    return (base_outer(value, 0) ** (8 - int(focal == (1, 0)))
            * base_outer(value, 1) ** (16 - int(focal == (0, 1))))


def analytic_hit_kernel(value, spec: dict):
    focal = tuple(spec["cluster_counts"][int(spec["h_cluster"])])
    p = member_outer(value, int(spec["h_kind"]))
    if focal == (0, 0):
        conditional = p
    elif spec["is_target"]:
        conditional = A(R) * p
    else:
        raise ArithmeticError("non-target singleton requires nested primitive")
    return left_kernel(value, focal) * conditional


def nested_right_kernel(value, spec: dict):
    focal = tuple(spec["cluster_counts"][int(spec["h_cluster"])])
    injected_kind = 0 if focal == (1, 0) else 1
    ph = cdf((value - THRESHOLDS[int(spec["h_kind"])])
             / A(Fraction(3, 10)).sqrt())
    pj = cdf((value - THRESHOLDS[injected_kind])
             / A(Fraction(3, 10)).sqrt())
    return ph * (1 - A(Fraction(1, 11)) * pj)


def series(fn, center: arb, length: int) -> list[arb]:
    return list(fn(arb_series([center, A(1)], length)))


def one_dimensional_band(fn, iv: Interval, order: int) -> arb:
    center_value = (iv[0] + iv[1]) / 2
    center, span = A(center_value), hull(A(iv[0]), A(iv[1]))
    radius = A((iv[1] - iv[0]) / 2)
    mom = moments(iv, center_value, order)
    centered = series(fn, center, order + 1)
    bounded = series(fn, span, order + 1)
    estimate = sum((centered[k] * mom[k] for k in range(order)), arb(0))
    remainder = abs_upper(bounded[order]) * radius ** order * mom[0]
    return estimate + symmetric(remainder)


@dataclass
class Band:
    xiv: Interval
    yivs: tuple[Interval, ...]
    values: dict[str, arb]
    y_scores: tuple[arb, ...]


class Certifier:
    def __init__(self, specs: dict[str, dict], deadline: float, order: int,
                 reverse_inner: bool):
        self.specs = specs
        self.deadline = deadline
        self.order = order
        self.reverse_inner = reverse_inner
        self.evaluations = 0
        self.ax = A(Fraction(1, 10)).sqrt()
        self.cy = A(Fraction(3, 5)).sqrt()
        self.tail = 66 * ((A(T) / A(2).sqrt()).erfc() / 2)
        self.nested = {
            kernel_id: spec for kernel_id, spec in specs.items()
            if tuple(spec["cluster_counts"][int(spec["h_cluster"])]) != (0, 0)
            and not spec["is_target"]
        }
        self.analytic = {
            kernel_id: spec for kernel_id, spec in specs.items()
            if kernel_id not in self.nested
        }

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Deadline

    def inner_center(self, x: Fraction, yivs: tuple[Interval, ...]) -> dict[str, list[arb]]:
        order = self.order
        totals = {kernel_id: [arb(0) for _ in range(order)] for kernel_id in self.nested}
        traversal = tuple(reversed(yivs)) if self.reverse_inner else yivs
        for yiv in traversal:
            self.check()
            yc = (yiv[0] + yiv[1]) / 2
            radius = A((yiv[1] - yiv[0]) / 2)
            my = moments(yiv, yc, order)
            f0 = self.ax * A(x) + self.cy * A(yc)
            fspan = self.ax * A(x) + self.cy * hull(A(yiv[0]), A(yiv[1]))
            for kernel_id, spec in self.nested.items():
                centered = series(lambda z, s=spec: nested_right_kernel(z, s), f0, 2 * order + 1)
                bounded = series(lambda z, s=spec: nested_right_kernel(z, s), fspan, 2 * order + 1)
                for k in range(order):
                    value = arb(0)
                    for j in range(order):
                        value += (A(math.comb(k + j, k)) * centered[k + j]
                                  * self.ax ** k * self.cy ** j * my[j])
                    remainder = (A(math.comb(k + order, k))
                                 * abs_upper(bounded[k + order])
                                 * self.ax ** k * self.cy ** order
                                 * radius ** order * my[0])
                    totals[kernel_id][k] += value + symmetric(remainder)
                self.evaluations += 1
        return totals

    def inner_bounds(self, xiv: Interval, yivs: tuple[Interval, ...]) -> dict[str, list[arb]]:
        order = self.order
        totals = {kernel_id: [arb(0) for _ in range(order + 1)] for kernel_id in self.nested}
        xs = hull(A(xiv[0]), A(xiv[1]))
        traversal = tuple(reversed(yivs)) if self.reverse_inner else yivs
        for yiv in traversal:
            self.check()
            fs = self.ax * xs + self.cy * hull(A(yiv[0]), A(yiv[1]))
            my = mass(yiv)
            for kernel_id, spec in self.nested.items():
                bounded = series(lambda z, s=spec: nested_right_kernel(z, s), fs, order + 1)
                for k in range(order + 1):
                    totals[kernel_id][k] += bounded[k] * self.ax ** k * my
                self.evaluations += 1
        return totals

    def band(self, xiv: Interval, yivs: tuple[Interval, ...]) -> Band:
        self.check()
        values = {"population": one_dimensional_band(population_kernel, xiv, self.order)}
        for kernel_id, spec in self.analytic.items():
            values[kernel_id] = one_dimensional_band(
                lambda value, s=spec: analytic_hit_kernel(value, s), xiv, self.order)

        center_value = (xiv[0] + xiv[1]) / 2
        mom = moments(xiv, center_value, self.order)
        inner_center = self.inner_center(center_value, yivs)
        inner_bounds = self.inner_bounds(xiv, yivs)
        radius = A((xiv[1] - xiv[0]) / 2)
        for kernel_id, spec in self.nested.items():
            focal = tuple(spec["cluster_counts"][int(spec["h_cluster"])])
            left_center = arb_series(series(lambda z, f=focal: left_kernel(z, f),
                                            A(center_value), self.order), self.order)
            left_bounds = arb_series(series(lambda z, f=focal: left_kernel(z, f),
                                            hull(A(xiv[0]), A(xiv[1])), self.order + 1),
                                     self.order + 1)
            center_product = list(left_center * arb_series(inner_center[kernel_id], self.order))
            bound_product = list(left_bounds * arb_series(inner_bounds[kernel_id], self.order + 1))
            estimate = sum((center_product[k] * mom[k] for k in range(self.order)), arb(0))
            remainder = abs_upper(bound_product[self.order]) * radius ** self.order * mom[0]
            values[kernel_id] = estimate + symmetric(remainder)
        scores = tuple(A((iv[1] - iv[0]) ** self.order) * mass(iv) for iv in yivs)
        return Band(xiv=xiv, yivs=yivs, values=values, y_scores=scores)


def class_probs(survival: arb) -> tuple[arb, arb, arb]:
    return (A(Fraction(13, 20)) * survival,
            A(Fraction(1, 4)) * survival,
            1 - A(Fraction(9, 10)) * survival)


def winner(probabilities: tuple[arb, arb, arb]) -> int | None:
    candidates = [index for index in range(3)
                  if all(index == other
                         or probabilities[index].lower() > probabilities[other].upper()
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


def classify(population: arb, joint_hit: arb, spec: dict) -> dict[str, object]:
    q = (THRESHOLDS[int(spec["h_kind"])] / A(2).sqrt()).erfc() / 2
    joint_hit = intersect(unit_interval(joint_hit), hull(A(0), q.upper()))
    joint_miss = unit_interval(hull(max(A(0), population.lower() - joint_hit.upper()),
                                    min((1 - q).upper(), population.upper() - joint_hit.lower())))
    population = intersect(population, joint_hit + joint_miss)
    hit, miss = unit_interval(joint_hit / q), unit_interval(joint_miss / (1 - q))
    ph, pm, pp = class_probs(hit), class_probs(miss), class_probs(population)
    wins = winner(ph), winner(pm), winner(pp)
    if None in wins:
        return {"accepted": False, "reason": "possible_interval_argmax_tie",
                "hit": hit, "miss": miss, "population": population,
                "joint_hit": joint_hit, "joint_miss": joint_miss,
                "winners": wins, "lift": None, "classification": "refused"}
    if wins[0] == wins[1] == wins[2]:
        lift, classification = arb(0), "true_null"
    else:
        lift = macro(ph, pm, q, wins[0], wins[1]) - macro(ph, pm, q, wins[2], wins[2])
        classification = "non_null"
    separation = (lift.lower() if lift.lower() > 0 else
                  -lift.upper() if lift.upper() < 0 else A(0))
    accepted = lift.rad() <= A(MAX_RADIUS)
    if classification == "non_null":
        accepted = accepted and separation >= A(MIN_ZERO_SEPARATION)
    return {"accepted": bool(accepted),
            "reason": "accepted" if accepted else "lift_threshold_failed",
            "classification": classification, "hit": hit, "miss": miss,
            "population": population, "joint_hit": joint_hit,
            "joint_miss": joint_miss, "winners": wins, "lift": lift,
            "zero_separation": separation}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, object]:
    started = time.perf_counter()
    deadline = time.monotonic() + args.max_seconds
    specs = load_specs()
    certifier = Certifier(specs, deadline, args.order, args.reverse_inner)
    y0 = tuple(initial_intervals(args.axis))
    bands = [certifier.band(iv, y0) for iv in initial_intervals(args.axis)]
    keys = ("population", *specs.keys())
    tail_pad = arb(certifier.tail.upper() / 2, certifier.tail.upper() / 2)

    def rebuild() -> dict[str, arb]:
        result = {}
        for key in keys:
            lo = sum((band.values[key].lower() for band in bands), arb(0))
            hi = sum((band.values[key].upper() for band in bands), arb(0))
            result[key] = unit_interval(hull(lo, hi) + tail_pad)
        return result

    def decisions(values: dict[str, arb]) -> dict[str, dict[str, object]]:
        return {kernel_id: classify(values["population"], values[kernel_id], spec)
                for kernel_id, spec in specs.items()}

    values = rebuild()
    results = decisions(values)
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    step = 0
    with args.checkpoint.open("w", encoding="utf-8") as handle:
        while True:
            handle.write(json.dumps({
                "schema": "m32.p6-c3-taylor-checkpoint.v1",
                "step": step, "precision_bits": args.precision,
                "replay": args.replay, "bands": len(bands),
                "leaves": sum(len(band.yivs) for band in bands),
                "evaluations": certifier.evaluations,
                "accepted_kernels": sum(bool(row["accepted"]) for row in results.values()),
                "population": enclosure(values["population"]),
                "kernels": {kernel_id: {
                    "classification": row["classification"],
                    "strict_argmax": row["winners"],
                    "signed_lift_radius": (row["lift"].rad().str(25, more=True, radius=False)
                                           if row["lift"] is not None else None),
                } for kernel_id, row in results.items()},
            }, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            if all(bool(row["accepted"]) for row in results.values()):
                break
            if time.monotonic() >= deadline or len(bands) >= args.max_bands:
                break
            index = max(range(len(bands)), key=lambda i: (
                max(float(bands[i].values[key].upper() - bands[i].values[key].lower())
                    for key in keys), -float(bands[i].xiv[0])))
            old = bands[index]
            yi = max(range(len(old.yivs)), key=lambda i: (float(old.y_scores[i]), -i))
            try:
                yl, yr = bisect(old.yivs[yi])
                y_candidate = certifier.band(
                    old.xiv, old.yivs[:yi] + (yl, yr) + old.yivs[yi + 1:])
                xl, xr = bisect(old.xiv)
                x_candidates = (certifier.band(xl, old.yivs),
                                certifier.band(xr, old.yivs))
            except Deadline:
                break
            yw = max(y_candidate.values[key].upper() - y_candidate.values[key].lower()
                     for key in keys)
            xw = max(x_candidates[0].values[key].upper() - x_candidates[0].values[key].lower()
                     + x_candidates[1].values[key].upper() - x_candidates[1].values[key].lower()
                     for key in keys)
            if xw.upper() < yw.lower():
                bands[index:index + 1] = list(x_candidates)
            else:
                bands[index] = y_candidate
            bands.sort(key=lambda band: band.xiv)
            step += 1
            current = rebuild()
            values = {key: intersect(values[key], current[key]) for key in keys}
            results = decisions(values)

    kernels = {}
    accepted = True
    for kernel_id, spec in specs.items():
        row = results[kernel_id]
        accepted = accepted and bool(row["accepted"])
        kernels[kernel_id] = {
            "spec": spec,
            "status": "certified" if row["accepted"] else "refused",
            "classification": row["classification"], "reason": row["reason"],
            "strict_argmax": {"hit": row["winners"][0], "miss": row["winners"][1],
                              "population": row["winners"][2]},
            "conditional_hit_interval": enclosure(row["hit"]),
            "conditional_miss_interval": enclosure(row["miss"]),
            "population_interval": enclosure(row["population"]),
            "joint_hit_interval": enclosure(row["joint_hit"]),
            "joint_miss_interval": enclosure(row["joint_miss"]),
            "signed_lift_interval": enclosure(row["lift"]) if row["lift"] is not None else None,
            "signed_lift_radius": (row["lift"].rad().str(35, more=True, radius=False)
                                   if row["lift"] is not None else None),
            "zero_separation": row.get("zero_separation", A(0)).str(35, more=True, radius=False),
        }
    payload = {
        "schema": "m32.p6-c3-reduced-taylor-certificate.v1",
        "cell": "P6/C3", "status": "certified" if accepted else "refused",
        "method": "rigorous_C3_analytic_singleton_reduction_with_nested_Arb_Taylor_centered_moments",
        "rng_used": False, "broader_kernel_run": False, "protocol_changed": False,
        "analytic_reduction_preserved": True,
        "source_certificate_sha256": sha256(SOURCE),
        "estimand_spec_sha256": sha256(ESTIMAND),
        "precision_bits": args.precision, "taylor_order": args.order,
        "axis": args.axis, "reverse_inner_traversal": args.reverse_inner,
        "replay": args.replay, "runtime_seconds": time.perf_counter() - started,
        "refinements": step, "bands": len(bands),
        "leaves": sum(len(band.yivs) for band in bands),
        "evaluations": certifier.evaluations, "gaussian_tail_bound": enclosure(certifier.tail),
        "lift_radius_target": "1e-12",
        "zero_separation_target_non_null": "1e-10", "kernels": kernels,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "runtime_seconds": payload["runtime_seconds"],
                      "refinements": step, "bands": len(bands), "leaves": payload["leaves"]},
                     sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision", type=int, default=224)
    parser.add_argument("--order", type=int, default=14)
    parser.add_argument("--axis", type=int, default=80)
    parser.add_argument("--max-bands", type=int, default=512)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--reverse-inner", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.precision < 192 or not 4 <= args.order <= 18 or args.axis < 8
            or args.max_bands < args.axis or not 0 < args.max_seconds <= 900):
        raise SystemExit("fail closed: invalid certificate configuration")
    ctx.prec = args.precision
    ctx.cap = 2 * args.order + 2
    payload = run(args)
    return 0 if payload["status"] == "certified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
