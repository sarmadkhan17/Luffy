#!/usr/bin/env python3
"""Benchmark reference (production _metric path) vs FastMetric on fixed synthetic full-geometry fixtures.

Fixtures come from a fixture-only seed (not a protocol seed).  No protocol world, no validation run.
Projects full-run CPU-hours from measured per-draw cost (draw + statistic + exceedance update).
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from trader.cognition import m32_scheme_d_validation as V  # noqa: E402
from trader.cognition.m32_fast_metric import FastMetric  # noqa: E402
import equivalence_suite as S  # noqa: E402

FULL_WORLDS, FULL_B = 75_000, 767_999


def main() -> int:
    labels = [h.label for h in V.HYPOTHESIS_TABLE]
    kind = 3                                   # censoring-style masks: the hardest realistic case (ties, masked rows)
    memberships, cat, per, obs, rng = S.full_fixture(S.TEST_SEED + 700, kind)
    strata = tuple((1 + b % 4, int((b // 3) % 2), 0) for b in range(48))
    groups = V.strata_groups(strata)

    def block_map():
        bm = np.arange(48)
        for g in groups:
            bm[g] = rng.permutation(g)
        return bm

    maps = [None] + [block_map() for _ in range(3)]
    world = V.SyntheticWorld(0, 1, 0, memberships, cat, per, obs, strata, {})

    # ---- reference: harness statistic path (rows + 384 x _metric), identical to runner.actual_statistics
    ref_times, ref_vals = [], []
    ids = np.asarray([f"row-{i}" for i in range(V.ROWS)], dtype=object)
    for bm in maps[:3]:
        t0 = time.perf_counter()
        rows, lookup = V._arranged_rows(world, bm)
        out = [V._metric(rows, set(ids[memberships[:, h]]), labels[h], lookup).get("signed_effect") for h in range(384)]
        ref_times.append(time.perf_counter() - t0)
        ref_vals.append(out)

    # ---- fast: per-world precompute (memory measured here), then per-draw
    tracemalloc.start()
    t0 = time.perf_counter()
    fm = FastMetric(memberships, cat, per, obs, labels)
    fm.statistics(None)                         # forces lazy first-occurrence tensors on first tie use
    for bm in maps[1:]:
        fm.statistics(bm)
    precompute = time.perf_counter() - t0
    _, peak_trace = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    tensor_bytes = fm.nbytes()

    # equivalence on the benchmark fixture itself (all 384 hypotheses, 3 maps)
    mism = 0
    for bm, ref in zip(maps[:3], ref_vals):
        sig, ok = fm.statistics(bm)
        for h in range(384):
            if ref[h] is None or not ok[h] or np.float64(ref[h]).view(np.uint64) != sig[h].view(np.uint64):
                mism += 1

    draws = 400
    draw_maps = [block_map() for _ in range(draws)]
    sig_times = []
    t0 = time.perf_counter()
    for bm in draw_maps:
        s0 = time.perf_counter()
        fm.statistics(bm)
        sig_times.append(time.perf_counter() - s0)
    statistic_only = (time.perf_counter() - t0) / draws

    # end-to-end draw loop as the runner would run it: map draw + statistic + exceedance update (test-seeded map RNG)
    observed, _ = fm.statistics(None)
    threshold = np.abs(observed) - V.TIE_REL_TOL * np.maximum(1.0, np.abs(observed))
    counts = np.zeros(384, dtype=np.int64)
    loop_draws = 600
    prng = np.random.Generator(np.random.PCG64(S.TEST_SEED + 9))
    t0 = time.perf_counter()
    for _ in range(loop_draws):
        bm = np.arange(48)
        for g in groups:
            bm[g] = prng.permutation(g)
        vals, _ok = fm.statistics(bm)
        counts += np.abs(vals) >= threshold
    per_draw = (time.perf_counter() - t0) / loop_draws

    ref_vector = float(np.mean(ref_times))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    per_world_fixed = precompute / 4            # precompute + 3 warm statistics vectors / 4 ~ one world's first-use cost
    total_seconds = FULL_WORLDS * (per_world_fixed + (1 + FULL_B) * per_draw)
    ref_total_seconds = FULL_WORLDS * (1 + FULL_B) * ref_vector
    result = {
        "schema": "m3.2-fast-metric-benchmark.v1", "fixture": "synthetic full geometry (censoring-style masks, N1-like probabilities), fixture-only seed",
        "mismatches_on_benchmark_fixture": mism, "hypotheses": 384, "rows": V.ROWS,
        "reference_seconds_per_vector": ref_vector, "reference_vector_seconds_each": ref_times,
        "fast_seconds_per_vector_statistic_only": statistic_only, "fast_seconds_per_draw_end_to_end": per_draw,
        "fast_precompute_seconds_per_world_first_use": precompute, "speedup_statistic_only": ref_vector / statistic_only,
        "speedup_end_to_end_per_draw": ref_vector / per_draw,
        "memory": {"tracemalloc_peak_bytes": peak_trace, "tensor_bytes": tensor_bytes, "process_peak_rss_bytes": rss,
                   "process_peak_rss_mib": rss / 2 ** 20},
        "projection": {"worlds": FULL_WORLDS, "draws_per_world": FULL_B, "vectors_per_world": 1 + FULL_B,
                       "per_world_fixed_seconds": per_world_fixed,
                       "reference_cpu_hours": ref_total_seconds / 3600, "fast_cpu_hours": total_seconds / 3600,
                       "fast_cpu_years": total_seconds / 3600 / 24 / 365.25,
                       "speedup_projected": ref_total_seconds / total_seconds,
                       "note": "excludes world generation, refusal evaluation and receipt writing (small, per world) and any parallel overhead"},
        "host": {"cpu_count": os.cpu_count(), "openblas_threads": os.environ.get("OPENBLAS_NUM_THREADS")},
    }
    print(json.dumps(result, indent=1, sort_keys=True))
    if "--out" in sys.argv:
        Path(sys.argv[sys.argv.index("--out") + 1]).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
