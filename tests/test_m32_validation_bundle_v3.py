"""Guard the RNG-free M3.2 validation bundle REVISION 3: hash binding, recorded preflight, fail-closed gating."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v3_20260921"
TRUTH_SHA = "a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_manifest_binds_files_pins_and_frozen_truth():
    manifest = json.loads((BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    assert manifest["no_rng_no_seed_generation_no_worlds_no_search_no_gate2"] is True
    assert len(manifest["files_sha256"]) == 15 and manifest["bundle_revision"] == 3
    for rel, digest in {**manifest["files_sha256"], **manifest["pinned_inputs_sha256"]}.items():
        assert _sha(ROOT / rel) == digest, rel
    frozen = manifest["frozen_truth_package"]
    assert frozen["package_sha256"] == TRUTH_SHA == _sha(ROOT / frozen["package_dir"] / "manifest.sha256")
    assert frozen["rows"] == 21_120 and frozen["unresolved_rows"] == 0


def test_recorded_preflight_is_clean_and_rng_free():
    report = json.loads((BUNDLE / "artifacts/preflight_report.json").read_text())
    assert report["result"] == "PASS" and report["checks_total"] == report["checks_passed"] == 33
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


def test_v2_v1_untouched_and_v3_supersedes_with_fast_metric_bound():
    root = ROOT / "scripts"
    v2 = root / "m32_scheme_d_validation_bundle_v2_20260921/BUNDLE_MANIFEST.json"
    v1 = root / "m32_scheme_d_validation_bundle_20260921/BUNDLE_MANIFEST.json"
    assert _sha(v2) == "397961d944f98f730eb723d537594d56428cf72db349449ad848678ad85f0c4e"
    assert _sha(v1) == "0b42c6a38c3329eff6ec760176b682003ff993b0976d45588f72eebb4be78a3e"
    manifest = json.loads((BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    assert manifest["supersedes"]["bundle_sha256"] == _sha(v2)
    assert manifest["fast_metric"]["equivalence"] == {"comparisons": 7_592_064, "statistic_vectors": 22_314,
                                                       "fixtures": 4_529, "mismatches": 0}
    config = json.loads((BUNDLE / "artifacts/config.json").read_text())
    assert config["bundle_revision"] == 3 and config["statistic"]["path"] == "fast_metric_exact_equivalent"
    assert {d["id"] for d in config["owner_decisions"]["decisions"] if d["changed_vs_v2"]} == {"I8"}
    receipt = json.loads((BUNDLE / "artifacts/equivalence_receipt.json").read_text())
    assert receipt["comparisons"] == 7_592_064 and receipt["mismatches"] == 0 and receipt["protocol_master_seed_used"] is False
    for key in ("module", "suite", "equivalence_report", "benchmark_result", "reference"):
        entry = config["fast_metric"][key]
        assert _sha(ROOT / entry["path"]) == entry["sha256"], key


def test_equivalence_gate_static_passes_and_rejects_drift():
    sys.path.insert(0, str(BUNDLE / "runner"))
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    import scheme_d_runner as R
    assert R.equivalence_gate_static()["mismatches"] == 0
    cfg = json.loads((BUNDLE / "artifacts/config.json").read_text())
    cfg["fast_metric"]["equivalence"]["mismatches"] = 1
    try:
        R.equivalence_gate_static(config=cfg)
        raise AssertionError("gate accepted a config claiming mismatches")
    except R.IntegrityError:
        pass
