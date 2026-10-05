# LUFFY runtime bounded evidence latency R1

BOUNDED_EVIDENCE_LATENCY: PASS

DEPLOYED_SHA: `b880e52cf319451307261ee7728d1f0bfd7014c4`

Kernel and Dashboard stopped after a complete configured FROZEN observation. No recovery/reset/activation was performed.

| Phase | Cycle seconds | Actual successful-heartbeat gaps |
|---|---|---|
|Five normal|189.766, 188.082, 208.949, 198.160, 202.896|190.819, 189.125, 210.032, 199.166, 203.948|
|Three Dashboard|205.455, 195.531, 189.355|206.488, 196.556, 190.372|

Cold initialization 0.156s; boot 25.081s; startup warmup 228.702s. The first restarted heartbeat gap 1337.327s includes the intentional stopped interval and is not an ordinary-cycle gap.

Original oversized source: Attention `scans` rowid 24026, `scan_c771018d25de4ace80bfba0639db5a0f`, 16,226,290 bytes against 2,097,152. Repeated membership selection/ticker/volume receipts caused the payload. Lossless publication retains exact SHA `3e3d96a5c1b4bde723df1dc9cd0bdbfc1fb52fdf5e93a55be5f82778818f05e9` in a 16,685-byte reference packet. The original actual Portfolio refusal reproduces; the same retained source passes with exact bytes. Five-second source deadline unchanged.

Measured causes and fixes:

Repeated source/history serialization, per-chunk decoding/hash/SQLite/native scheduling crossings under configured background CPU competition: main evidence storage 127.018 wall/1.857 CPU seconds before diagnostic overlap. Retained 23,081,567-byte detail under two bounded offline CPU competitors: original cold 16.094/repeat 10.705 seconds, fixed 2.478/.182, exact equality.

Repeated multi-year reference receipt validation/serialization: original 128 rows 61.284 wall/61.077 CPU, fixed 2.728/2.716; full observed 3,232 rows fixed 6.105/6.040, exact retained rows/revisions.

Unindexed vote outcome join over 5,391,377 votes: 35.206 wall/34.814 CPU seconds; exact indexed results .090 CPU (cold offline 12.182 wall). Latest-32 decisions sort over 771,791 rows: 1.323/1.093 to .000331/.000330.

Too many database crossings inside receipt transaction: 3,232-row original 74.165 wall/1.001 CPU, bounded statements 3.612/.157 with identical rowids/ancestry. Derivative DELETE writer owned by derivs-recorder 14.704 wall/1.711 CPU, reader locked; WAL preserves committed reader snapshots.

Attention pipe scheduling wait: 802 writes transferred only 3,284,992 bytes, 8.199 seconds in writes, ten-second worker timed out. Same child/JSON through private seekable input descriptor 2.624 wall/.192 CPU under same offline contention.

Portfolio ledger repeated full previous/current contexts: checkpoint 102.346 wall/85.083 CPU; 1,282,465,792 allocated bytes in four cycles. Retained exact evaluation processing 85.090/62.126 to 29.544/26.661; installed lossless packets retain full logical content.

Bounded compression/decompression scheduling: 512 actual retained chunks, 33,801,315 expanded bytes, old compression 8.562/.449 to 1.403/.393; old decode 14.109/.176 to .277/.071; all 3,404 retained blobs byte/hash/length compatible.

Source SQL row-by-row crossings exhausted unchanged five-second deadline under contention (cold 7.655 seconds). Bounded cases/latest-updates SQL aggregates: cold 2.403 seconds, all 32 cases/updates exact, inventory SHA ff3fc91366500794933b694f7443808a7a8756de5c572f29d1a66b5fb7817544.

Attention initialization under actual held Portfolio snapshot: old auto_vacuum PRAGMA SQLITE_BUSY after .053668 wall/.000670 CPU. Existing-store WAL producer 1.224 seconds, transaction .483; exact seven-table identity/content proofs.

Completion prune committed before new complete marker: real large producer/Portfolio reader originally sees scan_id=None. Atomic completion/prune reproduces previous-source-before/new-source-after without changing headroom. Original assertion fails 4.26 seconds; fixed integration passes 5.64 seconds.

Mean main-thread exclusive stages (no nested/parallel double count):

|Stage|Wall seconds|CPU seconds|
|---|---:|---:|
|network.wait|25.625|0.233|
|market.load|18.106|3.155|
|evidence.store|13.262|3.185|
|trader.learning.capture.snapshot|12.847|0.288|
|market.eligible_frame|12.221|8.294|
|portfolio.event_evaluate|11.829|11.157|
|trader.portfolio.current.checkpoint|11.287|10.000|
|trader.portfolio.current.freeze|10.295|9.208|
|portfolio.serialize|10.245|9.947|
|kernel.cycle|9.307|2.249|
|evidence.hash|6.251|0.230|
|portfolio.evaluate_store|5.809|5.426|
|Journal.query|4.571|0.109|
|evidence.decode|4.098|2.038|
|Universe.membership_receipts|3.605|1.648|

All eight ordinary cycles: 149,630 chunk resolutions expand 9,889,030,078 bytes including repeated shared references; 86,182 actual chunk decodes total 5,700,073,278 bytes. Market details decoded: 37,424. Per-cycle counts and offline retained metadata candidate counts are linked below. Candidate counts are SQL index-prefix/rank inputs, not physical page visits.

Final derivative receipt transaction: derivs-recorder, 17.659 wall/1.265 CPU seconds, .561 commit/cleanup. This is an ownership upper bound; no exact OS lock-wait duration is inferred. WAL readers completed without lock errors.

Each cycle completed 18/18 candidates and snapshots with zero skips. Exit detection, both retained position management and heartbeat servicing completed with zero failures. Before/after signed snapshots show two matched protective stops and no ordinary orders.

Storage before startup → graceful shutdown: all stores/logs +78,336,000 allocated bytes; non-WAL +78,217,216; WAL +118,784. Subsequent read-only owner proof created 32,768 bytes of SHM; final inclusive growth +78,368,768. Portfolio file allocation stayed flat by using existing freelist; the nine evaluation roots share 33,324,389 compressed bytes. Whole-task production allocation growth, including every rejected window and old inflated records, is 2,995,052,544 bytes. No history or backup was deleted. External evidence/preservation task-directory allocation is 14,225,698,816 bytes; isolated workspace fixtures/code/graph allocation 5,049,094,144 bytes, reported separately from active stores.

Active WAL peak allocations: journal 178,151,424 bytes; Attention 21,319,680; derivatives 27,836,416. Frame/generation/checkpoint metadata and each boundary are retained separately; net allocation is not a total-writes estimate.

863 targeted regressions passed. Installed SQL owner view resolves the last cut to 431,301,143 bytes and SHA d4da7de7de93a4dc4e5af318617c125dbdb0ef9183bace3e50b0b5a1047c2277; exact normal Consumer and Portfolio/Risk replay PASS. No original required context is truncated or replaced. Missing/corrupt/wrong-cut evidence refuses. Disabled provider regression passes; final admissions/attempts zero and usage ledger unchanged.

Persisted active safety conditions: none. FROZEN, operator hold, recovery latch and watchdog.off retained. Research insufficiency, replay-complete learning-authority refusals and Attention advisory capture-budget overruns remain explicit; no coverage backfill/paid provider or ACTIVE transition. Kernel PID: none. Dashboard PID: none. F8: FROZEN runtime verified; ACTIVE/real-money handoff pending. READY_FOR_FROZEN_OBSERVATION: YES (five plus three completed). BLOCKERS: none for this task.

Diagnostic disclosures:

All workers enabled in final window. No worker-disabled run claimed as proof.

Initial missing multiprocessing spawn guard launched a second FROZEN Kernel; both stopped. Overlapped 953.950/611.612-second cycles excluded; pre-overlap attribution retained.

Other prior windows stopped after measured source-deadline/lock/pipe/storage/native-scheduling/source-availability blockers. One earlier cycle overlapped an offline CPU test; excluded. No cycle from an earlier window substituted into final consecutive proof.

Initial Dashboard localhost probe did not match LAN binding. A later unauthenticated LAN probe caused an erroneous stop after five normal plus one Dashboard cycle; corrected authenticated fresh window, original result retained.

Fast source metadata originally suppressed a .025591-second no-source read and carried a previous diagnostic flag. That window excluded; every source load now emits metadata and controller resets flag at cycle begin. Actual atomic-prune fault reproduced and fixed before final window.

Eight final measured cycles plus separate startup warmup all fresh verified sources, no stage failures/source refusals/worker errors/timeouts, zero provider admissions; only this final complete configured window is acceptance evidence.

Final read-only exact evaluation reader 5.456 seconds and owner inspection 14.416 seconds concern retained ledger replay, not the Portfolio source loader deadline. Actual source loading was 1.004–2.057 seconds within unchanged five-second boundary.

Evidence (metadata/proofs; no full source payload dump):

- [final-report.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/final-report.json)
- [atomic-window-measurements.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/atomic-window-measurements.json)
- [read-candidate-counts.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/read-candidate-counts.json)
- [installed-consumer-final-proof.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/installed-consumer-final-proof.json)
- [retained-source-reproduction.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/retained-source-reproduction.json)
- [atomic-completion-before.txt](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/atomic-completion-before.txt)
- [atomic-completion-integration.txt](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/atomic-completion-integration.txt)
- [native-regressions.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/native-regressions.json)
- [account-before-atomic-window.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/account-before-atomic-window.json)
- [account-after-atomic-window.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/account-after-atomic-window.json)
- [external-artifact-storage.json](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/external-artifact-storage.json)
- [Raw stage completion/failure metadata](/mnt/luffy-recovery/recovery/runtime-bounded-evidence-latency-r1/evidence/observation-atomic-window/stages.jsonl)
- [Repository change report](/mnt/luffy-data/luffy/production/docs/superpowers/reports/2026-10-05-runtime-bounded-evidence-latency-r1.md)
