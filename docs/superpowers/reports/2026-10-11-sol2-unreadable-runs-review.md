# Sol2 — research_unreadable_runs review

SCHEMA:

The production-worktree `data/luffy.db` SQLite schema was inspected through a bounded read-only connection on 2026-10-11. Eleven columns; one rowid table and one unique primary-key autoindex. Contract: `strategy-health-unreadable-run.v1`; canonical receipt is authoritative. All non-key columns are NOT NULL. SQLite reports the TEXT primary key as `notnull=0`; canonical writer/reader enforce a valid recomputed 64-hex identity, so schema migration is not justified by an observed bad row.

| Column | SQLite type | Purpose |
|---|---|---|
| run_id | TEXT PRIMARY KEY | SHA-256 identity of explicit schema, kind, runner, run_key and input timestamp; duplicate lookup and bank/shadow linkage. |
| schema | TEXT NOT NULL | Versioned receipt contract, `strategy-health-unreadable-run.v1`; checked against canonical JSON. |
| run_kind | TEXT NOT NULL | Family discriminator, `strategy_health_unreadable`; checked projection. |
| runner_id | TEXT NOT NULL | Versioned producer identity, `strategy-health-unreadable-research-runner.v1`; checked projection. |
| run_key | TEXT NOT NULL | Caller-supplied nonempty offline invocation key, one of the identity inputs. |
| run_recorded_at_ms | INTEGER NOT NULL | Explicit nonnegative input timestamp handed to each chain step; part of run identity, independent of insertion metadata. |
| canonical_sha256 | TEXT NOT NULL | Digest of exact canonical receipt JSON; verified by row projection and used by linked bank/shadow artifacts. |
| canonical_json | TEXT NOT NULL | Immutable context_only receipt: inputs, four ordered question/plan/evidence/result steps, status/error, exact inserted/duplicate object ID/hash/scope references, conflict IDs and refusals; no assessment/admission authority. |
| telemetry_sha256 | TEXT NOT NULL | Independent digest of canonical telemetry; excluded from receipt hash and duplicate comparison but verified and bound by bank artifacts. |
| telemetry_json | TEXT NOT NULL | `strategy-health-unreadable-run-telemetry.v1`: per-step measured wall nanoseconds and Journal-returned rows, or explicit NOT_MEASURED reason; excludes writer-transaction page/row reads. |
| recorded_at_ms | INTEGER NOT NULL | Storage insertion timestamp; excluded from duplicate comparison and row projection, preserving insertion metadata independently of explicit run inputs. |

Receipt identity is deterministic. Telemetry varies between executions and deliberately sits outside identity/duplicate comparison. Writer is append-only; the immutable intent is application-enforced, not a database DELETE/UPDATE trigger. No extra indexes, foreign keys, defaults or retention jobs exist for this table.

WHOLE_TABLE_NECESSITY:

Challenge: could this entire table be dropped and inferred from question/plan/evidence/result rows or bank objects? No under the existing offline contracts. Those rows cannot reconstruct invocation grouping, failed and not-run steps, exact refusal/conflict sequence, measured telemetry, or distinct run keys/timestamps reaching the same object chain. Bank objects require the run receipt and telemetry digests and actively reverify the stored source; replacing them with embedded bank snapshots would remove verification evidence. The table is not required for trading, but it is a required historical audit source for the already implemented optional unreadable offline chain. Empty production-worktree usage is a reason to avoid expansion, not evidence that deleting the schema is safe.

ISSUE:

No proven correctness defect or useful space reclamation was found. Source-reader fanout can grow: the all-runs reader materializes rows; `load` verifies every referenced object; all-runs bank filing repeats verification; same-ID reruns also reverify the historical chain. Costs are real potential scaling concerns, but zero production-worktree rows provide no production bottleneck and do not justify changing failure-closed verification. Append-only retention has no TTL: future growth would depend on explicitly authorized runs with new identities. Research remains DENIED; no operational or real-data research run or operational write was performed. Runner execution was confined to existing synthetic pytest fixtures in recovery storage.

Canonical WRITER, READER and ALL CONSUMERS verified by graphify query followed by repository-wide exact table/module/method search and callsite inspection:

- Canonical business writer: `trader/cognition/research_unreadable_run.py:run` invokes four unchanged chain recorders then `Journal.record_research_unreadable_run`; the latter (`trader/core/journal.py:1637`) uses a transaction, compares only receipt projection, returns inserted/duplicate/conflict and never overwrites.
- Canonical storage reader: `Journal.research_unreadable_runs` (`journal.py:1661`), all rows in rowid order or a PK-filtered identity. Canonical verified reader: unreadable `load` (`research_unreadable_run.py:606`), exact receipt/telemetry contracts, recomputed identity/digests/projection, primary-key referenced chain verification and byte-equal receipt rebuild. Same-ID `run` uses this verified reader and executes no step again.
- Unreadable bank: `_verified_run` calls `ru.load`; `_chain`, filing (`record_from_journal` / `record_from_run`) and `load` / `verify_row` depend on that receipt. All-runs filing enumerates the raw storage reader before verified loading.
- Unreadable prior-research recall: `research_unreadable_recall.recall` verifies selected bank rows through `ub.verify_row`, which verifies their runs. Indirect consumer, no direct raw run-table read.
- Cost ledger: `research_cost.from_sources(..., include_unreadable_runs=True)` reads stored runs; `_unreadable_run_family` validates canonical receipt/telemetry and row projection, not the live evidence chain. Default excludes this family. This is an intentionally narrower telemetry consumer, not the canonical evidence verifier.
- Offline shadow child: `_research_shadow_child._runners` explicitly selects unreadable `run`; its worker reads the resulting stored row digest and files bank objects in the shadow database. This audit did not execute that production harness.
- Offline shadow parent: `research_shadow._FAMILY_STORES` and `_check_committed` read the shadow run table directly, validate `from_json` / `row_for`, and bind receipt to completion and bank links during recovery. The parent does not open the source journal.
- Read-only shadow report: `research_shadow_report` consumes bank verification/optional recall and opts into unreadable cost ledger, so it consumes runs indirectly through both chains.
- Focused test fixtures exercise these APIs and intentionally tamper/delete synthetic rows. No kernel, analyst, risk, executor, attention, LLM/provider, dashboard, timer or live research invocation was found reading/writing this table directly. No triggers/views or other canonical database consumers were found in schema inspection.

DUPLICATION:

`research_runs` is the strategy-decay sibling with a different contract and outcome shape; separation prevents cross-family admission/recall. Code shape is similar, not interchangeable stored meaning. Q/P/E/R rows store semantic objects, while this table stores orchestration outcomes and telemetry. Bank objects repeat source links and outcome context but require independent run verification. Shadow completions repeat run IDs/digests for invocation integrity, not complete run receipts. SQL projection columns repeat canonical fields intentionally for lookup and tamper detection. `run_recorded_at_ms` and `recorded_at_ms` are usually equal in the canonical runner but express input identity versus insertion metadata; historical low-level API compatibility precludes deleting either casually. No safe table merge or field deletion was established.

FIX:

No code/schema/data correction; added this evidence-backed report. No production DB writes, vacuum, checkpoints, pruning, test collection against operational data, deployments, activation, restart or trading changes. Sol1's dirty `research_bank_view.py`, Claude's preserved work and unrelated graph files were not edited. This agent performed no broad graph extraction. Lead owns the code-only incremental graph update for the coordinated bank integration correction.

DECISION:

KEEP. Retain existing minimal immutable store and family isolation. An 8 KiB empty store and contract dependencies make removal, migration or speculative indexing a worse trade than preserving compatibility. Unbounded all-runs verification is documented for a future evidence-driven optimization, without weakening current validation.

Storage, processing and retention costs:

- Production-worktree `data/luffy.db`: 0 rows, 0 canonical/telemetry payload bytes; table 1 x 4,096 B page and PK autoindex 1 x 4,096 B page, total 8,192 B. Empty data means first/last record timestamp and measured runtime history are unavailable. Exact-name `dbstat` restricted to the table and its autoindex.
- Bounded read-only PRAGMA/count/dbstat/query-plan diagnostic completed in 0.005342 s; this is audit measurement, not an offline-run performance claim. PK query uses `sqlite_autoindex_research_unreadable_runs_1`; all-runs ORDER BY rowid scans the table without an extra sort/index.
- Synthetic already-created focused-test fixtures illustrate a two-object receipt at 3,272 JSON bytes plus 2,592–2,594 telemetry bytes. These are examples, not production averages or future size bounds. Additional references/refusals increase receipt size and SQLite page allocation adds record/index/overflow overhead.
- Stored telemetry measures four chain-step elapsed wall times and Journal rows only. Duplicate verification, outcome-reference reads, receipt writing, direct chain calls, bank filing/loading, SQLite pages and writer reads are excluded. Total CPU, monetary and total research cost remain NOT_ESTABLISHED; no data/provider cost was incurred by this audit.
- Permanent append-only retention is currently 0 live rows. New distinct run identities would grow storage; same-ID duplicate calls do not add receipts. Safe retention cannot independently delete runs referenced by bank/shadow artifacts. No authorized retention change or worthwhile root-space savings exists here.

TESTS:

Fixture-only joint coverage: all 200 run/bank cases validated. First pass: 199 passed, 1 inherited historical-pin failure in 56.58 s. Post-bank-hook integration pass: 199 passed, same historical-pin failure in 55.47 s. The failing parameter compared `research_bank_view.py` against an old pre-Sol1 baseline and rejected Sol1's preserved committed `journal_evidence.install(conn)` correction. The bank agent accepted only that path's independently verified commit `1338cab44659eefb3084c1a1795d053c1aa7d5ce` as its historical baseline; other pins remain unchanged. The exact previously failing parameter then passed on final test source: 1 passed in 0.95 s. No Sol1 code was reverted and no tests were suppressed. Bank agent separately owns new capture-hook/recall/shadow consumer checks.

The post-hook shared suite exercises run duplicate idempotence, exception/not-run receipts, telemetry gaps, tamper/refhash/source failures, races/conflicts, family isolation, unchanged legacy fixture IDs/hashes, append-only API behavior and additive legacy schema recreation. Integration correction belongs to the bank agent; it extracts a regular-Journal capture hook and makes ShadowJournal override it to preserve the isolated research boundary. Runs storage and receipt contracts are unchanged.

Root headroom immediately before first tests: 3,564,298,240 B (> 2 GiB); before post-hook rerun 3,560,742,912 B; before final pin check 3,559,809,024 B; operational/recovery share `/mnt/luffy-data`, 5,469,720,576 B. Scratch, pytest basetemp, cache and TMPDIR use exclusively `/mnt/luffy-data/luffy/recovery/sol2-next-three-runs`; bytecode creation disabled.

COMMIT:

No commit staged or created by this agent; lead owns integration. Report-only change, no source edits.

CLOSURE:

Audit and consumer inventory complete; historical contracts retained and research remains DENIED. Owned disposable pytest/TMPDIR/cache trees have been removed: first pass 308,936,704 allocated B, final pass 309,030,912 allocated B, total 617,967,616 allocated B. Small test-output/cleanup evidence files remain in recovery storage. This is test scratch reclamation; no historical database rows were deleted. Post-cleanup root free 3,563,552,768 B; production/recovery shared volume free 4,991,971,328 B. Lead reports root/operational/recovery capacity and diagnoses nine bootstrap timer pairs; this agent changed no timer/service/log configuration.
