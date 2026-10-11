# SOL1 research_registrations review — 2026-10-11

## SCHEMA

Canonical schema: `trader/core/journal.py:519`. SQLite rowid table, all six columns NOT NULL:

| Column | Type / constraint | Necessity |
| --- | --- | --- |
| record_type | TEXT, composite PK part | Contract-family namespace; point lookup and family-isolated Bank join. Prevents cross-family identity substitution. |
| record_id | TEXT, composite PK part | Exact question/Bank identity; binds original filing. |
| canonical_sha256 | TEXT | Content binding independent of potentially changed/reinserted source row; checked against canonical record by consumers. |
| recorded_at_ms | INTEGER | First-registration time, used for strict known-before-question eligibility; distinct from source event order and row insertion time. |
| envelope_sha256 | TEXT UNIQUE | Stored canonical envelope digest, validated by readers; second collision key protected against replacement. |
| envelope_json | TEXT | Canonical `research-registration.v1` evidence: schema, record_type, record_id, canonical_sha256, recorded_at_ms. Exact canonical serialization verified by readers. |

Two implicit indexes: UNIQUE envelope_sha256 and PRIMARY KEY(record_type,record_id). Three BEFORE triggers reject UPDATE, DELETE, and any INSERT collision on either unique key, including INSERT OR REPLACE. No FK or SQL hash/JSON validation; content validation lives in canonical readers. `Journal.registration_envelope` emits sorted compact JSON with allow_nan=False.

## WHOLE_TABLE_DECISION

**KEEP.** The entire table provides independent immutable first-registration receipts shared across decay questions/Bank, unreadable questions/Bank and external-evidence Bank. Empty production state does not make the contract obsolete. Dropping it or merging its times into mutable source rows loses temporal integrity, first-write continuity and fail-closed handling of historical records without receipts. Keep every column and both indexes under current consumers. No safe offline storage correction established.

## ISSUE

Confirmed representational duplication: the canonical envelope repeats four projected values; bank rows repeat canonical_sha256, and question/Bank rows have recorded_at_ms. This is deliberately verified redundancy, not two duplicate workflows. Registration time can differ from a reinserted Bank row time; existing regression explicitly preserves the original receipt. Questions, results, evidence and Bank objects hold domain data/lineage; this table holds filing provenance, not research evidence or trading admission. It does not duplicate research_tests held-out budget records. Plans/results/evidence/runs/next-questions do not receive receipts through this writer path: adding receipts for them is not required by inspected consumers.

No obsolete writer, duplicate registration workflow, unnecessary payload or removal candidate was confirmed. No claims of cryptographic tamper resistance against unrestricted DB access: receipts and projections are consistency checks guarded against normal SQL mutation, not signed externally.

### Writers

Only `Journal._register` issues receipt INSERT SQL. Transactional callers:

- `record_research_question` via `cognition/research_question.record_from_journal`.
- `record_research_bank_object` via `cognition/research_bank.record_from_journal` and `research/external_research` external-evidence filing.
- `_record_registered` via `record_research_unreadable_question` and `record_research_unreadable_bank_object`; callers are matching unreadable cognition contracts.

Insert and receipt share one transaction; failures roll back. Duplicates never overwrite first time or backfill a historical row missing its receipt. Decay identical-content reinsertion retains an original receipt; unreadable helper refuses an orphan existing receipt. These existing family-specific historical semantics were preserved.

### Readers and all inventoried consumers

- `Journal.research_registration`: exact (record_type,record_id) lookup; PK index confirmed by EXPLAIN QUERY PLAN.
- `Journal.research_bank_registrations` / `research_unreadable_bank_registrations`: family/scope-limited LEFT JOIN; missing receipt stays visible and causes recall refusal, never silently disappears.
- `cognition/research_recall` and `research_unreadable_recall`: exact envelope, hash, type and projection validation; first registration strictly precedes question registration; separate source-event chronology verification. Context-only output.
- `cognition/research_shadow_report`: calls both validators, exposes VERIFIED/MISSING/INVALID receipts and context-only recall in owner report CLI.
- `cognition/research_bank_view`: transitive decay recall and verified Bank-chain owner view; transitive consumer `owner/queries` research/capability/cost lookup.
- `learning/foundation.prior_failed_for_question`: transitive context-only failed/inconclusive recall; suppression_authority=False. No inventoried caller gives this function live trading authority.
- `research/external_research.registered_question` and `verify_bank`: bind question and external Bank original content/filing envelope; consumed by worker selection, retained negative-memory/history checks, external Bank verification and `owner/queries`.
- `research/generated_question_source`: reads original producer registration into exact provenance; external worker verifies provenance without importing question into its Journal. Generated-source handoff/restart/history are consumers.
- `dashboard/overview_desk`: receipt row count, surfaced in research diagnostics. Count is descriptive, not admission.

Repository searches covered table name, receipt methods, writer helper and direct/transitive module consumers. Tests also perform deliberate adversarial SQL corruption in isolated fixtures. No Kernel/Risk/Execution authority wiring was found; consumer allowlist regression checks this boundary.

### Actual resource costs

Independent production measurement used SQLite mode=ro, query_only, temp_store=MEMORY, 2s lock timeout and 12s progress deadline; no copies/migrations/checkpoints. `data/luffy.db` file size at measurement: 3,582,115,840 bytes. registrations: 0 rows, 0 payload bytes; table 4,096 B, each implicit index 4,096 B, **12,288 B total**; 12,264 B unused within those pages. Hash/envelope scan found no rows, so no live historical integrity claim is possible.

Lead supplied independently bounded measurements for operational `/mnt/luffy-data/luffy/operational/frozen-demo-de6b35fc83e8/data/luffy.db` and recovery `/mnt/luffy-data/luffy/recovery/research-state-transfer-r1/candidate/luffy.db`: same empty table and **12,288 B** footprint each. Sum across three contexts: **36,864 B**. These pages are database allocation, not guaranteed reclaimable filesystem space; no VACUUM or deletion was performed. WAL size cannot be attributed to this empty table. CPU/network cost from inspected writer: two SHA256 calculations plus compact envelope serialization per new receipt; one extra table/index insertion inside existing transaction, no provider or LLM calls. No per-row production timing is measurable with 0 rows; no fabricated benchmark.

## FIX

No source, schema or stored-data correction justified. Added this review only. Preserved DENIED/FROZEN and research.referee/research.handoff restrictions, held-out budget gates and frozen historical artifacts. No deployment, migration, restart, worktree, provider call or DB copy.

## TESTS

**100 passed in 188.29s** across `tests/test_strategy_decay_research_recall.py` and `tests/test_generated_question_handoff_negative_memory.py`. Covers first registration, duplicate/restart stability, receipt rollback, temporal eligibility, legacy rows without receipts, immutable UPDATE/DELETE/REPLACE, payload/projection corruption, generated producer provenance, negative-memory policy and historical checkpoint compatibility. **4 external/router tests passed in 14.30s**: normal registered-question/Bank provenance, checkpoint/Bank corruption refusal, predictive/live boundary refusal, and existing-family recall compatibility. These tests inject `FakeChild`; no network/provider calls. Commands use ./venv/bin/python with PYTHONDONTWRITEBYTECODE=1, owned TMPDIR and --basetemp under /mnt/luffy-data, -p no:cacheprovider. Additional in-memory execution validated both recall-family receipt readers and shadow report VERIFIED/MISSING/INVALID classification: **22 assertions passed**, including corruption of each of six fields and absent historical receipts. Bank-view tests are owned by next-questions agent and will be reported by lead; full shadow invocation/report integration was inspected, not freshly exercised here.

## COMMIT

No commit created by this agent; report will be integrated by lead. No source changes to Graphify-update.

## CLOSURE

Decision complete: keep whole table and every current column. Compatibility grounded in offline regressions and consumer tracing, with live historical validation limited by all three measured tables being empty. Both owned pytest directories were removed after completion. Allocated storage reclaimed: core/generated **461,021,184 B**, external/router **10,227,712 B**, total **471,248,896 B**. No owned temporary files remain. No integration conflicts introduced; only the exclusive report changed. Latest observed root available **3,568,594,944 B** and data available **4,742,393,856 B** during tests; lead owns final headroom verification. Config inspected: referee=false, handoff=false.
