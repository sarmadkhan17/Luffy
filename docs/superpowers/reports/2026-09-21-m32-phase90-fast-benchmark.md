# M3.2 Scheme D phase-90 optimized benchmark (bundle v3), September 21

Owner authorization (benchmark mode only, bound to bundle-v3 SHA-256
`eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01`):
`scripts/m32_scheme_d_phase90_fast_benchmark_20260921/authorization.json`
(SHA-256 `af3a64614a04b40dcdd105d231394d192445ff68c9ce0f2a0f02a7e86db4574c`).
Run once with the unmodified v3 runner (`benchmark` mode; FastMetric gate passed via
`assert_equivalence_gate`); external wrapper `run_benchmark.py` measured process time and peak RSS.
No validation run, search, Gate 2, referee/handoff, protocol or truth change.

| Item | Result |
|---|---|
| Bit identity | reference `_metric` vs `FastMetric` on the generated phase-90 world: **bitwise identical**, 384 hypotheses x 3 vectors (1 observed + 2 permuted) = 1,152 comparisons, 0 mismatches |
| Workload | BENCH/C0/N1, 302 block maps, 300 timed fast draws, seed tuples `[2026091902, 90, 0, 1, 0, 0/1/2]` |
| Runtime | runner 27.48 s; process wall 27.79 s; child CPU 27.58 s user + 0.17 s system (26.76 s of it is the reference identity check) |
| Throughput | 847.6 draws/s/core end-to-end (1.18 ms/draw; 300-draw loop 0.333 s + map draw); 7,562x the reference (8.92 s/vector) |
| Peak memory | 93.4 MiB RSS |
| Full-run projection | 18,881 CPU-hours (2.15 CPU-years), 75,000 worlds x 768,000 draws; excludes world generation, refusal evaluation, receipts |

Wall time, linear scaling / with +20% overhead:

| vCPUs | wall (linear) | wall (+20%) |
|---|---|---|
| 4 | 4,720 h (196.7 d) | 236 d |
| 16 | 1,180 h (49.2 d) | 59.0 d |
| 32 | 590 h (24.6 d) | 29.5 d |
| 64 | 295 h (12.3 d) | 14.8 d |
| 256 | 73.8 h (3.1 d) | 3.7 d |

The bundle's fixture projection (1,023 draws/s, 15,636 CPU-h) was optimistic by about 21% versus a
generated world (847.6 draws/s, 18,881 CPU-h). Receipt SHA-256
`920ff7c0f9ff62b7130d6db237b5c0a6314d260e9d7e4b53317b17fe7c0729bb` (internal
`856ba860…b6`); all hashes in `benchmark_summary.json`.

**Practicality:** a full run is not feasible on this 4-core box and needs off-box parallelism. The runner's
`validation` mode is sequential in one process; deterministic world sharding with resumable
exclusive-create outputs does not exist yet.
