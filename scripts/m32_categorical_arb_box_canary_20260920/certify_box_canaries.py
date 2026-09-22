#!/usr/bin/env python3
"""Boxwise rigorous canaries for the frozen P5/C3 and P6/C4 primitives.

The Gaussian measure of every rectangle is evaluated analytically.  Only the
positive PGF is interval-evaluated on the rectangle, so no validated integral
is nested inside another validated integral.  Every evaluated rectangle is
written to a JSONL checkpoint before the next rectangle is started.
"""
from __future__ import annotations

import argparse
import heapq
import json
import time
from fractions import Fraction
from pathlib import Path

from flint import arb, ctx


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "m32_categorical_arb_batch_20260920" / "certificate.json"
T = 16
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
DELTA = Fraction(1, 10_000_000_000)
MAX_RADIUS = Fraction(1, 1_000_000_000_000)
MAJORITY = Fraction(20, 31)


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


def qtail(x: arb) -> arb:
    return (x / A(2).sqrt()).erfc() / 2


def hull(lo: arb, hi: arb) -> arb:
    return arb((lo + hi) / 2, (hi - lo) / 2)


def unit_interval(x: arb) -> arb:
    lo = max(A(0), x.lower())
    hi = min(A(1), x.upper())
    if lo > hi:
        raise ArithmeticError("probability enclosure misses [0,1]")
    return hull(lo, hi)


def bounded_remainder(upper: arb) -> arb:
    upper = max(A(0), upper.upper())
    return arb(upper / 2, upper / 2)


def member(f: arb, threshold: arb, systematic: Fraction, residual: Fraction) -> arb:
    return cdf((A(systematic).sqrt() * f - threshold) / A(residual).sqrt())


def factor(ps: tuple[arb, arb], count: tuple[int, int], r: arb) -> arb:
    return (1 - (1-r)*ps[0])**count[0] * (1-(1-r)*ps[1])**count[1]


def minus(count: tuple[int, int], which: int) -> tuple[int, int]:
    out = list(count)
    out[which] -= 1
    if out[which] < 0:
        raise ArithmeticError("focal target absent")
    return out[0], out[1]


def endpoints(boxes: int) -> list[arb]:
    return [A(Fraction(-T*boxes + 2*T*i, boxes)) for i in range(boxes+1)]


def normal_boxes(boxes: int) -> tuple[list[arb], list[arb]]:
    ep = endpoints(boxes)
    spans = [hull(ep[i], ep[i+1]) for i in range(boxes)]
    masses = [cdf(ep[i+1])-cdf(ep[i]) for i in range(boxes)]
    return spans, masses


def dyadic(x: arb) -> dict[str, int]:
    m, e = x.man_exp()
    return {"mantissa": int(m), "exponent": int(e)}


def enclosure(x: arb) -> dict[str, object]:
    return {
        "lower_dyadic": dyadic(x.lower()),
        "upper_dyadic": dyadic(x.upper()),
        "decimal": x.str(40, more=True),
        "width_decimal": (x.upper()-x.lower()).str(20, more=True, radius=False),
        "radius_decimal": x.rad().str(20, more=True, radius=False),
    }


def checkpoint(fp, *, canary: str, i: int, j: int, evaluations: int,
               precision: int, replay: bool) -> None:
    fp.write(json.dumps({"canary": canary, "i": i, "j": j,
                         "evaluation_count": evaluations,
                         "precision_bits": precision, "replay": replay},
                        sort_keys=True, separators=(",", ":")) + "\n")
    fp.flush()


def select_spec(kind: str) -> tuple[str, dict]:
    report = json.loads(SOURCE.read_text())
    correlation, scenario = (3, 5) if kind == "c3" else (4, 6)
    candidates = [(kid, rec["spec"]) for kid, rec in report["kernels"].items()
                  if rec["spec"]["correlation"] == correlation
                  and rec["spec"]["scenario"] == scenario]
    if not candidates:
        raise SystemExit(f"no P{scenario}/C{correlation} canary in source report")
    return sorted(candidates)[0]


def c3_once(spec: dict, boxes: int, deadline: float, fp, precision: int,
            replay: bool) -> tuple[tuple[arb, arb, arb], int, bool]:
    spans, masses = normal_boxes(boxes)
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    counts = tuple(tuple(x) for x in spec["cluster_counts"])
    focal = counts[0]
    multiplicity = sum(c == focal for c in counts)
    if focal == (0, 0) or any(c != (0, 0) and c != focal for c in counts):
        raise ArithmeticError("canary reduction requires one repeated nonempty cluster type")
    hkind, target = int(spec["h_kind"]), bool(spec["is_target"])
    total = [arb(0), arb(0), arb(0)]
    evaluations = 0
    stopped = False
    processed_g_mass = arb(0)
    for i, (g, mg) in enumerate(zip(spans, masses)):
        inner = [arb(0), arb(0), arb(0)]
        processed_z_mass = arb(0)
        for j, (z, mz) in enumerate(zip(spans, masses)):
            if time.monotonic() >= deadline:
                stopped = True
                break
            f = A(Fraction(1, 10)).sqrt()*g + A(Fraction(3, 5)).sqrt()*z
            ps = (cdf((f-thresholds[0])/A(Fraction(3, 10)).sqrt()),
                  cdf((f-thresholds[1])/A(Fraction(3, 10)).sqrt()))
            base = factor(ps, focal, r)
            ph = ps[hkind]
            if target:
                reduced = factor(ps, minus(focal, hkind), r)
                hit, miss = ph*r*reduced, (1-ph)*reduced
            else:
                hit, miss = ph*base, (1-ph)*base
            for k, value in enumerate((base, hit, miss)):
                inner[k] += mz*value
            processed_z_mass += mz
            evaluations += 1
            checkpoint(fp, canary="P5/C3", i=i, j=j, evaluations=evaluations,
                       precision=precision, replay=replay)
        z_remaining = 1-processed_z_mass
        inner = [unit_interval(v + bounded_remainder(z_remaining)) for v in inner]
        total[0] += mg*inner[0]**multiplicity
        total[1] += mg*inner[0]**(multiplicity-1)*inner[1]
        total[2] += mg*inner[0]**(multiplicity-1)*inner[2]
        processed_g_mass += mg
        if stopped:
            break
    g_remaining = 1-processed_g_mass
    raw = [unit_interval(v + bounded_remainder(g_remaining)) for v in total]
    q = qtail(thresholds[hkind])
    raw[1] = hull(max(A(0), raw[1].lower()), min(q.upper(), raw[1].upper()))
    raw[2] = hull(max(A(0), raw[2].lower()), min((1-q).upper(), raw[2].upper()))
    return (raw[0], unit_interval(raw[1]/q), unit_interval(raw[2]/(1-q))), evaluations, not stopped


def sector(f: arb, count: tuple[int, int], r: arb, thresholds: tuple[arb, arb],
           mode: str = "base", hkind: int = 0, target: bool = False) -> arb:
    ps = (member(f, thresholds[0], Fraction(3, 5), Fraction(2, 5)),
          member(f, thresholds[1], Fraction(3, 5), Fraction(2, 5)))
    base = factor(ps, count, r)
    if mode == "base":
        return base
    ph = ps[hkind]
    if target:
        reduced = factor(ps, minus(count, hkind), r)
        return ph*r*reduced if mode == "hit" else (1-ph)*reduced
    return ph*base if mode == "hit" else (1-ph)*base


def c4_once(spec: dict, boxes: int, deadline: float, fp, precision: int,
            replay: bool) -> tuple[tuple[arb, arb, arb], int, bool]:
    spans, masses = normal_boxes(boxes)
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    counts = tuple(tuple(x) for x in spec["sector_counts"])
    sec, hkind, target = int(spec["h_sector"]), int(spec["h_kind"]), bool(spec["is_target"])
    total = [arb(0), arb(0), arb(0)]
    processed_mass = arb(0)
    evaluations = 0
    stopped = False
    for i, (x, mx) in enumerate(zip(spans, masses)):
        for j, (y, my) in enumerate(zip(spans, masses)):
            if time.monotonic() >= deadline:
                stopped = True
                break
            f = -x/2 + A(3).sqrt()*y/2
            left = sector(x, counts[0], r, thresholds)
            right = sector(f, counts[1], r, thresholds)
            base = left*right
            if sec == 0:
                hit = sector(x, counts[0], r, thresholds, "hit", hkind, target)*right
                miss = sector(x, counts[0], r, thresholds, "miss", hkind, target)*right
            else:
                hit = left*sector(f, counts[1], r, thresholds, "hit", hkind, target)
                miss = left*sector(f, counts[1], r, thresholds, "miss", hkind, target)
            mass = mx*my
            for k, value in enumerate((base, hit, miss)):
                total[k] += mass*value
            processed_mass += mass
            evaluations += 1
            checkpoint(fp, canary="P6/C4", i=i, j=j, evaluations=evaluations,
                       precision=precision, replay=replay)
        if stopped:
            break
    remaining = 1-processed_mass
    raw = [unit_interval(v + bounded_remainder(remaining)) for v in total]
    q = qtail(thresholds[hkind])
    raw[1] = hull(max(A(0), raw[1].lower()), min(q.upper(), raw[1].upper()))
    raw[2] = hull(max(A(0), raw[2].lower()), min((1-q).upper(), raw[2].upper()))
    return (raw[0], unit_interval(raw[1]/q), unit_interval(raw[2]/(1-q))), evaluations, not stopped


# These adaptive routines retain the reductions above and only change how the
# finite [-T,T] rectangles are subdivided. Gaussian rectangle masses remain
# analytic and omitted tails remain bounded by their mass times [0,1].
Interval = tuple[Fraction, Fraction]


def width(x: arb) -> arb:
    return x.upper() - x.lower()


def width_float(x: arb) -> float:
    return float(width(x))


def mass(iv: Interval) -> arb:
    return cdf(A(iv[1])) - cdf(A(iv[0]))


def span(iv: Interval) -> arb:
    return hull(A(iv[0]), A(iv[1]))


def bisect(iv: Interval) -> tuple[Interval, Interval]:
    mid = (iv[0] + iv[1]) / 2
    return (iv[0], mid), (mid, iv[1])


def initial_intervals(n: int = 4) -> list[Interval]:
    return [(Fraction(-T) + Fraction(2*T*i, n),
             Fraction(-T) + Fraction(2*T*(i+1), n)) for i in range(n)]


def majority_winner(g: arb) -> int | None:
    """Certify the sole possible p0/p2 boundary, g=20/31."""
    boundary = A(MAJORITY)
    if g.lower() > boundary:
        return 0
    if g.upper() < boundary:
        return 2
    return None


def finalize(raw: tuple[arb, arb, arb], spec: dict) -> tuple[arb, arb, arb]:
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    q = qtail(thresholds[int(spec["h_kind"])])
    pg, hit, miss = raw
    hit = hull(max(A(0), hit.lower()), min(q.upper(), hit.upper()))
    miss = hull(max(A(0), miss.lower()), min((1-q).upper(), miss.upper()))
    return unit_interval(pg), unit_interval(hit/q), unit_interval(miss/(1-q))


def adaptive_checkpoint(fp, *, label: str, step: int, action: str,
                        evaluations: int, leaf_boxes: int,
                        values: tuple[arb, arb, arb], precision: int,
                        replay: bool, detail: dict | None = None) -> None:
    record = {
        "action": action, "canary": label,
        "enclosure_widths": [enclosure(v)["width_decimal"] for v in values],
        "evaluation_count": evaluations, "leaf_boxes": leaf_boxes,
        "majorities": [majority_winner(v) for v in (values[1], values[2], values[0])],
        "precision_bits": precision, "replay": replay, "step": step,
    }
    if detail:
        record["detail"] = detail
    fp.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    fp.flush()


def c3_point(spec: dict, f: arb) -> tuple[arb, arb, arb]:
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    focal = tuple(spec["cluster_counts"][0])
    hkind, target = int(spec["h_kind"]), bool(spec["is_target"])
    ps = (cdf((f-thresholds[0])/A(Fraction(3, 10)).sqrt()),
          cdf((f-thresholds[1])/A(Fraction(3, 10)).sqrt()))
    base = factor(ps, focal, r)
    ph = ps[hkind]
    if target:
        reduced = factor(ps, minus(focal, hkind), r)
        return base, ph*r*reduced, (1-ph)*reduced
    return base, ph*base, (1-ph)*base


def c3_cell(spec: dict, giv: Interval, ziv: Interval) -> tuple[arb, arb, arb]:
    """Certified rectangle values, reusing monotonicity for base and miss."""
    f = A(Fraction(1, 10)).sqrt()*span(giv) + A(Fraction(3, 5)).sqrt()*span(ziv)
    lo = c3_point(spec, f.lower())
    hi = c3_point(spec, f.upper())
    pop = hull(hi[0].lower(), lo[0].upper())
    miss = hull(hi[2].lower(), lo[2].upper())
    # The hit derivative may change sign; direct Arb evaluation is conservative.
    hit = c3_point(spec, f)[1]
    return unit_interval(pop), unit_interval(hit), unit_interval(miss)


def c3_band(inner: tuple[arb, arb, arb], mg: arb, multiplicity: int,
            inner_tail: arb) -> tuple[arb, arb, arb]:
    vals = [unit_interval(v + bounded_remainder(inner_tail)) for v in inner]
    return (mg*vals[0]**multiplicity,
            mg*vals[0]**(multiplicity-1)*vals[1],
            mg*vals[0]**(multiplicity-1)*vals[2])


def c3_adaptive(spec: dict, max_axis: int, deadline: float, fp,
                precision: int, replay: bool):
    givs = initial_intervals()
    domain_mass = cdf(A(T))-cdf(A(-T))
    tail = 1-domain_mass
    counts = tuple(tuple(x) for x in spec["cluster_counts"])
    focal = counts[0]
    multiplicity = sum(c == focal for c in counts)
    if focal == (0, 0) or any(c != (0, 0) and c != focal for c in counts):
        raise ArithmeticError("canary reduction requires one repeated nonempty cluster type")
    # Every outer band owns its inner partition. Splitting one band therefore
    # never forces evaluation of the same inner interval in unrelated bands.
    cells, inner, bands, zparts = {}, {}, {}, {}
    evaluations = 0

    def fill_band(giv: Interval, zivs: list[Interval]) -> None:
        nonlocal evaluations
        sums = [arb(0), arb(0), arb(0)]
        for ziv in zivs:
            val = c3_cell(spec, giv, ziv)
            cells[giv, ziv] = val
            mz = mass(ziv)
            for k in range(3):
                sums[k] += mz*val[k]
            evaluations += 1
        zparts[giv] = zivs
        inner[giv] = sums
        bands[giv] = c3_band(tuple(sums), mass(giv), multiplicity, tail)

    for giv in givs:
        fill_band(giv, initial_intervals())
    total_lo = [sum((bands[g][k].lower() for g in givs), arb(0)) for k in range(3)]
    total_hi = [sum((bands[g][k].upper() for g in givs), arb(0)) for k in range(3)]

    def leaf_count() -> int:
        return sum(len(zparts[g]) for g in givs)

    def current():
        raw = tuple(unit_interval(hull(total_lo[k], total_hi[k])+
                                  bounded_remainder(tail)) for k in range(3))
        return finalize(raw, spec)

    values, step = current(), 0
    adaptive_checkpoint(fp, label="P5/C3", step=step, action="initial",
                        evaluations=evaluations, leaf_boxes=leaf_count(),
                        values=values, precision=precision, replay=replay,
                        detail={"inner_partition": "local_per_outer_band"})
    while time.monotonic() < deadline:
        unresolved = tuple(k for k in range(3) if majority_winner(values[k]) is None)
        if not unresolved:
            break

        # Pick the local rectangle with the greatest unresolved-majority
        # uncertainty, then bisect the coordinate contributing more to its
        # latent-factor span. This keeps outer and inner resolution balanced
        # without creating a shared inner axis.
        best_local = None
        local_key = None
        for giv in givs:
            mg = mass(giv).upper()
            for ziv in zparts[giv]:
                val = cells[giv, ziv]
                mz = mass(ziv).upper()
                scores = []
                for k in unresolved:
                    if k == 0:
                        sensitivity = A(multiplicity)*width(val[0])
                    else:
                        sensitivity = (A(multiplicity-1)*width(val[0])
                                       + width(val[k]))
                    scores.append(float(mg*mz*sensitivity)/2)
                key = (max(scores), -float(giv[0]), -float(ziv[0]))
                if local_key is None or key > local_key:
                    best_local, local_key = (giv, ziv), key
        if best_local is None:
            break
        giv, ziv = best_local
        g_span2 = Fraction(1, 10)*(giv[1]-giv[0])**2
        z_span2 = Fraction(3, 5)*(ziv[1]-ziv[0])**2
        split_g = (g_span2 >= z_span2 and len(givs) < max_axis)
        if len(zparts[giv]) >= max_axis:
            split_g = len(givs) < max_axis
        if not split_g and len(zparts[giv]) >= max_axis:
            break

        if split_g:
            old, children = giv, bisect(giv)
            idx = givs.index(old)
            givs[idx:idx+1] = list(children)
            for k in range(3):
                total_lo[k] -= bands[old][k].lower()
                total_hi[k] -= bands[old][k].upper()
            parent_zivs = zparts.pop(old)
            del inner[old], bands[old]
            for ziv in parent_zivs:
                del cells[old, ziv]
            for child in children:
                fill_band(child, list(parent_zivs))
                for k in range(3):
                    total_lo[k] += bands[child][k].lower()
                    total_hi[k] += bands[child][k].upper()
            action, detail = "split_g", {
                "interval": [str(old[0]), str(old[1])],
                "inherited_inner_intervals": len(parent_zivs),
            }
        else:
            old = ziv
            children = bisect(old)
            zivs = zparts[giv]
            idx = zivs.index(old)
            zivs[idx:idx+1] = list(children)
            mz_old = mass(old)
            old_band = bands[giv]
            old_val = cells.pop((giv, old))
            old_terms = [mz_old*old_val[k] for k in range(3)]
            next_lo = [inner[giv][k].lower()-old_terms[k].lower() for k in range(3)]
            next_hi = [inner[giv][k].upper()-old_terms[k].upper() for k in range(3)]
            for child in children:
                val = c3_cell(spec, giv, child)
                cells[giv, child] = val
                mz = mass(child)
                for k in range(3):
                    term = mz*val[k]
                    next_lo[k] += term.lower()
                    next_hi[k] += term.upper()
                evaluations += 1
            inner[giv] = [hull(next_lo[k], next_hi[k]) for k in range(3)]
            bands[giv] = c3_band(tuple(inner[giv]), mass(giv), multiplicity, tail)
            for k in range(3):
                total_lo[k] += bands[giv][k].lower()-old_band[k].lower()
                total_hi[k] += bands[giv][k].upper()-old_band[k].upper()
            action, detail = "split_z_local", {
                "outer_interval": [str(giv[0]), str(giv[1])],
                "inner_interval": [str(old[0]), str(old[1])],
            }
        detail["unresolved_majorities"] = [
            ("population", "hit", "miss")[k] for k in unresolved
        ]
        step += 1
        values = current()
        adaptive_checkpoint(fp, label="P5/C3", step=step, action=action,
                            evaluations=evaluations, leaf_boxes=leaf_count(),
                            values=values, precision=precision, replay=replay, detail=detail)
    sizes = [len(zparts[g]) for g in givs]
    partition = {
        "scheme": "local_inner_partition_per_outer_band",
        "shared_inner_axis": False,
        "outer_bands": len(givs),
        "inner_intervals_total": sum(sizes),
        "inner_intervals_min": min(sizes),
        "inner_intervals_max": max(sizes),
        "selection_uses_unresolved_majorities_only": True,
    }
    return (values, evaluations, leaf_count(), time.monotonic() < deadline,
            step, partition)


def sector_log_derivative(f: arb, count: tuple[int, int], r: arb,
                          thresholds: tuple[arb, arb]) -> arb:
    residual = A(Fraction(2, 5)).sqrt()
    ps = (member(f, thresholds[0], Fraction(3, 5), Fraction(2, 5)),
          member(f, thresholds[1], Fraction(3, 5), Fraction(2, 5)))
    out, a = arb(0), 1-r
    for p, n, threshold in zip(ps, count, thresholds):
        if not n:
            continue
        u = (A(Fraction(3, 5)).sqrt()*f-threshold)/residual
        density = (-u*u/2).exp()/(2*arb.pi()).sqrt()
        dp = A(Fraction(3, 5)).sqrt()/residual*density
        out -= n*a*dp/(1-a*p)
    return out


def c4_point(spec: dict, x: arb, y: arb) -> tuple[arb, arb, arb]:
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    counts = tuple(tuple(x) for x in spec["sector_counts"])
    sec, hkind, target = int(spec["h_sector"]), int(spec["h_kind"]), bool(spec["is_target"])
    f = -x/2 + A(3).sqrt()*y/2
    left, right = sector(x, counts[0], r, thresholds), sector(f, counts[1], r, thresholds)
    base = left*right
    if sec == 0:
        return (base, sector(x, counts[0], r, thresholds, "hit", hkind, target)*right,
                sector(x, counts[0], r, thresholds, "miss", hkind, target)*right)
    return (base, left*sector(f, counts[1], r, thresholds, "hit", hkind, target),
            left*sector(f, counts[1], r, thresholds, "miss", hkind, target))


def c4_cell(spec: dict, xiv: Interval, yiv: Interval) -> tuple[arb, arb, arb]:
    xs, ys = span(xiv), span(yiv)
    generic = c4_point(spec, xs, ys)
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    counts = tuple(tuple(x) for x in spec["sector_counts"])
    f = -xs/2 + A(3).sqrt()*ys/2
    dx = (sector_log_derivative(xs, counts[0], r, thresholds)
          - sector_log_derivative(f, counts[1], r, thresholds)/2)
    pop = generic[0]
    # Population is decreasing in y; reuse x monotonicity only when certified.
    if dx.lower() >= 0:
        pop = hull(c4_point(spec, A(xiv[0]), A(yiv[1]))[0].lower(),
                   c4_point(spec, A(xiv[1]), A(yiv[0]))[0].upper())
    elif dx.upper() <= 0:
        pop = hull(c4_point(spec, A(xiv[1]), A(yiv[1]))[0].lower(),
                   c4_point(spec, A(xiv[0]), A(yiv[0]))[0].upper())
    return unit_interval(pop), unit_interval(generic[1]), unit_interval(generic[2])


def c4_adaptive(spec: dict, max_leaves: int, deadline: float, fp,
                precision: int, replay: bool):
    leaves, heap = {}, []
    serial = evaluations = 0
    total_lo = [arb(0), arb(0), arb(0)]
    total_hi = [arb(0), arb(0), arb(0)]

    def add_leaf(xiv: Interval, yiv: Interval, dx: int, dy: int) -> None:
        nonlocal serial, evaluations
        val = c4_cell(spec, xiv, yiv)
        cmass = mass(xiv)*mass(yiv)
        contrib = tuple(cmass*v for v in val)
        leaves[serial] = (xiv, yiv, dx, dy, contrib)
        heapq.heappush(heap, (-width_float(contrib[0]), serial))
        for k in range(3):
            total_lo[k] += contrib[k].lower()
            total_hi[k] += contrib[k].upper()
        serial += 1
        evaluations += 1

    add_leaf((Fraction(-T), Fraction(T)), (Fraction(-T), Fraction(T)), 0, 0)
    domain_mass = cdf(A(T))-cdf(A(-T))
    tail = 1-domain_mass**2

    def current():
        raw = tuple(unit_interval(hull(total_lo[k], total_hi[k])+
                                  bounded_remainder(tail)) for k in range(3))
        return finalize(raw, spec)

    values, step = current(), 0
    adaptive_checkpoint(fp, label="P6/C4", step=step, action="initial",
                        evaluations=evaluations, leaf_boxes=len(leaves), values=values,
                        precision=precision, replay=replay)
    while (time.monotonic() < deadline and len(leaves) < max_leaves and
           None in (majority_winner(values[1]), majority_winner(values[2]),
                    majority_winner(values[0]))):
        while heap:
            _, key = heapq.heappop(heap)
            if key in leaves:
                break
        xiv, yiv, dx, dy, old = leaves.pop(key)
        for k in range(3):
            total_lo[k] -= old[k].lower()
            total_hi[k] -= old[k].upper()
        split_x = dx <= dy
        target = xiv if split_x else yiv
        for part in bisect(target):
            add_leaf(part if split_x else xiv, yiv if split_x else part,
                     dx+int(split_x), dy+int(not split_x))
        step += 1
        values = current()
        adaptive_checkpoint(fp, label="P6/C4", step=step,
                            action="split_x" if split_x else "split_y",
                            evaluations=evaluations, leaf_boxes=len(leaves), values=values,
                            precision=precision, replay=replay,
                            detail={"interval": [str(target[0]), str(target[1])]})
    return values, evaluations, len(leaves), time.monotonic() < deadline, step


def class_probs(g: arb) -> tuple[arb, arb, arb]:
    return A(Fraction(13, 20))*g, A(Fraction(1, 4))*g, 1-A(Fraction(9, 10))*g


def winner(ps: tuple[arb, arb, arb]) -> int | None:
    wins = [i for i in range(3)
            if all(i == j or ps[i].lower() > ps[j].upper() for j in range(3))]
    return wins[0] if len(wins) == 1 else None


def intersect(x: arb, y: arb) -> arb:
    lo, hi = max(x.lower(), y.lower()), min(x.upper(), y.upper())
    if lo > hi:
        raise ArithmeticError("population and conditional enclosures are disjoint")
    return hull(lo, hi)


def macro(ph: tuple[arb, ...], pm: tuple[arb, ...], q: arb,
          pred_h: int, pred_m: int) -> arb:
    pop = [q*ph[c]+(1-q)*pm[c] for c in range(3)]
    terms = []
    for c in range(3):
        tp = (q*ph[c] if pred_h == c else 0)+((1-q)*pm[c] if pred_m == c else 0)
        predicted = (q if pred_h == c else 0)+((1-q) if pred_m == c else 0)
        terms.append(2*tp/(2*tp+(predicted-tp)+(pop[c]-tp)))
    return sum(terms, arb(0))/3


def classify(values: tuple[arb, arb, arb], spec: dict) -> tuple[str, str, arb | None, tuple]:
    pg, gh, gm = values
    thresholds = (frozen(STATE_TEXT), frozen(LINKED_TEXT))
    q = qtail(thresholds[int(spec["h_kind"])])
    pg = intersect(pg, q*gh+(1-q)*gm)
    ph, pm, pp = class_probs(gh), class_probs(gm), class_probs(pg)
    wins = winner(ph), winner(pm), winner(pp)
    if None in wins:
        return "refused", "possible_interval_argmax_tie", None, wins
    if wins[0] == wins[1] == wins[2]:
        return "true_null", "algebraic_same_constant_classifier", arb(0), wins
    lift = macro(ph, pm, q, wins[0], wins[1])-macro(ph, pm, q, wins[2], wins[2])
    separated = lift.lower() > A(DELTA) or lift.upper() < -A(DELTA)
    if lift.rad() <= A(MAX_RADIUS) and separated:
        return "non_null", "strict_argmax_radius_and_zero_exclusion", lift, wins
    return "refused", "radius_or_zero_exclusion_failed", lift, wins


def overlap(a: arb, b: arb) -> bool:
    return max(a.lower(), b.lower()) <= min(a.upper(), b.upper())


def run(args: argparse.Namespace) -> dict:
    start = time.monotonic()
    deadline = start + args.runtime_cap
    kid, spec = select_spec(args.canary)
    label = "P5/C3" if args.canary == "c3" else "P6/C4"
    runner = c3_adaptive if args.canary == "c3" else c4_adaptive
    before = None
    if args.output.exists():
        try:
            prior = json.loads(args.output.read_text())
            if prior.get("canary") == label:
                before = (prior.get("enclosure_widths_before")
                          or prior.get("enclosure_widths"))
        except (OSError, ValueError):
            pass
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with args.checkpoint.open("w", encoding="utf-8") as fp:
        ctx.prec = args.precision_bits
        primary_run = runner(spec, args.boxes, deadline, fp,
                             args.precision_bits, False)
        primary, n_primary, leaf_primary, complete, subdivisions = primary_run[:5]
        partition = primary_run[5] if len(primary_run) > 5 else None
        classification, reason, lift, wins = classify(primary, spec)
        replay_result = {"required": classification != "refused", "performed": False,
                         "passed": None, "reason": "not_required"}
        n_replay = 0
        if classification != "refused":
            if time.monotonic() < deadline:
                replay_result["performed"] = True
                ctx.prec = max(320, args.precision_bits+64)
                replay_run = runner(spec, args.boxes, deadline, fp, ctx.prec, True)
                replay, n_replay, leaf_replay, replay_complete, replay_steps = replay_run[:5]
                rcls, rreason, rlift, rwins = classify(replay, spec)
                agree = rcls == classification and rwins == wins and all(
                    overlap(a, b) for a, b in zip(primary, replay))
                if lift is not None and rlift is not None:
                    agree = agree and overlap(lift, rlift)
                replay_result.update({"passed": agree, "classification": rcls,
                                      "reason": rreason, "complete": replay_complete,
                                      "precision_bits": ctx.prec,
                                      "leaf_boxes": leaf_replay,
                                      "subdivisions": replay_steps})
                if not agree:
                    classification, reason = "refused", "independent_replay_failed"
            else:
                replay_result["reason"] = "runtime_cap_before_required_replay"
                classification, reason = "refused", "independent_replay_not_completed"
        elapsed = time.monotonic()-start
    widths = {"population_pgf": enclosure(primary[0])["width_decimal"],
              "hit_pgf": enclosure(primary[1])["width_decimal"],
              "miss_pgf": enclosure(primary[2])["width_decimal"],
              "signed_lift": enclosure(lift)["width_decimal"] if lift is not None else None}
    evaluations = n_primary+n_replay
    payload = {
        "schema": "m3.2-categorical-arb-box-canary.v1",
        "canary": label, "kernel_id": kid, "spec": spec,
        "reduction": ("analytic Gaussian rectangle masses plus interval PGF; repeated-cluster power reduction"
                      if args.canary == "c3" else
                      "analytic Gaussian rectangle masses plus interval PGF; direct two-factor rectangle reduction"),
        "rng_constructed": False, "simulation_worlds_constructed": 0,
        "protocol_changed": False,
        "precision_bits": args.precision_bits, "subdivision_limit": args.boxes,
        "runtime_cap_seconds": args.runtime_cap, "runtime_seconds": elapsed,
        "evaluation_count": evaluations, "primary_evaluation_count": n_primary,
        "replay_evaluation_count": n_replay,
        "primary_complete": complete, "primary_leaf_boxes": leaf_primary,
        "primary_subdivisions": subdivisions,
        "enclosure_emitted": True,
        "enclosures": {"population_pgf": enclosure(primary[0]),
                       "hit_pgf": enclosure(primary[1]),
                       "miss_pgf": enclosure(primary[2]),
                       "signed_lift": enclosure(lift) if lift is not None else None},
        "enclosure_widths": widths,
        "enclosure_widths_before": before,
        "majority_boundary_separated": None not in wins,
        "strict_argmax": {"hit": wins[0], "miss": wins[1], "population": wins[2]},
        "classification": classification, "reason": reason,
        "acceptance": {"maximum_radius": "1e-12", "minimum_zero_exclusion": "1e-10",
                       "possible_ties_fail_closed": True, "strict_interval_argmax": True},
        "checkpoint_every_subdivision": True, "checkpoint_resume_supported": False,
        "replay": replay_result,
        "method_viable_for_remaining_27": (classification != "refused"
                                             and elapsed <= args.runtime_cap),
        "projected_27_kernel_seconds_no_sharing": elapsed*27/2,
    }
    if partition is not None:
        payload["adaptive_partition"] = partition
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("canary", choices=("c3", "c4"))
    parser.add_argument("--boxes", type=int, default=262144,
                        help="C3 maximum intervals per axis; C4 maximum leaf boxes")
    parser.add_argument("--precision-bits", type=int, default=224)
    parser.add_argument("--runtime-cap", type=float, default=285.0)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.boxes < 16 or args.precision_bits < 192 or not (0 < args.runtime_cap <= 300):
        raise SystemExit("fail closed: invalid canary configuration")
    payload = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
