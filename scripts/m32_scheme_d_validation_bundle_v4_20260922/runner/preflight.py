#!/usr/bin/env python3
"""RNG-free deterministic preflight/check suite (protocol section 14 item 6), bundle revision 4.

Revision 3 added the fail-closed gate for the exact-equivalent optimized statistic: hash binding of the reference
and optimized implementations and of the equivalence suite/results, the recorded equivalence receipt, a live
RNG-free bit-identity check (arithmetic-pattern fixtures against the production ``_metric``), fast-vs-reference
receipt identity, gate tamper tests, and the phase-90 benchmark core through a fixed-pattern stub.

Revision 4 applies the 2026-09-20 pre-RNG correction (protocol correction section 2-3): P1 (scenario 101) and P3
(scenario 103) are true-null perturbed-calibration scenarios, not power scenarios.  This file adds checks that
``acceptance.POWER_RULES``/``acceptance.FDR_SCENARIOS`` exclude 101/103, that ``cell_plan()`` tags their ten C0-C4
cells ``kind="perturbed_null"``, that the frozen (unchanged) truth tables mark every P1/P3 hypothesis true_null,
that injection dispatch (keyed by scenario, not kind) is unaffected, and that ``evaluate_cell(kind="perturbed_null")``
computes the Type-I false-positive gate correctly (true-null rejections count as false positives / FDP=1; zero
rejections pass; excessive rejections fail; refused worlds are excluded and still count toward the refusal blocker;
no power/bootstrap/marginal-calibration gate is present).

Every check is a fixed-input computation.  numpy/stdlib random generator
constructors are patched to raise for the whole run; the report records the
number of construction attempts (must be 0).  Fixtures are built from arithmetic
patterns, never from a random generator.  A stub that mimics the generator API
with fixed arithmetic patterns exercises the world-generation code paths.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent
ROOT = BUNDLE.parents[1]
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

GUARD = {"attempts": 0}


class RngGuard(BaseException):
    pass


def _trip(*_a, **_k):
    GUARD["attempts"] += 1
    raise RngGuard("random generator construction attempted during preflight")


_ORIGINAL = {n: getattr(np.random, n) for n in ("PCG64", "SeedSequence", "default_rng", "Generator", "RandomState")}
_ORIGINAL_PY = random.Random
for _n in _ORIGINAL:
    setattr(np.random, _n, _trip)
random.Random = _trip  # type: ignore[misc,assignment]

import scheme_d_runner as R  # noqa: E402
import acceptance as A  # noqa: E402
from trader.cognition import m32_scheme_d_validation as V  # noqa: E402

CHECKS: list = []


def check(fn):
    CHECKS.append(fn)
    return fn


class DeterministicStub:
    """NOT a random generator: fixed arithmetic patterns that mimic the API shapes."""

    def __init__(self, permutation="roll", uniform=0.0, geometric=None):
        self.permutation_mode, self.uniform_value, self.geometric_value = permutation, uniform, geometric

    @staticmethod
    def _n(size):
        return int(np.prod(size)) if size is not None else 1

    def _shape(self, arr, size):
        return arr.reshape(size) if size is not None else arr[0]

    def integers(self, lo, hi, size=None):
        n = self._n(size)
        return self._shape((np.arange(n) % (hi - lo) + lo).astype(np.int64), size)

    def random(self, size=None):
        n = self._n(size)
        return self._shape(np.full(n, self.uniform_value) if self.uniform_value is not None else
                           (np.arange(n) * 0.6180339887498949) % 1.0, size)

    def standard_normal(self, size=None):
        n = self._n(size)
        return self._shape(np.sin(np.arange(n) * 1.7) * 1.5, size)

    def standard_t(self, df, size=None):
        n = self._n(size)
        return self._shape(np.cos(np.arange(n) * 0.9) * 2.0, size)

    def geometric(self, p, size=None):
        n = self._n(size)
        if self.geometric_value is not None:
            return self._shape(np.full(n, self.geometric_value, dtype=np.int64), size)
        return self._shape((np.arange(n) % 3 + 1).astype(np.int64), size)

    def choice(self, a, size=None, p=None):
        n = self._n(size)
        return self._shape((np.arange(n) % a).astype(np.int64), size)

    def permutation(self, x):
        x = np.asarray(x)
        return x.copy() if self.permutation_mode == "identity" else np.roll(x, 1)


def fixture_world(density: int = 20, only: set[int] | None = None, dgp: int = 1):
    rows = np.arange(R.ROWS)
    categorical = ((rows * 7 + rows // 80) % 3).astype(np.int64)
    persistence = ((rows * 37) % 101) / 50.0 - 1.0 + 0.001 * (rows // 80)
    memberships = ((rows[:, None] * 13 + np.arange(384)[None, :] * 7 + (rows[:, None] // 80) * 5) % density == 0)
    if only is not None:
        keep = np.zeros(384, dtype=bool)
        keep[sorted(only)] = True
        memberships &= keep[None, :]
    strata = tuple((1 + b % 4, int((b // 3) % 2), 0) for b in range(R.BLOCKS))
    return V.SyntheticWorld(0, dgp, 0, memberships, categorical, persistence, np.ones(R.ROWS, dtype=bool), strata,
                            V.valid_metadata(strata))


def approx(a, b, tol=1e-12):
    assert abs(a - b) <= tol, (a, b)


def load(name: str):
    return json.loads((BUNDLE / "artifacts" / name).read_text())


# ------------------------------------------------------------------------- checks
@check
def fast_metric_hash_binding():
    cfg = load("config.json")["fast_metric"]
    gate = R.equivalence_gate_static()                          # raises on any hash / receipt / loaded-source drift
    assert cfg["reference"]["sha256"] == V.FROZEN_HASHES["trader/cognition/m32_search.py"]
    R.open_statistic_gate(gate)
    return {"module_sha256": cfg["module"]["sha256"], "reference_sha256": cfg["reference"]["sha256"],
            "suite_sha256": cfg["suite"]["sha256"], "equivalence_report_sha256": cfg["equivalence_report"]["sha256"],
            "benchmark_result_sha256": cfg["benchmark_result"]["sha256"], "commit": cfg["commit"]}


@check
def frozen_environment_and_sources():
    env = R.assert_environment()
    return {"python": env["python"], "numpy": env["numpy"], "openblas_threads": env["openblas_threads"],
            "source_hashes": len(env["source_hashes"])}


@check
def family_design_and_membership_table():
    V.validate_fixed_design()
    table = load("membership_table.json")
    assert len(table["rows"]) == 384
    for row, hyp in zip(table["rows"], V.HYPOTHESIS_TABLE):
        assert (row["hypothesis"], row["kind"], row["label"], float(row["threshold"]), row["cluster"], row["sector"]) == \
               (hyp.index, hyp.kind, hyp.label, hyp.threshold, hyp.cluster, hyp.sector)
    counts = Counter((r["kind"], r["label"]) for r in table["rows"])
    assert counts == {("state", "case_kind"): 128, ("state", "persistence"): 128,
                      ("transition", "continuation"): 64, ("sequence", "continuation"): 64}
    return {"rows": 384, "clusters": 32, "cluster_size": 12}


@check
def config_matches_runner_and_design():
    c = load("config.json")
    assert c["master_seed"] == R.MASTER_SEED == V.MASTER_SEED
    assert c["seed_map"]["phase_code"] == {"null": 10, "power": 20, "checks": 30, "benchmark": 90} == R.PHASE
    assert c["seed_map"]["stream_code"] == R.STREAM
    assert c["sampling"]["permutations_per_world"] == R.B == V.FULL_PERMUTATIONS == 767_999
    assert (c["design"]["blocks"], c["design"]["symbols"], c["design"]["keys"], c["design"]["rows_per_world"]) == \
           (R.BLOCKS, V.SYMBOLS, V.KEYS, R.ROWS)
    assert c["family"]["thresholds"] == {"state": repr(V.STATE_THRESHOLD), "linked": repr(V.LINKED_THRESHOLD)}
    assert c["environment"] == {"python": "3.12.3", "numpy": "2.5.2", "OPENBLAS_NUM_THREADS": "1"}
    assert c["cells"]["worlds_total"] == 75_000 == V.FULL_WORLDS
    plan = R.cell_plan()
    assert len(plan) == 55 and sum(p["worlds"] for p in plan) == 75_000
    assert Counter(p["kind"] for p in plan) == {"null": 20, "power": 25, "perturbed_null": 10}
    dgp = c["dgp"]
    assert tuple(R.RATE_BY_CLASS) == tuple(float(Fraction(x)) for x in dgp["N2"]["coverage"]["rates"])
    assert R.N2_MEMBERSHIP == tuple(float(Fraction(x)) for x in dgp["N2"]["membership_shift_by_realized_class"])
    assert R.N2_PERSISTENCE == tuple(float(Fraction(x)) for x in dgp["N2"]["persistence_shift_null_mad_units_by_realized_class"])
    assert R.N3_MEMBERSHIP_Q == tuple(float(Fraction(x)) for x in dgp["N3"]["membership_shift_by_quarter"])
    assert R.N3_PERSISTENCE_Q == tuple(float(Fraction(x)) for x in dgp["N3"]["persistence_shift_null_mad_units_by_quarter"])
    assert (R.N3_MEMBERSHIP_REGIME1, R.N3_PERSISTENCE_REGIME1) == (1.2, 1.5)
    assert R.SUPPORT == {k: v for k, v in c["refusal"]["hypothesis_level_floors"].items()}
    assert c["pinned_sources_sha256"] == {k: v for k, v in c["pinned_sources_sha256"].items()}
    return {"cells": 55, "worlds": 75_000, "distinct_seed_tuples": c["seed_map"]["distinct_tuples"]["total"]}


@check
def power_rules_and_fdr_scenarios_exclude_p1_p3():
    """Protocol correction section 2-3: P1 (101) and P3 (103) lose the power/mixed-FDR gates."""
    assert set(A.POWER_RULES) == {102, 104, 105, 106}
    assert set(A.FDR_SCENARIOS) == {102, 104, 105, 106}
    assert 101 not in A.POWER_RULES and 103 not in A.POWER_RULES
    assert 101 not in A.FDR_SCENARIOS and 103 not in A.FDR_SCENARIOS
    assert tuple(A.PERTURBED_NULL_SCENARIOS) == (101, 103)
    fdr_cfg = load("config.json")["acceptance"]["fdr"]
    assert sorted(fdr_cfg["scenarios"]) == [102, 104, 105, 106]
    power_cfg = load("config.json")["acceptance"]["power"]
    assert "P1_P3" not in power_cfg
    # P2's original single-target power criterion (Wilson lower >= 0.80) is preserved, just no longer bundled
    # under the same key as P1/P3.
    assert power_cfg["P2"] == {"wilson_lower_min": 0.80}
    assert A.POWER_RULES[102] == ((1, 0.80),)
    return {"power_rules": sorted(A.POWER_RULES), "fdr_scenarios": sorted(A.FDR_SCENARIOS)}


@check
def p1_p3_role_and_cell_kind_and_truth():
    """Effect-assignment role, cell_plan kind and the (frozen, unchanged) truth tables agree: P1/P3 are true-null."""
    t = load("effect_assignment_table.json")
    roles = {s["scenario_code"]: s["role"] for s in t["scenarios"]}
    assert roles[101] == roles[103] == "calibration"
    assert roles[102] == roles[104] == roles[105] == roles[106] == "acceptance"
    assert roles[107] == "sensitivity_only"
    plan = R.cell_plan()
    kinds = {p["scenario"]: p["kind"] for p in plan if p["scenario"] is not None}
    assert kinds[101] == kinds[103] == "perturbed_null"
    for s in (102, 104, 105, 106, 107):
        assert kinds[s] == "power"
    for scenario in (101, 103):
        for corr in range(5):
            recs = R.load_truth(scenario, None, corr)
            assert len(recs) == 384 and all(r["classification"] == "true_null" for r in recs), (scenario, corr)
    return {"p1_p3_role": "calibration", "p1_p3_kind": "perturbed_null", "p1_p3_truth_rows_checked": 384 * 2 * 5}


@check
def effect_assignment_table_matches_pinned_sets():
    t = load("effect_assignment_table.json")
    assert {s["scenario_code"]: tuple(s["targets"]) for s in t["scenarios"]} == \
           {k: tuple(sorted(v)) for k, v in V.INJECTIONS.items()}
    for s in t["scenarios"]:
        code = s["scenario_code"]
        for a in s["assignments"]:
            if a["target"] == "persistence":
                assert float(Fraction(a["magnitude_null_mad_units"])) == V.PERSISTENCE_SHIFT_BY_SCENARIO[code]
            else:
                assert Fraction(a["odds_ratio"]) == V.OR_BY_SCENARIO[code]
    sizes = {s["scenario"]: s["size"] for s in t["scenarios"]}
    assert sizes == {"P1": 1, "P2": 1, "P3": 1, "P4": 8, "P5": 32, "P6": 32, "P7": 96}
    return {"scenarios": 7, "sizes": sizes}


@check
def null_mad_derivation():
    derived = V.derive_n1_null_mad()
    approx(derived, V.N1_NULL_MAD, 1e-13)
    from statistics import NormalDist
    approx(NormalDist().inv_cdf(0.75), 0.6744897501960817, 1e-15)
    assert load("config.json")["null_mad"]["t3_common_jump"] == repr(V.N1_NULL_MAD)
    return {"t3_common_jump": V.N1_NULL_MAD, "derived": round(derived, 15), "gaussian": 0.6744897501960817}


@check
def loadings_and_covariance_psd():
    psd, load_ = load("covariance_psd.json"), load("loadings.json")
    out = {}
    for c in range(5):
        rec = psd["codes"][f"C{c}"]
        assert rec["exact_factor_form_reproduces_all_384x384_entries"] is True
        assert Fraction(rec["exact_minimum_eigenvalue"]) == Fraction(rec["residual_variance"]) > 0
        assert Fraction(rec["unit_variance_identity"]) == 1 and rec["numeric_cholesky_succeeded"] is True
        approx(float(rec["numeric_minimum_eigenvalue"]), float(Fraction(rec["exact_minimum_eigenvalue"])), 1e-9)
        assert load_["codes"][f"C{c}"]["residual_variance"].split("/")[0] != "0"
        out[f"C{c}"] = rec["exact_minimum_eigenvalue"]
    # the implemented latent constructions realise these matrices: empirical-free check via implementation loadings
    root3 = math.sqrt(3.0) / 2.0
    L = np.array([[math.sqrt(0.6) * 0.5, math.sqrt(0.6) * (root3 if h % 32 < 16 else -root3)] for h in range(384)])
    cov = L @ L.T + 0.4 * np.eye(384)
    assert abs(cov[0, 1] - 0.6) < 1e-15 and abs(cov[0, 16] + 0.3) < 1e-15 and abs(cov[0, 0] - 1.0) < 1e-15
    return {"exact_min_eigenvalue": out}


@check
def metric_equivalence_fixtures():
    # known answers derived by hand: persistence (7-3)/(1+1) = 2 ; categorical lift = 17/45
    rows = [{"row_id": f"r{i}", "features": {"direction": 1}, "labels": {"price_change_bps": v}}
            for i, v in enumerate((1, 2, 3, 4, 10))]
    m = R._metric(rows, {"r3", "r4"}, "persistence", {})
    assert m["signed_effect"] == 2.0
    rows = [{"row_id": f"c{i}", "features": {}, "labels": {"case_kind": v}} for i, v in enumerate((0, 0, 0, 1, 1, 2))]
    m = R._metric(rows, {"c3", "c4", "c5"}, "case_kind", {})
    approx(m["signed_effect"], 17 / 45, 1e-15)
    # degenerate constant outcome: exactly zero lift
    rows = [{"row_id": f"d{i}", "features": {}, "labels": {"case_kind": 1}} for i in range(6)]
    assert R._metric(rows, {"d0", "d1"}, "case_kind", {})["signed_effect"] == 0.0
    # runner statistic path is bit-identical to the pinned harness on a fixture, unpermuted and permuted
    world = fixture_world()
    ref = V.actual_statistic_vector(world)
    assert np.array_equal(R.actual_statistics(world, None, range(384)), ref)
    block_map = R.draw_block_map(DeterministicStub(), R.strata_all(world.block_strata))
    assert np.array_equal(R.actual_statistics(world, block_map, range(384)), V.actual_statistic_vector(world, block_map))
    assert R._metric is V._metric
    return {"hypotheses_compared": 384, "permuted": True, "bit_identical": True}


@check
def bh_exact_with_refusals_in_denominator():
    B = R.B
    assert R.exact_bh([99] + [None] * 383) == {0}            # p = 1/7680 exactly at the k=1 threshold
    assert R.exact_bh([100] + [None] * 383) == set()
    assert R.exact_bh([None] * 384) == set()
    assert R.exact_bh([199, 199] + [None] * 382) == {0, 1}      # both at k=2: p = 200/768000 <= 2/7680
    assert R.exact_bh([199, 200] + [None] * 382) == set()
    ex = [10, 20, 30_000, 400_000] + [700_000] * 380
    pf = [(1 + x) / (B + 1) for x in ex]
    assert R.exact_bh(ex) == V.bh_rejections(pf)
    refused_heavy = [5] + [None] * 383
    assert R.exact_bh(refused_heavy) == {0}                    # m stays 384, so rank-1 threshold is 1/7680
    return {"boundary_p": "1/7680", "denominator": 384}


@check
def p_value_and_tie_rule():
    assert V.monte_carlo_p(0, R.B) == Fraction(1, 768_000)
    assert V.monte_carlo_p(R.B, R.B) == 1
    obs = np.full(384, 0.5)
    at = np.full(384, 0.5 - 0.9e-12)
    below = np.full(384, 0.5 - 1.1e-12)
    counts, n = V.exceedance_counts(obs, [at, below, -at, np.full(384, 0.5 + 1e-3)])
    assert n == 4 and int(counts[0]) == 3          # at-tolerance and both signs count, below does not
    try:
        V.exceedance_counts(obs, [np.full(384, np.nan)])
        raise AssertionError("nonfinite draw accepted")
    except V.IntegrityError:
        pass
    return {"tolerance": 1e-12, "floor": "1/768000"}


@check
def with_replacement_sampling_includes_identity():
    world = fixture_world()
    groups = R.strata_all(world.block_strata)
    identity = R.draw_block_map(DeterministicStub("identity"), groups)
    assert np.array_equal(identity, np.arange(R.BLOCKS))               # identity is a legal draw, not excluded
    first = R.draw_block_map(DeterministicStub("roll"), groups)
    second = R.draw_block_map(DeterministicStub("roll"), groups)
    assert np.array_equal(first, second) and not np.array_equal(first, identity)   # no memory / no dedup
    assert all(len(g) > 1 for g in groups if len(g) > 1) and len(groups) == 8
    singleton = R.draw_block_map(DeterministicStub("roll"), (np.array([5]),))
    assert np.array_equal(singleton, np.arange(R.BLOCKS))               # only movable strata consume a permutation
    return {"strata": 8, "identity_allowed": True}


@check
def orbit_floor_and_strata():
    strata = tuple((1 + b % 4, int((b // 3) % 2), 0) for b in range(48))
    sizes = Counter(strata)
    assert sorted(sizes.values()) == [4, 4, 4, 4, 8, 8, 8, 8]
    assert {k[:2]: v for k, v in sizes.items()} == {(1, 0): 8, (1, 1): 4, (2, 0): 4, (2, 1): 8,
                                                     (3, 0): 8, (3, 1): 4, (4, 0): 4, (4, 1): 8}
    orbit = V.orbit_size(strata)
    assert orbit == math.factorial(8) ** 4 * math.factorial(4) ** 4 and orbit > 1_536_000
    assert Fraction(2, orbit) <= Fraction(1, 768_000)
    assert V.orbit_size(strata, [b"same"] * 48) == 1                    # identical tensors collapse the orbit
    return {"n0_orbit": str(orbit), "approx": f"{orbit:.3e}"}


@check
def invalid_case_refusals_all_100_percent():
    base = V.valid_metadata(tuple((1 + b % 4, int((b // 3) % 2), 0) for b in range(48)))
    cases = {
        "between_block_dependence": {"between_block_independent": False},
        "block_footprint_crossing": {"footprints_within_block": False},
        "link_crossing": {"links_valid": False},                        # linked copy of key 4 in another block
        "unconditioned_drift": {"conditioned_mean_variance": False},
        "outcome_dependent_missingness": {"missingness_outcome_independent": False},
        "omitted_confounder": {"confounders_in_D": False},
        "clock_violation": {"clock_valid": False},
        "invalid_exchangeability_certificate": {"certificate": None},
        "source_hash_mismatch": {"source_hashes_match": False},
        "config_mismatch": {"config_match": False},
        "family_size_not_384": {"family_size": 383},
        "invalid_strata": {"block_strata": None},
        "movable_blocks_below_24": {"block_strata": tuple((b, 0, 0) for b in range(48))},
        "movable_strata_below_3": {"block_strata": tuple((1 + b % 2, 0, 0) for b in range(48))},
        "permutation_orbit_below_floor": {"block_strata": tuple((b // 2, 0, 0) if b < 24 else (100 + b, 0, 0)
                                                                for b in range(48))},
    }
    refused = 0
    for reason, patch in cases.items():
        try:
            V.validate_world_metadata({**base, **patch})
        except V.WorldRefused as exc:
            assert reason in exc.reasons, (reason, exc.reasons)
            refused += 1
    assert refused == len(cases)
    assert V.validate_world_metadata(base) > 1_536_000
    return {"cases": len(cases), "refused": refused}


@check
def hypothesis_level_refusals():
    w = fixture_world()
    assert all(r is None for r in R.hypothesis_refusals(w))            # baseline fully testable
    # block below 8 observed unique labels -> every hypothesis
    obs = w.observed.copy(); obs[:80 - 7] = False
    bad = V.SyntheticWorld(0, 1, 0, w.memberships, w.categorical, w.persistence, obs, w.block_strata, w.metadata)
    assert set(R.hypothesis_refusals(bad)) == {"block_below_8_observed_labels"}
    # matched floors: state needs 12 matched, transition needs 8
    mem = w.memberships.copy(); mem[:, 5] = False; mem[:11, 5] = True
    mem[:, 300] = False; mem[:7, 300] = True; mem[:, 301] = False; mem[:8, 301] = True
    low = V.SyntheticWorld(0, 1, 0, mem, w.categorical, w.persistence, w.observed, w.block_strata, w.metadata)
    r = R.hypothesis_refusals(low)
    assert r[5] == "matched_below_floor" and r[300] == "matched_below_floor" and r[301] == "fewer_than_3_dependence_groups"
    # dependence groups: 14 matched rows inside two blocks only
    mem = w.memberships.copy(); mem[:, 6] = False; mem[:7, 6] = True; mem[80:87, 6] = True
    two = V.SyntheticWorld(0, 1, 0, mem, w.categorical, w.persistence, w.observed, w.block_strata, w.metadata)
    assert R.hypothesis_refusals(two)[6] == "fewer_than_3_dependence_groups"
    # worst case: every matched slot is masked in another block of its stratum
    obs = w.observed.copy(); mem = w.memberships.copy(); mem[:, 7] = False
    slots = [b * 80 + 3 for b in range(0, 12)]
    mem[slots, 7] = True
    for b in range(1, 48):                                           # block 0 keeps its rows; its stratum-mates mask slot 3
        obs[b * 80 + 3] = False
    masked = V.SyntheticWorld(0, 1, 0, mem, w.categorical, w.persistence, obs, w.block_strata, w.metadata)
    assert R.hypothesis_refusals(masked)[7] in ("observed_below_floor", "worst_case_observed_below_floor")
    # constant outcome column
    const = V.SyntheticWorld(0, 1, 0, w.memberships, np.zeros(R.ROWS, dtype=np.int64), w.persistence, w.observed,
                             w.block_strata, w.metadata)
    rc = R.hypothesis_refusals(const)
    assert rc[0] == "degenerate_constant_outcome" and rc[130] is None and rc[300] == "degenerate_constant_outcome"
    return {"reasons_exercised": 7}


@check
def seed_map_and_registry_gates():
    c = load("config.json")["seed_map"]["distinct_tuples"]
    assert c["null"] == 120_000 and c["power"] == 140_000 and c["checks_bootstrap"] == 20 and c["total"] == 260_020
    plan = R.cell_plan()
    null_tuples = sum(p["worlds"] * 3 for p in plan if p["kind"] == "null")
    power_tuples = sum(p["worlds"] * 4 for p in plan if p["kind"] in ("power", "perturbed_null"))
    assert (null_tuples, power_tuples) == (c["null"], c["power"])
    bootstrap_tuples = len(A.FDR_SCENARIOS) * 5
    assert bootstrap_tuples == c["checks_bootstrap"] == 20
    # tuple = (master, phase, corr, dgp, world, stream): every coordinate is recoverable, so the space is injective.
    # phase sets are disjoint (10/20/30/90) and dgp codes are disjoint between phases 10 (0-3) and 20/30 (101-107).
    try:
        R.SeedRegistry(None)
        raise AssertionError("registry built without authorization")
    except R.IntegrityError as exc:
        assert str(exc) == "authorization_required"
    try:
        R.Authorization(object(), "benchmark", "x")
        raise AssertionError("authorization forged")
    except R.IntegrityError as exc:
        assert str(exc) == "authorization_forged"
    bench = R.SeedRegistry(R.Authorization(R.Authorization._token, "benchmark", "test"))
    for phase in (10, 20, 30):
        try:
            bench.generator(phase, 0, 0, 0, 0)
            raise AssertionError("phase accepted")
        except R.IntegrityError as exc:
            assert str(exc) == "phase_not_authorized"
    try:
        bench.generator(90, 0, 0, 0, 4)
        raise AssertionError("bootstrap outside phase 30")
    except R.IntegrityError as exc:
        assert str(exc) == "stream_not_permitted"
    bench.seen.add((R.MASTER_SEED, 90, 0, 1, 0, 0))
    try:
        bench.generator(90, 0, 1, 0, 0)
        raise AssertionError("duplicate tuple accepted")
    except R.IntegrityError as exc:
        assert str(exc) == "duplicate_seed_tuple"
    val = R.SeedRegistry(R.Authorization(R.Authorization._token, "validation", "test"))
    try:
        val.generator(90, 0, 0, 0, 0)
        raise AssertionError("benchmark phase in validation")
    except R.IntegrityError as exc:
        assert str(exc) == "phase_not_authorized"
    return {"distinct_tuples": c["total"], "gates": "authorization/phase/stream/duplicate"}


@check
def authorization_records_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "auth.json"
        for content, why in ((None, "authorization_missing"), ("{}", "authorization_mismatch"),
                             (json.dumps({"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "benchmark",
                                          "bundle_sha256": "0" * 64, "authorized_by": "owner"}), "authorization_mismatch")):
            if content is not None:
                path.write_text(content)
            try:
                R.authorize(path, "benchmark")
                raise AssertionError("authorization accepted")
            except R.IntegrityError as exc:
                assert str(exc) in (why, "bundle_manifest_missing"), str(exc)
        try:
            R.authorize(path, "preflight")
            raise AssertionError("preflight has no rng")
        except R.IntegrityError as exc:
            assert str(exc) == "mode_has_no_rng"
    return {"missing": True, "wrong_hash": True, "wrong_mode": True}


@check
def exclusive_create_behaviour():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sub" / "out.json"
        V.exclusive_json(p, "{}")
        try:
            V.exclusive_json(p, "{}")
            raise AssertionError("overwrite allowed")
        except V.IntegrityError as exc:
            assert str(exc) == "output_path_collision"
        assert p.read_text() == "{}"
    return {"collision_refused": True}


@check
def import_isolation_and_network_block():
    code = ("import os,sys; os.environ['OPENBLAS_NUM_THREADS']='1'; sys.path.insert(0, %r);"
            "import scheme_d_runner as R; R.assert_isolation();"
            "bad=[m for m in R.DENYLIST_MODULES if m in sys.modules or any(k.startswith(m+'.') for k in sys.modules)];"
            "print(len(bad), 'sqlite3' in sys.modules, len([m for m in sys.modules if m.startswith('trader.')]))") % str(HERE)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
    assert proc.returncode == 0, proc.stderr[-400:]
    bad, sqlite, trader_mods = proc.stdout.split()
    assert bad == "0" and sqlite == "False"
    import socket
    with V.blocked_network():
        try:
            socket.socket()
            raise AssertionError("socket created")
        except V.IntegrityError as exc:
            assert str(exc) == "network_access_refused"
    saved = sys.modules.pop("sqlite3", None)
    sys.modules["sqlite3"] = type(sys)("sqlite3")
    try:
        R.assert_isolation()
        raise AssertionError("isolation did not trip")
    except R.IntegrityError as exc:
        assert "sqlite3" in str(exc)
    finally:
        sys.modules.pop("sqlite3", None)
        if saved is not None:
            sys.modules["sqlite3"] = saved
    return {"denied_modules_loaded": 0, "trader_modules_loaded": int(trader_mods), "socket_creation_blocked": True}


@check
def static_scan_rng_and_imports():
    allowed_rng = {"generator"}
    for name in ("scheme_d_runner.py", "acceptance.py"):
        tree = ast.parse((HERE / name).read_text())
        found = []
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr in ("PCG64", "SeedSequence", "default_rng",
                                                                        "RandomState", "Generator"):
                        found.append((fn.name, node.attr))
        assert name != "acceptance.py" or not found, found
        assert all(f[0] in allowed_rng for f in found if name == "scheme_d_runner.py"), found
        for node in tree.body:                       # no module-level RNG use
            assert not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                        and "random" in ast.dump(node).lower()), name
    imports = set()
    for name in ("scheme_d_runner.py", "acceptance.py"):
        for node in ast.walk(ast.parse((HERE / name).read_text())):
            if isinstance(node, ast.Import):
                imports |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    allowed = {"argparse", "hashlib", "inspect", "json", "os", "sys", "time", "collections", "fractions", "pathlib", "math",
               "numpy", "trader", "acceptance", "preflight", "__future__"}
    assert imports <= allowed, imports - allowed
    assert "sqlite3" not in imports and "socket" not in imports and "requests" not in imports
    return {"runner_imports": sorted(imports)}


@check
def acceptance_evaluators():
    F = Fraction
    approx(A.wilson(50, 100)[0], 0.40383152963549296, 1e-12)
    approx(A.wilson(100, 2000)[1], 0.06044410902402285, 1e-12)
    assert A.type_one_pass(100, 2000) is False and A.type_one_pass(90, 2000) is True and A.type_one_pass(0, 2000) is True
    assert [A.cp_band_contains(k, 2000, F(5, 100)) for k in (40, 70, 100, 130, 140)] == [False, True, True, True, False]
    assert [A.cp_band_contains(k, 2000, F(1, 100)) for k in (5, 10, 20, 35, 40)] == [False, True, True, True, False]
    assert A.power_pass(102, [1] * 1000) and not A.power_pass(102, [1] * 800 + [0] * 200)
    assert A.power_pass(105, [32] * 1000) and not A.power_pass(105, [20] * 900 + [0] * 100)
    assert A.power_pass(104, [8] * 1000) and not A.power_pass(104, [1] * 1000)
    # P1/P3 excluded from POWER_RULES by the pre-RNG correction: power_pass is always False, regardless of input
    assert A.power_pass(101, [1] * 1000) is False and A.power_pass(103, [1] * 1000) is False
    assert A.refusal_blockers(2000, 20, 0)["blocks"] is False and A.refusal_blockers(2000, 21, 0)["blocks"] is True
    assert A.refusal_blockers(1000, 0, 3840)["blocks"] is False      # exactly 1% does not exceed 1%
    assert A.refusal_blockers(1000, 0, 3841)["blocks"] is True
    fdps = [0.0] * 1000
    assert A.bootstrap_upper(fdps, lambda lo, hi, size: np.tile(np.arange(hi), (size[0], 1))) == 0.0
    assert A.fdr_pass([0.05] * 10, 0.06) and not A.fdr_pass([0.051] * 10, 0.06) and not A.fdr_pass([0.0] * 10, 0.0601)
    grid = [F(2 * w + 1, 4000) for w in range(2000)]
    assert all(A.calibration_pass(grid).values())
    quiet = [{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
              "attribution_errors": 0, "marginal_p": grid[w]} for w in range(2000)]
    cell = A.evaluate_cell("null", None, quiet)
    assert cell["passes"] and cell["type_one_rate"] == 0.0
    noisy = [{**q, "R": 1} if w < 200 else q for w, q in enumerate(quiet)]
    assert not A.evaluate_cell("null", None, noisy)["passes"]
    p7 = A.evaluate_cell("power", 107, [{**quiet[0], "true_discoveries": 0} for _ in range(10)])
    assert p7["passes"] and "power" not in p7["gates"]
    overall = A.overall({"N0/C0": cell, "P7/C0": p7})
    assert overall["passes"] and A.overall({"N0/C0": {**cell, "passes": False}})["failing_cells"] == ["N0/C0"]

    # --- perturbed_null (P1/P3): true-null rejections are false positives; FDP indicator = R > 0.
    calm = [{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
             "attribution_errors": 0, "marginal_p": None} for _ in range(1000)]
    p1_pass = A.evaluate_cell("perturbed_null", 101, calm)
    assert p1_pass["passes"] and p1_pass["gates"] == {"refusal_blocker_absent": True, "type_one": True}
    assert p1_pass["fdp_count"] == 0 and p1_pass["mean_fdp"] == 0.0 and p1_pass["fdp_rate"] == 0.0
    assert "power" not in p1_pass["gates"] and "fdr" not in p1_pass["gates"] and "marginal_calibration" not in p1_pass["gates"]
    noisy_calm = [{**c, "R": 1, "V": 1} if i < 40 else c for i, c in enumerate(calm)]      # 4.0% rejecting: point <= .05
    p1_borderline = A.evaluate_cell("perturbed_null", 101, noisy_calm)
    assert p1_borderline["fdp_count"] == 40 and abs(p1_borderline["mean_fdp"] - 0.04) < 1e-12
    assert p1_borderline["passes"] == A.type_one_pass(40, 1000)
    excessive = [{**c, "R": 1, "V": 1} if i < 120 else c for i, c in enumerate(calm)]       # 12%: clearly fails
    p1_fail = A.evaluate_cell("perturbed_null", 103, excessive)
    assert not p1_fail["passes"] and p1_fail["gates"]["type_one"] is False and p1_fail["mean_fdp"] == 0.12
    refused_mix = calm[:900] + [{"refused": True, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
                                 "attribution_errors": 0, "marginal_p": None}] * 100
    p1_refused = A.evaluate_cell("perturbed_null", 101, refused_mix)
    assert p1_refused["non_refused"] == 900 and p1_refused["refusal"]["blocks"] is True  # 10% refused > 1% blocker
    assert not p1_refused["passes"]                                                       # blocked regardless of type_one
    return {"wilson": "verified", "clopper_pearson": "exact", "p7": "descriptive", "p1_p3": "type_one_only"}


@check
def world_generation_and_injection_logic_with_stub():
    shapes = {}
    for dgp in range(4):
        for corr in range(5):
            w = R.generate_world(dgp, corr, 0, DeterministicStub(uniform=None), DeterministicStub())
            assert w.memberships.shape == (R.ROWS, 384) and w.memberships.dtype == np.bool_
            assert w.categorical.shape == w.persistence.shape == w.observed.shape == (R.ROWS,)
            assert len(w.block_strata) == 48 and w.metadata["family_size"] == 384
            assert set(np.unique(w.categorical)) <= {0, 1, 2} and np.all(np.isfinite(w.persistence))
            shapes[f"N{dgp}/C{corr}"] = True
    w3 = R.generate_world(3, 0, 0, DeterministicStub(uniform=None), DeterministicStub())
    grid = w3.observed.reshape(48, 16, 5)
    lags = np.minimum(np.arange(48 * 16) % 3, 5).reshape(48, 16)           # stub geometric: G-1 in {0,1,2}
    expected = np.ones((48, 16, 5), dtype=bool)
    for b in range(48):
        for s in range(16):
            if lags[b, s]:
                expected[b, s, 5 - lags[b, s]:] = False
    assert np.array_equal(grid, expected)
    w2 = R.generate_world(2, 0, 0, DeterministicStub(uniform=None), DeterministicStub())
    classes = np.asarray([R.missing_class(int((~w2.observed.reshape(48, 16, 5)[b]).sum()), 80) for b in range(48)])
    assert [s[2] for s in w2.block_strata] == classes.tolist()
    # injection: persistence shift deterministic, categorical switch uses the injection stream
    base = fixture_world(only={0, 130, 260, 330})
    inj = R.apply_injection(base, 104, DeterministicStub(uniform=0.0))   # uniform 0 < switch probability: all switch
    assert np.all(inj.categorical[base.memberships[:, 0]] == 2)
    m = base.memberships[:, 130]
    assert np.allclose(inj.persistence[m] - base.persistence[m], 0.5 * V.N1_NULL_MAD)
    none = R.apply_injection(base, 104, DeterministicStub(uniform=0.999))
    assert np.array_equal(none.categorical, base.categorical)
    assert np.array_equal(inj.memberships, base.memberships) and inj.block_strata == base.block_strata
    # P1 (scenario 101, target h=0) and P3 (scenario 103, target h=320): dispatch is keyed by scenario, not by
    # cell "kind" -- injection still occurs for the perturbed_null cells exactly as for any power cell.
    p1_base = fixture_world(only={0})
    p1_inj = R.apply_injection(p1_base, 101, DeterministicStub(uniform=0.0))
    assert np.all(p1_inj.categorical[p1_base.memberships[:, 0]] == 2)
    p1_none = R.apply_injection(p1_base, 101, DeterministicStub(uniform=0.999))
    assert np.array_equal(p1_none.categorical, p1_base.categorical)
    p3_base = fixture_world(only={320})
    p3_inj = R.apply_injection(p3_base, 103, DeterministicStub(uniform=0.0))
    assert np.all(p3_inj.categorical[p3_base.memberships[:, 320]] == 2)
    return {"cells_generated_with_stub": len(shapes), "p1_p3_injection_dispatch": "by scenario"}


@check
def receipt_pipeline_with_stub():
    only = {0, 130, 260, 330}
    world = fixture_world(only=only)
    truth = [{"classification": "true_null", "sign": None}] * 384
    receipt = R.world_receipt(world, DeterministicStub(), 90, "STUB", {}, draws=2, truth=truth, injected=())
    assert set(receipt["refusals"]) == {str(h) for h in range(384) if h not in only}
    assert receipt["draws"] == 2 and receipt["p"][0].endswith("/3") and receipt["p"][1] is None
    assert receipt["exceedances"][1] is None and len(receipt["signed_hex"]) == 384
    assert receipt["sha256"] == hashlib.sha256(json.dumps({k: v for k, v in receipt.items() if k != "sha256"},
                                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    refused = R.world_receipt(V.SyntheticWorld(0, 1, 0, world.memberships, world.categorical, world.persistence,
                                               world.observed, world.block_strata, {**world.metadata, "links_valid": False}),
                              DeterministicStub(), 90, "STUB", {}, draws=2, truth=truth, injected=())
    assert refused["world_refused"] == ["link_crossing"] and R.world_summary(refused)["refused"] is True
    truth_v = [{"classification": "analytic_non_null", "sign": 1}] + [{"classification": "true_null", "sign": None}] * 383
    c = R.classify_rejections({0, 5}, np.array([0.3] + [0.0] * 383), truth_v, (0,))
    assert c == {"R": 2, "V": 1, "true_discoveries": 1, "attribution_errors": 0}
    c = R.classify_rejections({0}, np.array([-0.3] + [0.0] * 383), truth_v, (0,))
    assert c["V"] == 1 and c["true_discoveries"] == 0
    # P1/P3-style all-true-null truth: every rejection is a false discovery (V == R), matching acceptance's FDP=1{R>0}
    truth_null_only = [{"classification": "true_null", "sign": None}] * 384
    c = R.classify_rejections({0, 5, 130}, np.array([0.3, -0.2] + [0.0] * 382), truth_null_only, (0,))
    assert c == {"R": 3, "V": 3, "true_discoveries": 0, "attribution_errors": 0}
    return {"receipt_hashing": True, "refused_world": "link_crossing", "p1_p3_all_rejections_false": True}


@check
def class_edges_half_open_integer():
    """Protocol section 5 edges [0,.2), [.2,.4), [.4,.6), [.6,1] with no binary-float boundary ambiguity."""
    edges = (Fraction(1, 5), Fraction(2, 5), Fraction(3, 5))
    for masked in range(81):
        expected = sum(Fraction(masked, 80) >= e for e in edges)
        assert R.missing_class(masked, 80) == expected, masked
    assert [R.missing_class(m, 80) for m in (0, 15, 16, 31, 32, 47, 48, 80)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert 1.0 - 64 / 80 < 0.2                                  # the superseded float rule put 16/80 in class 0
    assert "searchsorted" not in (HERE / "scheme_d_runner.py").read_text()
    return {"masked_counts_checked": 81, "exact_edges": {"16": 1, "32": 2, "48": 3}}


@check
def world_generation_class_edges_at_exact_boundaries():
    got = {}
    for g in (1, 2, 3, 4, 5):                                    # constant lag L = g-1 -> masked fraction (g-1)/5
        w = R.generate_world(3, 0, 0, DeterministicStub(uniform=None, geometric=g), DeterministicStub())
        got[g] = sorted({s[2] for s in w.block_strata})
    assert got == {1: [0], 2: [1], 3: [2], 4: [3], 5: [3]}, got
    return {"lag_to_class": {str(k): v[0] for k, v in got.items()}}


@check
def zero_tested_world_is_refused():
    truth = [{"classification": "true_null", "sign": None}] * 384
    base = fixture_world()
    obs = base.observed.copy(); obs[:80 - 7] = False           # a block below 8 observed rows -> all 384 refused
    low = V.SyntheticWorld(0, 1, 0, base.memberships, base.categorical, base.persistence, obs, base.block_strata, base.metadata)
    r = R.world_receipt(low, DeterministicStub(), 90, "STUB", {}, draws=2, truth=truth, injected=())
    assert r["world_refused"] == ["no_tested_hypotheses"] and len(r["refusals"]) == 384
    s = R.world_summary(r)
    assert s["refused"] is True and s["R"] == 0 and s["marginal_p"] is None
    empty = V.SyntheticWorld(0, 1, 0, np.zeros_like(base.memberships), base.categorical, base.persistence,
                             base.observed, base.block_strata, base.metadata)
    r2 = R.world_receipt(empty, DeterministicStub(), 90, "STUB", {}, draws=2, truth=truth, injected=())
    assert r2["world_refused"] == ["no_tested_hypotheses"]
    cell = A.evaluate_cell("null", None, [s] * 30 + [{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0,
                                                       "true_discoveries": 0, "attribution_errors": 0,
                                                       "marginal_p": Fraction(1, 2)}] * 70)
    assert cell["non_refused"] == 70 and cell["refusal"]["blocks"] is True      # counted in the world-refusal blocker
    power = A.power_pass(102, [0] * 30 + [1] * 970)                              # refused world = failure, stays in denominator
    assert power is True and A.power_pass(102, [0] * 250 + [1] * 750) is False
    assert A.power_pass(101, [0] * 30 + [1] * 970) is False        # P1 excluded: always False, not a "failure" of a real gate
    return {"refused_world": "no_tested_hypotheses", "excluded_from_type_one_fdr": True, "power_failure": True}


@check
def zero_observed_lift_is_tested_not_refused():
    """Owner clarification I4: an observed zero lift is valid evidence and is never refused."""
    world = fixture_world(only={0, 130, 260, 330})
    rows = np.arange(R.ROWS)
    cat = np.zeros(R.ROWS, dtype=np.int64)
    cat[(rows * 7 + 3) % 23 == 0] = 1
    cat[(rows * 11 + 5) % 29 == 0] = 2
    zero = V.SyntheticWorld(0, 1, 0, world.memberships, cat, world.persistence, world.observed, world.block_strata,
                            world.metadata)
    stat = R.actual_statistics(zero, None, [0, 260])
    assert stat[0] == 0.0 and stat[260] == 0.0                   # majorities coincide: lift is exactly zero
    reasons = R.hypothesis_refusals(zero)
    assert reasons[0] is None and reasons[260] is None
    truth = [{"classification": "true_null", "sign": None}] * 384
    r = R.world_receipt(zero, DeterministicStub(), 90, "STUB", {}, draws=2, truth=truth, injected=())
    assert "0" not in r["refusals"] and "260" not in r["refusals"]
    assert r["signed_hex"][0] == float(0.0).hex() and r["p"][0] is not None and r["exceedances"][0] == 2
    const = V.SyntheticWorld(0, 1, 0, world.memberships, np.zeros(R.ROWS, dtype=np.int64), world.persistence,
                             world.observed, world.block_strata, world.metadata)
    assert R.hypothesis_refusals(const)[0] == "degenerate_constant_outcome"      # only a constant column is degenerate
    return {"zero_lift_tested": True, "p": r["p"][0], "constant_outcome_refused": True}


@check
def calibration_excludes_refused_marginal_worlds():
    """Owner decision I6: worlds whose h(w) is refused leave the ECDF denominator."""
    F = Fraction
    grid = [F(2 * w + 1, 2000) for w in range(1000)]                         # perfectly calibrated on n = 1000
    with_refused = grid + [None] * 1000
    assert all(A.calibration_pass(grid).values())
    assert A.calibration_pass(with_refused) == A.calibration_pass(grid)     # None entries change nothing
    assert not all(A.cp_band_contains(sum(1 for p in grid if p <= t), 2000, t) for t in A.MARGINAL_T)   # counting them as > t would fail
    quiet = [{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
              "attribution_errors": 0, "marginal_p": p} for p in grid]
    cell = A.evaluate_cell("null", None, quiet + [{**quiet[0], "marginal_p": None} for _ in range(20)])
    assert cell["calibration_worlds"] == 1000 and cell["non_refused"] == 1020
    assert A.calibration_pass([None, None]) == {str(t): False for t in A.MARGINAL_T}
    return {"denominator_after_exclusion": 1000, "refused_marginal_worlds_ignored": True}


# ------------------------------------------------------------------------- revision 3: optimized statistic gate
@check
def equivalence_receipt_recorded():
    cfg = load("config.json")["fast_metric"]
    rec = load("equivalence_receipt.json")
    report = json.loads((ROOT / cfg["equivalence_report"]["path"]).read_text())
    assert rec["comparisons"] == report["comparisons"] == cfg["equivalence"]["comparisons"] == 7_592_064
    assert rec["mismatches"] == report["mismatches"] == 0 and report["mismatch_details"] == []
    assert (rec["statistic_vectors"], rec["fixtures"]) == (22_314, 4_529) == (report["statistic_vectors"], report["fixtures"])
    assert rec["protocol_master_seed_used"] is False and report["protocol_master_seed_used"] is False and report["quick"] is False
    assert report["test_seed"] == 314159265 != V.MASTER_SEED
    assert set(report["sections"]) == {"compensated_sum_vs_builtin_sum", "reference_row_builder_vs_harness_arranged_rows",
                                       "exhaustive_tiny_geometry_fixtures", "adversarial_hand_built_fixtures",
                                       "fallback_domain_fixtures", "randomized_fixed_seed_fixtures", "full_geometry_fixtures"}
    assert all(report["coverage_counters"].get(k, 0) > 0 for k in ("hit_ties", "nonhit_ties", "overall_ties", "zero_lift",
                                                                     "persistence_even", "fallback_vectors"))
    proj = load("compute_projection.json")
    assert proj["benchmark_result_sha256"] == cfg["benchmark_result"]["sha256"]
    assert proj["projection"]["fast_cpu_hours"] < proj["projection"]["reference_cpu_hours"] / 1000
    return {"comparisons": rec["comparisons"], "mismatches": 0, "fast_cpu_hours": proj["projection"]["fast_cpu_hours"]}


def _pattern_world(masked: bool):
    rows = np.arange(R.ROWS)
    cat = ((rows * 7 + rows // 80 + (rows // 5) % 2) % 3).astype(np.int64)
    per = np.sin(rows * 0.37) * ((rows % 11) + 1) + ((rows * 13) % 17) / 10.0
    per[::29] = np.round(per[::29])                                          # exact ties in value
    mem = ((rows[:, None] * 13 + np.arange(384)[None, :] * 7 + (rows[:, None] // 80) * 5) % 4 == 0)
    obs = ((rows % 7) != 3) if masked else np.ones(R.ROWS, dtype=bool)
    strata = tuple((1 + b % 4, int((b // 3) % 2), 0) for b in range(48))
    return V.SyntheticWorld(0, 1, 0, mem, cat, per, obs, strata, V.valid_metadata(strata))


@check
def fast_metric_live_equivalence_deterministic():
    """RNG-free bit-identity of FastMetric against the production _metric on arithmetic-pattern fixtures."""
    from trader.cognition.m32_fast_metric import FastMetric as FM, compensated_sum_columns, reference_rows, reference_statistics
    import itertools
    comparisons = 0
    i = np.arange(60_000, dtype=np.float64)
    cols = [np.mod(i * 0.6180339887498949, 1.0), np.mod(i * 0.7548776662466927, 1.0), np.mod(i * 0.5698402909980532, 1.0)]
    cols[2][::7] = 0.0
    for take in (3, 2, 1):
        got = compensated_sum_columns(cols[:take])
        want = np.asarray([sum(float(c[k]) for c in cols[:take]) for k in range(len(i))])
        assert np.array_equal(got.view(np.uint64), want.view(np.uint64))
        comparisons += len(i)
    special = [np.array([1e16, 0.1, 5e-324]), np.array([1.0, 1e-17, 0.1]), np.array([-1e16, 0.2, 0.0]), np.array([1.0, 1.0, 1e-16])]
    got = compensated_sum_columns(special)
    want = np.asarray([sum(float(c[k]) for c in special) for k in range(3)])
    assert np.array_equal(got.view(np.uint64), want.view(np.uint64)); comparisons += 3

    def compare(fm, bm):
        signed, ok = fm.statistics(bm)
        ref = reference_statistics(fm.memberships, fm.categorical, fm.persistence, fm.observed, fm.labels, bm, fm.B, fm.RPB)
        for h, r in enumerate(ref):
            if r is None:
                assert not ok[h], h
            else:
                assert ok[h] and np.float64(r).view(np.uint64) == signed[h].view(np.uint64), (h, r, signed[h])
        return len(ref)

    B, RPB = 3, 2
    subsets = np.asarray(list(itertools.product([False, True], repeat=B * RPB)), dtype=bool)
    mem = np.repeat(subsets, 3, axis=0).T
    labels = ("case_kind", "persistence", "continuation") * len(subsets)
    xs = (np.asarray([0.5, -1.0, 0.5, 2.0, -1.0, 0.5]), np.arange(1.0, 7.0))
    os_ = (np.ones(6, bool), np.asarray([1, 0, 1, 1, 0, 1], bool))
    perms = [np.asarray(p) for p in itertools.permutations(range(B))]
    fixtures = 0
    for y in list(itertools.product(range(3), repeat=6))[::40]:
        for o in os_:
            fm = FM(mem, np.asarray(y), xs[sum(y) % 2], o, labels, blocks=B, rows_per_block=RPB, chunk=(1, 2, 3, 64)[sum(y) % 4])
            fixtures += 1
            comparisons += sum(compare(fm, p) for p in perms)
    B, RPB = 4, 3
    n = B * RPB
    subsets = np.asarray(list(itertools.product([False, True], repeat=n)), dtype=bool)[::53]
    mem = np.repeat(subsets, 3, axis=0).T
    labels = ("case_kind", "persistence", "continuation") * len(subsets)
    ones = np.ones(n, bool)
    cases = ((np.asarray([2, 1, 0, 0, 1, 2, 1, 0, 2, 2, 0, 1]), ones, np.arange(n, dtype=float)),
             (np.full(n, 1), ones, np.arange(n, dtype=float)),
             (np.asarray([0, 1, 2, 2, 1, 0, 1, 1, 2, 0, 0, 1]), np.asarray([1, 0, 1, 0, 1, 1, 1, 0, 0, 1, 1, 0], bool), np.arange(n, dtype=float)[::-1]),
             (np.asarray([0, 1, 2] * 4), ones, np.full(n, 3.25)),
             (np.asarray([0, 0, 1, 1, 2, 2] * 2), ones, np.asarray([1, 1, 2, 2, 3, 3, 1, 2, 3, 1, 2, 3], dtype=float)),
             (np.asarray([0, 1, 2] * 4), ones, np.asarray([np.nan, 1.0, np.inf, 2.0, -np.inf, 3.0, np.nan, 4.0, 5.0, 6.0, np.inf, 7.0])),
             (np.asarray([0, 10, 2] * 4), ones, np.arange(n, dtype=float)),
             (np.asarray([0, 1, 2] * 4), ones, np.asarray([0.0, -0.0, 1.0, 0.0, -0.0, 2.0, -0.0, 0.0, 3.0, 0.0, 1.0, -0.0])))
    perms4 = [np.asarray(p) for p in itertools.permutations(range(B))]
    for y, o, x in cases:
        for chunk in (1, 5, 64):
            fm = FM(mem, y, x, o, labels, blocks=B, rows_per_block=RPB, chunk=chunk)
            fixtures += 1
            comparisons += sum(compare(fm, p) for p in perms4)
    # full geometry: builder identity with the harness, then all 384 hypotheses, identity and one permuted map
    for masked in (False, True):
        world = _pattern_world(masked)
        groups = R.strata_all(world.block_strata)
        bm = R.draw_block_map(DeterministicStub(), groups)
        rows_h, lookup_h = V._arranged_rows(world, bm)
        rows_r, lookup_r = reference_rows(world.categorical, world.persistence, world.observed, bm, 48, 80)
        assert rows_h == rows_r and lookup_h == lookup_r
        fm = FM.from_world(world)
        fixtures += 1
        for m in (None, bm):
            signed, ok = fm.statistics(m)
            ref = R.actual_statistics(world, m, range(384))
            assert ok.all() and np.array_equal(signed.view(np.uint64), ref.view(np.uint64))
            comparisons += 384
    return {"comparisons": comparisons, "fixtures": fixtures, "rng_free": True, "bit_identical": True}


@check
def runner_fast_equals_reference_receipts():
    only = {0, 130, 260, 330}
    truth = [{"classification": "true_null", "sign": None}] * 384
    for world in (fixture_world(only=only), _sparse_pattern_world(only)):
        groups = R.strata_all(world.block_strata)
        maps = [R.draw_block_map(DeterministicStub(), groups),                        # roll within strata
                R.draw_block_map(DeterministicStub("identity"), groups),                # identity is a legal draw
                np.asarray([3 - (b % 4) + (b // 4) * 4 for b in range(48)])]            # reversal within groups of four blocks
        fast = R.world_receipt(world, None, 90, "X", {}, draws=3, truth=truth, injected=(), statistic="fast", block_maps=maps)
        ref = R.world_receipt(world, None, 90, "X", {}, draws=3, truth=truth, injected=(), statistic="reference", block_maps=maps)
        strip = lambda r: {k: v for k, v in r.items() if k not in ("statistic_path", "sha256")}
        assert fast["statistic_path"] == "fast" and ref["statistic_path"] == "reference"
        assert strip(fast) == strip(ref)
        drawn_fast = R.world_receipt(world, DeterministicStub(), 90, "X", {}, draws=2, truth=truth, injected=(), statistic="fast")
        drawn_ref = R.world_receipt(world, DeterministicStub(), 90, "X", {}, draws=2, truth=truth, injected=(), statistic="reference")
        assert strip(drawn_fast) == strip(drawn_ref)
    try:
        R.world_receipt(fixture_world(only=only), None, 90, "X", {}, draws=3, truth=truth, injected=(), block_maps=[np.arange(48)])
        raise AssertionError("block map count mismatch accepted")
    except R.IntegrityError as exc:
        assert str(exc) == "block_map_count_mismatch"
    try:
        R.world_receipt(fixture_world(only=only), None, 90, "X", {}, draws=1, truth=truth, injected=(), statistic="mystery",
                        block_maps=[np.arange(48)])
        raise AssertionError("unknown statistic path accepted")
    except R.IntegrityError as exc:
        assert str(exc) == "unknown_statistic_path"
    return {"worlds": 2, "receipts_identical": True, "default_path": "fast"}


def _sparse_pattern_world(only):
    world = _pattern_world(True)
    keep = np.zeros(384, dtype=bool)
    keep[sorted(only)] = True
    return V.SyntheticWorld(0, 1, 0, world.memberships & keep[None, :], world.categorical, world.persistence, world.observed,
                            world.block_strata, world.metadata)


@check
def equivalence_gate_fails_closed():
    import copy
    import shutil
    cfg_full = load("config.json")
    cfg = cfg_full["fast_metric"]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for key in ("module", "suite", "benchmark_script", "equivalence_report", "benchmark_result", "artifact_hashes",
                    "reference", "equivalence_test"):
            dest = root / cfg[key]["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / cfg[key]["path"], dest)
        receipt = BUNDLE / "artifacts/equivalence_receipt.json"
        assert R.equivalence_gate_static(root, cfg_full, receipt)["mismatches"] == 0        # untouched copies pass
        outcomes = {}

        def expect_fail(name, mutate_file=None, mutate_cfg=None, rehash=None, receipt_path=receipt):
            c = copy.deepcopy(cfg_full)
            saved = {}
            try:
                if mutate_file:
                    path = root / cfg[mutate_file[0]]["path"]
                    saved[path] = path.read_bytes()
                    path.write_bytes(mutate_file[1](path.read_bytes()))
                    if rehash:                       # keep every hash consistent so only the content check can reject
                        new = hashlib.sha256(path.read_bytes()).hexdigest()
                        c["fast_metric"][rehash]["sha256"] = new
                        reg_path = root / cfg["artifact_hashes"]["path"]
                        if reg_path != path:
                            saved[reg_path] = reg_path.read_bytes()
                            reg = json.loads(reg_path.read_text())
                            reg["sha256"][cfg[rehash]["path"]] = new
                            reg_path.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
                            c["fast_metric"]["artifact_hashes"]["sha256"] = hashlib.sha256(reg_path.read_bytes()).hexdigest()
                if mutate_cfg:
                    mutate_cfg(c["fast_metric"])
                try:
                    R.equivalence_gate_static(root, c, receipt_path)
                    outcomes[name] = "NOT REJECTED"
                except R.IntegrityError as exc:
                    outcomes[name] = str(exc)
            finally:
                for path, data in saved.items():
                    path.write_bytes(data)

        def edit_json(fn):
            def go(raw):
                d = json.loads(raw); fn(d); return (json.dumps(d, sort_keys=True, indent=1) + "\n").encode()
            return go
        expect_fail("module_byte_drift", ("module", lambda b: b + b" "))
        expect_fail("suite_byte_drift", ("suite", lambda b: b + b" "))
        expect_fail("reference_byte_drift", ("reference", lambda b: b + b" "))
        expect_fail("benchmark_result_drift", ("benchmark_result", lambda b: b + b" "))
        expect_fail("report_mismatch_count", ("equivalence_report", edit_json(lambda d: d.__setitem__("mismatches", 1))), rehash="equivalence_report")
        expect_fail("report_quick_run", ("equivalence_report", edit_json(lambda d: d.__setitem__("quick", True))), rehash="equivalence_report")
        expect_fail("report_master_seed_used", ("equivalence_report", edit_json(lambda d: d.__setitem__("protocol_master_seed_used", True))),
                    rehash="equivalence_report")
        expect_fail("report_fewer_comparisons", ("equivalence_report", edit_json(lambda d: d.__setitem__("comparisons", d["comparisons"] - 1))),
                    rehash="equivalence_report")
        expect_fail("registry_disagrees", ("artifact_hashes", edit_json(lambda d: d["sha256"].__setitem__(cfg["module"]["path"], "0" * 64))),
                    rehash="artifact_hashes")
        expect_fail("config_expects_fewer", mutate_cfg=lambda c: c["equivalence"].__setitem__("mismatches", 1))
        bad_receipt = root / "receipt.json"
        d = json.loads(receipt.read_text()); d["mismatches"] = 3; bad_receipt.write_text(json.dumps(d))
        expect_fail("receipt_mismatches", receipt_path=bad_receipt)
        d = json.loads(receipt.read_text()); d["fast_metric_sha256"] = "0" * 64; bad_receipt.write_text(json.dumps(d))
        expect_fail("receipt_module_hash", receipt_path=bad_receipt)
        assert all(v != "NOT REJECTED" for v in outcomes.values()), outcomes
        # a closed gate refuses the optimized path; the state is restored either way
        was = R._GATE["open"]
        try:
            R._GATE["open"] = False
            try:
                R.world_receipt(fixture_world(only={0}), None, 90, "X", {}, draws=1, truth=[{"classification": "true_null", "sign": None}] * 384,
                                injected=(), block_maps=[np.arange(48)])
                raise AssertionError("closed gate accepted")
            except R.IntegrityError as exc:
                assert str(exc) == "fast_metric_gate_closed"
            try:
                R.open_statistic_gate({"mismatches": 5})
                raise AssertionError("gate opened without verified equivalence")
            except R.IntegrityError:
                pass
        finally:
            R._GATE["open"] = was
    return {"tamper_cases_rejected": len(outcomes), "reasons": sorted(set(outcomes.values()))}


@check
def benchmark_core_with_stub():
    only = {0, 130, 260, 330}
    world = _sparse_pattern_world(only)
    clock = iter(np.arange(1, 200, 0.5)).__next__                              # deterministic fake clock (no wall time)
    out = R.benchmark_world(world, DeterministicStub(), reference_draws=2, fast_draws=3, clock=clock)
    assert out["workload"]["tested_hypotheses"] == 4 and out["equivalence"]["bitwise_identical"] is True
    assert out["equivalence"]["hypotheses_compared"] == 4 * 3
    assert out["projection"]["cpu_hours"] > 0 and set(out["projection"]["wall_hours"]) == {"4", "64", "256"}
    assert out["fast"]["speedup_vs_reference"] > 0
    empty = V.SyntheticWorld(0, 1, 0, np.zeros_like(world.memberships), world.categorical, world.persistence, world.observed,
                             world.block_strata, world.metadata)
    try:
        R.benchmark_world(empty, DeterministicStub(), reference_draws=1, fast_draws=1)
        raise AssertionError("benchmark on a world with no tested hypotheses")
    except R.IntegrityError as exc:
        assert str(exc) == "benchmark_world_has_no_tested_hypotheses"
    return {"identity_checked_hypotheses": out["equivalence"]["hypotheses_compared"], "projection_keys": sorted(out["projection"])}


@check
def frozen_truth_package_consumed_unchanged():
    freeze = json.loads((ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921/FREEZE.json").read_text())
    pkg = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921/package"
    assert hashlib.sha256((pkg / "manifest.sha256").read_bytes()).hexdigest() == freeze["package_sha256"]
    assert freeze["checks"]["rows"] == 21_120 and freeze["checks"]["unresolved_rows"] == 0 and freeze["immutable"] is True
    for line in (pkg / "manifest.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((pkg / name).read_bytes()).hexdigest() == digest, name
    for scenario, dgp, corr in ((None, 1, 0), (None, 3, 4), (101, None, 2), (107, None, 4)):
        recs = R.load_truth(scenario, dgp, corr)
        assert len(recs) == 384 and all(r["classification"] in ("true_null", "analytic_non_null") for r in recs)
    # v4: the corrected protocol requires every P1/P3 hypothesis, every correlation, to be true_null (section 3.1)
    for scenario in (101, 103):
        for corr in range(5):
            recs = R.load_truth(scenario, None, corr)
            assert all(r["classification"] == "true_null" and r.get("sign") is None for r in recs), (scenario, corr)
    return {"package_sha256": freeze["package_sha256"], "rows": 21_120, "p1_p3_true_null_checked": True}


def run() -> dict:
    results, ok = [], True
    for fn in CHECKS:
        try:
            detail = fn()
            results.append({"check": fn.__name__, "result": "PASS", "detail": detail})
        except BaseException as exc:                    # noqa: BLE001  fail closed on anything, incl. the RNG guard
            ok = False
            results.append({"check": fn.__name__, "result": "FAIL", "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    ok = ok and GUARD["attempts"] == 0
    return {"schema": "m3.2-scheme-d-preflight-report.v1", "result": "PASS" if ok else "FAIL",
            "checks_total": len(results), "checks_passed": sum(r["result"] == "PASS" for r in results),
            "rng_construction_attempts": GUARD["attempts"], "rng_constructed": False,
            "worlds_generated_from_random_generator": 0, "seed_tuples_materialised": 0, "results": results}


def main(out: Path | None = None) -> int:
    if os.environ.get("OPENBLAS_NUM_THREADS") != "1":
        print("fail closed: OPENBLAS_NUM_THREADS must be 1", file=sys.stderr)
        return 2
    report = run()
    text = json.dumps(report, sort_keys=True, indent=1) + "\n"
    if out:
        out.write_text(text)
    else:
        print(text)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    raise SystemExit(main(out))
