# Stage1 recovery and monitoring foundation R1

Package: `LUFFY-STAGE1-RECOVERY-MONITORING-FOUNDATION-R1`.
Base: `fcfe9e0359874b18d65a0b1e1ccfd156d18115e1`.
Branch: `luffy-stage1-recovery-monitoring-foundation-r1`.
Scope: MI-6 and MI-7 only; uncommitted and undeployed. No LUFFY process,
service restart, venue request, production-store test, or real order submission.
Independent review remains required; Stage1 build_complete remains false.

## Critical inventory and replay boundary

`trader/persistence/backup.py:critical_inventory` builds a typed allowlist rather
than a repository archive. SDD sections 24–28 require operational truth,
version/approval authority and retained replay evidence.

| Logical asset | Retained authority / replay evidence |
| --- | --- |
| `data/luffy.db` | `state_kv`, control events, Risk baseline, execution requests/recovery, trades/bookings/protection, immutable StrategyVersions/specs/install/owner approvals, instrument/account receipts, research questions/plans/Bank/runs/evidence, captured World/learning registrations/dependencies/revisions/actions/receipts |
| `data/candles.db`, `data/derivs.db` | Retained market/reference/derivative receipts and revision/availability evidence, required for historical replay; these are not expendable caches |
| `data/attention.db`, `data/declared-population/attention.db` | Retained observations, membership declarations, immutable capture identities |
| `data/attention_learning.db`, `data/investigation.db` | Observational learning and continuing research evidence |
| `data/runtime-portfolio.db` | Portfolio cuts and intent/Risk decision evidence |
| `data/execution-evidence.db`, `data/stage5-public/public.db` | Execution/public evidence where installed |
| `data/accounting-worker/queue.db` plus its retained JSON receipts | Durable accounting job/attempt identities and detached audit/replay receipts; every snapshot queue reference must be included |
| `SDD.md`, `STATE.yaml`, `NEXT.yaml`, `config.yaml` | Required architecture/control-plane/versioned runtime configuration bytes |
| `org.yaml`, `data/doctrine.json`, `data/agent_weights.json`, `data/stage5-activation.json` | Present component configuration, frozen doctrine, weights and owner activation window |
| Present safety/attention/learning/investigation health files, safety incident receipts | Retained safety and capture audit evidence; they do not establish current post-restore health |

Installed optional stores become mandatory assets of that generation. Explicit
`Asset` registrations extend the inventory for detached experiment/artifact
identities; omission of a registered asset refuses capture/restore. Configured
research candle/derivative paths within the root are included. A configured
store outside that root refuses normal capture and requires explicit inventory
root/migration work; it cannot silently disappear. Frozen numerical artifacts
are registered individually if replay depends on them. Code is identified by
`repository_version` (the reviewed commit plus any relevant uncommitted build
identity), not by copying the entire repository. The manifest also binds exact
configuration and artifact bytes; version identities must not imply unavailable
historical external data was retained.

No `.env`, credentials, session material, socket, graphify cache, node_modules,
build output or ordinary cache is selected. Registered forbidden paths and
symlinks are refused. Configuration containing nonempty credential/secret fields
is refused. Operational stores must continue to contain no credentials; this
package does not ingest secret files or migrate the secret architecture.

## Backup and restore contract

`Inventory`, `Asset` and `BackupManifest` are versioned typed contracts. Each
manifest identifies backup ID, capture times, source repository/version/root,
logical roles/paths/kinds, sizes, SHA-256 hashes, SQLite schema catalog hash,
user/application versions, capture engine version and completion. `COMPLETE`
binds the exact canonical manifest SHA-256. Duplicate keys/assets, missing or
unexpected assets, unsafe paths, unsupported schemas and inconsistent metadata
refuse verification. Compatibility uses the trusted inventory/registered store
schema, never a schema declaration trusted solely because the backup contains it.
That compatibility registry also supports verification after loss of source data.

SQLite online `Connection.backup` includes committed WAL data in a consistent
transactional snapshot. Snapshot output uses DELETE journaling and passes
`integrity_check`; no WAL/shm file is copied or required to restore it. Files
are read and hashed as exact bytes, checked around each read and again across
the capture interval. Capture times explicitly describe per-store transactions
and stable file intervals, **NOT_GLOBAL_ATOMIC**. Concurrent changes or new
accounting references outside the captured inventory cause refusal.

Capture uses a uniquely named `.incomplete` generation, captures and verifies
all assets, writes and verifies the manifest and completion marker, fsyncs
files/directories, then publishes with Linux `renameat2(RENAME_NOREPLACE)`.
An interrupted `.incomplete` directory is never a valid normal restore source,
even if its marker was written before interruption. There is no old-backup deletion.

Destination must be explicitly supplied and outside the source/runtime tree.
The implementation supports an external mount but records
`physical_offhost_NOT_VERIFIED`. No upload, network/cloud dependency or paid
storage is introduced.

Restore accepts only an absent, offline target outside the source tree. It
verifies the generation, stages and checks every asset, then uses the existing
ControlStateMachine to move captured ACTIVE through FROZEN to RECOVERY. Existing
FROZEN/HALTED/RECOVERY holds remain contained. Staging reuses Journal transaction
methods without constructor schema migration/backfill. Old Supervisor ownership
is invalidated and a durable `needs_owner` record requires normal owner/Supervisor
recovery, venue truth, reconciliation, protection verification and fresh Risk.
The operational snapshot's preserved evidence is unchanged; control/audit
changes are recorded with post-restore hashes.

Publication atomically renames the entire staged root without overwriting even
an empty target that appears in a race. Unsupported atomic publication is
refused. Replacement of an existing live root or multiple live paths is refused.
No production restore command is provided or executed. Refusal and verified
publication receipts are durably stored separately under the destination.
The target contains a truthful VERIFIED_STAGED receipt prepared before rename;
the separate PUBLISHED receipt is written only after successful publication.
Unverified candidate identity is labelled as such on refusal receipts.

## Storage and monitoring boundary

Journal read (`query`, `kv_get`) and write/commit (`_tx`) storage errors publish
independent safety incidents. Integrity/business-validation errors are not
mislabelled as storage outages. The pre-entry availability check performs a
real read and committed write; a readable but query-only Journal fails closed.
The exact Risk permission, durable reservation, canonical identity and control
fence remain authoritative. A final artifact check also runs inside existing
Risk validation without introducing a nested storage probe/commit.

`SafetyHealth` publishes an atomic typed artifact beside the Journal, independent
of Journal writes, plus transition receipts. A notifier/logging sink receives
first detection and resolution; its failure cannot suppress the artifact. If
artifact writes also fail, independent logging/stderr still exposes the fault
and the Journal object's local failure flag blocks entries. The independent
`scripts.monitor --safety-only` path never constructs a Journal, contacts a
venue or starts/restarts anything. The existing watchdog calls it before its
existing launcher checks; no launcher action was executed for this package.

Additional required operational stores are registered through
`safety_monitor.critical_stores: [{path: ..., role: ...}]` in the existing runtime
configuration. Both the kernel entry checks and independent observer probe these
stores. The Journal is always required. Stores used only for optional shadow
telemetry do not acquire trading authority simply because they are backed up.

One existing Heartbeat producer now publishes schema, producer, boot instance,
sequence, start/publication times and successful cycle context atomically.
Kernel publishes it after the successful trading-cycle bookkeeping; exceptions
do not restamp an old success. `SafetyObserver` independently validates identity,
future/malformed records, sequence/time regression, missing/stale records and
producer restarts. It never writes a producer's heartbeat or restamps freshness.
A new instance starts its own sequence and requires recovery. The existing
kernel policy is `scan_interval_seconds * 4`; there is no newly invented safety
threshold. Without that configured basis, the typed seam reports
POLICY_NOT_CONFIGURED and blocks entries; tests inject controlled values.
The legacy watchdog restart threshold remains a separate launcher setting.

One incident identity is retained per unresolved condition. Repeated observations
update the same incident; resolution produces the same incident's durable
receipt; a later outage gets a new identity. There is no repeat interval or
arbitrary missed-count policy. Recovery remains latched after storage/heartbeat
freshness returns. Only the existing Supervisor's fresh full recovery proof can
clear the latch; generation comparison prevents clearing a newer observation.
FROZEN/RECOVERY still manage exits; a storage entry block does not introduce a
HALTED transition or venue action. When persistence cannot commit, no durable
control transition is fabricated. Protection/reconciliation actions keep their
existing evidence requirements; no blind venue action is invented for a DB error.

The existing typed OwnerService `health` query exposes the independent safety
artifact even when Supervisor Journal status is unreadable. No frontend redesign.

## Validation and real-world classification

Evidence: `docs/superpowers/evidence/stage1-recovery-monitoring-foundation-r1/`.
The acceptance fixture rejects network connects and SQLite paths outside its
own temporary root. It uses controlled clocks, real temporary SQLite WAL stores,
fake venues/alerts and interruption callbacks. Six in-memory mutants execute
altered function paths and must fail their specific acceptance assertion:
ignored hash, incomplete acceptance, ACTIVE restore, write-failure entry,
stale-as-fresh, and Journal-only alert publication.

Policy not configured: actual destination/mount, backup cadence, retention,
restore-drill cadence and real notification routing. The heartbeat limit has
an existing configured basis; no missing production heartbeat threshold is
invented or claimed when the configuration omits that basis.

Needs real runtime evidence: physically off-host operation, production backup
history, restore drill, kernel heartbeat/restart chronology, alert delivery and
post-restore venue/reconciliation/protection/Risk recovery. These are not
package-local missing implementation. Independent review is the next package,
not pre-start readiness.

Final validation results:

- Package acceptance: **54 passed in 9.04s** (including all six mutants).
- Acceptance plus Supervisor after activation-health guard: **103 passed in 15.03s** (52 package cases at that checkpoint plus 51 Supervisor cases).
- Control/entry/recovery regressions: **154 passed in 41.98s**.
- Kernel boot/owner recovery/concurrency: **121 passed in 21.60s** after the final Supervisor guard.
- Risk baseline/persistence and owner-health/Stage8 regressions: **285 passed, 3 deselected in 75.65s**.
- Those three tests were also executed: **3 failed, 285 passed** in the prior complete Risk/health batch. A read-only in-process load of the exact required-base modules reproduced **the same 3 failures**. All no-order/HALTED safety assertions pass; only the obsolete expected `set_leverage` side effect fails. The existing tests and MI-1 through MI-5 are unchanged.
- An initial combined regression process exceeded its 1024 descriptor limit; bounded fresh-process batches above completed without that resource failure.
- Python AST, shell syntax and package diff whitespace checks: PASS.
- Graphify code-only update: PASS, zero semantic extraction/API work. Graph labels use hub names when prior community labels no longer match; no LLM relabel was run.

The final backup checks additionally refuse incomplete core inventories,
recognized credential keys inside operational state, and camel-case credential
fields in configuration. Existing state/control/execution authority files were
inspected directly; Risk policy and Executor behavior were not altered.
