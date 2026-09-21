#!/usr/bin/env python3
"""Independent verifier for the RNG-free M3.2 Scheme D validation bundle, REVISION 3.

Revision 3 adds: hash binding of the reference and optimized implementations and of the complete equivalence
suite/results (typed here, checked against git commit a43336d), full reproduction of the equivalence receipt by
re-running the suite, an independent equivalence probe on verifier-own fixtures against the production ``_metric``,
the recorded compute projection recomputed, and runner gate checks.

Imports neither the builder, the runner, the preflight suite nor the pinned harness.  It re-derives the
membership and effect-assignment tables, the C0-C4 covariance matrices and PSD proofs, the null MAD and the
config's fixed numeric design from the protocol text (typed here), checks every hash binding (bundle files,
pinned inputs, protocol documents, frozen truth package, git ancestry and tracking), re-checks import
isolation in a clean interpreter and the absence of RNG use, and re-runs the preflight suite in a subprocess,
requiring its report to equal the recorded one.  Fails closed.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata as md
import json
import math
import os
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FROZEN_TRUTH_COMMIT = "7279ce0e4b9e245a00a45e69c6914d5d47d32903"
BASELINE_COMMIT = "ab9e39683f5e9f756fc7ef6690133ecb5b608f64"
TRUTH_PACKAGE_SHA256 = "a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb"
PROTOCOL = "docs/superpowers/specs/2026-09-19-m32-scheme-d-actual-statistic-validation-protocol.md"
CORRECTION = "docs/superpowers/specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md"
ADDENDUM = "docs/superpowers/specs/2026-09-21-m32-scheme-d-owner-clarifications-i1-i8.md"
V1_DIR = "scripts/m32_scheme_d_validation_bundle_20260921"
V1_COMMIT = "8f21592fd9fcbabf65c8fa21b3c1ec0ddf52e217"
V1_SHA256 = "0b42c6a38c3329eff6ec760176b682003ff993b0976d45588f72eebb4be78a3e"
V2_DIR = "scripts/m32_scheme_d_validation_bundle_v2_20260921"
V2_COMMIT = "0e2437d456c2dcbaab5a4f417f0bc7a48c45d31d"
V2_SHA256 = "397961d944f98f730eb723d537594d56428cf72db349449ad848678ad85f0c4e"
V3_DIR = "scripts/m32_scheme_d_validation_bundle_v3_20260921"
FAST_COMMIT = "a43336d488deb86139b32c8335c36e7b951a2939"
FAST_HASHES = {
    "trader/cognition/m32_fast_metric.py": "892be0d02537b7ad1d353456a7a960e7181afefd6101983d47764944c95105d0",
    "scripts/m32_fast_metric_20260921/equivalence_suite.py": "5cabfe07ae1c07ca0e631ba354976ada5ccf794128ba2d00fa9478869963e1b6",
    "scripts/m32_fast_metric_20260921/benchmark_fast_metric.py": "7eb17fe925f8d10f67fa5d94d5aae4093568c2bd7c057adf7d7c19b513635d19",
    "scripts/m32_fast_metric_20260921/equivalence_report.json": "435d6754198b430130f79a2e2651e4ff5117d2445dedd20f042db0195cafecb2",
    "scripts/m32_fast_metric_20260921/benchmark_result.json": "da7c26d7ea08bbdae13144703579f689b47d882b06faed3efc75f61deee58a1f",
    "scripts/m32_fast_metric_20260921/artifact_hashes.json": "4bd789b3d81a965997f1ff713fb22a1fed4fb67138592bb6655bbf7da2c21e89",
    "tests/test_m32_fast_metric_equivalence.py": "736bf8f381d7db3d53fb1c03932b30bf0fe441bdeb695861566a22f722c660af"}
EQ = {"comparisons": 7_592_064, "statistic_vectors": 22_314, "fixtures": 4_529, "mismatches": 0}
PROTOCOL_SHA = {PROTOCOL: "8b9e36c3767f5550607901b6410553fdab6d0b0c70e8d4c632e82eed0b59d7ac",
                CORRECTION: "2e925ebaa5beea9a3d04e5c687406642d5305053a639edb16b27d3e970495163",
                ADDENDUM: "09a70e84121e6b613732820f346f74c1fd53587f4c5dca300aa3eebb349471dc"}
SECTION2 = {"trader/cognition/m32_search.py": "1081e3482c0d709b81b4b93db2fa94437ebead2e4bc92c78b9b359f7030a827b",
            "trader/cognition/m32_protocol.py": "ade9d184c8e84a2e0be2219c106e6b77403089637dabd19f33bd34995be1e3bd",
            "trader/cognition/m32_correction.py": "67a617be122c05616aa653cacc501f8e1658e493e659e9530e25c48153f10db7"}
HARNESS = {"trader/cognition/m32_scheme_d_validation.py": "7d30fa8b80727485c8e1efe34128c5c94222598becfd24f8aebbae4b51928f61"}
EXPECTED_FILES = {"build_bundle.py", "verify_bundle.py", "requirements.lock", "artifacts/config.json",
                  "artifacts/membership_table.json", "artifacts/effect_assignment_table.json", "artifacts/loadings.json",
                  "artifacts/covariance_psd.json", "artifacts/environment_lock.json", "artifacts/preflight_report.json",
                  "artifacts/equivalence_receipt.json", "artifacts/compute_projection.json",
                  "runner/scheme_d_runner.py", "runner/acceptance.py", "runner/preflight.py"}
DENY = ("trader.kernel", "trader.data", "trader.core", "trader.engine", "trader.dashboard", "trader.brain",
        "trader.research", "trader.strategy", "sqlite3", "requests", "httpx", "aiohttp", "urllib3", "http.client",
        "ccxt", "websockets")
H = 384


class Fail(AssertionError):
    pass


def need(cond, msg):
    if not cond:
        raise Fail(msg)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def jl(rel: str):
    return json.loads((HERE / rel).read_text())


def frac(s: str) -> Fraction:
    return Fraction(s)


# ------------------------------------------------------------------ protocol constants, typed independently
STATE, LINKED = 0.6744897501960817, 0.8416212335729143


def proto_targets() -> dict[int, set[int]]:
    """Section 11.1 index rules, written from the protocol's set-builder text."""
    P = {101: {0}, 102: {128}, 103: {320}, 104: {0, 33, 130, 163, 260, 293, 326, 359}}
    P[105] = {x for c in range(4) for x in (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)}
    P[106] = ({c for c in range(0, 8)} | {128 + c for c in range(8, 16)} | {256 + c for c in range(16, 24)}
              | {320 + c for c in range(24, 32)})
    P[107] = ({c for c in range(0, 24)} | {128 + c for c in range(8, 32)} | {256 + c for c in range(16, 32)}
              | {288 + c for c in range(0, 8)} | {320 + c for c in list(range(0, 16)) + list(range(24, 32))})
    return P


def label_of(h: int) -> str:
    return "case_kind" if h < 128 else "persistence" if h < 256 else "continuation"


def kind_of(h: int) -> str:
    return "state" if h < 256 else "transition" if h < 320 else "sequence"


def check_git_and_hashes(report: dict) -> None:
    need(git("cat-file", "-t", FROZEN_TRUTH_COMMIT).stdout.strip() == "commit", "freeze commit missing")
    need(git("merge-base", "--is-ancestor", FROZEN_TRUTH_COMMIT, "HEAD").returncode == 0, "freeze commit not an ancestor")
    need(git("merge-base", "--is-ancestor", BASELINE_COMMIT, FROZEN_TRUTH_COMMIT).returncode == 0,
         "baseline commit not an ancestor of the freeze commit")
    manifest = jl("BUNDLE_MANIFEST.json")
    need(manifest["schema"] == "m3.2-scheme-d-validation-bundle-manifest.v3" and manifest["bundle_revision"] == 3, "manifest schema")
    sup = manifest["supersedes"]
    need(sup["bundle_sha256"] == V2_SHA256 == sup["v2_manifest_file_sha256"] == sha(ROOT / V2_DIR / "BUNDLE_MANIFEST.json"),
         "v2 supersession record")
    need(sup["earlier"] == [{"bundle_manifest": V1_DIR + "/BUNDLE_MANIFEST.json", "bundle_sha256": V1_SHA256,
                             "manifest_file_sha256": V1_SHA256}] and sha(ROOT / V1_DIR / "BUNDLE_MANIFEST.json") == V1_SHA256,
         "v1 supersession record")
    need(git("diff", "--quiet", V1_COMMIT, "--", V1_DIR).returncode == 0, "v1 bundle was modified")
    need(git("diff", "--quiet", V2_COMMIT, "--", V2_DIR).returncode == 0, "v2 bundle was modified")
    need(git("merge-base", "--is-ancestor", FAST_COMMIT, "HEAD").returncode == 0, "fast-metric commit not an ancestor")
    need(manifest["fast_metric"] == {"commit": FAST_COMMIT, "module": "trader/cognition/m32_fast_metric.py",
                                     "reference": "trader/cognition/m32_search.py", "equivalence": EQ}, "manifest fast-metric record")
    need(git("diff", "--quiet", FAST_COMMIT, "--", *FAST_HASHES).returncode == 0, "fast-metric files differ from commit a43336d")
    need(manifest["clarification_addendum"] == {"path": ADDENDUM, "sha256": PROTOCOL_SHA[ADDENDUM]}, "addendum binding")
    need(manifest["frozen_truth_commit"] == FROZEN_TRUTH_COMMIT and manifest["baseline_commit"] == BASELINE_COMMIT, "commits")
    need(manifest["no_rng_no_seed_generation_no_worlds_no_search_no_gate2"] is True, "manifest flags")
    listed = {str(Path(k).relative_to(V3_DIR)): v
              for k, v in manifest["files_sha256"].items()}
    need(set(listed) == EXPECTED_FILES, f"bundle file set differs: {set(listed) ^ EXPECTED_FILES}")
    for rel, h in listed.items():
        need(sha(HERE / rel) == h, f"bundle file drift {rel}")
        tracked = git("ls-files", "--error-unmatch", str((HERE / rel).relative_to(ROOT))).returncode == 0
        need(tracked, f"bundle file not tracked in git {rel}")
    pinned = manifest["pinned_inputs_sha256"]
    need(set(pinned) == set({**SECTION2, **HARNESS, **PROTOCOL_SHA, **FAST_HASHES}), "pinned input set")
    for rel, h in {**SECTION2, **HARNESS, **PROTOCOL_SHA, **FAST_HASHES}.items():
        need(pinned.get(rel) == h == sha(ROOT / rel), f"pinned input drift {rel}")
        need(git("ls-files", "--error-unmatch", rel).returncode == 0, f"pinned input not tracked {rel}")
    need(manifest["implementation_hashes_protocol_section_2"] == SECTION2, "section 2 hashes")
    fz = manifest["frozen_truth_package"]
    need(fz["package_sha256"] == TRUTH_PACKAGE_SHA256 and fz["rows"] == 21_120 and fz["unresolved_rows"] == 0,
         "frozen truth binding")
    pkg = ROOT / fz["package_dir"]
    need(sha(pkg / "manifest.sha256") == TRUTH_PACKAGE_SHA256, "truth package hash drift")
    for line in (pkg / "manifest.sha256").read_text().splitlines():
        h, name = line.split("  ", 1)
        need(sha(pkg / name) == h, f"truth package file drift {name}")
    need(sha(ROOT / fz["freeze_json"]) == fz["freeze_json_sha256"], "FREEZE.json drift")
    need(git("diff", "--quiet", FROZEN_TRUTH_COMMIT, "--", fz["package_dir"], fz["freeze_json"]).returncode == 0,
         "truth package differs from the freeze commit")
    freeze_check = subprocess.run([sys.executable, str(ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921/freeze_package.py"),
                                   "--check"], capture_output=True, text=True, cwd=ROOT)
    need(freeze_check.returncode == 0 and "FROZEN_INTACT" in freeze_check.stdout, "freeze --check failed")
    report["bundle_sha256"] = sha(HERE / "BUNDLE_MANIFEST.json")
    report["bundle_files"] = len(listed)
    report["pinned_inputs"] = len(pinned)
    report["truth_package_sha256"] = TRUTH_PACKAGE_SHA256


def check_tables(report: dict) -> None:
    m = jl("artifacts/membership_table.json")
    need(len(m["rows"]) == H, "membership rows")
    for row in m["rows"]:
        h = row["hypothesis"]
        need((row["kind"], row["label"], row["cluster"], row["sector"]) ==
             (kind_of(h), label_of(h), h % 32, "A" if h % 32 < 16 else "B"), f"membership row {h}")
        need(float(row["threshold"]) == (STATE if h < 256 else LINKED)
             and row["threshold_binary64_hex"] == float(STATE if h < 256 else LINKED).hex(), f"threshold {h}")
        need(row["outcome_column"] == ("persistence" if h in range(128, 256) else "categorical"), f"outcome column {h}")
    e = jl("artifacts/effect_assignment_table.json")
    targets = proto_targets()
    shift = {102: Fraction(1), 104: Fraction(1, 2), 105: Fraction(1, 2), 106: Fraction(1, 2), 107: Fraction(1, 4)}
    odds = {101: Fraction(3), 103: Fraction(3), 104: Fraction(2), 105: Fraction(2), 106: Fraction(2), 107: Fraction(3, 2)}
    need({s["scenario_code"] for s in e["scenarios"]} == set(targets), "scenario set")
    sizes = {}
    for s in e["scenarios"]:
        code = s["scenario_code"]
        need(set(s["targets"]) == targets[code] and s["targets"] == sorted(targets[code]), f"targets P{code}")
        need(s["role"] == ("sensitivity_only" if code == 107 else "acceptance"), f"role {code}")
        need([a["hypothesis"] for a in s["assignments"]] == sorted(targets[code]), f"assignment order {code}")
        for a in s["assignments"]:
            if label_of(a["hypothesis"]) == "persistence":
                need(a["effect"] == "shift" and frac(a["magnitude_null_mad_units"]) == shift[code], f"shift {code}")
            else:
                p2 = Fraction(1, 10)
                new = odds[code] * p2 / (1 - p2 + odds[code] * p2)
                need(a["effect"] == "odds_ratio" and frac(a["odds_ratio"]) == odds[code] and a["target_class_index"] == 2
                     and frac(a["new_class2_probability"]) == new and frac(a["switch_probability"]) == (new - p2) / (1 - p2),
                     f"odds ratio {code}")
        sizes[code] = len(s["targets"])
    need(sizes == {101: 1, 102: 1, 103: 1, 104: 8, 105: 32, 106: 32, 107: 96}, "sizes")
    per_kind = {c: {k: sum(1 for h in t if label_of(h) == k) for k in ("case_kind", "persistence", "continuation")}
                for c, t in targets.items()}
    need(per_kind[105] == {"case_kind": 8, "persistence": 8, "continuation": 16}, "P5 balance")
    need(per_kind[107]["case_kind"] == 24 and per_kind[107]["persistence"] == 24 and per_kind[107]["continuation"] == 48,
         "P7 balance")
    report["hypotheses"] = H
    report["effect_scenarios"] = len(targets)


def exact_entry(c: int, i: int, j: int) -> Fraction:
    if i == j:
        return Fraction(1)
    same_sector = (i % 32 < 16) == (j % 32 < 16)
    return {0: Fraction(0), 1: Fraction(3, 10), 2: Fraction(7, 10),
            3: Fraction(1, 10) + (Fraction(3, 5) if i % 32 == j % 32 else 0),
            4: Fraction(3, 5) * (Fraction(1, 4) + (Fraction(3, 4) if same_sector else -Fraction(3, 4)))}[c]


FACTORS = {0: ([], Fraction(1)), 1: ([(Fraction(3, 10), [1] * H)], Fraction(7, 10)),
           2: ([(Fraction(7, 10), [1] * H)], Fraction(3, 10)),
           3: ([(Fraction(1, 10), [1] * H)] + [(Fraction(3, 5), [int(h % 32 == k) for h in range(H)]) for k in range(32)],
               Fraction(3, 10)),
           4: ([(Fraction(3, 20), [1] * H), (Fraction(9, 20), [1 if h % 32 < 16 else -1 for h in range(H)])],
               Fraction(2, 5))}


def check_psd(report: dict) -> None:
    cov, load = jl("artifacts/covariance_psd.json"), jl("artifacts/loadings.json")
    out = {}
    root3_2 = math.sqrt(3.0) / 2.0
    need(load["codes"]["C4"]["sector_A_loading_binary64"] == [0.5.hex(), root3_2.hex()]
         and load["codes"]["C4"]["sector_B_loading_binary64"] == [0.5.hex(), (-root3_2).hex()], "C4 loadings hex")
    need(load["codes"]["C4"]["theta_degrees"] == 60 and load["codes"]["C4"]["lambda"] == "3/5", "C4 lambda/theta")
    for c in range(5):
        factors, d = FACTORS[c]
        rec = cov["codes"][f"C{c}"]
        need(d > 0 and all(w >= 0 for w, _ in factors), f"C{c} factor form signs")
        for h in range(H):
            need(sum((w * v[h] ** 2 for w, v in factors), Fraction(0)) + d == 1, f"C{c} unit variance h{h}")
        m = np.empty((H, H))
        for i in range(H):
            for j in range(H):
                e = exact_entry(c, i, j)
                total = sum((w * v[i] * v[j] for w, v in factors), Fraction(0)) + (d if i == j else 0)
                need(total == e, f"C{c} factor form != entry rule at ({i},{j})")
                m[i, j] = e.numerator / e.denominator
        need(len(factors) < H, f"C{c} factor count")          # => Sigma - d I singular => lambda_min = d exactly
        need(rec["exact_minimum_eigenvalue"] == f"{d.numerator}/{d.denominator}", f"C{c} exact min eigenvalue")
        need(hashlib.sha256(m.astype("<f8").tobytes()).hexdigest() == rec["matrix_float64_row_major_sha256"], f"C{c} matrix hash")
        w = np.linalg.eigvalsh(m)
        need(abs(w[0] - float(d)) < 1e-9 and abs(w[0] - float(rec["numeric_minimum_eigenvalue"])) < 1e-9
             and abs(w[-1] - float(rec["numeric_maximum_eigenvalue"])) < 1e-7, f"C{c} numeric eigenvalues")
        np.linalg.cholesky(m)
        # implementation-style loadings realise the exact C4 matrix to float rounding
        if c == 4:
            L = np.array([[math.sqrt(0.6) * 0.5, math.sqrt(0.6) * (root3_2 if h % 32 < 16 else -root3_2)] for h in range(H)])
            need(np.max(np.abs(L @ L.T + 0.4 * np.eye(H) - m)) < 1e-15, "C4 implementation loadings")
        out[f"C{c}"] = {"exact_min_eigenvalue": rec["exact_minimum_eigenvalue"], "numeric_min": f"{w[0]:.10f}",
                        "numeric_max": f"{w[-1]:.6f}", "factors": len(factors)}
    report["psd"] = out


def null_mad_t3(order: int = 1024) -> float:
    nodes, weights = np.polynomial.legendre.leggauss(order)
    th = nodes * math.pi / 2
    u, wt = np.tan(th), weights * np.cos(th) ** 2
    F = lambda x: 0.5 + (np.arctan(x) + x / (1 + x * x)) / math.pi      # cdf of t3/sqrt(3)

    def mass(m):
        return float(0.9 * (2 * F(m) - 1) + 0.1 * np.sum(wt * (F(m - 2 * u) - F(-m - 2 * u))))
    lo, hi = 0.0, 3.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if mass(mid) < 0.5 else (lo, mid)
    return (lo + hi) / 2


def check_config(report: dict) -> None:
    c = jl("artifacts/config.json")
    need(c["master_seed"] == 2026091902 and c["status"] == "FROZEN_CONFIG_NOT_AUTHORIZED_TO_RUN", "master seed / status")
    a = c["authority"]
    need(c["schema"] == "m3.2-scheme-d-validation-config.v3" and c["bundle_revision"] == 3
         and c["supersedes"]["bundle_manifest"] == V2_DIR + "/BUNDLE_MANIFEST.json" and c["supersedes"]["bundle_sha256"] == V2_SHA256
         and c["supersedes"]["earlier"] == [{"bundle_manifest": V1_DIR + "/BUNDLE_MANIFEST.json", "bundle_sha256": V1_SHA256}],
         "config revision")
    need(a["protocol_sha256"] == PROTOCOL_SHA[PROTOCOL] and a["correction_sha256"] == PROTOCOL_SHA[CORRECTION]
         and a["clarification_addendum"] == ADDENDUM and a["clarification_addendum_sha256"] == PROTOCOL_SHA[ADDENDUM]
         and a["baseline_commit"] == BASELINE_COMMIT and a["frozen_truth_commit"] == FROZEN_TRUTH_COMMIT, "authority")
    sm = c["seed_map"]
    need(sm["phase_code"] == {"null": 10, "power": 20, "checks": 30, "benchmark": 90}, "phase codes")
    need(sm["correlation_code"] == {f"C{i}": i for i in range(5)}, "correlation codes")
    need(sm["dgp_code_phase_10"] == {f"N{i}": i for i in range(4)} and
         sm["dgp_code_phase_20"] == {f"P{k}": 100 + k for k in range(1, 8)}, "dgp codes")
    need(sm["stream_code"] == {"data": 0, "membership": 1, "permutation": 2, "injection": 3, "bootstrap": 4}, "streams")
    need(sm["world_index"] == {"null": [0, 1999], "power": [0, 999], "checks": [0, 0]}, "world index")
    tuples = sm["distinct_tuples"]
    need(tuples == {"null": 4 * 5 * 2000 * 3, "power": 7 * 5 * 1000 * 4, "checks_bootstrap": 30, "total": 260_030}, "tuple counts")
    need(sm["tuple_materialisation"].startswith("none"), "no tuples materialised")
    need(c["rng"]["bit_generator"] == "numpy.random.PCG64", "bit generator")
    need(c["environment"] == {"python": "3.12.3", "numpy": "2.5.2", "OPENBLAS_NUM_THREADS": "1"}, "environment")
    d = c["design"]
    need((d["blocks"], d["block_days"], d["symbols"], d["keys"], d["rows_per_block"], d["rows_per_world"]) ==
         (48, 28, 16, 5, 80, 3840), "geometry")
    need(d["strata_sizes"] == {"(1,0)": 8, "(1,1)": 4, "(2,0)": 4, "(2,1)": 8, "(3,0)": 8, "(3,1)": 4, "(4,0)": 4, "(4,1)": 8},
         "strata")
    need(d["missing_fraction_class_edges"] == [[0, 0.2], [0.2, 0.4], [0.4, 0.6], [0.6, 1]], "class edges")
    need(d["class_rule"].startswith("integer counts: class = [5m >= T] + [5m >= 2T] + [5m >= 3T]"), "integer class rule")
    f = c["family"]
    need(f["size"] == 384 and f["clusters"] == 32 and f["thresholds"] == {"state": repr(STATE), "linked": repr(LINKED)}, "family")
    s = c["sampling"]
    need(s["permutations_per_world"] == 767_999 and s["with_replacement"] and s["identity_included"]
         and s["replacement_of_failed_draws"] is False and s["tie_rule"] == "T* >= T - 1e-12 * max(1, |T|)"
         and s["p_value"] == "(1 + X) / (B + 1)", "sampling")
    mult = c["multiplicity"]
    need(mult["q"] == "1/20" and mult["m"] == 384 and mult["refused_stay_in_denominator"] and mult["refused_p_value"] is None, "BH")
    need(c["cells"]["null"] == {"count": 20, "worlds_each": 2000} and c["cells"]["power"] == {"count": 35, "worlds_each": 1000}
         and c["cells"]["worlds_total"] == 20 * 2000 + 35 * 1000 == 75_000, "cells")
    dgp = c["dgp"]
    need(dgp["N1"]["categorical"] == ["13/20", "1/4", "1/10"] and dgp["N2"]["coverage"]["rates"] == ["8/100", "3/10", "1/2", "18/25"]
         and dgp["N2"]["membership_shift_by_realized_class"] == ["0", "2/5", "4/5", "6/5"]
         and dgp["N2"]["persistence_shift_null_mad_units_by_realized_class"] == ["0", "1/2", "1", "3/2"]
         and dgp["N3"]["membership_shift_by_quarter"] == ["-4/5", "-1/5", "2/5", "9/10"] and dgp["N3"]["membership_shift_regime1"] == "6/5"
         and dgp["N3"]["persistence_shift_null_mad_units_by_quarter"] == ["9/10", "-1/2", "3/10", "-1"]
         and dgp["N3"]["persistence_shift_regime1"] == "3/2", "dgp tables")
    ac = c["acceptance"]
    need(ac["wilson_z"] == 1.959964 and ac["type_one"] == {"point_max": 0.05, "wilson_upper_max": 0.06}
         and ac["fdr"]["resamples"] == 10000 and ac["fdr"]["order_statistic"] == 9750
         and ac["fdr"]["seed"] == "[2026091902, 30, correlation_code, scenario_code, 0, 4]"
         and ac["calibration"]["clopper_pearson_alpha"] == "1/6000" and ac["calibration"]["t"] == ["1/100", "5/100", "1/10"],
         "acceptance constants")
    need(Fraction(1, 6000) == Fraction(1, 100) / 60, "CP alpha = 0.01/60")
    need(ac["power"]["P1_P3"]["wilson_lower_min"] == 0.80 and ac["power"]["P4"] == [{"at_least": 1, "min": 0.90}, {"at_least": 4, "min": 0.75}]
         and ac["power"]["P5_P6"] == [{"at_least": 1, "min": 0.95}, {"at_least": 16, "min": 0.75}], "power rules")
    need(c["refusal"]["hypothesis_level_floors"] == {"state": {"matched": 12, "observed": 8, "episodes": 3},
                                                     "transition": {"matched": 8, "episodes": 3},
                                                     "sequence": {"matched": 8, "episodes": 3}}, "floors")
    need(c["statistic"]["sha256"] == SECTION2["trader/cognition/m32_search.py"] and c["statistic"]["function"] == "_metric"
         and c["statistic"]["path"] == "fast_metric_exact_equivalent" and c["statistic"]["reference_path"] == "reference_metric_import"
         and c["statistic"]["optimized_path"] == "trader.cognition.m32_fast_metric.FastMetric", "statistic binding")
    fm = c["fast_metric"]
    need({k: v["sha256"] for k, v in fm.items() if isinstance(v, dict) and "path" in v} ==
         {"module": FAST_HASHES["trader/cognition/m32_fast_metric.py"], "suite": FAST_HASHES["scripts/m32_fast_metric_20260921/equivalence_suite.py"],
          "benchmark_script": FAST_HASHES["scripts/m32_fast_metric_20260921/benchmark_fast_metric.py"],
          "equivalence_report": FAST_HASHES["scripts/m32_fast_metric_20260921/equivalence_report.json"],
          "benchmark_result": FAST_HASHES["scripts/m32_fast_metric_20260921/benchmark_result.json"],
          "artifact_hashes": FAST_HASHES["scripts/m32_fast_metric_20260921/artifact_hashes.json"],
          "reference": SECTION2["trader/cognition/m32_search.py"],
          "equivalence_test": FAST_HASHES["tests/test_m32_fast_metric_equivalence.py"]}, "config fast-metric hashes")
    need(fm["commit"] == FAST_COMMIT and all(fm["equivalence"][k] == v for k, v in EQ.items())
         and fm["equivalence"]["protocol_master_seed_used"] is False and fm["equivalence"]["test_seed"] == 314159265, "config equivalence record")
    od = c["owner_decisions"]
    need(od["status"] == "DECIDED_2026-09-21" and od["addendum"] == ADDENDUM, "owner decisions status")
    need([x["id"] for x in od["decisions"]] == [f"I{i}" for i in range(1, 9)] + ["class_edge"], "owner decision ids")
    need({x["id"] for x in od["decisions"] if x["changed_vs_v1"]} == {"I3", "I6", "class_edge"}, "only I3/I6/class-edge changed vs v1")
    need({x["id"] for x in od["decisions"] if x["changed_vs_v2"]} == {"I8"}, "only I8 changed vs v2")
    need("zero tested hypotheses (owner decision I3)" in c["refusal"]["hypothesis_level_floors"].keys() or
         "zero tested hypotheses (owner decision I3)" in c["refusal"]["world_level"], "I3 world refusal recorded")
    need(c["acceptance"]["calibration"]["refused_h_of_w"].startswith("world excluded from the ECDF denominator"), "I6 recorded")
    need("interpretations_flagged_for_owner_review" not in c, "stale v1 interpretation key")
    need(c["null_mad"]["gaussian"] == repr(STATE) and abs(float(c["null_mad"]["t3_common_jump"]) - null_mad_t3()) < 1e-12,
         "null MAD independently re-derived")
    need(c["frozen_truth_package"]["consumed_unchanged"] is True and c["pinned_sources_sha256"] ==
         {**SECTION2, **HARNESS}, "config pins")
    report["null_mad_t3_rederived"] = null_mad_t3()


def check_environment(report: dict) -> None:
    need(sys.version_info[:3] == (3, 12, 3), "python 3.12.3 required")
    need(md.version("numpy") == "2.5.2" and np.__version__ == "2.5.2", "numpy 2.5.2 required")
    lock = jl("artifacts/environment_lock.json")
    need(lock["python"] == "3.12.3" and lock["numpy"] == "2.5.2" and lock["OPENBLAS_NUM_THREADS"] == "1"
         and lock["runner_third_party_imports"] == ["numpy"], "environment lock")
    req = (HERE / "requirements.lock").read_text().splitlines()
    need("numpy==2.5.2" in req and "python==3.12.3" in req and "env OPENBLAS_NUM_THREADS=1" in req, "requirements.lock")
    dist = md.distribution("numpy")
    record = Path(dist.locate_file("")) / f"numpy-{dist.version}.dist-info" / "RECORD"
    need(lock["numpy_dist_record_sha256"] == sha(record), "numpy RECORD hash drift")
    need(os.environ.get("OPENBLAS_NUM_THREADS") == "1", "OPENBLAS_NUM_THREADS=1 must be set for the verifier")
    report["environment"] = {"python": "3.12.3", "numpy": "2.5.2", "openblas_threads": "1",
                             "numpy_record_sha256": lock["numpy_dist_record_sha256"]}


def check_isolation_and_rng(report: dict) -> None:
    code = ("import sys; sys.path.insert(0, %r); import scheme_d_runner as R;"
            "bad=[m for m in %r if m in sys.modules or any(k.startswith(m+'.') for k in sys.modules)];"
            "print(len(bad), 'sqlite3' in sys.modules)") % (str(HERE / "runner"), DENY)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
    need(proc.returncode == 0 and proc.stdout.split() == ["0", "False"], f"import isolation: {proc.stdout} {proc.stderr[-200:]}")
    rng_names = {"PCG64", "SeedSequence", "default_rng", "RandomState", "Generator", "seed"}
    homes = {}
    for name in ("scheme_d_runner.py", "acceptance.py"):
        tree = ast.parse((HERE / "runner" / name).read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr in rng_names:
                        homes.setdefault(name, set()).add(fn.name)
            elif isinstance(fn, ast.Import):
                need(all(a.name.split(".")[0] not in ("sqlite3", "socket", "requests", "random") for a in fn.names),
                     f"forbidden import in {name}")
    need(homes == {"scheme_d_runner.py": {"generator"}}, f"RNG construction outside SeedRegistry.generator: {homes}")
    for path in (HERE / "runner").rglob("*"):
        need(path.suffix != ".jsonl" and "receipt" not in path.name.lower(), f"unexpected output artifact {path}")
    report["import_isolation"] = {"denied_modules_loaded": 0, "sqlite3_loaded": False}
    report["rng_constructions_outside_seed_registry"] = 0


def check_preflight(report: dict) -> None:
    recorded = jl("artifacts/preflight_report.json")
    need(recorded["result"] == "PASS" and recorded["rng_construction_attempts"] == 0 and recorded["rng_constructed"] is False
         and recorded["worlds_generated_from_random_generator"] == 0 and recorded["seed_tuples_materialised"] == 0, "recorded preflight")
    need(all(r["result"] == "PASS" for r in recorded["results"]) and recorded["checks_total"] == len(recorded["results"]) == 33,
         "recorded preflight checks")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "pre.json"
        proc = subprocess.run([sys.executable, str(HERE / "runner" / "preflight.py"), "--out", str(out)],
                              capture_output=True, text=True, env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
        need(proc.returncode == 0, f"preflight rerun failed: {proc.stderr[-300:]}")
        fresh = json.loads(out.read_text())
    need(fresh == recorded, "fresh preflight report differs from the recorded report")
    report["preflight"] = {"checks": recorded["checks_total"], "passed": recorded["checks_passed"],
                           "rng_construction_attempts": 0}


def cp_contains(k: int, n: int, t: Fraction, alpha: Fraction = Fraction(1, 6000)) -> bool:
    a, b = t.numerator, t.denominator

    def cdf(kk):
        return sum(math.comb(n, i) * a ** i * (b - a) ** (n - i) for i in range(kk + 1)), b ** n
    half = alpha / 2
    if k > 0:
        num, den = cdf(k - 1)
        if Fraction(den - num, den) < half:
            return False
    if k < n:
        num, den = cdf(k)
        if Fraction(num, den) < half:
            return False
    return True


V2_PROBE = r"""
import sys, os, json
sys.path.insert(0, %r)
import numpy as np
def trip(*a, **k):
    raise SystemExit("random generator constructed")
for n in ("PCG64", "SeedSequence", "default_rng", "Generator", "RandomState"):
    setattr(np.random, n, trip)
import scheme_d_runner as R
import acceptance as A
from fractions import Fraction as F
class S:
    def __init__(self, g=None): self.g = g
    @staticmethod
    def _n(size): return int(np.prod(size)) if size is not None else 1
    def _shape(self, a, size): return a.reshape(size) if size is not None else a[0]
    def integers(self, lo, hi, size=None): return self._shape((np.arange(self._n(size)) %% (hi - lo) + lo).astype(np.int64), size)
    def random(self, size=None): return self._shape(np.full(self._n(size), 0.5), size)
    def standard_normal(self, size=None): return self._shape(np.sin(np.arange(self._n(size)) * 1.7), size)
    def standard_t(self, df, size=None): return self._shape(np.cos(np.arange(self._n(size)) * 0.9), size)
    def geometric(self, p, size=None): return self._shape(np.full(self._n(size), self.g, dtype=np.int64), size)
    def choice(self, a, size=None, p=None): return self._shape((np.arange(self._n(size)) %% a).astype(np.int64), size)
    def permutation(self, x): return np.roll(np.asarray(x), 1)
out = {"classes": [R.missing_class(m, 80) for m in range(81)]}
out["gen"] = {str(g): sorted({t[2] for t in R.generate_world(3, 0, 0, S(g), S()).block_strata}) for g in (1, 2, 3, 4, 5)}
rows = np.arange(R.ROWS)
mem = ((rows[:, None] * 13 + np.arange(384)[None, :] * 7) %% 20 == 0)
obs = np.ones(R.ROWS, bool); obs[:73] = False
strata = tuple((1 + b %% 4, int((b // 3) %% 2), 0) for b in range(48))
world = R.V.SyntheticWorld(0, 1, 0, mem, (rows %% 3).astype(np.int64), (rows %% 101) / 50.0, obs, strata, R.V.valid_metadata(strata))
r = R.world_receipt(world, S(), 90, "V", {}, draws=2, truth=[{"classification": "true_null", "sign": None}] * 384, injected=())
out["i3"] = {"world_refused": r.get("world_refused"), "summary_refused": R.world_summary(r)["refused"]}
grid = [F(2 * w + 1, 2000) for w in range(1000)]
out["i6_none_ignored"] = A.calibration_pass(grid + [None] * 1000)
out["i6_plain"] = A.calibration_pass(grid)
mixed = [F(2 * w + 1, 4000) for w in range(1900)] + [None] * 100
out["i6_mixed"] = A.calibration_pass(mixed)
print(json.dumps(out))
"""


def check_v2_semantics(report: dict) -> None:
    proc = subprocess.run([sys.executable, "-c", V2_PROBE % str(HERE / "runner")], capture_output=True, text=True,
                          env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
    need(proc.returncode == 0, f"v2 probe failed: {proc.stderr[-300:]}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    edges = (Fraction(1, 5), Fraction(2, 5), Fraction(3, 5))
    need(out["classes"] == [sum(Fraction(m, 80) >= e for e in edges) for m in range(81)], "class-edge rule")
    need(out["classes"][16] == 1 and out["classes"][15] == 0 and out["classes"][32] == 2 and out["classes"][48] == 3, "class edge values")
    need(out["gen"] == {"1": [0], "2": [1], "3": [2], "4": [3], "5": [3]}, f"generate_world classes at exact edges: {out['gen']}")
    need(out["i3"] == {"world_refused": ["no_tested_hypotheses"], "summary_refused": True}, "I3 zero-tested world refusal")
    grid = [Fraction(2 * w + 1, 2000) for w in range(1000)]
    exp = {str(t): cp_contains(sum(1 for p in grid if p <= t), 1000, t) for t in (Fraction(1, 100), Fraction(5, 100), Fraction(1, 10))}
    need(out["i6_none_ignored"] == exp == out["i6_plain"], "I6 exclusion (None entries must not change the result)")
    mixed = [Fraction(2 * w + 1, 4000) for w in range(1900)]
    exp = {str(t): cp_contains(sum(1 for p in mixed if p <= t), 1900, t) for t in (Fraction(1, 100), Fraction(5, 100), Fraction(1, 10))}
    need(out["i6_mixed"] == exp, "I6 mixed fixture")
    src = (HERE / "runner" / "scheme_d_runner.py").read_text()
    need("searchsorted" not in src and "1.0 - observed" not in src, "float class rule still present")
    report["v2_semantics"] = {"class_edges": "exact half-open (16->1, 32->2, 48->3)", "i3": "zero tested hypotheses = refused world",
                              "i6": "refused h(w) excluded from ECDF denominator"}


def check_fast_metric_artifacts(report: dict) -> None:
    rec, proj, cfg = jl("artifacts/equivalence_receipt.json"), jl("artifacts/compute_projection.json"), jl("artifacts/config.json")
    rep = json.loads((ROOT / "scripts/m32_fast_metric_20260921/equivalence_report.json").read_text())
    need(all(rep[k] == v for k, v in EQ.items()) and rep["quick"] is False and rep["protocol_master_seed_used"] is False
         and rep["mismatch_details"] == [] and rep["test_seed"] == 314159265 != 2026091902, "equivalence report content")
    need(all(rec[k] == v for k, v in EQ.items()) and rec["result"] == "PASS_0_MISMATCHES" and rec["sections"] == rep["sections"]
         and rec["coverage_counters"] == rep["coverage_counters"] and rec["untestable_none_cases"] == rep["untestable_none_cases"]
         and rec["fast_metric_commit"] == FAST_COMMIT, "equivalence receipt content")
    need(rec["equivalence_report_sha256"] == FAST_HASHES["scripts/m32_fast_metric_20260921/equivalence_report.json"]
         and rec["suite_sha256"] == FAST_HASHES["scripts/m32_fast_metric_20260921/equivalence_suite.py"]
         and rec["fast_metric_sha256"] == FAST_HASHES["trader/cognition/m32_fast_metric.py"]
         and rec["reference_sha256"] == SECTION2["trader/cognition/m32_search.py"]
         and rec["artifact_hashes_sha256"] == FAST_HASHES["scripts/m32_fast_metric_20260921/artifact_hashes.json"], "receipt hashes")
    registry = json.loads((ROOT / "scripts/m32_fast_metric_20260921/artifact_hashes.json").read_text())["sha256"]
    for rel, h in registry.items():
        need(sha(ROOT / rel) == h, f"fast-metric registry drift {rel}")
    bench = json.loads((ROOT / "scripts/m32_fast_metric_20260921/benchmark_result.json").read_text())
    p = bench["projection"]
    cpu_hours = p["worlds"] * (p["per_world_fixed_seconds"] + (1 + p["draws_per_world"]) * bench["fast_seconds_per_draw_end_to_end"]) / 3600
    need(abs(cpu_hours - p["fast_cpu_hours"]) <= 1e-9 * cpu_hours and p["worlds"] == 75_000 and p["draws_per_world"] == 767_999,
         "benchmark projection recompute")
    need(bench["mismatches_on_benchmark_fixture"] == 0 and bench["hypotheses"] == 384 and bench["rows"] == 3840, "benchmark fixture")
    need(bench["reference_seconds_per_vector"] / bench["fast_seconds_per_draw_end_to_end"] > 5_000, "speedup claim")
    need(proj["benchmark_result_sha256"] == FAST_HASHES["scripts/m32_fast_metric_20260921/benchmark_result.json"]
         and abs(proj["projection"]["fast_cpu_hours"] - cpu_hours) <= 1e-9 * cpu_hours
         and proj["projection"]["fast_cpu_hours"] == p["fast_cpu_hours"], "compute projection binding")
    need(set(proj["wall_time_estimate"]) == {"4", "64", "256"}, "wall-time keys")
    for n in (4, 64, 256):
        w = proj["wall_time_estimate"][str(n)]
        need(abs(w["hours"] - p["fast_cpu_hours"] / n) < 1e-6 and abs(w["days"] - p["fast_cpu_hours"] / n / 24) < 1e-6, f"wall time {n}")
    need(proj["throughput_draws_per_second_single_core"] == 1.0 / bench["fast_seconds_per_draw_end_to_end"], "throughput")
    report["fast_metric"] = {"comparisons": rec["comparisons"], "mismatches": rec["mismatches"], "fast_cpu_hours": p["fast_cpu_hours"],
                             "reference_cpu_hours": p["reference_cpu_hours"],
                             "wall_days": {str(n): round(p["fast_cpu_hours"] / n / 24, 2) for n in (4, 64, 256)},
                             "draws_per_second_single_core": round(1.0 / bench["fast_seconds_per_draw_end_to_end"], 1)}


GATE_PROBE = r"""
import sys, os, json
sys.path.insert(0, %r)
import numpy as np
import scheme_d_runner as R
out = {}
out["static"] = R.equivalence_gate_static()
out["closed_gate"] = None
rows = np.arange(R.ROWS)
strata = tuple((1 + b %% 4, int((b // 3) %% 2), 0) for b in range(48))
world = R.V.SyntheticWorld(0, 1, 0, ((rows[:, None] * 13 + np.arange(384)[None, :] * 7) %% 4 == 0), (rows %% 3).astype(np.int64),
                           np.sin(rows * 0.37), np.ones(R.ROWS, bool), strata, R.V.valid_metadata(strata))
try:
    R.world_receipt(world, None, 90, "P", {}, draws=1, truth=[], injected=(), block_maps=[np.arange(48)])
    out["closed_gate"] = "NOT REFUSED"
except Exception as exc:
    out["closed_gate"] = str(exc)
out["assert_gate"] = R.assert_equivalence_gate()
out["opened"] = R._GATE["open"]
print(json.dumps(out))
"""


def check_runner_gate(report: dict) -> None:
    src = (HERE / "runner" / "scheme_d_runner.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    defaults = fns["world_receipt"].args.kw_defaults + fns["world_receipt"].args.defaults
    need(any(isinstance(d, ast.Constant) and d.value == "fast" for d in defaults if d is not None),
         "world_receipt default statistic must be fast")
    calls = {n.func.id for n in ast.walk(fns["run_mode"]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    need({"assert_equivalence_gate", "authorize", "assert_environment", "assert_isolation"} <= calls, "run_mode gates")
    need("pinned_inputs_sha256" in ast.get_source_segment(src, fns["verify_bundle"]), "runner does not verify pinned inputs")
    need('_GATE["open"] = True' in ast.get_source_segment(src, fns["open_statistic_gate"]), "gate opener")
    body = ast.get_source_segment(src, fns["run_validation"])
    need("fast_benchmark_receipt_sha256" in body and "runtime_estimate_cpu_hours" in body, "validation authorization requirements")
    proc = subprocess.run([sys.executable, "-c", GATE_PROBE % str(HERE / "runner")], capture_output=True, text=True,
                          env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
    need(proc.returncode == 0, f"gate probe failed: {proc.stderr[-400:]}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    need(out["static"]["comparisons"] == EQ["comparisons"] and out["static"]["mismatches"] == 0, "static gate")
    need(out["closed_gate"] == "fast_metric_gate_closed", f"closed gate must refuse the optimized path: {out['closed_gate']}")
    need(out["opened"] is True and out["assert_gate"]["mismatches"] == 0, "assert_equivalence_gate")
    report["runner_gate"] = {"default_path": "fast", "closed_gate_refuses": True, "assert_equivalence_gate": "PASS",
                             "run_mode_gates": sorted(calls & {"assert_equivalence_gate", "authorize", "assert_environment", "assert_isolation"})}


def own_rows(y, o, x, pi, B, RPB):
    rows, lookup = [], {}
    for i in range(B * RPB):
        src = int(pi[i // RPB]) * RPB + i % RPB
        seen = bool(o[src])
        rows.append({"row_id": f"r{i}", "features": {"direction": 1, "current_row_id": f"t{i}"},
                     "labels": {"case_kind": int(y[src]) if seen else None, "price_change_bps": float(x[src]) if seen else None}})
        if seen:
            lookup[f"t{i}"] = {"row_type": f"class-{int(y[src])}", "producer": "synthetic"}
    return rows, lookup


def check_independent_equivalence_probe(report: dict) -> None:
    """Verifier-own fixtures and row builder against the production _metric (not the suite's fixtures or builder)."""
    import itertools
    import struct
    sys.path.insert(0, str(ROOT))
    from trader.cognition.m32_fast_metric import FastMetric
    from trader.cognition.m32_search import _metric
    B, RPB = 2, 4
    n = B * RPB
    subsets = np.asarray(list(itertools.product([False, True], repeat=n)), dtype=bool)
    mem = np.repeat(subsets, 3, axis=0).T
    labels = ("case_kind", "persistence", "continuation") * len(subsets)
    xs = (np.asarray([1.0, 1.0, 2.0, 3.0, 3.0, 2.0, 1.0, 5.0]), np.asarray([-4.0, 0.5, 0.5, 8.0, -4.0, 2.5, 0.5, 9.0]),
          np.asarray([2.0, 4.0, 6.0, 8.0, 1.0, 3.0, 5.0, 7.0]))
    os_ = (np.ones(n, bool), np.asarray([1, 0] * 4, bool), np.asarray([1, 1, 1, 0, 0, 1, 0, 1], bool))
    ids = [f"r{i}" for i in range(n)]
    compared = fixtures = 0
    for k, y in enumerate(itertools.product(range(3), repeat=n)):
        if k % 211:
            continue
        for oi, o in enumerate(os_):
            x = xs[(k + oi) % 3]
            fm = FastMetric(mem, np.asarray(y), x, o, labels, blocks=B, rows_per_block=RPB, chunk=(1, 2, 64)[k % 3])
            need(fm.unsupported is None, "probe fixture unexpectedly outside the fast domain")
            fixtures += 1
            for pi in ([0, 1], [1, 0]):
                rows, lookup = own_rows(y, o, x, pi, B, RPB)
                signed, ok = fm.statistics(np.asarray(pi))
                for h in range(mem.shape[1]):
                    got = _metric(rows, {ids[i] for i in np.flatnonzero(mem[:, h])}, labels[h], lookup).get("signed_effect")
                    compared += 1
                    if got is None:
                        need(not ok[h], f"probe: fast scored an untestable hypothesis (fixture {k}/{oi}, h{h})")
                    else:
                        need(ok[h] and struct.pack("<d", got) == struct.pack("<d", float(signed[h])),
                             f"probe mismatch fixture {k}/{oi} h{h}: {got!r} vs {float(signed[h])!r}")
    report["independent_equivalence_probe"] = {"fixtures": fixtures, "comparisons": compared, "mismatches": 0}


def check_equivalence_reproduction(report: dict) -> None:
    """Re-run the complete equivalence suite and require every deterministic field to equal the recorded receipt."""
    recorded = json.loads((ROOT / "scripts/m32_fast_metric_20260921/equivalence_report.json").read_text())
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "eq.json"
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/m32_fast_metric_20260921/equivalence_suite.py"), "--out", str(out)],
                              capture_output=True, text=True, env={**os.environ, "OPENBLAS_NUM_THREADS": "1"}, cwd=ROOT)
        need(proc.returncode == 0, f"equivalence suite failed on reproduction: {proc.stderr[-300:]}")
        fresh = json.loads(out.read_text())
    a = {k: v for k, v in fresh.items() if k != "seconds"}
    b = {k: v for k, v in recorded.items() if k != "seconds"}
    need(a == b, "reproduced equivalence report differs from the recorded report")
    need(fresh["comparisons"] == EQ["comparisons"] and fresh["mismatches"] == 0, "reproduced counts")
    report["equivalence_reproduction"] = {"comparisons": fresh["comparisons"], "statistic_vectors": fresh["statistic_vectors"],
                                          "fixtures": fresh["fixtures"], "mismatches": fresh["mismatches"],
                                          "identical_to_recorded_receipt": True, "reproduction_seconds": round(fresh["seconds"], 1)}


def main() -> int:
    report: dict = {"schema": "m3.2-scheme-d-validation-bundle-verification.v2", "independent_implementation": True,
                    "rng_constructed": False}
    for step in (check_git_and_hashes, check_tables, check_psd, check_config, check_environment,
                 check_isolation_and_rng, check_v2_semantics, check_fast_metric_artifacts, check_runner_gate,
                 check_independent_equivalence_probe, check_preflight, check_equivalence_reproduction):
        step(report)
    report["result"] = "PASS"
    text = json.dumps(report, sort_keys=True, indent=1) + "\n"
    (HERE / "artifacts" / "verification_result.json").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Fail as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
