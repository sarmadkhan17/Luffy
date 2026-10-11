# Sol2: research_unreadable_plans review

TABLE / SCHEMA / ISSUE / FIX / DECISION / TESTS / COMMIT / CLOSURE

`research_unreadable_plans` / `strategy-health-unreadable-plan.v1` / No demonstrated defect or removable whole-table duplication / No code or schema correction / KEEP / Read-only production metadata and shared unreadable run/bank suites (result recorded by coordinating agent) / Coordinating agent commits this report / Offline review closed; no deployment, migration, activation, service or venue action.

## Whole-table necessity and actual data

The table preserves one immutable, context_only evidence-routing plan per verified unreadable strategy-health question. It is the durable question-to-evidence link used by the unreadable evidence collector, run receipt hydration and Research Bank revalidation. Generic `research_plans` intentionally accepts a different contract; merging these tables would break isolation and existing source bindings. Recomputing a plan would discard the stored canonical hash and immutable artifact that evidence and historical receipts bind. KEEP is justified by these actual consumers, even though this operational database currently contains no plan rows.

Bounded read-only inspection of production `data/luffy.db` found the declared 12 columns, 0 rows, no recorded timestamp range and no canonical payload bytes. Exact-name `dbstat` queries measured 4,096 bytes for the table and 4,096 bytes for each of its two automatic indexes: **12,288 bytes total**. Root separately corroborated 0 rows and the same allocation in the actual operational `/mnt/luffy-data/luffy/operational/frozen-demo-de6b35fc83e8/data/luffy.db`, using `mode=ro`, `query_only` and a bounded progress handler. This table does not account for current root disk growth. It has no active live-loop writer; it can be populated by an explicitly invoked offline research chain. No production database was copied, mutated or vacuumed.

## Every column

| Column | Actual purpose and consumers | Decision |
| --- | --- | --- |
| `plan_id` | Canonical identity projection; primary key, writer duplicate/conflict detection, run by-ID hydration, evidence and bank plan binding; verified against JSON identity. | KEEP |
| `schema` | Contract projection; schema/kind/question uniqueness, writer conflict detection and full reader projection verification. | KEEP |
| `plan_kind` | Contract projection; schema/kind/question uniqueness, writer conflict detection and full reader projection verification. | KEEP |
| `planner_id` | Frozen planner provenance projection; verified by canonical reader and evidence collector. No independent filtering found. | KEEP for historical projection compatibility |
| `question_id` | Upstream join key; filtered plan reader, source-question revalidation, evidence collection, run hydration and bank chain binding. | KEEP |
| `question_sha256` | Upstream canonical-byte binding projection; full reader and evidence collector revalidate it. | KEEP |
| `scope_kind` | Canonical scope projection; full reader/collector checks, run receipt and bank derive scope from verified canonical plan. No independent SQL filter found. | KEEP for historical projection compatibility |
| `scope_id` | Canonical spec scope projection; full reader/collector checks, run receipt and bank derive scope from verified canonical plan. No independent SQL filter found. | KEEP for historical projection compatibility |
| `source_event_id` | Bound source-observation projection; deterministic reader ordering and writer same-source conflict detection. | KEEP |
| `canonical_sha256` | Canonical artifact digest projection; checked by reader/collector and bound by downstream chain. | KEEP |
| `canonical_json` | Authoritative frozen routing mapping, question binding, scope and health-record ID/hash bindings; fully rebuilt and checked by canonical reader. | KEEP |
| `recorded_at_ms` | First-insert audit timestamp; deliberately excluded from canonical identity/projection and duplicate equality, so a later retry preserves the original row. No semantic reader depends on it. | KEEP audit/history metadata |

Eleven columns repeat canonical identity/content projections; this is explicit in the schema contract. Some projections lack standalone SQL consumers, but every projection participates in integrity validation. Removing them would change the stored contract and break existing reader/writer compatibility for negligible measured saving. The timestamp has no canonical counterpart and is not a second claim about source event time.

## Mandatory writer, reader and consumer checks

- Canonical producer: `trader/cognition/research_unreadable_plan.py:record_from_journal` revalidates stored questions and health evidence, builds the deterministic plan and calls `Journal.record_research_unreadable_plan` with `row_for(plan)`.
- Storage writer: `trader/core/journal.py:record_research_unreadable_plan` performs a serialized duplicate/conflict check under `BEGIN IMMEDIATE`, then inserts once. It ignores retry timestamps when deciding identical duplicates and never overwrites conflicts. The SQL constraint is `UNIQUE(schema, plan_kind, question_id)`; same-source-event uniqueness is additionally enforced by the canonical storage writer. No migration is justified by the narrower declarative constraint while this sole canonical writer enforces the rule.
- Canonical reader: `research_unreadable_plan.load` fetches stored rows, revalidates the question and source health records, checks canonical bytes against a fresh build, and checks all eleven row projections. `from_json` is the pure contract verifier. `Journal.research_unreadable_plans` and `research_unreadable_plan_by_id` are raw storage readers, not semantic validation APIs.
- Direct semantic consumers: `research_unreadable_evidence.record_from_journal` and `load` consume all plan rows and revalidate through `_plan_inputs`; `research_unreadable_run` invokes the plan writer in chain order, uses the by-ID reader for receipt hydration, and invokes `up.load` when verifying references; `research_unreadable_bank._chain` invokes `up.load` to revalidate stored chain links.
- Transitive consumers: `research_unreadable_result` obtains a validated plan through evidence input verification; `_research_shadow_child` explicitly invokes the unreadable runner and bank in the existing offline harness. Existing run/bank tests exercise these consumers. Repository-wide Python searches found no other table accesses or plan storage writers. No frontend/dashboard, kernel, risk or execution consumer was found.
- Further Bank/run consumers: `research_unreadable_recall`, `research_shadow`, `research_shadow_report`, `learning/capture_runtime.research_bank` and `learning/capture` consume verified chain artifacts. `research_cost` parses run receipts/telemetry, and `scripts/predictive_research_shadow.py` inventories Bank rows; neither directly reads or independently validates this plan table.

## Cost, history and verification limits

Writer lookup is bounded by the existing rows but its OR predicate includes an unindexed source-event branch. The unfiltered offline reader loads all plan/question/health rows; plan history route bindings can accumulate with same-spec health history. These are possible future scaling costs, not observed production growth, and adding an index or changing routing would be speculative at 0 rows.

Existing unreadable run/bank tests cover deterministic chains, duplicate/conflict behavior, corrupted/deleted object refusal, rehashed substitution, source changes, legacy additive Journal construction, isolation from readable/decay research, and byte-identical upstream modules/fixtures. `test_strategy_health_unreadable_bank.py` explicitly pins the plan module to its historical source blob, so needless edits would also violate historical guarantees. Results agent completed these combined suites: **200 passed in 64.94s**, exit 0, using an exclusive basetemp on `/mnt/luffy-data`, subsequently removed. No new implementation-mirroring tests were added for this unchanged table. Production has no rows, so actual populated-production payload compatibility cannot be measured; coverage comes from existing historical fixtures and tests.

Local Claude execution was unavailable (`Not logged in · Please run /login`), as verified by the coordinating agent. The user explicitly authorized native agents; this agent performed bounded native read-only auditing and report writing. No substantial implementation was transferred or undertaken.

Only this report is owned by this agent. No disposable files or database copies were created. An overly broad read-only `dbstat LIKE` scan was terminated on its own process and replaced with exact-name queries; operational processes were untouched.
