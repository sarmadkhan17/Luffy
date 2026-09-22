# M3.2 Scheme D deterministic sharding / resume layer (bundle v3), September 21

> **Superseded 2026-09-22:** the layer hash below (`2c72e369…`) was replaced by the D1–D3 hardening patch; see
> `2026-09-22-m32-shard-layer-hardening.md` and `LAYER_MANIFEST.json` for the current SHA. The scheme described here is unchanged.

Bundle-v3 SHA-256 `eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01` (manifest, runner, FastMetric,
protocol and truth package unchanged and re-verified). **No RNG constructed, no validation worlds, no protocol or
statistic change, no search / Gate 2 / referee / handoff, no authorization created.** Layer:
`scripts/m32_scheme_d_sharding_20260921/` (`shard_layer.py` SHA-256
`2c72e369d3a9a8cd0060e609222d2907d9a7d7a51f5d4cb22798a4c6bb57fedf`; all hashes in `LAYER_MANIFEST.json`).

## Scheme

- Worlds are numbered in `cell_plan()` order (75,000). A shard is a contiguous world range inside one cell,
  `[k*S, min((k+1)*S, cell.worlds))`; ids are sequential. The partition depends only on (plan, S), not on the worker
  count, so each world is in exactly one shard. Every world's randomness is keyed by its own seed tuple, so shard
  output is independent of process, order and restarts.
- Storage per shard: append-only `.receipts.jsonl.partial` (one fsynced canonical receipt per world, the resume unit),
  finalised by exclusive hard link to `.receipts.jsonl`, then an exclusive `.complete.json` (checksums + bindings),
  `flock` guard. `RUN_MANIFEST.json` (plan, shard table, all bindings) is published exclusively and compared by every
  worker and the merger. Reason-coded events in `events/worker_N.jsonl`.
- Bindings on every shard: bundle SHA, authorization SHA, runner SHA, shard-layer SHA, environment-lock SHA plus live
  python/numpy/BLAS threads, plan SHA, shard-plan SHA, draws, master seed, run-manifest SHA, receipts SHA-256/size and
  every per-world receipt hash. Production additionally requires the validation authorization to carry
  `shard_layer_sha256` and `shard_worlds`, the measured-benchmark fields, and the FastMetric gate.
- Workers: `--assign static` (shard id mod workers; works across hosts, outputs are self-verifying files) or `dynamic`
  (rotated scan, `flock` arbitration). `merge` refuses unless `audit` is clean, writes `merged/` (per-cell receipts +
  `run_result.json` + `MERGE_MANIFEST.json`) in a staging dir and renames it atomically, never overwriting.

## Verification (non-inferential fixtures only)

Fixtures run the real `generate_world` / `apply_injection` / `world_receipt` / FastMetric / acceptance code with
arithmetic tokens instead of generators (one fixed pattern per seed tuple; refused worlds and BH rejections are
injected deterministically so every summary path is exercised).

- `tests/test_m32_shard_layer.py`: 33 passed; with `tests/test_m32_validation_bundle_v3.py` 38 passed (about 47 s).
- **Sequential equivalence:** the real `run_validation` on a 16-world, 5-cell fixture versus the sharded merge for
  shard sizes 3 and 1 (1 and 3 workers), a CLI two-worker dynamic run, a hard-exit crash and restart, and a SIGKILL and
  restart: every cell receipts file and `run_result.json` byte-identical. A deliberate wiring mutation (data and
  membership streams swapped) turns all six files different, so the comparison is sensitive.
- Also tested: exclusive create / no overwrite, binding mismatch, torn tail dropped but corrupt complete line fails
  closed, crash between finalise steps, duplicate worker lock, receipt tamper, swapped identity, missing / duplicate /
  overlapping / extra / temp files, run-manifest tamper, merge determinism across worker count and order, multi-host
  copy-and-merge, production CLI fail-closed, no generator built (constructors tripped), and a textual guard that the
  production evaluator's calls and seed positions match `run_cell`.
- Limit: checks are tamper-evident, not tamper-proof; an attacker who recomputes every hash passes them. The merge
  manifest records each completion hash so it can be stored externally. The production registry path (real generators)
  is unexercised by design; the drift guard and token-keyed fixtures cover its wiring.
- The four failures in the untracked `tests/test_m32_scheme_d_validation.py` are pre-existing (identical without this work).

## Measurements

Fixture: 64 worlds, 300 draws, shard size 4, all 384 hypotheses tested, this 4-vCPU VM (kernel and dashboard also
running). 1 / 2 / 4 workers: best wall 44.6 / 25.9 / 16.3 s; speedup 1.00 / 1.72 / 2.74; efficiency 1.00 / 0.86 /
0.68. Workers finish within 5% of each other (no imbalance). A bare-evaluator control (no layer) loses about 20% at
4 concurrent processes; tmpfs storage does not help (0.64), so the loss is compute contention on this host, with an
unattributed remainder. Layer cost 14.5 ms per world (fsync, validation, completion); audit 0.25 s and merge 0.3 s
per 64 worlds. Peak RSS 100.8 MiB per worker (real world generation, C4/N3 included); the phase-90 run peaked at 93 MiB.

Projection (`full_run_projection.json`): 906.5 CPU-s per world (phase-90 fast projection plus measured fixed cost and
layer overhead), 18,886 CPU-hours. Wall time by whole shards per worker, shard size 5 (15,000 shards):
16 / 32 / 64 / 128 / 256 workers = 49.2 / 24.6 / 12.3 / 6.2 / 3.1 days (quantisation loss at most 0.7%; shard
size 25 gives 49.3 / 24.7 / 12.3 / 6.3 / 3.1). At this host's 4-worker efficiency the same figures are about 72 / 36 /
18 / 9 / 4.5 days; dedicated cores should sit between the two. Per-core speed on target hardware is unmeasured.
Memory: about 0.1 GB per worker, 26 GB at 256 workers.
