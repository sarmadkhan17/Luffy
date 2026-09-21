"""Deterministic, read-only validation primitives for the M3.2 slice.

This module intentionally does not search, score, draw nulls, or write
candidates.  It pins the approved historical artifact and exposes the frozen
vocabulary/identity/support contracts that the later search must use.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "docs/superpowers/artifacts/pit-dataset/2026-09-19-historical-discovery-artifact.json"
# The user-supplied value has 66 hex characters and is not a SHA-256 digest.
# This is the verified 64-hex digest of the unchanged artifact on disk.
ARTIFACT_SHA256 = "3a5ea9bd5a8a36ccfb0c50f0f6a3b17fdcb618d27922f92b7028edef10a0f7a8"
PROTOCOL_ID = "3a6e87c28a8f20e49bb148c4dacfc4b34040781b93d38fe7eeeda39fd78880bd"

LOCKED = {
    "dataset_id": "m3.1-historical-discovery-2026-09-19",
    "dataset_version": "5d4d1fc16549f2385e89fdafd6d19525069185ca0348566f53096a4ddaefddff",
    "schema_version": "pit-dataset.v1",
    "discovery_cut_ms": 1789830909506,
}

STATE_FEATURES = (
    "recent_move_bps", "bar_return_0_bps", "bar_return_1_bps",
    "bar_return_2_bps", "bar_return_3_bps", "bar_return_4_bps",
    "direction", "window_bars",
)
JOURNAL_FEATURES = (
    "action", "executed", "attribution_linked",
    "registration_time_availability", "skip_reason",
)
INVESTIGATION_FEATURES = ("family", "cohort", "sign", "catalog_id")
FEATURE_FAMILIES = STATE_FEATURES + JOURNAL_FEATURES + INVESTIGATION_FEATURES

SUPPORT_FLOORS = {
    "state": {"matched": 12, "observed": 8, "episodes": 3},
    "transition": {"matched": 8, "episodes": 3},
    "sequence": {"matched": 8, "episodes": 3},
}
BUDGET = {
    "feature_families": 32,
    "state_atoms": 96,
    "state_patterns": 256,
    "transition_patterns": 64,
    "sequence_patterns": 64,
    "unique_patterns": 384,
    "null_draws_per_pattern_family_cut": 2000,
    "additional_cut_dimensions": 3,
}
CUT_DIMENSIONS = ("hour", "weekday", "month", "regime")
OUTCOME_STATUSES = (
    "selected", "ignored", "skipped", "failed", "outcome_observed",
    "outcome_unknown", "not_eligible", "search_error",
)


class ProtocolError(ValueError):
    """The frozen input or protocol contract was violated."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def load_locked_artifact(path: Path = ARTIFACT) -> dict:
    """Load only the approved artifact, without touching any other source."""
    path = Path(path)
    _require(path.resolve() == ARTIFACT.resolve(), "artifact_path_not_locked")
    _require(path.is_file(), "artifact_not_found")
    _require(sha256_file(path) == ARTIFACT_SHA256, "artifact_sha256_mismatch")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    _require(artifact.get("schema_version") == "historical-pit-artifact.v1",
             "artifact_schema_mismatch")
    dataset = artifact.get("dataset")
    _require(isinstance(dataset, dict), "dataset_missing")
    declaration = dataset.get("declaration") or artifact.get("declaration") or {}
    _require(declaration.get("dataset_id") == LOCKED["dataset_id"], "dataset_id_mismatch")
    _require(dataset.get("dataset_version") == LOCKED["dataset_version"],
             "dataset_version_mismatch")
    _require(dataset.get("schema_version") == LOCKED["schema_version"],
             "dataset_schema_mismatch")
    _require(declaration.get("discovery_cut_ms") == LOCKED["discovery_cut_ms"],
             "discovery_cut_mismatch")
    sufficiency = artifact.get("sufficiency") or {}
    _require(sufficiency.get("status") == "sufficient", "sufficiency_not_accepted")
    _require(sufficiency.get("population_sampling_claim") is False,
             "population_sampling_claim_present")
    _require(artifact.get("replay_passed") is True, "artifact_replay_not_passed")
    source_meta = dataset.get("source_meta") or {}
    _require(not source_meta.get("adapter_rejections"), "adapter_rejections_present")
    rows = dataset.get("rows")
    _require(isinstance(rows, list), "dataset_rows_missing")
    return artifact


def rows_of(artifact: dict) -> list[dict]:
    return list(artifact["dataset"]["rows"])


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _state_value(row: dict, name: str):
    features = row.get("features") or {}
    if name.startswith("bar_return_"):
        i = int(name.rsplit("_", 2)[1])
        values = features.get("bar_returns_bps")
        return values[i] if isinstance(values, list) and i < len(values) else None
    if name in STATE_FEATURES:
        return features.get(name)
    return features.get(name)


def allowed_atom(atom: dict) -> bool:
    """Return whether an atom is exactly in the frozen allow-list."""
    if not isinstance(atom, dict) or set(atom) - {"family", "feature", "value"}:
        return False
    family, feature = atom.get("family"), atom.get("feature")
    allowed = {
        "forecast": STATE_FEATURES,
        "journal": JOURNAL_FEATURES,
        "investigation": INVESTIGATION_FEATURES,
    }
    return family in allowed and feature in allowed[family] and "value" in atom


def canonical_pattern(pattern: dict) -> dict:
    """Canonicalize and validate a state/transition/sequence pattern."""
    _require(isinstance(pattern, dict), "pattern_not_object")
    family = pattern.get("family")
    _require(family in {"state", "transition", "sequence"}, "pattern_family_not_allowed")
    atoms = pattern.get("atoms")
    _require(isinstance(atoms, list) and atoms, "pattern_atoms_missing")
    _require(len(atoms) <= (3 if family == "state" else 2 if family == "transition" else 4),
             "pattern_complexity_exceeded")
    if family == "state":
        _require(all(allowed_atom(a) for a in atoms), "feature_not_allow_listed")
        _require(len({(a["family"], a["feature"], json.dumps(a["value"], sort_keys=True)) for a in atoms}) == len(atoms),
                 "duplicate_state_atom")
        atoms = sorted(atoms, key=lambda a: json.dumps(a, sort_keys=True, separators=(",", ":")))
        pattern = dict(pattern, atoms=atoms)
    else:
        _require(all(isinstance(a, dict) and set(a) <= {"from", "to", "feature", "family"}
                     for a in atoms), "linked_atom_not_allowed")
    return json.loads(json.dumps(pattern, sort_keys=True, separators=(",", ":")))


def pattern_id(pattern: dict) -> str:
    canonical = canonical_pattern(pattern)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def numeric_bins(values: Iterable[float]) -> dict:
    """Compute frozen global p33/p67 bins; missing/non-finite values are ignored."""
    xs = sorted(float(x) for x in values if _finite(x))
    _require(xs, "no_finite_values")
    def quantile(q):
        pos = (len(xs) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
    return {"p33_33": quantile(1 / 3), "p66_67": quantile(2 / 3), "n": len(xs)}


def cut_labels(row: dict) -> dict[str, str]:
    features = row.get("features") or {}
    if row.get("point_in_time_features"):
        clock = features.get("as_of_ms")
    else:
        clock = (row.get("clocks") or {}).get("known_ms")
    _require(isinstance(clock, int), "cut_clock_missing")
    dt = datetime.fromtimestamp(clock / 1000, timezone.utc)
    hour = ("00_05" if dt.hour < 6 else "06_11" if dt.hour < 12
            else "12_17" if dt.hour < 18 else "18_23")
    weekday = "mon_thu" if dt.weekday() < 4 else "fri_sun"
    regime = "unknown"
    for key in ("regime", "market_regime", "regime_label", "market_type"):
        if isinstance(features.get(key), str) and features[key]:
            regime = features[key]
            break
    return {"hour": hour, "weekday": weekday, "month": dt.strftime("%Y-%m"),
            "regime": regime}


def _label_observed(row: dict, label_family: str) -> bool:
    """Count the registered label, never substitute actual accounting."""
    labels = row.get("labels") or {}
    if label_family == "case_kind":
        if isinstance(labels.get("case_kind"), str) and bool(labels["case_kind"]):
            return True
        status = labels.get("actual_status")
        return status not in (None, "unknown", "unavailable")
    if label_family == "persistence":
        return _finite(labels.get("price_change_bps"))
    if label_family == "continuation":
        return bool((row.get("features") or {}).get("current_row_id"))
    return False


def support_accounting(rows: Iterable[dict], matched_ids: Iterable[str], family: str) -> dict:
    matched = set(matched_ids)
    selected = [r for r in rows if r.get("row_id") in matched]
    episodes = {r.get("underlying_id") or r.get("case_id") or r.get("row_id") for r in selected}
    groups = {r.get("dependence_group", "unknown") for r in selected}
    label_family = {"state": "case_kind", "persistence": "persistence",
                    "sequence": "continuation"}.get(family, family)
    observed = sum(1 for r in selected if _label_observed(r, label_family))
    return {"matched": len(selected), "observed": observed, "episodes": len(episodes),
            "dependence_groups": len(groups),
            "statistically_testable": len(groups) >= 3,
            "status": "descriptive_only" if len(groups) < 3 else "eligible"}


def zero_search_manifest(artifact: dict) -> dict:
    rows = rows_of(artifact)
    types = {}
    producers = {}
    case_kinds = {}
    outcome_statuses = {}
    base_rows = [r for r in rows if r.get("row_type") != "sequence"]
    for row in rows:
        key = row.get("row_type", "unknown")
        types[key] = types.get(key, 0) + 1
        producer = row.get("producer", "unknown")
        producers[producer] = producers.get(producer, 0) + 1
    for row in base_rows:
        labels = row.get("labels") or {}
        case = labels.get("case_kind")
        if case is not None:
            case_kinds[case] = case_kinds.get(case, 0) + 1
        status = labels.get("actual_status")
        if status is not None:
            outcome_statuses[status] = outcome_statuses.get(status, 0) + 1
    return {
        "schema": "m3.2-protocol-check.v1",
        "protocol_id": PROTOCOL_ID,
        "artifact_sha256": ARTIFACT_SHA256,
        "dataset_id": LOCKED["dataset_id"],
        "dataset_version": LOCKED["dataset_version"],
        "rows": len(rows),
        "row_types": dict(sorted(types.items())),
        "producers": dict(sorted(producers.items())),
        "case_kinds": dict(sorted(case_kinds.items())),
        "outcome_statuses": dict(sorted(outcome_statuses.items())),
        "evidence_classes": {
            "selected": case_kinds.get("selected_forecast", 0),
            "ignored": case_kinds.get("ignored_forecast", 0),
            "skipped": case_kinds.get("skip", 0),
            "failure": types.get("failure", 0),
            "transition": types.get("transition", 0),
            "sequence": types.get("sequence", 0),
            "outcome_rows": sum(outcome_statuses.values()),
        },
        "dependence_groups": dict(sorted({r.get("dependence_group", "unknown") for r in rows}.copy() and {g: sum(1 for r in rows if r.get("dependence_group", "unknown") == g) for g in {r.get("dependence_group", "unknown") for r in rows}}.items())),
        "feature_families": len(FEATURE_FAMILIES),
        "budget": BUDGET,
        "patterns_evaluated": 0,
        "null_draws_used": 0,
        "candidates": {"survivors": 0, "rejected": 0, "untestable": 0},
        "search_status": "protocol_validated_zero_search",
        "strategy_admission": False,
        "gate2": False,
        "live_handoff": False,
        "research_referee": False,
        "research_handoff": False,
    }
