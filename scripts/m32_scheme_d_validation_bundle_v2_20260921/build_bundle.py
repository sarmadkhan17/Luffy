#!/usr/bin/env python3
"""Deterministically build the RNG-free M3.2 validation bundle artifacts.

Everything is typed from the protocol text here; it imports neither the runner
nor the pinned harness.  No random generator or seed sequence is constructed.
Order: artifacts -> preflight report (separate step) -> BUNDLE_MANIFEST.json (--manifest).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import platform
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ART = HERE / "artifacts"
FREEZE_COMMIT = "7279ce0e4b9e245a00a45e69c6914d5d47d32903"
BASELINE_COMMIT = "ab9e39683f5e9f756fc7ef6690133ecb5b608f64"
PROTOCOL = "docs/superpowers/specs/2026-09-19-m32-scheme-d-actual-statistic-validation-protocol.md"
CORRECTION = "docs/superpowers/specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md"
ADDENDUM = "docs/superpowers/specs/2026-09-21-m32-scheme-d-owner-clarifications-i1-i8.md"
V1_MANIFEST = "scripts/m32_scheme_d_validation_bundle_20260921/BUNDLE_MANIFEST.json"
V1_SHA256 = "0b42c6a38c3329eff6ec760176b682003ff993b0976d45588f72eebb4be78a3e"
PINNED = {
    "trader/cognition/m32_search.py": "1081e3482c0d709b81b4b93db2fa94437ebead2e4bc92c78b9b359f7030a827b",
    "trader/cognition/m32_protocol.py": "ade9d184c8e84a2e0be2219c106e6b77403089637dabd19f33bd34995be1e3bd",
    "trader/cognition/m32_correction.py": "67a617be122c05616aa653cacc501f8e1658e493e659e9530e25c48153f10db7",
    "trader/cognition/m32_scheme_d_validation.py": "7d30fa8b80727485c8e1efe34128c5c94222598becfd24f8aebbae4b51928f61",
}
STATE_T, LINKED_T = 0.6744897501960817, 0.8416212335729143
N1_NULL_MAD = 0.47472299235208826
H = 384
INJECTIONS = {
    101: [0], 102: [128], 103: [320],
    104: [0, 33, 130, 163, 260, 293, 326, 359],
    105: sorted(x for c in range(4) for x in (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)),
    106: list(range(0, 8)) + list(range(136, 144)) + list(range(272, 280)) + list(range(344, 352)),
    107: (list(range(0, 24)) + list(range(136, 160)) + list(range(272, 288)) + list(range(288, 296))
          + list(range(320, 336)) + list(range(344, 352))),
}
OR = {101: "3", 103: "3", 104: "2", 105: "2", 106: "2", 107: "3/2"}
SHIFT = {102: "1", 104: "1/2", 105: "1/2", 106: "1/2", 107: "1/4"}
ROLE = {101: "acceptance", 102: "acceptance", 103: "acceptance", 104: "acceptance", 105: "acceptance",
        106: "acceptance", 107: "sensitivity_only"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def fs(x: Fraction) -> str:
    return f"{x.numerator}/{x.denominator}"


def kind_label(h: int) -> tuple[str, str]:
    if h < 128:
        return "state", "case_kind"
    if h < 256:
        return "state", "persistence"
    return ("transition", "continuation") if h < 320 else ("sequence", "continuation")


def membership_table() -> dict:
    rows = []
    for h in range(H):
        kind, label = kind_label(h)
        thr = STATE_T if h < 256 else LINKED_T
        rows.append({"hypothesis": h, "kind": kind, "label": label,
                     "outcome_column": "persistence" if label == "persistence" else "categorical",
                     "threshold": repr(thr), "threshold_binary64_hex": float(thr).hex(),
                     "cluster": h % 32, "sector": "A" if h % 32 < 16 else "B"})
    return {"schema": "m3.2-scheme-d-hypothesis-membership-table.v1", "hypotheses": H, "rows": rows,
            "ranges": {"case_kind": [0, 127], "persistence": [128, 255], "transition": [256, 319],
                       "sequence": [320, 383]},
            "cluster_rule": "h mod 32", "sector_rule": "A if cluster < 16 else B",
            "membership_rule": "M_h(r) = 1{Z_h(r) + s(r) > tau_h}; Z drawn independently per unique row (protocol 6.1)"}


def effect_table() -> dict:
    out = []
    for code, targets in INJECTIONS.items():
        entries = []
        for h in sorted(targets):
            kind, label = kind_label(h)
            if label == "persistence":
                entries.append({"hypothesis": h, "target": "persistence", "effect": "shift",
                                "magnitude_null_mad_units": SHIFT[code]})
            else:
                odds = Fraction(OR[code])
                p2 = Fraction(1, 10)
                p2n = odds * p2 / (1 - p2 + odds * p2)
                entries.append({"hypothesis": h, "target": "categorical", "effect": "odds_ratio", "odds_ratio": fs(odds),
                                "target_class_index": 2, "base_class2_probability": "1/10",
                                "new_class2_probability": fs(p2n),
                                "switch_probability": fs((p2n - p2) / (1 - p2))})
        counts = {}
        for h in targets:
            counts[kind_label(h)[1]] = counts.get(kind_label(h)[1], 0) + 1
        out.append({"scenario_code": code, "scenario": f"P{code - 100}", "role": ROLE[code], "size": len(targets),
                    "targets": sorted(targets), "targets_by_label": counts, "assignments": entries,
                    "application_order": "ascending hypothesis index; persistence shifts deterministic (no RNG); "
                                         "categorical switches use injection stream 3 uniforms"})
    return {"schema": "m3.2-scheme-d-effect-assignment-table.v1", "base_dgp": "N1", "scenarios": out,
            "null_mad_reference": {"N1": repr(N1_NULL_MAD), "gaussian": repr(STATE_T)}}


# ------------------------------------------------------------------ correlation structures
def structure(c: int) -> dict:
    """Exact factor form: Sigma = sum_k w_k s_k s_k^T + d I, all w_k >= 0, d > 0, unit diagonal."""
    if c == 0:
        return {"factors": [], "residual": Fraction(1)}
    if c in (1, 2):
        return {"factors": [(Fraction(3, 10) if c == 1 else Fraction(7, 10), [1] * H)],
                "residual": Fraction(7, 10) if c == 1 else Fraction(3, 10)}
    if c == 3:
        f = [(Fraction(1, 10), [1] * H)]
        f += [(Fraction(3, 5), [1 if h % 32 == k else 0 for h in range(H)]) for k in range(32)]
        return {"factors": f, "residual": Fraction(3, 10)}
    return {"factors": [(Fraction(3, 20), [1] * H),
                        (Fraction(9, 20), [1 if h % 32 < 16 else -1 for h in range(H)])],
            "residual": Fraction(2, 5)}


def exact_entry(c: int, i: int, j: int) -> Fraction:
    if i == j:
        return Fraction(1)
    if c == 0:
        return Fraction(0)
    if c == 1:
        return Fraction(3, 10)
    if c == 2:
        return Fraction(7, 10)
    if c == 3:
        return Fraction(1, 10) + (Fraction(3, 5) if i % 32 == j % 32 else Fraction(0))
    return Fraction(3, 5) * (Fraction(1, 4) + (1 if (i % 32 < 16) == (j % 32 < 16) else -1) * Fraction(3, 4))


def matrix(c: int) -> np.ndarray:
    m = np.empty((H, H), dtype=np.float64)
    for i in range(H):
        for j in range(H):
            e = exact_entry(c, i, j)
            m[i, j] = e.numerator / e.denominator
    return m


def factor_covariance_exact_matches(c: int) -> bool:
    """Sigma from the factor form equals the entry rule exactly, for every one of the 384x384 entries."""
    s = structure(c)
    for i in range(H):
        for j in range(H):
            total = sum((w * v[i] * v[j] for w, v in s["factors"]), Fraction(0))
            if i == j:
                total += s["residual"]
            if total != exact_entry(c, i, j):
                return False
    return True


def loadings() -> dict:
    root3_2 = float(np.sqrt(3.0) / 2.0)
    return {"schema": "m3.2-scheme-d-latent-loadings.v1", "family_size": H, "codes": {
        "C0": {"name": "independent", "construction": "Z_h = e_h", "factors": [], "residual_variance": "1/1"},
        "C1": {"name": "equicorrelated", "construction": "Z_h = sqrt(3/10) f + sqrt(7/10) e_h",
               "factors": [{"name": "f", "weight": "3/10", "loading_signs": "all +"}], "residual_variance": "7/10"},
        "C2": {"name": "equicorrelated", "construction": "Z_h = sqrt(7/10) f + sqrt(3/10) e_h",
               "factors": [{"name": "f", "weight": "7/10", "loading_signs": "all +"}], "residual_variance": "3/10"},
        "C3": {"name": "clustered", "construction": "Z_h = sqrt(1/10) g + sqrt(3/5) f_{c(h)} + sqrt(3/10) e_h",
               "factors": [{"name": "g", "weight": "1/10", "loading_signs": "all +"},
                           {"name": "f_c (32 cluster factors)", "weight": "3/5", "loading_signs": "indicator of h mod 32 = c"}],
               "residual_variance": "3/10"},
        "C4": {"name": "signed sectors", "construction": "Z_h = sqrt(3/5) (l_h . f) + sqrt(2/5) e_h, f in R^2",
               "lambda": "3/5", "theta_degrees": 60,
               "sector_A_loading_exact": ["1/2", "sqrt(3)/2"], "sector_B_loading_exact": ["1/2", "-sqrt(3)/2"],
               "sector_A_loading_binary64": [float(0.5).hex(), float(root3_2).hex()],
               "sector_B_loading_binary64": [float(0.5).hex(), float(-root3_2).hex()],
               "factors": [{"name": "f1", "weight": "3/20", "loading_signs": "all +"},
                           {"name": "f2", "weight": "9/20", "loading_signs": "+ for h mod 32 < 16 else -"}],
               "residual_variance": "2/5"}}}


def covariance_psd() -> dict:
    out = {"schema": "m3.2-scheme-d-covariance-psd-certificates.v1", "family_size": H, "codes": {}}
    for c in range(5):
        s, m = structure(c), matrix(c)
        d = s["residual"]
        variances = {sum((w * v[h] ** 2 for w, v in s["factors"]), Fraction(0)) + d for h in range(H)}
        variance = variances.pop() if len(variances) == 1 else Fraction(-1)
        eig = np.linalg.eigvalsh(m)
        np.linalg.cholesky(m)                       # raises on non-PD
        loading = np.array([[np.sqrt(0.6) * 0.5, np.sqrt(0.6) * s_] for s_ in
                            [np.sqrt(3.0) / 2.0 if h % 32 < 16 else -np.sqrt(3.0) / 2.0 for h in range(H)]])
        impl = (loading @ loading.T + 0.4 * np.eye(H)) if c == 4 else None
        out["codes"][f"C{c}"] = {
            "entry_rule": {"diagonal": "1"} | {
                0: {"off_diagonal": "0"}, 1: {"off_diagonal": "3/10"}, 2: {"off_diagonal": "7/10"},
                3: {"same_cluster": "7/10", "cross_cluster": "1/10"},
                4: {"same_sector": "3/5", "cross_sector": "-3/10"}}[c],
            "factor_count": len(s["factors"]), "residual_variance": fs(d),
            "unit_variance_identity": fs(variance),
            "unit_variance_definition": "for every h: sum_k w_k s_k[h]^2 + d == 1 (exact)",
            "exact_factor_form_reproduces_all_384x384_entries": factor_covariance_exact_matches(c),
            "psd_proof": ("Sigma = d*I + sum_k w_k s_k s_k^T with every w_k >= 0 and d > 0, so Sigma - d*I is a nonnegative "
                          "combination of rank-one Gram matrices (PSD) and Sigma >= d*I > 0. The factor count is below 384, so "
                          "Sigma - d*I is singular and lambda_min(Sigma) = d exactly."),
            "exact_minimum_eigenvalue": fs(d),
            "numeric_minimum_eigenvalue": f"{eig[0]:.10f}", "numeric_maximum_eigenvalue": f"{eig[-1]:.10f}",
            "numeric_cholesky_succeeded": True,
            "matrix_float64_row_major_sha256": hashlib.sha256(m.astype("<f8").tobytes()).hexdigest(),
            "implementation_loading_vs_exact_max_abs_error": (float(np.max(np.abs(impl - m))) if impl is not None else None),
        }
    return out


# ------------------------------------------------------------------ config
def config() -> dict:
    return {
        "schema": "m3.2-scheme-d-validation-config.v2",
        "bundle_revision": 2,
        "supersedes": {"bundle_manifest": V1_MANIFEST, "bundle_sha256": V1_SHA256},
        "status": "FROZEN_CONFIG_NOT_AUTHORIZED_TO_RUN",
        "authority": {"protocol": PROTOCOL, "protocol_sha256": sha(ROOT / PROTOCOL), "correction": CORRECTION,
                      "correction_sha256": sha(ROOT / CORRECTION), "clarification_addendum": ADDENDUM,
                      "clarification_addendum_sha256": sha(ROOT / ADDENDUM), "baseline_commit": BASELINE_COMMIT,
                      "frozen_truth_commit": FREEZE_COMMIT},
        "master_seed": 2026091902,
        "rng": {"bit_generator": "numpy.random.PCG64", "seeding": "numpy.random.SeedSequence([master, phase, correlation, dgp, world, stream])",
                "generator": "numpy.random.Generator", "constructed_by": "runner/scheme_d_runner.py::SeedRegistry.generator only"},
        "seed_map": {"phase_code": {"null": 10, "power": 20, "checks": 30, "benchmark": 90},
                     "correlation_code": {"C0": 0, "C1": 1, "C2": 2, "C3": 3, "C4": 4},
                     "dgp_code_phase_10": {"N0": 0, "N1": 1, "N2": 2, "N3": 3},
                     "dgp_code_phase_20": {f"P{k}": 100 + k for k in range(1, 8)},
                     "dgp_code_phase_30": "scenario code of the cell being checked",
                     "world_index": {"null": [0, 1999], "power": [0, 999], "checks": [0, 0]},
                     "stream_code": {"data": 0, "membership": 1, "permutation": 2, "injection": 3, "bootstrap": 4},
                     "bootstrap_stream_phase": 30,
                     "benchmark_isolation": "phase 90 only; phases 10/20/30 refused outside an authorized validation run",
                     "distinct_tuples": {"null": 4 * 5 * 2000 * 3, "power": 7 * 5 * 1000 * 4, "checks_bootstrap": 6 * 5,
                                         "total": 4 * 5 * 2000 * 3 + 7 * 5 * 1000 * 4 + 6 * 5},
                     "tuple_materialisation": "none in this bundle; the runner registry rejects duplicates at run time"},
        "environment": {"python": "3.12.3", "numpy": "2.5.2", "OPENBLAS_NUM_THREADS": "1"},
        "design": {"blocks": 48, "block_days": 28, "symbols": 16, "keys": 5, "rows_per_block": 80, "rows_per_world": 3840,
                   "linked_copy_of_key_4": "moves with its block; excluded from every statistic, floor and support count",
                   "quarter": "1 + (b mod 4)", "regime": "(b // 3) mod 2",
                   "strata_sizes": {"(1,0)": 8, "(1,1)": 4, "(2,0)": 4, "(2,1)": 8, "(3,0)": 8, "(3,1)": 4, "(4,0)": 4, "(4,1)": 8},
                   "missing_fraction_class_edges": [[0, 0.2], [0.2, 0.4], [0.4, 0.6], [0.6, 1]],
                   "class_rule": "integer counts: class = [5m >= T] + [5m >= 2T] + [5m >= 3T], m masked of T = 80 unique rows; "
                                 "half-open edges, no binary-float fraction",
                   "n0_orbit": "(8!)^4 * (4!)^4"},
        "family": {"size": 384, "ranges": {"case_kind": [0, 127], "persistence": [128, 255], "transition": [256, 319],
                                            "sequence": [320, 383]},
                   "thresholds": {"state": repr(STATE_T), "linked": repr(LINKED_T)}, "clusters": 32, "sectors": 2,
                   "table": "artifacts/membership_table.json"},
        "statistic": {"source": "trader/cognition/m32_search.py", "function": "_metric",
                      "sha256": PINNED["trader/cognition/m32_search.py"], "path": "reference_metric_import",
                      "optimized_path": None,
                      "note": "The runner imports the production _metric. No vectorized rewrite is included; the full protocol "
                              "is not runnable at reference speed and validation mode refuses without a measured benchmark estimate."},
        "sampling": {"permutations_per_world": 767999, "with_replacement": True, "identity_included": True,
                     "replacement_of_failed_draws": False, "movable_strata_only": True,
                     "tie_rule": "T* >= T - 1e-12 * max(1, |T|)", "p_value": "(1 + X) / (B + 1)"},
        "multiplicity": {"method": "BH step-up", "q": "1/20", "m": 384, "refused_stay_in_denominator": True,
                         "refused_p_value": None,
                         "exact_rule": "reject the k* smallest, k* = max k with 7680*(1+X_(k)) <= k*(B+1) (integer arithmetic)"},
        "correlations": "artifacts/loadings.json, artifacts/covariance_psd.json",
        "dgp": {"N0": {"persistence": "gaussian", "categorical": ["1/3", "1/3", "1/3"], "missingness": "none"},
                "N1": {"persistence": "t3/sqrt(3) + common jump p=0.10 x 2*t3/sqrt(3)", "categorical": ["13/20", "1/4", "1/10"],
                       "missingness": "none"},
                "N2": {"persistence": "gaussian", "categorical": ["1/3", "1/3", "1/3"],
                       "coverage": {"classes": 4, "rates": ["8/100", "3/10", "1/2", "18/25"]},
                       "membership_shift_by_realized_class": ["0", "2/5", "4/5", "6/5"],
                       "persistence_shift_null_mad_units_by_realized_class": ["0", "1/2", "1", "3/2"]},
                "N3": {"persistence": "t3/sqrt(3) + common jump", "categorical": ["13/20", "1/4", "1/10"],
                       "censoring": "L = min(G-1, 5), G ~ Geometric(1/2) on {1,2,...}; keys 5-L..4 masked",
                       "membership_shift_by_quarter": ["-4/5", "-1/5", "2/5", "9/10"], "membership_shift_regime1": "6/5",
                       "persistence_shift_null_mad_units_by_quarter": ["9/10", "-1/2", "3/10", "-1"],
                       "persistence_shift_regime1": "3/2"}},
        "null_mad": {"gaussian": repr(STATE_T), "t3_common_jump": repr(N1_NULL_MAD),
                     "t3_derivation": "deterministic Gauss-Legendre + bisection of the frozen mixture; verified by preflight and the independent verifier"},
        "power_worlds": {"base_dgp": "N1", "pairing": "pre-injection family from streams 0 and 1 of tuple (20,c,P,w); injection from stream 3"},
        "injections": "artifacts/effect_assignment_table.json",
        "consumption_order": ["data: coverage classes / masks / lags", "membership: Z", "data: common jumps",
                              "data: persistence noise", "data: categorical labels", "injection (power only)",
                              "observed statistics", "permutation"],
        "cells": {"null": {"count": 20, "worlds_each": 2000}, "power": {"count": 35, "worlds_each": 1000},
                  "acceptance_partition": {"null": 20, "p1_p3_calibration": 10, "p2_p4_p5_p6_power": 20, "p7_sensitivity": 5},
                  "worlds_total": 75000},
        "refusal": {"world_level": ["frozen mismatch", "invalid certificate", "between-block dependence",
                                    "footprint or link crossing", "unconditioned drift", "outcome-dependent missingness",
                                    "omitted confounder", "clock or link violation", "family size != 384",
                                    "movable blocks < 24 or movable strata < 3", "orbit < 1,536,000 or 2/|G| > 1/768000",
                                    "zero tested hypotheses (owner decision I3)"],
                    "hypothesis_level_floors": {"state": {"matched": 12, "observed": 8, "episodes": 3},
                                                "transition": {"matched": 8, "episodes": 3},
                                                "sequence": {"matched": 8, "episodes": 3}},
                    "blocking_rate": "1/100 of worlds; 1/100 of (world, hypothesis) pairs over 384 x non-refused worlds"},
        "acceptance": {"wilson_z": 1.959964, "type_one": {"point_max": 0.05, "wilson_upper_max": 0.06},
                       "fdr": {"mean_max": 0.05, "bootstrap_upper_max": 0.06, "resamples": 10000, "order_statistic": 9750,
                               "seed": "[2026091902, 30, correlation_code, scenario_code, 0, 4]"},
                       "calibration": {"t": ["1/100", "5/100", "1/10"], "clopper_pearson_alpha": "1/6000",
                                       "hypothesis_for_world": "w mod 384",
                                       "refused_h_of_w": "world excluded from the ECDF denominator (owner decision I6)"},
                       "power": {"P1_P3": {"wilson_lower_min": 0.80},
                                 "P4": [{"at_least": 1, "min": 0.90}, {"at_least": 4, "min": 0.75}],
                                 "P5_P6": [{"at_least": 1, "min": 0.95}, {"at_least": 16, "min": 0.75}]},
                       "p7": "descriptive only"},
        "owner_decisions": {
            "status": "DECIDED_2026-09-21", "addendum": ADDENDUM,
            "decisions": [
                {"id": "I1", "decision": "dependence group = block index; episode = unique row/episode identity",
                 "implementation": "unchanged from v1", "changed_vs_v1": False},
                {"id": "I2", "decision": "permutation-invariant support accounting; observed support sufficient for the statistic to be defined",
                 "implementation": "unchanged from v1: matched/episodes/groups invariant; observed floor on the observed arrangement and on the worst-case "
                                   "slot bound; transition/sequence need worst-case observed >= 1", "changed_vs_v1": False},
                {"id": "I3", "decision": "hypothesis-level refusal; a world with zero tested hypotheses is a refused world, excluded from "
                                         "Type-I/FDR and failed for power",
                 "implementation": "world_receipt returns world_refused=[no_tested_hypotheses]; counted in the world-refusal blocker",
                 "changed_vs_v1": True},
                {"id": "I4", "decision": "degenerate = the hypothesis outcome column is constant; an observed zero lift is valid evidence and is never refused",
                 "implementation": "unchanged from v1 (constant-column refusal only); recorded in the addendum", "changed_vs_v1": False},
                {"id": "I5", "decision": "sampler order frozen through the hash-bound runner",
                 "implementation": "consumption_order below plus runner/scheme_d_runner.py hash", "changed_vs_v1": False},
                {"id": "I6", "decision": "marginal-calibration worlds where h(w) is refused are excluded from the empirical CDF denominator",
                 "implementation": "acceptance.calibration_pass drops None entries", "changed_vs_v1": True},
                {"id": "I7", "decision": "exact integer BH comparison", "implementation": "unchanged from v1 (exact_bh)", "changed_vs_v1": False},
                {"id": "I8", "decision": "reference _metric path only for the phase-90 benchmark; full-run optimized implementation needs the "
                                         "section 3 exact-equivalence proof and a separate benchmark",
                 "implementation": "statistic.path = reference_metric_import; validation mode refuses without a measured runtime estimate",
                 "changed_vs_v1": False},
                {"id": "class_edge", "decision": "implement protocol section 5 half-open coverage classes without binary-float boundary ambiguity",
                 "implementation": "integer-count rule in runner.missing_class", "changed_vs_v1": True}]},
        "frozen_truth_package": {"freeze_json": "scripts/m32_scheme_d_pre_rng_truth_20260921/FREEZE.json",
                                 "consumed_unchanged": True},
        "pinned_sources_sha256": PINNED,
    }


# ------------------------------------------------------------------ environment lock
def environment_lock() -> dict:
    dist = md.distribution("numpy")
    record = (Path(dist.locate_file("")) / f"numpy-{dist.version}.dist-info" / "RECORD")
    cfg = {}
    try:
        cfg = np.show_config(mode="dicts")
    except Exception:
        pass
    blas = cfg.get("Build Dependencies", {}).get("blas", {}) if isinstance(cfg, dict) else {}
    freeze = sorted(f"{d.metadata['Name']}=={d.version}" for d in md.distributions())
    return {"schema": "m3.2-scheme-d-environment-lock.v1", "python": platform.python_version(),
            "implementation": platform.python_implementation(), "numpy": np.__version__,
            "numpy_dist_record_sha256": sha(record) if record.is_file() else None,
            "blas": {"name": blas.get("name"), "version": blas.get("version")},
            "OPENBLAS_NUM_THREADS": "1", "runner_third_party_imports": ["numpy"],
            "requirements_lock": "requirements.lock", "installed_distribution_count": len(freeze),
            "installed_distributions_sha256": hashlib.sha256("\n".join(freeze).encode()).hexdigest(),
            "platform": {"system": platform.system(), "machine": platform.machine()},
            "note": "gates: python, numpy, OPENBLAS_NUM_THREADS. blas/platform/installed set are informational locks."}


def build_artifacts() -> None:
    ART.mkdir(exist_ok=True)
    for name, obj in (("membership_table.json", membership_table()), ("effect_assignment_table.json", effect_table()),
                      ("loadings.json", loadings()), ("covariance_psd.json", covariance_psd()),
                      ("config.json", config()), ("environment_lock.json", environment_lock())):
        (ART / name).write_text(canon(obj), encoding="utf-8")
    (HERE / "requirements.lock").write_text("# pinned runner environment (protocol section 14 item 7)\n"
                                            f"python=={platform.python_version()}\nnumpy==2.5.2\n"
                                            "env OPENBLAS_NUM_THREADS=1\n", encoding="utf-8")


BUNDLE_FILES = ("build_bundle.py", "verify_bundle.py", "requirements.lock", "artifacts/config.json",
                "artifacts/membership_table.json", "artifacts/effect_assignment_table.json", "artifacts/loadings.json",
                "artifacts/covariance_psd.json", "artifacts/environment_lock.json", "artifacts/preflight_report.json",
                "runner/scheme_d_runner.py", "runner/acceptance.py", "runner/preflight.py")


def write_manifest() -> None:
    rel = lambda p: str((HERE / p).relative_to(ROOT))
    freeze = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921"
    fz = json.loads((freeze / "FREEZE.json").read_text())
    files = {rel(p): sha(HERE / p) for p in BUNDLE_FILES}
    pinned = {**{p: sha(ROOT / p) for p in PINNED}, PROTOCOL: sha(ROOT / PROTOCOL), CORRECTION: sha(ROOT / CORRECTION),
              ADDENDUM: sha(ROOT / ADDENDUM)}
    manifest = {
        "schema": "m3.2-scheme-d-validation-bundle-manifest.v2", "bundle_revision": 2,
        "supersedes": {"bundle_manifest": V1_MANIFEST, "bundle_sha256": V1_SHA256,
                       "v1_manifest_file_sha256": sha(ROOT / V1_MANIFEST)},
        "clarification_addendum": {"path": ADDENDUM, "sha256": sha(ROOT / ADDENDUM)},
        "status": "FROZEN_PRE_RUN_BUNDLE_NOT_AUTHORIZED_TO_RUN",
        "no_rng_no_seed_generation_no_worlds_no_search_no_gate2": True,
        "frozen_truth_commit": FREEZE_COMMIT, "baseline_commit": BASELINE_COMMIT,
        "frozen_truth_package": {"package_dir": "scripts/m32_scheme_d_pre_rng_truth_20260921/package",
                                 "package_sha256": fz["package_sha256"],
                                 "freeze_json": "scripts/m32_scheme_d_pre_rng_truth_20260921/FREEZE.json",
                                 "freeze_json_sha256": sha(freeze / "FREEZE.json"),
                                 "rows": fz["checks"]["rows"], "unresolved_rows": fz["checks"]["unresolved_rows"]},
        "files_sha256": files, "pinned_inputs_sha256": pinned,
        "implementation_hashes_protocol_section_2": {p: pinned[p] for p in list(PINNED)[:3]},
    }
    (HERE / "BUNDLE_MANIFEST.json").write_text(canon(manifest), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true", help="write BUNDLE_MANIFEST.json (after preflight_report.json exists)")
    a = ap.parse_args()
    write_manifest() if a.manifest else build_artifacts()
    print("manifest written" if a.manifest else "artifacts written")
