#!/usr/bin/env python3
"""RNG-free deterministic preflight/check suite (protocol section 14 item 6).

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

    def __init__(self, permutation="roll", uniform=0.0):
        self.permutation_mode, self.uniform_value = permutation, uniform

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
    assert Counter(p["kind"] for p in plan) == {"null": 20, "power": 35}
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
    assert c["null"] == 120_000 and c["power"] == 140_000 and c["checks_bootstrap"] == 30 and c["total"] == 260_030
    plan = R.cell_plan()
    null_tuples = sum(p["worlds"] * 3 for p in plan if p["kind"] == "null")
    power_tuples = sum(p["worlds"] * 4 for p in plan if p["kind"] == "power")
    assert (null_tuples, power_tuples) == (c["null"], c["power"])
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
    allowed = {"argparse", "hashlib", "json", "os", "sys", "time", "collections", "fractions", "pathlib", "math",
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
    assert A.power_pass(101, [1] * 1000) and not A.power_pass(101, [1] * 800 + [0] * 200)
    assert A.power_pass(105, [32] * 1000) and not A.power_pass(105, [20] * 900 + [0] * 100)
    assert A.power_pass(104, [8] * 1000) and not A.power_pass(104, [1] * 1000)
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
    return {"wilson": "verified", "clopper_pearson": "exact", "p7": "descriptive"}


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
    classes = np.asarray([R.missing_class(1.0 - w2.observed.reshape(48, 16, 5)[b].mean()) for b in range(48)])
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
    return {"cells_generated_with_stub": len(shapes)}


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
    return {"receipt_hashing": True, "refused_world": "link_crossing"}


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
    return {"package_sha256": freeze["package_sha256"], "rows": 21_120}


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
