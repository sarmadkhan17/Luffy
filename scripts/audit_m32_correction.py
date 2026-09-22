#!/usr/bin/env python3
"""Run the read-only M3.2 corrective sufficiency audit; never run search."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.cognition.m32_correction import AUDIT_FILENAME, audit, write_immutable

root = Path(__file__).resolve().parents[1]
report = audit()
path = root / "docs/superpowers/artifacts/m32-search" / AUDIT_FILENAME
print(write_immutable(report, path), path)
print({"power_pass": report["power_audit"]["pass"],
       "verified_outcomes": report["outcome_eligibility"]["verified_outcome_count_after"],
       "groups_before": report["dependence"]["before"],
       "groups_after": report["dependence"]["after"],
       "coverage": report["coverage"]})
