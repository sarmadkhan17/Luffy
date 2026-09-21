"""Guard the nine remaining P5/C3 and P6/C4 categorical kernel certificates."""
import json
from fractions import Fraction
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "scripts/m32_p5c3_p6c4_remaining_20260921"
PKG = Path(__file__).resolve().parents[1] / "scripts/m32_scheme_d_pre_rng_truth_20260921/package"
MAJ = Fraction(20, 31)


def _iv(rec):
    def dy(d):
        return Fraction(int(d["mantissa"])) * Fraction(2) ** int(d["exponent"])
    return dy(rec["lower_dyadic"]), dy(rec["upper_dyadic"])


def test_nine_kernels_certified_and_replayed():
    primary, replay = (json.loads((D / f"{n}.json").read_text()) for n in ("primary", "replay"))
    check = json.loads((D / "replay_result.json").read_text())
    assert check["result"] == "PASS" and check["kernels_checked"] == 9
    assert primary["rng_used"] is False and primary["search_performed"] is False
    assert set(primary["kernels"]) == set(replay["kernels"]) and len(primary["kernels"]) == 9
    assert replay["precision_bits"] > primary["precision_bits"]
    for kid, k in primary["kernels"].items():
        for run in (k, replay["kernels"][kid]):
            assert run["status"] == "certified"
            assert _iv(run["conditional_hit_interval"])[1] < MAJ
            assert _iv(run["conditional_miss_interval"])[0] > MAJ
            assert _iv(run["population_interval"])[0] > MAJ
            lo, hi = _iv(run["signed_lift_interval"])
            assert (hi - lo) / 2 <= Fraction(1, 10**12) and lo >= Fraction(1, 10**10)


def test_truth_package_has_no_unresolved_rows():
    cert = json.loads((PKG / "proof_certificate.json").read_text())
    assert cert["unresolved_rows"] == 0 and cert["freeze_possible"] is True
    assert cert["uncertified_categorical_kernels"] == []
