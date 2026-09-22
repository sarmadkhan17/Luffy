#!/usr/bin/env python3
"""Authorization-gated launcher for the frozen M3.2 pilot.

This adapter leaves bundle-v3 and the hardened shard layer unchanged. It only
builds the four-cell pilot Context before delegating storage, workers, audit,
and merge to the hardened layer.
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
LAYER_FILE = ROOT / "scripts/m32_scheme_d_sharding_20260921/shard_layer.py"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_layer():
    sys.path.insert(0, str(LAYER_FILE.parent))
    spec = importlib.util.spec_from_file_location("m32_pilot_shard_layer", LAYER_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def context(auth_path: Path, shard_worlds: int):
    S = load_layer()
    p = json.loads(PRESPEC.read_text())
    auth = json.loads(auth_path.read_text())
    if sha256_file(PRESPEC) != auth.get("pilot_prespec_sha256"):
        raise S.ShardError("pilot_prespec_hash_mismatch")
    if auth.get("bundle_sha256") != p["bundle"]["sha256"]:
        raise S.ShardError("pilot_bundle_binding_mismatch")
    if auth.get("shard_layer_sha256") != p["shard_layer"]["sha256"]:
        raise S.ShardError("pilot_layer_binding_mismatch")
    if auth.get("shard_worlds") != shard_worlds or auth.get("workers") != p["execution"]["workers"]:
        raise S.ShardError("pilot_execution_binding_mismatch")
    R = S.runner()
    env = R.assert_environment()
    R.assert_isolation()
    verified = R.authorize(auth_path, "validation")
    R.assert_equivalence_gate()
    by_name = {c["name"]: c for c in R.cell_plan()}
    plan = [{**by_name[name], "worlds": p["world_indices"]["worlds_per_cell"]} for name in p["cells"]]
    registry = R.SeedRegistry(verified)
    common = {
        "bundle_sha256": R.bundle_sha256(),
        "authorization_sha256": sha256_file(auth_path),
        "runner_sha256": S.sha256_file(R.__file__),
        "environment_lock_sha256": S.sha256_file(S.BUNDLE / "artifacts/environment_lock.json"),
        "environment": {k: env[k] for k in ("python", "numpy", "openblas_threads")},
        "non_inferential": False,
        "pilot_prespec_sha256": sha256_file(PRESPEC),
        "pilot_launcher_sha256": sha256_file(Path(__file__)),
    }
    return S, S.Context(mode="validation", plan=plan, shard_worlds=shard_worlds, draws=R.B,
                        bindings_common=common, evaluator=S.make_evaluator(registry, R.B),
                        bootstrap=S.bootstrap_provider(registry))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("init", "work", "audit", "merge"))
    ap.add_argument("--authorization", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--worker", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--shard-worlds", type=int, default=5)
    args = ap.parse_args(argv)
    S, ctx = context(args.authorization, args.shard_worlds)
    if args.workers != 2:
        raise SystemExit("fail closed: pilot requires exactly 2 workers")
    if args.command == "init":
        result = S.init_run(args.out, ctx)
    elif args.command == "work":
        result = S.work(args.out, ctx, args.worker, args.workers, "static")
    elif args.command == "audit":
        result = S.audit(args.out, ctx)
    else:
        result = S.merge(args.out, ctx)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
