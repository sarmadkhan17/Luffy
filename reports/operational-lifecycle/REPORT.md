# SOL1 unified operational data lifecycle

Integration package on `sol1/operational-data-lifecycle`, based on D6 `f10f974`.
No deployment, restart, provider request, trading, CI change, production row deletion,
checkpoint or production VACUUM was performed. Kernel/Dashboard source was not edited.

The owner authorized using committed D6/D7 handoffs. D6 owns
`trader/data/market_provenance.py`; this branch includes its complete committed timing
contract before adding physical sharing. D7 `e6a4037` explicitly left that file
unchanged. Its pending vector reconciliation remains D6/D7 work, outside this package.
Other lifecycle changes do not touch D6/D7 strategy, execution or feature files.
The repository-requested Claude execution probe returned `Not logged in`; Codex
performed this implementation. No subagents were used.

## Rules and last consumers

`retention-rules.json` assigns all **146 already inventoried tables** to existing
owners and explicit actions. This is derived from the supplied catalog, not a new
inventory. The original catalog, measured report and writer/reader ownership CSV
are preserved under `supplied-evidence/`, with original SHA256 digests alongside.

| Family | Last required consumer and retention rule | Enforcement |
|---|---|---|
| Historical candles, reference projections, derivatives, floors | Features, historical evaluation, replay and coverage require retained history. No time expiration or row deletion. | Existing market owners; unchanged history. |
| Market receipts/revisions | Exact as-of, first-eligible replay, retrospective and lineage readers require each distinct receipt, acquisition/request identity, clocks, supersedes and values. Keep every receipt. | Append automatically shares source strings >=256 UTF-8 bytes in the existing `market_raw_sources`; all logical readers reconstruct and verify hashes. Existing exact raw strings can be reused. Unique raw bodies stay inline. |
| Shared market source bytes | Every referencing receipt and reproducible snapshot remains a consumer. No raw-source garbage collection is authorized by this package; uncertain sources remain. | Content hash checked on write/reuse/read. Missing/corrupt bytes refuse reads. Frozen predictive snapshots materialize exact logical receipts, avoiding dangling private references. |
| Completed R&D | Keep successful/failed results, hypothesis/combo identity, parts, outcome/reason, ablation, windows, controls, slices, provenance, candidates and complete statistical look/budget sequence. | Existing `Ledger._slim` remains the normal write rule. Old `portfolio.order` is removable only under the exact eligibility checks below. No evaluated identity is deleted. |
| Active/unfinished R&D and reproducible datasets | Resume, registered experiment fingerprints and reproduction remain consumers. Preserve all. No filesystem dataset or working-copy deletion has a proven eligible candidate in this assignment. | No filesystem purger. Dormant `.prepare-work-*` is outside the 146-table operational catalog and remains untouched. No inferred completion from age, filename or lack of measured growth. |
| Learning captures, registrations, source blobs, produced chains and actions | Exact historical replay and immutable source identities remain consumers. Preserve them. The supplied review found distinct capture/chain stages, not interchangeable copies. | Existing immutable producer tables and source hashes; no speculative deletion of these representations. |
| Portfolio | Keep current restart state and every immutable checkpoint/evaluation and proposal/risk outcome. | New evaluation writes anchor exact `current`/`previous` subtrees through the existing journal codec, sharing the same chunks as restart state. Old unique/or uncertain orphan chunks remain. Existing DA-10 copy-only GC still requires complete root closure plus an exact surviving-evaluation reconstruction witness. |
| Attention scans and source delivery | Existing short-lived source deliveries are retained according to their owner's scan/version reference rules. | Existing `Store._prune`, configured 7 days / 512 scans / 64 MiB main-file ceiling; no new scan deletion rule or timer. This package changes no original observation identities. |
| Investigations and typed memory | Existing owner retains active cases and case inputs; terminal-case retention is 30 days and typed outcomes use the existing 90-day policy and bounded store. | Existing investigation/memory/outcome mechanisms are unchanged. Their retained memory context exports include exact raw source archives; retention/replay tests exercise the last-consumer boundary. This package grants no additional historical purge eligibility here; it does not certify every existing TTL deletion against arbitrary external consumers. |
| Forecast Learning | Existing pending episodes and source captures remain the owner's responsibility; terminal episodes use the existing 30-day protocol. | Existing learning worker unchanged. No new outcome purge authorization. |
| Financial/trading/execution/accounting/owner audit/control/recovery | Open positions, historical accounting, unresolved controls and restart consumers require these records. Preserve every record, including auxiliary accounting queues and public/execution evidence. | No cleanup statement targets these tables or files. Unknown cleanup-trigger side effects cause local refusal. |
| Incidents and file evidence | Active/unresolved incidents, referenced evidence and recovery dependencies have no established deletion proof. | Retain. No incident-file purger. |
| Monitoring logs | Existing seven-day monitoring retention. | Untouched; no logging reimplementation. |
| Unrecognized schema, producer, consumer or dependency | Eligibility is unknown. | Retain affected records/store; independently proven market/R&D cleanup can continue. |

This package deliberately authorizes a limited set of proven representation
reductions. It does **not** claim that dormant research files, all old outcomes or
all unreferenced blobs have become safe to delete. Existing age-based owners are
not upgraded to a universal cross-store reference guarantee by these changes.

## Exact old-result eligibility

Only `research_combos.result.portfolio.order`, already omitted by the current
writer, may be dropped. Require a JSON object with a portfolio object and matching
combo hash; no reproducible `evidence`; no `research_tests.hash`, candidate hash or
twin reference; no unfinished batch for its timeframe/geometry; and a finished
matching batch at/after its creation. Unknown/corrupt rows refuse individually.
Every other result field, status, identity, rowid and statistical expenditure stays.
Archives, budgets, tests, candidates, registrations and research evidence are never
rewritten. Unknown triggers refuse this family.

After separate owner approval, existing `state_kv` key
`lifecycle.research.backfill.approved.v1=1` permits the existing batch-completion
owner to compact at most 32 old result rows per completion transaction. Its
`lifecycle.research.rowid.v1` cursor commits with the result changes and wraps after
reaching the end, so previously active/refused rows can be reconsidered later.
The approval key is **absent/default off** and was not set in operational storage.
Future normal result writes already compact automatically without that key.

## Automatic mechanism and bounded historical rehearsal

No new database, table, retention service, timer, framework or logging mechanism.
Market sharing runs in the existing bounded 128-record append transaction;
immutable source lookups are batched. Portfolio sharing runs in its existing
atomic checkpoint transaction. Failed transactions roll back both source content
and references. All source content is retained, including after failed/uncertain
eligibility, so no physical reference is collected prematurely.

The existing cleanup module now also supports bounded market/R&D rehearsal through
`apply_lifecycle_copy`. It requires the existing DA-10 recognized-copy receipt,
rejects source/hardlink targets, recomputes the exact plan under `BEGIN IMMEDIATE`,
applies at most 128 rows and commits atomically. It performs no VACUUM/checkpoint.
The CLI exposes this only for identified copies:

```bash
./venv/bin/python scripts/storage_cleanup.py \
  --isolated-copies /isolated/snapshot/copies --database candles.db \
  --lifecycle-batch market --after 0 --batch-size 128 \
  --report /isolated/reports/batch.json
```

Advance using `next_rowid`. Research uses `--database luffy.db --lifecycle-batch
research`. Neither command applies a historical market backfill to production.
A future production application requires its own owner-approved bounded manifest,
using these same checks under the applying transaction. No such action occurred.
Old-version rollback requires materializing reference-bearing market receipts with
`read_record`/`encode` using this version before returning to older readers. Shared
source rows remain available; no destructive schema migration prevents recovery.

## Actual-storage dry-run and approval records

```bash
./venv/bin/python -B scripts/storage_cleanup.py \
  --operational-dry-run /mnt/luffy-data/luffy/production/data \
  --windows 8 --batch-size 128 \
  --report reports/operational-lifecycle/operational-dry-run.json
```

The CLI reexecutes inside Bubblewrap with `/` read-only, only a dedicated report
directory writable, and network isolated. Each resolved store must have a verified
read-only filesystem; absent support refuses execution. SQLite uses `mode=ro`,
`query_only`, in-memory temporary storage and the actual live WAL, not unsafe
`immutable=1`. Per-store short snapshots and query deadlines allow independent
refusal without blocking other safe inspection. The result is not an atomic
cross-database snapshot.

`operational-dry-run.json:approval_records` lists exact database/table, rowid,
revision ID, before/after SHA256 and payload estimates for **1,024 sampled candle
receipts**. These are representation rewrites, not receipt deletions. No sampled
research/derivative record was eligible. Approval of this sample does not authorize
unsampled records; each later bounded range requires a fresh manifest and current
eligibility. Source/raw rows, unique orphan chunks, temporary datasets and unresolved
evidence are not on the approval list. No first production purge is authorized.

**Audit exception:** an initial `mode=ro`-only attempt was inadequate. SQLite opened
WAL sidecars writable and reinitialized `attention.db-shm` (truncate/page writes).
No database record was changed/deleted. This is recorded in `initial-ro-audit.strace`
and was reported to the owner. The corrected protected run has zero successful
operational write opens, write syscalls or writable shared mappings; see
`protected-dry-run-syscalls.txt`. The earlier attempt must not be described as a
strict no-write run.

## Measurements and practical limits

The dry-run estimates approximately **1.06 decimal GB of candle receipt payload
reduction**, from eight stratified 128-row windows over the existing rowid range.
It preserves all receipt identities. It is not a full-table exact byte count:
rowid gaps, workload changes, index/B-tree overhead and fragmentation limit the
extrapolation. Unique derivative raw-body references were rejected as a design
choice because the first draft would have increased storage. Sampled derivatives
and completed R&D now have zero new eligible reduction.

`sample-rehearsal.json` measures a private sample of 2,048 actual receipts:
2,184,076 net payload bytes removed, 2,010 retained-window as-of selections verified, byte-exact
logical parity, idempotent repetition, and SQLite integrity `ok`. Before and after
files were normalized only on that private sample: 8,564,736 -> 2,928,640 bytes,
or **5,636,096 physical bytes saved**. Applying the row rewrite without offline
compaction saved **zero filesystem bytes**, with zero whole freelist pages; freed
space can be inside retained pages. Do not multiply this small-sample physical
ratio across production stores. Production physical savings remain **zero**.

Three actual retained Portfolio checkpoints use 2,201,933 compressed blob bytes
under existing segmentation versus 1,148,948 when anchored, a **1,052,985-byte / 47.8%**
reduction for this sample. Full logical JSON is identical. This is a prospective
compression comparison, not production reclamation or a universal growth estimate.

Using the supplied measured 8,915 candle receipts / 796.648 seconds and the sampled
~1,058-byte per-receipt reduction gives approximately **1.02 GB/day less retained
payload growth** at that workload. Physical allocation growth is not equal to
payload growth, so this is not subtracted directly from the prior 4.00 GB/day
filesystem forecast. Future ACTIVE workload and acquisition rates remain unknown.
Existing reusable pages and occupied page bytes are recorded separately in the
dry-run. No whole-host free-space delta is presented as causal storage savings.

## Verification and integration

See test logs and `verification.json` for exact commands/results. Tests cover
receipt clocks/IDs and supersedes; as-of/retrospective/lineage replay; missing or
corrupt shared sources; frozen predictive fingerprint equality; rollback;
restart/checkpoint behavior; failed research identities and spent looks; local
unknown-row/schema/trigger refusal; no financial-trigger side effects; bounded
copy enforcement; strict read-only dry-run and unchanged source-file hashes.

The broad predictive fixture suite was stopped after two passing cases because its
repeated large synthetic population setup is unnecessary for this storage change;
its interruption is retained, not presented as a pass. Dedicated predictive
fingerprint and production-sample replay checks cover the changed boundary.
Two legacy migration tests passed in the earlier 102-test run. An intermediate
repeat failed their fixed 10 GiB capacity prerequisite. Available space later met
that prerequisite, and the final separate retry passed both tests. No manual disk
cleanup or capacity-check weakening was performed to make them pass.

The branch is an implementation candidate with conservative refusal boundaries.
Review/merge code and coordinated readers, obtain approval for any first historical
production batch, and deploy through the owner's existing process in a later task.
No runtime action is requested or performed by this delivery.

Generated Graphify files were updated locally using AST-only extraction; the
update log is included. Regenerate that graph after integration rather than
merging a large unrelated historical graph rewrite.
