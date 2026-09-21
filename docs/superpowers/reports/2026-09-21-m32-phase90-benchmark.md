# M3.2 Scheme D phase-90 benchmark (bundle v2), September 21

Owner authorization (benchmark mode only, bound to bundle-v2 SHA-256
`397961d9…c4e`): `scripts/m32_scheme_d_phase90_benchmark_20260921/authorization.json`
(SHA-256 `e3d27eb512e87bea554f7bbced44f5f4f329acdfb7e14111051ea148e03771b8`).
Benchmark run once with the unmodified v2 runner and the imported production
`_metric`; external wrapper `run_benchmark.py` measured process time and peak RSS.
No validation world, search, Gate 2, referee/handoff, protocol or truth change.

| Item | Result |
|---|---|
| Workload | phase 90, cell BENCH/C0/N1, 1 world, 2 permutation maps, 3 statistic vectors (1 observed + 2 permuted), 384 hypotheses tested / 0 refused, 1,152 `_metric` calls, 0 BH rejections; seed tuples `[2026091902, 90, 0, 1, 0, 0/1/2]` |
| Runtime | runner 26.09 s; process wall 26.35 s; child CPU 26.13 s user + 0.16 s system |
| Memory | peak RSS 61.6 MiB (64,643,072 bytes) |
| Throughput | 0.115 statistic vectors/s (8.70 s per vector, upper bound); 44.2 `_metric` calls/s; 0.077 permutation maps/s (includes fixed cost) |
| Full-run extrapolation | 5.76e10 vectors (2.21e13 `_metric` calls) x 8.70 s = 5.01e11 CPU-s = **1.39e8 CPU-hours = 15,871 CPU-years** (about 3,968 wall-years on this 4-core host) |

Method: `runner_seconds / 3 x 75,000 x 767,999+1`; an upper bound because world
generation is included in every vector, but that fixed cost is small next to
the metric loop (preflight fixture timing 7.2 s per vector). Receipt SHA-256
`94ddf656a6b26f180c19b622de00b4c6afd072cc02ba0811aa747b7e9d4b6d14`; all hashes in
`benchmark_summary.json`.

**Practicality:** the full validation is not practical as written. The protocol's
own naive estimate is 15,200 CPU-hours and "hundreds to low thousands" optimized;
the reference path is about 9,000x above the naive figure and about 70,000x above
2,000 CPU-hours. It needs an exact-equivalence-proven optimized statistic (section 3)
and off-box parallelism, then its own benchmark.
