#!/usr/bin/env python3
"""Freeze (or --check) the complete M3.2 pre-RNG truth package.

Runs the independent verifier, re-derives every source/family-manifest hash,
requires every pinned input to be tracked in git, and writes FREEZE.json (or
compares against it with --check).  No RNG, no simulation, no search.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import verify_truth_package as V  # noqa: E402  (source registry + manifest lookup only)

BASE_COMMIT = "4998e368671e40576cb1b28b4f9e7dec1fd3a09f"
PKG = HERE / "package"
OUT = HERE / "FREEZE.json"
PROTOCOLS = (
    "docs/superpowers/specs/2026-09-19-m32-scheme-d-actual-statistic-validation-protocol.md",
    "docs/superpowers/specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md",
)
IMPLEMENTATION = (
    "scripts/m32_scheme_d_pre_rng_truth_20260921/generate_truth_package.py",
    "scripts/m32_scheme_d_pre_rng_truth_20260921/verify_truth_package.py",
    "scripts/m32_scheme_d_pre_rng_truth_20260921/freeze_package.py",
    "tests/test_m32_pre_rng_truth_package.py",
    "tests/test_m32_remaining_categorical_kernels.py",
    "tests/test_m32_pre_rng_freeze.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tracked(rel: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                          capture_output=True).returncode == 0


def build() -> dict:
    proc = subprocess.run([sys.executable, str(HERE / "verify_truth_package.py")], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"fail closed: verifier failed\n{proc.stderr[-600:]}")
    report = json.loads(proc.stdout)
    cert = json.loads((PKG / "proof_certificate.json").read_text())
    checks = {
        "verifier_result": report["result"], "independent_implementation": report["independent_implementation"],
        "freeze_possible": cert["freeze_possible"] and report["freeze_possible"],
        "cells": report["cells_checked"], "rows": report["rows_checked"],
        "unresolved_rows": report["unresolved_rows"], "unresolved_cells": cert["unresolved_cells"],
        "uncertified_categorical_kernels": report["uncertified_categorical_kernels"],
        "rng_constructed": report["rng_constructed"] or cert["rng_constructed"],
        "regression_vs_v3": report["regression_vs_v3"],
    }
    ok = (checks["verifier_result"] == "PASS" and checks["freeze_possible"] is True and checks["cells"] == 55
          and checks["rows"] == 55 * 384 == 21120 and checks["unresolved_rows"] == 0
          and checks["unresolved_cells"] == [] and checks["uncertified_categorical_kernels"] == 0
          and checks["rng_constructed"] is False and cert["uncertified_categorical_kernels"] == [])
    if not ok:
        raise SystemExit(f"fail closed: freeze conditions not met: {checks}")

    sources, manifests = {}, {}
    for sid, (path, manifest, key) in sorted(V.D.items()):
        recorded = V.manifest_hash(manifest, key)
        if not (sha(ROOT / path) == cert["sources"][sid]["sha256"] == recorded):
            raise SystemExit(f"fail closed: source/manifest hash disagreement {sid}")
        sources[sid] = {"path": path, "sha256": sha(ROOT / path), "family_manifest": manifest,
                        "family_manifest_key": key, "family_manifest_recorded_sha256": recorded}
        manifests[manifest] = sha(ROOT / manifest)
    v3 = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260920"
    v3cert = json.loads((v3 / "certificates/proof_certificate.json").read_text())
    frozen_inputs = {rel: sha(ROOT / rel) for rel in sorted(v3cert["frozen_inputs"]["source_sha256"])}
    for rel, h in v3cert["frozen_inputs"]["source_sha256"].items():
        if frozen_inputs[rel] != h:
            raise SystemExit(f"fail closed: frozen input drift {rel}")
    v3_files = {str(p.relative_to(ROOT)): sha(p) for p in sorted((v3 / "certificates").rglob("*")) if p.is_file()}
    pinned = (sorted({s["path"] for s in sources.values()}) + sorted(manifests) + sorted(frozen_inputs)
              + sorted(v3_files) + list(PROTOCOLS) + list(IMPLEMENTATION)
              + [str(p.relative_to(ROOT)) for p in sorted(PKG.rglob("*")) if p.is_file()])
    untracked = sorted({p for p in pinned if not tracked(p)})
    listing = (PKG / "manifest.sha256").read_text()
    package_files = {str(p.relative_to(PKG)): sha(p) for p in sorted(PKG.rglob("*")) if p.is_file()}
    tree = hashlib.sha256("".join(f"{h}  {n}\n" for n, h in sorted(package_files.items())).encode()).hexdigest()
    return {
        "schema": "m3.2-pre-rng-truth-package-freeze.v1",
        "status": "FROZEN", "immutable": True,
        "certified_base_commit": BASE_COMMIT,
        "scope": "N0-N3 and P1-P7 x C0-C4: 55 cells x 384 rows = 21,120 analytic truth rows",
        "no_rng_no_search_no_gate2_no_protocol_change": True,
        "checks": checks,
        "package_sha256": sha(PKG / "manifest.sha256"),
        "package_sha256_definition": "SHA-256 of package/manifest.sha256, which lists proof_certificate.json and all 55 table hashes; "
                                     "proof_certificate.json in turn pins all 25 source hashes and the generator/verifier hashes",
        "package_tree_sha256": tree,
        "package_tree_sha256_definition": "SHA-256 of the sorted '<sha256>  <name>' lines of every file under package/",
        "package_files": package_files,
        "proof_certificate_sha256": sha(PKG / "proof_certificate.json"),
        "verification_result_sha256": sha(PKG / "verification_result.json"),
        "source_count": len(sources), "family_manifest_count": len(manifests),
        "sources": sources, "family_manifests": dict(sorted(manifests.items())),
        "v3_frozen_input_sha256": frozen_inputs, "v3_certificate_files_sha256": v3_files,
        "authority_documents_sha256": {p: sha(ROOT / p) for p in PROTOCOLS},
        "implementation_sha256": {p: sha(ROOT / p) for p in IMPLEMENTATION if (ROOT / p).exists()},
        "untracked_pinned_inputs": untracked,
        "downstream_authorization": {
            "authorized": ("Assemble and independently verify the remaining RNG-free protocol section 14 freeze items "
                           "(config, membership and effect-assignment tables, C0-C4 loading/covariance PSD records, "
                           "runner and deterministic check suite, requirements lock, hash verification, import isolation) "
                           "as one SHA-256-manifested pre-run freeze bundle that consumes this package unchanged; "
                           "then request owner authorization."),
            "not_authorized": ["any RNG construction", "validation worlds or seed tuples", "benchmark run",
                               "historical search", "Gate 2 or held-out look", "referee/handoff",
                               "any protocol, scenario, truth-row or threshold change", "demo/live action"],
        },
        "immutability_policy": ("Do not edit any file listed here. Any change to a listed hash invalidates the freeze; "
                                "revision requires a new dated protocol and package."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    payload = build()
    text = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    if args.check:
        frozen = json.loads(OUT.read_text())
        if frozen["untracked_pinned_inputs"]:
            print("freeze records untracked inputs", frozen["untracked_pinned_inputs"]); return 1
        drift = [k for k in payload if k != "untracked_pinned_inputs" and payload[k] != frozen.get(k)]
        if drift:
            print("freeze drift:", drift); return 1
        print(json.dumps({"result": "FROZEN_INTACT", "package_sha256": frozen["package_sha256"]}))
        return 0
    if payload["untracked_pinned_inputs"]:
        raise SystemExit(f"fail closed: untracked pinned inputs {payload['untracked_pinned_inputs']}")
    OUT.write_text(text)
    print(json.dumps({"package_sha256": payload["package_sha256"], "tree": payload["package_tree_sha256"],
                      "sources": payload["source_count"], "family_manifests": payload["family_manifest_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
