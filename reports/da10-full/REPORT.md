# SOL1 — DA-10 complete offline SQLite/storage resolution

DA_10_STATUS: CLOSED_OFFLINE

All justified fixes in this bounded storage assignment are implemented and verified offline. No production integration, production writes/deletion, provider calls, Kernel/Dashboard starts, trading, threshold changes, SDD/tracker edits, new storage subsystem or new cache. Factory/lifecycle modules are unchanged. Remaining operational limits and the unproven historical disk attribution are explicit below.

## Restored lineage and boundaries

The verified data lineage is WM-03 `78e25833c8358e783006ebbbabbba47a9f93261c`, including DA-04, DA-05, DA-08, DA-09, WM-01 and the four integrated original DA-10 implementation commits (`41d0308f`, `3efd6d9d`, `2880d52a`, `dd4f5f5b`). `lineage.json` records ancestry checks. The original DA-10 history remains separately preserved at `verified-original-da10` (head `2e4e8b2838eabb6ad719d1e5c135898749a0f342`); input bundle SHA256 values were checked again.

DA-06's verified analyst optimization was reapplied as `cb15d7c5c97427cb2c399515662b161c88855a79`, with identical patch ID; its original history/evidence is retained at `verified-da06`. The original DA-10 and DA-06 refs are included in the self-contained bundle. Opus's separate lifecycle closure branch was inspected for ancestry/disk evidence only and was not merged, altered or deleted. New work remains independent of that branch.

`scope.json` verifies unchanged bytes for Kernel, configuration, SDD/control documents, admission allocation, DATA receipt/feed paths, DA-06, Risk, EntryAuthority and Executor, and unchanged Factory/strategy, Dashboard and tracker trees. Existing DATA projection suppression, exact receipt retry behavior, cache coverage and source-stability guards are preserved.

## Root causes and implemented fixes

* Journal `_tx` caught only Exception. KeyboardInterrupt, SystemExit and GeneratorExit could leave the SQLite writer transaction open. A rollback failure could mask the original error and leave the failed connection reusable. Journal now reuses the existing DATA `sqlite_tx.abort` rollback-or-discard helper, including BaseException cleanup, before publishing storage health. No retry, timeout or durability policy changed. Ordinary RuntimeError rollback remains a positive baseline control.
* Several short-lived SQLite scopes used the connection's commit/rollback context without closing the owned connection. SQLite connection/UDF cycles and retained Reader references delayed release of handles and deleted-file allocations. Explicit `contextlib.closing` now wraps owned scopes in Learning readers/delivery, portfolio readers/consumer, accounting queue/source reads, prospective execution, a numerical-ledger creation scope, backup restoration and copy-rehearsal failure paths. Existing transaction contexts remain in place; commits, rollback, immutable rows and coherent active read snapshots are preserved. Persistent thread-local Journal/DATA connections retain their existing ownership model.
* Portfolio `consume` took BEGIN IMMEDIATE before pure event evaluation, allocation, Risk replay and JSON construction, including duplicate-cut replay. Preparation now occurs before writer ownership. The exact previous state and checkpoint presence are checked again under the existing atomic writer transaction. If another writer changed them, the prepared result is discarded and the original serialized computation runs against current state. Concurrent identical cuts return the exact existing duplicate. This preserves the serialized result without a new stale-cut refusal or retry policy. Checkpoints, evaluation, blobs and state still commit or roll back together.
* Prospective execution INSERT OR IGNORE retries advanced sqlite_sequence and appended WAL even though the observation already existed. An existing immutable identity now returns without writer ownership. The conditional insert remains atomic for a concurrent retry race. Existing observations, keys, bodies, clocks and relative append order are retained; future internal sequence gaps from ignored retries disappear. New evidence still respects writer contention and the existing storage/STOP bounds.
* Learning snapshot recomputed canonical JSON solely to hash it. It now hashes the exact text already being stored. Roles, source IDs, clocks, SHA256 identities, validation and immutable conflict/refusal behavior remain unchanged.
* Backup capture/verify/restore materialized complete SQLite files in Python bytes. These paths now hash/copy with bounded 1 MiB blocks into the existing generation/restore staging mechanism. Restore performed the same store integrity/metadata scan three times; it now scans once. Size/hash checks, fsync, safe destination checks, secret exclusions, schema checks, recovery-state transitions, corruption refusal and atomic publication remain active.
* Copy-only snapshot preparation now refuses a known insufficient destination capacity before copying database/sidecar bytes. This uses actual logical byte requirements and available space, with no new configurable threshold. Existing source-stability checks still detect file changes. It is not a reservation against concurrent filesystem writers.

## SQLite contention and storage measurements

All fixtures were on the recovery filesystem, with synthetic SQLite databases and normal durability. No nearly full production-volume stress or production compaction occurred. Absolute times vary with host load; counts, exact bytes and lock ownership are the stronger evidence.

| Bounded workload | Baseline | New |
|---|---:|---:|
| Duplicate portfolio replay while another writer holds 120 ms | 138.04 ms | 8.60 ms |
| Maximum competing-writer wait during injected 40 ms pure portfolio preparation | 60.121 ms | 0.053 ms |
| 32 duplicate execution observations: WAL growth | 131,872 bytes | 0 |
| Same retry batch: open-handle growth with GC disabled | 65 | 0 |
| 32 ended retained-reader scopes: open-handle growth | 32 | 0 |
| Deleted private 8 MiB fixture still allocated through ended scopes | 8,462,336 bytes | 0 |
| Learning snapshot canonical encodes per call | 2 | 1 |
| Median bounded 2 MiB Learning snapshot call | 19.95 ms | 16.38 ms |
| 8 MiB backup capture peak Python allocation | 9,040,493 bytes | 2,134,374 bytes |
| Backup verify peak Python allocation | 9,018,463 bytes | 2,112,297 bytes |
| Backup restore peak Python allocation | 9,027,876 bytes | 2,164,755 bytes |

Tracemalloc is Python allocation, not whole-process RSS. The necessary source/generation/restore fixture image footprint remains approximately 29.1 MB on both versions; no reduction of required backup copies is claimed. No production bytes were reclaimed or deleted.

The WAL probe preserves a useful active read snapshot exactly on both versions. Sixteen new 64 KiB rows grow its WAL to 1,207,192 bytes; TRUNCATE correctly reports busy while that snapshot is active. After the scope ends, checkpoint succeeds and WAL becomes zero on both versions. This distinguishes legitimate snapshot retention from ended-scope handle leakage. Existing WAL mode, automatic checkpoint policy, synchronous settings and production busy timeouts were not tuned.

## Temporary zero-free-space event

`zero-space-evidence.json` retains the exact disk paragraph and SHA256 of the latest lifecycle closure report, plus this task's read-only measurements. That report describes the temporary zero-free event and a concurrent foreign gui01-baseline pytest, explicitly without proven attribution. The current read-only allocation survey measured 11,126,398,976 bytes under `/mnt/luffy-data/test-tmp`, 2,201,067,520 under `/mnt/luffy-data/tmp`, and 16,384 under `.Trash-1000`. These are current sizes, not event-time sizes. The warning-level journal search since October 15 returned no matching entries; that does not rule out ENOSPC.

**Verified:** the source volume was about 96% used with about 4.2 GiB available; large foreign fixture trees existed; the private tests reproduce ignored-insert WAL writes, delayed handle/deleted-file release and whole-file Python allocation. **Unproven:** which process, file, WAL, backup, compaction or fixture allocation exhausted free space during the reported event. No event-time allocation/open-file trace is retained. Concurrent fixture growth is a hypothesis, not a demonstrated cause. No foreign files were removed or modified.

The task's fixtures, worktrees and code graph ran on the recovery volume. A full unrelated numerical-research test attempt was interrupted after it exceeded the bounded window/fixture budget; it had allocated 1,487,761,408 bytes in its own basetemp and is not counted as a pass. Its temporary files were removed after its process exited. The actual numerical-store creation scope is instead tested in isolation for successful commit and failure closure, using the production scope's AST. This grants no end-to-end research certification. The largest observed whole task scratch usage was approximately 2.4 GiB; no production-volume stress was performed.

## Replay and regression evidence

* Matched storage/Learning matrix: **211 baseline passed, 211 candidate passed**, same exact test IDs, default SQLite durability and zero status regressions.
* New controls against the restored baseline: **2 passed / 28 failed**. New code: **30/30 passed**. Failures reproduce ended-scope leakage, duplicate sequence/WAL work, serialized JSON duplication, oversized file materialization, repeated integrity checks, interrupted/failed Journal rollback, unnecessary writer ownership, copy-capacity refusal and restore race handling. The two positive baseline controls are ordinary RuntimeError rollback and refusal of genuinely new evidence under another writer.
* Final storage-focused regression: **181 passed**, including all 30 controls, serialized-race parity, existing backup/corruption/mutant/recovery contracts, accounting, portfolio, immutable observation behavior and preserved DA-06 tests. Together the completed selections cover **267 unique passing cases**. `verify.py` computes the exact union without double counting.
* The matched 211-case run preceded the final immutable observation retry fast path; all affected execution-observation tests and the 181-case final suite passed after that leaf change. Verification is by affected source/consumer coverage, not a claim that the full repository suite ran.
* The existing hash-check mutant test was updated to recognize the streamed size/SHA predicate, with the same corruption mutant still detected. The revised test also passed on the original baseline. Raw pytest text/XML is retained verbatim, including formatting; source/test-code whitespace checks pass. Raw exploratory logs, including a corrected AST fixture selection and an intentionally interrupted fault test, remain labeled attempts; only the final selections above define status.
* All four measured portfolio bodies, complete backup asset sizes/SHA256 values, and source dependencies are identical before/after. The 77 actual historical DA-06 source blobs round-trip through baseline/new Learning capture and fresh readers with exact dependency identities, table bytes, rowids, schema and **complete SQLite file SHA256 parity**. This is source-byte/replay preservation, not a fresh trading-performance evaluation.
* Prior DA-10's 95-record portfolio replay evidence remains preserved in its original report/bundle (56 pass, 39 identical pre-existing refusals). This task did not rerun a production-sized historical database or rewrite refused history to manufacture a pass. New evidence is the bounded exact-source and portfolio replay described above.

## Remaining operational limitations

The recent zero-space writer remains unknown. Legitimate active SQLite snapshots can retain WAL; useful immutable evidence growth, required backup/restore staging and interrupted generations consume space. Capacity preflight cannot reserve space against other processes. No production deployment, checkpoint, compaction, data cleanup, retention change or quota policy was authorized or performed. Broad repository and full numerical-research certification remain outside these bounded checks. There is no further owner policy decision required for the implemented fixes.

## Implementation commits

* `cb15d7c5c97427cb2c399515662b161c88855a79` — verified DA-06 reapplication onto the latest data lineage.
* `f3ed34aa2fc99f7459308448651a67582e80c63b` — SQLite lifetime/failure cleanup, shorter portfolio writer ownership, write reduction, bounded backup persistence and private-copy capacity checks.
* `596f9f17c9da466aed0b28aba0da47f44f303671` — immutable observation retries without writer ownership.

The final evidence commit, verified self-contained bundle checksum, independent restore results and cleanup audit are recorded beside this report in DA10_FULL_RETURN.json and SHA256SUMS.json. All task-created working repositories, basetemps and verification clones are removed only after bundle verification; necessary reports, patch and bundle remain.
