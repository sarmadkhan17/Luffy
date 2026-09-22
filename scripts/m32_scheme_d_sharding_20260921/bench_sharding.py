#!/usr/bin/env python3
"""Non-inferential scaling / overhead benchmark of the shard layer (RNG-free fixtures only; no validation worlds).

Runs the fixture through ``shard_layer.py work --fixture`` with 1/2/4 worker processes (static assignment), reports wall
time, speedup, efficiency and per-worker peak RSS, measures per-world layer overhead in-process, and projects the full
75,000-world run from the recorded phase-90 fast benchmark (per-world CPU seconds) with shard quantisation.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import shard_fixture as F  # noqa: E402
import shard_layer as SL  # noqa: E402

CELLS, DRAWS, SHARD_WORLDS, REPS = "N1/C0:16,N3/C4:16,P1/C0:16,P4/C3:16", 300, 4, 2
PHASE90 = ROOT / "scripts/m32_scheme_d_phase90_fast_benchmark_20260921/benchmark_summary.json"
FULL_WORLDS, PRODUCTION_SHARD_WORLDS = 75_000, 25
ENV = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}


def run_workers(workers: int, out: Path) -> dict:
    common = ["--fixture", "--out", str(out), "--shard-worlds", str(SHARD_WORLDS), "--fixture-cells", CELLS, "--draws", str(DRAWS),
              "--workers", str(workers)]
    cli = [sys.executable, str(HERE / "shard_layer.py")]
    subprocess.run([*cli, "init", *common], check=True, env=ENV, capture_output=True)
    start = time.perf_counter()
    procs = [subprocess.Popen([*cli, "work", *common, "--worker", str(i)], env=ENV, stdout=subprocess.PIPE, text=True)
             for i in range(workers)]
    stats = [json.loads(p.communicate()[0]) for p in procs]
    wall = time.perf_counter() - start
    assert all(p.returncode == 0 for p in procs)
    start = time.perf_counter()
    audit = subprocess.run([*cli, "audit", *common], check=True, env=ENV, capture_output=True, text=True)
    audit_s = time.perf_counter() - start
    start = time.perf_counter()
    subprocess.run([*cli, "merge", *common], check=True, env=ENV, capture_output=True)
    merge_s = time.perf_counter() - start
    return {"workers": workers, "wall_seconds": wall, "audit_seconds": audit_s, "merge_seconds": merge_s,
            "worlds": sum(s["worlds_evaluated"] for s in stats), "worker_wall_seconds": [s["wall_seconds"] for s in stats],
            "worker_peak_rss_mib": [s["peak_rss_mib"] for s in stats]}


def layer_overhead(tmp: Path) -> dict:
    """Per-world cost of the layer (fsynced line, per-line validation, completion) versus the bare evaluator."""
    R = SL.runner()
    with F.installed(R):
        ctx = SL.fixture_context((("N1/C0", 8),), 8, 0)               # draws=0: fixed per-world cost only
        cell = ctx.plan[0]
        t = time.perf_counter()
        for w in range(8):
            ctx.evaluator(cell, w)
        bare = (time.perf_counter() - t) / 8
        ctx2 = SL.fixture_context((("N1/C0", 8),), 8, 0)
        t = time.perf_counter()
        SL.work(tmp / "ovh", ctx2)
        layered = (time.perf_counter() - t) / 8
    return {"fixed_seconds_per_world_draws0": bare, "with_layer_seconds_per_world": layered,
            "layer_overhead_seconds_per_world": max(layered - bare, 0.0)}


def project(per_world_cpu: float, extra_per_world: float, efficiency: float) -> dict:
    n_shards = len(SL.make_shards(SL.normalise_plan(SL.runner().cell_plan()), PRODUCTION_SHARD_WORLDS))
    shard_seconds = PRODUCTION_SHARD_WORLDS * (per_world_cpu + extra_per_world)
    out = {"shard_worlds": PRODUCTION_SHARD_WORLDS, "shards": n_shards, "seconds_per_shard": shard_seconds,
           "hours_per_shard": shard_seconds / 3600, "per_world_cpu_seconds": per_world_cpu + extra_per_world, "workers": {}}
    cpu_hours = FULL_WORLDS * (per_world_cpu + extra_per_world) / 3600
    out["cpu_hours"] = cpu_hours
    for w in (16, 32, 64, 128, 256):
        waves = math.ceil(n_shards / w)
        ideal = waves * shard_seconds / 3600
        out["workers"][str(w)] = {"waves": waves, "quantisation_loss": waves * w / n_shards - 1,
                                  "wall_hours_ideal_linear": cpu_hours / w, "wall_hours_quantised": ideal,
                                  "wall_days_quantised": ideal / 24,
                                  "wall_days_at_measured_efficiency": ideal / efficiency / 24}
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="m32-shard-bench-"))
    try:
        runs = {1: [], 2: [], 4: []}
        for rep in range(REPS):
            for w in (1, 2, 4):
                runs[w].append(run_workers(w, tmp / f"w{w}r{rep}"))
        summary = {}
        base = min(r["wall_seconds"] for r in runs[1])
        for w, rs in runs.items():
            best = min(r["wall_seconds"] for r in rs)
            summary[str(w)] = {"wall_seconds_reps": [r["wall_seconds"] for r in rs], "best_wall_seconds": best,
                               "speedup_vs_1_worker_best": base / best, "efficiency": base / best / w,
                               "mean_efficiency": (sum(r["wall_seconds"] for r in runs[1]) / REPS) /
                                                  (sum(r["wall_seconds"] for r in rs) / REPS) / w,
                               "worlds_per_second": rs[0]["worlds"] / best,
                               "peak_rss_mib_max": max(x for r in rs for x in r["worker_peak_rss_mib"]),
                               "audit_seconds": rs[0]["audit_seconds"], "merge_seconds": rs[0]["merge_seconds"]}
        ovh = layer_overhead(tmp)
        phase90 = json.loads(PHASE90.read_text())
        per_world_cpu = phase90["projection"]["cpu_hours"] * 3600 / FULL_WORLDS
        extra = max(ovh["fixed_seconds_per_world_draws0"] - 0.2094, 0.0) + ovh["layer_overhead_seconds_per_world"]
        result = {"schema": "m3.2-shard-layer-scaling-benchmark.v1", "non_inferential": True, "rng_constructed": False,
                  "fixture": {"cells": CELLS, "worlds": 64, "draws": DRAWS, "shard_worlds": SHARD_WORLDS,
                             "shards": 16, "reps": REPS, "host_cpu_count": os.cpu_count(),
                             "note": "trading kernel and dashboard share this host; 4 workers use every vCPU"},
                  "scaling": summary, "layer_overhead": ovh,
                  "projection": project(per_world_cpu, extra, summary["4"]["efficiency"]),
                  "projection_inputs": {"phase90_cpu_hours": phase90["projection"]["cpu_hours"],
                                        "extra_fixed_seconds_per_world_added": extra,
                                        "measured_4_worker_efficiency": summary["4"]["efficiency"]},
                  "runs": runs}
        (HERE / "scaling_benchmark.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: result[k] for k in ("scaling", "layer_overhead", "projection", "projection_inputs")},
                         indent=1, sort_keys=True))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
