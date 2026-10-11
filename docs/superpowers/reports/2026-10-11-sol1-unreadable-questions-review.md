# Sol1: unreadable research questions review

SCHEMA: `research_unreadable_questions`, `strategy-health-unreadable-question.v1`, 10 SQL columns. Family `question_kind=strategy_health_unreadable`, `scope_kind=strategy`, `source_kind=strategy_health_observed`, canonical authority `context_only`.
WHOLE_TABLE_DECISION: **KEEP**, including all columns and both uniqueness indexes.
ISSUE: No confirmed unnecessary table, obsolete writer or removable persisted column. Enumerating and spec-filtered readers scan/sort; no measured populated workload justifies a new index. Some constant SQL projections duplicate canonical JSON; they enforce exact persisted contracts and existing APIs.
FIX: Review documentation only; no schema/source/database change.
TESTS: **237 distinct focused cases passed** across complete question/run/bank suites; see final validation below.
COMMIT: Root coordinates the review-only commit.
CLOSURE: Offline audit complete; no deployment, migration, service restart or provider request.

## Exact schema and every column

`trader/core/journal.py:378` defines a rowid table: `question_id TEXT PRIMARY KEY`; every remaining column is explicitly `NOT NULL`; `source_event_id` and `recorded_at_ms` are INTEGER, remaining columns TEXT. Unique `(source_kind, source_event_id)` prevents competing questions for the same observation. SQLite's TEXT primary key here is not explicitly NOT NULL (`PRAGMA table_info` reports 0); canonical offline writer always supplies a verified nonempty deterministic identity. No defaults, foreign keys or checks are declared. Two SQLite autoindexes implement the primary key and source uniqueness.

| Column | Whole-column necessity and actual consumers | Decision |
|---|---|---|
| `question_id` | SHA-256 identity over family/schema/spec/source event/raw hash; keyed Journal lookup; plan/evidence/result/run/bank/recall references. Present in JSON too. | Keep |
| `schema` | Version projection; duplicate/conflict and reader projection verification; first-insert receipt record_type. | Keep |
| `question_kind` | Family projection and exact projection verification. No independent filter consumer found. | Keep for row/API compatibility |
| `scope_kind` | Strategy scope projection, exact verification; unreadable run reads directly for receipt references. | Keep |
| `scope_id` | Spec projection; Journal filtered reader; exact verification; unreadable run reads directly. | Keep |
| `source_kind` | Observation-kind projection; unique source constraint, conflict lookup and exact verification. | Keep |
| `source_event_id` | Source identity projection; unique constraint, conflict lookup, canonical list ordering and exact verification. | Keep |
| `canonical_sha256` | Derived hash projection; run references read directly; canonical readers recompute and verify; recall binds the recomputed canonical hash to registration. | Keep |
| `canonical_json` | Authoritative versioned question, bound source/sweep hashes, stored reasons, immutable semantics and deterministic question text. All canonical readers consume. | Keep |
| `recorded_at_ms` | First insert provenance outside canonical JSON; written atomically alongside registration; writer inserts the same timestamp in the receipt and row. Excluded from duplicate equality, never backfilled. | Keep |

Constant/derived fields are confirmed byte duplication, not confirmed unnecessary storage. Dropping them would break SELECT-* storage APIs, projection checks, pinned historical fixtures, downstream hash/scope receipt construction, and registration compatibility. The schema comment saying all other fields are projections has a provenance exception: `recorded_at_ms`. No canonical-byte or identity rewrite is justified.

## Purpose, writers and readers

The question asks whether one `compile_failed` or `evaluation_failed` strategy-health observation represents a capability/data-evaluation gap described by its stored reasons. It does not diagnose a cause or establish an edge. Every qualifying observation gets its own deterministic question; repeated sweeps are distinct source events and intentionally remain distinct. Raw health observation/sweep retention is required: canonical questions bind their exact hashes but do not copy the full source content.

Canonical writer: `research_unreadable_question.derive` is pure; `record_from_journal` derives from retained health history and calls `Journal.record_research_unreadable_question`. Journal `_record_registered` compares all nine canonical projections, returns inserted/duplicate/conflict, and atomically inserts the original timestamp and research-registration receipt. It never overwrites or backfills historical rows. The canonical runner invokes this writer; `research_shadow` and its restricted `_research_shadow_child` can invoke that runner in the explicit offline harness. Direct Journal calls are storage primitives, not independently authenticated contract writers.

Canonical reader: `research_unreadable_question.load` validates exact JSON shape, types, semantics, canonical encoding, deterministic ID and text, every SQL projection, and re-derivation from history bounded through the source sweep. Later unrelated history is excluded; missing/tampered bound evidence fails closed. `from_json` alone establishes contract validity, not source presence.

Direct consumers: `research_unreadable_plan` enumerates questions for planning/loading and applies `_question_text` verification; `research_unreadable_evidence` enumerates question rows and routes them through the plan validator; `research_unreadable_run` calls the writer, reads keyed rows for hash/scope references, and re-verifies question contracts/projections/source evidence; `research_unreadable_bank` performs keyed full-chain question validation; `research_unreadable_recall` reads keyed questions, validates source evidence and first-registration provenance before comparing recorded reasons. `research_families` calls pure derivation to dispatch source observations between two disjoint families; it does not read this table.

Transitive consumers: unreadable results through verified evidence; unreadable bank through verified Q/P/E/R/run; unreadable recall through bank; `research_shadow` and `research_shadow_report` through verified run/bank/recall; `learning/capture_runtime.research_bank` and `learning/capture` through verified bank (copying the linked question into learning provenance); restricted shadow child imports and executes the chain. `research_cost` reads downstream receipt/telemetry, and `scripts/predictive_research_shadow.py` inventories bank metadata; these are adjacent downstream consumers without direct question reads. Kernel/dashboard/Attention/Risk/Execution have no direct table writer/reader found. Constructing Journal initializes schema and does not derive questions.

## Comparison and whole-table decision

`research_questions` is the distinct strategy-decay question contract for `decayed` observations. Its parser and planner explicitly refuse this unreadable schema. Family dispatch has disjoint accepted verdicts. A common SQL table would require changing dispatch, ID uniqueness, APIs and every historical chain boundary; that is not an offline correction supported by this audit.

Results close a verified evidence chain as INCONCLUSIVE/NOT_ASSESSED with no registered falsifier; evidence freezes routed health content; bank objects bind completed run/result chains and enable verified recall. None substitutes for the question's original intent, recorded reasons, source identity or first-registration order. Questions also exist before downstream chains. No repeated *same-source* rows are permitted; repeated unreadable sweeps are intentional history, not confirmed duplicate questions. Current lack of persisted rows does not establish obsolescence: the offline family and its consumers remain implemented and tested.

Replacing this table with an on-demand view would remove immutable registration time, change historical provenance, and prevent detection of stored conflicts. Removal/merge/projection trimming has no demonstrated functional benefit and only 12 KiB measured empty-table footprint per database context. KEEP is justified by active contract consumers and tiny current cost.

## Real resource costs and limits

Root's bounded read-only SQLite audit (`mode=ro`, `query_only=ON`, progress handler/deadline) measured the production `data/luffy.db`, operational `/mnt/luffy-data/luffy/operational/frozen-demo-de6b35fc83e8/data/luffy.db`, and recovery `/mnt/luffy-data/luffy/recovery/research-state-transfer-r1/candidate/luffy.db`: **0 rows and 0 canonical payload bytes in each**. Exact-name dbstat returned **4096 B table + 4096 B primary-key index + 4096 B source uniqueness index = 12,288 B per context**, **36,864 B across the three existing databases**. These are existing databases, not audit-created copies. Reused root measurement to avoid duplicate scans; this agent independently verified production table metadata and query plans with a five-second read-only bounded connection.

List-all and spec-filtered ORDER BY both use table scan plus temporary B-tree; keyed question lookup uses the primary-key index. All projections have zero current row bytes because the table is empty. No real populated-table throughput, marginal projection savings, provider/CPU evaluation expense or growth rate is measurable from these databases. Synthetic regression time is validation cost, not a production performance benchmark. Derivation reads health history; downstream re-verification multiplies reads. No measured workload warrants weakening verification or adding storage for an index.

## Safety and historical compatibility

No operational data, research status, configuration or frozen historical artifact was modified. `config.yaml` remains `research.referee: false`, `research.handoff: false`. No DENIED/FROZEN/trading restriction changes are involved. Context-only authority remains mandatory; INCONCLUSIVE does not become admission. Historical question IDs, canonical bytes, insert timestamps and receipt absence remain untouched. Existing historical rows lacking receipts stay loadable as questions when evidence verifies, while provenance-dependent recall refuses absent receipts instead of backfilling. Tests cover source/sweep mutation/deletion, conflicts, repeat idempotence and unchanged decay question/plan/run/bank bytes.

## Final validation

Complete `tests/test_strategy_health_unreadable_bank.py` passed all **96 cases** within the initial combined invocation. That invocation returned **207 passed, 30 setup errors in 65.74s** because this agent started before creating its TMPDIR parent: every error was fixture setup `FileNotFoundError` on that absent parent. After creating the directory, complete `tests/test_strategy_health_unreadable_question.py` and `tests/test_strategy_health_unreadable_run.py` rerun returned **141 passed in 27.46s**, exit 0. Together **237 distinct cases passed**; no source regression remains. No source or test edits were made.

Both invocations used `PYTHONDONTWRITEBYTECODE=1`, `TMPDIR=/mnt/luffy-data/sol1-unreadable-questions-tmp`, `-p no:cacheprovider` and exclusive `--basetemp` subdirectories. Removed the entire owned temporary directory after completion, reclaiming **464,224,256 allocated bytes**; deletion verified. No unrelated or preserved root artifacts were removed. Production source paths have no agent-made diff; root owns final integration/commit and final disk verification.
