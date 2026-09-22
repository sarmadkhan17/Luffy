import copy
import hashlib
import json
from pathlib import Path

import pytest

from trader.cognition import m32_protocol as p


def test_locked_artifact_and_zero_search_manifest():
    artifact = p.load_locked_artifact()
    manifest = p.zero_search_manifest(artifact)
    assert manifest["artifact_sha256"] == "3a5ea9bd5a8a36ccfb0c50f0f6a3b17fdcb618d27922f92b7028edef10a0f7a8"
    assert manifest["rows"] == 227
    assert manifest["evidence_classes"] == {
        "selected": 16, "ignored": 78, "skipped": 67,
        "failure": 1, "transition": 1, "sequence": 64,
        "outcome_rows": 163,
    }
    assert manifest["patterns_evaluated"] == 0
    assert manifest["null_draws_used"] == 0
    assert manifest["candidates"] == {"survivors": 0, "rejected": 0, "untestable": 0}
    assert all(manifest[k] is False for k in ("strategy_admission", "gate2", "live_handoff", "research_referee", "research_handoff"))


def test_artifact_is_not_mutated():
    before = hashlib.sha256(p.ARTIFACT.read_bytes()).hexdigest()
    p.load_locked_artifact()
    after = hashlib.sha256(p.ARTIFACT.read_bytes()).hexdigest()
    assert before == after == p.ARTIFACT_SHA256


def test_loader_refuses_other_path_and_modified_copy(tmp_path):
    with pytest.raises(p.ProtocolError, match="artifact_path_not_locked"):
        p.load_locked_artifact(tmp_path / "artifact.json")
    target = tmp_path / "artifact.json"
    target.write_bytes(p.ARTIFACT.read_bytes())
    with pytest.raises(p.ProtocolError, match="artifact_path_not_locked"):
        p.load_locked_artifact(target)


def test_pattern_id_is_canonical_and_allow_list_is_exact():
    one = {"family": "state", "atoms": [{"family": "forecast", "feature": "direction", "value": "long"}]}
    two = {"atoms": list(reversed(one["atoms"])), "family": "state"}
    assert p.pattern_id(one) == p.pattern_id(two)
    pair = {"family": "state", "atoms": [
        {"family": "forecast", "feature": "direction", "value": "long"},
        {"family": "forecast", "feature": "window_bars", "value": 6},
    ]}
    reverse_pair = {"family": "state", "atoms": list(reversed(pair["atoms"]))}
    assert p.pattern_id(pair) == p.pattern_id(reverse_pair)
    assert p.allowed_atom(one["atoms"][0])
    assert not p.allowed_atom({"family": "forecast", "feature": "price", "value": "high"})
    with pytest.raises(p.ProtocolError, match="feature_not_allow_listed"):
        p.canonical_pattern({"family": "state", "atoms": [{"family": "forecast", "feature": "price", "value": "high"}]})


def test_numeric_bins_ignore_missing_without_fabricating_zero():
    bins = p.numeric_bins([None, float("nan"), -2, 0, 5, 10])
    assert bins["n"] == 4
    assert bins["p33_33"] != 0 or bins["p66_67"] != 0
    with pytest.raises(p.ProtocolError, match="no_finite_values"):
        p.numeric_bins([None, float("nan")])


def test_common_cuts_use_pit_clock_or_known_clock():
    pit = {"point_in_time_features": True, "features": {"as_of_ms": 1789603501724}}
    obs = {"point_in_time_features": False, "features": {}, "clocks": {"known_ms": 1789643402650}}
    assert set(p.cut_labels(pit)) == {"hour", "weekday", "month", "regime"}
    assert p.cut_labels(pit)["month"] == "2026-09"
    assert p.cut_labels(obs)["month"] == "2026-09"
    with pytest.raises(p.ProtocolError, match="cut_clock_missing"):
        p.cut_labels({"point_in_time_features": False, "features": {}, "clocks": {}})


def test_support_counts_episodes_and_dependence_groups():
    rows = [
        {"row_id": "a", "underlying_id": "e1", "dependence_group": "g1", "labels": {"actual_status": "measured"}},
        {"row_id": "b", "underlying_id": "e1", "dependence_group": "g1", "labels": {"actual_status": "unavailable"}},
        {"row_id": "c", "underlying_id": "e2", "dependence_group": "g2", "labels": {"actual_status": "measured"}},
    ]
    result = p.support_accounting(rows, ["a", "b", "c"], "state")
    assert result["matched"] == 3
    assert result["observed"] == 2
    assert result["episodes"] == 2
    assert result["dependence_groups"] == 2
    assert result["statistically_testable"] is False
    assert result["status"] == "descriptive_only"


def test_frozen_budget_is_search_only_and_bounded():
    assert p.BUDGET["unique_patterns"] == 384
    assert p.BUDGET["null_draws_per_pattern_family_cut"] == 2000
    assert sum(1 for _ in p.FEATURE_FAMILIES) <= p.BUDGET["feature_families"]
