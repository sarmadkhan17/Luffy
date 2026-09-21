"""Guard the RNG-free M3.2 validation bundle REVISION 2: hash binding, recorded preflight, fail-closed gating."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v2_20260921"
TRUTH_SHA = "a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_manifest_binds_files_pins_and_frozen_truth():
    manifest = json.loads((BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    assert manifest["no_rng_no_seed_generation_no_worlds_no_search_no_gate2"] is True
    assert len(manifest["files_sha256"]) == 13 and manifest["bundle_revision"] == 2
    for rel, digest in {**manifest["files_sha256"], **manifest["pinned_inputs_sha256"]}.items():
        assert _sha(ROOT / rel) == digest, rel
    frozen = manifest["frozen_truth_package"]
    assert frozen["package_sha256"] == TRUTH_SHA == _sha(ROOT / frozen["package_dir"] / "manifest.sha256")
    assert frozen["rows"] == 21_120 and frozen["unresolved_rows"] == 0


def test_recorded_preflight_is_clean_and_rng_free():
    report = json.loads((BUNDLE / "artifacts/preflight_report.json").read_text())
    assert report["result"] == "PASS" and report["checks_total"] == report["checks_passed"] == 27
    assert report["rng_construction_attempts"] == 0 and report["rng_constructed"] is False
    assert report["worlds_generated_from_random_generator"] == 0 and report["seed_tuples_materialised"] == 0


def test_rng_modes_fail_closed_without_owner_authorization():
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    with tempfile.TemporaryDirectory() as td:
        bogus = Path(td) / "auth.json"
        bogus.write_text(json.dumps({"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "benchmark",
                                     "bundle_sha256": "0" * 64, "authorized_by": "owner"}))
        for mode in ("benchmark", "validation"):
            proc = subprocess.run([sys.executable, str(BUNDLE / "runner/scheme_d_runner.py"), mode,
                                   "--authorization", str(bogus), "--out", str(Path(td) / "out")],
                                  capture_output=True, text=True, env=env, timeout=120)
            assert proc.returncode != 0 and "authorization_mismatch" in proc.stderr
        assert not (Path(td) / "out").exists()


def test_v1_untouched_and_superseded_and_addendum_bound():
    v1 = ROOT / "scripts/m32_scheme_d_validation_bundle_20260921/BUNDLE_MANIFEST.json"
    manifest = json.loads((BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    assert _sha(v1) == "0b42c6a38c3329eff6ec760176b682003ff993b0976d45588f72eebb4be78a3e"
    assert manifest["supersedes"]["bundle_sha256"] == _sha(v1)
    addendum = manifest["clarification_addendum"]
    assert _sha(ROOT / addendum["path"]) == addendum["sha256"]
    config = json.loads((BUNDLE / "artifacts/config.json").read_text())
    changed = {d["id"] for d in config["owner_decisions"]["decisions"] if d["changed_vs_v1"]}
    assert changed == {"I3", "I6", "class_edge"} and config["bundle_revision"] == 2
