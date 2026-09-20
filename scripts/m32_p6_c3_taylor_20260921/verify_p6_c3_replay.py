#!/usr/bin/env python3
"""Deterministically compare the P6/C3 primary and higher-precision replay."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path


HERE = Path(__file__).resolve().parent
PRIMARY = HERE / "p6_c3_primary.json"
REPLAY = HERE / "p6_c3_replay.json"
OUTPUT = HERE / "p6_c3_replay_result.json"
FIELDS = (
    "conditional_hit_interval",
    "conditional_miss_interval",
    "population_interval",
    "joint_hit_interval",
    "joint_miss_interval",
    "signed_lift_interval",
)


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
    same_ids = set(primary["kernels"]) == set(replay["kernels"])
    rows = []
    for kernel_id in sorted(set(primary["kernels"]) | set(replay["kernels"])):
        if kernel_id not in primary["kernels"] or kernel_id not in replay["kernels"]:
            rows.append({"kernel_id": kernel_id, "result": "FAIL_CLOSED",
                         "reason": "kernel_set_mismatch"})
            continue
        left, right = primary["kernels"][kernel_id], replay["kernels"][kernel_id]
        overlap = {field: overlaps(left[field], right[field]) for field in FIELDS}
        classification_match = left["classification"] == right["classification"]
        winners_match = left["strict_argmax"] == right["strict_argmax"]
        ok = classification_match and winners_match and all(overlap.values())
        rows.append({"kernel_id": kernel_id, "result": "PASS" if ok else "FAIL_CLOSED",
                     "classification_match": classification_match,
                     "winner_triple_match": winners_match, "interval_overlap": overlap})
    passed = (primary["status"] == replay["status"] == "certified"
              and same_ids and len(rows) == 8 and all(row["result"] == "PASS" for row in rows))
    payload = {
        "schema": "m32.p6-c3-higher-precision-replay-check.v1",
        "result": "PASS" if passed else "FAIL_CLOSED",
        "primary_precision_bits": primary["precision_bits"],
        "replay_precision_bits": replay["precision_bits"],
        "distinct_configuration": {
            "precision": primary["precision_bits"] != replay["precision_bits"],
            "order": primary["taylor_order"] != replay["taylor_order"],
            "axis": primary["axis"] != replay["axis"],
            "inner_traversal": (primary["reverse_inner_traversal"]
                                != replay["reverse_inner_traversal"]),
        },
        "kernels_replayed": len(rows), "rows": rows,
    }
    if not all(payload["distinct_configuration"].values()):
        payload["result"] = "FAIL_CLOSED"
        passed = False
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "kernels_replayed": len(rows)},
                     sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
