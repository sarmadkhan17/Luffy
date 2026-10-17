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
    WorldRefused,
    actual_statistic_vector,
    bh_rejections,
    blocked_network,
    derive_n1_null_mad,
    draw_block_map,
    exclusive_json,
    generate_truth_tables,
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


def test_symbolic_truth_tables_are_complete_in_shape_and_honestly_refuse_ambiguity():
    truth = generate_truth_tables()
    # v2 records ambiguity on each hypothesis instead of a redundant boolean.
    # Derive the same predicate from every row; never assume missing means true.
    def fully_classified(cell):
        assert all(r['classification'] in {'true_null', 'analytic_non_null', 'unclassifiable'}
                   for r in cell['records'])
        return all(r['classification'] != 'unclassifiable' for r in cell['records'])
    assert len(truth["cells"]) == 55
    assert all(len(cell["records"]) == HYPOTHESES for cell in truth["cells"])
    assert truth['schema'] == 'm3.2-scheme-d-symbolic-truth.v2'
    assert truth['unresolved_cells'] == sum(not fully_classified(c) for c in truth['cells'])
    assert not all(fully_classified(c) for c in truth['cells'])
    p1 = next(c for c in truth["cells"] if c.get("scenario") == 101 and c["correlation"] == 4)
    assert fully_classified(p1)
    assert p1["records"][0]["classification"] == "true_null"
    p3 = next(c for c in truth["cells"] if c.get("scenario") == 103 and c["correlation"] == 4)
    assert p3["records"][320]["classification"] == "true_null"
    p2 = next(c for c in truth["cells"] if c.get("scenario") == 102 and c["correlation"] == 4)
    assert p2["records"][128]["sign"] == 1
    assert p2["records"][143]["sign"] == 1
    assert p2["records"][144]["sign"] == -1
    p4_c0 = next(c for c in truth["cells"] if c.get("scenario") == 104 and c["correlation"] == 0)
    assert fully_classified(p4_c0)
    p4_c1 = next(c for c in truth["cells"] if c.get("scenario") == 104 and c["correlation"] == 1)
    assert not fully_classified(p4_c1)


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


def test_actual_statistic_harness_calls_all_384_imported_statistics(monkeypatch):
    from collections import Counter
    from trader.cognition import m32_scheme_d_validation as D
    rows = np.arange(D.ROWS)
    memberships = (rows[:, None] + np.arange(HYPOTHESES)[None, :]) % 4 == 0
    world = D.SyntheticWorld(0, 1, 0, memberships, rows % 3,
        (rows % 17).astype(float), np.ones(D.ROWS, dtype=bool), n0_strata(),
        valid_metadata(n0_strata()))
    metric = D._metric
    calls = []
    def measured(rows, matched, label, lookup):
        calls.append(label)
        return metric(rows, matched, label, lookup)
    def forbidden(*args, **kwargs):
        pytest.fail('pre-RNG computation reached a generator')
    monkeypatch.setattr(D, '_metric', measured)
    monkeypatch.setattr(D.np.random, 'default_rng', forbidden)
    monkeypatch.setattr(D, 'SeedRegistry', forbidden)
    values = actual_statistic_vector(world)
    assert HYPOTHESIS_TABLE[0].label == 'case_kind'
    assert HYPOTHESIS_TABLE[128].label == 'persistence'
    assert values.shape == (HYPOTHESES,)
    assert np.all(np.isfinite(values))
    assert Counter(calls) == {'case_kind': 128, 'persistence': 128, 'continuation': 128}
