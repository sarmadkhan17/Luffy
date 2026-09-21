"""Synthetic-only Scheme D actual-statistic validation primitives.

This is deliberately not an inferential runner.  It contains the frozen
synthetic-world definitions and emits pre-RNG symbolic truth tables.  RNG
construction is disabled in this pre-RNG module; a future frozen runner must
provide a separate, explicitly authorized implementation.
"""
from __future__ import annotations

import contextlib
import hashlib
import math
import os
import socket
import sys
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import numpy as np

from .m32_search import _metric


MASTER_SEED = 2026091902
PHASE_BENCHMARK = 90
HYPOTHESES = 384
BLOCKS = 48
SYMBOLS = 16
KEYS = 5
ROWS_PER_BLOCK = SYMBOLS * KEYS
ROWS = BLOCKS * ROWS_PER_BLOCK
FULL_PERMUTATIONS = 767_999
FULL_WORLDS = 75_000
STATE_THRESHOLD = 0.6744897501960817
LINKED_THRESHOLD = 0.8416212335729143
N1_NULL_MAD = 0.47472299235208826
TIE_REL_TOL = 1e-12
FROZEN_HASHES = {
    "trader/cognition/m32_search.py": "1081e3482c0d709b81b4b93db2fa94437ebead2e4bc92c78b9b359f7030a827b",
    "trader/cognition/m32_protocol.py": "ade9d184c8e84a2e0be2219c106e6b77403089637dabd19f33bd34995be1e3bd",
    "trader/cognition/m32_correction.py": "67a617be122c05616aa653cacc501f8e1658e493e659e9530e25c48153f10db7",
}


class IntegrityError(RuntimeError):
    """A deterministic validation or benchmark-isolation check failed."""


class WorldRefused(ValueError):
    """Synthetic metadata failed before permutation RNG construction."""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = tuple(reasons)
        super().__init__(",".join(self.reasons))


@dataclass(frozen=True)
class Hypothesis:
    index: int
    kind: str
    label: str
    threshold: float
    cluster: int
    sector: str


@dataclass
class SyntheticWorld:
    correlation: int
    dgp: int
    world_index: int
    memberships: np.ndarray
    categorical: np.ndarray
    persistence: np.ndarray
    observed: np.ndarray
    block_strata: tuple[tuple[int, int, int], ...]
    metadata: dict


class SeedRegistry:
    """Fail closed: the pre-RNG module cannot construct any generator."""

    def __init__(self) -> None:
        self._seen: set[tuple[int, ...]] = set()

    def generator(
        self,
        phase: int,
        correlation: int,
        dgp: int,
        world: int,
        stream: int,
    ) -> np.random.Generator:
        del phase, correlation, dgp, world, stream
        raise IntegrityError("pre_rng_rng_construction_refused")


def hypothesis_table() -> tuple[Hypothesis, ...]:
    out = []
    for h in range(HYPOTHESES):
        if h < 128:
            kind, label, threshold = "state", "case_kind", STATE_THRESHOLD
        elif h < 256:
            kind, label, threshold = "state", "persistence", STATE_THRESHOLD
        elif h < 320:
            kind, label, threshold = "transition", "continuation", LINKED_THRESHOLD
        else:
            kind, label, threshold = "sequence", "continuation", LINKED_THRESHOLD
        cluster = h % 32
        out.append(Hypothesis(h, kind, label, threshold, cluster,
                              "A" if cluster < 16 else "B"))
    return tuple(out)


HYPOTHESIS_TABLE = hypothesis_table()
THRESHOLDS = np.asarray([h.threshold for h in HYPOTHESIS_TABLE])


def validate_fixed_design() -> None:
    counts = Counter((h.kind, h.label) for h in HYPOTHESIS_TABLE)
    if len(HYPOTHESIS_TABLE) != HYPOTHESES:
        raise IntegrityError("family_size_not_384")
    if counts != Counter({("state", "case_kind"): 128,
                          ("state", "persistence"): 128,
                          ("transition", "continuation"): 64,
                          ("sequence", "continuation"): 64}):
        raise IntegrityError("family_partition_mismatch")
    cluster_counts = Counter(h.cluster for h in HYPOTHESIS_TABLE)
    sector_counts = Counter(h.sector for h in HYPOTHESIS_TABLE)
    if set(cluster_counts.values()) != {12} or sector_counts != {"A": 192, "B": 192}:
        raise IntegrityError("cluster_or_sector_mismatch")
    if FULL_PERMUTATIONS != 767_999 or MASTER_SEED != 2026091902:
        raise IntegrityError("fixed_numeric_design_mismatch")


def verify_frozen_environment(
    root: Path | None = None,
    *,
    python_version: str | None = None,
    numpy_version: str | None = None,
    openblas_threads: str | None = None,
) -> dict:
    """Verify protocol hashes and exact runtime metadata before any RNG."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    for relative, expected in FROZEN_HASHES.items():
        path = root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            raise IntegrityError(f"frozen_hash_mismatch:{relative}")
    actual_python = python_version or ".".join(map(str, sys.version_info[:3]))
    actual_numpy = numpy_version or np.__version__
    actual_threads = openblas_threads if openblas_threads is not None else os.environ.get("OPENBLAS_NUM_THREADS")
    if actual_python != "3.12.3":
        raise IntegrityError("python_version_mismatch")
    if actual_numpy != "2.5.2":
        raise IntegrityError("numpy_version_mismatch")
    if actual_threads != "1":
        raise IntegrityError("openblas_threads_mismatch")
    return {"python": actual_python, "numpy": actual_numpy,
            "openblas_threads": actual_threads, "source_hashes": dict(FROZEN_HASHES)}


def _missing_class(fraction: float) -> int:
    return int(np.searchsorted(np.asarray((0.2, 0.4, 0.6)), fraction, side="right"))


def _membership_latents(rng: np.random.Generator, correlation: int) -> np.ndarray:
    e = rng.standard_normal((ROWS, HYPOTHESES))
    if correlation == 0:
        return e
    if correlation in (1, 2):
        rho = 0.30 if correlation == 1 else 0.70
        f = rng.standard_normal((ROWS, 1))
        return math.sqrt(rho) * f + math.sqrt(1.0 - rho) * e
    if correlation == 3:
        g = rng.standard_normal((ROWS, 1))
        f = rng.standard_normal((ROWS, 32))
        clusters = np.arange(HYPOTHESES) % 32
        return (math.sqrt(0.10) * g + math.sqrt(0.60) * f[:, clusters]
                + math.sqrt(0.30) * e)
    if correlation == 4:
        f = rng.standard_normal((ROWS, 2))
        loadings = np.empty((HYPOTHESES, 2), dtype=np.float64)
        loadings[:, 0] = 0.5
        loadings[:, 1] = np.where(np.arange(HYPOTHESES) % 32 < 16,
                                  math.sqrt(3.0) / 2.0, -math.sqrt(3.0) / 2.0)
        return math.sqrt(0.60) * (f @ loadings.T) + math.sqrt(0.40) * e
    raise IntegrityError("unknown_correlation")


def _nuisance_shift(dgp: int, classes: np.ndarray) -> np.ndarray:
    blocks = np.arange(BLOCKS)
    if dgp == 2:
        return np.asarray((0.0, 0.4, 0.8, 1.2))[classes]
    if dgp == 3:
        quarter = blocks % 4
        regime = (blocks // 3) % 2
        return np.asarray((-0.8, -0.2, 0.4, 0.9))[quarter] + 1.2 * regime
    return np.zeros(BLOCKS)


def generate_world(
    correlation: int = 0,
    dgp: int = 1,
    world_index: int = 0,
    *,
    registry: SeedRegistry | None = None,
) -> SyntheticWorld:
    """Refuse world generation while this module remains pre-RNG."""
    raise IntegrityError("pre_rng_world_generation_refused")

    # Retained frozen implementation for a future separately authorized runner.
    if dgp not in range(4):
        raise IntegrityError("unknown_dgp")
    registry = registry or SeedRegistry()
    data_rng = registry.generator(PHASE_BENCHMARK, correlation, dgp, world_index, 0)
    member_rng = registry.generator(PHASE_BENCHMARK, correlation, dgp, world_index, 1)

    observed = np.ones((BLOCKS, SYMBOLS, KEYS), dtype=np.bool_)
    if dgp == 2:
        nominal = data_rng.integers(0, 4, size=BLOCKS)
        rates = np.asarray((0.08, 0.30, 0.50, 0.72))[nominal]
        observed = data_rng.random(observed.shape) >= rates[:, None, None]
    elif dgp == 3:
        lags = np.minimum(data_rng.geometric(0.5, size=(BLOCKS, SYMBOLS)) - 1, 5)
        for b in range(BLOCKS):
            for s in range(SYMBOLS):
                lag = int(lags[b, s])
                if lag:
                    observed[b, s, KEYS - lag:] = False
    classes = np.asarray([_missing_class(1.0 - observed[b].mean()) for b in range(BLOCKS)])

    latents = _membership_latents(member_rng, correlation)
    shifts = np.repeat(_nuisance_shift(dgp, classes), ROWS_PER_BLOCK)
    memberships = latents + shifts[:, None] > THRESHOLDS[None, :]
    del latents

    heavy = dgp in (1, 3)
    persistence = (data_rng.standard_t(3, size=ROWS) / math.sqrt(3.0)
                   if heavy else data_rng.standard_normal(ROWS))
    if heavy:
        jumps = ((data_rng.random(BLOCKS) < 0.10)
                 * (2.0 * data_rng.standard_t(3, size=BLOCKS) / math.sqrt(3.0)))
        persistence += np.repeat(jumps, ROWS_PER_BLOCK)
    if dgp == 2:
        persistence += np.repeat(np.asarray((0.0, 0.5, 1.0, 1.5))[classes]
                                 * STATE_THRESHOLD, ROWS_PER_BLOCK)
    elif dgp == 3:
        quarter = np.arange(BLOCKS) % 4
        regime = (np.arange(BLOCKS) // 3) % 2
        nuisance = np.asarray((0.9, -0.5, 0.3, -1.0))[quarter] + 1.5 * regime
        persistence += np.repeat(nuisance * N1_NULL_MAD, ROWS_PER_BLOCK)
    probs = np.asarray((0.65, 0.25, 0.10) if heavy else (1 / 3, 1 / 3, 1 / 3))
    categorical = data_rng.choice(3, size=ROWS, p=probs)

    observed_flat = observed.reshape(ROWS)
    strata = tuple((1 + b % 4, int((b // 3) % 2), int(classes[b]))
                   for b in range(BLOCKS))
    metadata = valid_metadata(strata)
    return SyntheticWorld(correlation, dgp, world_index, memberships, categorical,
                          persistence, observed_flat, strata, metadata)


def valid_metadata(strata: Sequence[tuple[int, int, int]]) -> dict:
    return {
        "source_hashes_match": True,
        "config_match": True,
        "certificate": "scheme_d_exchangeable_within_D.v1",
        "between_block_independent": True,
        "footprints_within_block": True,
        "conditioned_mean_variance": True,
        "missingness_outcome_independent": True,
        "confounders_in_D": True,
        "clock_valid": True,
        "links_valid": True,
        "family_size": HYPOTHESES,
        "block_strata": tuple(strata),
    }


def orbit_size(strata: Sequence[tuple[int, int, int]], fingerprints: Sequence[bytes] | None = None) -> int:
    groups: dict[tuple[int, int, int], list[int]] = {}
    for block, key in enumerate(strata):
        groups.setdefault(tuple(key), []).append(block)
    total = 1
    for blocks in groups.values():
        if len(blocks) < 2:
            continue
        ways = math.factorial(len(blocks))
        if fingerprints is not None:
            for count in Counter(fingerprints[b] for b in blocks).values():
                ways //= math.factorial(count)
        total *= ways
    return total


def validate_world_metadata(metadata: dict, fingerprints: Sequence[bytes] | None = None) -> int:
    reasons = []
    checks = {
        "source_hashes_match": "source_hash_mismatch",
        "config_match": "config_mismatch",
        "between_block_independent": "between_block_dependence",
        "footprints_within_block": "block_footprint_crossing",
        "conditioned_mean_variance": "unconditioned_drift",
        "missingness_outcome_independent": "outcome_dependent_missingness",
        "confounders_in_D": "omitted_confounder",
        "clock_valid": "clock_violation",
        "links_valid": "link_crossing",
    }
    for key, reason in checks.items():
        if metadata.get(key) is not True:
            reasons.append(reason)
    if metadata.get("certificate") != "scheme_d_exchangeable_within_D.v1":
        reasons.append("invalid_exchangeability_certificate")
    if metadata.get("family_size") != HYPOTHESES:
        reasons.append("family_size_not_384")
    strata = metadata.get("block_strata")
    if not isinstance(strata, tuple) or len(strata) != BLOCKS:
        reasons.append("invalid_strata")
    else:
        counts = Counter(strata)
        movable = [n for n in counts.values() if n > 1]
        if sum(movable) < 24:
            reasons.append("movable_blocks_below_24")
        if len(movable) < 3:
            reasons.append("movable_strata_below_3")
        orbit = orbit_size(strata, fingerprints)
        if orbit < 1_536_000:
            reasons.append("permutation_orbit_below_floor")
        if Fraction(2, orbit) > Fraction(1, 768_000):
            reasons.append("two_sided_floor_too_coarse")
    if reasons:
        raise WorldRefused(reasons)
    return orbit


def block_fingerprints(world: SyntheticWorld) -> tuple[bytes, ...]:
    out = []
    for b in range(BLOCKS):
        sl = slice(b * ROWS_PER_BLOCK, (b + 1) * ROWS_PER_BLOCK)
        out.append(world.categorical[sl].tobytes() + world.persistence[sl].tobytes()
                   + world.observed[sl].tobytes())
    return tuple(out)


def strata_groups(strata: Sequence[tuple[int, int, int]]) -> tuple[np.ndarray, ...]:
    grouped: dict[tuple[int, int, int], list[int]] = {}
    for block, key in enumerate(strata):
        grouped.setdefault(tuple(key), []).append(block)
    return tuple(np.asarray(grouped[key], dtype=np.int64) for key in sorted(grouped))


def draw_block_map(rng: np.random.Generator, groups: Sequence[np.ndarray]) -> np.ndarray:
    """Draw one synchronized whole-block map; repeated calls are with replacement."""
    mapping = np.arange(BLOCKS, dtype=np.int64)
    for destinations in groups:
        mapping[destinations] = rng.permutation(destinations)
    return mapping


def _arranged_rows(world: SyntheticWorld, block_map: np.ndarray | None):
    if block_map is None:
        source = np.arange(ROWS)
    else:
        within = np.arange(ROWS) % ROWS_PER_BLOCK
        source = block_map[np.arange(ROWS) // ROWS_PER_BLOCK] * ROWS_PER_BLOCK + within
    cat = world.categorical[source]
    persistence = world.persistence[source]
    observed = world.observed[source]
    rows = []
    lookup = {}
    for i in range(ROWS):
        target_id = f"target-{i}"
        rows.append({"row_id": f"row-{i}",
                     "features": {"direction": 1, "current_row_id": target_id},
                     "labels": {"case_kind": int(cat[i]) if observed[i] else None,
                                "price_change_bps": float(persistence[i]) if observed[i] else None}})
        if observed[i]:
            lookup[target_id] = {"row_type": f"class-{int(cat[i])}", "producer": "synthetic"}
    return rows, lookup


def actual_statistic_vector(world: SyntheticWorld, block_map: np.ndarray | None = None) -> np.ndarray:
    """Evaluate all 384 hypotheses through the imported production ``_metric``."""
    rows, lookup = _arranged_rows(world, block_map)
    row_ids = np.asarray([f"row-{i}" for i in range(ROWS)], dtype=object)
    signed = np.empty(HYPOTHESES, dtype=np.float64)
    for hypothesis in HYPOTHESIS_TABLE:
        matched = set(row_ids[world.memberships[:, hypothesis.index]])
        result = _metric(rows, matched, hypothesis.label, lookup)
        value = result.get("signed_effect")
        if value is None or not math.isfinite(value):
            raise IntegrityError(f"nonfinite_metric_h{hypothesis.index}")
        signed[hypothesis.index] = value
    return signed


def exceedance_counts(observed: np.ndarray, permuted: Iterable[np.ndarray]) -> tuple[np.ndarray, int]:
    counts = np.zeros(HYPOTHESES, dtype=np.int64)
    draws = 0
    threshold = np.abs(observed) - TIE_REL_TOL * np.maximum(1.0, np.abs(observed))
    for values in permuted:
        if values.shape != (HYPOTHESES,) or not np.all(np.isfinite(values)):
            raise IntegrityError("failed_draw")
        counts += np.abs(values) >= threshold
        draws += 1
    return counts, draws


def monte_carlo_p(exceedances: int, draws: int) -> Fraction:
    if draws < 0 or not 0 <= exceedances <= draws:
        raise ValueError("invalid_exceedance_count")
    return Fraction(1 + exceedances, 1 + draws)


def bh_rejections(pvalues: Sequence[float | None], q: float = 0.05) -> set[int]:
    if len(pvalues) != HYPOTHESES:
        raise IntegrityError("bh_family_size_not_384")
    tested = sorted(((float(p), i) for i, p in enumerate(pvalues) if p is not None),
                    key=lambda item: (item[0], item[1]))
    cutoff = 0
    for rank, (pvalue, _) in enumerate(tested, 1):
        if pvalue <= rank * q / HYPOTHESES:
            cutoff = rank
    return {i for _, i in tested[:cutoff]}


INJECTIONS = {
    101: (0,),
    102: (128,),
    103: (320,),
    104: (0, 33, 130, 163, 260, 293, 326, 359),
    105: tuple(x for c in range(4) for x in
               (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)),
    106: tuple(range(0, 8)) + tuple(range(136, 144)) + tuple(range(272, 280)) + tuple(range(344, 352)),
    107: (tuple(range(0, 24)) + tuple(range(136, 160)) + tuple(range(272, 288))
          + tuple(range(288, 296)) + tuple(range(320, 336)) + tuple(range(344, 352))),
}
OR_BY_SCENARIO = {101: Fraction(3), 103: Fraction(3), 104: Fraction(2),
                  105: Fraction(2), 106: Fraction(2), 107: Fraction(3, 2)}
PERSISTENCE_SHIFT_BY_SCENARIO = {102: 1.0, 104: 0.5, 105: 0.5,
                                 106: 0.5, 107: 0.25}


def apply_injections(
    world: SyntheticWorld,
    scenario: int,
    *,
    registry: SeedRegistry,
) -> SyntheticWorld:
    """Refuse injected-world generation while this module remains pre-RNG."""
    raise IntegrityError("pre_rng_world_generation_refused")

    # Retained frozen implementation for a future separately authorized runner.
    if scenario not in INJECTIONS:
        raise IntegrityError("unknown_injection_scenario")
    categorical = world.categorical.copy()
    persistence = world.persistence.copy()
    injection_rng = registry.generator(PHASE_BENCHMARK, world.correlation,
                                       scenario, world.world_index, 3)
    for h in sorted(INJECTIONS[scenario]):
        matched = world.memberships[:, h]
        if HYPOTHESIS_TABLE[h].label == "persistence":
            persistence[matched] += PERSISTENCE_SHIFT_BY_SCENARIO[scenario] * N1_NULL_MAD
        else:
            odds_ratio = float(OR_BY_SCENARIO[scenario])
            base = 0.10
            target = odds_ratio * base / (1.0 - base + odds_ratio * base)
            switch_probability = (target - base) / (1.0 - base)
            switch = matched & (categorical != 2) & (injection_rng.random(ROWS) < switch_probability)
            categorical[switch] = 2
    return SyntheticWorld(world.correlation, world.dgp, world.world_index,
                          world.memberships.copy(), categorical, persistence,
                          world.observed.copy(), world.block_strata, dict(world.metadata))


def _bernoulli_count_distribution(probabilities: Sequence[Fraction]) -> list[Fraction]:
    dist = [Fraction(1)]
    for probability in probabilities:
        nxt = [Fraction(0)] * (len(dist) + 1)
        for n, mass in enumerate(dist):
            nxt[n] += mass * (1 - probability)
            nxt[n + 1] += mass * probability
        dist = nxt
    return dist


def _class_probs_after_or(count: int, odds_ratio: Fraction) -> tuple[Fraction, ...]:
    # The protocol specifies repeated realized-label switches.  Each injection
    # uses q=(p2'-p2)/(1-p2) from the N1 base p2=0.10, so overlap composes the
    # survival probability; it is not an odds_ratio**count shortcut.
    survival = (Fraction(10, 9 + odds_ratio)) ** count
    return Fraction(13, 20) * survival, Fraction(1, 4) * survival, 1 - Fraction(9, 10) * survival


def _mixture_probs(probabilities: Sequence[Fraction], odds_ratio: Fraction,
                   forced: int | None = None) -> tuple[Fraction, ...]:
    dist = _bernoulli_count_distribution(probabilities)
    result = [Fraction(0), Fraction(0), Fraction(0)]
    for count, mass in enumerate(dist):
        probs = _class_probs_after_or(count + (forced or 0), odds_ratio)
        for cls in range(3):
            result[cls] += mass * probs[cls]
    return tuple(result)


def _macro_f1_lift_c0(h: int, scenario: int) -> Fraction:
    targets = [x for x in INJECTIONS[scenario] if HYPOTHESIS_TABLE[x].label != "persistence"]
    if h not in targets:
        return Fraction(0)
    q = Fraction(1, 4) if h < 256 else Fraction(1, 5)
    remaining = [Fraction(1, 4) if x < 256 else Fraction(1, 5)
                 for x in targets if x != h]
    conditional_hit = _mixture_probs(remaining, OR_BY_SCENARIO[scenario], forced=1)
    conditional_miss = _mixture_probs(remaining, OR_BY_SCENARIO[scenario], forced=0)
    population = tuple(q * conditional_hit[c] + (1 - q) * conditional_miss[c]
                       for c in range(3))
    pred_hit = max(range(3), key=lambda c: conditional_hit[c])
    pred_miss = max(range(3), key=lambda c: conditional_miss[c])
    pred_base = max(range(3), key=lambda c: population[c])

    def score(a: int, b: int) -> Fraction:
        values = []
        for cls in range(3):
            tp = (q * conditional_hit[cls] if a == cls else 0) + (
                (1 - q) * conditional_miss[cls] if b == cls else 0)
            predicted = (q if a == cls else 0) + ((1 - q) if b == cls else 0)
            fp = predicted - tp
            fn = population[cls] - tp
            values.append(Fraction(0) if 2 * tp + fp + fn == 0
                          else 2 * tp / (2 * tp + fp + fn))
        return sum(values, Fraction(0)) / 3

    return score(pred_hit, pred_miss) - score(pred_base, pred_base)


def _truth_record(h: int, classification: str, sign: int | None, proof: str) -> dict:
    return {"hypothesis": h, "classification": classification, "sign": sign, "proof": proof}


def _scenario_truth(scenario: int, correlation: int) -> list[dict]:
    targets = set(INJECTIONS[scenario])
    persistence_targets = {h for h in targets if HYPOTHESIS_TABLE[h].label == "persistence"}
    records = []
    for hypothesis in HYPOTHESIS_TABLE:
        h = hypothesis.index
        if hypothesis.label == "persistence":
            if not persistence_targets:
                records.append(_truth_record(h, "true_null", None, "outcome_column_independence"))
            elif correlation == 0:
                if h in persistence_targets:
                    records.append(_truth_record(h, "analytic_non_null", 1, "direct_positive_shift"))
                else:
                    records.append(_truth_record(h, "true_null", None, "independent_membership"))
            elif correlation in (1, 2, 3):
                records.append(_truth_record(h, "analytic_non_null", 1,
                                             "positive_association_monotone_shift"))
            else:
                target_sectors = {HYPOTHESIS_TABLE[x].sector for x in persistence_targets}
                if len(target_sectors) == 1:
                    sign = 1 if hypothesis.sector in target_sectors else -1
                    records.append(_truth_record(h, "analytic_non_null", sign,
                                                 "signed_sector_monotone_shift"))
                else:
                    records.append(_truth_record(h, "unclassifiable", None,
                                                 "opposed_sector_effects_require_numeric_orthant_probability"))
            continue

        if scenario in (101, 102, 103):
            records.append(_truth_record(h, "true_null", None,
                                         "class_0_majority_invariant_under_pointwise_injection"))
        elif correlation == 0:
            lift = _macro_f1_lift_c0(h, scenario)
            if lift == 0:
                records.append(_truth_record(h, "true_null", None,
                                             "exact_rational_macro_f1_equality"))
            else:
                records.append(_truth_record(h, "analytic_non_null", 1 if lift > 0 else -1,
                                             "exact_rational_macro_f1_lift"))
        else:
            records.append(_truth_record(h, "unclassifiable", None,
                                         "correlated_threshold_membership_requires_numeric_orthant_probability"))
    return records


def generate_truth_tables() -> dict:
    """Generate all 55 pre-RNG 384-row tables, refusing no ambiguity silently."""
    validate_fixed_design()
    cells = []
    for dgp in range(4):
        for correlation in range(5):
            records = [_truth_record(h, "true_null", None, "null_outcome_independence")
                       for h in range(HYPOTHESES)]
            cells.append({"phase": "null", "dgp": dgp, "correlation": correlation,
                          "records": records})
    roles = {101: "calibration", 102: "power", 103: "calibration",
             104: "power", 105: "power", 106: "power", 107: "sensitivity"}
    for scenario in range(101, 108):
        for correlation in range(5):
            records = _scenario_truth(scenario, correlation)
            cells.append({"phase": roles[scenario], "scenario": scenario,
                          "correlation": correlation, "records": records})
    unresolved_cells = sum(
        any(record["classification"] == "unclassifiable" for record in cell["records"])
        for cell in cells
    )
    return {"schema": "m3.2-scheme-d-symbolic-truth.v2", "hypotheses": HYPOTHESES,
            "cells": cells, "unresolved_cells": unresolved_cells}


def derive_n1_null_mad(order: int = 512) -> float:
    """Deterministic Gauss-Legendre derivation for t3/sqrt(3) plus common jump."""
    nodes, weights = np.polynomial.legendre.leggauss(order)
    theta = nodes * math.pi / 2
    u = np.tan(theta)
    transformed_weights = weights * np.cos(theta) ** 2

    def cdf(x):
        return 0.5 + (np.arctan(x) + x / (1 + x * x)) / math.pi

    def mass(m):
        base = 0.9 * (2 * cdf(m) - 1)
        convolution = 0.1 * np.sum(transformed_weights * (cdf(m - 2 * u) - cdf(-m - 2 * u)))
        return float(base + convolution)

    lo, hi = 0.0, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if mass(mid) < 0.5:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@contextlib.contextmanager
def blocked_network() -> Iterator[None]:
    original_socket = socket.socket
    original_create = socket.create_connection

    def refuse(*_args, **_kwargs):
        raise IntegrityError("network_access_refused")

    socket.socket = refuse
    socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.create_connection = original_create


def exclusive_json(path: Path, payload: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise IntegrityError("output_path_collision") from exc
