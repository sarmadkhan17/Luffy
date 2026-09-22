#!/usr/bin/env python3
"""Full-run wall-time projection from recorded measurements only (no compute, no RNG).

Inputs: phase-90 fast benchmark projection (per-world CPU seconds), the fixture-measured extra fixed cost and layer
overhead, and the measured 4-worker efficiency of this host.  Wall time is quantised by whole shards per worker.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import shard_layer as SL  # noqa: E402

FULL_WORLDS = 75_000
bench = json.loads((HERE / "scaling_benchmark.json").read_text())
per_world = bench["projection"]["per_world_cpu_seconds"]
eff = bench["projection_inputs"]["measured_4_worker_efficiency"]
plan = SL.normalise_plan(SL.runner().cell_plan())
out = {"schema": "m3.2-shard-full-run-projection.v1", "per_world_cpu_seconds": per_world,
       "cpu_hours": FULL_WORLDS * per_world / 3600, "measured_4_worker_efficiency_this_host": eff, "by_shard_worlds": {}}
for sw in (25, 5, 1):
    shards = len(SL.make_shards(plan, sw))
    rows = {}
    for w in (16, 32, 64, 128, 256):
        waves = math.ceil(shards / w)
        hours = waves * sw * per_world / 3600
        rows[str(w)] = {"waves": waves, "wall_hours": hours, "wall_days": hours / 24,
                        "quantisation_loss": waves * w / shards - 1,
                        "wall_days_at_this_host_efficiency": hours / eff / 24}
    out["by_shard_worlds"][str(sw)] = {"shards": shards, "hours_per_shard": sw * per_world / 3600, "workers": rows}
(HERE / "full_run_projection.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
for sw, v in out["by_shard_worlds"].items():
    print(f"shard_worlds={sw} shards={v['shards']} h/shard={v['hours_per_shard']:.2f}")
    for w, r in v["workers"].items():
        print(f"  {w:>3} workers: {r['wall_hours']:8.1f} h = {r['wall_days']:5.2f} d (loss {r['quantisation_loss']*100:.2f}%)  @this-host-eff {r['wall_days_at_this_host_efficiency']:.1f} d")
print("cpu_hours", round(out["cpu_hours"], 1))
