#!/usr/bin/env python3
"""External measurement wrapper for the bundle-v2 phase-90 benchmark (benchmark mode only).

Runs the unmodified bundle runner in a child process, measures wall/CPU time and the child's peak RSS,
reads the runner's receipt, and writes benchmark_summary.json with hashes and the full-run extrapolation.
Does not construct any generator itself; the runner does, under the owner authorization record.
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
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v2_20260921"
AUTH, OUT = HERE / "authorization.json", HERE / "out"
FULL_WORLDS, FULL_B, HYPOTHESES = 75_000, 767_999, 384


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
        print(proc.stderr[-800:], file=sys.stderr)
        return proc.returncode
    receipt_path = OUT / "phase90_benchmark_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    tested = HYPOTHESES - len(receipt["refusals"])
    vectors = 1 + receipt["draws"]                       # observed arrangement + permuted arrangements
    runtime = receipt["runtime_seconds"]
    per_vector = runtime / vectors                        # upper bound: includes world generation and receipt work
    total_vectors = FULL_WORLDS * (1 + FULL_B)
    cpu_seconds = total_vectors * per_vector
    summary = {
        "schema": "m3.2-scheme-d-phase90-benchmark-summary.v1", "mode": "benchmark", "inferential_validation_run": False,
        "workload": {"phase_code": 90, "cell": receipt["cell"], "worlds": 1, "correlation": "C0", "dgp": "N1",
                     "permutation_maps": receipt["draws"], "statistic_vectors": vectors,
                     "hypotheses_tested": tested, "hypotheses_refused": len(receipt["refusals"]),
                     "metric_calls": tested * vectors, "reference_metric": "trader.cognition.m32_search._metric (imported)",
                     "seed_tuples": receipt["seed_tuples"], "bh_rejections": receipt["R"]},
        "runtime": {"runner_seconds": runtime, "process_wall_seconds": wall,
                    "child_cpu_user_seconds": usage.ru_utime, "child_cpu_system_seconds": usage.ru_stime},
        "memory": {"peak_rss_bytes": int(usage.ru_maxrss) * 1024, "peak_rss_mib": usage.ru_maxrss / 1024},
        "throughput": {"statistic_vectors_per_second": vectors / runtime, "metric_calls_per_second": tested * vectors / runtime,
                       "permutation_maps_per_second": receipt["draws"] / runtime,
                       "seconds_per_statistic_vector_upper_bound": per_vector},
        "extrapolation": {"method": "runner_seconds / (1 observed + 2 permuted vectors) x 75,000 x (1 + 767,999) vectors; "
                                    "upper bound because fixed world-generation cost is included in every vector",
                          "full_run_statistic_vectors": total_vectors, "full_run_metric_calls": total_vectors * tested,
                          "cpu_seconds": cpu_seconds, "cpu_hours": cpu_seconds / 3600,
                          "cpu_years": cpu_seconds / (3600 * 24 * 365.25),
                          "wall_years_on_this_host_cores": cpu_seconds / (3600 * 24 * 365.25) / (os.cpu_count() or 1),
                          "host_cpu_count": os.cpu_count(),
                          "protocol_estimate_cpu_hours": {"naive_384_statistic": 15200, "expected_optimized": "hundreds to low thousands"},
                          "speedup_needed_for_2000_cpu_hours": cpu_seconds / 3600 / 2000},
        "hashes": {"authorization_sha256": sha(AUTH), "bundle_manifest_sha256": sha(BUNDLE / "BUNDLE_MANIFEST.json"),
                   "runner_sha256": sha(BUNDLE / "runner/scheme_d_runner.py"), "receipt_sha256": sha(receipt_path),
                   "receipt_internal_sha256": receipt["sha256"], "wrapper_sha256": sha(__file__)},
        "environment": {"python": sys.version.split()[0], "openblas_threads": env["OPENBLAS_NUM_THREADS"]},
    }
    (HERE / "benchmark_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
