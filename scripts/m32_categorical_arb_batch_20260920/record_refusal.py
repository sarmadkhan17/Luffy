#!/usr/bin/env python3
"""Record the bounded rigorous batch attempt as a fail-closed certificate."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "m32_categorical_equivalence_20260920" / "certificate.json"
PRIOR = HERE.parent / "m32_categorical_arb_completion_20260920" / "certificate.json"
SPEC = HERE.parent / "m32_categorical_equivalence_20260920" / "estimand_spec.json"
INTEGRATOR = HERE / "certify_batch.py"
OUTPUT = HERE / "certificate.json"
CELLS = {f"P{s}/C{c}" for s in (4, 5, 6) for c in (3, 4)}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    base = json.loads(BASE.read_text())
    prior = json.loads(PRIOR.read_text())
    row_cells = defaultdict(set)
    for row in base["row_assignments"]:
        row_cells[row["kernel_id"]].add(row["cell"])
    selected = {
        kid: rec for kid, rec in prior["kernels"].items()
        if rec["classification"] == "refused" and row_cells[kid] <= CELLS
    }
    if len(selected) != 27 or set().union(*(row_cells[k] for k in selected)) != CELLS:
        raise SystemExit("fail closed: source scope is not exactly the remaining 27 kernels")
    rows = [r for r in base["row_assignments"] if r["kernel_id"] in selected]
    kernels = {}
    for kid, old in sorted(selected.items()):
        kernels[kid] = {
            "spec": old["spec"],
            "classification": "refused",
            "sign": None,
            "reason": "validated_arb_integral_resource_bound_before_enclosure",
            "strict_argmax": {"hit": None, "miss": None, "population": None},
            "signed_lift_enclosure": None,
            "attempt": {
                "method": "batched factorized adaptive validated Gauss-Legendre",
                "precision_bits": 224,
                "absolute_tolerance": "2e-13",
                "truncation": 16,
                "outward_rounding": "Arb midpoint-radius balls",
                "explicit_tail_bounds": {
                    "C3": "66*Q(16)",
                    "C4_nested": "4*Q(16)",
                    "C4_reduced": "2*Q(16)",
                },
                "batching": ["scenario population PGF", "equivalent conditional PGFs",
                             "zero-count factors", "singleton Gaussian convolutions"],
                "terminal_condition": "no rigorous enclosure emitted within bounded engineering run",
            },
        }
    payload = {
        "schema": "m3.2-categorical-arb-batch-completion.v1",
        "status": "FAIL_CLOSED",
        "scope": "only final 27 categorical kernels in P4-P6/C3-C4; persistence excluded",
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "p7_c4_persistence_touched": False,
        "source_certificate_sha256": sha(PRIOR),
        "base_certificate_sha256": sha(BASE),
        "estimand_spec_sha256": sha(SPEC),
        "integrator_implementation_sha256": sha(INTEGRATOR),
        "acceptance": {"strict_interval_argmax": True, "possible_ties_fail_closed": True,
                       "maximum_radius": "1e-12", "minimum_zero_exclusion": "1e-10",
                       "exact_null": "algebraic proof only"},
        "counts": {"kernels_attempted": 27, "kernels_certified": 0, "kernels_refused": 27,
                   "rows_attempted": len(rows), "rows_newly_classified": 0,
                   "true_null_kernels": 0, "non_null_kernels": 0,
                   "cells_newly_classified": 0},
        "newly_classified_cells": [],
        "remaining_unresolved_categorical_cells": sorted(CELLS),
        "categorical_truth_complete": False,
        "worst_accepted_signed_lift_enclosure": None,
        "runtime": {"longest_single_primary_attempt_seconds_at_least": 28200,
                    "peak_rss_kib_observed": 42512,
                    "note": "bounded attempt stopped without an enclosure; figures are operational evidence, not proof inputs"},
        "kernels": kernels,
        "implementation_sha256": sha(Path(__file__)),
    }
    OUTPUT.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    print(json.dumps({k: payload[k] for k in ("status", "counts",
                                               "remaining_unresolved_categorical_cells",
                                               "categorical_truth_complete", "runtime")}, indent=2))


if __name__ == "__main__":
    main()
