#!/usr/bin/env python3
"""Independent deterministic verifier for the merged Scheme D pre-RNG truth package.

Standard library only; it does not import the generator or any trader module.
It re-derives every expected row from the raw frozen certificates, checks every
source file against both the package registry and its family's own freeze
manifest, and reports unresolved rows honestly.  A PASS means "the package is
exactly what the frozen evidence supports", not "the freeze is possible".
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PKG = HERE / "package"
V3 = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260920/certificates"
N = 384
MAJ, DELTA, RAD = Fraction(20, 31), Fraction(1, 10**10), Fraction(1, 10**12)
CATEGORICAL = tuple(range(128)) + tuple(range(256, 384))
PERSIST = frozenset(range(128, 256))
TARGETS = {
    1: {0}, 2: {128}, 3: {320}, 4: {0, 33, 130, 163, 260, 293, 326, 359},
    5: {x for c in range(4) for x in (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)},
    6: set(range(8)) | set(range(136, 144)) | set(range(272, 280)) | set(range(344, 352)),
    7: set(range(24)) | set(range(136, 160)) | set(range(272, 288)) | set(range(288, 296))
       | set(range(320, 336)) | set(range(344, 352)),
}
OR = {1: Fraction(3), 3: Fraction(3), 4: Fraction(2), 5: Fraction(2), 6: Fraction(2), 7: Fraction(3, 2)}
ROLE = {1: "calibration", 2: "power", 3: "calibration", 4: "power", 5: "power", 6: "power", 7: "sensitivity"}
D = {  # id -> (path, manifest path or None, manifest key/basename)
    "estimand_spec": ("scripts/m32_categorical_equivalence_20260920/estimand_spec.json",
                      "scripts/m32_p4_c3_c4_taylor_20260921/p4_c3_c4_freeze_manifest.json", "frozen_estimand_spec"),
    "base_certificate": ("scripts/m32_categorical_equivalence_20260920/certificate.json",
                         "scripts/m32_categorical_equivalence_20260920/manifest.sha256", "certificate.json"),
    "base_replay": ("scripts/m32_categorical_equivalence_20260920/replay.json",
                    "scripts/m32_categorical_equivalence_20260920/manifest.sha256", "replay.json"),
    "completion_certificate": ("scripts/m32_categorical_arb_completion_20260920/certificate.json",
                               "scripts/m32_categorical_arb_completion_20260920/manifest.sha256", "certificate.json"),
    "completion_replay": ("scripts/m32_categorical_arb_completion_20260920/replay.json",
                          "scripts/m32_categorical_arb_completion_20260920/manifest.sha256", "replay.json"),
    "p4_c3_primary": ("scripts/m32_p4_c3_c4_taylor_20260921/primary_c3.json",
                      "scripts/m32_p4_c3_c4_taylor_20260921/p4_c3_c4_freeze_manifest.json", "primary_c3"),
    "p4_c3_replay": ("scripts/m32_p4_c3_c4_taylor_20260921/replay_c3.json",
                     "scripts/m32_p4_c3_c4_taylor_20260921/p4_c3_c4_freeze_manifest.json", "replay_c3"),
    "p4_c4_primary": ("scripts/m32_p4_c3_c4_taylor_20260921/primary_c4.json",
                      "scripts/m32_p4_c3_c4_taylor_20260921/p4_c3_c4_freeze_manifest.json", "primary_c4"),
    "p4_c4_replay": ("scripts/m32_p4_c3_c4_taylor_20260921/replay_c4.json",
                     "scripts/m32_p4_c3_c4_taylor_20260921/p4_c3_c4_freeze_manifest.json", "replay_c4"),
    "p5_c3_primary": ("scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_primary.json",
                      "scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_freeze_manifest.json",
                      "p5_c3_hit_miss_primary.json"),
    "p5_c3_replay": ("scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_replay.json",
                     "scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_freeze_manifest.json",
                     "p5_c3_hit_miss_replay.json"),
    "p5_c3_certifier": ("scripts/m32_p5_c3_taylor_canary_20260920/certify_hit_miss_taylor.py",
                        "scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_freeze_manifest.json",
                        "certify_hit_miss_taylor.py"),
    "p5_c4_primary": ("scripts/m32_p5_c4_taylor_20260921/p5_c4_primary.json",
                      "scripts/m32_p5_c4_taylor_20260921/p5_c4_freeze_manifest.json", "primary_certificate"),
    "p5_c4_replay": ("scripts/m32_p5_c4_taylor_20260921/p5_c4_replay.json",
                     "scripts/m32_p5_c4_taylor_20260921/p5_c4_freeze_manifest.json", "replay_certificate"),
    "p6_c3_primary": ("scripts/m32_p6_c3_taylor_20260921/p6_c3_primary.json",
                      "scripts/m32_p6_c3_taylor_20260921/p6_c3_freeze_manifest.json", "p6_c3_primary.json"),
    "p6_c3_replay": ("scripts/m32_p6_c3_taylor_20260921/p6_c3_replay.json",
                     "scripts/m32_p6_c3_taylor_20260921/p6_c3_freeze_manifest.json", "p6_c3_replay.json"),
    "p6_c4_primary": ("scripts/m32_p6_c4_taylor_20260921/p6_c4_primary.json",
                      "scripts/m32_p6_c4_taylor_20260921/p6_c4_freeze_manifest.json", "p6_c4_primary.json"),
    "p6_c4_replay": ("scripts/m32_p6_c4_taylor_20260921/p6_c4_replay.json",
                     "scripts/m32_p6_c4_taylor_20260921/p6_c4_freeze_manifest.json", "p6_c4_replay.json"),
    "p7c4_persistence_primary": ("scripts/m32_p7c4_persistence_20260921/primary_certificate.json",
                                 "scripts/m32_p7c4_persistence_20260921/p7c4_persistence_freeze_manifest.json",
                                 "primary_certificate.json"),
    "p7c4_persistence_replay": ("scripts/m32_p7c4_persistence_20260921/replay_certificate.json",
                                "scripts/m32_p7c4_persistence_20260921/p7c4_persistence_freeze_manifest.json",
                                "replay_certificate.json"),
    "p7c4_persistence_replay_check": ("scripts/m32_p7c4_persistence_20260921/replay_result.json",
                                      "scripts/m32_p7c4_persistence_20260921/p7c4_persistence_freeze_manifest.json",
                                      "replay_result.json"),
    "remaining_primary": ("scripts/m32_p5c3_p6c4_remaining_20260921/primary.json", "scripts/m32_p5c3_p6c4_remaining_20260921/freeze_manifest.json", "primary.json"),
    "remaining_replay": ("scripts/m32_p5c3_p6c4_remaining_20260921/replay.json", "scripts/m32_p5c3_p6c4_remaining_20260921/freeze_manifest.json", "replay.json"),
    "remaining_replay_check": ("scripts/m32_p5c3_p6c4_remaining_20260921/replay_result.json", "scripts/m32_p5c3_p6c4_remaining_20260921/freeze_manifest.json",
                               "replay_result.json"),
    "remaining_certifier": ("scripts/m32_p5c3_p6c4_remaining_20260921/certify_remaining.py", "scripts/m32_p5c3_p6c4_remaining_20260921/freeze_manifest.json",
                            "certify_remaining.py"),
}


class Fail(AssertionError):
    pass


def need(condition: bool, message: str) -> None:
    if not condition:
        raise Fail(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jload(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def frac(text: str) -> Fraction:
    n, _, d = text.partition("/")
    return Fraction(int(n), int(d or 1))


def fs(x: Fraction) -> str:
    return f"{x.numerator}/{x.denominator}"


# ----------------------------------------------------------------- manifest lookup
def manifest_hash(manifest: str, key: str) -> str | None:
    path = ROOT / manifest
    if manifest.endswith(".sha256"):
        for line in path.read_text().splitlines():
            h, name = line.split("  ", 1)
            if os.path.basename(name) == key:
                return h
        return None

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("path"), str) and os.path.basename(o["path"]) == key and "sha256" in o:
                return o["sha256"]
            for k, v in o.items():
                if k == key and isinstance(v, str):
                    return v
                got = walk(v)
                if got:
                    return got
        elif isinstance(o, list):
            for v in o:
                got = walk(v)
                if got:
                    return got
        return None

    return walk(json.loads(path.read_text()))


# ----------------------------------------------------------------- independent model
def rho(c: int, i: int, j: int) -> Fraction:
    """Correlation from the factor form  sum_k w_k s_ik s_jk  (i != j)."""
    if c == 0:
        return Fraction(0)
    if c in (1, 2):
        return {1: Fraction(3, 10), 2: Fraction(7, 10)}[c]
    if c == 3:
        return Fraction(1, 10) * 1 * 1 + Fraction(3, 5) * (1 if i % 32 == j % 32 else 0)
    si, sj = (1 if i % 32 < 16 else -1), (1 if j % 32 < 16 else -1)
    return Fraction(3, 20) * 1 * 1 + Fraction(9, 20) * si * sj


def spec_key(s: int, c: int, h: int) -> tuple[dict, str]:
    tg = sorted(x for x in TARGETS[s] if x not in PERSIST)
    comp = lambda xs: [sum(1 for x in xs if x < 256), sum(1 for x in xs if x >= 256)]
    r = Fraction(10) / (9 + OR[s])
    spec = {"scenario": s, "correlation": c, "h_kind": 0 if h < 256 else 1, "is_target": h in tg,
            "r_n": r.numerator, "r_d": r.denominator}
    if c in (1, 2):
        spec["target_counts"] = comp(tg)
    elif c == 3:
        raw = [comp([x for x in tg if x % 32 == k]) for k in range(32)]
        f = h % 32
        spec["h_cluster"] = 0
        spec["cluster_counts"] = [raw[f]] + sorted(raw[:f] + raw[f + 1:])
    else:
        spec["h_sector"] = 0 if h % 32 < 16 else 1
        spec["sector_counts"] = [comp([x for x in tg if (x % 32 < 16) == (k == 0)]) for k in range(2)]
    text = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return spec, hashlib.sha256(text.encode()).hexdigest()[:20]


def dy(d: dict) -> Fraction:
    return Fraction(int(d["mantissa"])) * Fraction(2) ** int(d["exponent"])


def ival(rec: dict) -> tuple[Fraction, Fraction]:
    return dy(rec["lower_dyadic"]), dy(rec["upper_dyadic"])


def cls_of(rec: dict):
    lo, hi = ival(rec)
    return 0 if lo > MAJ else 2 if hi < MAJ else None


def touch(a: dict, b: dict) -> bool:
    (al, ah), (bl, bh) = ival(a), ival(b)
    return max(al, bl) <= min(ah, bh)


def verdict(p: dict, r: dict, names, lift):
    """(classification, sign, triple) or None, from primary + replay records."""
    tp = [cls_of(p[n]) for n in names]
    tr = [cls_of(r[n]) for n in names]
    if None in tp or tp != tr or not all(touch(p[n], r[n]) for n in names):
        return None
    ls = [x.get(lift) for x in (p, r)] if lift else [None, None]
    if len(set(tp)) == 1:
        if any(l is not None and ival(l) != (0, 0) for l in ls):
            return None
        return "true_null", None, tp
    if None in ls or not touch(*ls):
        return None
    sg = set()
    for lo, hi in map(ival, ls):
        if (hi - lo) / 2 > RAD:
            return None
        sg.add(1 if lo > DELTA else -1 if hi < -DELTA else 0)
    return ("analytic_non_null", sg.pop(), tp) if len(sg) == 1 and 0 not in sg else None


def ball(text: str):
    mid, _, rad = text.strip("[]").partition(" +/- ")
    return Fraction(Decimal(mid)), Fraction(Decimal(rad))


def raw_evidence() -> dict[str, tuple]:
    """kernel_id -> (classification, sign, triple, source id) from the raw certificates."""
    ev: dict[str, tuple] = {}

    def put(kid, src, v):
        if v is None:
            return
        need(kid not in ev or ev[kid][:2] == v[:2], f"conflicting evidence {kid}")
        ev.setdefault(kid, (v[0], v[1], v[2], src))

    ids = {}
    for s in range(4, 8):
        for c in range(1, 5):
            for h in CATEGORICAL:
                sp, k = spec_key(s, c, h)
                ids[k] = sp
    b, br = jload(D["base_certificate"][0]), jload(D["base_replay"][0])
    need(br["result"] == "PASS" and br["mapping_match"] is True, "base replay not PASS")
    for k, rec in b["kernels"].items():
        if rec["classification"] != "true_null":
            continue
        sg = [(1 if ival(rec["majority_margins"][n])[0] > 0 else -1 if ival(rec["majority_margins"][n])[1] < 0 else 0)
              for n in ("population", "hit", "miss")]
        rr = br["kernel_results"][k]
        if len(set(sg)) == 1 and sg[0] and rr["classification"] == "true_null" and rr["majority_signs"] == sg \
                and rr["primary_certified_conclusion_reproduced"] and rr["primary_replay_intervals_overlap"]:
            put(k, "base_certificate", ("true_null", None, [2 if sg[0] > 0 else 0] * 3))
    c, cr = jload(D["completion_certificate"][0]), jload(D["completion_replay"][0])
    need(cr["result"] == "PASS", "completion replay not PASS")
    rows = {x["kernel_id"]: x for x in cr["rows"]}
    for k, rec in c["kernels"].items():
        if rec["classification"] != "non_null" or k not in rows or rows[k]["result"] != "PASS":
            continue
        tp = [cls_of(rec[n]) for n in ("hit_pgf", "miss_pgf", "population_pgf")]
        lo, hi = ival(rec["signed_lift_enclosure"])
        mid, rad = ball(rows[k]["signed_lift_enclosure"])
        sg = 1 if lo > DELTA else -1 if hi < -DELTA else 0
        if None not in tp and len(set(tp)) > 1 and tp == rows[k]["argmax"] and sg and (hi - lo) / 2 <= RAD \
                and max(lo, mid - rad) <= min(hi, mid + rad) and rows[k]["overlaps_primary_enclosure"]:
            put(k, "completion_certificate", ("analytic_non_null", sg, tp))
    for cc in (3, 4):
        p, r = jload(D[f"p4_c{cc}_primary"][0]), jload(D[f"p4_c{cc}_replay"][0])
        need(p["kernel_id"] == r["kernel_id"], "p4 kernel mismatch")
        need(any(spec_key(4, cc, h)[1] == p["kernel_id"] for h in CATEGORICAL), "p4 kernel not in cell")
        if p["status"] == r["status"] == "certified" and p["rng_used"] is False and r["rng_used"] is False:
            put(p["kernel_id"], f"p4_c{cc}_primary",
                verdict(p, r, ("conditional_hit_interval", "conditional_miss_interval", "population_interval"),
                        "signed_lift_interval"))
    for tag in ("p5_c4", "p6_c3"):
        p, r = jload(D[f"{tag}_primary"][0]), jload(D[f"{tag}_replay"][0])
        for k, pk in p["kernels"].items():
            rk = r["kernels"].get(k)
            need(rk is not None and pk["spec"] == ids[k] == rk["spec"], f"{tag} spec/id mismatch {k}")
            if pk["status"] == rk["status"] == "certified":
                put(k, f"{tag}_primary",
                    verdict(pk, rk, ("conditional_hit_interval", "conditional_miss_interval", "population_interval"),
                            "signed_lift_interval"))
    p, r, chk = jload(D["remaining_primary"][0]), jload(D["remaining_replay"][0]), jload(D["remaining_replay_check"][0])
    need(chk["result"] == "PASS" and chk["kernels_checked"] == 9 and set(p["kernels"]) == set(r["kernels"]),
         "remaining replay check")
    need(p["rng_used"] is False and r["rng_used"] is False and p["search_performed"] is False, "remaining flags")
    for k, pk in p["kernels"].items():
        rk = r["kernels"][k]
        need(pk["spec"] == ids[k] == rk["spec"], f"remaining spec/id mismatch {k}")
        if pk["status"] == rk["status"] == "certified":
            put(k, "remaining_primary",
                verdict(pk, rk, ("conditional_hit_interval", "conditional_miss_interval", "population_interval"),
                        "signed_lift_interval"))
    p, r = jload(D["p6_c4_primary"][0]), jload(D["p6_c4_replay"][0])
    kid = hashlib.sha256(json.dumps(p["spec"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    need(kid in ids and ids[kid] == p["spec"] == r["spec"], "p6 c4 canary spec not a known kernel")
    if p["accepted"] and r["accepted"]:
        put(kid, "p6_c4_primary", verdict(p, r, ("hit", "miss", "population"), "macro_f1_lift"))
    p, r = jload(D["p5_c3_primary"][0]), jload(D["p5_c3_replay"][0])
    text = (ROOT / D["p5_c3_certifier"][0]).read_text()
    cand = [k for k, sp in ids.items() if sp["scenario"] == 5 and sp["correlation"] == 3 and sp["is_target"]
            and sp["h_kind"] == 1 and sp["cluster_counts"][0] == [2, 4]]
    need(len(cand) == 1, "p5 c3 structural kernel not unique")
    need("return d0**2*d1**4, self.r*ps[1]*other, (1-ps[1])*other" in text and "other = d0**2*d1**3" in text
         and "p**4, h*p**3, m*p**3" in text and (ids[cand[0]]["r_n"], ids[cand[0]]["r_d"]) == (10, 11),
         "p5 c3 certifier constants do not match the structural kernel")
    if p["accepted"] and r["accepted"]:
        put(cand[0], "p5_c3_primary", verdict(p, r, ("hit", "miss", "population"), "macro_f1_lift"))
    return ev


def p7c4_expected() -> dict[str, tuple]:
    p, r = jload(D["p7c4_persistence_primary"][0]), jload(D["p7c4_persistence_replay"][0])
    chk = jload(D["p7c4_persistence_replay_check"][0])
    need(p["status"] == r["status"] == chk["result"] == "PASS", "p7c4 persistence not PASS")
    need(p["target_count_structure"] == {**p["target_count_structure"], "sector_A": 8, "sector_B": 16, "total": 24},
         "p7c4 target structure")
    out = {}
    for name in ("A_target", "A_non_target", "B_target", "B_non_target"):
        ep, er = p["signed_persistence_effect_intervals"][name], r["signed_persistence_effect_intervals"][name]
        (lo, hi), (rl, rh) = ival(ep), ival(er)
        need(lo > DELTA and rl > DELTA and (hi - lo) / 2 <= RAD and (rh - rl) / 2 <= RAD and touch(ep, er),
             f"p7c4 persistence {name} rejected")
        out[name] = (1, fs(lo), fs((hi - lo) / 2))
    return out


# ----------------------------------------------------------------- exact C0
def convolve(ps):
    out = [Fraction(1)]
    for p in ps:
        nxt = [Fraction(0)] * (len(out) + 1)
        for k, m in enumerate(out):
            nxt[k] += m * (1 - p)
            nxt[k + 1] += m * p
        out = nxt
    return out


def probs(ps, odds, forced):
    out = [Fraction(0)] * 3
    for k, m in enumerate(convolve(ps)):
        a = (Fraction(10) / (9 + odds)) ** (k + forced)
        for i, v in enumerate((Fraction(13, 20) * a, Fraction(1, 4) * a, 1 - Fraction(9, 10) * a)):
            out[i] += m * v
    return out


def c0_witness_ok(s: int, h: int, w: dict) -> None:
    tg = sorted(x for x in TARGETS[s] if x not in PERSIST)
    q = Fraction(1, 4) if h < 256 else Fraction(1, 5)
    rest = [Fraction(1, 4) if x < 256 else Fraction(1, 5) for x in tg if x != h]
    hit, miss = probs(rest, OR[s], 1), probs(rest, OR[s], 0)
    pop = [q * hit[i] + (1 - q) * miss[i] for i in range(3)]
    maj = [max(range(3), key=lambda i: v[i]) for v in (hit, miss, pop)]
    need(len(set(maj)) == 1 and w["majorities_hit_miss_population"] == maj, f"C0 majority P{s} h{h}")
    need([frac(x) for x in w["conditional_hit_class_probabilities"]] == hit
         and [frac(x) for x in w["conditional_miss_class_probabilities"]] == miss
         and [frac(x) for x in w["population_class_probabilities"]] == pop
         and frac(w["macro_f1_lift"]) == 0 and w["odds_ratio"] == fs(OR[s]), f"C0 witness P{s} h{h}")


# ----------------------------------------------------------------- main
def expected_persistence(s, c, h, p7c4):
    tg = sorted(x for x in TARGETS[s] if x in PERSIST)
    if not tg:
        return "true_null", None, "OUTCOME_COLUMN_INDEPENDENCE"
    if c == 0 and h not in tg:
        return "true_null", None, "C0_NON_TARGET_INDEPENDENCE"
    if s == 7 and c == 4:
        cls = ("A" if h % 32 < 16 else "B") + ("_target" if h in TARGETS[7] else "_non_target")
        return "analytic_non_null", p7c4[cls][0], "P7_C4_PERSISTENCE_ARB_INTERVAL"
    rs = [rho(c, h, t) for t in tg if t != h]
    need(all(abs(x) < 1 for x in rs), "rho out of range")
    if all(x >= 0 for x in rs) and (h in tg or any(x > 0 for x in rs)):
        return "analytic_non_null", 1, "PERSISTENCE_GAUSSIAN_COUPLING_SIGN_V1"
    if all(x <= 0 for x in rs) and h not in tg and any(x < 0 for x in rs):
        return "analytic_non_null", -1, "PERSISTENCE_GAUSSIAN_COUPLING_SIGN_V1"
    return "unclassifiable", None, "PERSISTENCE_GAUSSIAN_COUPLING_PREMISE_FAILED"


def main() -> int:
    cert = json.loads((PKG / "proof_certificate.json").read_text())
    need(cert["rng_constructed"] is False and cert["simulation_worlds_constructed"] == 0
         and cert["optimization_performed"] is False, "certificate flags")
    # manifest and table hashes
    listed = {}
    for line in (PKG / "manifest.sha256").read_text().splitlines():
        h, name = line.split("  ", 1)
        need(digest(PKG / name) == h, f"manifest mismatch {name}")
        listed[name] = h
    need(len(listed) == 56 and listed["proof_certificate.json"] == digest(PKG / "proof_certificate.json"), "manifest size")
    need({k: listed[k] for k in cert["table_sha256"]} == cert["table_sha256"] and len(cert["table_sha256"]) == 55,
         "table hash registry")
    for name in ("generate_truth_package.py", "verify_truth_package.py"):
        need(cert["implementation_sha256"][name] == digest(HERE / name), f"implementation drift {name}")
    # sources: registry hash == file hash == family manifest hash
    need(set(cert["sources"]) == set(D), "source registry differs")
    for sid, (path, manifest, key) in D.items():
        need(cert["sources"][sid]["path"] == path and digest(ROOT / path) == cert["sources"][sid]["sha256"],
             f"source drift {sid}")
        mh = manifest_hash(manifest, key)
        need(mh == cert["sources"][sid]["sha256"], f"source not matching its family manifest: {sid}")
    # foundations
    fnd = cert["foundations"]
    for name, (weights, residual) in {"C1": (["3/10"], "7/10"), "C2": (["7/10"], "3/10"),
                                     "C3": (["1/10", "3/5"], "3/10"), "C4": (["3/20", "9/20"], "2/5")}.items():
        need(fnd[name]["weights"] == weights and fnd[name]["residual"] == residual, f"foundation {name}")
        need(all(frac(w) >= 0 for w in weights) and frac(residual) > 0
             and sum((frac(w) for w in weights), Fraction(0)) + frac(residual) == 1, f"PD/unit variance {name}")
    need(rho(3, 128, 160) == Fraction(7, 10) and rho(3, 128, 129) == Fraction(1, 10)
         and rho(4, 128, 130) == Fraction(3, 5) and rho(4, 128, 144) == Fraction(-3, 10)
         and rho(1, 1, 2) == Fraction(3, 10) and rho(2, 1, 2) == Fraction(7, 10), "closed-form rho")
    need(min(abs(rho(c, 128, j)) for c in (1, 2, 3, 4) for j in (129, 144, 160)) >= Fraction(1, 10), "rho margin")

    # foundations against the frozen generator source (text match, no import) and the v3 frozen inputs
    v3 = json.loads((V3 / "proof_certificate.json").read_text())
    for rel, h in v3["frozen_inputs"]["source_sha256"].items():
        need(digest(ROOT / rel) == h, f"frozen source drift {rel}")
    need({int(k[1:]): set(v) for k, v in v3["frozen_inputs"]["injections"].items()} == TARGETS,
         "injection sets differ from the frozen inputs")
    gen = (ROOT / "trader/cognition/m32_scheme_d_validation.py").read_text()
    for snippet in (
            "rho = 0.30 if correlation == 1 else 0.70",
            "return math.sqrt(rho) * f + math.sqrt(1.0 - rho) * e",
            "return (math.sqrt(0.10) * g + math.sqrt(0.60) * f[:, clusters]",
            "+ math.sqrt(0.30) * e)",
            "clusters = np.arange(HYPOTHESES) % 32",
            "loadings[:, 0] = 0.5",
            "loadings[:, 1] = np.where(np.arange(HYPOTHESES) % 32 < 16,",
            "math.sqrt(3.0) / 2.0, -math.sqrt(3.0) / 2.0)",
            "return math.sqrt(0.60) * (f @ loadings.T) + math.sqrt(0.40) * e",
            "memberships = latents + shifts[:, None] > THRESHOLDS[None, :]",
            "persistence[matched] += PERSISTENCE_SHIFT_BY_SCENARIO[scenario] * N1_NULL_MAD",
            "PERSISTENCE_SHIFT_BY_SCENARIO = {102: 1.0, 104: 0.5, 105: 0.5,",
            "106: 0.5, 107: 0.25}",
            '"A" if cluster < 16 else "B"'):
        need(snippet in gen, f"generator source no longer matches foundation: {snippet}")
    need(all(digest(ROOT / rel) == v3["frozen_inputs"]["source_sha256"][rel] for rel in
             ("trader/cognition/m32_scheme_d_validation.py",)), "validation source hash")

    evidence = raw_evidence()
    p7c4 = p7c4_expected()
    counts = Counter()
    unresolved_by_cell = {}
    used_sources = Counter()
    checked = 0
    cells = []
    for scope, ids in (("N", range(4)), ("P", range(1, 8))):
        for s in ids:
            for c in range(5):
                name = f"{scope}{s}_C{c}"
                t = json.loads((PKG / "tables" / f"{name}.json").read_text())
                role = "null" if scope == "N" else ROLE[s]
                need(t["cell"] == f"{scope}{s}/C{c}" and t["role"] == role and t["hypotheses"] == N
                     and [r["hypothesis"] for r in t["records"]] == list(range(N)), f"table shape {name}")
                unresolved = []
                for h, row in enumerate(t["records"]):
                    lab = "persistence" if h in PERSIST else ("case_kind" if h < 128 else "continuation")
                    need(row["label"] == lab and row["cluster"] == h % 32
                         and row["sector"] == ("A" if h % 32 < 16 else "B"), f"row meta {name}/{h}")
                    w = row["witness"]
                    if scope == "N":
                        exp = ("true_null", None, "NULL_CELL_TRUE_NULL_BY_CONSTRUCTION")
                    elif lab == "persistence":
                        exp = expected_persistence(s, c, h, p7c4)
                        tg = sorted(x for x in TARGETS[s] if x in PERSIST)
                        if exp[2] == "PERSISTENCE_GAUSSIAN_COUPLING_SIGN_V1":
                            rs = Counter(fs(rho(c, h, x)) for x in tg if x != h)
                            need(w["targets"] == tg and w["self_target"] == (h in tg)
                                 and w["rho_counts"] == {k: rs[k] for k in sorted(rs)}
                                 and w["max_abs_rho"] == fs(max((abs(rho(c, h, x)) for x in tg if x != h),
                                                                default=Fraction(0)))
                                 and w["strict_witness"] == ("self_target" if h in tg else "nonzero_rho")
                                 and w["shift_positive"] is True and w["base_density_everywhere_positive"] is True,
                                 f"theorem premises {name}/{h}")
                        elif exp[2] == "P7_C4_PERSISTENCE_ARB_INTERVAL":
                            cl = ("A" if h % 32 < 16 else "B") + ("_target" if h in TARGETS[7] else "_non_target")
                            need(w["class"] == cl and w["effect_lower_bound"] == p7c4[cl][1]
                                 and w["effect_radius"] == p7c4[cl][2], f"p7c4 witness {name}/{h}")
                    elif s in (1, 3):
                        exp = ("true_null", None, "P1_P3_SINGLE_OR_MAJORITY")
                    elif s == 2:
                        exp = ("true_null", None, "NO_CATEGORICAL_INJECTION")
                    elif c == 0:
                        if h in TARGETS[s]:
                            exp = ("true_null", None, "C0_TARGET_EXACT_RATIONAL_MAJORITY")
                            c0_witness_ok(s, h, w)
                        else:
                            exp = ("true_null", None, "C0_NON_TARGET_INDEPENDENCE")
                    else:
                        _, kid = spec_key(s, c, h)
                        need(w["kernel_id"] == kid, f"kernel id {name}/{h}")
                        ev = evidence.get(kid)
                        if ev is None:
                            exp = ("unclassifiable", None, "CATEGORICAL_KERNEL_UNCERTIFIED")
                        else:
                            exp = (ev[0], ev[1], "CATEGORICAL_KERNEL_COMMON_MAJORITY_NULL" if ev[0] == "true_null"
                                   else "CATEGORICAL_KERNEL_SIGNED_LIFT")
                            need(w["source"] == ev[3] and w["hit_miss_population_classes"] == ev[2],
                                 f"evidence witness {name}/{h}")
                            used_sources[ev[3]] += 1
                    need((row["classification"], row["sign"], row["proof_id"]) == exp,
                         f"row mismatch {name}/{h}: {(row['classification'], row['sign'], row['proof_id'])} != {exp}")
                    if row["classification"] == "unclassifiable":
                        unresolved.append(h)
                    counts[(row["classification"], row["sign"])] += 1
                    checked += 1
                need(t["unresolved_hypotheses"] == unresolved, f"unresolved list {name}")
                if unresolved:
                    unresolved_by_cell[t["cell"]] = len(unresolved)
                cells.append(t["cell"])
    need(len(cells) == 55 and checked == 55 * N, "cell/row count")
    roles = Counter(("null" if x.startswith("N") else ROLE[int(x[1])]) for x in cells)
    need(roles == {"null": 20, "calibration": 10, "power": 20, "sensitivity": 5}
         and cert["acceptance_partition"]["total_cells"] == 55, "acceptance partition")
    need(cert["unresolved_cells"] == sorted(unresolved_by_cell) and cert["unresolved_rows"] == sum(unresolved_by_cell.values()),
         "unresolved bookkeeping")
    need(cert["freeze_possible"] is (cert["unresolved_rows"] == 0), "freeze_possible flag")
    gaps = Counter()
    for s in range(4, 8):
        for c in range(1, 5):
            for h in CATEGORICAL:
                _, kid = spec_key(s, c, h)
                if kid not in evidence:
                    gaps[(f"P{s}/C{c}", kid)] += 1
    need(cert["uncertified_categorical_kernels"] == [{"cell": a, "kernel_id": b, "rows": n}
                                                       for (a, b), n in sorted(gaps.items())], "kernel gap list")
    # regression against the superseded v3 tables: no resolved v3 row may change class or sign, except the
    # v3 slogan-proof persistence rows whose sign must be unchanged and the previously refused P7/C4 rows.
    regress = {"compared": 0, "changed": 0}
    if (V3 / "manifest.sha256").exists():
        for line in (V3 / "manifest.sha256").read_text().splitlines():
            h, name = line.split("  ", 1)
            need(digest(V3 / name) == h, f"v3 manifest {name}")
        for s in range(1, 8):
            for c in range(5):
                old = json.loads((V3 / f"tables/P{s}_C{c}.json").read_text())["records"]
                new = json.loads((PKG / f"tables/P{s}_C{c}.json").read_text())["records"]
                for a, b in zip(old, new):
                    if a["classification"] == "unclassifiable":
                        continue
                    regress["compared"] += 1
                    if (a["classification"], a["sign"]) != (b["classification"], b["sign"]):
                        regress["changed"] += 1
        need(regress["changed"] == 0, f"regression against v3: {regress}")
    report = {
        "schema": "m3.2-scheme-d-pre-rng-package-verification.v4",
        "result": "PASS", "independent_implementation": True, "rng_constructed": False,
        "cells_checked": len(cells), "rows_checked": checked,
        "sources_checked": len(D), "manifest_entries_checked": len(listed),
        "classification_counts": {f"{k[0]}:{k[1]}": v for k, v in sorted(counts.items(), key=str)},
        "categorical_rows_by_source": dict(sorted(used_sources.items())),
        "unresolved_cells": {k: v for k, v in sorted(unresolved_by_cell.items())},
        "unresolved_rows": sum(unresolved_by_cell.values()),
        "uncertified_categorical_kernels": len(gaps),
        "regression_vs_v3": regress,
        "freeze_possible": cert["freeze_possible"],
    }
    (PKG / "verification_result.json").write_text(json.dumps(report, sort_keys=True, indent=1) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
