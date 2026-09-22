# M3.2 host / filesystem qualification (RNG-free), September 22

Tool `scripts/m32_scheme_d_qualification_20260922/qualify.py` (SHA-256 `dabeaeb7…c0458`; all hashes in
`QUALIFICATION_MANIFEST.json`). Bound, fail-closed, to bundle-v3 `eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01`
and shard layer `68195fb6f8548722d3055a00e5892d9d5fdc1caaab0735b3f706e05bc1ab0657` (the layer SHA quoted in the task was
not a valid 64-hex value; its prefix and suffix match this hash, which is also the one in `LAYER_MANIFEST.json`).
**No RNG constructed, no validation worlds, no owner authorization read or created, no bundle/protocol/layer change.**

## Checks per host (`host --dir <target mount>`)
bindings + bundle verification; environment gate (python/numpy/`OPENBLAS_NUM_THREADS`); environment-lock hash (bundle-pinned
file) and installed-distribution set; CPU model/features; NumPy + build/runtime OpenBLAS identity; filesystem policy
(tmpfs/ramfs and known-bad network/FUSE types fail closed); hard link + `EEXIST` (incl. 8-process link race); `flock`
exclusivity across processes and release after SIGKILL; `renameat2(RENAME_NOREPLACE)` (raw + the layer's primitive; a
fallback is only a conditional pass); file + directory fsync; atomic publish (6 racing publishers, concurrent readers, once
with identical host/PID/clock; directory publish seen whole or not at all); fixture canary (2 dynamic workers + merge through
the layer CLI on the target mount, compared with the sequential runner and hashed); arithmetic numeric canary (BLAS, LAPACK,
SIMD libm, reductions, sorts); FastMetric bit identity vs the reference statistic on a production-size token world; median
1-worker throughput across 7 fresh processes; worker-scaling ladder giving the qualified worker count.
`flock-peer` runs the two-host lock protocol (each host probes as a prober; same-kernel peers are `SAME_HOST_PEER`, never
cross-host evidence). `combine` verifies receipts (hash, optional HMAC, bindings, tool SHA, RNG-free attestation, no quick
receipts) and reports compatibility classes, filesystem capability, qualified workers and readiness.
Receipts are SHA-256 hash-bound; HMAC signing is available (`--hmac-key-file`) but no key is provisioned, so these are
hash-bound only. Network mounts and rename fallbacks yield `INCOMPLETE` without cross-host flock evidence.

## Result: `sarmad-VMware-Virtual-Platform` (this development VM), target `/home/sarmad/trader/data` (ext4, `/dev/sda2`)
PASS on every check; cross-host flock NOT_EXERCISED (no peer). AMD Ryzen 5 5600H (AVX2, no AVX512), NumPy 2.5.2,
scipy-openblas 0.3.34.0 core Haswell, lock hash `4d9c4fcc…742a`. Fixture canary `cac435d8…` equals the sequential runner;
numeric canary `2ad88361…`; FastMetric bitwise identical to the reference on all 384 hypotheses x 2 vectors.
Throughput (median of 7 processes, 300 draws): 709 draws/s per worker (spread x1.15), speed ratio 1.02 vs the reference
calibration, calibrated full-run 18,548 core-hours (recorded phase-90: 18,880). A second independent run gave 707 draws/s,
18,585 core-hours, identical canaries and the same worker count. Scaling efficiency 1.02 / 0.96 / 0.77 at 1 / 2 / 4 workers;
qualified workers 4 (rule: largest ladder size with efficiency >= 0.60, capped by CPUs and by memory). The trading kernel
and dashboard share this VM, so 4 workers means no headroom for them.
Projection at 4 workers: about 252 days for the full run.

## Caveats found while building
- Per-process FastMetric speed varies about 1.5x on this VM (465-765 draws/s across fresh processes, no code difference), so
  single-process timings are unreliable; the tool and its calibration use the median across fresh processes.
- The token-world proxy is slower per draw than the real phase-90 world and depends on the draw count, so it is only a ratio
  and is used only at the calibration draw count.
- fsync probes prove the calls succeed, not power-loss durability.

## Verdict
Single-host qualified; **execution hosts are not ready for a real authorization**: only one host is evidenced, no cross-host
flock or cross-CPU canary comparison exists, and this VM cannot finish the run in a useful time. Outside this tool: the owner
authorization (with layer SHA and `shard_worlds`) and the owner-authorized measured benchmark receipt.
