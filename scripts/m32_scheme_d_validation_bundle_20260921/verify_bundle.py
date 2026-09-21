#!/usr/bin/env python3
"""Independent verifier for the RNG-free M3.2 Scheme D validation bundle.

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
PROTOCOL_SHA = {PROTOCOL: "8b9e36c3767f5550607901b6410553fdab6d0b0c70e8d4c632e82eed0b59d7ac",
                CORRECTION: "2e925ebaa5beea9a3d04e5c687406642d5305053a639edb16b27d3e970495163"}
SECTION2 = {"trader/cognition/m32_search.py": "1081e3482c0d709b81b4b93db2fa94437ebead2e4bc92c78b9b359f7030a827b",
            "trader/cognition/m32_protocol.py": "ade9d184c8e84a2e0be2219c106e6b77403089637dabd19f33bd34995be1e3bd",
            "trader/cognition/m32_correction.py": "67a617be122c05616aa653cacc501f8e1658e493e659e9530e25c48153f10db7"}
HARNESS = {"trader/cognition/m32_scheme_d_validation.py": "7d30fa8b80727485c8e1efe34128c5c94222598becfd24f8aebbae4b51928f61"}
EXPECTED_FILES = {"build_bundle.py", "verify_bundle.py", "requirements.lock", "artifacts/config.json",
                  "artifacts/membership_table.json", "artifacts/effect_assignment_table.json", "artifacts/loadings.json",
                  "artifacts/covariance_psd.json", "artifacts/environment_lock.json", "artifacts/preflight_report.json",
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
    need(manifest["schema"] == "m3.2-scheme-d-validation-bundle-manifest.v1", "manifest schema")
    need(manifest["frozen_truth_commit"] == FROZEN_TRUTH_COMMIT and manifest["baseline_commit"] == BASELINE_COMMIT, "commits")
    need(manifest["no_rng_no_seed_generation_no_worlds_no_search_no_gate2"] is True, "manifest flags")
    listed = {str(Path(k).relative_to("scripts/m32_scheme_d_validation_bundle_20260921")): v
              for k, v in manifest["files_sha256"].items()}
    need(set(listed) == EXPECTED_FILES, f"bundle file set differs: {set(listed) ^ EXPECTED_FILES}")
    for rel, h in listed.items():
        need(sha(HERE / rel) == h, f"bundle file drift {rel}")
        tracked = git("ls-files", "--error-unmatch", str((HERE / rel).relative_to(ROOT))).returncode == 0
        need(tracked, f"bundle file not tracked in git {rel}")
    pinned = manifest["pinned_inputs_sha256"]
    for rel, h in {**SECTION2, **HARNESS, **PROTOCOL_SHA}.items():
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
    need(a["protocol_sha256"] == PROTOCOL_SHA[PROTOCOL] and a["correction_sha256"] == PROTOCOL_SHA[CORRECTION]
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
    need(c["statistic"]["sha256"] == SECTION2["trader/cognition/m32_search.py"] and c["statistic"]["optimized_path"] is None
         and c["statistic"]["function"] == "_metric", "statistic binding")
    need({i["id"] for i in c["interpretations_flagged_for_owner_review"]} == {f"I{i}" for i in range(1, 9)}, "interpretations")
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
    need(all(r["result"] == "PASS" for r in recorded["results"]) and recorded["checks_total"] == len(recorded["results"]) == 22,
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


def main() -> int:
    report: dict = {"schema": "m3.2-scheme-d-validation-bundle-verification.v1", "independent_implementation": True,
                    "rng_constructed": False}
    for step in (check_git_and_hashes, check_tables, check_psd, check_config, check_environment,
                 check_isolation_and_rng, check_preflight):
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
