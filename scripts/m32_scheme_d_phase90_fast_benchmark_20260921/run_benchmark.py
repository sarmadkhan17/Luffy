#!/usr/bin/env python3
"""External measurement wrapper for the bundle-v3 optimized phase-90 benchmark (benchmark mode only).

Runs the unmodified v3 runner in a child process (FastMetric gate, reference-vs-fast bit identity on the
generated phase-90 world, 300 fast draws), measures wall/CPU time and the child's peak RSS, and writes
benchmark_summary.json with hashes and full-run wall-time projections.  Constructs no generator itself.
"""
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v3_20260921"
AUTH, OUT = HERE / "authorization.json", HERE / "out"
VCPUS = (4, 16, 32, 64, 256)


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main() -> int:
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, str(BUNDLE / "runner/scheme_d_runner.py"), "benchmark",
                           "--authorization", str(AUTH), "--out", str(OUT)],
                          capture_output=True, text=True, env=env, cwd=ROOT)
    wall = time.perf_counter() - started
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    if proc.returncode != 0:
        print(proc.stderr[-1500:], file=sys.stderr)
        return proc.returncode
    receipt_path = OUT / "phase90_benchmark_receipt.json"
    r = json.loads(receipt_path.read_text())
    cpu_h = r["projection"]["cpu_hours"]
    summary = {
        "schema": "m3.2-scheme-d-phase90-fast-benchmark-summary.v1", "mode": "benchmark",
        "inferential_validation_run": False,
        "workload": {"cell": r["cell"], "phase": r["phase"], "seed_tuples": r["seed_tuples"], **r["workload"]},
        "bit_identity": r["equivalence"],
        "runtime": {"runner_seconds": r["runtime_seconds"], "process_wall_seconds": wall,
                    "child_cpu_user_seconds": usage.ru_utime, "child_cpu_system_seconds": usage.ru_stime},
        "memory": {"peak_rss_bytes": int(usage.ru_maxrss) * 1024, "peak_rss_mib": usage.ru_maxrss / 1024},
        "throughput": {"fast_draws_per_second_per_core": r["fast"]["draws_per_second"],
                       "seconds_per_draw_end_to_end": r["fast"]["seconds_per_draw_end_to_end"],
                       "speedup_vs_reference": r["fast"]["speedup_vs_reference"],
                       "reference_seconds_per_vector": r["reference"]["seconds_per_vector"]},
        "projection": {"cpu_hours": cpu_h, "cpu_years": r["projection"]["cpu_years"],
                       "wall_hours_linear": {str(n): cpu_h / n for n in VCPUS},
                       "wall_days_linear": {str(n): cpu_h / n / 24 for n in VCPUS},
                       "wall_days_with_20pct_overhead": {str(n): cpu_h / n / 24 * 1.2 for n in VCPUS},
                       "note": r["projection"]["note"]},
        "hashes": {"authorization_sha256": sha(AUTH), "bundle_manifest_sha256": sha(BUNDLE / "BUNDLE_MANIFEST.json"),
                   "runner_sha256": sha(BUNDLE / "runner/scheme_d_runner.py"), "receipt_sha256": sha(receipt_path),
                   "receipt_internal_sha256": r["sha256"], "wrapper_sha256": sha(__file__)},
        "environment": {"python": sys.version.split()[0], "openblas_threads": env["OPENBLAS_NUM_THREADS"],
                        "host_cpu_count": os.cpu_count()},
    }
    (HERE / "benchmark_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
