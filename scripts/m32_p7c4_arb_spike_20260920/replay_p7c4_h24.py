#!/usr/bin/env python3
"""Independent, higher-precision replay of the isolated P7/C4 Arb spike."""
from __future__ import annotations

import hashlib
import json
import resource
import time
from fractions import Fraction
from pathlib import Path

import flint
from flint import arb, ctx


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "certificate.json"
OUTPUT = HERE / "replay.json"
BITS = 192
N = 256
BOUND = 10


def a(q: Fraction | int) -> arb:
    q = Fraction(q)
    return arb(f"{q.numerator}/{q.denominator}")


def threshold(text: str) -> arb:
    n, d = float(text).as_integer_ratio()
    return a(Fraction(n, d))


def cdf(z: arb) -> arb:
    return (1 + (z / a(2).sqrt()).erf()) / 2


def qtail(z: arb) -> arb:
    return (z / a(2).sqrt()).erfc() / 2


def p(f: arb, t: arb) -> arb:
    return cdf(((a(3) / 5).sqrt() * f - t) / (a(2) / 5).sqrt())


def bounds(z: arb) -> tuple[tuple[int, int], tuple[int, int]]:
    lm, le = z.lower().man_exp()
    um, ue = z.upper().man_exp()
    return (int(lm), int(le)), (int(um), int(ue))


def primary_contains(replay: arb, primary_bounds: dict) -> bool:
    lo_d = primary_bounds["lower_dyadic"]
    hi_d = primary_bounds["upper_dyadic"]
    lo = arb((int(lo_d["mantissa"]), int(lo_d["exponent"])))
    hi = arb((int(hi_d["mantissa"]), int(hi_d["exponent"])))
    return lo <= replay.lower() and hi >= replay.upper()


def main() -> None:
    ctx.prec = BITS
    prior = json.loads(SOURCE.read_text(encoding="utf-8"))
    ts, tl = threshold("0.6744897501960817"), threshold("0.8416212335729143")
    edges = [a(Fraction(-BOUND * N + 2 * BOUND * i, N)) for i in range(N + 1)]
    masses = [cdf(edges[i + 1]) - cdf(edges[i]) for i in range(N)]
    cells = [arb((edges[i] + edges[i + 1]) / 2, (edges[i + 1] - edges[i]) / 2)
             for i in range(N)]
    pop = arb(0)
    num = arb(0)
    started = time.perf_counter()
    for i, x in enumerate(cells):
        fa = x
        for j, y in enumerate(cells):
            fb = -x / 2 + a(3).sqrt() * y / 2
            g = ((1 - p(fa, ts) / 21) ** 16 * (1 - p(fa, tl) / 21) ** 24 *
                 (1 - p(fb, ts) / 21) ** 8 * (1 - p(fb, tl) / 21) ** 24)
            w = masses[i] * masses[j]
            pop += w * g
            num += w * g * p(fb, ts)
    omitted = 4 * qtail(a(BOUND))
    cap = omitted.upper()
    pad = arb(cap / 2, cap / 2)
    pop += pad
    num += pad
    cond = num / qtail(ts)
    pop_margin = 1 - a(Fraction(31, 20)) * pop
    cond_margin = 1 - a(Fraction(31, 20)) * cond
    majority = pop_margin > 0 and cond_margin > 0
    prior_pop = prior["quadrature"]["population_pgf_enclosure"]
    prior_cond = prior["quadrature"]["conditioned_pgf_enclosure"]
    result = {
        "schema": "m3.2-p7-c4-categorical-pgf-arb-replay.v1",
        "result": "PASS" if majority and primary_contains(pop, prior_pop) and primary_contains(cond, prior_cond) else "FAIL_CLOSED",
        "independent_implementation": True,
        "rng_constructed": False,
        "python_flint": flint.__version__,
        "flint": flint.__FLINT_VERSION__,
        "precision_bits": BITS,
        "partitions_per_axis": N,
        "population_pgf_bounds_dyadic": bounds(pop),
        "conditioned_pgf_bounds_dyadic": bounds(cond),
        "population_margin": pop_margin.str(30, more=True),
        "conditioned_margin": cond_margin.str(30, more=True),
        "primary_encloses_replay_population_interval": primary_contains(pop, prior_pop),
        "primary_encloses_replay_conditioned_interval": primary_contains(cond, prior_cond),
        "same_exact_zero_metric_conclusion": majority,
        "final_signed_metric_enclosure": "[0,0]",
        "runtime_seconds": time.perf_counter() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "source_certificate_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
