#!/usr/bin/env python3
"""Run the fixed tiny phase-90 Scheme D actual-statistic benchmark."""
from __future__ import annotations

import json
import resource
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.cognition.m32_scheme_d_validation import (
    FULL_PERMUTATIONS,
    FULL_WORLDS,
    HYPOTHESES,
    PHASE_BENCHMARK,
    SeedRegistry,
    actual_statistic_vector,
    block_fingerprints,
    blocked_network,
    draw_block_map,
    exceedance_counts,
    generate_world,
    strata_groups,
    validate_fixed_design,
    validate_world_metadata,
    verify_frozen_environment,
)


BENCHMARK_PERMUTATIONS = 2


def main() -> None:
    validate_fixed_design()
    environment = verify_frozen_environment()
    tracemalloc.start()
    started = time.perf_counter()
    registry = SeedRegistry()
    with blocked_network():
        world = generate_world(correlation=0, dgp=1, world_index=0, registry=registry)
        orbit = validate_world_metadata(world.metadata, block_fingerprints(world))
        generated = time.perf_counter()
        observed = actual_statistic_vector(world)
        observed_done = time.perf_counter()
        permutation_rng = registry.generator(PHASE_BENCHMARK, 0, 1, 0, 2)
        groups = strata_groups(world.block_strata)
        permuted = []
        draw_seconds = []
        for _ in range(BENCHMARK_PERMUTATIONS):
            draw_started = time.perf_counter()
            block_map = draw_block_map(permutation_rng, groups)
            permuted.append(actual_statistic_vector(world, block_map))
            draw_seconds.append(time.perf_counter() - draw_started)
        _, successful = exceedance_counts(observed, permuted)
    finished = time.perf_counter()
    _, trace_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    seconds_per_permutation = sum(draw_seconds) / len(draw_seconds)
    fixed_seconds_per_world = generated - started + observed_done - generated
    estimated_seconds_per_world = fixed_seconds_per_world + FULL_PERMUTATIONS * seconds_per_permutation
    estimated_cpu_hours = FULL_WORLDS * estimated_seconds_per_world / 3600
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = int(rss * 1024)  # Linux reports KiB.
    payload = {
        "schema": "m3.2-scheme-d-phase90-benchmark.v1",
        "inferential_validation_run": False,
        "phase_code": PHASE_BENCHMARK,
        "correlation": "C0",
        "dgp": "N1",
        "worlds": 1,
        "hypotheses_per_world": HYPOTHESES,
        "permutations_per_world_tested": successful,
        "generation_seconds": generated - started,
        "observed_statistics_seconds": observed_done - generated,
        "permutation_seconds": draw_seconds,
        "runtime_seconds": finished - started,
        "runtime_per_world_seconds": finished - started,
        "mean_seconds_per_permutation": seconds_per_permutation,
        "peak_rss_bytes": rss_bytes,
        "tracemalloc_peak_bytes": trace_peak,
        "orbit_size": orbit,
        "full_protocol_worlds": FULL_WORLDS,
        "full_protocol_permutations_per_world": FULL_PERMUTATIONS,
        "estimated_cpu_hours_full_protocol": estimated_cpu_hours,
        "estimate_method": "phase90 fixed_cost plus measured mean actual-statistic permutation time times 767999 and 75000",
        "numpy_version": np.__version__,
        "environment": environment,
    }
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
