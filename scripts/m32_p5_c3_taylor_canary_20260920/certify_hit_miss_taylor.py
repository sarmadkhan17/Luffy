#!/usr/bin/env python3
"""Rigorous deterministic P5/C3 hit/miss and macro-F1 certificate.

This reuses the accepted population certificate's nested Gaussian
rectangle/Taylor-moment method, with higher-order centered moments.  It reads
no population data and constructs no random state.
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
    return (1 + (x / A(2).sqrt()).erf()) / 2


def density(x: arb) -> arb:
    return (-x*x/2).exp() / (2*arb.pi()).sqrt()


def hull(lo: arb, hi: arb) -> arb:
    return arb((lo + hi)/2, (hi - lo)/2)


def symmetric(radius: arb) -> arb:
    return arb(0, max(A(0), radius.upper()))


def abs_upper(x: arb) -> arb:
    return max(abs(x.lower()), abs(x.upper()))


def enclosure(x: arb) -> dict[str, object]:
    lm, le = x.lower().man_exp()
    um, ue = x.upper().man_exp()
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
    return cdf(A(iv[1])) - cdf(A(iv[0]))


def moments(iv: Interval, center: Fraction, order: int) -> list[arb]:
    """Exact outward enclosures of int (x-center)^k phi(x) dx."""
    out = [mass(iv)]
    lo = A(iv[0]); hi = A(iv[1]); cc = A(center)
    plo = density(lo); phi = density(hi)
    for k in range(1, order + 1):
        prior = A(k-1)*out[k-2] if k >= 2 else arb(0)
        edge = (lo-cc)**(k-1)*plo - (hi-cc)**(k-1)*phi
        out.append(prior-cc*out[k-1]+edge)
    return out


def intersect(x: arb, y: arb) -> arb:
    lo, hi = max(x.lower(), y.lower()), min(x.upper(), y.upper())
    if lo > hi:
        raise ArithmeticError("independent certified enclosures are disjoint")
    return hull(lo, hi)


@dataclass
class Band:
    giv: Interval
    zivs: tuple[Interval, ...]
    values: tuple[arb, arb, arb]  # population, joint-hit, joint-miss
    z_scores: tuple[arb, ...]


class Certifier:
    def __init__(self, deadline: float, order: int):
        self.deadline = deadline
        self.order = order
        self.evaluations = 0
        self.a = A(Fraction(1, 10)).sqrt()
        self.c = A(Fraction(3, 5)).sqrt()
        self.s = A(Fraction(3, 10)).sqrt()
        self.thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
        self.r = A(Fraction(10, 11))
        self.omr = 1-self.r
        self.tail = 1-(cdf(A(T))-cdf(A(-T)))

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Deadline

    def kernels(self, x) -> tuple:
        ps = [cdf((x-threshold)/self.s) for threshold in self.thresholds]
        d0 = 1-self.omr*ps[0]
        d1 = 1-self.omr*ps[1]
        other = d0**2*d1**3
        return d0**2*d1**4, self.r*ps[1]*other, (1-ps[1])*other

    def kernel_series(self, f0: arb, length: int) -> tuple[list[arb], ...]:
        x = arb_series([f0, A(1)], length)
        return tuple(list(v) for v in self.kernels(x))

    def cluster_center(self, g: Fraction, zivs: tuple[Interval, ...]) -> tuple[list[arb], ...]:
        """Taylor coefficients in delta-g for the three truncated cluster integrals."""
        D = self.order
        totals = [[arb(0) for _ in range(D)] for _ in range(3)]
        for ziv in zivs:
            self.check()
            zc = (ziv[0]+ziv[1])/2
            radius = A((ziv[1]-ziv[0])/2)
            mz = moments(ziv, zc, D)
            series = self.kernel_series(self.a*A(g)+self.c*A(zc), 2*D+1)
            fspan = self.a*A(g)+self.c*hull(A(ziv[0]), A(ziv[1]))
            bounds = self.kernel_series(fspan, 2*D+1)
            for which in range(3):
                for k in range(D):
                    value = arb(0)
                    for j in range(D):
                        value += (A(math.comb(k+j, k))*series[which][k+j]
                                  * self.a**k*self.c**j*mz[j])
                    rem = (A(math.comb(k+D, k))*abs_upper(bounds[which][k+D])
                           * self.a**k*self.c**D*radius**D*mz[0])
                    totals[which][k] += value+symmetric(rem)
            self.evaluations += 1
        return tuple(totals)

    def cluster_bounds(self, giv: Interval, zivs: tuple[Interval, ...]) -> tuple[list[arb], ...]:
        """Bounds for delta-g Taylor coefficients throughout an outer band."""
        D = self.order
        totals = [[arb(0) for _ in range(D+1)] for _ in range(3)]
        gs = hull(A(giv[0]), A(giv[1]))
        for ziv in zivs:
            self.check()
            fs = self.a*gs+self.c*hull(A(ziv[0]), A(ziv[1]))
            series = self.kernel_series(fs, D+1)
            mz = mass(ziv)
            for which in range(3):
                for k in range(D+1):
                    totals[which][k] += series[which][k]*self.a**k*mz
            self.evaluations += 1
        return tuple(totals)

    @staticmethod
    def products(series: tuple[list[arb], ...], length: int) -> tuple[list[arb], ...]:
        p, h, m = (arb_series(v, length) for v in series)
        return tuple(list(v) for v in (p**4, h*p**3, m*p**3))

    def band(self, giv: Interval, zivs: tuple[Interval, ...]) -> Band:
        D = self.order
        gc = (giv[0]+giv[1])/2
        mg = moments(giv, gc, D)
        center = self.products(self.cluster_center(gc, zivs), D)
        bounds = self.products(self.cluster_bounds(giv, zivs), D+1)
        radius = A((giv[1]-giv[0])/2)
        values = []
        for which in range(3):
            value = sum((center[which][k]*mg[k] for k in range(D)), arb(0))
            rem = abs_upper(bounds[which][D])*radius**D*mg[0]
            values.append(value+symmetric(rem))
        # A score per z leaf, used only for deterministic refinement priority.
        scores = tuple(A((z[1]-z[0])**D)*mass(z) for z in zivs)
        return Band(giv, zivs, tuple(values), scores)


def checkpoint(fp, step: int, action: str, bands: list[Band], totals: tuple[arb, ...],
               evaluations: int) -> None:
    fp.write(json.dumps({
        "action": action, "bands": len(bands), "evaluation_count": evaluations,
        "leaves": sum(len(b.zivs) for b in bands), "step": step,
        "population": enclosure(totals[0]), "joint_hit": enclosure(totals[1]),
        "joint_miss": enclosure(totals[2]),
    }, sort_keys=True, separators=(",", ":"))+"\n")
    fp.flush()


def run(args: argparse.Namespace) -> dict:
    start = time.monotonic()
    cert = Certifier(start+args.runtime_cap, args.order)
    z0 = tuple(initial_intervals(args.initial_axis))
    bands = [cert.band(g, z0) for g in initial_intervals(args.initial_axis)]
    # Four cluster factors: replacing truncated cluster integrals by full ones
    # changes each product by at most four one-dimensional Gaussian tail masses.
    tail_error = 4*cert.tail

    def rebuild() -> tuple[arb, arb, arb]:
        ans = []
        for which in range(3):
            lo = sum((b.values[which].lower() for b in bands), arb(0))
            hi = sum((b.values[which].upper() for b in bands), arb(0))
            ans.append(hull(max(A(0), lo), min(A(1), hi+tail_error+cert.tail)))
        return tuple(ans)

    totals = rebuild()
    best = totals
    step = 0
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with args.checkpoint.open("w", encoding="utf-8") as fp:
        checkpoint(fp, step, "initial", bands, best, cert.evaluations)
        while time.monotonic() < cert.deadline:
            pop, jh, jm = best
            q = cdf(-cert.thresholds[1])
            hit, miss = jh/q, jm/(1-q)
            # Compute the frozen macro-F1 lift only after all three majority
            # decisions are strict; stop when its certified radius is accepted.
            if (hit.upper() < A(MAJORITY) and miss.lower() > A(MAJORITY)
                    and pop.lower() > A(MAJORITY)):
                f10 = 2*A(Fraction(13, 20))*jm / ((1-q)+A(Fraction(13, 20))*pop)
                f12 = 2*(q-A(Fraction(9, 10))*jh) / (q+1-A(Fraction(9, 10))*pop)
                score = (f10+f12)/3
                baseline = (2*A(Fraction(13, 20))*pop
                            / (1+A(Fraction(13, 20))*pop))/3
                lift = score-baseline
                radius = (lift.upper()-lift.lower())/2
                separation = min(abs(lift.lower()), abs(lift.upper()))
                if radius <= A(Fraction(1, 10**12)) and separation >= A(Fraction(1, 10**10)):
                    break
            idx = max(range(len(bands)), key=lambda i: (
                max(float(bands[i].values[w].upper()-bands[i].values[w].lower())
                    for w in range(3)), -float(bands[i].giv[0])))
            old = bands[idx]
            zi = max(range(len(old.zivs)), key=lambda i: (float(old.z_scores[i]), -i))
            try:
                zl, zr = bisect(old.zivs[zi])
                nz = old.zivs[:zi]+(zl, zr)+old.zivs[zi+1:]
                z_candidate = cert.band(old.giv, nz)
                gl, gr = bisect(old.giv)
                g_candidates = (cert.band(gl, old.zivs), cert.band(gr, old.zivs))
            except Deadline:
                break
            zw = max(z_candidate.values[w].upper()-z_candidate.values[w].lower()
                     for w in range(3))
            gw = max(g_candidates[0].values[w].upper()-g_candidates[0].values[w].lower()
                     + g_candidates[1].values[w].upper()-g_candidates[1].values[w].lower()
                     for w in range(3))
            if gw.upper() < zw.lower():
                bands[idx:idx+1] = list(g_candidates); action = "split_g"
            else:
                bands[idx] = z_candidate; action = "split_z"
            bands.sort(key=lambda b: b.giv)
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
        f10 = 2*A(Fraction(13, 20))*jm / ((1-q)+A(Fraction(13, 20))*pop)
        f12 = 2*(q-A(Fraction(9, 10))*jh) / (q+1-A(Fraction(9, 10))*pop)
        score = (f10+f12)/3
        baseline = (2*A(Fraction(13, 20))*pop/(1+A(Fraction(13, 20))*pop))/3
        lift = score-baseline
    radius = (lift.upper()-lift.lower())/2 if lift is not None else None
    separation = min(abs(lift.lower()), abs(lift.upper())) if lift is not None and not lift.contains(0) else A(0)
    accepted = bool(hit_side and miss_side and lift is not None
                    and radius <= A(Fraction(1, 10**12))
                    and separation >= A(Fraction(1, 10**10)))
    payload = {
        "schema": "m3.2-p5-c3-hit-miss-taylor.v1", "canary": "P5/C3",
        "rng_constructed": False, "protocol_changed": False,
        "precision_bits": args.precision_bits, "taylor_order": args.order,
        "initial_axis": args.initial_axis, "runtime_cap_seconds": args.runtime_cap,
        "runtime_seconds": time.monotonic()-start, "subdivisions": step,
        "bands": len(bands), "leaves": sum(len(b.zivs) for b in bands),
        "evaluations": cert.evaluations, "majority_threshold": "20/31",
        "population": enclosure(pop), "joint_hit": enclosure(jh), "joint_miss": enclosure(jm),
        "hit": enclosure(hit), "hit_majority": hit_side,
        "miss": enclosure(miss), "miss_majority": miss_side,
        "macro_f1_lift": enclosure(lift) if lift is not None else None,
        "signed_lift_radius": radius.str(35, more=True, radius=False) if radius is not None else None,
        "zero_separation": separation.str(35, more=True, radius=False) if lift is not None else None,
        "accepted": accepted,
        "method": "nested Gaussian rectangle higher-order Taylor/centered-moment enclosure",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision-bits", type=int, default=224)
    parser.add_argument("--runtime-cap", type=float, default=285.0)
    parser.add_argument("--initial-axis", type=int, default=32)
    parser.add_argument("--order", type=int, default=10)
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
