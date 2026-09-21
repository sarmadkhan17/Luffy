# M3.2 Scheme D validation bundle, revision 3 (RNG-free), September 21

Bundle: `scripts/m32_scheme_d_validation_bundle_v3_20260921/`. **Bundle-v3 SHA-256
`eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01`.** Supersedes v2
(`397961d9…`) and v1 (`0b42c6a3…`), both left byte-identical. Frozen truth package
(`a100a19c…13eb`) unchanged. Integrates the exact-equivalent `FastMetric` (commit
`a43336d`). No protocol RNG, seeds, validation worlds, search, Gate 2, referee/handoff. No owner
authorization exists or was created for v3.

## Changes versus v2 (v2 semantics preserved: I1-I8, class edges, I3, I6, truth package)

- Statistic path is `FastMetric`, behind a fail-closed gate: `equivalence_gate_static` checks the
  hashes of the reference (`m32_search.py`), the optimized module, the suite, the equivalence report,
  the benchmark result and the artifact registry, the report content (7,592,064 comparisons, 22,314
  vectors, 4,529 fixtures, 0 mismatches, full run, protocol master seed unused), the bundle receipt,
  and that the loaded code is the pinned code. RNG modes additionally run `assert_equivalence_gate`
  (bundle manifest, all pinned inputs, recorded preflight PASS including the live equivalence check).
- Runner `verify_bundle` now also verifies every pinned input (reference and optimized sources included).
- `world_receipt` gains `statistic` (default `fast`, refused while the gate is closed) and `block_maps`;
  the reference path is kept for the benchmark's live bit-identity check.
- `benchmark` mode now: reference-vs-fast bit identity on the generated phase-90 world, fast throughput over
  300 draws, and a projection; the validation authorization must carry `runtime_estimate_cpu_hours` and
  `fast_benchmark_receipt_sha256` (decision I8: separate optimized benchmark first).
- New artifacts `equivalence_receipt.json`, `compute_projection.json`; 15 manifest-bound files, 14 pinned
  inputs (adds the optimized module, suite, benchmark script, report, benchmark result, registry, test).

## Verification

- Preflight 33/33 PASS, 0 generator constructions. New checks: hash binding, recorded receipt, live RNG-free
  bit-identity (360,099 comparisons on arithmetic-pattern fixtures incl. full geometry), fast-vs-reference
  receipt identity, 12 gate tamper cases, benchmark core through a fixed-pattern stub. Deliberately broken
  optimized variants (naive sum, tie-break ignoring first occurrence, off-by-one order statistic) are caught.
- Independent verifier PASS (about 11 minutes): typed hashes checked against commit `a43336d`; the
  complete equivalence suite re-run, every deterministic field identical (7,592,064 comparisons, 0
  mismatches); its own 147,456-comparison probe on verifier-built fixtures and row builder; projection
  recomputed; runner gate probed; v1/v2 unmodified. 10 tamper cases rejected.

## Compute projection (measured single core, fixed synthetic fixture; linear scaling)

1,023 draws/s per core; 15,636 CPU-hours (1.78 CPU-years) versus 1.22e8 for the reference. Wall time:
4 vCPUs 3,909 h (162.9 days); 64 vCPUs 244 h (10.2 days); 256 vCPUs 61 h (2.5 days); add 10-20% overhead.
Excludes world generation, refusal evaluation and receipts. Peak RSS 87 MiB per worker.

## Limits

The runner's `validation` mode runs cells sequentially in one process; a full run needs deterministic world
sharding with resumable exclusive-create outputs (not built). The verification-result schema label reads
`.v2` (cosmetic). The verifier takes about 11 minutes because it re-runs the equivalence suite.
