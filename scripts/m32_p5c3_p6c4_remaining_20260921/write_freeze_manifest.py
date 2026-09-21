#!/usr/bin/env python3
"""Deterministically write freeze_manifest.json (SHA-256 of the frozen artifacts)."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
core = ("certify_remaining.py", "primary.json", "replay.json", "replay_result.json", "merge_and_check.py",
        "freeze_report.md", "run_all.sh")
extra = {str(p.relative_to(HERE)): h(p) for d in ("primary", "replay", "validation", "logs")
         for p in sorted((HERE / d).iterdir())}
manifest = {
    "schema": "m3.2-remaining-categorical-kernels-freeze.v1",
    "date": "2026-09-21", "status": "certified", "immutable": True,
    "scope": "P5/C3 x4 kernels (240 rows) and P6/C4 x5 kernels (208 rows) only",
    "kernels_attempted": 9, "kernels_certified": 9, "kernels_refused": 0,
    "rng_used": False, "search_performed": False, "protocol_changed": False,
    "requirements": {"signed_lift_radius_maximum": "1e-12", "non_null_zero_exclusion_minimum": "1e-10",
                     "classification": "strict hit/miss/population majority vs 20/31"},
    "inputs": {"frozen_estimand_spec": {
        "path": "scripts/m32_categorical_equivalence_20260920/estimand_spec.json",
        "sha256": h(ROOT / "scripts/m32_categorical_equivalence_20260920/estimand_spec.json")}},
    "sha256": {name: h(HERE / name) for name in core},
    "artifact_sha256": extra,
    "supersession_policy": "Do not edit this frozen artifact; append separately identified evidence under separately authorized protocol.",
}
(HERE / "freeze_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print("manifest written", len(extra), "artifacts")
