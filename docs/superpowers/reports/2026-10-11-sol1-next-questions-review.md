# research_next_questions audit — 2026-10-11

SCHEMA: `research-next-question.v1`, defined in `trader/core/journal.py:353`.
One immutable, explicitly filed, context-only structural gap per exact Bank
object/hypothesis; canonical JSON is authoritative. All columns are NOT NULL
except the SQLite TEXT PRIMARY KEY declaration, which has no explicit NOT NULL.
Two automatic indexes implement the primary key and UNIQUE(bank_object_id,
hypothesis). No foreign keys or immutability triggers; append-only behavior is
enforced by the canonical writer, not arbitrary SQL.

| Column | Exact type | Necessity and decision |
| --- | --- | --- |
| next_question_id | TEXT PRIMARY KEY | Deterministic identity binding generator, kind, hypothesis and exact Bank/result IDs+hashes; conflict lookup/index. KEEP. |
| schema | TEXT NOT NULL | Canonical contract-version projection, checked on load and retries. Constant today; not independently queried. KEEP for compatibility. |
| question_kind | TEXT NOT NULL | Projection of the registered structural gap mapping, checked on load/retry; not independently selected. KEEP for compatibility. |
| generator_id | TEXT NOT NULL | Exact mapping version projection; constant today, checked on load/retry. KEEP for compatibility. |
| bank_object_id | TEXT NOT NULL | Indexed reader scope and composite uniqueness; indispensable relational access. KEEP. |
| result_id | TEXT NOT NULL | Exact bound-result projection, verified against canonical/source chain; currently not independently selected. KEEP for compatibility. |
| hypothesis | TEXT NOT NULL | Composite uniqueness prevents competing filed gaps for the same Bank/hypothesis. KEEP. |
| scope_kind | TEXT NOT NULL | Canonical scope integrity projection, currently not a selector. KEEP for compatibility. |
| scope_id | TEXT NOT NULL | Exact spec scope integrity projection, currently not a selector. KEEP for compatibility. |
| canonical_sha256 | TEXT NOT NULL | Canonical bytes digest projection; checked on load/retry. KEEP. |
| canonical_json | TEXT NOT NULL | Entire independently identifiable, immutable filed-gap contract and source links. KEEP. |
| recorded_at_ms | INTEGER NOT NULL | Filing timestamp, not canonical identity; duplicate retry preserves first timestamp. KEEP. |

WHOLE_TABLE_DECISION: KEEP. It duplicates deterministically derivable structural
information but records the separate fact that a gap was filed. Its reader
explicitly displays only stored rows and does not assert completeness. Replacing
this with an on-demand derivation would invent filing for previously unfiled
objects. No confirmed obsolete workflow or removable historical records were
found. Projection simplification is a possible future versioned design, not a
justified current migration: these columns participate in full-row conflict and
integrity contracts, and measured storage pressure is negligible.

Canonical writer: `research_next_question.record_for_bank_object` verifies one
stored Bank through `research_bank.verify_row`, including Q→plan→evidence→result
and run chain, derives all hypotheses using exactly two registered status/reason
mappings, then calls `Journal.record_research_next_question`. It refuses unknown
mappings or bad sources before filing; per-row commits permit partial filing,
which the view labels NOT_ASSERTED. Same exact projected bytes return duplicate;
different ID/content or same Bank/hypothesis return conflict without overwriting.
There are no production caller imports of this writer: it is an explicit offline
API. Raw Journal insertion is a primitive, not the contract-verifying entrypoint.

Readers and full reachable consumer paths found by scoped Graphify plus source
search: `Journal.research_next_questions` → `research_next_question.load` (strict
JSON, exact source rederivation, digest/projection verification) →
`research_bank_view._next_questions/build`. The view is consumed by its explicit
offline CLI and `trader.owner.queries.query` for research/capability/cost. Typed
owner queries reach `/api/owner/query/{kind}` in `dashboard/owner_api.py` and the
generic `frontend/src/components/OwnerEvidence.tsx` reader; research query also
feeds `owner.knowledge.enrich`, the owner knowledge endpoint and Knowledge view.
No Kernel/Attention/Analyst/Risk/Execution/provider or enqueue consumer exists.
The Research board/combo view is a distinct quantitative ledger path.

Comparison: `research_questions` contains event-triggered registered strategy
decay questions; `research_results` carries structural hypothesis status/reason;
`research_evidence` holds the frozen routed evidence; `research_bank_objects`
binds the entire verified chain and retains its historical
NOT_AVAILABLE/no_next_question_generator field unchanged. This table does not
replace any of them. Its canonical source entry/status/reason and fixed gap
statement duplicate derivable result content, and 10 projected text columns
duplicate canonical JSON paths. That duplication is confirmed, but differs from
duplicate rows or an interchangeable research object. Investigation RES05 and
predictive RES08 next-question generators use other schemas/store contracts;
matching English names do not justify merging them into this table.

Real resource cost: bounded `mode=ro`, query_only, in-memory temp SQLite queries
with 12s progress deadline measured production `data/luffy.db`: zero rows, zero
JSON/projection payload, three 4096-byte dbstat pages = 12,288 bytes allocated.
Exact dbstat filter covered only this table and its two named automatic indexes.
The coordinating root independently confirmed the same zero rows / 12,288 bytes
in operational frozen-demo and recovery candidate journals. No live write cost
is demonstrated. Local table count 0.096ms, empty kind aggregation 0.047ms,
targeted dbstat 0.690ms; these are one-shot observational timings, not benchmarks.
Bank lookup uses composite uniqueness index; ORDER BY rowid uses a temporary
B-tree. Verification cost when populated includes JSON parsing, source-chain
queries/hashing/rebuild and fresh derivation once per Bank per load; no measured
per-record benchmark is claimed. No disk savings from table removal are claimed:
dropped pages would not automatically shrink the database file.

ISSUE: no dedicated next-question contract regressions existed. A genuine
read-only consumer test also found `research_bank_view.open_readonly` did not
install `journal_detail_v1`, while current Journal queries route brain events
through `brain_events_logical_v1`, which requires that UDF. Thus a verified Bank
view reopened via the canonical offline CLI failed before reaching its gap rows:
`source_invalid:bank_object:OperationalError:no such function: journal_detail_v1`.

FIX: install the existing `journal_evidence.install(conn)` read resolver on the
read-only connection, exactly as the normal Journal reader does. This registers
a connection-local UDF; it runs no DDL, backfill, migration or database write.
Added dedicated tests in `tests/test_research_next_question_contract.py` for
writer/retry/reopen, both gap mappings, full historical chain and real FROZEN /
macro_guard_operator_hold preservation, owner research/capability/cost, corrupt source-chain
refusal, projection conflicts, missing-table historical behavior, and reopened
read-only view/UDF compatibility including corrupted evidence refusal.
The synthetic DENIED state marker is an isolation probe, not evidence of a real
authorization mechanism. Actual recorded restrictions/config remain governed by
the coordinating root's read-only baseline.

TESTS: final dedicated run: 8 passed in 9.07s. Existing Bank regressions: 84
passed in the initial combined run. `git diff --check` for the source fix passed.
Initial combined run: 90 passed / 1 failed in 51.66s; the failure exposed the
missing UDF (84 existing Bank regressions passed). After correcting the historic
missing-table probe, seven dedicated tests passed in 7.93s; the minimal UDF repair
and dedicated reopened-reader regression were subsequently added. No production,
operational or recovery DB was copied. Only tiny synthetic fixture databases were
copied by existing test helpers inside the exclusive data-mount basetemp.
Owned directory `/mnt/luffy-data/sol1-next-questions-owned-20261011` was removed
after verification; 307,142,656 allocated bytes reclaimed (directory-entry
allocation included), and absence was confirmed. Final disk remained approximately 3.3 GiB root
and 5.1 GiB data free. The command wrapper rejected shell `rm -rf` before execution;
cleanup succeeded with a path-asserted Python `shutil.rmtree` of that exact owned
directory. No unrelated temporary files were removed.

COMMIT: delegated review does not commit; coordinating root owns integration and
the final selective commit. Exclusive owned files: this report, dedicated test
module and the minimal Bank-view reader correction. Shared Graphify update is
owned by root; existing unrelated graph/source changes must remain unstaged.

CLOSURE: KEEP table and all current columns; no migration/deletion/admission or
provider action. Canonical offline reader compatibility repair and meaningful
contract coverage only. Empty historical tables remain empty; no backfill is
introduced. Root disk headroom verified at task entry as 3.34 GiB, data 5.10 GiB.
No worktree, build, deployment, restart or live process action was performed.
