#!/usr/bin/env python3
"""Deterministic population-only P5/C3 Gaussian moment/Taylor canary."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from flint import arb, ctx


T = 16
MAJORITY = Fraction(20, 31)
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
SPEC = {
    "cluster_counts": [[2, 4], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0],
                       [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0],
                       [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0],
                       [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0],
                       [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [2, 4],
                       [2, 4], [2, 4]],
    "correlation": 3, "scenario": 5, "r_n": 10, "r_d": 11,
}
PRIOR_LO = (17310577113938694545792170221160981501386559758386614843176280202001, -224)
PRIOR_HI = (17902088053084786932939196891653892480366017300214235404406469761809, -224)
Interval = tuple[Fraction, Fraction]


class Deadline(Exception):
    pass


def A(x: Fraction | int | str) -> arb:
    if isinstance(x, str):
        return arb(x)
    x = Fraction(x)
    return arb(f"{x.numerator}/{x.denominator}")


def frozen(text: str) -> arb:
    n, d = float(text).as_integer_ratio()
    return A(Fraction(n, d))


def cdf(x: arb) -> arb:
    return (1 + (x / A(2).sqrt()).erf()) / 2


def density(x: arb) -> arb:
    return (-x*x/2).exp() / (2*arb.pi()).sqrt()


def hull(lo: arb, hi: arb) -> arb:
    return arb((lo + hi)/2, (hi - lo)/2)


def bounded(upper: arb) -> arb:
    u = max(A(0), upper.upper())
    return arb(u/2, u/2)


def symmetric(radius: arb) -> arb:
    return arb(0, max(A(0), radius.upper()))


def dyadic(m: int, e: int) -> arb:
    return A(m) * A(2)**e


def enclosure(x: arb) -> dict[str, object]:
    lm, le = x.lower().man_exp()
    um, ue = x.upper().man_exp()
    return {
        "lower_dyadic": {"mantissa": int(lm), "exponent": int(le)},
        "upper_dyadic": {"mantissa": int(um), "exponent": int(ue)},
        "decimal": x.str(45, more=True),
        "width_decimal": (x.upper()-x.lower()).str(25, more=True, radius=False),
    }


def mass(iv: Interval) -> arb:
    return cdf(A(iv[1])) - cdf(A(iv[0]))


def centered_first(iv: Interval, center: Fraction) -> arb:
    # Integral (x-center) phi(x) dx on the rectangle.
    return density(A(iv[0])) - density(A(iv[1])) - A(center)*mass(iv)


def bisect(iv: Interval) -> tuple[Interval, Interval]:
    mid = (iv[0]+iv[1])/2
    return (iv[0], mid), (mid, iv[1])


def initial_intervals(n: int) -> list[Interval]:
    return [(Fraction(-T)+Fraction(2*T*i, n),
             Fraction(-T)+Fraction(2*T*(i+1), n)) for i in range(n)]


def abs_upper(x: arb) -> arb:
    return max(abs(x.lower()), abs(x.upper()))


def intersect(x: arb, y: arb) -> arb:
    lo, hi = max(x.lower(), y.lower()), min(x.upper(), y.upper())
    if lo > hi:
        raise ArithmeticError("independent certified enclosures are disjoint")
    return hull(lo, hi)


@dataclass
class Band:
    giv: Interval
    zivs: tuple[Interval, ...]
    contribution: arb
    z_scores: tuple[arb, ...]


class Certifier:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.evaluations = 0
        self.a = A(Fraction(1, 10)).sqrt()
        self.c = A(Fraction(3, 5)).sqrt()
        self.s = A(Fraction(3, 10)).sqrt()
        self.thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
        self.r = A(Fraction(10, 11))
        self.omr = 1-self.r
        self.counts = (2, 4)
        self.multiplicity = 4
        self.tail = 1-(cdf(A(T))-cdf(A(-T)))
        n = A(sum(self.counts))
        p1max = 1/(self.s*(2*arb.pi()).sqrt())
        p2max = (-A(Fraction(1, 2))).exp()/((2*arb.pi()).sqrt()*self.s**2)
        dmin = self.r
        self.global_b1 = n*self.omr*p1max/dmin
        self.global_b2 = (self.global_b1**2 + n*(self.omr*p2max/dmin
                          + self.omr**2*p1max**2/dmin**2))

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Deadline

    def derivatives(self, f: arb) -> tuple[arb, arb, arb]:
        ps, p1s, p2s = [], [], []
        for threshold in self.thresholds:
            u = (f-threshold)/self.s
            ps.append(cdf(u))
            p1s.append(density(u)/self.s)
            p2s.append(-u*density(u)/self.s**2)
        base, l1, l2 = arb(1), arb(0), arb(0)
        for p, p1, p2, n in zip(ps, p1s, p2s, self.counts):
            d = 1-self.omr*p
            base *= d**n
            l1 -= A(n)*self.omr*p1/d
            l2 -= A(n)*(self.omr*p2*d+self.omr**2*p1**2)/d**2
        return base, base*l1, base*(l1**2+l2)

    def point_integral(self, g: Fraction, zivs: tuple[Interval, ...],
                       scores: bool = False) -> tuple[arb, tuple[arb, ...]]:
        total = arb(0)
        rem_scores = []
        for ziv in zivs:
            self.check()
            z0 = (ziv[0]+ziv[1])/2
            f0 = self.a*A(g)+self.c*A(z0)
            b0, b1, _ = self.derivatives(f0)
            fspan = self.a*A(g)+self.c*hull(A(ziv[0]), A(ziv[1]))
            b2 = self.derivatives(fspan)[2]
            mz = mass(ziv)
            radius = self.c*A((ziv[1]-ziv[0])/2)
            rem = mz*abs_upper(b2)*radius**2/2
            total += mz*b0 + self.c*b1*centered_first(ziv, z0) + symmetric(rem)
            rem_scores.append(rem)
            self.evaluations += 1
        return total+bounded(self.tail), tuple(rem_scores) if scores else ()

    def derivative_integrals(self, giv: Interval, zivs: tuple[Interval, ...]) -> tuple[arb, arb]:
        j1, j2 = arb(0), arb(0)
        gs = hull(A(giv[0]), A(giv[1]))
        for ziv in zivs:
            self.check()
            fs = self.a*gs+self.c*hull(A(ziv[0]), A(ziv[1]))
            _, b1, b2 = self.derivatives(fs)
            mz = mass(ziv)
            j1 += mz*b1
            j2 += mz*b2
            self.evaluations += 1
        j1 += symmetric(self.tail*self.global_b1)
        j2 += symmetric(self.tail*self.global_b2)
        return self.a*j1, self.a**2*j2

    def band(self, giv: Interval, zivs: tuple[Interval, ...]) -> Band:
        gc = (giv[0]+giv[1])/2
        i0, scores = self.point_integral(gc, zivs, True)
        ilo, _ = self.point_integral(giv[1], zivs)
        ihi, _ = self.point_integral(giv[0], zivs)
        irange = hull(max(A(0), ilo.lower()), min(A(1), ihi.upper()))
        i1center, _ = self.derivative_integrals((gc, gc), zivs)
        i1range, i2range = self.derivative_integrals(giv, zivs)
        m = A(self.multiplicity)
        h0 = i0**self.multiplicity
        h1 = m*i0**(self.multiplicity-1)*i1center
        h2 = (m*A(self.multiplicity-1)*irange**(self.multiplicity-2)*i1range**2
              + m*irange**(self.multiplicity-1)*i2range)
        mg = mass(giv)
        radius = A((giv[1]-giv[0])/2)
        rem = mg*abs_upper(h2)*radius**2/2
        contribution = mg*h0+h1*centered_first(giv, gc)+symmetric(rem)
        # Since b, I and I**m are decreasing, endpoint values also give a
        # rigorous one-sided enclosure for the whole outer-band integral.
        monotone = hull(mg*max(A(0), ilo.lower())**self.multiplicity,
                        mg*min(A(1), ihi.upper())**self.multiplicity)
        contribution = intersect(contribution, monotone)
        return Band(giv, zivs, contribution, scores)


def checkpoint(fp, step: int, action: str, bands: list[Band], total: arb,
               evaluations: int, detail: dict | None = None) -> None:
    rec = {"action": action, "bands": len(bands), "canary": "P5/C3-population",
           "evaluation_count": evaluations, "leaves": sum(len(b.zivs) for b in bands),
           "population": enclosure(total), "step": step}
    if detail:
        rec["detail"] = detail
    fp.write(json.dumps(rec, sort_keys=True, separators=(",", ":"))+"\n")
    fp.flush()


def run(args: argparse.Namespace) -> dict:
    start = time.monotonic()
    cert = Certifier(start+args.runtime_cap)
    z0 = tuple(initial_intervals(args.initial_axis))
    bands: list[Band] = []
    timed_out = False
    try:
        bands = [cert.band(g, z0) for g in initial_intervals(args.initial_axis)]
    except Deadline:
        raise SystemExit("runtime cap too small for deterministic initial partition")

    tail_outer = cert.tail

    def rebuild() -> arb:
        lo = sum((b.contribution.lower() for b in bands), arb(0))
        hi = sum((b.contribution.upper() for b in bands), arb(0))
        return hull(max(A(0), lo), min(A(1), hi+tail_outer))

    total = rebuild()
    best = total
    step = 0
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with args.checkpoint.open("w", encoding="utf-8") as fp:
        checkpoint(fp, step, "initial", bands, total, cert.evaluations,
                   {"global_sum": "rebuilt_from_leaves"})
        while best.lower() <= A(MAJORITY) and time.monotonic() < cert.deadline:
            # Largest enclosure deficit is the band with the greatest amount by
            # which its upper contribution can exceed its certified lower one.
            idx = max(range(len(bands)), key=lambda i: (
                float(bands[i].contribution.upper()-bands[i].contribution.lower()),
                -float(bands[i].giv[0])))
            old = bands[idx]
            zi = max(range(len(old.zivs)), key=lambda i: (float(old.z_scores[i]), -i))
            try:
                zl, zr = bisect(old.zivs[zi])
                nz = old.zivs[:zi]+(zl, zr)+old.zivs[zi+1:]
                z_candidate = cert.band(old.giv, nz)
                gl, gr = bisect(old.giv)
                g_candidates = (cert.band(gl, old.zivs), cert.band(gr, old.zivs))
            except Deadline:
                timed_out = True
                break
            z_gain = z_candidate.contribution.lower()-old.contribution.lower()
            g_gain = (g_candidates[0].contribution.lower()+g_candidates[1].contribution.lower()
                      - old.contribution.lower())
            if g_gain.lower() > z_gain.upper():
                bands[idx:idx+1] = list(g_candidates)
                action = "split_g"
            else:
                bands[idx] = z_candidate
                action = "split_z"
            bands.sort(key=lambda b: b.giv)
            step += 1
            # Rebuilding every 32 accepted refinements prevents subtraction and
            # insertion order from accumulating path-dependent Arb wrapping.
            total = rebuild()
            best = intersect(best, total)
            checkpoint(fp, step, action, bands, best, cert.evaluations,
                       {"global_sum": "rebuilt_from_leaves",
                        "priority": "largest_lower_bound_deficit_contribution",
                        "selected_gain": "lower_bound"})

    elapsed = time.monotonic()-start
    prior = hull(dyadic(*PRIOR_LO), dyadic(*PRIOR_HI))
    threshold = A(MAJORITY)
    total = best
    distance = total.lower()-threshold
    payload = {
        "schema": "m3.2-p5-c3-population-taylor-canary.v1",
        "canary": "P5/C3", "scope": "population_only", "rng_constructed": False,
        "protocol_changed": False, "precision_bits": args.precision_bits,
        "runtime_cap_seconds": args.runtime_cap, "runtime_seconds": elapsed,
        "timed_out": timed_out or elapsed >= args.runtime_cap,
        "bands": len(bands), "leaves": sum(len(b.zivs) for b in bands),
        "evaluations": cert.evaluations, "subdivisions": step,
        "population_before": enclosure(prior), "population_after": enclosure(total),
        "majority_threshold": "20/31",
        "lower_bound_distance_to_threshold": distance.str(30, more=True, radius=False),
        "majority_resolved": bool(total.lower() > threshold or total.upper() < threshold),
        "majority_side": ("above" if total.lower() > threshold else
                          "below" if total.upper() < threshold else None),
        "width_improvement_factor": ((prior.upper()-prior.lower())/
                                     (total.upper()-total.lower())).str(20, more=True, radius=False),
        "proof_path_viable": bool(total.lower() > threshold and elapsed <= args.runtime_cap),
        "method": "nested one-sided Gaussian rectangle moment/Taylor enclosure",
        "analytic_gaussian_rectangle_masses": True,
        "certified_truncated_normal_first_moments": True,
        "monotonicity_used": "population cluster PGF and conditional integral decreasing",
        "second_order_remainder": "interval Hessian supremum times squared rectangle radius over 2",
        "global_sum_rebuild_period": 32,
        "priority": "outer-band lower-bound deficit contribution; candidate split chosen by certified lower gain",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision-bits", type=int, default=224)
    parser.add_argument("--runtime-cap", type=float, default=285.0)
    parser.add_argument("--initial-axis", type=int, default=16)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.precision_bits < 192 or not (0 < args.runtime_cap <= 300) or args.initial_axis < 4:
        raise SystemExit("fail closed: invalid canary configuration")
    ctx.prec = args.precision_bits
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
