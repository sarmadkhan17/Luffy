#!/usr/bin/env python3
"""Frozen M3.2 Scheme D actual-statistic validation runner (protocol 2026-09-19 + pre-RNG correction).

Bundle revision 2: integer half-open coverage classes (protocol section 5), a world with zero tested
hypotheses is a refused world (owner decision I3); see the 2026-09-21 owner-clarification addendum.

Bundle revision 3: the statistic path is the exact-equivalent ``FastMetric`` (bit-identical to the production
``_metric`` on 7,592,064 recorded comparisons).  It can only be used after ``equivalence_gate_static`` (hashes of the
reference, the optimized module, the suite and its results, and the recorded equivalence receipt) and, in RNG modes,
``assert_equivalence_gate`` (bundle manifest, pinned inputs, recorded preflight PASS) have passed.  The reference
path stays available for the benchmark's live bit-identity check and for preflight.

Modes
  preflight   RNG-free.  Runs the deterministic check suite; constructs no generator.
  benchmark   Phase-90 tiny noninferential workload.  Needs an owner authorization file.
  validation  Full protocol run (phases 10/20/30).  Needs an owner authorization file.

Every mode first verifies the bundle manifest, the frozen truth package and the pinned
sources.  A random generator can only be built by ``SeedRegistry.generator`` after a
matching authorization was verified; there is no other RNG construction in this file.
The reference statistic is the imported production ``_metric`` (no reimplementation).
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import sys
import time
from collections import Counter
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent
ROOT = BUNDLE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402  (OPENBLAS_NUM_THREADS must already be 1; verified in assert_environment)

from trader.cognition import m32_scheme_d_validation as V  # noqa: E402
from trader.cognition.m32_search import _metric  # noqa: E402
from trader.cognition.m32_fast_metric import FastMetric  # noqa: E402
sys.path.insert(0, str(HERE))
import acceptance  # noqa: E402

MASTER_SEED = 2026091902
PHASE = {"null": 10, "power": 20, "checks": 30, "benchmark": 90}
STREAM = {"data": 0, "membership": 1, "permutation": 2, "injection": 3, "bootstrap": 4}
B = V.FULL_PERMUTATIONS
HYPOTHESES, BLOCKS, ROWS, RPB = V.HYPOTHESES, V.BLOCKS, V.ROWS, V.ROWS_PER_BLOCK
MODES_WITH_RNG = ("benchmark", "validation")
DENYLIST_MODULES = ("trader.kernel", "trader.data", "trader.core", "trader.engine", "trader.dashboard",
                    "trader.brain", "trader.research", "trader.strategy", "sqlite3", "requests", "httpx",
                    "aiohttp", "urllib3", "http.client", "ccxt", "websockets")
NULL_DGP = (0, 1, 2, 3)
SCENARIOS = tuple(range(101, 108))
RATE_BY_CLASS = (0.08, 0.30, 0.50, 0.72)
N2_MEMBERSHIP = (0.0, 0.4, 0.8, 1.2)
N2_PERSISTENCE = (0.0, 0.5, 1.0, 1.5)
N3_MEMBERSHIP_Q = (-0.8, -0.2, 0.4, 0.9)
N3_PERSISTENCE_Q = (0.9, -0.5, 0.3, -1.0)
N3_MEMBERSHIP_REGIME1, N3_PERSISTENCE_REGIME1 = 1.2, 1.5
GAUSSIAN_NULL_MAD = 0.6744897501960817


class IntegrityError(V.IntegrityError):
    pass


_GATE = {"open": False}
REFERENCE_DRAWS, FAST_DRAWS = 2, 300
REQUIRED_PREFLIGHT_CHECKS = ("fast_metric_hash_binding", "equivalence_receipt_recorded",
                             "fast_metric_live_equivalence_deterministic", "runner_fast_equals_reference_receipts",
                             "equivalence_gate_fails_closed", "benchmark_core_with_stub")


# --------------------------------------------------------------------------- environment / binding
def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_environment() -> dict:
    """Frozen interpreter/NumPy/BLAS metadata and the three protocol source hashes."""
    return V.verify_frozen_environment(ROOT)


def assert_isolation() -> None:
    loaded = [m for m in DENYLIST_MODULES if m in sys.modules or any(k.startswith(m + ".") for k in sys.modules)]
    if loaded:
        raise IntegrityError(f"import_isolation_violation:{','.join(sorted(loaded))}")


def verify_bundle(manifest_path: Path | None = None) -> dict:
    """Manifest self-consistency plus the frozen truth package hash; fail closed."""
    manifest_path = manifest_path or (BUNDLE / "BUNDLE_MANIFEST.json")
    if not manifest_path.is_file():
        raise IntegrityError("bundle_manifest_missing")
    manifest = json.loads(manifest_path.read_text())
    for rel, expected in manifest["files_sha256"].items():
        path = ROOT / rel
        if not path.is_file() or sha256_file(path) != expected:
            raise IntegrityError(f"bundle_hash_mismatch:{rel}")
    for rel, expected in manifest["pinned_inputs_sha256"].items():          # v3: pinned sources drift fail closed
        path = ROOT / rel
        if not path.is_file() or sha256_file(path) != expected:
            raise IntegrityError(f"pinned_input_hash_mismatch:{rel}")
    frozen = manifest["frozen_truth_package"]
    package = ROOT / frozen["package_dir"]
    if sha256_file(package / "manifest.sha256") != frozen["package_sha256"]:
        raise IntegrityError("truth_package_hash_mismatch")
    for line in (package / "manifest.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if sha256_file(package / name) != digest:
            raise IntegrityError(f"truth_package_file_mismatch:{name}")
    return manifest


def bundle_sha256() -> str:
    return sha256_file(BUNDLE / "BUNDLE_MANIFEST.json")


def equivalence_gate_static(root: Path = ROOT, config: dict | None = None, receipt_path: Path | None = None) -> dict:
    """Hash- and receipt-level gate for the optimized statistic; fails closed (no dependency on preflight)."""
    cfg = (config if config is not None else json.loads((BUNDLE / "artifacts/config.json").read_text()))["fast_metric"]
    keys = ("module", "suite", "benchmark_script", "equivalence_report", "benchmark_result", "artifact_hashes",
            "reference", "equivalence_test")
    paths = {}
    for key in keys:
        path = Path(root) / cfg[key]["path"]
        if not path.is_file() or sha256_file(path) != cfg[key]["sha256"]:
            raise IntegrityError(f"fast_metric_hash_mismatch:{key}")
        paths[key] = path
    registry = json.loads(paths["artifact_hashes"].read_text())["sha256"]
    for key in ("module", "suite", "benchmark_script", "equivalence_report", "benchmark_result", "reference"):
        if registry.get(cfg[key]["path"]) != cfg[key]["sha256"]:
            raise IntegrityError(f"fast_metric_registry_mismatch:{key}")
    report = json.loads(paths["equivalence_report"].read_text())
    want = cfg["equivalence"]
    if (report.get("comparisons"), report.get("statistic_vectors"), report.get("fixtures"), report.get("mismatches")) != \
            (want["comparisons"], want["statistic_vectors"], want["fixtures"], 0) or want["mismatches"] != 0:
        raise IntegrityError("fast_metric_equivalence_counts_invalid")
    if (report.get("quick") is not False or report.get("protocol_master_seed_used") is not False
            or report.get("test_seed") != want["test_seed"] or report.get("mismatch_details") != []
            or report.get("sections") != want["sections"]):
        raise IntegrityError("fast_metric_equivalence_report_invalid")
    receipt = json.loads((receipt_path or (BUNDLE / "artifacts/equivalence_receipt.json")).read_text())
    if (receipt.get("comparisons"), receipt.get("mismatches"), receipt.get("equivalence_report_sha256"),
            receipt.get("fast_metric_sha256"), receipt.get("reference_sha256"), receipt.get("suite_sha256")) != \
            (want["comparisons"], 0, cfg["equivalence_report"]["sha256"], cfg["module"]["sha256"],
             cfg["reference"]["sha256"], cfg["suite"]["sha256"]):
        raise IntegrityError("fast_metric_equivalence_receipt_mismatch")
    if Path(root).resolve() == ROOT.resolve():             # the code actually loaded is the pinned code
        for key, obj in (("module", FastMetric), ("reference", _metric)):
            if Path(inspect.getsourcefile(obj)).resolve() != (Path(root) / cfg[key]["path"]).resolve():
                raise IntegrityError(f"fast_metric_loaded_from_unpinned_file:{key}")
    return {"comparisons": want["comparisons"], "mismatches": 0, "module_sha256": cfg["module"]["sha256"],
            "reference_sha256": cfg["reference"]["sha256"], "suite_sha256": cfg["suite"]["sha256"]}


def open_statistic_gate(static: dict) -> None:
    if not isinstance(static, dict) or static.get("mismatches") != 0:
        raise IntegrityError("fast_metric_gate_requires_verified_equivalence")
    _GATE["open"] = True


def assert_equivalence_gate() -> dict:
    """RNG-mode gate: bundle manifest + pinned inputs + equivalence receipt + recorded preflight PASS."""
    verify_bundle()
    static = equivalence_gate_static()
    report = json.loads((BUNDLE / "artifacts/preflight_report.json").read_text())
    passed = {r["check"] for r in report.get("results", []) if r.get("result") == "PASS"}
    if (report.get("result") != "PASS" or report.get("rng_construction_attempts") != 0
            or report.get("checks_total") != len(report.get("results", [])) or not set(REQUIRED_PREFLIGHT_CHECKS) <= passed
            or report.get("checks_passed") != report.get("checks_total")):
        raise IntegrityError("recorded_preflight_not_passed")
    open_statistic_gate(static)
    return static


class Authorization:
    """Verified owner authorization; can only be built by ``authorize``."""
    _token = object()

    def __init__(self, token, mode: str, bundle: str):
        if token is not Authorization._token:
            raise IntegrityError("authorization_forged")
        self.mode, self.bundle = mode, bundle


def authorize(path: Path, mode: str) -> Authorization:
    if mode not in MODES_WITH_RNG:
        raise IntegrityError("mode_has_no_rng")
    verify_bundle()
    if not Path(path).is_file():
        raise IntegrityError("authorization_missing")
    record = json.loads(Path(path).read_text())
    if (record.get("schema") != "m3.2-scheme-d-owner-authorization.v1" or record.get("mode") != mode
            or record.get("bundle_sha256") != bundle_sha256() or record.get("authorized_by") != "owner"):
        raise IntegrityError("authorization_mismatch")
    return Authorization(Authorization._token, mode, record["bundle_sha256"])


# --------------------------------------------------------------------------- seed map (the only RNG construction)
class SeedRegistry:
    def __init__(self, authorization: Authorization):
        if not isinstance(authorization, Authorization):
            raise IntegrityError("authorization_required")
        self.mode = authorization.mode
        self.seen: set[tuple[int, ...]] = set()

    def generator(self, phase: int, correlation: int, dgp: int, world: int, stream: int):
        allowed = (PHASE["benchmark"],) if self.mode == "benchmark" else (10, 20, 30)
        if phase not in allowed:
            raise IntegrityError("phase_not_authorized")
        if stream not in STREAM.values() or (stream == STREAM["bootstrap"] and phase != PHASE["checks"]):
            raise IntegrityError("stream_not_permitted")
        key = (MASTER_SEED, phase, correlation, dgp, world, stream)
        if key in self.seen:
            raise IntegrityError("duplicate_seed_tuple")
        self.seen.add(key)
        return np.random.Generator(np.random.PCG64(np.random.SeedSequence(list(key))))


# --------------------------------------------------------------------------- world generation
def missing_class(masked: int, total: int) -> int:
    """Half-open classes [0,.2), [.2,.4), [.4,.6), [.6,1] from integer counts (no float fraction)."""
    return int(5 * masked >= total) + int(5 * masked >= 2 * total) + int(5 * masked >= 3 * total)


def membership_latents(rng, correlation: int) -> np.ndarray:
    return V._membership_latents(rng, correlation)          # pinned construction (hash-bound)


def draw_coverage(data_rng, dgp: int) -> np.ndarray:
    observed = np.ones((BLOCKS, V.SYMBOLS, V.KEYS), dtype=np.bool_)
    if dgp == 2:
        nominal = data_rng.integers(0, 4, size=BLOCKS)
        rates = np.asarray(RATE_BY_CLASS)[nominal]
        observed = data_rng.random(observed.shape) >= rates[:, None, None]
    elif dgp == 3:
        lags = np.minimum(data_rng.geometric(0.5, size=(BLOCKS, V.SYMBOLS)) - 1, 5)
        for b in range(BLOCKS):
            for s in range(V.SYMBOLS):
                if int(lags[b, s]):
                    observed[b, s, V.KEYS - int(lags[b, s]):] = False
    return observed


def membership_shift(dgp: int, classes: np.ndarray) -> np.ndarray:
    blocks = np.arange(BLOCKS)
    if dgp == 2:
        return np.asarray(N2_MEMBERSHIP)[classes]
    if dgp == 3:
        return np.asarray(N3_MEMBERSHIP_Q)[blocks % 4] + N3_MEMBERSHIP_REGIME1 * ((blocks // 3) % 2)
    return np.zeros(BLOCKS)


def generate_world(dgp: int, correlation: int, world_index: int, data_rng, member_rng) -> V.SyntheticWorld:
    """Section 8 consumption order: coverage -> membership -> jumps -> persistence -> categorical."""
    if dgp not in NULL_DGP:
        raise IntegrityError("unknown_dgp")
    observed = draw_coverage(data_rng, dgp)
    classes = np.asarray([missing_class(int((~observed[b]).sum()), V.SYMBOLS * V.KEYS) for b in range(BLOCKS)])
    latents = membership_latents(member_rng, correlation)
    shifts = np.repeat(membership_shift(dgp, classes), RPB)
    memberships = latents + shifts[:, None] > V.THRESHOLDS[None, :]
    del latents
    heavy = dgp in (1, 3)
    jumps = None
    if heavy:
        jumps = ((data_rng.random(BLOCKS) < 0.10) * (2.0 * data_rng.standard_t(3, size=BLOCKS) / np.sqrt(3.0)))
    persistence = (data_rng.standard_t(3, size=ROWS) / np.sqrt(3.0) if heavy else data_rng.standard_normal(ROWS))
    if heavy:
        persistence = persistence + np.repeat(jumps, RPB)
    if dgp == 2:
        persistence = persistence + np.repeat(np.asarray(N2_PERSISTENCE)[classes] * GAUSSIAN_NULL_MAD, RPB)
    elif dgp == 3:
        blocks = np.arange(BLOCKS)
        nuisance = np.asarray(N3_PERSISTENCE_Q)[blocks % 4] + N3_PERSISTENCE_REGIME1 * ((blocks // 3) % 2)
        persistence = persistence + np.repeat(nuisance * V.N1_NULL_MAD, RPB)
    probs = np.asarray((0.65, 0.25, 0.10) if heavy else (1 / 3, 1 / 3, 1 / 3))
    categorical = data_rng.choice(3, size=ROWS, p=probs)
    strata = tuple((1 + b % 4, int((b // 3) % 2), int(classes[b])) for b in range(BLOCKS))
    return V.SyntheticWorld(correlation, dgp, world_index, memberships, categorical, persistence,
                            observed.reshape(ROWS), strata, V.valid_metadata(strata))


def apply_injection(world: V.SyntheticWorld, scenario: int, injection_rng) -> V.SyntheticWorld:
    """Section 11.1: sequential in ascending h; shifts deterministic; OR switches use stream 3."""
    if scenario not in V.INJECTIONS:
        raise IntegrityError("unknown_injection_scenario")
    categorical, persistence = world.categorical.copy(), world.persistence.copy()
    for h in sorted(V.INJECTIONS[scenario]):
        matched = world.memberships[:, h]
        if V.HYPOTHESIS_TABLE[h].label == "persistence":
            persistence[matched] += V.PERSISTENCE_SHIFT_BY_SCENARIO[scenario] * V.N1_NULL_MAD
        else:
            odds = float(V.OR_BY_SCENARIO[scenario])
            base = 0.10
            target = odds * base / (1.0 - base + odds * base)
            switch_probability = (target - base) / (1.0 - base)
            switch = matched & (categorical != 2) & (injection_rng.random(ROWS) < switch_probability)
            categorical[switch] = 2
    return V.SyntheticWorld(world.correlation, world.dgp, world.world_index, world.memberships.copy(),
                            categorical, persistence, world.observed.copy(), world.block_strata,
                            dict(world.metadata))


# --------------------------------------------------------------------------- refusals (section 9)
SUPPORT = {"state": {"matched": 12, "observed": 8, "episodes": 3}, "transition": {"matched": 8, "episodes": 3},
           "sequence": {"matched": 8, "episodes": 3}}


def strata_all(strata) -> tuple[np.ndarray, ...]:
    grouped: dict[tuple, list[int]] = {}
    for block, key in enumerate(strata):
        grouped.setdefault(tuple(key), []).append(block)
    return tuple(np.asarray(grouped[k], dtype=np.int64) for k in sorted(grouped))


def hypothesis_refusals(world: V.SyntheticWorld) -> list[str | None]:
    """Reason code per hypothesis, or None when testable (all evaluated before any draw)."""
    obs = world.observed
    mask = obs.reshape(BLOCKS, RPB)
    if int(mask.sum(axis=1).min()) < 8:
        return ["block_below_8_observed_labels"] * HYPOTHESES
    worst = np.empty_like(mask)
    for group in strata_all(world.block_strata):
        worst[group] = mask[group].min(axis=0)
    worst_flat = worst.reshape(ROWS)
    constant = {"case_kind": len(np.unique(world.categorical[obs])) < 2,
                "persistence": len(np.unique(world.persistence[obs])) < 2}
    constant["continuation"] = constant["case_kind"]
    block_of = np.arange(ROWS) // RPB
    reasons: list[str | None] = []
    for hyp in V.HYPOTHESIS_TABLE:
        member = world.memberships[:, hyp.index]
        floors = SUPPORT[hyp.kind]
        matched = int(member.sum())
        observed = int((member & obs).sum())
        worst_observed = int((member & worst_flat).sum())
        groups = len(np.unique(block_of[member])) if matched else 0
        if constant[hyp.label]:
            reasons.append("degenerate_constant_outcome")
        elif matched < floors["matched"]:
            reasons.append("matched_below_floor")
        elif hyp.kind == "state" and observed < floors["observed"]:
            reasons.append("observed_below_floor")
        elif matched < floors["episodes"]:
            reasons.append("episodes_below_floor")
        elif groups < 3:
            reasons.append("fewer_than_3_dependence_groups")
        elif worst_observed < (floors.get("observed", 1)):
            reasons.append("worst_case_observed_below_floor")
        else:
            reasons.append(None)
    return reasons


# --------------------------------------------------------------------------- statistic, p-values, BH
def actual_statistics(world: V.SyntheticWorld, block_map, indices) -> np.ndarray:
    """Production ``_metric`` on the requested hypotheses only; nonfinite is an integrity stop."""
    rows, lookup = V._arranged_rows(world, block_map)
    row_ids = np.asarray([f"row-{i}" for i in range(ROWS)], dtype=object)
    out = np.full(HYPOTHESES, np.nan)
    for h in indices:
        hyp = V.HYPOTHESIS_TABLE[h]
        result = _metric(rows, set(row_ids[world.memberships[:, hyp.index]]), hyp.label, lookup)
        value = result.get("signed_effect")
        if value is None or not np.isfinite(value):
            raise IntegrityError(f"nonfinite_metric_h{h}")
        out[h] = value
    return out


def draw_block_map(rng, groups) -> np.ndarray:
    """One synchronized whole-block map; one uniform permutation per movable stratum, ascending order."""
    mapping = np.arange(BLOCKS, dtype=np.int64)
    for destinations in groups:
        if len(destinations) > 1:
            mapping[destinations] = rng.permutation(destinations)
    return mapping


def exact_bh(exceedances: list[int | None], draws: int = B) -> set[int]:
    """BH step-up q=1/20, m=384 always; refused (None) stay in the denominator.

    p=(1+X)/(B+1) <= k/(20*384)  <=>  7680*(1+X) <= k*(B+1)   (exact integers).
    """
    tested = sorted((x, i) for i, x in enumerate(exceedances) if x is not None)
    cutoff = 0
    for rank, (x, _) in enumerate(tested, 1):
        if 7680 * (1 + x) <= rank * (draws + 1):
            cutoff = rank
    return {i for _, i in tested[:cutoff]}


# --------------------------------------------------------------------------- truth tables / classification
def load_truth(scenario: int | None, dgp: int | None, correlation: int) -> list[dict]:
    package = BUNDLE.parents[0] / "m32_scheme_d_pre_rng_truth_20260921" / "package" / "tables"
    name = f"N{dgp}_C{correlation}" if scenario is None else f"P{scenario - 100}_C{correlation}"
    return json.loads((package / f"{name}.json").read_text())["records"]


def classify_rejections(rejected: set[int], signed: np.ndarray, truth: list[dict], injected: tuple[int, ...]) -> dict:
    v = td = attribution = 0
    injected_set = set(injected)
    for h in sorted(rejected):
        rec = truth[h]
        observed_sign = 1 if signed[h] > 0 else -1 if signed[h] < 0 else 0
        if rec["classification"] == "true_null":
            v += 1
        elif rec["classification"] == "analytic_non_null":
            if observed_sign != rec["sign"]:
                v += 1
            elif h in injected_set:
                td += 1
            else:
                attribution += 1
        else:
            raise IntegrityError("unclassified_truth_row")
    return {"R": len(rejected), "V": v, "true_discoveries": td, "attribution_errors": attribution}


# --------------------------------------------------------------------------- one world
def fast_vector(fm: FastMetric, block_map, tested) -> np.ndarray:
    """Optimized statistics on the tested hypotheses; nonfinite or untestable is an integrity stop (as the reference)."""
    signed, ok = fm.statistics(block_map)
    idx = np.asarray(tested, dtype=np.int64)
    bad = ~(ok[idx] & np.isfinite(signed[idx]))
    if bad.any():
        raise IntegrityError(f"nonfinite_metric_h{int(idx[int(np.argmax(bad))])}")
    out = np.full(HYPOTHESES, np.nan)
    out[idx] = signed[idx]
    return out


def statistic_function(world: V.SyntheticWorld, tested, statistic: str):
    if statistic == "reference":
        return lambda block_map: actual_statistics(world, block_map, tested)
    if statistic != "fast":
        raise IntegrityError("unknown_statistic_path")
    if not _GATE["open"]:
        raise IntegrityError("fast_metric_gate_closed")
    fm = FastMetric.from_world(world)
    return lambda block_map: fast_vector(fm, block_map, tested)


def world_receipt(world: V.SyntheticWorld, permutation_rng, phase: int, cell: str, tuples: dict, *,
                  draws: int, truth: list[dict], injected: tuple[int, ...], statistic: str = "fast",
                  block_maps=None) -> dict:
    try:
        orbit = V.validate_world_metadata(world.metadata, V.block_fingerprints(world))
    except V.WorldRefused as exc:
        receipt = {"schema": "m3.2-scheme-d-world-receipt.v1", "cell": cell, "phase": phase,
                   "world": world.world_index, "seed_tuples": tuples, "world_refused": list(exc.reasons)}
        receipt["sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return receipt
    reasons = hypothesis_refusals(world)
    tested = [h for h, r in enumerate(reasons) if r is None]
    if not tested:                                            # owner decision I3: zero tested hypotheses = refused world
        receipt = {"schema": "m3.2-scheme-d-world-receipt.v1", "cell": cell, "phase": phase,
                   "world": world.world_index, "seed_tuples": tuples, "world_refused": ["no_tested_hypotheses"],
                   "refusals": {str(h): r for h, r in enumerate(reasons) if r}}
        receipt["sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return receipt
    if block_maps is not None and len(block_maps) != draws:
        raise IntegrityError("block_map_count_mismatch")
    stat = statistic_function(world, tested, statistic)
    observed = stat(None)
    threshold = np.abs(observed) - V.TIE_REL_TOL * np.maximum(1.0, np.abs(observed))
    counts = np.zeros(HYPOTHESES, dtype=np.int64)
    groups = strata_all(world.block_strata)
    successful = 0
    for i in range(draws):
        values = stat(block_maps[i] if block_maps is not None else draw_block_map(permutation_rng, groups))
        counts[tested] += np.abs(values[tested]) >= threshold[tested]
        successful += 1
    if successful != draws:
        raise IntegrityError("failed_draw")
    exceed = [int(counts[h]) if reasons[h] is None else None for h in range(HYPOTHESES)]
    rejected = exact_bh(exceed, draws)
    summary = classify_rejections(rejected, np.nan_to_num(observed), truth, injected)
    receipt = {
        "schema": "m3.2-scheme-d-world-receipt.v1", "cell": cell, "phase": phase, "world": world.world_index,
        "seed_tuples": tuples, "orbit_size": str(orbit), "draws": draws, "statistic_path": statistic,
        "refusals": {str(h): r for h, r in enumerate(reasons) if r},
        "signed_hex": [float(observed[h]).hex() if reasons[h] is None else None for h in range(HYPOTHESES)],
        "exceedances": exceed,
        "p": [f"{1 + x}/{draws + 1}" if x is not None else None for x in exceed],
        "rejected": sorted(rejected), **summary,
    }
    receipt["sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return receipt


def benchmark_world(world: V.SyntheticWorld, permutation_rng, *, reference_draws: int = REFERENCE_DRAWS,
                    fast_draws: int = FAST_DRAWS, clock=time.perf_counter) -> dict:
    """Phase-90 measurement: reference-vs-fast bit identity on this world, fast throughput, full-run projection."""
    if not _GATE["open"]:
        raise IntegrityError("fast_metric_gate_closed")
    reasons = hypothesis_refusals(world)
    tested = [h for h, r in enumerate(reasons) if r is None]
    if not tested:
        raise IntegrityError("benchmark_world_has_no_tested_hypotheses")
    groups = strata_all(world.block_strata)
    t0 = clock()
    maps = [draw_block_map(permutation_rng, groups) for _ in range(reference_draws + fast_draws)]
    map_seconds = clock() - t0
    t0 = clock()
    ref = [actual_statistics(world, m, tested) for m in [None] + maps[:reference_draws]]
    reference_seconds = clock() - t0
    t0 = clock()
    fm = FastMetric.from_world(world)
    encode_seconds = clock() - t0
    t0 = clock()
    fast = [fast_vector(fm, m, tested) for m in [None] + maps[:reference_draws]]
    fast_first_seconds = clock() - t0
    idx = np.asarray(tested)
    identical = all(np.array_equal(a[idx].view(np.uint64), b[idx].view(np.uint64)) for a, b in zip(ref, fast))
    if not identical:
        raise IntegrityError("fast_reference_mismatch")
    threshold = np.abs(fast[0]) - V.TIE_REL_TOL * np.maximum(1.0, np.abs(fast[0]))
    counts = np.zeros(HYPOTHESES, dtype=np.int64)
    t0 = clock()
    for m in maps[reference_draws:]:
        counts[tested] += np.abs(fast_vector(fm, m, tested)[tested]) >= threshold[tested]
    fast_loop_seconds = clock() - t0
    vectors = 1 + reference_draws
    per_draw = fast_loop_seconds / max(fast_draws, 1) + map_seconds / len(maps)
    per_world_fixed = encode_seconds + fast_first_seconds / vectors
    cpu_seconds = V.FULL_WORLDS * (per_world_fixed + (1 + B) * per_draw)
    return {
        "workload": {"tested_hypotheses": len(tested), "reference_vectors": vectors, "fast_vectors_identity": vectors,
                     "fast_draws_timed": fast_draws, "block_maps": len(maps)},
        "equivalence": {"bitwise_identical": True, "hypotheses_compared": len(tested) * vectors},
        "reference": {"seconds_total": reference_seconds, "seconds_per_vector": reference_seconds / vectors},
        "fast": {"encode_seconds": encode_seconds, "first_vectors_seconds": fast_first_seconds,
                 "loop_seconds": fast_loop_seconds, "map_draw_seconds": map_seconds,
                 "seconds_per_draw_end_to_end": per_draw, "draws_per_second": 1.0 / per_draw,
                 "speedup_vs_reference": (reference_seconds / vectors) / per_draw},
        "projection": {"worlds": V.FULL_WORLDS, "draws_per_world": B, "cpu_seconds": cpu_seconds,
                       "cpu_hours": cpu_seconds / 3600, "cpu_years": cpu_seconds / 3600 / 24 / 365.25,
                       "wall_hours": {str(n): cpu_seconds / 3600 / n for n in (4, 64, 256)},
                       "note": "linear scaling; excludes world generation, refusal evaluation, receipts"},
    }


def world_summary(receipt: dict) -> dict:
    if "world_refused" in receipt:
        return {"refused": True, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
                "attribution_errors": 0, "marginal_p": None}
    h = receipt["world"] % HYPOTHESES
    text = receipt["p"][h]
    return {"refused": False, "refused_hypotheses": len(receipt["refusals"]), "R": receipt["R"], "V": receipt["V"],
            "true_discoveries": receipt["true_discoveries"], "attribution_errors": receipt["attribution_errors"],
            "marginal_p": Fraction(text) if text else None}


def cell_plan() -> list[dict]:
    plan = [{"kind": "null", "phase": 10, "dgp": d, "scenario": None, "correlation": c, "worlds": 2000,
             "name": f"N{d}/C{c}"} for d in NULL_DGP for c in range(5)]
    plan += [{"kind": "power", "phase": 20, "dgp": 1, "scenario": s, "correlation": c, "worlds": 1000,
              "name": f"P{s - 100}/C{c}"} for s in SCENARIOS for c in range(5)]
    return plan


def run_cell(cell: dict, registry: SeedRegistry, out_dir: Path, draws: int) -> list[dict]:
    phase, c, w_dgp = cell["phase"], cell["correlation"], cell["dgp"] if cell["kind"] == "null" else cell["scenario"]
    truth = load_truth(cell["scenario"], cell["dgp"] if cell["kind"] == "null" else None, c)
    injected = V.INJECTIONS[cell["scenario"]] if cell["scenario"] else ()
    path = out_dir / f"{cell['name'].replace('/', '_')}.receipts.jsonl"
    summaries = []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:               # exclusive create
        for w in range(cell["worlds"]):
            tuples = {n: [MASTER_SEED, phase, c, w_dgp, w, STREAM[n]]
                      for n in ("data", "membership", "permutation") + (("injection",) if cell["scenario"] else ())}
            world = generate_world(1 if cell["kind"] == "power" else cell["dgp"], c, w,
                                   registry.generator(phase, c, w_dgp, w, 0), registry.generator(phase, c, w_dgp, w, 1))
            if cell["kind"] == "power":
                world = apply_injection(world, cell["scenario"], registry.generator(phase, c, w_dgp, w, 3))
            receipt = world_receipt(world, registry.generator(phase, c, w_dgp, w, 2), phase, cell["name"], tuples,
                                    draws=draws, truth=truth, injected=injected)
            fh.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
            summaries.append(world_summary(receipt))
    return summaries


def run_validation(auth: Authorization, registry: SeedRegistry, out_dir: Path, record: dict) -> dict:
    """Full protocol.  Refused unless the authorization carries a measured benchmark runtime estimate."""
    if (not isinstance(record.get("runtime_estimate_cpu_hours"), (int, float))
            or not isinstance(record.get("fast_benchmark_receipt_sha256"), str)
            or len(record["fast_benchmark_receipt_sha256"]) != 64):
        raise IntegrityError("validation_requires_measured_fast_benchmark_in_authorization")
    cells = {}
    for cell in cell_plan():
        summaries = run_cell(cell, registry, out_dir, B)
        boot = None
        if cell["kind"] == "power" and cell["scenario"] in acceptance.FDR_SCENARIOS:
            boot = registry.generator(30, cell["correlation"], cell["scenario"], 0, 4).integers
        cells[cell["name"]] = acceptance.evaluate_cell(cell["kind"], cell["scenario"], summaries, boot)
    result = acceptance.overall(cells)
    V.exclusive_json(out_dir / "run_result.json", json.dumps({"overall": result, "cells": cells}, sort_keys=True,
                                                            default=str) + "\n")
    return result


def run_mode(mode: str, authorization_path: Path, out_dir: Path) -> dict:
    """benchmark: one phase-90 C0/N1 world, reference-vs-fast identity + fast throughput.  validation: the full protocol."""
    assert_environment()
    assert_isolation()
    auth = authorize(authorization_path, mode)
    assert_equivalence_gate()
    registry = SeedRegistry(auth)
    if mode == "validation":
        with V.blocked_network():
            return run_validation(auth, registry, out_dir, json.loads(Path(authorization_path).read_text()))
    started = time.perf_counter()
    with V.blocked_network():
        tuples = {s: [MASTER_SEED, 90, 0, 1, 0, STREAM[s]] for s in ("data", "membership", "permutation")}
        world = generate_world(1, 0, 0, registry.generator(90, 0, 1, 0, 0), registry.generator(90, 0, 1, 0, 1))
        receipt = {"schema": "m3.2-scheme-d-phase90-benchmark-receipt.v2", "cell": "BENCH/C0/N1", "phase": 90,
                   "seed_tuples": tuples, **benchmark_world(world, registry.generator(90, 0, 1, 0, 2))}
    receipt["runtime_seconds"] = time.perf_counter() - started
    receipt["sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    V.exclusive_json(out_dir / "phase90_benchmark_receipt.json", json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("preflight", "benchmark", "validation"))
    ap.add_argument("--authorization", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.mode == "preflight":
        import preflight
        return preflight.main()
    if not args.authorization or not args.out:
        raise SystemExit("fail closed: --authorization and --out are required for RNG modes")
    result = run_mode(args.mode, args.authorization, args.out)
    print(json.dumps({"mode": args.mode, "result": result.get("passes", result.get("runtime_seconds"))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
