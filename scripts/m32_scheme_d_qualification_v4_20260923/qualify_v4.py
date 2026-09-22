#!/usr/bin/env python3
"""Deterministic hash-and-wiring qualification for the v4 M3.2 Scheme D shard layer.

This is an "equivalent deterministic binding" qualification, NOT the full cloud/host/performance qualification
that ``scripts/m32_scheme_d_qualification_20260922/qualify.py`` runs for bundle v3 (subprocess-forked flock
cross-host exclusivity, worker-scaling throughput, filesystem-capability probing on a shared mount).  That work
is explicitly out of scope here and remains a separate, future task; see ``not_ready_reasons`` in the receipt this
tool writes.

What this tool verifies, all in-process, RNG-free and single-host:
  1. exact hash bindings -- bundle v4's manifest, frozen runner and frozen acceptance module, the v4 shard layer,
     the v4 fixture helper and this tool itself -- against the pinned freeze-tag hashes;
  2. that ``scripts/m32_scheme_d_sharding_20260921/`` (v3) and
     ``scripts/m32_scheme_d_validation_bundle_v3_20260921/`` (v3 bundle) are byte-identical to the freeze commit
     ``m32-scheme-d-v4-freeze`` (079e8b5c590429aeb0ca5c7b0b261328291c6b2e) -- i.e. this work touched nothing in v3;
  3. bundle v4's own RNG-free ``equivalence_gate_static()`` hash/receipt gate;
  4. a fixture canary: a tiny RNG-free fixture run (arithmetic tokens) through ``work`` / ``audit`` / ``merge`` on
     the v4 shard layer, checked for a clean audit and a published merge manifest.

No random generator is constructed anywhere in this tool (guarded by ``RNGGuard``; the receipt records zero
counters).  No owner authorization is read, minted or referenced as sufficient for anything.  Production entry
through the v4 shard layer's ``production_context`` remains gated on a separate, future, explicitly v4-bound owner
authorization -- this tool never produces or implies one, and the v4 shard layer refuses a v3-shaped authorization
by construction (mismatched ``bundle_sha256``; see the accompanying test suite).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHARDING_DIR = ROOT / "scripts/m32_scheme_d_sharding_v4_20260923"
V3_SHARDING_DIR = ROOT / "scripts/m32_scheme_d_sharding_20260921"
V3_BUNDLE_DIR = ROOT / "scripts/m32_scheme_d_validation_bundle_v3_20260921"
sys.path.insert(0, str(SHARDING_DIR))
sys.path.insert(0, str(SHARDING_DIR.parent))                       # not used for import, only path hygiene
import rng_guard  # noqa: E402

SCHEMA_RECEIPT = "m3.2-scheme-d-v4-qualification-receipt.v1"
TOOL_PATH = Path(__file__).resolve()
FREEZE_TAG = "m32-scheme-d-v4-freeze"
FREEZE_COMMIT = "079e8b5c590429aeb0ca5c7b0b261328291c6b2e"

# Pinned exact hashes (also independently checked inside shard_layer.py's own runner() before it imports anything).
EXPECTED = {
    "scripts/m32_scheme_d_validation_bundle_v4_20260922/BUNDLE_MANIFEST.json":
        "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023",
    "scripts/m32_scheme_d_validation_bundle_v4_20260922/runner/scheme_d_runner.py":
        "7e4d693bdaffc7cf35370fe3425858ea195fc70eb0c6425c4461836008410548",
    "scripts/m32_scheme_d_validation_bundle_v4_20260922/runner/acceptance.py":
        "a575c07fcef86948c50467dc1f32a7bdba1ea32d78a3897b0e2eb1d4569d7ec1",
}


class QualificationError(RuntimeError):
    pass


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def self_hashed(body: dict) -> dict:
    return {**body, "receipt_sha256": sha256_bytes(canon(body).encode())}


def _load_isolated(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------------------------ checks
def check_own_hash_bindings() -> dict:
    """The exact freeze-tag hashes this tool is pinned to, independent of whatever the layer re-checks itself."""
    problems = []
    for rel, expected in EXPECTED.items():
        path = ROOT / rel
        got = sha256_file(path) if path.is_file() else None
        if got != expected:
            problems.append({"path": rel, "expected": expected, "actual": got})
    detail = {"checked": sorted(EXPECTED), "problems": problems}
    return {"status": "FAIL" if problems else "PASS", "detail": detail}


def check_v3_untouched() -> dict:
    """``scripts/m32_scheme_d_sharding_20260921/`` and the v3 bundle must be byte-identical to the freeze commit."""
    rel_paths = sorted(str(p.relative_to(ROOT)) for p in V3_SHARDING_DIR.iterdir()
                       if p.is_file() and p.suffix in (".py", ".json"))
    rel_paths += sorted(str(p.relative_to(ROOT)) for p in (V3_BUNDLE_DIR, V3_BUNDLE_DIR / "runner")
                        if p.is_dir() for p in p.iterdir() if p.is_file() and p.suffix in (".py", ".json"))
    problems = []
    for rel in rel_paths:
        try:
            frozen = subprocess.run(["git", "show", f"{FREEZE_COMMIT}:{rel}"], cwd=ROOT, capture_output=True,
                                    check=True, timeout=30).stdout
        except subprocess.CalledProcessError as exc:
            problems.append({"path": rel, "problem": f"not_in_freeze_commit:{exc.stderr.decode()[:200]}"})
            continue
        if sha256_bytes(frozen) != sha256_file(ROOT / rel):
            problems.append({"path": rel, "problem": "differs_from_freeze_commit"})
    detail = {"freeze_tag": FREEZE_TAG, "freeze_commit": FREEZE_COMMIT, "files_checked": len(rel_paths), "problems": problems}
    return {"status": "FAIL" if problems else "PASS", "detail": detail}


def check_equivalence_gate_static(guard: rng_guard.RNGGuard) -> dict:
    """Bundle v4's own RNG-free hash/receipt gate for the optimized statistic (no dependency on preflight)."""
    SL = _load_isolated("m32_v4_qual_shard_layer", SHARDING_DIR / "shard_layer.py")
    R = SL.runner()
    try:
        static = R.equivalence_gate_static()
    except Exception as exc:                                         # noqa: BLE001
        return {"status": "FAIL", "detail": {"error": f"{type(exc).__name__}: {exc}"}}
    return {"status": "PASS", "detail": {**static, "bundle_sha256": R.bundle_sha256(),
                                         "shard_layer_sha256": SL.sha256_file(SL.LAYER_PATH)}}


def check_fixture_canary(guard: rng_guard.RNGGuard) -> dict:
    """A tiny RNG-free fixture run through work / audit / merge on the v4 shard layer (single process, single host)."""
    SL = _load_isolated("m32_v4_qual_shard_layer_canary", SHARDING_DIR / "shard_layer.py")
    F = SL.fixture_module()
    R = SL.runner()
    with tempfile.TemporaryDirectory(prefix="m32_v4_qual_canary_") as td:
        out = Path(td) / "o"
        try:
            with F.installed(R):
                ctx = SL.fixture_context(cells=(("N1/C0", 2), ("P1/C0", 2), ("P2/C0", 1)), shard_worlds=2)
                SL.work(out, ctx)
                report = SL.audit(out, ctx)
                if not report["ok"]:
                    return {"status": "FAIL", "detail": {"audit": report}}
                manifest = SL.merge(out, ctx)
        except Exception as exc:                                      # noqa: BLE001
            return {"status": "FAIL", "detail": {"error": f"{type(exc).__name__}: {exc}"}}
    return {"status": "PASS", "detail": {"worlds": ctx.total_worlds, "shards": len(ctx.shards),
                                         "merge_deterministic_sha256": manifest["deterministic_sha256"],
                                         "overall_cells_evaluated": manifest["overall"]["cells_evaluated"]}}


CHECKS = (("own_hash_bindings", lambda g: check_own_hash_bindings()),
          ("v3_untouched", lambda g: check_v3_untouched()),
          ("equivalence_gate_static", check_equivalence_gate_static),
          ("fixture_canary", check_fixture_canary))


def run() -> dict:
    guard = rng_guard.RNGGuard()
    results = {}
    with guard:
        for name, fn in CHECKS:
            results[name] = fn(guard)
    counters = guard.counters()
    overall_pass = all(r["status"] == "PASS" for r in results.values()) and not any(counters.values())
    body = {
        "schema": SCHEMA_RECEIPT,
        "tool_sha256": sha256_file(TOOL_PATH),
        "freeze_tag": FREEZE_TAG,
        "freeze_commit": FREEZE_COMMIT,
        "bindings": dict(EXPECTED),
        "checks": results,
        "check_order": [name for name, _ in CHECKS],
        "rng_guard_counters": counters,
        "no_rng_no_seed_generation_no_worlds_no_authorization_no_cloud": True,
        "scope": "single_host_in_process_deterministic_binding_only",
        "not_full_host_or_performance_qualification": True,
        "authorization_minted": False,
        "production_gate": {
            "authorized": False,
            "reason": "PRODUCTION_ENTRY_BLOCKED_PENDING_FUTURE_V4_OWNER_AUTHORIZATION_AND_HOST_QUALIFICATION",
            "requires": [
                "a separate owner authorization whose bundle_sha256 matches this exact v4 bundle "
                "(a v3 pilot/validation authorization is refused by construction: different bundle_sha256)",
                "shard_layer_sha256 and shard_worlds bound in that authorization, matching this exact v4 shard layer",
                "a measured optimized-benchmark receipt (owner-authorized, RNG) for runtime_estimate_cpu_hours",
                "a future full host/filesystem/performance qualification pass (out of scope for this tool)",
            ],
        },
        "verdict": "PASS" if overall_pass else "FAIL",
    }
    return self_hashed(body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("run",))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    receipt = run()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, sort_keys=True, indent=1, default=str) + "\n")
    print(json.dumps({"verdict": receipt["verdict"], "rng_guard_counters": receipt["rng_guard_counters"],
                      "out": str(args.out)}, sort_keys=True))
    return 0 if receipt["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
