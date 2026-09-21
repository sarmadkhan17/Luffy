#!/usr/bin/env python3
"""Compare the P7/C4 persistence primary and higher-precision replay certificates."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path


HERE = Path(__file__).resolve().parent
PRIMARY = HERE / "primary_certificate.json"
REPLAY = HERE / "replay_certificate.json"
OUTPUT = HERE / "replay_result.json"
CLASSES = ("A_target", "A_non_target", "B_target", "B_non_target")


def dyadic(record: dict[str, int]) -> Fraction:
    value = Fraction(int(record["mantissa"]), 1)
    exponent = int(record["exponent"])
    return value * (2 ** exponent) if exponent >= 0 else value / (2 ** -exponent)


def interval(record: dict) -> tuple[Fraction, Fraction]:
    return dyadic(record["lower_dyadic"]), dyadic(record["upper_dyadic"])


def overlaps(left: dict, right: dict) -> bool:
    llo, lhi = interval(left)
    rlo, rhi = interval(right)
    return max(llo, rlo) <= min(lhi, rhi)


def main() -> int:
    primary = json.loads(PRIMARY.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    checks: dict[str, bool] = {}
    checks["population_median"] = overlaps(primary["population_median_interval"],
                                           replay["population_median_interval"])
    checks["population_MAD"] = overlaps(primary["population_MAD_interval"],
                                        replay["population_MAD_interval"])
    for name in CLASSES:
        checks[f"{name}_median"] = overlaps(primary["conditional_median_intervals"][name],
                                            replay["conditional_median_intervals"][name])
        checks[f"{name}_effect"] = overlaps(primary["signed_persistence_effect_intervals"][name],
                                            replay["signed_persistence_effect_intervals"][name])
        checks[f"{name}_classification"] = (primary["truth_classification"][name]
                                            == replay["truth_classification"][name])
    for name, row in primary["count_distributions"].items():
        checks[f"{name}_masses"] = all(overlaps(a, b) for a, b in
                                       zip(row, replay["count_distributions"][name]))
    pa, ra = primary["arithmetic"], replay["arithmetic"]
    pr, rr = primary["reduction"], replay["reduction"]
    distinct = {
        "precision": ra["precision_bits"] > pa["precision_bits"],
        "hermite_order": rr["hermite_order"] > pr["hermite_order"],
        "gaussian_bound": Fraction(rr["gaussian_bound_B"]) > Fraction(pr["gaussian_bound_B"]),
        "traversal": ra["reverse_traversal"] != pa["reverse_traversal"],
    }
    passed = (primary["status"] == replay["status"] == "PASS"
              and primary["implementation_sha256"] == replay["implementation_sha256"]
              and all(checks.values()) and all(distinct.values()))
    payload = {
        "schema": "m32.p7-c4-persistence-higher-precision-replay-check.v1",
        "result": "PASS" if passed else "FAIL_CLOSED",
        "primary_precision_bits": pa["precision_bits"],
        "replay_precision_bits": ra["precision_bits"],
        "distinct_configuration": distinct,
        "interval_overlap_and_classification_checks": checks,
        "largest_primary_effect_radius": max(
            float(primary["signed_persistence_effect_intervals"][n]["radius_decimal"])
            for n in CLASSES),
        "largest_replay_effect_radius": max(
            float(replay["signed_persistence_effect_intervals"][n]["radius_decimal"])
            for n in CLASSES),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "checks": len(checks)}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
