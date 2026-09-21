#!/usr/bin/env python3
"""Deterministic P7/C4 persistence truth certificate (no RNG, no worlds).

Reduction.  Every persistence hypothesis has the standard-normal membership
latent ``sqrt(3/5)*F + sqrt(2/5)*e`` with ``F`` the sector factor.  The two C4
sector factors are standard normal with correlation ``-1/2``; given both,
memberships are independent.  Each count-mass numerator is therefore
``sum_pairs c * E[f(U) g(V)]`` with ``f = p^a q^b`` of sector A's factor ``U``
and ``g`` likewise of sector B's factor ``V``.  Mehler's expansion gives
``E[f g] = sum_n rho^n alpha_n(f) alpha_n(g)`` with orthonormal Hermite
coefficients ``alpha_n``; Parseval + Cauchy-Schwarz bound the tail after
order ``N`` by ``|rho|^(N+1) * sqrt(E f^2 - sum_{n<=N} alpha_n^2) *
sqrt(E g^2 - sum_{n<=N} beta_n^2)``.  The 1-D Hermite coefficients are
enclosed by Arb's rigorous adaptive integrator; the omitted Gaussian tail
beyond ``|z| > B`` is bounded with Cramer's inequality
``|He_n(z)| <= K sqrt(n!) exp(z^2/4)``, ``K < 1.09``.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import resource
import time
from fractions import Fraction
from pathlib import Path

import flint
from flint import acb, arb, ctx


HERE = Path(__file__).resolve().parent
STATE_TEXT = "0.6744897501960817"
NULL_MAD_TEXT = "0.47472299235208826"
MAX_RADIUS = arb("1e-12")
DELTA_TRUTH = arb("1e-10")
CLASSES = ("A_target", "A_non_target", "B_target", "B_non_target")
NAMES = ("population",) + CLASSES
RHO = Fraction(-1, 2)
CRAMER_K = "1.09"
Pair = tuple[int, int]


def A(value: int | Fraction | str) -> arb:
    if isinstance(value, Fraction):
        return arb(f"{value.numerator}/{value.denominator}")
    return arb(value)


def binary64(text: str) -> tuple[arb, dict[str, str]]:
    value = float(text)
    n, d = value.as_integer_ratio()
    return A(Fraction(n, d)), {
        "decimal_text": text,
        "binary64_hex": value.hex(),
        "exact_binary64_rational": f"{n}/{d}",
    }


def qtail(z: arb) -> arb:
    return (z / A(2).sqrt()).erfc() / 2


def _arb_wire(value: arb) -> tuple[tuple[int, int], tuple[int, int]]:
    lm, le = value.lower().man_exp()
    um, ue = value.upper().man_exp()
    return (int(lm), int(le)), (int(um), int(ue))


def _arb_unwire(value) -> arb:
    lo, hi = arb(tuple(value[0])), arb(tuple(value[1]))
    return arb((lo + hi) / 2, (hi - lo) / 2)


def member_pairs() -> set[Pair]:
    """Every (a, b) exponent pair p^a q^b needed by the five numerators."""
    pairs: set[Pair] = set()
    for total in (8, 9, 16, 17):
        pairs |= {(a, total - a) for a in range(total + 1)}
    return pairs


def _alpha_task(task: tuple[Pair, int, int, str]) -> tuple[Pair, dict]:
    """Scaled Hermite coefficients alpha_n and E[F^2] for F = p^a q^b."""
    (a, b), bits, order, bound_text = task
    ctx.prec = bits
    bound = A(bound_text)
    threshold, _ = binary64(STATE_TEXT)
    loading = A(Fraction(3, 5)).sqrt()
    residual = A(Fraction(2, 5)).sqrt()
    sqrt2 = A(2).sqrt()
    two_pi = (2 * arb.pi()).sqrt()
    rel = A(2) ** (-(bits - 40))

    def p_of(z: acb) -> acb:
        return (1 + (((loading * z - threshold) / residual) / sqrt2).erf()) / 2

    def hermite(n: int, z: acb) -> acb:
        prev, cur = acb(1), z
        if n == 0:
            return prev
        for k in range(1, n):
            prev, cur = cur, z * cur - k * prev
        return cur

    def integral(fn, tol: arb) -> arb:
        value = acb.integral(fn, -bound, bound, abs_tol=tol, rel_tol=rel,
                             eval_limit=4_000_000, depth_limit=60, deg_limit=80)
        if not value.imag.contains(0):
            raise RuntimeError("fail closed: real Hermite integral has imaginary part")
        return value.real

    # |integral_{|z|>B} He_n phi F| / sqrt(n!) <= sqrt(2) K erfc(B/2)  (F in [0, 1])
    tau = A(2).sqrt() * A(CRAMER_K) * (bound / 2).erfc()
    tau_ball = arb(0, tau.upper())
    tol = A(2) ** (-(bits - 60))
    alphas = []
    for n in range(order + 1):
        scale = arb(math.factorial(n)).sqrt()

        def integrand(z: acb, _analytic: bool, n=n) -> acb:
            p = p_of(z)
            return (-z * z / 2).exp() / two_pi * hermite(n, z) * p ** a * (1 - p) ** b

        alphas.append(integral(integrand, tol * scale) / scale + tau_ball)

    def norm_integrand(z: acb, _analytic: bool) -> acb:
        p = p_of(z)
        return (-z * z / 2).exp() / two_pi * p ** (2 * a) * (1 - p) ** (2 * b)

    tail = 2 * qtail(bound)
    norm2 = integral(norm_integrand, tol) + arb(tail.upper() / 2, tail.upper() / 2)
    return (a, b), {"alpha": [_arb_wire(x) for x in alphas], "norm2": _arb_wire(norm2)}


def compute_hermite_data(bits: int, order: int, bound_text: str, workers: int,
                         reverse: bool, checkpoint_path: Path) -> dict[Pair, dict]:
    config = {"bits": bits, "order": order, "bound": bound_text,
              "schema": "p7c4-hermite-checkpoint.v1"}
    done: dict[str, dict] = {}
    if checkpoint_path.exists():
        prior = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if prior.get("config") == config:
            done = prior["pairs"]
    pairs = sorted(member_pairs(), reverse=reverse)
    tasks = [(pair, bits, order, bound_text) for pair in pairs if f"{pair[0]},{pair[1]}" not in done]
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_alpha_task, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            pair, wire = future.result()
            done[f"{pair[0]},{pair[1]}"] = wire
            tmp = checkpoint_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"config": config, "pairs": done}, sort_keys=True) + "\n",
                           encoding="utf-8")
            tmp.replace(checkpoint_path)
            print(f"hermite pair {len(done)}/{len(member_pairs())} {pair}", flush=True)
    out = {}
    for key, wire in done.items():
        a, b = (int(x) for x in key.split(","))
        alphas = [_arb_unwire(w) for w in wire["alpha"]]
        norm2 = _arb_unwire(wire["norm2"])
        sq = sum((x * x for x in alphas), arb(0))
        rem2 = norm2.upper() - sq.lower()
        remainder = (rem2 if rem2 > 0 else arb(0)).sqrt().upper()
        out[(a, b)] = {"alpha": alphas, "remainder": remainder}
    return out


def terms(name: str, k: int) -> list[tuple[int, Pair, Pair]]:
    """(coefficient, sector-A exponent pair, sector-B exponent pair) for count k."""
    out = []
    if name in ("population", "A_non_target", "B_non_target"):
        da = 1 if name == "A_non_target" else 0
        db = 1 if name == "B_non_target" else 0
        for ia in range(max(0, k - 16), min(8, k) + 1):
            ib = k - ia
            out.append((math.comb(8, ia) * math.comb(16, ib),
                        (ia + da, 8 - ia), (ib + db, 16 - ib)))
    elif name == "A_target" and k >= 1:
        for oa in range(max(0, k - 1 - 16), min(7, k - 1) + 1):
            ib = k - 1 - oa
            out.append((math.comb(7, oa) * math.comb(16, ib), (oa + 1, 7 - oa), (ib, 16 - ib)))
    elif name == "B_target" and k >= 1:
        for ia in range(max(0, k - 1 - 15), min(8, k - 1) + 1):
            ob = k - 1 - ia
            out.append((math.comb(8, ia) * math.comb(15, ob), (ia, 8 - ia), (ob + 1, 15 - ob)))
    return out


def count_masses(data: dict[Pair, dict], order: int, reverse: bool) -> dict[str, list[arb]]:
    rho = A(RHO)
    rho_pow = [rho ** n for n in range(order + 1)]
    rho_next = abs(RHO) ** (order + 1)
    indices = range(order, -1, -1) if reverse else range(order + 1)
    condition = qtail(binary64(STATE_TEXT)[0])
    result: dict[str, list[arb]] = {}
    for name in NAMES:
        masses = []
        for k in range(25):
            total = arb(0)
            for coef, fa, gb in terms(name, k):
                f, g = data[fa], data[gb]
                inner = sum((rho_pow[n] * f["alpha"][n] * g["alpha"][n] for n in indices), arb(0))
                bound = A(rho_next) * f["remainder"] * g["remainder"]
                total += coef * (inner + arb(0, bound.upper()))
            masses.append(total)
        if name != "population":
            masses = [m / condition for m in masses]
        result[name] = masses
    return result


def base_cdf(x: arb) -> arb:
    fv = A(Fraction(1, 2)) + (x.atan() + x / (1 + x * x)) / arb.pi()
    x2 = x * x
    fw = A(Fraction(1, 2)) + ((x / 3).atan() + 3 * x / (9 + x2)
                              + 12 * x / (9 + x2) ** 2) / arb.pi()
    return A(Fraction(9, 10)) * fv + A(Fraction(1, 10)) * fw


def outcome_cdf(x: arb, masses: list[arb], shift: arb) -> arb:
    return sum((mass * base_cdf(x - shift * k) for k, mass in enumerate(masses)), arb(0))


def bisect_root(fn, lo: Fraction, hi: Fraction, target: arb, steps: int = 200) -> tuple[arb, arb]:
    left, right = A(lo), A(hi)
    if not (fn(left).upper() < target.lower() and fn(right).lower() > target.upper()):
        raise RuntimeError("fail closed: root bracket endpoints are not certified")
    for _ in range(steps):
        mid = (left + right) / 2
        value = fn(mid)
        if value.upper() < target.lower():
            left = mid
        elif value.lower() > target.upper():
            right = mid
        else:
            break
    return left, right


def interval_from_bounds(lo: arb, hi: arb) -> arb:
    lower, upper = lo.lower(), hi.upper()
    return arb((lower + upper) / 2, (upper - lower) / 2)


def interval_record(value: arb) -> dict[str, object]:
    lm, le = value.lower().man_exp()
    um, ue = value.upper().man_exp()
    return {
        "lower_dyadic": {"mantissa": int(lm), "exponent": int(le)},
        "upper_dyadic": {"mantissa": int(um), "exponent": int(ue)},
        "decimal": value.str(40, more=True),
        "radius_decimal": value.rad().str(18, more=True, radius=False),
    }


def validate_masses(masses: dict[str, list[arb]]) -> dict[str, list[arb]]:
    """Intersect with [0, 1] (they are probabilities) and require normalization."""
    clean = {}
    for name, row in masses.items():
        fixed = []
        for m in row:
            lower, upper = max(m.lower(), arb(0)), min(m.upper(), arb(1))
            if lower > upper:
                raise RuntimeError(f"fail closed: mass enclosure outside [0,1] for {name}")
            fixed.append(arb((lower + upper) / 2, (upper - lower) / 2))
        total = sum(fixed, arb(0))
        if not total.contains(1):
            raise RuntimeError(f"fail closed: mass normalization excludes one for {name}: {total}")
        clean[name] = fixed
    mean = sum((k * m for k, m in enumerate(clean["population"])), arb(0))
    if not mean.contains(24 * qtail(binary64(STATE_TEXT)[0])):
        raise RuntimeError("fail closed: population mean count excludes 24*P(member)")
    return clean


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def certify(bits: int, order: int, bound_text: str, reverse: bool, workers: int,
            checkpoint_path: Path) -> dict[str, object]:
    started = time.perf_counter()
    ctx.prec = bits
    data = compute_hermite_data(bits, order, bound_text, workers, reverse, checkpoint_path)
    ctx.prec = bits
    distributions = validate_masses(count_masses(data, order, reverse))
    null_mad, mad_input = binary64(NULL_MAD_TEXT)
    _, threshold_input = binary64(STATE_TEXT)
    shift = null_mad / 4
    half = A(Fraction(1, 2))
    # Consistency of the frozen closed-form base CDF with the frozen N1 null MAD.
    mad_check = 2 * base_cdf(null_mad) - 1 - half
    mad_consistent = abs(mad_check.mid()) + mad_check.rad() <= arb("1e-15")
    median_records, medians = {}, {}
    for name, masses in distributions.items():
        lo, hi = bisect_root(lambda z, ms=masses: outcome_cdf(z, ms, shift),
                             Fraction(-8), Fraction(12), half)
        medians[name] = interval_from_bounds(lo, hi)
        median_records[name] = interval_record(medians[name])
    pop_median, pop_masses = medians["population"], distributions["population"]

    def central_mass(r: arb) -> arb:
        return outcome_cdf(pop_median + r, pop_masses, shift) - outcome_cdf(pop_median - r, pop_masses, shift)

    mad_lo, mad_hi = bisect_root(central_mass, Fraction(0), Fraction(8), half)
    population_mad = interval_from_bounds(mad_lo, mad_hi)
    effects, classifications, success = {}, {}, mad_consistent
    for name in CLASSES:
        effect = (medians[name] - pop_median) / (population_mad + 1)
        effects[name] = effect
        separated = effect.lower() > DELTA_TRUTH or effect.upper() < -DELTA_TRUTH
        if separated and effect.rad() <= MAX_RADIUS:
            classifications[name] = {"classification": "analytic_non_null",
                                     "sign": 1 if effect > 0 else -1}
        else:
            classifications[name] = {"classification": "unclassifiable", "sign": None}
            success = False
    return {
        "schema": "m3.2-p7-c4-persistence-certificate.v2",
        "status": "PASS" if success else "FAIL_CLOSED",
        "scope": "P7/C4 persistence only",
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "categorical_truth_modified": False,
        "protocol_modified": False,
        "target_count_structure": {"sector_A": 8, "sector_B": 16, "total": 24,
                                   "hypotheses": "136..159 (cluster 8..15 sector A, 16..31 sector B)"},
        "frozen_binary64_inputs": {"state_threshold": threshold_input, "N1_null_MAD": mad_input},
        "persistence_shift": interval_record(shift),
        "base_outcome_cdf": {
            "identity": "0.9*F_V(x)+0.1*F_(V+2U)(x), V,U iid density 2/(pi*(1+x^2)^2)",
            "F_V": "1/2+(atan(x)+x/(1+x^2))/pi",
            "F_V_plus_2U": "1/2+(atan(x/3)+3*x/(9+x^2)+12*x/(9+x^2)^2)/pi",
            "frozen_null_MAD_consistency_2F(m)-1-1/2": interval_record(mad_check),
            "frozen_null_MAD_consistent_to_1e-15": bool(mad_consistent),
        },
        "reduction": {"method": "Mehler-Hermite expansion of the rho=-1/2 sector-factor coupling",
                      "rho": "-1/2", "hermite_order": order, "gaussian_bound_B": bound_text,
                      "cramer_K": CRAMER_K,
                      "remainder": "|rho|^(N+1) sqrt(E f^2 - sum alpha^2) sqrt(E g^2 - sum beta^2)"},
        "arithmetic": {"python_flint": flint.__version__, "flint": flint.__FLINT_VERSION__,
                       "precision_bits": bits, "reverse_traversal": reverse,
                       "worker_processes": workers,
                       "directed_rounding": "Arb midpoint-radius balls"},
        "count_distributions": {name: [interval_record(x) for x in masses]
                                for name, masses in distributions.items()},
        "conditional_median_intervals": {name: median_records[name] for name in CLASSES},
        "population_median_interval": median_records["population"],
        "population_MAD_interval": interval_record(population_mad),
        "signed_persistence_effect_intervals": {name: interval_record(effects[name]) for name in CLASSES},
        "truth_classification": classifications,
        "maximum_final_radius": "1e-12",
        "delta_truth": "1e-10",
        "runtime_seconds": time.perf_counter() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bits", type=int, default=224)
    parser.add_argument("--order", type=int, default=48)
    parser.add_argument("--bound", default="16")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=HERE / "certificate.json")
    args = parser.parse_args()
    if args.bits < 160 or args.order < 16:
        raise SystemExit("fail closed: insufficient precision/order")
    checkpoint = args.checkpoint or (HERE / f"hermite_checkpoint_{args.bits}.json")
    result = certify(args.bits, args.order, args.bound, args.reverse, args.workers, checkpoint)
    result["implementation_sha256"] = sha256(Path(__file__))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "truth_classification", "runtime_seconds")},
                     indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
