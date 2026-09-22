from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest

from trader.cognition.m32_scheme_d_validation import (
    BLOCKS,
    HYPOTHESES,
    HYPOTHESIS_TABLE,
    IntegrityError,
    N1_NULL_MAD,
    PHASE_BENCHMARK,
    SeedRegistry,
    WorldRefused,
    actual_statistic_vector,
    apply_injections,
    bh_rejections,
    blocked_network,
    derive_n1_null_mad,
    draw_block_map,
    exclusive_json,
    generate_truth_tables,
    generate_world,
    hypothesis_table,
    monte_carlo_p,
    orbit_size,
    validate_fixed_design,
    validate_world_metadata,
    valid_metadata,
    verify_frozen_environment,
)


def n0_strata():
    return tuple((1 + b % 4, (b // 3) % 2, 0) for b in range(BLOCKS))


def test_fixed_384_hypothesis_family_and_labels():
    validate_fixed_design()
    table = hypothesis_table()
    assert len(table) == HYPOTHESES
    assert [sum(h.label == label for h in table) for label in
            ("case_kind", "persistence", "continuation")] == [128, 128, 128]
    assert {sum(h.cluster == c for h in table) for c in range(32)} == {12}
    assert sum(h.sector == "A" for h in table) == 192


def test_seed_registry_allows_only_unique_phase_90_tuples():
    registry = SeedRegistry()
    registry.generator(PHASE_BENCHMARK, 0, 1, 0, 0)
    with pytest.raises(IntegrityError, match="duplicate_seed_tuple"):
        registry.generator(PHASE_BENCHMARK, 0, 1, 0, 0)
    with pytest.raises(IntegrityError, match="non_phase_90_rng_refused"):
        registry.generator(10, 0, 1, 0, 0)


def test_frozen_hashes_and_environment_match(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "1")
    assert verify_frozen_environment()["numpy"] == "2.5.2"
    with pytest.raises(IntegrityError, match="python_version_mismatch"):
        verify_frozen_environment(python_version="3.12.2", openblas_threads="1")


def test_n0_orbit_and_floor_are_exact():
    expected = math.factorial(8) ** 4 * math.factorial(4) ** 4
    assert orbit_size(n0_strata()) == expected
    assert validate_world_metadata(valid_metadata(n0_strata())) == expected


@pytest.mark.parametrize(("field", "reason"), [
    ("source_hashes_match", "source_hash_mismatch"),
    ("config_match", "config_mismatch"),
    ("between_block_independent", "between_block_dependence"),
    ("footprints_within_block", "block_footprint_crossing"),
    ("conditioned_mean_variance", "unconditioned_drift"),
    ("missingness_outcome_independent", "outcome_dependent_missingness"),
    ("confounders_in_D", "omitted_confounder"),
    ("clock_valid", "clock_violation"),
    ("links_valid", "link_crossing"),
])
def test_invalid_metadata_refuses_before_rng(field, reason):
    metadata = valid_metadata(n0_strata())
    metadata[field] = False
    with pytest.raises(WorldRefused) as exc:
        validate_world_metadata(metadata)
    assert reason in exc.value.reasons


def test_invalid_certificate_and_family_refuse():
    metadata = valid_metadata(n0_strata())
    metadata["certificate"] = None
    metadata["family_size"] = 383
    with pytest.raises(WorldRefused) as exc:
        validate_world_metadata(metadata)
    assert {"invalid_exchangeability_certificate", "family_size_not_384"} <= set(exc.value.reasons)


def test_bh_keeps_refusals_in_384_denominator():
    pvalues = [None] * HYPOTHESES
    pvalues[7] = 0.05 / HYPOTHESES
    assert bh_rejections(pvalues) == {7}
    pvalues[7] = 0.05 / (HYPOTHESES - 1)
    assert bh_rejections(pvalues) == set()


def test_p_value_and_conservative_tie_rule_inputs():
    assert monte_carlo_p(0, 767_999) == Fraction(1, 768_000)
    assert monte_carlo_p(767_999, 767_999) == 1


def test_sampling_can_include_identity_and_repeats_with_replacement():
    class IdentityRng:
        def permutation(self, values):
            return np.asarray(values).copy()
    groups = (np.arange(BLOCKS),)
    first = draw_block_map(IdentityRng(), groups)
    second = draw_block_map(IdentityRng(), groups)
    assert np.array_equal(first, np.arange(BLOCKS))
    assert np.array_equal(first, second)


def test_actual_statistic_harness_calls_all_384_imported_statistics():
    world = generate_world(correlation=0, dgp=1, world_index=0)
    values = actual_statistic_vector(world)
    assert values.shape == (HYPOTHESES,)
    assert np.all(np.isfinite(values))
    assert HYPOTHESIS_TABLE[0].label == "case_kind"
    assert HYPOTHESIS_TABLE[128].label == "persistence"


def test_phase90_injections_preserve_membership_masks_and_strata():
    registry = SeedRegistry()
    world = generate_world(correlation=0, dgp=1, world_index=4, registry=registry)
    injected = apply_injections(world, 104, registry=registry)
    assert np.array_equal(injected.memberships, world.memberships)
    assert np.array_equal(injected.observed, world.observed)
    assert injected.block_strata == world.block_strata
    assert np.any(injected.persistence != world.persistence)


def test_symbolic_truth_tables_are_complete_in_shape_and_honestly_refuse_ambiguity():
    truth = generate_truth_tables()
    assert len(truth["cells"]) == 55
    assert all(len(cell["records"]) == HYPOTHESES for cell in truth["cells"])
    assert not truth["fully_classified"]
    p1 = next(c for c in truth["cells"] if c.get("scenario") == 101 and c["correlation"] == 4)
    assert p1["fully_classified"]
    assert p1["records"][0]["classification"] == "true_null"
    p3 = next(c for c in truth["cells"] if c.get("scenario") == 103 and c["correlation"] == 4)
    assert p3["records"][320]["classification"] == "true_null"
    p2 = next(c for c in truth["cells"] if c.get("scenario") == 102 and c["correlation"] == 4)
    assert p2["records"][128]["sign"] == 1
    assert p2["records"][143]["sign"] == 1
    assert p2["records"][144]["sign"] == -1
    p4_c0 = next(c for c in truth["cells"] if c.get("scenario") == 104 and c["correlation"] == 0)
    assert p4_c0["fully_classified"]
    p4_c1 = next(c for c in truth["cells"] if c.get("scenario") == 104 and c["correlation"] == 1)
    assert not p4_c1["fully_classified"]


def test_n1_null_mad_derivation_matches_frozen_value():
    assert derive_n1_null_mad() == pytest.approx(N1_NULL_MAD, abs=2e-14)


def test_network_is_blocked_and_output_is_exclusive(tmp_path):
    import socket
    with blocked_network(), pytest.raises(IntegrityError, match="network_access_refused"):
        socket.socket()
    path = tmp_path / "receipt.json"
    exclusive_json(path, "{}\n")
    with pytest.raises(IntegrityError, match="output_path_collision"):
        exclusive_json(path, "{}\n")
