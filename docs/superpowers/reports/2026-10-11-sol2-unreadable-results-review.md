# SOL2 unreadable results table review

TABLE: `research_unreadable_results`.
SCHEMA: `strategy-health-unreadable-result.v1`; 14 columns, primary key
`result_id`, unique `(schema, result_kind, evidence_id)`.
ISSUE: No demonstrated correctness defect. The result module's introductory
isolation prose predates its run/Bank consumers; actual consumers are below.
FIX: No code, schema, migration, or operational change justified.
DECISION: KEEP the table and every column.
TESTS: Combined existing unreadable run and Bank suite: 200 passed.
COMMIT: Parent integrates this report with the other SOL2 audit reports.
CLOSURE: Audit complete; operational activation remains unauthorized.

## Necessity and canonical contract

The table stores an immutable structural closure for each verified unreadable
evidence record. Its only outcome is `INCONCLUSIVE` / `NOT_ASSESSED` with
`no_registered_falsifier_predicates`; it grants no admission or trading authority.
Although the outcome is constant, a result also binds exact evidence identity,
hash, plan/question ancestry and scope. Removing the table would break durable
run references, Bank chain verification and their downstream consumers. Merging
it into decay `research_results` would mix incompatible contracts.

Canonical writer: `trader/cognition/research_unreadable_result.py`:
`record_from_journal` re-verifies evidence, calls `build` / `row_for`, then
`Journal.record_research_unreadable_result` (`trader/core/journal.py`) performs
an atomic append, idempotent duplicate detection and conflict refusal.
The runner invokes this unchanged writer as its result step.

Canonical reader: result `load` -> `_verified_evidence` -> evidence `load`,
then result `from_json`, deterministic rebuild and complete row projection
comparison. Journal list/by-ID methods are storage primitives, not sufficient
contract verification by themselves. Source liveness intentionally remains
required: alteration/deletion of upstream health/question/plan/evidence data
makes the persisted chain unloadable, without rewriting stored result bytes.

## Every column

| Column | Actual use and retention reason |
| --- | --- |
| `result_id` | Content-derived identity, primary-key lookup, run/Bank links and duplicate/conflict protection. |
| `schema` | Exact version/family discriminator; writer uniqueness and reader projection validation. |
| `result_kind` | Unreadable family discriminator; uniqueness and projection validation. |
| `resolver_id` | Resolver provenance and deterministic result identity; projection validation. |
| `evidence_id` | Exact parent link, filtered result loading, uniqueness and writer conflict detection. |
| `evidence_sha256` | Parent canonical-byte binding; rebuild and projection validation. |
| `plan_id` | Reader selects and verifies the evidence's parent plan; ancestry projection validation. |
| `question_id` | Exact ancestry binding used by Bank chain and projection validation. |
| `scope_kind` | Run reference scope, Bank linkage and projection validation. |
| `scope_id` | Spec scope in run references, Bank linkage and projection validation. |
| `status` | Stored `INCONCLUSIVE` projection, checked against canonical structural outcome. |
| `canonical_sha256` | Run receipt object reference hash and projection validation. |
| `canonical_json` | Authoritative exact-key record, semantic constants, source binding and deterministic rebuild. |
| `recorded_at_ms` | First-insert storage timestamp; deliberately excluded from canonical identity and duplicate comparison. |

The projection duplicates canonical fields deliberately. Dropping currently
constant or non-filtering columns would remove historical projection checks and
change persisted rows. `recorded_at_ms` is metadata rather than authenticated
result content; a later duplicate preserves the original timestamp.

## Actual consumers

Direct consumers and primitives:

- Result `record_from_journal` and `load` perform canonical writes/reads.
- `research_unreadable_run.py`: `STEPS`, `_stored_ref`, `_verified` and
  `_rebuilt_outcomes` write the result step and verify persisted result references.
- `research_unreadable_bank.py`: `_chain` reads by result ID, calls result
  `load`, verifies all Q/P/E/R links, and preserves structural outcome in Bank.
- Journal exposes list and primary-key readers and the append-only writer.
- Existing unreadable run/Bank tests read and deliberately corrupt result rows
  to prove missing, altered and unsupported outcomes fail closed.

Indirect consumers of the verified result through run/Bank:

- `research_unreadable_recall.py`: registered Bank recall verifies linked chain.
- `_research_shadow_child.py`: explicit isolated unreadable runner/Bank writer;
  `research_shadow.py` validates copied run/Bank artifacts;
  `research_shadow_report.py` verifies Bank and recall for reports.
- `trader/learning/capture_runtime.py::research_bank` uses Bank `verify_row`
  and freezes the verified result/chain into learning evidence;
  `trader/learning/capture.py` replays Bank construction and exact chain links.
- `research_cost.py` consumes unreadable run receipts/telemetry via pure
  run contract parsing; it does not directly load the result table or claim
  independent result source verification.
- `scripts/predictive_research_shadow.py` inventories Bank object rows; it
  does not directly consume or validate this table.

Repository-wide identifier and import searches found no direct dashboard,
frontend, venue or execution consumer of this table. No live path was executed.

## Cost, duplication and historical compatibility

Read-only `sqlite3` URI `mode=ro` plus `PRAGMA query_only=ON` inspected the
production `data/luffy.db` schema and aggregate size: **0 rows**, no canonical JSON payload,
and the expected two SQLite automatic indexes. A bounded metadata query
confirmed the filtered `evidence_id` reader scans the primary-key index because
the compound uniqueness index leads with `schema, result_kind`. An additional
index is unjustified at the current empty/offline volume, and no migration was
performed. Full `dbstat` scanning was stopped by terminating only this agent's
owned read-only helper process. Root separately corroborated the actual
operational database at
`/mnt/luffy-data/luffy/operational/frozen-demo-de6b35fc83e8/data/luffy.db`:
this table also has **0 rows**. Root's exact-name bounded `dbstat` query measured
4,096 bytes for the table and 4,096 bytes for each of its two automatic indexes:
**12,288 bytes total**. All three audited unreadable tables together occupy
36,864 bytes including their automatic indexes. These empty tables are not a
root disk growth source.

Result writes hold one transaction and compare the existing semantic columns;
loads rebuild and re-verify ancestors. Repeated full-source verification and
overlapping run/Bank references can increase cost as volume grows, but a change
needs measured volume and careful source-liveness compatibility. Current evidence
does not justify caching, dropping ancestry or adding indexes.

Preserve current v1 canonical JSON, IDs, hashes, ordering, duplicate/conflict
behavior and first-insert timestamps. Do not collapse unreadable into decay
results, reinterpret INCONCLUSIVE, delete historical objects or remove upstream
source liveness. Operational databases, backups, Claude registry changes and
Sol1 work were untouched. Local Claude execution is unavailable (`Not logged in`);
root explicitly assigned native audit/test execution after that failure.

## Validation outcome

`./venv/bin/python -m pytest tests/test_strategy_health_unreadable_run.py tests/test_strategy_health_unreadable_bank.py --basetemp=/mnt/luffy-data/sol2-unreadable-results-tests -q`

**200 passed in 64.94 seconds**, exit 0. These execute real question/plan/
evidence/result `record_from_journal` writers through runner `STEPS`, canonical
loaders and Bank source verification. Coverage includes duplicate/conflict
behavior, tampering and deletion refusal, result substitution, byte-identical
decay fixtures/IDs/hashes, registry pins, upstream module pins and additive
legacy-journal compatibility. Downstream consumer existence was checked by
source tracing; unrelated consumer suites were not broadened or claimed.
Owned test fixture directory was removed after completion. No code changed,
so `graphify update .` was unnecessary.
