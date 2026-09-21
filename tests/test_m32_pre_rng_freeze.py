"""The frozen M3.2 pre-RNG truth package must stay byte-identical to FREEZE.json."""
import json
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "scripts/m32_scheme_d_pre_rng_truth_20260921"


def test_freeze_is_intact():
    proc = subprocess.run([sys.executable, str(PKG / "freeze_package.py"), "--check"],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-500:] + proc.stderr[-500:]
    frozen = json.loads((PKG / "FREEZE.json").read_text())
    assert frozen["immutable"] is True and frozen["checks"]["freeze_possible"] is True
    assert frozen["checks"]["rows"] == 21120 and frozen["checks"]["unresolved_rows"] == 0
    assert frozen["untracked_pinned_inputs"] == []
