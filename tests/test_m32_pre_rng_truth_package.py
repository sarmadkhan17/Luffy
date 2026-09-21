"""Guard the merged Scheme D pre-RNG truth package: independent verifier must pass."""
import json
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "scripts/m32_scheme_d_pre_rng_truth_20260921"


def test_truth_package_verifies_independently():
    proc = subprocess.run([sys.executable, str(PKG / "verify_truth_package.py")],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["result"] == "PASS" and report["rng_constructed"] is False
    assert report["cells_checked"] == 55 and report["rows_checked"] == 55 * 384
    cert = json.loads((PKG / "package/proof_certificate.json").read_text())
    assert cert["freeze_possible"] is (report["unresolved_rows"] == 0)
