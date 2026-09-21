#!/usr/bin/env python3
"""Rigorous deterministic certificate for one remaining P5/C3 or P6/C4 kernel.

Unchanged frozen reductions: the C3 global/cluster Gaussian reduction of the
accepted P5/C3 certificate and the C4 bivariate two-factor reduction of the
accepted P6/C4 certificate, both with the outward Taylor/centered-moment
enclosure.  Only the kernel functions differ per frozen kernel spec.  The
categorical estimand is the frozen probability-mass macro-F1 lift of the
accepted P5/C4 certifier (general hit/miss/population class triple).
No RNG, no simulation, no search, no optimisation.
"""
from __future__ import annotations

import argparse
import hashlib
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
MAX_RADIUS = Fraction(1, 10**12)
MIN_SEPARATION = Fraction(1, 10**10)
Interval = tuple[Fraction, Fraction]
R_N, R_D = 10, 11

C3_SPEC = {"scenario": 5, "correlation": 3, "r_n": R_N, "r_d": R_D, "h_cluster": 0}
KERNELS = {
    # id: (scenario, correlation, h_kind, is_target, geometry)
    "362941837322c8d33797": (5, 3, 0, False, ((2, 4), 3)),
    "5b0820e2459afe050862": (5, 3, 0, False, ((0, 0), 4)),
    "82fa6cbd43d44f3d5ad8": (5, 3, 1, False, ((0, 0), 4)),
    "f94178b70f5311220985": (5, 3, 0, True, ((2, 4), 3)),
    "192a266e63028fd9405f": (6, 4, 0, True, (0, ((8, 0), (0, 16)))),
    "1c44894573b030f89efe": (6, 4, 0, False, (0, ((8, 0), (0, 16)))),
    "444db08366c304e754fc": (6, 4, 1, False, (0, ((8, 0), (0, 16)))),
    "63a8f6ab39aa1458349a": (6, 4, 0, False, (1, ((8, 0), (0, 16)))),
    "7d91c0980805e807c160": (6, 4, 1, True, (1, ((8, 0), (0, 16)))),
}
# already-frozen canaries, kept only to validate this generalisation against
# the accepted certificates (they are not among the nine remaining kernels).
CANARIES = {
    "3b715db927f6e887cfda": (5, 3, 1, True, ((2, 4), 3)),
    "0493407cf230203cfe7b": (6, 4, 1, False, (1, ((8, 0), (0, 16)))),
}
REMAINING = tuple(KERNELS)
KERNELS.update(CANARIES)


def frozen_spec(kid: str) -> dict:
    scenario, corr, hk, target, geo = KERNELS[kid]
    spec = {"scenario": scenario, "correlation": corr, "h_kind": hk,
            "is_target": target, "r_n": R_N, "r_d": R_D}
    if corr == 3:
        focal, n_other = geo
        counts = [list(focal)] + [[0, 0]] * (32 - 1 - n_other) + [[2, 4]] * n_other
        spec.update({"h_cluster": 0, "cluster_counts": counts})
    else:
        sector, counts = geo
        spec.update({"h_sector": sector, "sector_counts": [list(c) for c in counts]})
    return spec


def kernel_id_of(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


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


def qtail(x: arb) -> arb:
    return (x / A(2).sqrt()).erfc() / 2


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
    lo, hi, cc = A(iv[0]), A(iv[1]), A(center)
    plo, phi = density(lo), density(hi)
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


def unit_interval(x: arb) -> arb:
    return hull(max(A(0), x.lower()), min(A(1), x.upper()))


# ----------------------------------------------------------- frozen estimand
def class_probs(survival: arb) -> tuple[arb, arb, arb]:
    return A(Fraction(13, 20))*survival, A(Fraction(1, 4))*survival, 1 - A(Fraction(9, 10))*survival


def winner(probabilities: tuple[arb, arb, arb]) -> int | None:
    cands = [i for i in range(3)
             if all(i == o or probabilities[i].lower() > probabilities[o].upper() for o in range(3))]
    return cands[0] if len(cands) == 1 else None


def macro(ph, pm, q: arb, pred_hit: int, pred_miss: int) -> arb:
    population = [q*ph[c] + (1-q)*pm[c] for c in range(3)]
    terms = []
    for c in range(3):
        tp = ((q*ph[c] if pred_hit == c else 0) + ((1-q)*pm[c] if pred_miss == c else 0))
        predicted = ((q if pred_hit == c else 0) + ((1-q) if pred_miss == c else 0))
        terms.append(2*tp / (2*tp + (predicted-tp) + (population[c]-tp)))
    return sum(terms, arb(0)) / 3


def classify(pop: arb, jh: arb, jm: arb, q: arb) -> dict:
    hit, miss = unit_interval(jh/q), unit_interval(jm/(1-q))
    ph, pm, pp = class_probs(hit), class_probs(miss), class_probs(pop)
    wins = (winner(ph), winner(pm), winner(pp))
    out = {"hit": hit, "miss": miss, "population": pop, "winners": wins,
           "lift": None, "classification": "refused", "accepted": False,
           "radius": None, "separation": None}
    if None in wins:
        return out
    if wins[0] == wins[1] == wins[2]:
        out.update(lift=arb(0), classification="true_null", accepted=True,
                   radius=A(0), separation=A(0))
        return out
    lift = macro(ph, pm, q, wins[0], wins[1]) - macro(ph, pm, q, wins[2], wins[2])
    radius = (lift.upper()-lift.lower())/2
    sep = min(abs(lift.lower()), abs(lift.upper())) if not lift.contains(0) else A(0)
    out.update(lift=lift, classification="non_null", radius=radius, separation=sep,
               accepted=bool(radius <= A(MAX_RADIUS) and sep >= A(MIN_SEPARATION)))
    return out


# ----------------------------------------------------------- kernel models
class Model:
    """Frozen kernel functions.  Cell C3: one field f = a g + c z per cluster
    (three cluster integrals).  Cell C4: left function of x, right of f(x,y)."""

    def __init__(self, kid: str):
        self.kid = kid
        self.spec = frozen_spec(kid)
        assert kernel_id_of(self.spec) == kid, "kernel id/spec mismatch"
        self.scenario, self.corr, self.hk, self.target, self.geo = KERNELS[kid]
        self.thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
        self.r = A(Fraction(R_N, R_D))
        self.omr = 1 - self.r

    def q(self) -> arb:
        return qtail(self.thresholds[self.hk])

    @staticmethod
    def _pw(base, k: int):
        return base**k if k else 1 + 0*base

    def survive(self, p0, p1, counts):
        return self._pw(1-self.omr*p0, counts[0]) * self._pw(1-self.omr*p1, counts[1])

    def focal(self, p0, p1, counts):
        """(pop, hit, miss) kernels of the focal group with the given counts."""
        ph = (p0, p1)[self.hk]
        if self.target:
            rest = list(counts)
            rest[self.hk] -= 1
            assert rest[self.hk] >= 0
            base = self.survive(p0, p1, tuple(rest))
            return self.survive(p0, p1, counts), self.r*ph*base, (1-ph)*base
        base = self.survive(p0, p1, counts)
        return base, ph*base, (1-ph)*base


class C3Model(Model):
    """f = a g + c z ; p_k = Phi((f - t_k)/s)."""

    def __init__(self, kid: str):
        super().__init__(kid)
        self.focal_counts, self.n_other = self.geo
        self.a = A(Fraction(1, 10)).sqrt()
        self.c = A(Fraction(3, 5)).sqrt()
        self.s = A(Fraction(3, 10)).sqrt()
        self.assembly_power = self.n_other + (1 if self.focal_counts != (0, 0) else 0)

    def kernels(self, x) -> tuple:
        p0, p1 = (cdf((x-t)/self.s) for t in self.thresholds)
        pop = self.survive(p0, p1, (2, 4))               # one (2,4) cluster
        if self.focal_counts == (0, 0):
            ph = (p0, p1)[self.hk]
            return pop, ph, 1-ph                          # focal cluster is empty
        _, fh, fm = self.focal(p0, p1, self.focal_counts)
        return pop, fh, fm

    def assemble(self, cp, ch, cm, length):
        p, h, m = (arb_series(v, length) for v in (cp, ch, cm))
        rest = p**self.n_other
        return list(p**self.assembly_power), list(h*rest), list(m*rest)


class C4Model(Model):
    """Sector 0 uses factor x, sector 1 uses f = -x/2 + sqrt(3) y/2."""

    def __init__(self, kid: str):
        super().__init__(kid)
        self.sector, self.counts = self.geo
        self.ax = A(Fraction(-1, 2))
        self.cy = A(Fraction(3, 4)).sqrt()
        self.systematic = A(Fraction(3, 5)).sqrt()
        self.residual = A(Fraction(2, 5)).sqrt()

    def member(self, f, which: int):
        return cdf((self.systematic*f - self.thresholds[which])/self.residual)

    def sector_kernels(self, f, sec: int):
        p0, p1 = self.member(f, 0), self.member(f, 1)
        if sec == self.sector:
            return self.focal(p0, p1, self.counts[sec])
        base = self.survive(p0, p1, self.counts[sec])
        return base, base, base

    def left_kernels(self, x):
        return self.sector_kernels(x, 0)

    def right_kernels(self, f):
        return self.sector_kernels(f, 1)

    @property
    def right_shared(self) -> bool:
        return self.sector == 0


# ----------------------------------------------------------- band data
@dataclass
class Band:
    outer: Interval
    leaves: tuple[Interval, ...]
    values: tuple[arb, arb, arb]
    scores: tuple[arb, ...]


class Certifier:
    def __init__(self, model: Model, deadline: float, order: int, reverse: bool, max_steps: int):
        self.m = model
        self.deadline = deadline
        self.order = order
        self.reverse = reverse
        self.max_steps = max_steps
        self.evaluations = 0
        self.tail = 1 - (cdf(A(T)) - cdf(A(-T)))

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Deadline

    def order_leaves(self, leaves):
        return reversed(leaves) if self.reverse else leaves

    def leaf_series(self, fn, f0: arb, length: int) -> tuple[list[arb], ...]:
        s = arb_series([f0, A(1)], length)
        return tuple(list(v) for v in fn(s))

    # -- shared inner integral: kernels of the inner field, three outputs
    def inner_fn(self):
        return self.m.kernels if isinstance(self.m, C3Model) else self.m.right_kernels

    def inner_load(self) -> tuple[arb, arb]:
        """f = outer_load * outer + inner_load * inner."""
        if isinstance(self.m, C3Model):
            return self.m.a, self.m.c
        return self.m.ax, self.m.cy

    def n_dist(self) -> int:
        return 1 if (isinstance(self.m, C4Model) and self.m.right_shared) else 3

    def inner_center(self, x: Fraction, leaves):
        D = self.order
        ol, il = self.inner_load()
        nd = self.n_dist()
        totals = [[arb(0) for _ in range(D)] for _ in range(nd)]
        for iv in self.order_leaves(leaves):
            self.check()
            zc = (iv[0]+iv[1])/2
            radius = A((iv[1]-iv[0])/2)
            mz = moments(iv, zc, D)
            series = self.leaf_series(self.inner_fn(), ol*A(x)+il*A(zc), 2*D+1)
            fspan = ol*A(x)+il*hull(A(iv[0]), A(iv[1]))
            bounds = self.leaf_series(self.inner_fn(), fspan, 2*D+1)
            for w in range(nd):
                for k in range(D):
                    value = arb(0)
                    for j in range(D):
                        value += (A(math.comb(k+j, k))*series[w][k+j]*ol**k*il**j*mz[j])
                    rem = (A(math.comb(k+D, k))*abs_upper(bounds[w][k+D])
                           * abs(ol)**k*il**D*radius**D*mz[0])
                    totals[w][k] += value+symmetric(rem)
            self.evaluations += 1
        return tuple(totals)

    def inner_bounds(self, oiv: Interval, leaves):
        D = self.order
        ol, il = self.inner_load()
        nd = self.n_dist()
        totals = [[arb(0) for _ in range(D+1)] for _ in range(nd)]
        gs = hull(A(oiv[0]), A(oiv[1]))
        for iv in self.order_leaves(leaves):
            self.check()
            fs = ol*gs+il*hull(A(iv[0]), A(iv[1]))
            series = self.leaf_series(self.inner_fn(), fs, D+1)
            mz = mass(iv)
            for w in range(nd):
                for k in range(D+1):
                    totals[w][k] += series[w][k]*ol**k*mz
            self.evaluations += 1
        return tuple(totals)

    def products(self, x0, center_or_bounds, length: int):
        if isinstance(self.m, C3Model):
            return self.m.assemble(*center_or_bounds, length)
        right = center_or_bounds
        if len(right) == 1:
            right = (right[0],)*3
        left = tuple(arb_series(v, length) for v in self.leaf_series(self.m.left_kernels, x0, length))
        return tuple(list(left[w]*arb_series(right[w], length)) for w in range(3))

    def band(self, oiv: Interval, leaves) -> Band:
        D = self.order
        oc = (oiv[0]+oiv[1])/2
        mo = moments(oiv, oc, D)
        center = self.products(A(oc), self.inner_center(oc, leaves), D)
        bounds = self.products(hull(A(oiv[0]), A(oiv[1])), self.inner_bounds(oiv, leaves), D+1)
        radius = A((oiv[1]-oiv[0])/2)
        values = []
        for w in range(3):
            value = sum((center[w][k]*mo[k] for k in range(D)), arb(0))
            rem = abs_upper(bounds[w][D])*radius**D*mo[0]
            values.append(value+symmetric(rem))
        scores = tuple(A((z[1]-z[0])**D)*mass(z) for z in leaves)
        return Band(oiv, leaves, tuple(values), scores)


def width(x: arb):
    return x.upper()-x.lower()


def run(args: argparse.Namespace) -> dict:
    start = time.monotonic()
    model = C3Model(args.kernel_id) if KERNELS[args.kernel_id][1] == 3 else C4Model(args.kernel_id)
    cert = Certifier(model, start+args.runtime_cap, args.order, args.reverse_inner, args.max_steps)
    q = model.q()
    leaves0 = tuple(initial_intervals(args.initial_axis))
    bands = [cert.band(o, leaves0) for o in initial_intervals(args.initial_axis)]
    # truncated inner integrals of nonnegative integrands are lower bounds; each
    # (at most 5) inner factor and the outer truncation lose at most one tail mass.
    n_dims = 5 if isinstance(model, C3Model) else 2
    tail_error = n_dims*cert.tail

    def rebuild() -> tuple[arb, arb, arb]:
        ans = []
        for w in range(3):
            lo = sum((b.values[w].lower() for b in bands), arb(0))
            hi = sum((b.values[w].upper() for b in bands), arb(0))
            ans.append(hull(max(A(0), lo), min(A(1), hi+tail_error+cert.tail)))
        ans[0] = intersect(ans[0], ans[1]+ans[2])
        return tuple(ans)

    best = rebuild()
    step = 0
    stopped = "budget"
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with args.checkpoint.open("w", encoding="utf-8") as fp:
        def log(action):
            fp.write(json.dumps({
                "kernel_id": args.kernel_id, "action": action, "step": step, "bands": len(bands),
                "leaves": sum(len(b.leaves) for b in bands), "evaluation_count": cert.evaluations,
                "population": enclosure(best[0]), "joint_hit": enclosure(best[1]),
                "joint_miss": enclosure(best[2])}, sort_keys=True, separators=(",", ":"))+"\n")
            fp.flush()
        log("initial")
        while True:
            res = classify(best[0], best[1], best[2], q)
            if res["accepted"]:
                stopped = "accepted"
                break
            if step >= args.max_steps:
                break
            if time.monotonic() >= cert.deadline:
                stopped = "runtime_safety_cap"
                break
            idx = max(range(len(bands)), key=lambda i: (
                max(float(width(bands[i].values[w])) for w in range(3)), -float(bands[i].outer[0])))
            old = bands[idx]
            zi = max(range(len(old.leaves)), key=lambda i: (float(old.scores[i]), -i))
            try:
                zl, zr = bisect(old.leaves[zi])
                nz = old.leaves[:zi]+(zl, zr)+old.leaves[zi+1:]
                z_candidate = cert.band(old.outer, nz)
                gl, gr = bisect(old.outer)
                g_candidates = (cert.band(gl, old.leaves), cert.band(gr, old.leaves))
            except Deadline:
                stopped = "runtime_safety_cap"
                break
            zw = max(width(z_candidate.values[w]) for w in range(3))
            gw = max(width(g_candidates[0].values[w])+width(g_candidates[1].values[w]) for w in range(3))
            if gw.upper() < zw.lower():
                bands[idx:idx+1] = list(g_candidates)
                action = "split_outer"
            else:
                bands[idx] = z_candidate
                action = "split_inner"
            bands.sort(key=lambda b: b.outer)
            step += 1
            best = tuple(intersect(best[i], rebuild()[i]) for i in range(3))
            log(action)

    res = classify(best[0], best[1], best[2], q)
    lift = res["lift"]
    status = "certified" if res["accepted"] else "refused"
    payload = {
        "schema": "m3.2-remaining-categorical-kernel.v1", "kernel_id": args.kernel_id,
        "spec": model.spec, "status": status, "classification": res["classification"],
        "stopped_by": stopped,
        "strict_argmax": {"hit": res["winners"][0], "miss": res["winners"][1], "population": res["winners"][2]},
        "rng_constructed": False, "protocol_changed": False, "search_performed": False,
        "precision_bits": args.precision_bits, "taylor_order": args.order,
        "initial_axis": args.initial_axis, "reverse_inner_traversal": bool(args.reverse_inner),
        "max_steps": args.max_steps, "runtime_seconds": time.monotonic()-start,
        "subdivisions": step, "bands": len(bands), "leaves": sum(len(b.leaves) for b in bands),
        "evaluations": cert.evaluations, "majority_threshold": "20/31",
        "reduction": ("unchanged C3 global/cluster Gaussian reduction" if isinstance(model, C3Model)
                      else "unchanged C4 bivariate two-factor Gaussian reduction"),
        "joint_hit_interval": enclosure(best[1]), "joint_miss_interval": enclosure(best[2]),
        "conditional_hit_interval": enclosure(res["hit"]),
        "conditional_miss_interval": enclosure(res["miss"]),
        "population_interval": enclosure(res["population"]),
        "signed_lift_interval": enclosure(lift) if lift is not None else None,
        "signed_lift_radius": res["radius"].str(35, more=True, radius=False) if lift is not None else None,
        "zero_separation": res["separation"].str(35, more=True, radius=False) if lift is not None else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel-id", required=True, choices=sorted(KERNELS))
    ap.add_argument("--precision-bits", type=int, default=224)
    ap.add_argument("--runtime-cap", type=float, default=3300.0)
    ap.add_argument("--initial-axis", type=int, default=80)
    ap.add_argument("--order", type=int, default=14)
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--reverse-inner", action="store_true")
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if (args.precision_bits < 192 or not 0 < args.runtime_cap <= 7200
            or args.initial_axis < 8 or not 4 <= args.order <= 16):
        raise SystemExit("fail closed: invalid certificate configuration")
    ctx.prec = args.precision_bits
    ctx.cap = 2*args.order+2
    out = run(args)
    print(json.dumps({k: out[k] for k in ("kernel_id", "status", "classification", "stopped_by",
                                          "strict_argmax", "signed_lift_radius", "zero_separation",
                                          "runtime_seconds")}, sort_keys=True))


if __name__ == "__main__":
    main()
