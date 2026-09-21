#!/usr/bin/env python3
"""Deterministic bit-exact equivalence suite: FastMetric vs the production ``_metric`` reference.

Fixtures are synthetic arrays, never protocol worlds.  Randomized fixtures use TEST_SEED, a fixture-only
seed that is not the protocol master seed and is never combined with the protocol seed map.
Comparison is bit-level (uint64 view): None <-> ok False, otherwise identical float64 bit patterns.
"""
from __future__ import annotations

import itertools
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from trader.cognition import m32_scheme_d_validation as V  # noqa: E402
from trader.cognition.m32_fast_metric import (FastMetric, compensated_sum_columns,  # noqa: E402
                                              reference_rows, reference_statistics)

TEST_SEED = 314159265          # fixture-only; NOT the protocol master seed (2026091902)
LABELS3 = ("case_kind", "persistence", "continuation")


class Tally:
    def __init__(self):
        self.fixtures = self.vectors = self.comparisons = self.untestable = self.fallback_vectors = 0
        self.mismatches: list = []
        self.coverage: dict = {}
        self.sections: dict = {}

    def merge_counters(self, counters):
        for k, v in (counters or {}).items():
            self.coverage[k] = self.coverage.get(k, 0) + v


def compare(fast: FastMetric, ref_args: dict, block_map, tally: Tally, tag: str) -> None:
    signed, ok = fast.statistics(block_map)
    ref = reference_statistics(fast.memberships, fast.categorical, fast.persistence, fast.observed, fast.labels,
                               block_map, fast.B, fast.RPB)
    ref_ok = np.asarray([r is not None for r in ref])
    ref_val = np.asarray([r if r is not None else 0.0 for r in ref], dtype=np.float64)
    tally.vectors += 1
    tally.comparisons += len(ref)
    tally.untestable += int((~ref_ok).sum())
    bad = (ref_ok != ok) | (ref_ok & (ref_val.view(np.uint64) != np.where(ok, signed, 0.0).view(np.uint64)))
    if bad.any():
        for h in np.flatnonzero(bad)[:3]:
            tally.mismatches.append({"tag": tag, "hypothesis": int(h), "label": fast.labels[int(h)],
                                     "reference": ref[int(h)], "fast": float(signed[int(h)]), "fast_ok": bool(ok[int(h)])})
        tally.mismatches.append({"tag": tag, "count": int(bad.sum())})


def make(memberships, y, o, x, labels, B, RPB, collect=True, chunk=64) -> FastMetric:
    return FastMetric(np.asarray(memberships, dtype=bool), np.asarray(y, dtype=np.int64), np.asarray(x, dtype=np.float64),
                      np.asarray(o, dtype=bool), labels, blocks=B, rows_per_block=RPB, chunk=chunk, collect_counters=collect)


def all_perms(B):
    return [np.asarray(p, dtype=np.int64) for p in itertools.permutations(range(B))]


# ------------------------------------------------------------------------------------ 0. Neumaier replica
def section_compensated_sum(t: Tally, n: int = 400_000) -> None:
    rng = np.random.default_rng(TEST_SEED)
    cases = [rng.random((n, 3)), rng.random((n, 2)), rng.random((n, 1)),
             rng.standard_normal((n, 3)) * 10.0 ** rng.integers(-8, 8, (n, 3)),
             (rng.integers(0, 5, (n, 3)) / 7.0), np.zeros((1000, 3)), np.full((1000, 3), 0.1),
             np.tile([1e16, 1.0, -1e16], (1000, 1)), np.tile([0.1, 0.2, 0.3], (1000, 1)),
             np.tile([5e-324, 5e-324, 1.0], (1000, 1)), np.tile([1.0, 1e-17, 1e-17], (1000, 1))]
    bad = 0
    for arr in cases:
        got = compensated_sum_columns([arr[:, i] for i in range(arr.shape[1])])
        want = np.asarray([sum(map(float, row)) for row in arr])
        bad += int((got.view(np.uint64) != want.view(np.uint64)).sum())
        t.comparisons += len(arr)
    if bad:
        t.mismatches.append({"tag": "compensated_sum", "count": bad})
    t.sections["compensated_sum_vs_builtin_sum"] = sum(len(a) for a in cases)


# ------------------------------------------------------------------------------------ 1. reference row builder == harness
def section_builder_identity(t: Tally) -> None:
    rng = np.random.default_rng(TEST_SEED + 1)
    rows_n = V.ROWS
    memberships = rng.random((rows_n, V.HYPOTHESES)) < 0.2
    cat = rng.integers(0, 3, rows_n)
    per = rng.standard_normal(rows_n)
    per[::37] = 0.0
    obs = rng.random(rows_n) < 0.8
    world = V.SyntheticWorld(0, 1, 0, memberships, cat, per, obs, tuple((1, 0, 0) for _ in range(48)), {})
    checked = 0
    for bm in (None, rng.permutation(48), rng.permutation(48)):
        rows_h, lookup_h = V._arranged_rows(world, bm)
        rows_r, lookup_r = reference_rows(cat, per, obs, bm, V.BLOCKS, V.ROWS_PER_BLOCK)
        if rows_h != rows_r or lookup_h != lookup_r:
            t.mismatches.append({"tag": "builder_identity"})
        checked += 1
    t.sections["reference_row_builder_vs_harness_arranged_rows"] = checked


# ------------------------------------------------------------------------------------ 2. exhaustive tiny geometry
def section_exhaustive(t: Tally, stride: int = 1) -> None:
    B, RPB = 3, 2
    rows_n = B * RPB
    subsets = np.asarray(list(itertools.product([False, True], repeat=rows_n)), dtype=bool)        # 64 membership vectors
    memberships = np.repeat(subsets, 3, axis=0).T                                                    # (6, 192): 3 labels each
    labels = LABELS3 * len(subsets)
    x_patterns = [np.asarray([0.5, -1.0, 0.5, 2.0, -1.0, 0.5]), np.asarray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])]
    o_patterns = [np.ones(rows_n, bool), np.asarray([1, 0, 1, 1, 0, 1], bool)]
    perms = all_perms(B)
    ys = list(itertools.product(range(3), repeat=rows_n))[::stride]
    for y in ys:
        for o in o_patterns:
            fm = make(memberships, y, o, x_patterns[sum(y) % 2], labels, B, RPB, chunk=(1, 2, 3, 64)[sum(y) % 4])
            t.fixtures += 1
            for bm in perms:
                compare(fm, {}, bm, t, "exhaustive")
            t.merge_counters(fm.counters)
    t.sections["exhaustive_tiny_geometry_fixtures"] = len(ys) * len(o_patterns)


# ------------------------------------------------------------------------------------ 3. hand-built adversarial
def section_adversarial(t: Tally, stride: int = 1) -> None:
    B, RPB = 4, 3
    n = B * RPB
    subsets = np.asarray(list(itertools.product([False, True], repeat=n)), dtype=bool)[::stride]     # 4096 / stride
    memberships = np.repeat(subsets, 3, axis=0).T
    labels = LABELS3 * len(subsets)
    inf, nan = float("inf"), float("nan")
    ones = np.ones(n, bool)
    cases = {
        "ties_first_occurrence_3class": (np.asarray([2, 1, 0, 0, 1, 2, 1, 0, 2, 2, 0, 1]), ones, np.arange(n, dtype=float)),
        "single_class_present": (np.full(n, 1), ones, np.arange(n, dtype=float)),
        "two_classes_1_and_2_only": (np.asarray([1, 2] * 6), ones, np.arange(n, dtype=float) % 4),
        "absent_class_0": (np.asarray([2, 2, 1, 2, 1, 1, 2, 1, 2, 1, 1, 2]), ones, np.arange(n, dtype=float)),
        "sparse_codes_0_5_9": (np.asarray([5, 9, 0, 5, 5, 9, 0, 0, 9, 5, 0, 9]), ones, np.arange(n, dtype=float)),
        "all_unobserved": (np.asarray([0, 1, 2] * 4), np.zeros(n, bool), np.arange(n, dtype=float)),
        "single_observed_row": (np.asarray([0, 1, 2] * 4), np.eye(n, dtype=bool)[5], np.arange(n, dtype=float)),
        "few_observed_masked_blocks": (np.asarray([0, 1, 2, 2, 1, 0, 1, 1, 2, 0, 0, 1]),
                                       np.asarray([1, 0, 1, 0, 1, 1, 1, 0, 0, 1, 1, 0], bool), np.arange(n, dtype=float)[::-1]),
        "constant_persistence": (np.asarray([0, 1, 2] * 4), ones, np.full(n, 3.25)),
        "persistence_heavy_ties_even_odd": (np.asarray([0, 0, 1, 1, 2, 2] * 2), ones, np.asarray([1, 1, 2, 2, 3, 3, 1, 2, 3, 1, 2, 3], dtype=float)),
        "negative_and_huge_values": (np.asarray([0, 1, 2] * 4), ones, np.asarray([-1e300, 1e300, -5.0, 0.5, 1e-300, -1e-300, 2.0, 7.0, -7.0, 1e10, -1e10, 3.0])),
        "nan_inf_persistence_observed": (np.asarray([0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2]), ones,
                                         np.asarray([nan, 1.0, inf, 2.0, -inf, 3.0, nan, 4.0, 5.0, 6.0, inf, 7.0])),
        "overflowing_deviation": (np.asarray([0, 1, 2] * 4), ones, np.asarray([1.7e308, -1.7e308, 0.0, 1.0, 2.0, 3.0, 1.7e308, -1.7e308, 4.0, 5.0, 6.0, 7.0])),
        "zeros_single_sign": (np.asarray([0, 1, 2] * 4), ones, np.asarray([0.0] * 8 + [1.0, 2.0, 3.0, 4.0])),
        "subnormals": (np.asarray([0, 1, 2] * 4), ones, np.asarray([5e-324, 1e-323, 0.0, 5e-324, 2e-323, 1e-323, 0.0, 5e-324, 1.5e-323, 5e-324, 0.0, 1e-323])),
        "majority_tie_all_classes_equal": (np.asarray([0, 1, 2] * 4), ones, np.arange(n, dtype=float)),
    }
    perms = all_perms(B)
    for name, (y, o, x) in cases.items():
      for chunk in (1, 2, 5, 64):
        fm = make(memberships, y, o, x, labels, B, RPB, chunk=chunk)
        t.fixtures += 1
        for bm in perms:
            compare(fm, {}, bm, t, f"adversarial:{name}:chunk{chunk}")
        t.merge_counters(fm.counters)
    t.sections["adversarial_hand_built_fixtures"] = len(cases) * 4


# ------------------------------------------------------------------------------------ 4. fallback domain
def section_fallback(t: Tally) -> None:
    B, RPB = 4, 3
    n = B * RPB
    subsets = np.asarray(list(itertools.product([False, True], repeat=n)), dtype=bool)[::7]
    memberships = np.repeat(subsets, 3, axis=0).T
    labels = LABELS3 * len(subsets)
    mixed_zero = np.asarray([0.0, -0.0, 1.0, 0.0, -0.0, 2.0, -0.0, 0.0, 3.0, 0.0, 1.0, -0.0])
    cases = {"mixed_signed_zero": (np.asarray([0, 1, 2] * 4), mixed_zero, "mixed_signed_zero"),
             "class_code_ten": (np.asarray([0, 10, 2] * 4), np.arange(n, dtype=float), "class_code_outside_0_9"),
             "negative_class_code": (np.asarray([-1, 1, 2] * 4), np.arange(n, dtype=float), "class_code_outside_0_9")}
    for name, (y, x, reason) in cases.items():
        fm = make(memberships, y, np.ones(n, bool), x, labels, B, RPB)
        if fm.unsupported != reason:
            t.mismatches.append({"tag": f"fallback_not_flagged:{name}", "got": fm.unsupported})
        t.fixtures += 1
        for bm in all_perms(B)[:6]:
            compare(fm, {}, bm, t, f"fallback:{name}")
        t.merge_counters(fm.counters)
    t.sections["fallback_domain_fixtures"] = len(cases)


# ------------------------------------------------------------------------------------ 5. fixed-seed randomized sweep
def section_random(t: Tally, fixtures: int = 3000) -> None:
    rng = np.random.default_rng(TEST_SEED + 2)
    for f in range(fixtures):
        B, RPB, H = int(rng.integers(2, 9)), int(rng.integers(1, 10)), int(rng.integers(1, 15))
        n = B * RPB
        density = rng.choice([0.0, 0.05, 0.3, 0.7, 1.0])
        memberships = rng.random((n, H)) < density
        alphabet = ([0], [0, 1], [0, 1, 2], [1, 2], [0, 2, 5, 9])[int(rng.choice(5, p=[.05, .25, .45, .1, .15]))]
        probs = rng.dirichlet(np.ones(len(alphabet)) * rng.choice([0.3, 1.0, 5.0]))
        y = rng.choice(alphabet, size=n, p=probs)
        o = rng.random(n) < rng.choice([0.0, 0.3, 0.6, 0.9, 1.0])
        mode = int(rng.integers(0, 5))
        if mode == 0:
            x = rng.integers(-2, 3, n).astype(float)
        elif mode == 1:
            x = rng.standard_normal(n)
        elif mode == 2:
            x = np.full(n, float(rng.integers(-1, 2)))
        elif mode == 3:
            x = rng.standard_t(3, n) / np.sqrt(3.0) * 10.0 ** rng.integers(-3, 4)
        else:
            x = rng.integers(0, 3, n).astype(float) - 1.0
            x[rng.random(n) < 0.1] = float("nan")
        if rng.random() < 0.05:
            x[rng.random(n) < 0.1] = -0.0                     # occasionally mixes signed zeros -> fallback path
        labels = [LABELS3[int(k)] for k in rng.integers(0, 3, H)]
        fm = make(memberships, y, o, x, labels, B, RPB, chunk=int(rng.choice([1, 2, 3, 5, 8, 64])))
        t.fixtures += 1
        maps = [None] + [rng.permutation(B) for _ in range(3)]
        for bm in maps:
            compare(fm, {}, bm, t, f"random:{f}")
        t.merge_counters(fm.counters)
    t.sections["randomized_fixed_seed_fixtures"] = fixtures


# ------------------------------------------------------------------------------------ 6. full geometry vs harness
def full_fixture(seed: int, kind: int):
    rng = np.random.default_rng(seed)
    R, H = V.ROWS, V.HYPOTHESES
    thresholds = np.where(np.arange(H) < 256, V.STATE_THRESHOLD, V.LINKED_THRESHOLD)
    memberships = rng.standard_normal((R, H)) > thresholds
    if kind == 0:      # balanced Gaussian, complete masks
        cat, per, obs = rng.integers(0, 3, R), rng.standard_normal(R), np.ones(R, bool)
    elif kind == 1:    # imbalanced heavy-tailed, complete masks
        cat, per, obs = rng.choice(3, R, p=[.65, .25, .10]), rng.standard_t(3, R) / np.sqrt(3.0), np.ones(R, bool)
    elif kind == 2:    # coverage-style masks per block
        rates = rng.choice([0.08, 0.3, 0.5, 0.72], 48)
        cat, per = rng.integers(0, 3, R), rng.standard_normal(R)
        obs = rng.random(R) >= np.repeat(rates, 80)
    else:              # censoring-style masks, imbalanced
        cat, per = rng.choice(3, R, p=[.65, .25, .10]), rng.standard_t(3, R) / np.sqrt(3.0)
        obs = np.ones((48, 16, 5), bool)
        lags = np.minimum(rng.geometric(0.5, (48, 16)) - 1, 5)
        for b in range(48):
            for s in range(16):
                if lags[b, s]:
                    obs[b, s, 5 - lags[b, s]:] = False
        obs = obs.reshape(R)
    return memberships, cat, per, obs, rng


def section_full_geometry(t: Tally, fixtures: int = 4, maps: int = 2, hypotheses=None) -> None:
    labels_all = [h.label for h in V.HYPOTHESIS_TABLE]
    for kind in range(fixtures):
        memberships, cat, per, obs, rng = full_fixture(TEST_SEED + 100 + kind, kind)
        keep = np.arange(V.HYPOTHESES) if hypotheses is None else np.asarray(hypotheses)
        mem, labs = memberships[:, keep], [labels_all[h] for h in keep]
        fm = FastMetric(mem, cat, per, obs, labs, chunk=(64, 7, 33, 128)[kind % 4], collect_counters=True)
        t.fixtures += 1
        world = V.SyntheticWorld(0, 1, 0, memberships, cat, per, obs, tuple((1, 0, 0) for _ in range(48)), {})
        # identity + within-stratum block maps (whole-block permutations as in the protocol's D strata)
        strata = tuple((1 + b % 4, (b // 3) % 2, 0) for b in range(48))
        groups = V.strata_groups(strata)
        bms = [None]
        for _ in range(maps):
            bm = np.arange(48)
            for g in groups:
                bm[g] = rng.permutation(g)
            bms.append(bm)
        for bm in bms:
            compare(fm, {}, bm, t, f"full_geometry:{kind}")
            if hypotheses is None:                            # also against the harness pipeline itself
                rows, lookup = V._arranged_rows(world, bm)
                ids = np.asarray([f"row-{i}" for i in range(V.ROWS)], dtype=object)
                sig, ok = fm.statistics(bm)
                for h in range(0, V.HYPOTHESES, 16):
                    r = V._metric(rows, set(ids[memberships[:, h]]), labels_all[h], lookup).get("signed_effect")
                    t.comparisons += 1
                    if r is None or not ok[h] or struct.pack("<d", r) != struct.pack("<d", float(sig[h])):
                        t.mismatches.append({"tag": f"harness_pipeline:{kind}", "hypothesis": h})
        t.merge_counters(fm.counters)
    t.sections["full_geometry_fixtures"] = fixtures


def run(quick: bool = False) -> dict:
    t = Tally()
    started = time.perf_counter()
    section_compensated_sum(t)
    section_builder_identity(t)
    section_exhaustive(t, stride=9 if quick else 1)
    section_adversarial(t, stride=41 if quick else 5)
    section_fallback(t)
    section_random(t, 300 if quick else 3000)
    section_full_geometry(t, fixtures=1 if quick else 4, maps=1 if quick else 2,
                          hypotheses=list(range(0, 384, 4)) if quick else None)
    return {"schema": "m3.2-fast-metric-equivalence-report.v1", "quick": quick, "test_seed": TEST_SEED,
            "protocol_master_seed_used": False, "fixtures": t.fixtures, "statistic_vectors": t.vectors,
            "comparisons": t.comparisons, "untestable_none_cases": t.untestable, "mismatches": len(t.mismatches),
            "mismatch_details": t.mismatches[:20], "sections": t.sections, "coverage_counters": t.coverage,
            "seconds": time.perf_counter() - started}


if __name__ == "__main__":
    report = run(quick="--quick" in sys.argv)
    text = json.dumps(report, indent=1, sort_keys=True, default=int) + "\n"
    if "--out" in sys.argv:
        Path(sys.argv[sys.argv.index("--out") + 1]).write_text(text)
    print(text)
    raise SystemExit(0 if report["mismatches"] == 0 else 1)
