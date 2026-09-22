#!/usr/bin/env python3
"""Rigorous batched Arb completion of the final 27 M3.2 categorical kernels.

No random object is constructed.  The program consumes the preceding frozen
certificates, selects exactly their remaining P4--P6/C3--C4 refusals, and
evaluates shared factorized Gaussian integrals with validated ``acb.integral``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import flint
from flint import acb, arb, ctx


HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "m32_categorical_equivalence_20260920" / "certificate.json"
PRIOR = HERE.parent / "m32_categorical_arb_completion_20260920" / "certificate.json"
SPEC = HERE.parent / "m32_categorical_equivalence_20260920" / "estimand_spec.json"
OUTPUT = HERE / "certificate.json"
ALLOWED_CELLS = {f"P{s}/C{c}" for s in (4, 5, 6) for c in (3, 4)}
T = 16
DELTA = Fraction(1, 10_000_000_000)
MAX_RADIUS = Fraction(1, 1_000_000_000_000)
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"


def A(x: Fraction | int | str) -> arb:
    if isinstance(x, str):
        return arb(x)
    x = Fraction(x)
    return arb(f"{x.numerator}/{x.denominator}")


def frozen(text: str) -> tuple[arb, dict[str, str]]:
    value = float(text)
    n, d = value.as_integer_ratio()
    return A(Fraction(n, d)), {
        "decimal_text": text,
        "binary64_hex": value.hex(),
        "exact_binary64_rational": f"{n}/{d}",
    }


def density(x: acb) -> acb:
    return (-x*x/2).exp()/(2*arb.pi()).sqrt()


def cdf(x: acb) -> acb:
    return (1+(x/A(2).sqrt()).erf())/2


def qtail(x: arb) -> arb:
    return (x/A(2).sqrt()).erfc()/2


def member(f: acb, threshold: arb, systematic: Fraction, residual: Fraction) -> acb:
    return cdf((A(systematic).sqrt()*f-threshold)/A(residual).sqrt())


def factor(ps: tuple[acb, acb], count: tuple[int, int], r: arb) -> acb:
    return (1-(1-r)*ps[0])**count[0] * (1-(1-r)*ps[1])**count[1]


def minus(count: tuple[int, int], which: int) -> tuple[int, int]:
    out = list(count)
    out[which] -= 1
    if out[which] < 0:
        raise ArithmeticError("focal target absent from factor composition")
    return out[0], out[1]


def integrate(fn, tol: arb, *, replay: bool = False) -> acb:
    return acb.integral(
        fn, -T, T, abs_tol=tol, rel_tol=tol,
        deg_limit=140,
        eval_limit=900000,
        depth_limit=800,
        use_heap=False,
    )


def pad_nonnegative(x: arb, bound: arb) -> arb:
    upper = bound.upper()
    return x + arb(upper/2, upper/2)


def interval(x: arb) -> dict[str, object]:
    lm, le = x.lower().man_exp()
    um, ue = x.upper().man_exp()
    return {
        "lower_dyadic": {"mantissa": int(lm), "exponent": int(le)},
        "upper_dyadic": {"mantissa": int(um), "exponent": int(ue)},
        "decimal": x.str(40, more=True),
        "radius_decimal": x.rad().str(20, more=True, radius=False),
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BatchIntegrator:
    """Scenario batches sharing population and conditional factor integrals."""

    def __init__(self, thresholds: tuple[arb, arb], tol: arb):
        self.ts = thresholds
        self.tol = tol
        self.memo: dict[tuple, acb] = {}
        self.calls = Counter()
        self.hits = Counter()

    def _inner_c3(self, g: acb, count: tuple[int, int], r: arb,
                  mode: str = "base", hkind: int = 0, target: bool = False) -> acb:
        if count == (0, 0):
            if mode == "base":
                return acb(1)
            if target:
                raise ArithmeticError("target in empty focal cluster")
            # Integrating the cluster factor analytically leaves variance 9/10.
            p = member(g, self.ts[hkind], Fraction(1, 10), Fraction(9, 10))
            return p if mode == "hit" else 1-p
        key = (tuple(tuple(map(int, p.man_exp())) for p in (g.real.lower(), g.real.upper(), g.imag.lower(), g.imag.upper())), count, str(r), mode, hkind, target)
        if key in self.memo:
            self.hits["c3_inner"] += 1
            return self.memo[key]
        self.calls["c3_inner"] += 1

        def fn(z: acb, _analytic: int) -> acb:
            f = A(Fraction(1, 10)).sqrt()*g + A(Fraction(3, 5)).sqrt()*z
            ps = (cdf((f-self.ts[0])/A(Fraction(3, 10)).sqrt()),
                  cdf((f-self.ts[1])/A(Fraction(3, 10)).sqrt()))
            base = factor(ps, count, r)
            if mode == "base":
                value = base
            else:
                ph = ps[hkind]
                if target:
                    focal = factor(ps, minus(count, hkind), r)
                    value = ph*r*focal if mode == "hit" else (1-ph)*focal
                else:
                    value = ph*base if mode == "hit" else (1-ph)*base
            return density(z)*value

        value = integrate(fn, self.tol)
        self.memo[key] = value
        return value

    def c3_batch(self, specs: dict[str, dict]) -> dict[str, tuple[arb, arb, arb, arb]]:
        by_scenario: dict[int, list[tuple[str, dict]]] = defaultdict(list)
        for kid, spec in specs.items():
            by_scenario[spec["scenario"]].append((kid, spec))
        out = {}
        tail = 66*qtail(A(T))  # outer 2Q plus 32 independent inner 2Q tails
        for scenario, items in sorted(by_scenario.items()):
            r = A(Fraction(items[0][1]["r_n"], items[0][1]["r_d"]))
            population_cache: dict[tuple, arb] = {}
            conditional_cache: dict[tuple, arb] = {}

            def raw(spec: dict, mode: str) -> arb:
                counts = tuple(tuple(x) for x in spec["cluster_counts"])
                signature = tuple(sorted(Counter(counts).items()))
                if mode == "base" and signature in population_cache:
                    self.hits["c3_population"] += 1
                    return population_cache[signature]
                ckey = (signature, counts[0], spec["h_kind"], spec["is_target"], mode)
                if mode != "base" and ckey in conditional_cache:
                    self.hits["c3_conditional"] += 1
                    return conditional_cache[ckey]

                def outer(g: acb, _analytic: int) -> acb:
                    groups = Counter(counts)
                    result = acb(1)
                    focal = counts[0]
                    for count, number in groups.items():
                        exponent = number-(1 if count == focal else 0)
                        if exponent:
                            result *= self._inner_c3(g, count, r)**exponent
                    result *= self._inner_c3(
                        g, focal, r, mode,
                        int(spec["h_kind"]), bool(spec["is_target"]),
                    )
                    return density(g)*result

                self.calls["c3_outer"] += 1
                value = integrate(outer, self.tol).real
                if mode == "base":
                    population_cache[signature] = value
                else:
                    conditional_cache[ckey] = value
                return value

            for kid, spec in sorted(items):
                pop = raw(spec, "base")
                hit = raw(spec, "hit")
                miss = raw(spec, "miss")
                q = qtail(self.ts[spec["h_kind"]])
                out[kid] = (pad_nonnegative(pop, tail),
                            pad_nonnegative(hit, tail)/q,
                            pad_nonnegative(miss, tail)/(1-q), tail)
        return out

    def _sector(self, f: acb, count: tuple[int, int], r: arb,
                mode: str = "base", hkind: int = 0, target: bool = False) -> acb:
        ps = (member(f, self.ts[0], Fraction(3, 5), Fraction(2, 5)),
              member(f, self.ts[1], Fraction(3, 5), Fraction(2, 5)))
        base = factor(ps, count, r)
        if mode == "base":
            return base
        ph = ps[hkind]
        if target:
            focal = factor(ps, minus(count, hkind), r)
            return ph*r*focal if mode == "hit" else (1-ph)*focal
        return ph*base if mode == "hit" else (1-ph)*base

    def c4_batch(self, specs: dict[str, dict]) -> dict[str, tuple[arb, arb, arb, arb]]:
        by_scenario: dict[int, list[tuple[str, dict]]] = defaultdict(list)
        for kid, spec in specs.items():
            by_scenario[spec["scenario"]].append((kid, spec))
        out = {}
        for scenario, items in sorted(by_scenario.items()):
            first = items[0][1]
            counts = tuple(tuple(x) for x in first["sector_counts"])
            r = A(Fraction(first["r_n"], first["r_d"]))
            cache: dict[tuple, arb] = {}

            def reduced(mode: str, sec: int, hkind: int, target: bool) -> tuple[arb, arb]:
                key = (mode, sec, hkind, target)
                if key in cache:
                    self.hits["c4_primitive"] += 1
                    tail = 2*qtail(A(T)) if counts[1] == (0, 0) else 4*qtail(A(T))
                    return cache[key], tail

                if counts[1] == (0, 0):
                    # P4/P5: integrate the empty opposite sector exactly.
                    def fn(x: acb, _analytic: int) -> acb:
                        base0 = self._sector(x, counts[0], r)
                        if mode == "base":
                            value = base0
                        elif sec == 0:
                            value = self._sector(x, counts[0], r, mode, hkind, target)
                        else:
                            if target:
                                raise ArithmeticError("target in empty sector")
                            # E_y membership(-x/2+sqrt(3)y/2): sys=3/20,res=17/20.
                            ph = member(-x, self.ts[hkind], Fraction(3, 20), Fraction(17, 20))
                            value = base0*(ph if mode == "hit" else 1-ph)
                        return density(x)*value
                    tail = 2*qtail(A(T))
                else:
                    def fn(x: acb, _analytic: int) -> acb:
                        left = self._sector(x, counts[0], r,
                                            mode if sec == 0 else "base", hkind, target)

                        def inner(y: acb, _inner_analytic: int) -> acb:
                            f = -x/2+A(3).sqrt()*y/2
                            right = self._sector(f, counts[1], r,
                                                 mode if sec == 1 else "base", hkind, target)
                            return density(y)*right
                        self.calls["c4_inner"] += 1
                        return density(x)*left*integrate(inner, self.tol)
                    tail = 4*qtail(A(T))
                self.calls["c4_outer"] += 1
                value = integrate(fn, self.tol).real
                cache[key] = value
                return value, tail

            for kid, spec in sorted(items):
                sec = int(spec["h_sector"])
                pop, tail = reduced("base", 0, 0, False)
                hit, _ = reduced("hit", sec, int(spec["h_kind"]), bool(spec["is_target"]))
                miss, _ = reduced("miss", sec, int(spec["h_kind"]), bool(spec["is_target"]))
                q = qtail(self.ts[spec["h_kind"]])
                out[kid] = (pad_nonnegative(pop, tail),
                            pad_nonnegative(hit, tail)/q,
                            pad_nonnegative(miss, tail)/(1-q), tail)
        return out


def class_probs(g: arb) -> tuple[arb, arb, arb]:
    return A(Fraction(13, 20))*g, A(Fraction(1, 4))*g, 1-A(Fraction(9, 10))*g


def winner(ps: tuple[arb, arb, arb]) -> int | None:
    found = [i for i in range(3)
             if all(i == j or ps[i].lower() > ps[j].upper() for j in range(3))]
    return found[0] if len(found) == 1 else None


def intersect(x: arb, y: arb) -> arb:
    lo = max(x.lower(), y.lower())
    hi = min(x.upper(), y.upper())
    if lo > hi:
        raise ArithmeticError("population and conditional enclosures are disjoint")
    return arb((lo+hi)/2, (hi-lo)/2)


def macro(ph: tuple[arb, ...], pm: tuple[arb, ...], q: arb,
          pred_h: int, pred_m: int) -> arb:
    pop = [q*ph[c]+(1-q)*pm[c] for c in range(3)]
    terms = []
    for c in range(3):
        tp = (q*ph[c] if pred_h == c else 0)+((1-q)*pm[c] if pred_m == c else 0)
        predicted = (q if pred_h == c else 0)+((1-q) if pred_m == c else 0)
        terms.append(2*tp/(2*tp+(predicted-tp)+(pop[c]-tp)))
    return sum(terms, arb(0))/3


def classify(values: tuple[arb, arb, arb, arb], spec: dict,
             thresholds: tuple[arb, arb]) -> tuple[str, int | None, arb | None, tuple, str]:
    pg, gh, gm, _tail = values
    q = qtail(thresholds[spec["h_kind"]])
    pg = intersect(pg, q*gh+(1-q)*gm)
    ph, pm, pp = class_probs(gh), class_probs(gm), class_probs(pg)
    wh, wm, wp = winner(ph), winner(pm), winner(pp)
    if None in (wh, wm, wp):
        return "refused", None, None, (wh, wm, wp), "possible_interval_argmax_tie"
    if wh == wm == wp:
        return "true_null", None, arb(0), (wh, wm, wp), "algebraic_same_constant_classifier"
    lift = macro(ph, pm, q, wh, wm)-macro(ph, pm, q, wp, wp)
    separated = lift.lower() > A(DELTA) or lift.upper() < -A(DELTA)
    if lift.rad() <= A(MAX_RADIUS) and separated:
        return "non_null", 1 if lift.lower() > 0 else -1, lift, (wh, wm, wp), "strict_argmax_radius_and_zero_exclusion"
    return "refused", None, lift, (wh, wm, wp), "radius_or_zero_exclusion_failed"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precision-bits", type=int, default=224)
    parser.add_argument("--abs-tol", default="2e-13")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.precision_bits < 192 or A(args.abs_tol) > A("2e-13"):
        raise SystemExit("fail closed: weak integration configuration")
    ctx.prec = args.precision_bits
    state, state_input = frozen(STATE_TEXT)
    linked, linked_input = frozen(LINKED_TEXT)
    thresholds = state, linked
    base = json.loads(BASE.read_text())
    prior = json.loads(PRIOR.read_text())
    row_cells = defaultdict(set)
    for row in base["row_assignments"]:
        row_cells[row["kernel_id"]].add(row["cell"])
    specs = {
        kid: rec["spec"] for kid, rec in prior["kernels"].items()
        if rec["classification"] == "refused" and row_cells[kid] <= ALLOWED_CELLS
    }
    if len(specs) != 27 or set().union(*(row_cells[k] for k in specs)) != ALLOWED_CELLS:
        raise SystemExit("fail closed: source does not contain exactly the expected 27 kernels")

    started = time.perf_counter()
    engine = BatchIntegrator(thresholds, A(args.abs_tol))
    values = {}
    values.update(engine.c3_batch({k: v for k, v in specs.items() if v["correlation"] == 3}))
    values.update(engine.c4_batch({k: v for k, v in specs.items() if v["correlation"] == 4}))
    kernels = {}
    for kid, spec in sorted(specs.items()):
        cls, sign, lift, wins, reason = classify(values[kid], spec, thresholds)
        kernels[kid] = {
            "spec": spec,
            "classification": cls,
            "sign": sign,
            "reason": reason,
            "strict_argmax": {"hit": wins[0], "miss": wins[1], "population": wins[2]},
            "population_pgf": interval(values[kid][0]),
            "hit_pgf": interval(values[kid][1]),
            "miss_pgf": interval(values[kid][2]),
            "gaussian_tail_bound": interval(values[kid][3]),
            "signed_lift_enclosure": interval(lift) if lift is not None else None,
            "proof_transcript": {
                "factorization": "C3 global-cluster nested factors" if spec["correlation"] == 3 else "C4 two-sector factors",
                "exact_null_basis": reason if cls == "true_null" else None,
                "non_null_checks": {"radius_at_most_1e-12": cls == "non_null",
                                    "zero_exclusion_strictly_beyond_1e-10": cls == "non_null"},
            },
        }

    accepted = {k for k, v in kernels.items() if v["classification"] != "refused"}
    rows = [r for r in base["row_assignments"] if r["kernel_id"] in specs]
    new_rows = [r for r in rows if r["kernel_id"] in accepted]
    remaining = sorted({r["cell"] for r in rows if r["kernel_id"] not in accepted})
    cells = sorted({r["cell"] for r in rows})
    new_cells = sorted(set(cells)-set(remaining))
    accepted_lifts = [v["signed_lift_enclosure"] for v in kernels.values()
                      if v["classification"] != "refused"]
    worst = max(accepted_lifts, key=lambda x: A(x["radius_decimal"])) if accepted_lifts else None
    elapsed = time.perf_counter()-started
    payload = {
        "schema": "m3.2-categorical-arb-batch-completion.v1",
        "status": "PASS" if not remaining else "PARTIAL_FAIL_CLOSED",
        "scope": "only final 27 categorical kernels in P4-P6/C3-C4; persistence excluded",
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "p7_c4_persistence_touched": False,
        "source_certificate_sha256": sha(PRIOR),
        "base_certificate_sha256": sha(BASE),
        "estimand_spec_sha256": sha(SPEC),
        "frozen_binary64_inputs": {"state_threshold": state_input, "linked_threshold": linked_input},
        "arithmetic": {"python_flint": flint.__version__, "flint": flint.__FLINT_VERSION__,
                       "precision_bits": args.precision_bits, "directed_rounding": "Arb midpoint-radius balls",
                       "method": "batched factorized adaptive validated Gauss-Legendre",
                       "absolute_tolerance": args.abs_tol, "relative_tolerance": args.abs_tol,
                       "truncation": T},
        "tail_proof": {"C3": "66*Q(16): 2Q outer plus union bound over 32 cluster 2Q tails",
                       "C4_nested": "4*Q(16): 2Q per factor",
                       "C4_reduced": "2*Q(16): opposite empty sector integrated analytically"},
        "batching": {"scenario_population_reused": True, "equivalent_conditionals_reused": True,
                     "zero_count_factor_integrals_algebraic": True,
                     "primitive_integral_calls": dict(engine.calls), "cache_hits": dict(engine.hits)},
        "acceptance": {"strict_interval_argmax": True, "possible_ties_fail_closed": True,
                       "maximum_radius": "1e-12", "minimum_zero_exclusion": "1e-10",
                       "exact_null": "algebraic same-constant-classifier identity only"},
        "counts": {"kernels_attempted": 27, "kernels_certified": len(accepted),
                   "kernels_refused": 27-len(accepted), "rows_attempted": len(rows),
                   "rows_newly_classified": len(new_rows),
                   "true_null_kernels": sum(v["classification"] == "true_null" for v in kernels.values()),
                   "non_null_kernels": sum(v["classification"] == "non_null" for v in kernels.values()),
                   "cells_newly_classified": len(new_cells)},
        "newly_classified_cells": new_cells,
        "remaining_unresolved_categorical_cells": remaining,
        "categorical_truth_complete": not remaining,
        "worst_accepted_signed_lift_enclosure": worst,
        "runtime_seconds": elapsed,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "kernels": kernels,
    }
    payload["implementation_sha256"] = sha(Path(__file__))
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    print(json.dumps({k: payload[k] for k in ("status", "counts", "newly_classified_cells",
                                               "remaining_unresolved_categorical_cells",
                                               "runtime_seconds", "peak_rss_kib")}, indent=2))
    if remaining:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
