#!/usr/bin/env python3
"""Deterministic, frozen-before-RNG evaluator for the M3.2 Scheme-D v4 pilot.

Consumes a completed, merged pilot run directory (``RUN_MANIFEST.json``, ``shards/``, ``provenance/``, ``merged/``)
plus the owner authorization, verifies every binding and receipt, and writes ``PILOT_VERDICT.json`` using only the
rules frozen in ``PILOT_PRESPEC.json``.  Tampered, incomplete, mis-bound or mis-tagged inputs are refused (no verdict).

P1/C0 is a perturbed-null Type-I / false-positive stress cell: every P1 rejection is a false discovery.  This
evaluator never computes power, FDR, true discoveries or lift for P1, and refuses a run where P1 was tagged or
evaluated as power.  P6/C4 is the only power/effect cell.

Pure standard library: imports neither the runner nor the shard layer and constructs no random generator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESPEC = HERE / "PILOT_PRESPEC.json"
REQUEST = HERE / "AUTHORIZATION_REQUEST.json"
LAUNCHER = HERE / "run_pilot.py"
P6_TRUTH_TABLE = HERE.parents[1] / "scripts/m32_scheme_d_pre_rng_truth_20260921/package/tables/P6_C4.json"
P6_TRUTH_TABLE_SHA256 = "e33b2846e7098ea0f8bea054f77160239eefaf83835d00065d96fba5efaa0a06"   # = proof certificate table_sha256

EXPECTED_PRESPEC_SHA256 = "04c508efd924e04d9d52bb516843b461b6d5be5168c52906c7c7dff2873d7c95"
BUNDLE_SHA256 = "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023"
LAYER_SHA256 = "5e322153c62b103d00e9e898b30df4efc2031ea66c7144b79d5037b4d03c2170"
BENCHMARK_RECEIPT_SHA256 = "f70ace0a02ab6187647ecc33446238cf5ba9dbe97320194ea75f32876972f23e"
MASTER_SEED = 2026091902
STREAM = {"data": 0, "membership": 1, "permutation": 2, "injection": 3}
WORLD_SCHEMA = "m3.2-scheme-d-world-receipt.v1"
SCHEMA_RUN = "m3.2-scheme-d-shard-run-manifest.v4"
SCHEMA_SHARD = "m3.2-scheme-d-shard-completion.v4"
SCHEMA_MERGE = "m3.2-scheme-d-shard-merge-manifest.v4"
SCHEMA_PROVENANCE = "m3.2-scheme-d-shard-provenance.v4"
VERDICT_SCHEMA = "m3.2-scheme-d-pilot-verdict.v2"

CELLS = ("N0/C0", "N3/C4", "P1/C0", "P6/C4")
CELL_SPEC = {  # name -> (kind, phase, dgp, scenario, correlation)
    "N0/C0": ("null", 10, 0, None, 0), "N3/C4": ("null", 10, 3, None, 4),
    "P1/C0": ("perturbed_null", 20, 1, 101, 0), "P6/C4": ("power", 20, 1, 106, 4),
}
TYPE_ONE_CELLS = ("N0/C0", "N3/C4", "P1/C0")
EFFECT_CELL = "P6/C4"
WORLDS_PER_CELL, TOTAL_WORLDS, SHARD_WORLDS, WORKERS, SHARDS, HYPOTHESES = 20, 80, 5, 2, 16, 384
PROTOCOL_DRAWS = 767_999                                               # V.FULL_PERMUTATIONS (runner B)
REFUSAL_LIMIT, NULL_STOP, EFFECT_MIN, WRONG_MEDIAN, MIN_WORLDS = 0.01, 0.25, 0.01, -0.01, 12
Z = 1.959964
# Exactly V.INJECTIONS[106] (trader/cognition/m32_scheme_d_validation.py); the prespec must match it exactly.
EFFECT_FAMILIES = {"0-7": tuple(range(0, 8)), "136-143": tuple(range(136, 144)), "272-279": tuple(range(272, 280)),
                   "344-351": tuple(range(344, 352))}
INJECTIONS_106 = tuple(h for family in EFFECT_FAMILIES.values() for h in family)
EFFECT_NATURE = ("equal-weight, unstandardized, cross-family pilot diagnostic; NOT a formal Scheme-D effect-size "
                 "statistic and not part of Scheme-D acceptance")
HISTORICAL_V3_NOTE = ("The historical repository preserves the v3 pilot verdict value 0.13311869 but not enough "
                      "computation evidence to reconstruct its exact derivation; composite_signed_target_lift is not "
                      "claimed to reproduce it.")

NON_FINAL = [
    "pilot/non-final only",
    "does not satisfy Scheme-D validation",
    "does not establish Gate 2",
    "does not admit search",
    "does not enable referee/handoff",
    "does not authorize live trading",
    "does not authorize full validation",
]
P1_STATEMENT = ("P1/C0 is a perturbed-null Type-I / false-positive stress cell: every P1 rejection is a false "
                "discovery, never power or a true discovery. 20 pilot worlds cannot establish the frozen protocol's "
                "final P1 Type-I/Wilson acceptance requirement (point <= 0.05 and Wilson upper <= 0.06); this pilot "
                "criterion is diagnostic only.")


class RefusedInput(RuntimeError):
    """Inputs cannot be evaluated (tampered, incomplete, mis-bound, mis-tagged); no verdict is produced."""


# ------------------------------------------------------------------------------------------------ helpers
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _pretty(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, default=str) + "\n").encode()


def _self_hash_ok(obj: dict) -> bool:
    return obj.get("sha256") == sha256_bytes(canon({k: v for k, v in obj.items() if k != "sha256"}).encode())


def _load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        raise RefusedInput(f"missing_or_unreadable:{Path(path).name}") from None
    if not isinstance(data, dict):
        raise RefusedInput(f"not_an_object:{Path(path).name}")
    return data


def wilson(k: int, n: int) -> tuple[float, float]:
    """Identical to v4 ``acceptance.wilson``."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def expected_tuples(cell: str, world: int) -> dict:
    _, phase, dgp, scenario, c = CELL_SPEC[cell]
    w_dgp = scenario if scenario is not None else dgp
    streams = ("data", "membership", "permutation") + (("injection",) if scenario is not None else ())
    return {n: [MASTER_SEED, phase, c, w_dgp, world, STREAM[n]] for n in streams}


# ------------------------------------------------------------------------------------------------ bindings
def verify_authorization(auth_path: Path, *, prespec_path: Path = PRESPEC, request_path: Path = REQUEST,
                         launcher_path: Path = LAUNCHER, evaluator_path: Path | None = None) -> tuple[dict, dict]:
    evaluator_path = Path(__file__) if evaluator_path is None else evaluator_path
    if sha256_file(prespec_path) != EXPECTED_PRESPEC_SHA256:
        raise RefusedInput("pilot_prespec_altered")
    auth = _load(auth_path)
    expect = {"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "validation", "scope": "pilot_only_non_final",
              "authorized_by": "owner", "bundle_sha256": BUNDLE_SHA256, "shard_layer_sha256": LAYER_SHA256,
              "pilot_prespec_sha256": EXPECTED_PRESPEC_SHA256,
              "authorization_request_sha256": sha256_file(request_path),
              "pilot_launcher_sha256": sha256_file(launcher_path), "pilot_evaluator_sha256": sha256_file(evaluator_path),
              "shard_worlds": SHARD_WORLDS, "workers": WORKERS, "worlds": TOTAL_WORLDS,
              "fast_benchmark_receipt_sha256": BENCHMARK_RECEIPT_SHA256, "cloud_spend": False,
              "full_validation_authorized": False}
    for key, value in expect.items():
        if auth.get(key) != value or type(auth.get(key)) is not type(value):
            raise RefusedInput(f"authorization_binding_mismatch:{key}")
    return auth, _load(prespec_path)


def _verify_bindings(bindings: dict, auth_path: Path) -> None:
    expect = {"schema": "m3.2-scheme-d-shard-bindings.v4", "mode": "validation", "bundle_sha256": BUNDLE_SHA256,
              "shard_layer_sha256": LAYER_SHA256, "authorization_sha256": sha256_file(auth_path),
              "pilot_prespec_sha256": EXPECTED_PRESPEC_SHA256, "authorization_request_sha256": sha256_file(REQUEST),
              "pilot_launcher_sha256": sha256_file(LAUNCHER), "pilot_evaluator_sha256": sha256_file(Path(__file__)),
              "pilot_scope": "pilot_only_non_final", "non_inferential": False, "shard_worlds": SHARD_WORLDS,
              "worlds": TOTAL_WORLDS, "shards": SHARDS, "master_seed": MASTER_SEED, "statistic_path": "fast",
              "draws": PROTOCOL_DRAWS}
    for key, value in expect.items():
        if bindings.get(key) != value:
            raise RefusedInput(f"run_binding_mismatch:{key}")


def _check_receipt(line: bytes, cell: str, world: int, draws: int) -> dict:
    try:
        receipt = json.loads(line)
    except ValueError:
        raise RefusedInput(f"receipt_unparseable:{cell}:{world}") from None
    if not isinstance(receipt, dict) or (canon(receipt) + "\n").encode() != line or not _self_hash_ok(receipt):
        raise RefusedInput(f"receipt_tampered:{cell}:{world}")
    if (receipt.get("schema") != WORLD_SCHEMA or receipt.get("cell") != cell or receipt.get("world") != world
            or receipt.get("phase") != CELL_SPEC[cell][1] or receipt.get("seed_tuples") != expected_tuples(cell, world)):
        raise RefusedInput(f"receipt_identity_mismatch:{cell}:{world}")
    if "world_refused" not in receipt:
        if receipt.get("draws") != draws or receipt.get("statistic_path") != "fast":
            raise RefusedInput(f"receipt_protocol_mismatch:{cell}:{world}")
        if (len(receipt.get("signed_hex") or ()) != HYPOTHESES or not isinstance(receipt.get("rejected"), list)
                or receipt.get("R") != len(receipt["rejected"])):
            raise RefusedInput(f"receipt_shape_mismatch:{cell}:{world}")
    return receipt


def load_run(run_dir: Path, auth_path: Path) -> dict:
    """Verify the completed merged run end to end; return per-cell receipts plus manifest facts."""
    run_dir = Path(run_dir)
    merged = run_dir / "merged"
    manifest_raw = (merged / "MERGE_MANIFEST.json")
    manifest = _load(manifest_raw)
    if manifest.get("schema") != SCHEMA_MERGE or not _self_hash_ok(manifest) or _pretty(manifest) != manifest_raw.read_bytes():
        raise RefusedInput("merge_manifest_tampered")
    body = {k: v for k, v in manifest.items() if k not in ("sha256", "deterministic_sha256", "informational")}
    if manifest.get("deterministic_sha256") != sha256_bytes(canon(body).encode()):
        raise RefusedInput("merge_manifest_deterministic_hash_mismatch")
    bindings = manifest.get("bindings") or {}
    _verify_bindings(bindings, auth_path)
    if manifest.get("worlds") != TOTAL_WORLDS or manifest.get("shards") != SHARDS or manifest.get("non_inferential") is not False:
        raise RefusedInput("merge_world_or_shard_count_mismatch")
    run_manifest = _load(run_dir / "RUN_MANIFEST.json")
    if (sha256_file(run_dir / "RUN_MANIFEST.json") != manifest.get("run_manifest_sha256")
            or run_manifest.get("schema") != SCHEMA_RUN or not _self_hash_ok(run_manifest)
            or run_manifest.get("bindings") != bindings):
        raise RefusedInput("run_manifest_mismatch")
    plan = run_manifest.get("plan") or []
    if [c.get("name") for c in plan] != list(CELLS):
        raise RefusedInput("unexpected_cell_set")
    for c in plan:
        kind, phase, dgp, scenario, corr = CELL_SPEC[c["name"]]
        if (c.get("kind"), c.get("phase"), c.get("dgp"), c.get("scenario"), c.get("correlation"), c.get("worlds")) \
                != (kind, phase, dgp, scenario, corr, WORLDS_PER_CELL):
            raise RefusedInput(f"cell_kind_or_identity_mismatch:{c['name']}")
    shards = run_manifest.get("shards") or []
    if len(shards) != SHARDS:
        raise RefusedInput("incomplete_shards")
    if set(manifest.get("cells") or {}) != set(CELLS):
        raise RefusedInput("unexpected_cell_set")
    completions = manifest.get("shard_completion_sha256") or {}
    if sorted(completions) != sorted(str(s["id"]) for s in shards):
        raise RefusedInput("incomplete_shards")
    per_cell_bytes: dict = {name: b"" for name in CELLS}
    for shard in shards:
        stem = f"shard_{shard['id']:06d}"
        comp_path = run_dir / "shards" / f"{stem}.complete.json"
        if not comp_path.is_file() or sha256_file(comp_path) != completions[str(shard["id"])]:
            raise RefusedInput(f"incomplete_shards:{shard['id']}")
        comp = _load(comp_path)
        data_path = run_dir / "shards" / f"{stem}.receipts.jsonl"
        data = data_path.read_bytes() if data_path.is_file() else b""
        if (comp.get("schema") != SCHEMA_SHARD or not _self_hash_ok(comp) or comp.get("shard") != shard
                or comp.get("bindings") != bindings or sha256_bytes(data) != comp.get("receipts_sha256")
                or comp.get("worlds") != shard["hi"] - shard["lo"]):
            raise RefusedInput(f"shard_completion_mismatch:{shard['id']}")
        per_cell_bytes[shard["cell"]] += data
    cells: dict = {}
    for name in CELLS:
        meta = manifest["cells"][name]
        path = merged / meta["file"]
        data = path.read_bytes() if path.is_file() else b""
        if data != per_cell_bytes[name] or sha256_bytes(data) != meta.get("sha256") or meta.get("worlds") != WORLDS_PER_CELL:
            raise RefusedInput(f"merged_cell_file_mismatch:{name}")
        lines = data.split(b"\n")
        if lines[-1] != b"" or len(lines) - 1 != WORLDS_PER_CELL:
            raise RefusedInput(f"world_count_mismatch:{name}")
        cells[name] = [_check_receipt(line + b"\n", name, w, bindings["draws"]) for w, line in enumerate(lines[:-1])]
    result_path = merged / "run_result.json"
    if not result_path.is_file() or sha256_file(result_path) != manifest.get("run_result_sha256"):
        raise RefusedInput("run_result_mismatch")
    run_result = _load(result_path)
    if run_result.get("overall") != manifest.get("overall") or set(run_result.get("cells") or {}) != set(CELLS):
        raise RefusedInput("unexpected_cell_set")
    info = manifest.get("informational") or {}
    prov_dir = run_dir / "provenance"
    prov_files = sorted(prov_dir.glob("prov_*.json")) if prov_dir.is_dir() else []
    if not prov_files or not info.get("worker_provenance") or "invalid_provenance_files" not in info:
        raise RefusedInput("missing_provenance")
    return {"manifest": manifest, "bindings": bindings, "cells": cells, "run_result": run_result,
            "provenance_files": len(prov_files), "informational": info}


# ------------------------------------------------------------------------------------------------ diagnostics
def refusal_rates(receipts: list[dict]) -> dict:
    """Unchanged v4 ``acceptance.refusal_blockers`` accounting."""
    refused = sum(1 for r in receipts if "world_refused" in r)
    tested = len(receipts) - refused
    pairs = sum(len(r.get("refusals") or {}) for r in receipts if "world_refused" not in r)
    world_rate = refused / len(receipts) if receipts else 1.0
    pair_rate = pairs / (HYPOTHESES * tested) if tested else 1.0
    return {"worlds": len(receipts), "refused_worlds": refused, "non_refused_worlds": tested,
            "world_refusal_rate": world_rate, "hypothesis_refusal_rate": pair_rate,
            "exceeds_limit": world_rate > REFUSAL_LIMIT or pair_rate > REFUSAL_LIMIT}


def type_one_diagnostic(receipts: list[dict]) -> dict:
    valid = [r for r in receipts if "world_refused" not in r]
    k, n = sum(1 for r in valid if r["R"] > 0), len(valid)
    lo, hi = wilson(k, n)
    return {"rejecting_worlds": k, "non_refused_worlds": n, "rate": k / n if n else None,
            "wilson_95": [lo, hi], "stop": n == 0 or k / n >= NULL_STOP}


def effect_truth_signs(prespec: dict, truth_path: Path = P6_TRUTH_TABLE) -> dict[int, int]:
    """Frozen target set (exactly V.INJECTIONS[106]) and each target's sign from the hash-bound P6/C4 truth table."""
    out = prespec.get("outcome") or {}
    if tuple((out.get("effect_targets") or {}).get(EFFECT_CELL) or ()) != INJECTIONS_106 \
            or list(out.get("effect_targets") or {}) != [EFFECT_CELL]:
        raise RefusedInput("effect_target_set_mismatch")
    if {k: tuple(v) for k, v in (out.get("effect_target_families") or {}).items()} != EFFECT_FAMILIES:
        raise RefusedInput("effect_target_families_mismatch")
    if not Path(truth_path).is_file() or sha256_file(truth_path) != P6_TRUTH_TABLE_SHA256:
        raise RefusedInput("p6_truth_table_hash_mismatch")
    records = _load(truth_path).get("records") or []
    if len(records) != HYPOTHESES:
        raise RefusedInput("p6_truth_table_shape_mismatch")
    signs = {}
    for h in INJECTIONS_106:
        rec = records[h]
        if rec.get("hypothesis") != h or rec.get("classification") != "analytic_non_null" or rec.get("sign") not in (1, -1):
            raise RefusedInput(f"p6_target_not_analytic_non_null:{h}")
        signs[h] = rec["sign"]
    return signs


def _mean(values):
    return sum(values) / len(values) if values else None


def world_effect(receipt: dict, signs: dict[int, int]) -> dict:
    """One non-world-refused P6/C4 receipt: exact decode, truth-sign contributions over tested injected targets
    (hypothesis-refused targets excluded), composite mean, tested/refused counts and descriptive family means."""
    refused = {int(h) for h in (receipt.get("refusals") or {})}
    contributions = {}
    for h in INJECTIONS_106:
        text = receipt["signed_hex"][h]
        if h in refused:
            if text is not None:
                raise RefusedInput(f"p6_refused_target_has_statistic:{receipt['world']}:{h}")
            continue
        if not isinstance(text, str):
            raise RefusedInput(f"p6_tested_target_missing_statistic:{receipt['world']}:{h}")
        contributions[h] = float.fromhex(text) * signs[h]
    tested = len(contributions)
    return {"world": receipt["world"], "target_tested_count": tested, "target_refused_count": len(INJECTIONS_106) - tested,
            "effect_evaluable": tested > 0, "effect_status": "EVALUABLE" if tested else "UNEVALUABLE",
            "effect_refusal": None if tested else "zero_testable_injected_targets",
            "composite_signed_target_lift": _mean(list(contributions.values())),
            "family_means": {name: _mean([contributions[h] for h in family if h in contributions])
                             for name, family in EFFECT_FAMILIES.items()}}


def effect_diagnostic(receipts: list[dict], signs: dict[int, int]) -> dict:
    valid = [r for r in receipts if "world_refused" not in r]
    worlds = [world_effect(r, signs) for r in valid]
    values = [w["composite_signed_target_lift"] for w in worlds if w["effect_evaluable"]]
    family_medians = {}
    for name in EFFECT_FAMILIES:
        fam = [w["family_means"][name] for w in worlds if w["family_means"][name] is not None]
        family_medians[name] = statistics.median(fam) if fam else None
    return {"statistic": "composite_signed_target_lift", "nature": EFFECT_NATURE, "historical_v3_note": HISTORICAL_V3_NOTE,
            "targets": list(INJECTIONS_106), "non_refused_worlds": len(valid), "effect_evaluable_worlds": len(values),
            "effect_unevaluable_worlds": len(worlds) - len(values),
            "median_signed_target_lift": statistics.median(values) if values else None,
            "positive_worlds": sum(1 for x in values if x > 0), "non_positive_worlds": sum(1 for x in values if x <= 0),
            "worlds": worlds,
            "family_median_of_world_means": family_medians,
            "family_means_are_descriptive_only": True,
            "attribution_errors": sum(r.get("attribution_errors", 0) for r in valid),
            "true_discoveries": sum(r.get("true_discoveries", 0) for r in valid)}


def decide(*, integrity_failures: list[str], refusal: dict, type_one: dict, effect: dict) -> tuple[str, str]:
    """Frozen precedence: integrity/refusal STOP -> null/perturbed-null STOP -> P6 wrong-direction/negligible STOP ->
    clean P6 GO -> INCONCLUSIVE.  No effect-evaluable P6 world (no median) is INCONCLUSIVE, never negligible STOP."""
    if integrity_failures:
        return "STOP", "integrity_failure:" + ",".join(integrity_failures)
    over = sorted(name for name, r in refusal.items() if r["exceeds_limit"])
    if over:
        return "STOP", "refusal_rate_exceeds_1pct:" + ",".join(over)
    bad = sorted(name for name, d in type_one.items() if d["stop"])
    if bad:
        return "STOP", "null_or_perturbed_null_rejecting_rate_ge_0.25:" + ",".join(bad)
    median = effect["median_signed_target_lift"]
    if median is None:
        return "INCONCLUSIVE", "p6_no_effect_evaluable_worlds"
    if median <= WRONG_MEDIAN and effect["non_positive_worlds"] >= MIN_WORLDS:
        return "STOP", "p6_wrong_direction"
    if abs(median) < EFFECT_MIN:
        return "STOP", "p6_negligible_effect"
    if median >= EFFECT_MIN and effect["positive_worlds"] >= MIN_WORLDS:
        return "GO", "p6_encouraging_effect"
    return "INCONCLUSIVE", "no_stop_and_no_clean_p6_effect"


def check_p1_not_power(run_result: dict) -> None:
    """P1 must have been evaluated by acceptance as perturbed_null (Type-I only), never power/FDR."""
    p1 = run_result["cells"]["P1/C0"]
    gates = set((p1.get("gates") or {}))
    if gates != {"refusal_blocker_absent", "type_one"} or "fdp_count" not in p1 or "attribution_errors" in p1:
        raise RefusedInput("p1_evaluated_as_power")
    p6 = run_result["cells"]["P6/C4"]
    if "power" not in (p6.get("gates") or {}) or "fdr" not in (p6.get("gates") or {}):
        raise RefusedInput("p6_not_evaluated_as_power")


def evaluate(run_dir: Path, auth_path: Path) -> dict:
    auth, prespec = verify_authorization(auth_path)
    run = load_run(run_dir, auth_path)
    check_p1_not_power(run["run_result"])
    refusal = {name: refusal_rates(run["cells"][name]) for name in CELLS}
    for name in CELLS:                                                  # cross-check the frozen acceptance output
        acc = run["run_result"]["cells"][name].get("refusal") or {}
        if (acc.get("world_refusal_rate") != refusal[name]["world_refusal_rate"]
                or acc.get("hypothesis_refusal_rate") != refusal[name]["hypothesis_refusal_rate"]):
            raise RefusedInput(f"refusal_accounting_mismatch:{name}")
    type_one = {name: type_one_diagnostic(run["cells"][name]) for name in TYPE_ONE_CELLS}
    for name in TYPE_ONE_CELLS:
        if run["run_result"]["cells"][name].get("type_one_rate") != type_one[name]["rate"]:
            raise RefusedInput(f"type_one_accounting_mismatch:{name}")
    effect = effect_diagnostic(run["cells"][EFFECT_CELL], effect_truth_signs(prespec))
    overall, info = run["run_result"]["overall"], run["informational"]
    integrity = []
    if overall.get("integrity_stop"):
        integrity.append("run_integrity_stop")
    if overall.get("truth_table_refusal"):
        integrity.append("truth_table_refusal")
    if info.get("invalid_provenance_files"):
        integrity.append("invalid_provenance_files")
    if info.get("worker_hardware_identities_distinct") != 1:
        integrity.append("worker_hardware_identities_not_exactly_one")
    verdict, reason = decide(integrity_failures=integrity, refusal=refusal, type_one=type_one, effect=effect)
    p1 = type_one["P1/C0"]
    return {
        "schema": VERDICT_SCHEMA, "verdict": verdict, "reason": reason,
        "scope": "pilot_only_non_final", "non_final_statements": NON_FINAL,
        "bindings": {"pilot_prespec_sha256": EXPECTED_PRESPEC_SHA256, "authorization_sha256": sha256_file(auth_path),
                     "authorization_request_sha256": auth["authorization_request_sha256"],
                     "pilot_launcher_sha256": auth["pilot_launcher_sha256"],
                     "pilot_evaluator_sha256": auth["pilot_evaluator_sha256"], "bundle_sha256": BUNDLE_SHA256,
                     "shard_layer_sha256": LAYER_SHA256, "merge_manifest_sha256": run["manifest"]["sha256"],
                     "merge_deterministic_sha256": run["manifest"]["deterministic_sha256"],
                     "run_result_sha256": run["manifest"]["run_result_sha256"]},
        "integrity": {"shards_complete": SHARDS, "shards_total": SHARDS, "worlds": TOTAL_WORLDS,
                      "failures": integrity, "provenance_files": run["provenance_files"],
                      "worker_hardware_identities_distinct": info.get("worker_hardware_identities_distinct")},
        "refusal": refusal,
        "type_one_diagnostics": type_one,
        "p1_perturbed_null": {"rejecting_worlds": p1["rejecting_worlds"], "non_refused_worlds": p1["non_refused_worlds"],
                              "rate": p1["rate"], "wilson_95": p1["wilson_95"],
                              "every_rejection_is_false_discovery": True, "statement": P1_STATEMENT},
        "p6_effect": effect,
        "full_protocol_acceptance_output": {"overall": overall,
                                            "note": "frozen v4 acceptance on 20 worlds/cell; not the pilot verdict"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--authorization", type=Path, required=True)
    ap.add_argument("--verdict-out", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        verdict = evaluate(args.run_dir, args.authorization)
        fd = os.open(args.verdict_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)   # never overwrite a verdict
        with os.fdopen(fd, "wb") as fh:
            fh.write(_pretty(verdict))
    except (RefusedInput, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"verdict": verdict["verdict"], "reason": verdict["reason"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
