# SOL2 — research_unreadable_bank_objects

SCHEMA:

`trader/core/journal.py` defines 14 columns, no foreign keys or defaults, a TEXT primary key and a UNIQUE(run_id,result_id) constraint. Each creates an automatic index. SQLite reports the TEXT primary key's `notnull` flag as 0; every other column is explicitly NOT NULL. Normal writes enforce the family contract in the canonical writer, not through SQL CHECK constraints. No table-level UPDATE/DELETE trigger exists: append-only is a writer contract. Registration receipts have their own immutability protection.

| Column | Type | Purpose |
| --- | --- | --- |
| bank_object_id | TEXT PRIMARY KEY | SHA-256 identity of schema/kind/builder plus exact run/result IDs and canonical hashes; stable filing identity. |
| schema | TEXT | `strategy-health-unreadable-bank-object.v1`, isolates the record contract. |
| bank_kind | TEXT | `strategy_health_unreadable`, separates this family from strategy-decay research. |
| builder_id | TEXT | `strategy-health-unreadable-research-bank.v1`, deterministic builder version. |
| run_id | TEXT | Exact completed unreadable-run link; indexed with result_id for conflict protection and per-run lookup. |
| result_id | TEXT | Exact reached result; only one filing per run/result pair. |
| evidence_id | TEXT | Exact bound evidence record, whose full source content is retained elsewhere. |
| plan_id | TEXT | Exact routing-plan link. |
| question_id | TEXT | Exact unreadable strategy-health question link. |
| scope_kind | TEXT | Projection of the object's scope kind, used by recall's scope filter. |
| scope_id | TEXT | Projection of scope.spec_id, used to find prior research for the same strategy. |
| canonical_sha256 | TEXT | SHA-256 of canonical_json; integrity and registration binding. |
| canonical_json | TEXT | Authoritative serialized object: schema/identity/authority, exact linked IDs and hashes, scope, copied question/plan, evidence source identities, structural result, unavailable claims/next questions, unclassified support/contradiction, run-step experiments, limitations, inherited source liveness, copied run-wide telemetry and fixed semantics. |
| recorded_at_ms | INTEGER | First filing timestamp; duplicates/conflicts preserve it. Registration stores the authoritative first-registration timestamp separately. |

The JSON's `authority=context_only`, INCONCLUSIVE/NOT_ASSESSED result, unavailable claims and absent falsifier predicates confer no trading or admission authority. `canonical_json` is authoritative; the other contract columns must match its projection. Historical rows without registration receipts are not backfilled. Reader verification requires all upstream run/Q/P/E/R/health records to remain verifiable; this is not an independently authenticated archive.

WHOLE_TABLE_NECESSITY:

The entire object can be rebuilt from retained verified run/Q/P/E/R records; this table is not indispensable to compute the structural result. Its distinct durable function is recording **which exact object was filed**, preserving its bytes/identity, conflict behavior and atomic first-registration receipt. Prior recall and shadow completion/recovery use those durable filings. Rebuilding on demand would not recover first-registration chronology and would require changing established readers and receipts. Removing or merging it would save only 12 KiB in each inspected database today while breaking those contracts. Keep this family separate under the current denied-research boundary. A future new contract could store bindings plus receipts rather than repeated structure, but that is not a proven beneficial migration for empty historical stores.

ISSUE:

Proven offline isolation defect: `Journal.record_research_unreadable_bank_object` imported `trader.learning` before persistence and invoked capture inline. `ShadowJournal` suppresses regular bank capture because its research-only database lacks learning tables and the child import allowlist excludes learning modules, but unreadable bank capture bypassed that override. A synthetic source and actual initialized ShadowJournal under an import guard reproduced `ImportError: synthetic_shadow_import_guard:learning` before an insert. No production database or provider was involved.

Canonical WRITER: `research_unreadable_bank.record_from_journal` / `record_from_run` -> `_file_run` -> verified run/chain -> `build`/`row_for` -> `Journal.record_research_unreadable_bank_object` -> `_record_registered`, with atomic registration and duplicate/conflict refusal. The actual offline shadow child invokes `record_from_run`; it does not write production. Generic Journal is another supported storage context and retains its learning capture callback.

Canonical READER: `research_unreadable_bank.load` -> `Journal.research_unreadable_bank_objects` -> `verify_row`, strict canonical parsing, each upstream contract's verification, byte-equal rebuild and projection check. `verify_row` is also the one-object canonical verifier.

ALL CONSUMERS found by graph query followed by bounded repository call searches:

- `research_unreadable_recall.recall`: scope-filtered `research_unreadable_bank_registrations` JOIN, validates all scope receipts, bounds selected objects and calls `verify_row`; returns context-only prior filings. Used by the read-only shadow report.
- `_research_shadow_child`: files per-run objects and reads filed rows to bind bank IDs/hashes to completion receipts.
- `research_shadow`: reads exact bank IDs during parent recovery, checks `from_json` and projected columns; its recovery check is deliberately narrower than live upstream verification and never opens the production source journal.
- `research_shadow_report`: per-run rows, `verify_row`, first-registration metadata and optional unreadable recall; emits read-only owner-report structure. Its cost report reads run telemetry, not a second bank-cost ledger.
- Regular Journal's `learning.capture_runtime.research_bank(...,unreadable=True)`: verifies the new row, freezes the exact Q/P/E/R/run/bank/outcome into learning_source_blobs and registers/attaches a RESEARCH outcome. This is an actual integration missing from the bank module's older isolation prose; no claim that all integrations are absent is warranted. `learning.capture.validate_chain` rebuilds the retained unreadable bank on replay. `capture.attach` -> `learning.producers.materialize` -> foundation replay/evidence/proposal consumes these snapshots; materialization does not apply authority. The explicit `learning.application.prepare_captured` / `scripts/learning_application_shadow.py` consumer operates on retained learning outcomes. They are transitive snapshot consumers, not SQL readers of this bank table. This change preserves regular capture unchanged.
- `scripts/predictive_research_shadow.inventory`: read-only COUNT(*) inventory only; no canonical-object consumption. Not executed here.
- Focused synthetic bank/historical capture tests verify these contracts. There is no additional direct table reader or direct bank-module importer in kernel, Attention, Analyst, Risk, execution, owner query handlers, dashboard or frontend found by the repository search. Generic learned target consumers may consume separately governed learning projections; that does not make them direct readers of this table or grant this object trading authority.

DUPLICATION:

Projection columns duplicate canonical JSON intentionally for identity/filter/conflict checks. The JSON copies question/plan/result/run steps and telemetry; evidence source_content stays in the evidence table. Per-result objects repeat run-wide telemetry, which explicitly must not be summed as independent costs. research_registrations duplicates IDs/hashes/timestamp as an immutable provenance envelope, not as another bank object. Generic Journal learning snapshots repeat verified chain content for independent replay. `research_bank_objects` has a similar column layout but different family contracts; existing strategy-decay bank/view/recall readers deliberately reject unreadable-family objects. Similar structure does not establish safe interchangeable functionality.

Costs measured with SQLite `mode=ro`, `PRAGMA query_only=ON`, exact-name bounded dbstat; no database copy, broad dbstat scan, vacuum or production write:

- Production data/luffy.db: 0 rows, 0 canonical payload bytes, no oldest/latest filing. Table 4,096 B plus two automatic indexes 4,096 B each = **12,288 B** allocated, 0 B payload, 12,264 B unused within those pages. Bounded schema/count/index/dbstat measurement took 0.00451 s on this session, not a general throughput benchmark.
- Lead independently measured the operational and recovery DBs: likewise 0 rows and 12,288 B each, combined **36,864 B** across the three stores. A DROP would not by itself shrink the database file.
- No TTL/pruning/retention job or bank-table delete/update writer found. Retention is indefinite. Deleting linked rows makes filed objects unloadable and can invalidate recall/completion verification. The empty stores have no measured per-row growth rate; no bytes/row or future operating-cost rate is invented.
- `_file_run` shares one verified run across its reached results, but per-result Q/P/E/R verification still repeats. `load` re-verifies the run and linked chain for every row and materializes all matching rows; health-source reads can repeat. Recall scans/validates scope receipts before applying max_objects. EXPLAIN QUERY PLAN confirms a bank primary-index scan for the scope filter (no scope index). Zero rows make that cost negligible; an index or caching change has no demonstrated benefit here and might compromise fail-closed verification. No optimization was implemented speculatively.
- One already-created synthetic shared-suite fixture was inspected read-only: two verified bank objects had canonical sizes 11,755 and 12,005 B. One `ub.load` issued 125 traced SELECT statements and took 0.12343 s. This measures verification fanout on that small fixture, not production throughput or a future growth forecast; the fixture was released to its owner for cleanup immediately afterward.
- Each ordinary first insert also incurs receipt serialization/index writes and regular-Journal learning snapshot/replay work. Shadow stores now correctly incur only the research row/receipt cost. Research stays DENIED; no production workload was started to measure it.

FIX:

Moved only unreadable first-insert capture into an overridable `_capture_research_unreadable_bank` hook. Base Journal retains the exact existing `lc.safely(...lr.research_bank,row,recorded_at_ms,self,unreadable=True)` dispatch. ShadowJournal overrides that hook with no action, matching its existing regular-family boundary. SQL, canonical IDs/JSON/hashes, first-registration receipt semantics, historical rows, duplicate/conflict decisions and production learning/trading authority are unchanged. No migration or production mutation.

Dedicated regression checks actual synthetic ShadowJournal persistence under a denied learning import, one receipt, unchanged duplicate/conflict behavior, no learning tables, untouched source bank table; ordinary Journal's exact callback arguments and first-insert-only invocation; and atomic rollback of object and receipt when the capture hook raises. The source-caller isolation assertion was extended solely for the shadow hook. A historical bank-view byte pin was updated **only for that path** to accepted committed Sol1 repair `1338cab44659eefb3084c1a1795d053c1aa7d5ce`; every other module/fixture remains pinned to original `37f9473`. The Sol1 reader itself was not edited or reverted.

DECISION:

**KEEP** the whole table under its established isolated filing contract; correct the proven offline capture-boundary defect. No storage deletion, schema merge or retention change is justified by an empty 12-KiB store.

TESTS:

Focused capture/recall/historical verification: `tests/test_unreadable_bank_shadow_capture.py tests/test_journal_evidence_repair.py tests/test_strategy_decay_research_recall.py tests/test_historical_outcome_capture.py -k 'unreadable or shadow or recall'`: **92 passed, 43 deselected, 167.62 s**. Runs agent's joint unreadable-run/bank post-hook suite: **199 passed**; the one failing parameter had loaded the old historical bank-view baseline before the coordinated pin update. Exact parameter rerun on final source: **1 passed, 0.95 s**, covering all 200 shared cases on final implementation/accepted baseline. Prior failure was preserved Sol1's evidence-function installation, not changed family IDs, JSON or fixture bytes; the fixed committed baseline is narrowly documented above.

Lead owns the final AST-only graph update. Before focused tests, root free was 3,561,582,592 B (>2 GiB) and recovery free was 5,113,950,208 B. All scratch/basetemp/TMPDIR paths belong to recovery/sol2-next-three-bank; no root scratch or pytest cache was requested. Shared run+bank verification was coordinated with the runs agent.

COMMIT:

Agent did not stage or commit; integration commit belongs to the lead. The fixed Sol1 baseline commit is referenced only as evidence, not as this table's commit.

CLOSURE:

Research DENIED; no activation, provider calls/new collection, trading changes, deployment/restart or production database writes/copies. Shared edits were explicitly coordinated with the lead; preserved Sol1/Claude work remains intact. Owned recovery disposable tree was cleaned after final measurements/tests; bytes reclaimed recorded in lead integration evidence. Nine bootstrap timer-pair log-growth diagnosis is owned by the lead; this agent did not stop or modify timers.
