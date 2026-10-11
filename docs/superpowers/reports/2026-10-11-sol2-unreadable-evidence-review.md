# Sol2: unreadable evidence table review

TABLE: `research_unreadable_evidence`
SCHEMA: `strategy-health-unreadable-evidence.v1`; 13 SQL columns; primary key `evidence_id`; unique `(schema, evidence_kind, plan_id)`.
ISSUE: No demonstrated functional defect or worthwhile disk reduction. Derived SQL projections and repeated frozen health content have deliberate verification and historical compatibility roles.
FIX: Record the audit; no source, schema, migration, operational database or service change.
DECISION: KEEP table and all columns.
TESTS: Results agent's combined unreadable run/bank suites: **200 passed in 64.94s**, exit 0. Read-only production metadata and query-plan checks completed.
COMMIT: Root coordinates the review-only commit.
CLOSURE: Audit closed; deployment and activation remain outside scope.

## Whole-table necessity and authoritative paths

`trader/cognition/research_unreadable_evidence.py:record_from_journal` is the canonical writer. It verifies persisted plans/questions/health rows, collects precisely the plan's routed records, and calls `Journal.record_research_unreadable_evidence`. The storage primitive atomically classifies inserted/duplicate/conflict; timestamp differences do not change object identity. Direct callers of the storage primitive are not independent contract writers.

`research_unreadable_evidence.load` is the canonical reader. It verifies plan/question bindings, canonical JSON, all SQL projections, frozen content hashes, and equality to retained live source rows. `from_json` validates frozen evidence against supplied plan/question text without establishing current source liveness; it is not a substitute for `load`.

This separate table preserves immutable frozen health content and the evidence identity/hash referenced by unreadable results, run receipts and bank objects. `research_evidence` serves a different strategy-decay contract and refuses this family. Replacing unreadable evidence with a view or on-demand reconstruction would remove the persisted evidence boundary and break those references. Frozen copies also preserve exactly what the plan routed; nevertheless normal `load` still requires source retention and fails closed after source mutation/deletion.

## Every column

| Column | Actual role and cost/duplication | Decision |
|---|---|---|
| `evidence_id` | Deterministic identity, SQL primary key, result binding, run keyed reader. Repeated in canonical JSON. | Keep |
| `schema` | Contract/version projection; participates in unique and conflict checks. | Keep |
| `evidence_kind` | Family projection; participates in unique and conflict checks. | Keep |
| `collector_id` | Collector contract projection, equality and reader projection verification. No standalone filter consumer. | Keep for persisted row/API compatibility |
| `plan_id` | Source-plan projection, filtered evidence reader, result lookup, unique and conflict checks. | Keep |
| `plan_sha256` | Exact source-plan byte binding projection; equality/reader verification. | Keep for contract and persisted row compatibility |
| `question_id` | Source question projection; equality/reader verification. | Keep for provenance and persisted row compatibility |
| `scope_kind` | Scope projection; run receipt reads this SQL field directly and canonical reader verifies it. | Keep |
| `scope_id` | Spec scope projection; run receipt reads this SQL field directly and canonical reader verifies it. | Keep |
| `source_event_id` | Routed observation event projection; ordering and competing-source conflict checks. | Keep |
| `canonical_sha256` | Hash derived from canonical JSON; run references consume this SQL field directly and reader verifies it. | Keep |
| `canonical_json` | Authoritative frozen evidence contract, source contents and downstream hash input. Largest per-row field. | Keep |
| `recorded_at_ms` | External first-insert telemetry; deliberately outside canonical JSON, duplicate comparisons and reader projection validation. No decision consumer found. | Keep as storage provenance and historical SQL compatibility |

The schema comment's claim that all non-JSON columns are projections has one exception: `recorded_at_ms`. This review documents the exception; no implementation or stored-byte correction is required.

## All consumers and execution ownership

- `research_unreadable_result`: enumerates stored evidence; uses `ue.load` to verify evidence before creating or loading structural results.
- `research_unreadable_run`: `STEPS` invokes the canonical evidence writer; `research_unreadable_evidence_by_id` reads stored receipt references; `_verified` uses `ue.load` when rebuilding receipt references. Receipt construction reads `canonical_sha256`, `scope_kind`, and `scope_id` directly.
- `research_unreadable_bank`: `_chain` uses `ue.load` and follows result/evidence/plan/question links; frozen routing fields are consumed using `ue._ROUTE_COPY`.
- `_research_shadow_child`: permits the module in its restricted import closure and indirectly invokes the unreadable runner/bank through `_runners`; this is an explicit isolated harness path.
- Transitive verified-chain consumers: `research_unreadable_recall` calls bank verification; `research_shadow` and `research_shadow_report` verify unreadable run/bank chains; `learning/capture_runtime.research_bank` and `learning/capture` use unreadable bank verification. These inherit the canonical evidence reader through the bank/run contracts rather than reading the evidence table directly.
- Adjacent metadata consumers: `research_cost` consumes run receipt/telemetry rows through pure run-contract verification, without loading evidence; `scripts/predictive_research_shadow.py` inventories bank metadata. These depend on downstream artifacts but are not direct evidence readers.
- Tests: unreadable run/bank suites exercise writer/reader, deterministic references, duplicate/conflict behavior, source mutation/deletion, family isolation and historical byte preservation. Decay question tests also check the allowed research-module boundary.

Repository searches found no direct evidence writer/reader calls in kernel, dashboard, attention, execution or venue. Kernel and dashboard use `data/luffy.db`; merely constructing their Journal initializes schemas and does not collect unreadable evidence. The explicitly offline shadow dispatch and transitive learning consumers above must remain in any dependency assessment.

## Cost and historical compatibility

Read-only SQLite connection (`mode=ro`, `query_only=ON`) to production `data/luffy.db` observed **0 evidence rows**, no payload bytes and no timestamp range. Exact-name `dbstat` measured **4096 bytes** for the table and **4096 bytes each** for its two unique autoindexes: **12 KiB total**. This table cannot explain root disk pressure.

Root separately checked the actual operational frozen-demo database, distinct from production, using `mode=ro` and `query_only=ON`; all three reviewed unreadable tables were empty there too. This agent's 12 KiB measurement is specific to production and is not claimed as an operational-database measurement.

The plan-filter query performs a scan and temporary ordering; the existing uniqueness index starts with schema/kind, not plan. Collection and verification also read retained health/question/plan history and downstream repeated verification can multiply that cost. No production evidence rows or measured workload justifies adding an index, changing validation scope or weakening verification in this task.

One source observation can appear in both the source-observation and spec-history routes, and frozen payloads can recur across plans. This is intentional route-preserving duplication bound into canonical JSON hashes. Deduplicating payloads, dropping projections, changing identities or recomputing historical evidence would require explicit compatibility design and migration, which this session excludes. Preserve historical IDs, canonical bytes, results, receipts and bank references.

## Validation and ownership

CLAUDE.md and the graphify skill were read; existing-graph query ran first. Targeted source searches then resolved its truncated broad matches. Production checks made no Journal construction, writes or copies. Root's minimal local Claude execution failed with `Not logged in`; authorized native agents performed this bounded audit. No implementation was transferred to Claude or Astra.

An evidence-owned duplicate combined test invocation was stopped after root assigned authoritative ownership to the results agent; it produced 43 passing progress dots and no failures before termination, which is **not a completed validation result**. Its exclusive `.tmp-sol2-evidence` directory was removed. No disposable fixtures, production copies or backups remain owned by this agent. Unrelated Claude/Sol1 work and shared sources were preserved.

Authoritative coordinated validation: results agent ran `./venv/bin/python -m pytest -q tests/test_strategy_health_unreadable_run.py tests/test_strategy_health_unreadable_bank.py` with an exclusive basetemp under `/mnt/luffy-data`; **200 passed in 64.94s**, exit 0. This covers real chain writers/loaders, bank verification, duplicate/conflict/tamper refusal, historical upstream/registry/decay pins, and legacy compatibility. The results agent removed its dedicated temporary fixture directory after completion.
