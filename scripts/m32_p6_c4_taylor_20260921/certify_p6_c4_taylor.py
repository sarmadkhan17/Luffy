#!/usr/bin/env python3
"""Rigorous deterministic P6/C4 reduced Taylor/moment certificate.

The existing analytic bivariate Gaussian reduction is unchanged.  Higher-order
centered moments tighten only its three PGF integrals and the resulting frozen
categorical macro-F1 lift.  No RNG or synthetic world is constructed.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from flint import arb, arb_series, ctx


T = 16
MAJORITY = Fraction(20, 31)
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
SPEC = {
    "correlation": 4, "scenario": 6, "h_kind": 1, "h_sector": 1,
    "is_target": False, "r_n": 10, "r_d": 11,
    "sector_counts": [[8, 0], [0, 16]],
}
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


def cdf(x):
    return (1+(x/A(2).sqrt()).erf())/2


def density(x: arb) -> arb:
    return (-x*x/2).exp()/(2*arb.pi()).sqrt()


def hull(lo: arb, hi: arb) -> arb:
    return arb((lo+hi)/2, (hi-lo)/2)


def symmetric(radius: arb) -> arb:
    return arb(0, max(A(0), radius.upper()))


def abs_upper(x: arb) -> arb:
    return max(abs(x.lower()), abs(x.upper()))


def enclosure(x: arb) -> dict[str, object]:
    lm, le = x.lower().man_exp(); um, ue = x.upper().man_exp()
    return {
        "lower_dyadic": {"mantissa": int(lm), "exponent": int(le)},
        "upper_dyadic": {"mantissa": int(um), "exponent": int(ue)},
        "decimal": x.str(55, more=True),
        "width_decimal": (x.upper()-x.lower()).str(30, more=True, radius=False),
    }


def initial_intervals(n: int) -> list[Interval]:
    return [(Fraction(-T)+Fraction(2*T*i, n),
             Fraction(-T)+Fraction(2*T*(i+1), n)) for i in range(n)]


def bisect(iv: Interval) -> tuple[Interval, Interval]:
    mid = (iv[0]+iv[1])/2
    return (iv[0], mid), (mid, iv[1])


def mass(iv: Interval) -> arb:
    return cdf(A(iv[1]))-cdf(A(iv[0]))


def moments(iv: Interval, center: Fraction, order: int) -> list[arb]:
    out = [mass(iv)]
    lo, hi, cc = A(iv[0]), A(iv[1]), A(center)
    plo, phi = density(lo), density(hi)
    for k in range(1, order+1):
        prior = A(k-1)*out[k-2] if k >= 2 else arb(0)
        edge = (lo-cc)**(k-1)*plo-(hi-cc)**(k-1)*phi
        out.append(prior-cc*out[k-1]+edge)
    return out


def intersect(x: arb, y: arb) -> arb:
    lo, hi = max(x.lower(), y.lower()), min(x.upper(), y.upper())
    if lo > hi:
        raise ArithmeticError("independent certified enclosures are disjoint")
    return hull(lo, hi)


@dataclass
class Band:
    xiv: Interval
    yivs: tuple[Interval, ...]
    values: tuple[arb, arb, arb]
    y_scores: tuple[arb, ...]


class Certifier:
    def __init__(self, deadline: float, order: int):
        self.deadline = deadline
        self.order = order
        self.evaluations = 0
        self.ax = A(Fraction(-1, 2))
        self.cy = A(Fraction(3, 4)).sqrt()
        self.systematic = A(Fraction(3, 5)).sqrt()
        self.residual = A(Fraction(2, 5)).sqrt()
        self.thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
        self.r = A(Fraction(10, 11))
        self.omr = 1-self.r
        self.tail = 1-(cdf(A(T))-cdf(A(-T)))

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Deadline

    def member(self, x, which: int):
        return cdf((self.systematic*x-self.thresholds[which])/self.residual)

    def right_kernels(self, f) -> tuple:
        p = self.member(f, 1)
        base = (1-self.omr*p)**16
        return base, p*base, (1-p)*base

    def left_kernel(self, x):
        p = self.member(x, 0)
        return (1-self.omr*p)**8

    @staticmethod
    def series(fn, x0: arb, length: int) -> list[arb]:
        return list(fn(arb_series([x0, A(1)], length)))

    def right_center(self, x: Fraction, yivs: tuple[Interval, ...]) -> tuple[list[arb], ...]:
        D = self.order
        totals = [[arb(0) for _ in range(D)] for _ in range(3)]
        for yiv in yivs:
            self.check()
            yc = (yiv[0]+yiv[1])/2
            radius = A((yiv[1]-yiv[0])/2)
            my = moments(yiv, yc, D)
            f0 = self.ax*A(x)+self.cy*A(yc)
            series = tuple(self.series(lambda z, w=w: self.right_kernels(z)[w],
                                       f0, 2*D+1) for w in range(3))
            fspan = self.ax*A(x)+self.cy*hull(A(yiv[0]), A(yiv[1]))
            bounds = tuple(self.series(lambda z, w=w: self.right_kernels(z)[w],
                                       fspan, 2*D+1) for w in range(3))
            for which in range(3):
                for k in range(D):
                    value = arb(0)
                    for j in range(D):
                        value += (A(math.comb(k+j, k))*series[which][k+j]
                                  * self.ax**k*self.cy**j*my[j])
                    rem = (A(math.comb(k+D, k))*abs_upper(bounds[which][k+D])
                           * abs(self.ax)**k*self.cy**D*radius**D*my[0])
                    totals[which][k] += value+symmetric(rem)
            self.evaluations += 1
        return tuple(totals)

    def right_bounds(self, xiv: Interval, yivs: tuple[Interval, ...]) -> tuple[list[arb], ...]:
        D = self.order
        totals = [[arb(0) for _ in range(D+1)] for _ in range(3)]
        xs = hull(A(xiv[0]), A(xiv[1]))
        for yiv in yivs:
            self.check()
            fs = self.ax*xs+self.cy*hull(A(yiv[0]), A(yiv[1]))
            series = tuple(self.series(lambda z, w=w: self.right_kernels(z)[w],
                                       fs, D+1) for w in range(3))
            my = mass(yiv)
            for which in range(3):
                for k in range(D+1):
                    totals[which][k] += series[which][k]*self.ax**k*my
            self.evaluations += 1
        return tuple(totals)

    def outer_products(self, x0: arb, right: tuple[list[arb], ...], length: int):
        left = arb_series(self.series(self.left_kernel, x0, length), length)
        return tuple(list(left*arb_series(v, length)) for v in right)

    def band(self, xiv: Interval, yivs: tuple[Interval, ...]) -> Band:
        D = self.order
        xc = (xiv[0]+xiv[1])/2
        mx = moments(xiv, xc, D)
        center = self.outer_products(A(xc), self.right_center(xc, yivs), D)
        bounds = self.outer_products(hull(A(xiv[0]), A(xiv[1])),
                                     self.right_bounds(xiv, yivs), D+1)
        radius = A((xiv[1]-xiv[0])/2)
        values = []
        for which in range(3):
            value = sum((center[which][k]*mx[k] for k in range(D)), arb(0))
            rem = abs_upper(bounds[which][D])*radius**D*mx[0]
            values.append(value+symmetric(rem))
        scores = tuple(A((y[1]-y[0])**D)*mass(y) for y in yivs)
        return Band(xiv, yivs, tuple(values), scores)


def checkpoint(fp, step: int, action: str, bands: list[Band], totals: tuple[arb, ...],
               evaluations: int) -> None:
    fp.write(json.dumps({
        "action": action, "bands": len(bands), "canary": "P6/C4",
        "evaluation_count": evaluations, "leaves": sum(len(b.yivs) for b in bands),
        "step": step, "population": enclosure(totals[0]),
        "joint_hit": enclosure(totals[1]), "joint_miss": enclosure(totals[2]),
    }, sort_keys=True, separators=(",", ":"))+"\n")
    fp.flush()


def run(args: argparse.Namespace) -> dict:
    start = time.monotonic()
    cert = Certifier(start+args.runtime_cap, args.order)
    y0 = tuple(initial_intervals(args.initial_axis))
    bands = [cert.band(x, y0) for x in initial_intervals(args.initial_axis)]
    tail_error = 2*cert.tail

    def rebuild() -> tuple[arb, arb, arb]:
        ans = []
        for which in range(3):
            lo = sum((b.values[which].lower() for b in bands), arb(0))
            hi = sum((b.values[which].upper() for b in bands), arb(0))
            ans.append(hull(max(A(0), lo), min(A(1), hi+tail_error)))
        ans[0] = intersect(ans[0], ans[1]+ans[2])
        return tuple(ans)

    best = rebuild(); step = 0
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with args.checkpoint.open("w", encoding="utf-8") as fp:
        checkpoint(fp, step, "initial", bands, best, cert.evaluations)
        while time.monotonic() < cert.deadline:
            pop, jh, jm = best
            q = cdf(-cert.thresholds[1])
            hit, miss = jh/q, jm/(1-q)
            hit_side = hit.upper() < A(MAJORITY)
            miss_side = miss.lower() > A(MAJORITY)
            pop_side = pop.lower() > A(MAJORITY)
            lift = None
            if hit_side and miss_side and pop_side:
                f10 = 2*A(Fraction(13, 20))*jm/((1-q)+A(Fraction(13, 20))*pop)
                f12 = 2*(q-A(Fraction(9, 10))*jh)/(q+1-A(Fraction(9, 10))*pop)
                score = (f10+f12)/3
                baseline = (2*A(Fraction(13, 20))*pop/(1+A(Fraction(13, 20))*pop))/3
                lift = score-baseline
                radius = (lift.upper()-lift.lower())/2
                separation = min(abs(lift.lower()), abs(lift.upper()))
                if radius <= A(Fraction(1, 10**12)) and separation >= A(Fraction(1, 10**10)):
                    break
            idx = max(range(len(bands)), key=lambda i: (
                max(float(bands[i].values[w].upper()-bands[i].values[w].lower())
                    for w in range(3)), -float(bands[i].xiv[0])))
            old = bands[idx]
            yi = max(range(len(old.yivs)), key=lambda i: (float(old.y_scores[i]), -i))
            try:
                yl, yr = bisect(old.yivs[yi])
                ny = old.yivs[:yi]+(yl, yr)+old.yivs[yi+1:]
                y_candidate = cert.band(old.xiv, ny)
                xl, xr = bisect(old.xiv)
                x_candidates = (cert.band(xl, old.yivs), cert.band(xr, old.yivs))
            except Deadline:
                break
            yw = max(y_candidate.values[w].upper()-y_candidate.values[w].lower()
                     for w in range(3))
            xw = max(x_candidates[0].values[w].upper()-x_candidates[0].values[w].lower()
                     + x_candidates[1].values[w].upper()-x_candidates[1].values[w].lower()
                     for w in range(3))
            if xw.upper() < yw.lower():
                bands[idx:idx+1] = list(x_candidates); action = "split_x"
            else:
                bands[idx] = y_candidate; action = "split_y"
            bands.sort(key=lambda b: b.xiv)
            step += 1
            current = rebuild()
            best = tuple(intersect(best[i], current[i]) for i in range(3))
            checkpoint(fp, step, action, bands, best, cert.evaluations)

    pop, jh, jm = best
    q = cdf(-cert.thresholds[1])
    hit, miss = jh/q, jm/(1-q)
    hit_side = "above" if hit.lower() > A(MAJORITY) else "below" if hit.upper() < A(MAJORITY) else None
    miss_side = "above" if miss.lower() > A(MAJORITY) else "below" if miss.upper() < A(MAJORITY) else None
    pop_side = "above" if pop.lower() > A(MAJORITY) else "below" if pop.upper() < A(MAJORITY) else None
    lift = None
    if (hit_side, miss_side, pop_side) == ("below", "above", "above"):
        f10 = 2*A(Fraction(13, 20))*jm/((1-q)+A(Fraction(13, 20))*pop)
        f12 = 2*(q-A(Fraction(9, 10))*jh)/(q+1-A(Fraction(9, 10))*pop)
        lift = (f10+f12)/3-(2*A(Fraction(13, 20))*pop
                              /(1+A(Fraction(13, 20))*pop))/3
    radius = (lift.upper()-lift.lower())/2 if lift is not None else None
    separation = (min(abs(lift.lower()), abs(lift.upper()))
                  if lift is not None and not lift.contains(0) else A(0))
    accepted = bool(hit_side and miss_side and pop_side and lift is not None
                    and radius <= A(Fraction(1, 10**12))
                    and separation >= A(Fraction(1, 10**10)))
    payload = {
        "schema": "m3.2-p6-c4-reduced-taylor.v1", "canary": "P6/C4",
        "spec": SPEC, "reduction": "unchanged analytic bivariate Gaussian two-factor reduction",
        "rng_constructed": False, "protocol_changed": False,
        "precision_bits": args.precision_bits, "taylor_order": args.order,
        "initial_axis": args.initial_axis, "runtime_cap_seconds": args.runtime_cap,
        "runtime_seconds": time.monotonic()-start, "subdivisions": step,
        "bands": len(bands), "leaves": sum(len(b.yivs) for b in bands),
        "evaluations": cert.evaluations, "majority_threshold": "20/31",
        "population": enclosure(pop), "population_majority": pop_side,
        "joint_hit": enclosure(jh), "joint_miss": enclosure(jm),
        "hit": enclosure(hit), "hit_majority": hit_side,
        "miss": enclosure(miss), "miss_majority": miss_side,
        "macro_f1_lift": enclosure(lift) if lift is not None else None,
        "signed_lift_radius": radius.str(35, more=True, radius=False) if radius is not None else None,
        "zero_separation": separation.str(35, more=True, radius=False) if lift is not None else None,
        "accepted": accepted,
        "method": "bivariate Gaussian reduction with nested higher-order Taylor/centered-moment enclosure",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision-bits", type=int, default=224)
    parser.add_argument("--runtime-cap", type=float, default=285.0)
    parser.add_argument("--initial-axis", type=int, default=80)
    parser.add_argument("--order", type=int, default=14)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.precision_bits < 192 or not 0 < args.runtime_cap <= 300
            or args.initial_axis < 8 or not 4 <= args.order <= 16):
        raise SystemExit("fail closed: invalid certificate configuration")
    ctx.prec = args.precision_bits
    ctx.cap = 2*args.order+2
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
