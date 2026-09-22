# M3.2 shard layer hardening (D1–D3), September 22

Shard layer `scripts/m32_scheme_d_sharding_20260921/shard_layer.py` SHA-256
`68195fb6f8548722d3055a00e5892d9d5fdc1caaab0735b3f706e05bc1ab0657` (supersedes `2c72e369…`; all hashes in
`LAYER_MANIFEST.json`). Bundle-v3 `eab05d5d…dc01` unchanged. **No RNG constructed, no validation, no authorization
created, no change to partitioning, seed/world identity, FastMetric, protocol, bundle-v3 or validation semantics.**
Defects came from the 2026-09-21 independent verification.

## Fixes
- **D1 temp collisions.** Temp names are `<target>.tmp.<host>.<pid>.<time_ns>.<counter>`, created with `O_EXCL`; a name
  clash is retried with the next counter (never reopened, never truncated), fail-closed after 1000 clashes. The published
  name is a hard link to a fully written, fsynced inode, so a torn `RUN_MANIFEST.json` cannot be exposed. An unreadable
  manifest is now `run_manifest_unreadable` instead of a traceback.
- **D2 concurrent merge.** `merge.lock` (flock): a second merger exits `merge_locked_by_another_process` and touches
  nothing. Each merger stages in its own `merged.staging.<host>.<pid>.<ns>.<n>/`; only its own staging is ever removed
  (foreign / legacy `merged.partial*` are left alone and listed). Publication uses `renameat2(RENAME_NOREPLACE)` (fallback:
  existence check under the lock), so even an empty `merged/` is never replaced.
- **D3 durability.** Order: fsync every staged receipt and `run_result.json` -> fsync staging dir -> no-replace rename ->
  fsync `merged/` and its parent -> verify published files against the freshly computed hashes -> publish
  `MERGE_MANIFEST.json` (commit marker; exclusive, fsynced) -> fsync `merged/`. A crash between rename and marker leaves an
  unmarked `merged/`; the next merge re-verifies it byte-for-byte against the shards and only then writes the marker; a
  mismatch is refused with nothing modified.
- **Provenance (informational only).** Hostname, CPU model and flags, numpy CPU features, build and runtime OpenBLAS
  identity (core name, library SHA-256), python/numpy/threads and the environment-lock SHA-256 go to
  `provenance/prov_<sha16>.json`, `worker_started` events (every event now carries `host`) and
  `MERGE_MANIFEST.informational` with the merger's record and the number of distinct worker hardware identities.
  Provenance is never in a shard binding or completion; `MERGE_MANIFEST.deterministic_sha256` excludes it. Failure to
  collect it is an event, never an abort.

## Verification
- `tests/test_m32_shard_layer.py` 52 passed (33 original, one rewritten for the new staging rule, 19 new for D1/D2/D3 and
  provenance) with `tests/test_m32_validation_bundle_v3.py` 5: 57 passed. The D1 and D2 failures are reproduced by
  legacy-algorithm tests; each fix was mutation-checked (7 deliberate breakages, all caught).
- Independent verifier rerun (partition, seed tuples, 320-point crash/resume sweep, 16 multiprocess configurations,
  SIGKILL restart, rejection and binding matrix, production fail-closed paths, BLAS/SIMD variants): unchanged PASS.
  Former failures now: 150 concurrent `init_run` with identical PID/host/clock -> 150 ok (was 65 crashes + 1 torn read);
  6 concurrent mergers x 8 rounds -> exactly one publisher each time, others `merge_locked_by_another_process`, complete
  archive equal to the sequential output; mid-merge second merger -> A's staging untouched, archive complete (was 3 of 5
  cell files missing under a success return).

## Still open (not defects fixed here)
Tamper-evident, not tamper-proof (a full hash recompute passes); heterogeneous CPUs remain unmeasured (AVX512 hosts never
tested; provenance now makes a mixed run visible in the merge manifest but does not gate it); shared-filesystem
capabilities (hard links, cross-host flock, fsync semantics, NFS lost-reply EEXIST) are not probed; production path with a
valid authorization remains unexecuted by design.
