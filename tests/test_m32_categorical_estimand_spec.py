"""Equivalence proof for the frozen M3.2 categorical macro-F1 estimand spec.

The spec at ``scripts/m32_categorical_equivalence_20260920/estimand_spec.json``
claims to describe exactly what ``trader.cognition.m32_search._metric`` computes
on its categorical branch.  This module holds a tiny replay helper written only
from that spec and asserts it reproduces production on representative cases,
including the ones where the estimand is easy to get subtly wrong: missing
labels, a class that is never predicted, an empty nonmatched stratum, and an
encounter-order-dependent tie.

Deterministic by construction: no RNG, no synthetic world generation, no
certificate kernels.  Only the production ``_metric`` is imported.
"""

from __future__ import annotations

import json
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

from trader.cognition.m32_search import _metric

SPEC_PATH = (Path(__file__).resolve().parents[1]
             / "scripts" / "m32_categorical_equivalence_20260920"
             / "estimand_spec.json")

LABEL = "case_kind"


# --------------------------------------------------------------------------
# spec loading and validation
# --------------------------------------------------------------------------

class SpecViolation(AssertionError):
    """The frozen spec does not say what this replay helper implements."""


def load_spec(path: Path = SPEC_PATH) -> dict:
    """Load the frozen spec and validate every field the replay depends on."""
    spec = json.loads(path.read_text())

    if spec.get("status") != "frozen":
        raise SpecViolation("spec is not frozen")
    if spec.get("spec_id") != "m32-categorical-macro-f1-estimand":
        raise SpecViolation("unexpected spec_id")

    empirical = spec["empirical_estimand"]
    step_ids = [step["id"] for step in empirical["steps"]]
    expected_steps = [
        "1_target_extraction",
        "2_empirical_target_filtering",
        "3_fit_strata",
        "4_untestable_guard",
        "5_evaluation_population",
        "6_predictors",
        "7_class_set",
        "8_per_class_counts",
        "9_per_class_f1",
        "10_macro_average",
        "11_baseline",
        "12_score",
        "13_lift",
        "14_effect",
    ]
    if step_ids != expected_steps:
        raise SpecViolation(f"unexpected step sequence: {step_ids}")

    steps = {step["id"]: step for step in empirical["steps"]}
    if steps["9_per_class_f1"]["zero_denominator_value"] != 0.0:
        raise SpecViolation("zero-denominator F1 is not 0.0")
    if steps["10_macro_average"]["weighting"] != "unweighted":
        raise SpecViolation("macro average is not unweighted")
    if steps["10_macro_average"]["denominator"] != "len(classes)":
        raise SpecViolation("macro denominator is not len(classes)")
    if "sorted(set(all_labels), key=str)" not in steps["7_class_set"]["rule"]:
        raise SpecViolation("class set rule is not sorted(set(...), key=str)")
    if "most_common(1)[0][0]" not in steps["6_predictors"]["rule"]:
        raise SpecViolation("predictors are not Counter.most_common majorities")
    fallback = steps["6_predictors"]["no_nonmatched_fallback"]
    if "falls back to hit_majority" not in fallback or "exactly 0.0" not in fallback:
        raise SpecViolation("no-nonmatched fallback is not documented")
    if steps["13_lift"]["rule"] != "signed_effect = score - baseline":
        raise SpecViolation("lift is not score - baseline")
    if steps["14_effect"]["rule"] != "effect = abs(signed_effect)":
        raise SpecViolation("effect is not abs(signed_effect)")

    smoothing = empirical["no_smoothing_or_clipping"]
    for key in ("additive_smoothing", "prior_or_pseudocounts", "clipping_or_winsorizing"):
        if smoothing[key] != "none":
            raise SpecViolation(f"spec permits {key}")

    if spec["tie_rules"]["empirical"]["rule"] != "first_encounter":
        raise SpecViolation("empirical tie rule is not first_encounter")
    if spec["tie_rules"]["analytic"]["rule"] != "fail_closed":
        raise SpecViolation("analytic tie rule is not fail_closed")

    analytic = spec["analytic_population_model"]
    if [Fraction(x) for x in analytic["base_probabilities"]] != [
            Fraction(13, 20), Fraction(1, 4), Fraction(1, 10)]:
        raise SpecViolation("analytic base probabilities are not (13/20, 1/4, 1/10)")
    if analytic["population_class_model"]["formula"] != \
            "p = (13/20 * g, 1/4 * g, 1 - 9/10 * g)":
        raise SpecViolation("population class model formula changed")
    if analytic["sequential_or_switch"]["survival_per_injection"] != \
            "1 - q_switch = 10 / (9 + odds_ratio)":
        raise SpecViolation("sequential OR survival formula changed")
    if analytic["probability_mass_f1"]["macro"] != \
            "sum of the three per-class F1 values / 3":
        raise SpecViolation("probability-mass macro average changed")

    return spec


# --------------------------------------------------------------------------
# replay helper, written from the spec only
# --------------------------------------------------------------------------

def _first_encounter_majority(labels):
    """Spec tie_rules.empirical: Counter.most_common(1), first-encounter order."""
    return Counter(labels).most_common(1)[0][0]


def _macro_f1(values, matched, classes, pred_match, pred_nonmatch) -> Fraction:
    """Spec steps 5, 8, 9, 10: counts over all valid rows, unweighted macro."""
    scores = []
    for cls in classes:
        tp = fp = fn = 0
        for row_id, value in values:
            predicted = pred_match if row_id in matched else pred_nonmatch
            if value == cls and predicted == cls:
                tp += 1
            elif value != cls and predicted == cls:
                fp += 1
            elif value == cls and predicted != cls:
                fn += 1
        denominator = 2 * tp + fp + fn
        scores.append(Fraction(0) if denominator == 0
                      else Fraction(2 * tp, denominator))
    return sum(scores, Fraction(0)) / len(classes)


def replay_metric(spec: dict, rows, matched, label: str = LABEL) -> dict:
    """Replay the frozen categorical estimand exactly, in exact rationals."""
    if label == "persistence":
        raise SpecViolation("persistence is out of spec scope")
    if label not in spec["scope"]["covers"][0]:
        raise SpecViolation(f"label family {label!r} is not in spec scope")

    # step 1 + 2: target extraction, then empirical filtering of None targets.
    values = [(row["row_id"], (row.get("labels") or {}).get(label)) for row in rows]
    values = [(row_id, value) for row_id, value in values if value is not None]

    # step 3: two fit strata, matched and nonmatched, in row order.
    hit = [(row_id, value) for row_id, value in values if row_id in matched]
    nonhit = [value for row_id, value in values if row_id not in matched]

    # step 4: untestable guard.
    if not hit or not values:
        return {"effect": None, "signed_effect": None, "observed": len(hit),
                "baseline": None, "status": "untestable"}

    all_labels = [value for _, value in values]

    # step 6: majorities, with the no-nonmatched fallback.
    majority = _first_encounter_majority(all_labels)
    hit_majority = _first_encounter_majority([value for _, value in hit])
    nonhit_majority = _first_encounter_majority(nonhit) if nonhit else hit_majority

    # step 7: class set.
    classes = sorted(set(all_labels), key=str)

    # steps 11, 12, 13, 14.
    baseline = _macro_f1(values, matched, classes, majority, majority)
    score = _macro_f1(values, matched, classes, hit_majority, nonhit_majority)
    signed = score - baseline
    return {"effect": abs(signed), "signed_effect": signed, "observed": len(hit),
            "baseline": baseline, "score": score, "status": "scored"}


def analytic_argmax(masses) -> int:
    """Spec tie_rules.analytic: fail closed instead of breaking a tie."""
    best = max(masses)
    winners = [index for index, mass in enumerate(masses) if mass == best]
    if len(winners) != 1:
        raise SpecViolation("analytic argmax tie: refusing to break it")
    return winners[0]


def analytic_macro_f1_lift(q: Fraction, conditional_hit, conditional_miss) -> Fraction:
    """Spec analytic_population_model.probability_mass_f1, exact rationals."""
    population = tuple(q * conditional_hit[c] + (1 - q) * conditional_miss[c]
                       for c in range(3))
    pred_hit = analytic_argmax(conditional_hit)
    pred_miss = analytic_argmax(conditional_miss)
    pred_base = analytic_argmax(population)

    def score(a: int, b: int) -> Fraction:
        values = []
        for cls in range(3):
            tp = (q * conditional_hit[cls] if a == cls else Fraction(0)) + (
                (1 - q) * conditional_miss[cls] if b == cls else Fraction(0))
            predicted = (q if a == cls else Fraction(0)) + (
                (1 - q) if b == cls else Fraction(0))
            fp = predicted - tp
            fn = population[cls] - tp
            values.append(Fraction(0) if 2 * tp + fp + fn == 0
                          else 2 * tp / (2 * tp + fp + fn))
        return sum(values, Fraction(0)) / 3

    return score(pred_hit, pred_miss) - score(pred_base, pred_base)


# --------------------------------------------------------------------------
# case construction
# --------------------------------------------------------------------------

def _rows(labels):
    """Build rows in the given order; a None label is a missing target."""
    return [{"row_id": f"row-{i}",
             "features": {"direction": 1},
             "labels": {"case_kind": label}}
            for i, label in enumerate(labels)]


def _ids(rows, indices):
    return {rows[i]["row_id"] for i in indices}


def _case(labels, matched_indices):
    rows = _rows(labels)
    return rows, _ids(rows, matched_indices)


# Each case: (name, labels in row order, matched row indices, expected result).
# Expected values are exact rationals derived by hand from the frozen spec.
CASES = [
    (
        # Matched and nonmatched share the global majority -> exact null lift.
        "common_majority_null",
        ["a"] * 7 + ["b"] * 3,
        [0, 1, 2, 7],  # 3 x "a", 1 x "b"
        {"status": "scored", "observed": 4,
         "baseline": Fraction(7, 17), "score": Fraction(7, 17),
         "signed_effect": Fraction(0), "effect": Fraction(0)},
    ),
    (
        # Distinct stratum majorities -> nonzero lift.
        "distinct_majorities",
        ["b", "b", "b", "a", "a", "a", "a", "a", "b", "a"],
        [0, 1, 2, 3],  # 3 x "b", 1 x "a"; nonmatched 5 x "a", 1 x "b"
        {"status": "scored", "observed": 4,
         "baseline": Fraction(3, 8), "score": Fraction(19, 24),
         "signed_effect": Fraction(5, 12), "effect": Fraction(5, 12)},
    ),
    (
        # Same case with missing labels interleaved on both strata: the
        # None rows must vanish entirely and change nothing.
        "missing_label_exclusion",
        ["b", None, "b", "b", "a", None, "a", "a", "a", "a", "a", "b", None],
        [0, 1, 2, 3, 4, 5, 12],  # includes three None rows
        {"status": "scored", "observed": 4,
         "baseline": Fraction(3, 8), "score": Fraction(19, 24),
         "signed_effect": Fraction(5, 12), "effect": Fraction(5, 12)},
    ),
    (
        # Class "c" is present in the data but predicted by neither the
        # baseline nor the scored predictor: it still contributes a 0.0 term
        # to both unweighted macro averages.
        "absent_class_averaging",
        ["b", "b", "b", "a", "a", "a", "a", "a", "c", "c"],
        [0, 1, 2, 3],  # 3 x "b", 1 x "a"; nonmatched 4 x "a", 2 x "c"
        {"status": "scored", "observed": 4,
         "baseline": Fraction(2, 9), "score": Fraction(122, 231),
         "signed_effect": Fraction(212, 693), "effect": Fraction(212, 693)},
    ),
    (
        # Every valid row is matched: nonhit is empty, nonhit_majority falls
        # back to hit_majority, and the lift is exactly 0.0.  The trailing
        # missing-label row is unmatched and must not create a nonmatched
        # stratum.
        "no_nonmatched_fallback",
        ["a", "a", "a", "a", "b", "b", None],
        [0, 1, 2, 3, 4, 5],
        {"status": "scored", "observed": 6,
         "baseline": Fraction(2, 5), "score": Fraction(2, 5),
         "signed_effect": Fraction(0), "effect": Fraction(0)},
    ),
    (
        # Matched stratum is tied 1-1 between "z" and "a"; "z" is encountered
        # first, so hit_majority is "z" and the lift is nonzero.
        "tie_encounter_order_z_first",
        ["z", "a", "a", "a", "a", "a", "z", "z"],
        [0, 1],
        {"status": "scored", "observed": 2,
         "baseline": Fraction(5, 13), "score": Fraction(31, 55),
         "signed_effect": Fraction(128, 715), "effect": Fraction(128, 715)},
    ),
    (
        # Identical label multiset and identical matched multiset, only the
        # encounter order of the two matched rows is swapped: hit_majority
        # becomes "a", the scored predictor collapses onto the baseline, and
        # the lift is exactly 0.0.  This is the order dependence the spec
        # records and the reason the analytic rule must fail closed.
        "tie_encounter_order_a_first",
        ["a", "z", "a", "a", "a", "a", "z", "z"],
        [0, 1],
        {"status": "scored", "observed": 2,
         "baseline": Fraction(5, 13), "score": Fraction(5, 13),
         "signed_effect": Fraction(0), "effect": Fraction(0)},
    ),
    (
        # No matched row at all -> untestable refusal, not a zero effect.
        "untestable_empty_matched",
        ["a", "a", "b", "b"],
        [],
        {"status": "untestable", "observed": 0,
         "baseline": None, "signed_effect": None, "effect": None},
    ),
    (
        # Matched rows exist but all of them have missing labels -> after
        # empirical filtering the matched stratum is empty: untestable.
        "untestable_matched_all_missing",
        [None, None, "a", "b"],
        [0, 1],
        {"status": "untestable", "observed": 0,
         "baseline": None, "signed_effect": None, "effect": None},
    ),
    (
        # No valid row survives filtering at all.
        "untestable_no_valid_rows",
        [None, None, None],
        [0, 1, 2],
        {"status": "untestable", "observed": 0,
         "baseline": None, "signed_effect": None, "effect": None},
    ),
]

# Analytic parity case: exact rational masses whose realized per-stratum class
# counts are exactly proportional to the masses, so the probability-mass lift
# must equal the empirical lift.  q = 1/4 over 20 rows.
ANALYTIC_Q = Fraction(1, 4)
ANALYTIC_HIT = (Fraction(1, 5), Fraction(0), Fraction(4, 5))       # 5 rows: 1, 0, 4
ANALYTIC_MISS = (Fraction(2, 3), Fraction(1, 5), Fraction(2, 15))  # 15 rows: 10, 3, 2
ANALYTIC_LABELS = (["c0"] + ["c2"] * 4                              # matched
                   + ["c0"] * 10 + ["c1"] * 3 + ["c2"] * 2)         # nonmatched
ANALYTIC_MATCHED = list(range(5))
ANALYTIC_EXPECTED = Fraction(3488, 13299)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_spec_loads_and_validates():
    spec = load_spec()
    assert spec["version"] == "1.0.0"
    assert spec["frozen_on"] == "2026-09-20"
    authority = [entry["path"] for entry in spec["authority"]["empirical"]]
    assert "trader/cognition/m32_search.py" in authority


def test_spec_analytic_base_is_survival_one():
    """p = (13/20*g, 1/4*g, 1 - 9/10*g) collapses to the base at g = 1."""
    load_spec()
    g = Fraction(1)
    p = (Fraction(13, 20) * g, Fraction(1, 4) * g, 1 - Fraction(9, 10) * g)
    assert p == (Fraction(13, 20), Fraction(1, 4), Fraction(1, 10))
    assert sum(p) == 1


@pytest.mark.parametrize("odds_ratio", [Fraction(3), Fraction(2), Fraction(3, 2)])
def test_spec_sequential_or_survival_matches_switch_probability(odds_ratio):
    """1 - q_switch = 10/(9+OR), and g composes multiplicatively."""
    load_spec()
    base = Fraction(1, 10)
    target = odds_ratio * base / (1 - base + odds_ratio * base)
    q_switch = (target - base) / (1 - base)
    assert 1 - q_switch == Fraction(10, 9 + odds_ratio)
    for count in range(4):
        g = Fraction(10, 9 + odds_ratio) ** count
        p = (Fraction(13, 20) * g, Fraction(1, 4) * g, 1 - Fraction(9, 10) * g)
        assert sum(p) == 1
        assert all(0 <= x <= 1 for x in p)


def test_analytic_argmax_fails_closed_on_tie():
    load_spec()
    with pytest.raises(SpecViolation):
        analytic_argmax((Fraction(1, 2), Fraction(1, 2), Fraction(0)))
    assert analytic_argmax((Fraction(1, 2), Fraction(1, 4), Fraction(1, 4))) == 0


@pytest.mark.parametrize("name,labels,matched_indices,expected",
                         CASES, ids=[case[0] for case in CASES])
def test_replay_matches_production(name, labels, matched_indices, expected):
    spec = load_spec()
    rows, matched = _case(labels, matched_indices)

    produced = _metric(rows, matched, LABEL, {})
    replayed = replay_metric(spec, rows, matched, LABEL)

    assert produced["status"] == expected["status"] == replayed["status"]
    assert produced["observed"] == expected["observed"] == replayed["observed"]

    if expected["status"] == "untestable":
        for key in ("effect", "signed_effect", "baseline"):
            assert produced[key] is None
            assert replayed[key] is None
        assert "score" not in produced and "score" not in replayed
        return

    for key in ("baseline", "score", "signed_effect", "effect"):
        assert replayed[key] == expected[key], f"{name}: replay {key}"
        assert produced[key] == pytest.approx(float(expected[key]), rel=1e-12,
                                              abs=1e-12), f"{name}: production {key}"


def test_tie_order_actually_changes_the_result():
    """The two tie cases share a label multiset but not a lift."""
    spec = load_spec()
    first, second = CASES[5], CASES[6]
    assert sorted(first[1]) == sorted(second[1])
    assert Counter(first[1][i] for i in first[2]) == \
        Counter(second[1][i] for i in second[2])
    a_rows, a_matched = _case(first[1], first[2])
    b_rows, b_matched = _case(second[1], second[2])
    a = _metric(a_rows, a_matched, LABEL, {})
    b = _metric(b_rows, b_matched, LABEL, {})
    assert a["signed_effect"] != b["signed_effect"]
    assert replay_metric(spec, a_rows, a_matched)["signed_effect"] != \
        replay_metric(spec, b_rows, b_matched)["signed_effect"]


def test_probability_mass_lift_matches_production_on_proportional_counts():
    """The analytic mass formulas reproduce the empirical lift exactly."""
    spec = load_spec()
    assert sum(ANALYTIC_HIT) == 1 and sum(ANALYTIC_MISS) == 1

    analytic = analytic_macro_f1_lift(ANALYTIC_Q, ANALYTIC_HIT, ANALYTIC_MISS)
    assert analytic == ANALYTIC_EXPECTED

    rows, matched = _case(ANALYTIC_LABELS, ANALYTIC_MATCHED)
    # the realized counts are exactly proportional to the analytic masses
    assert len(rows) == 20 and len(matched) == 5
    replayed = replay_metric(spec, rows, matched)
    produced = _metric(rows, matched, LABEL, {})

    assert replayed["signed_effect"] == analytic
    assert produced["signed_effect"] == pytest.approx(float(analytic),
                                                      rel=1e-12, abs=1e-12)
