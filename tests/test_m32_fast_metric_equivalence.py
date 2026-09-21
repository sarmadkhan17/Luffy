"""Bit-exact equivalence of the optimized M3.2 metric against the production ``_metric`` (smoke subset).

The full deterministic suite (about 10 minutes) is scripts/m32_fast_metric_20260921/equivalence_suite.py; its
committed report is equivalence_report.json.  Fixtures use a fixture-only seed, never a protocol seed.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/m32_fast_metric_20260921"))

import equivalence_suite as S  # noqa: E402
from trader.cognition.m32_fast_metric import FastMetric, compensated_sum_columns  # noqa: E402


def test_smoke_equivalence_all_sections():
    t = S.Tally()
    S.section_compensated_sum(t, n=20_000)
    S.section_builder_identity(t)
    S.section_exhaustive(t, stride=81)
    S.section_adversarial(t, stride=409)
    S.section_fallback(t)
    S.section_random(t, 150)
    S.section_full_geometry(t, fixtures=1, maps=1, hypotheses=list(range(0, 384, 12)))
    assert t.mismatches == []
    assert t.comparisons > 200_000
    for key in ("hit_ties", "nonhit_ties", "overall_ties", "zero_lift", "persistence_even", "fallback_vectors"):
        assert t.coverage.get(key, 0) > 0, key


def test_compensated_sum_matches_builtin_sum_including_the_0_1_0_2_0_3_case():
    scores = [np.array([0.1]), np.array([0.2]), np.array([0.3])]
    assert compensated_sum_columns(scores)[0] == sum([0.1, 0.2, 0.3]) == 0.6      # naive left-to-right gives 0.6000000000000001


def test_unsupported_domains_fall_back_exactly_and_are_flagged():
    n = 12
    mem = np.ones((n, 3), dtype=bool)
    fm = FastMetric(mem, np.asarray([0, 10, 2] * 4), np.arange(n, dtype=float), np.ones(n, bool),
                    ["case_kind", "persistence", "continuation"], blocks=4, rows_per_block=3)
    assert fm.unsupported == "class_code_outside_0_9"
    t = S.Tally()
    S.compare(fm, {}, np.asarray([1, 0, 3, 2]), t, "fallback")
    assert t.mismatches == [] and t.comparisons == 3


def test_block_map_must_be_a_permutation():
    n = 12
    fm = FastMetric(np.ones((n, 1), dtype=bool), np.zeros(n, dtype=np.int64), np.arange(n, dtype=float), np.ones(n, bool),
                    ["case_kind"], blocks=4, rows_per_block=3)
    try:
        fm.statistics(np.asarray([0, 0, 1, 2]))
        raise AssertionError("non-permutation accepted")
    except ValueError:
        pass
