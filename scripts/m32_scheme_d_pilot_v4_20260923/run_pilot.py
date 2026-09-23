#!/usr/bin/env python3
"""Authorization-gated launcher for the frozen M3.2 Scheme-D v4 pilot (80 worlds, non-final).

Structural sibling of ``scripts/m32_scheme_d_pilot_20260922/run_pilot.py`` (v3, unchanged), bound to bundle v4 and
the v4 shard layer.  Every binding -- prespec, authorization request, owner authorization, this launcher, the
evaluator, bundle, shard layer, measured benchmark receipt and host qualification evidence -- is verified from the
files on disk BEFORE the v4 layer/runner is imported and long before ``SeedRegistry`` exists.  The launcher only
builds the four-cell pilot ``Context``; storage, workers, audit and merge are the v4 shard layer's, unchanged, and
every command runs inside the frozen runner's ``blocked_network()``.

Under bundle v4, P1/C0 is ``kind="perturbed_null"`` (a Type-I / false-positive stress cell, never power) and still
receives its injection, because the v4 layer dispatches generation/injection on ``cell["scenario"] is not None``.
P6/C4 is the pilot's only power/effect cell.  Pilot output is non-final: it does not satisfy Scheme-D validation,
establish Gate 2, admit search, enable referee/handoff, or authorize live trading or full validation.

Importing this module constructs no random generator and imports neither the layer nor the runner.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRESPEC = HERE / "PILOT_PRESPEC.json"
REQUEST = HERE / "AUTHORIZATION_REQUEST.json"
EVALUATOR = HERE / "evaluate_pilot.py"
LAYER_FILE = ROOT / "scripts/m32_scheme_d_sharding_v4_20260923/shard_layer.py"

EXPECTED_PRESPEC_SHA256 = "04c508efd924e04d9d52bb516843b461b6d5be5168c52906c7c7dff2873d7c95"
BUNDLE_SHA256 = "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023"
LAYER_SHA256 = "5e322153c62b103d00e9e898b30df4efc2031ea66c7144b79d5037b4d03c2170"
BENCHMARK_RECEIPT = ROOT / "scripts/m32_scheme_d_phase90_fast_benchmark_v4_20260923/out/phase90_benchmark_receipt.json"
BENCHMARK_RECEIPT_SHA256 = "f70ace0a02ab6187647ecc33446238cf5ba9dbe97320194ea75f32876972f23e"
FULL_WORKLOAD_CPU_HOURS = 16406.782531828467
FULL_WORKLOAD_WORLDS = 75000
HOST_RECEIPT = ROOT / "scripts/m32_scheme_d_host_qualification_v4_20260923/receipts/receipt_sarmad-VMware-Virtual-Platform.json"
HOST_RECEIPT_SHA256 = "451c1c5ec33ce5a1cc03008344a8cbe99e7644c676a6d8d7d135ca625b9998ae"
QUAL_REPORT = ROOT / "scripts/m32_scheme_d_host_qualification_v4_20260923/QUALIFICATION_REPORT.json"
QUAL_REPORT_SHA256 = "b54a14104c8850d68991578610a29b13e8b29e22a40f1f30b24efb5986b5858d"
HOST_EVIDENCE_COMMIT = "42386606c14bdc7a2b6351dcbf78512163621608"
QUALIFIED_WORKERS = 4

CELLS = ("N0/C0", "N3/C4", "P1/C0", "P6/C4")
EXPECTED_KIND = {"N0/C0": ("null", None), "N3/C4": ("null", None), "P1/C0": ("perturbed_null", 101),
                 "P6/C4": ("power", 106)}
WORLDS_PER_CELL, TOTAL_WORLDS, WORKERS, SHARD_WORLDS, SHARDS = 20, 80, 2, 5, 16
RUNTIME_CPU_HOURS = FULL_WORKLOAD_CPU_HOURS * TOTAL_WORLDS / FULL_WORKLOAD_WORLDS


class PilotError(RuntimeError):
    """Fail-closed pilot binding error; the message starts with a stable reason code."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise PilotError(f"unreadable:{Path(path).name}") from None
    if not isinstance(data, dict):
        raise PilotError(f"not_an_object:{Path(path).name}")
    return data


def verify_static(auth_path: Path, *, shard_worlds: int, workers: int, prespec_path: Path = PRESPEC,
                  request_path: Path = REQUEST, launcher_path: Path | None = None,
                  evaluator_path: Path = EVALUATOR, layer_path: Path = LAYER_FILE) -> dict:
    """Every pilot binding, verified from files alone (no layer/runner import, no RNG).  Returns the auth record."""
    launcher_path = Path(__file__) if launcher_path is None else launcher_path
    auth = _json(auth_path)
    prespec_sha = sha256_file(prespec_path)
    if prespec_sha != EXPECTED_PRESPEC_SHA256:
        raise PilotError("pilot_prespec_altered")
    expect = {
        "schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "validation", "scope": "pilot_only_non_final",
        "authorized_by": "owner", "bundle_sha256": BUNDLE_SHA256, "shard_layer_sha256": LAYER_SHA256,
        "pilot_prespec_sha256": prespec_sha, "authorization_request_sha256": sha256_file(request_path),
        "pilot_launcher_sha256": sha256_file(launcher_path), "pilot_evaluator_sha256": sha256_file(evaluator_path),
        "shard_worlds": SHARD_WORLDS, "workers": WORKERS, "worlds": TOTAL_WORLDS,
        "runtime_estimate_cpu_hours": RUNTIME_CPU_HOURS, "fast_benchmark_receipt_sha256": BENCHMARK_RECEIPT_SHA256,
        "host_qualification_evidence_commit": HOST_EVIDENCE_COMMIT,
        "host_receipt_sha256": HOST_RECEIPT_SHA256, "qualification_report_sha256": QUAL_REPORT_SHA256,
        "single_host_qualified": True, "qualified_workers": QUALIFIED_WORKERS,
        "cross_host_qualification": "not_exercised", "cloud_spend": False, "full_validation_authorized": False,
    }
    for key, value in expect.items():
        got = auth.get(key)
        if got != value or type(got) is not type(value):
            raise PilotError(f"authorization_binding_mismatch:{key}")
    if shard_worlds != SHARD_WORLDS or workers != WORKERS:
        raise PilotError("pilot_execution_binding_mismatch")
    if sha256_file(layer_path) != LAYER_SHA256:
        raise PilotError("shard_layer_hash_mismatch")
    for path, digest in ((BENCHMARK_RECEIPT, BENCHMARK_RECEIPT_SHA256), (HOST_RECEIPT, HOST_RECEIPT_SHA256),
                         (QUAL_REPORT, QUAL_REPORT_SHA256)):
        if not path.is_file() or sha256_file(path) != digest:
            raise PilotError(f"evidence_hash_mismatch:{path.name}")
    p = _json(prespec_path)
    if (p.get("bundle", {}).get("sha256") != BUNDLE_SHA256 or p.get("shard_layer", {}).get("sha256") != LAYER_SHA256
            or tuple(p.get("cells", ())) != CELLS or p.get("world_indices", {}).get("worlds_per_cell") != WORLDS_PER_CELL
            or p.get("execution", {}).get("workers") != WORKERS or p.get("execution", {}).get("shard_worlds") != SHARD_WORLDS
            or p.get("execution", {}).get("cloud_spend") is not False or p.get("full_validation_authorized") is not False):
        raise PilotError("pilot_prespec_content_mismatch")
    req = _json(request_path)
    for key in ("pilot_prespec_sha256", "bundle_sha256", "shard_layer_sha256", "pilot_launcher_sha256",
                "pilot_evaluator_sha256", "shard_worlds", "workers", "worlds", "runtime_estimate_cpu_hours",
                "fast_benchmark_receipt_sha256"):
        if req.get(key) != auth[key]:
            raise PilotError(f"authorization_request_mismatch:{key}")
    report = _json(QUAL_REPORT)
    host = next(iter(report.get("hosts", {}).values()), {})
    if (report.get("bindings", {}).get("bundle_sha256") != BUNDLE_SHA256
            or report.get("bindings", {}).get("shard_layer_sha256") != LAYER_SHA256
            or report.get("bindings", {}).get("benchmark_receipt_sha256") != BENCHMARK_RECEIPT_SHA256
            or host.get("verdict") != "PASS" or host.get("qualified_workers") != QUALIFIED_WORKERS
            or host.get("mount_fstype") != "ext4" or host.get("mount_source") != "/dev/sda2"
            or WORKERS > QUALIFIED_WORKERS):
        raise PilotError("host_qualification_mismatch")
    return auth


_LAYER: dict = {}


def load_layer():
    """The v4 shard layer under a private module name (hash verified first); cached per process."""
    if "module" not in _LAYER:
        if sha256_file(LAYER_FILE) != LAYER_SHA256:
            raise PilotError("shard_layer_hash_mismatch")
        spec = importlib.util.spec_from_file_location("m32_v4_pilot_shard_layer", LAYER_FILE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _LAYER["module"] = module
    return _LAYER["module"]


def pilot_plan(R) -> list[dict]:
    """The four frozen cells from the v4 ``cell_plan()``, 20 worlds each, with their v4 kinds asserted."""
    by_name = {c["name"]: c for c in R.cell_plan()}
    plan = []
    for name in CELLS:
        cell = by_name.get(name)
        kind, scenario = EXPECTED_KIND[name]
        if cell is None or cell["kind"] != kind or cell["scenario"] != scenario:
            raise PilotError(f"pilot_cell_kind_mismatch:{name}")
        plan.append({**cell, "worlds": WORLDS_PER_CELL})
    p1 = plan[CELLS.index("P1/C0")]
    if p1["scenario"] is None or 101 not in R.PERTURBED_NULL_SCENARIOS or 101 in R.acceptance.FDR_SCENARIOS \
            or 101 in R.acceptance.POWER_RULES:
        raise PilotError("p1_must_be_injected_perturbed_null_outside_power_fdr")
    if 106 not in R.acceptance.FDR_SCENARIOS or 106 not in R.acceptance.POWER_RULES:
        raise PilotError("p6_must_remain_power")
    return plan


def context(auth_path: Path, shard_worlds: int, workers: int = WORKERS):
    """Verify everything, then (and only then) construct the runner's ``SeedRegistry`` and the pilot ``Context``."""
    auth_path = Path(auth_path)
    verify_static(auth_path, shard_worlds=shard_worlds, workers=workers)
    S = load_layer()
    R = S.runner()
    plan = pilot_plan(R)
    env = R.assert_environment()
    R.assert_isolation()
    verified = R.authorize(auth_path, "validation")                     # runner's own bundle/owner check
    record = json.loads(auth_path.read_text())
    if record.get("shard_layer_sha256") != S.sha256_file(S.LAYER_PATH) or record.get("shard_worlds") != shard_worlds:
        raise PilotError("authorization_missing_shard_layer_binding")
    R.assert_equivalence_gate()
    registry = R.SeedRegistry(verified)                                 # first and only RNG-capable object
    common = {
        "bundle_sha256": R.bundle_sha256(),
        "authorization_sha256": sha256_file(auth_path),
        "runner_sha256": S.sha256_file(R.__file__),
        "environment_lock_sha256": S.sha256_file(S.BUNDLE / "artifacts/environment_lock.json"),
        "environment": {k: env[k] for k in ("python", "numpy", "openblas_threads")},
        "non_inferential": False,
        "pilot_prespec_sha256": sha256_file(PRESPEC),
        "authorization_request_sha256": sha256_file(REQUEST),
        "pilot_launcher_sha256": sha256_file(Path(__file__)),
        "pilot_evaluator_sha256": sha256_file(EVALUATOR),
        "pilot_scope": "pilot_only_non_final",
    }
    ctx = S.Context(mode="validation", plan=plan, shard_worlds=shard_worlds, draws=R.B, bindings_common=common,
                    evaluator=S.make_evaluator(registry, R.B), bootstrap=S.bootstrap_provider(registry))
    if ctx.total_worlds != TOTAL_WORLDS or len(ctx.shards) != SHARDS:
        raise PilotError("pilot_shard_plan_mismatch")
    return S, R, ctx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("init", "work", "audit", "merge"))
    ap.add_argument("--authorization", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--worker", type=int, default=0)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--shard-worlds", type=int, default=SHARD_WORLDS)
    args = ap.parse_args(argv)
    try:
        if args.workers != WORKERS or args.worker not in range(WORKERS):
            raise PilotError("pilot_requires_exactly_2_static_workers")
        S, R, ctx = context(args.authorization, args.shard_worlds, args.workers)
        with R.V.blocked_network():
            if args.command == "init":
                result = S.init_run(args.out, ctx)
            elif args.command == "work":
                result = S.work(args.out, ctx, args.worker, args.workers, "static")
            elif args.command == "audit":
                result = S.audit(args.out, ctx)
            else:
                result = S.merge(args.out, ctx)
    except Exception as exc:                                            # noqa: BLE001 - fail closed with a reason code
        print(f"fail closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if args.command != "audit" or result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
