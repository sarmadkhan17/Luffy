#!/usr/bin/env python3
"""Independent higher-precision replay of every categorical equivalence kernel."""
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
MANIFEST = HERE / "manifest.sha256"
T = 10
BITS = 224
STATE = "0.6744897501960817"
LINKED = "0.8416212335729143"
CAT = tuple(range(128)) + tuple(range(256, 384))
INJECTED = {
    4: {0, 33, 130, 163, 260, 293, 326, 359},
    5: {x for c in range(4) for x in (c, c+32, 128+c, 160+c, 256+c, 288+c, 320+c, 352+c)},
    6: set(range(8)) | set(range(136, 144)) | set(range(272, 280)) | set(range(344, 352)),
    7: set(range(24)) | set(range(136, 160)) | set(range(272, 288)) |
       set(range(288, 296)) | set(range(320, 336)) | set(range(344, 352)),
}
ODDS = {4: Fraction(2), 5: Fraction(2), 6: Fraction(2), 7: Fraction(3, 2)}


def A(x: Fraction | int) -> arb:
    x = Fraction(x)
    return arb(f"{x.numerator}/{x.denominator}")


def threshold(text: str) -> arb:
    n, d = float(text).as_integer_ratio()
    return A(Fraction(n, d))


def Phi(x: arb) -> arb:
    return (1+(x/A(2).sqrt()).erf())/2


def Q(x: arb) -> arb:
    return (x/A(2).sqrt()).erfc()/2


def p(f: arb, t: arb, sys: Fraction, res: Fraction) -> arb:
    return Phi((A(sys).sqrt()*f-t)/A(res).sqrt())


def grid(n: int) -> tuple[list[arb], list[arb]]:
    e = [A(Fraction(-T*n+2*T*i, n)) for i in range(n+1)]
    return ([arb((e[i]+e[i+1])/2, (e[i+1]-e[i])/2) for i in range(n)],
            [Phi(e[i+1])-Phi(e[i]) for i in range(n)])


def pad(x: arb, tail: arb) -> arb:
    u = tail.upper()
    return x + arb(u/2, u/2)


def product(ps: tuple[arb, arb], count: tuple[int, int], r: arb) -> arb:
    z = arb(1)
    for j in range(2):
        for _ in range(count[j]):
            z *= 1-(1-r)*ps[j]
    return z


def minus(count: tuple[int, int], j: int) -> tuple[int, int]:
    z = list(count)
    z[j] -= 1
    assert z[j] >= 0
    return z[0], z[1]


def one_dimensional(spec: dict, ts: tuple[arb, arb], n: int) -> tuple[arb, arb, arb]:
    cells, weights = grid(n)
    systematic = Fraction(3, 10) if spec["correlation"] == 1 else Fraction(7, 10)
    residual = 1-systematic
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    count = tuple(spec["target_counts"])
    out = [arb(0), arb(0), arb(0)]
    for f, w in zip(cells, weights):
        ps = (p(f, ts[0], systematic, residual), p(f, ts[1], systematic, residual))
        ph = ps[spec["h_kind"]]
        if spec["is_target"]:
            b = product(ps, minus(count, spec["h_kind"]), r)
            values = (b*(1-(1-r)*ph), ph*r*b, (1-ph)*b)
        else:
            b = product(ps, count, r)
            values = (b, ph*b, (1-ph)*b)
        for j in range(3):
            out[j] += w*values[j]
    tail = 2*Q(A(T))
    q = Q(ts[spec["h_kind"]])
    return pad(out[0], tail), pad(out[1], tail)/q, pad(out[2], tail)/(1-q)


def cluster_integral(g: arb, count: tuple[int, int], ts: tuple[arb, arb], r: arb,
                     cells: list[arb], weights: list[arb], mode: str,
                     hkind: int) -> arb:
    out = arb(0)
    for c, w in zip(cells, weights):
        mu = A(Fraction(1, 10)).sqrt()*g + A(Fraction(3, 5)).sqrt()*c
        ps = (Phi((mu-ts[0])/A(Fraction(3, 10)).sqrt()),
              Phi((mu-ts[1])/A(Fraction(3, 10)).sqrt()))
        ph = ps[hkind]
        if mode == "base":
            value = product(ps, count, r)
        elif mode == "target_hit":
            value = ph*r*product(ps, minus(count, hkind), r)
        elif mode == "target_miss":
            value = (1-ph)*product(ps, minus(count, hkind), r)
        elif mode == "nontarget_hit":
            value = ph*product(ps, count, r)
        else:
            value = (1-ph)*product(ps, count, r)
        out += w*value
    return pad(out, 2*Q(A(T)))


def nested(spec: dict, ts: tuple[arb, arb], n: int) -> tuple[arb, arb, arb]:
    cells, weights = grid(n)
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    counts = tuple(tuple(x) for x in spec["cluster_counts"])
    focal = spec["h_cluster"]
    out = [arb(0), arb(0), arb(0)]
    for g, w in zip(cells, weights):
        cache = {count: cluster_integral(g, count, ts, r, cells, weights, "base", 0)
                 for count in set(counts)}
        other = arb(1)
        for j, count in enumerate(counts):
            if j != focal:
                other *= cache[count]
        hmodes = ("target_hit", "target_miss") if spec["is_target"] else ("nontarget_hit", "nontarget_miss")
        values = (cache[counts[focal]],
                  cluster_integral(g, counts[focal], ts, r, cells, weights, hmodes[0], spec["h_kind"]),
                  cluster_integral(g, counts[focal], ts, r, cells, weights, hmodes[1], spec["h_kind"]))
        for j in range(3):
            out[j] += w*other*values[j]
    tail = 2*Q(A(T))
    q = Q(ts[spec["h_kind"]])
    return pad(out[0], tail), pad(out[1], tail)/q, pad(out[2], tail)/(1-q)


def two_dimensional(spec: dict, ts: tuple[arb, arb], n: int) -> tuple[arb, arb, arb]:
    cells, weights = grid(n)
    r = A(Fraction(spec["r_n"], spec["r_d"]))
    counts = tuple(tuple(x) for x in spec["sector_counts"])
    out = [arb(0), arb(0), arb(0)]
    for i, x in enumerate(cells):
        for j, y in enumerate(cells):
            fs = (x, -x/2+A(3).sqrt()*y/2)
            ps = tuple((p(f, ts[0], Fraction(3, 5), Fraction(2, 5)),
                        p(f, ts[1], Fraction(3, 5), Fraction(2, 5))) for f in fs)
            ph = ps[spec["h_sector"]][spec["h_kind"]]
            base = [product(ps[k], counts[k], r) for k in range(2)]
            population = base[0]*base[1]
            if spec["is_target"]:
                f = product(ps[spec["h_sector"]], minus(counts[spec["h_sector"]], spec["h_kind"]), r)
                o = base[1-spec["h_sector"]]
                values = (population, ph*r*f*o, (1-ph)*f*o)
            else:
                values = (population, ph*population, (1-ph)*population)
            w = weights[i]*weights[j]
            for k in range(3):
                out[k] += w*values[k]
    tail = 4*Q(A(T))
    q = Q(ts[spec["h_kind"]])
    return pad(out[0], tail), pad(out[1], tail)/q, pad(out[2], tail)/(1-q)


def hkind(h: int) -> int:
    return int(h >= 256)


def hcluster(h: int) -> int:
    return h % 32


def hsector(h: int) -> int:
    return int(hcluster(h) >= 16)


def comp(xs) -> tuple[int, int]:
    xs = tuple(xs)
    return sum(hkind(x) == 0 for x in xs), sum(hkind(x) == 1 for x in xs)


def independent_spec(s: int, c: int, h: int) -> dict:
    targets = tuple(sorted(INJECTED[s] - set(range(128, 256))))
    r = Fraction(10, 9+ODDS[s])
    z = {"scenario": s, "correlation": c, "h_kind": hkind(h), "is_target": h in targets,
         "r_n": r.numerator, "r_d": r.denominator}
    if c < 3:
        z["target_counts"] = comp(targets)
    elif c == 3:
        raw = tuple(comp(x for x in targets if hcluster(x) == k) for k in range(32))
        focal = hcluster(h)
        z["h_cluster"] = 0
        z["cluster_counts"] = (raw[focal],) + tuple(sorted(raw[:focal]+raw[focal+1:]))
    else:
        z["h_sector"] = hsector(h)
        z["sector_counts"] = tuple(comp(x for x in targets if hsector(x) == k) for k in range(2))
    return z


def kid(spec: dict) -> str:
    raw = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def endpoint(record: dict, which: str) -> arb:
    d = record[f"{which}_dyadic"]
    return arb((int(d["mantissa"]), int(d["exponent"])))


def overlaps(value: arb, record: dict) -> bool:
    return endpoint(record, "lower") <= value.upper() and endpoint(record, "upper") >= value.lower()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ctx.prec = BITS
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    expected_specs = {}
    expected_rows = []
    for s in range(4, 8):
        for c in range(1, 5):
            for h in CAT:
                spec = independent_spec(s, c, h)
                k = kid(spec)
                expected_specs[k] = spec
                expected_rows.append((f"P{s}/C{c}", h, k))
    actual_rows = [(r["cell"], r["hypothesis"], r["kernel_id"]) for r in source["row_assignments"]]
    canonical_expected = json.dumps(expected_specs, sort_keys=True, separators=(",", ":"))
    canonical_source = json.dumps({k: v["spec"] for k, v in source["kernels"].items()},
                                  sort_keys=True, separators=(",", ":"))
    mapping_match = actual_rows == expected_rows and canonical_expected == canonical_source
    ts = threshold(STATE), threshold(LINKED)
    started = time.perf_counter()
    results = {}
    verified = True
    for k, spec in sorted(expected_specs.items()):
        prior = source["kernels"][k]
        n = prior["partitions_per_axis"]
        values = (one_dimensional(spec, ts, n) if spec["correlation"] < 3 else
                  nested(spec, ts, n) if spec["correlation"] == 3 else
                  two_dimensional(spec, ts, n))
        margins = tuple(1-A(Fraction(31, 20))*x for x in values)
        signs = tuple(1 if x > 0 else -1 if x < 0 else 0 for x in margins)
        classification = "true_null" if signs[0] and len(set(signs)) == 1 else "unclassifiable"
        enclosures_overlap = all(overlaps(value, prior[name]) for value, name in
                                  zip(values, ("population_pgf", "hit_pgf", "miss_pgf")))
        conclusion_ok = prior["classification"] != "true_null" or classification == "true_null"
        verified &= enclosures_overlap and conclusion_ok
        results[k] = {"classification": classification, "majority_signs": signs,
                      "primary_replay_intervals_overlap": enclosures_overlap,
                      "primary_certified_conclusion_reproduced": conclusion_ok}
    result = {
        "schema": "m3.2-categorical-equivalence-replay.v1",
        "result": "PASS" if mapping_match and verified else "FAIL_CLOSED",
        "independent_implementation": True,
        "higher_precision": True,
        "precision_bits": BITS,
        "python_flint": flint.__version__, "flint": flint.__FLINT_VERSION__,
        "rng_constructed": False, "simulation_worlds_constructed": 0,
        "full_m32_validation_run": False,
        "kernels_replayed": len(results), "rows_remapped": len(expected_rows),
        "mapping_match": mapping_match, "kernel_results": results,
        "runtime_seconds": time.perf_counter()-started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "source_certificate_sha256": digest(SOURCE),
        "implementation_sha256": digest(Path(__file__)),
    }
    OUTPUT.write_text(json.dumps(result, sort_keys=True, separators=(",", ":"))+"\n", encoding="utf-8")
    files = ("certificate.json", "certify_categorical.py", "replay.json", "replay_categorical.py")
    MANIFEST.write_text("".join(f"{digest(HERE/name)}  {name}\n" for name in files), encoding="ascii")
    print(json.dumps({k: result[k] for k in ("result", "kernels_replayed", "rows_remapped", "mapping_match", "runtime_seconds", "peak_rss_kib")}, indent=2, sort_keys=True))
    if result["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
