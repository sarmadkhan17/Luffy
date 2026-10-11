# SOL2 — attention_selections review

Date: 2026-10-11. Scope: this table only; offline inspection and fixture tests. Production databases opened with SQLite `mode=ro`, `query_only=ON`, bounded query deadlines. No migration, deployment, provider call, restart, trading/configuration change, or registry-removal implementation. Graphify query ran first; its broad truncated result was supplemented with scoped source searches. Working HEAD observed: `f579723685886e983b6383eb7174290bdb42dff1`.

SCHEMA:

`trader/core/journal.py:198` declares 14 columns. `selection_id` is the TEXT primary key; SQLite does not implicitly enforce NOT NULL for this rowid-table TEXT key, although the adapter requires a valid SHA-256 ID. Selected/cursor fields are nullable; all other columns are explicitly NOT NULL. Its primary-key SQLite index is the only index. `attention_fetch_outcomes.selection_id` references it.

| Column | Actual purpose and consumers |
| --- | --- |
| selection_id TEXT PRIMARY KEY | SHA-256 of authoritative canonical JSON. Idempotent/conflicting replay key, fetch-outcome parent, and provenance reference carried into supplemental capture. Reader verifies hash. |
| rule_version TEXT | Selector rule version projection; validated against canonical payload and exposed in opportunity evidence. |
| cycle_as_of_ms INTEGER | Selector decision cut. Part of hashed identity, reconstruction and opportunity evidence. Offline shadow orders newest selections by this value. |
| outcome TEXT | `selected`, `snapshot_stale`, or `nothing_selectable`. Persists meaningful non-selections as well as selections; fetch proceeds only for `selected`. Compared with payload on read. |
| snapshot_id TEXT | Immutable registry snapshot identity; provenance link, hash input and opportunity evidence. Does not store the snapshot itself. |
| snapshot_as_of_ms INTEGER | Registry observation cut, freshness evidence and shadow source expiry calculation. |
| registry_age_ms INTEGER | Age at selector cut (`cycle_as_of_ms - snapshot_as_of_ms` from canonical selector). Reader integrity projection; no separate live SQL consumer. |
| max_snapshot_age_ms INTEGER | Freshness policy used for this selection; reconstruction and shadow source expiry. |
| selected_id TEXT nullable | Canonical selected instrument identity, absent for non-selection. Shadow can bind instrument identity; opportunity validation checks mapping. |
| selected_symbol TEXT nullable | Supplemental fetch symbol; shadow matches scan symbols via venue key. Absent for non-selection. |
| cursor_before TEXT nullable | Cursor input, including absent initial cursor. Atomic writer compare-and-swap refuses stale competing selections. Duplicate detection and read integrity include it. |
| cursor_after TEXT nullable | Durable next cursor, unchanged for non-selection. Atomic writer sets/deletes named cursor with selection insert before fetch begins. |
| canonical_json TEXT | Authoritative full Selection, canonical byte representation, hashed identity and verified read reconstruction. Also stores candidate IDs, exclusions, selected account eligibility and unmatched scan symbols that have no SQL projections. Offline shadow freezes it as registry provenance. |
| recorded_at_ms INTEGER | Caller-supplied persistence availability clock, not a database-generated commit timestamp. Excluded from selection identity/idempotence comparisons; preserved on duplicate replay. Shadow rejects records unavailable at its cut. |

WHOLE_TABLE_NECESSITY:

The table is necessary **if the retained registry supplemental workflow is used**: it atomically couples selected provenance with cursor advancement, provides the fetch parent, and permits integrity-checked historical reconstruction. It is not necessary to the current kernel scan/selection workflow. Production measurement found zero rows. No runtime call site constructs `RegistryAttention`; its retained writer exists as a directly callable boundary and in tests. A zero-row dormant table is not enough evidence to remove its contract while unfinished registry removal has another owner.

ISSUE:

1. Dormant registry persistence is still installed in every Journal schema despite no current kernel wiring. `tests/test_attention_kernel_wiring.py:3` explicitly describes removal of legacy Kernel hooks. This is a coordinated removal candidate, not a reason to reconnect or expand observation.
2. Canonical JSON deliberately duplicates 12 projected fields (all except the identity hash and recorded availability time). They are checked in duplicate replay and read integrity; several are used by the shadow reader. Removing projections requires coordinated writer/reader changes and historical compatibility, not merely dropping apparently unused SQL fields.
3. No row/age/byte pruning exists for this append-only selection ledger. If reintroduced, every cycle has a different cut/hash, and candidate/exclusion lists repeat across cycles. It would grow with cycles and registry population. No current growth was observed.
4. The offline shadow's `ORDER BY cycle_as_of_ms DESC LIMIT 16` has a table scan plus temporary sort. Its five-second deadline bounds work; LIMIT bounds returned rows, not scanned rows. Adding an index now would cost space for a dormant empty workflow and is not worthwhile.
5. `snapshot_id` does not establish replayable durable registry snapshots: the pure selector documents that its durable snapshot store does not exist. Selection JSON retains candidate/exclusion evidence but does not reconstruct all snapshot facts.

Canonical writers, readers and consumers:

- `registry_selector.select` creates the immutable Selection, excluding symbols covered by the complete strategy scan and rotating one supplemental observation slot. Account eligibility is recorded state and never grants trading permission.
- `RegistryAttention.run` in `observability/attention.py:461` reads durable cursor, selects, calls `selection_persistence.record_selection`, and only after a successful insert fetches. `record_attention_selection` in `core/journal.py:938` does duplicate/conflict detection and atomically inserts selection plus cursor under its transaction. Later outcome commits are independent.
- `Journal.attention_selection` reads by primary key. `selection_persistence.load_selection` validates SHA-256, canonical round-trip, allowed selection contract and every projection. Source search found no current trader/scripts caller of `load_selection`; tests exercise it extensively.
- `scripts/opportunity_context_shadow.py:89` is the sole direct non-test SQL reader found: optional table check, newest 16 rows, symbol/availability match, instrument identity and canonical registry source. It does not require this role for ordinary attention-only contexts; absent table already produces an empty selection inventory.
- `attention.capture` can carry `{mode, selection_id, snapshot_id}` in supplemental provenance into attention scan payloads; this is an indirect historical consumer of identity, not a SQL reader.
- `cognition/opportunity_context.py:_selection` consumes an injected Selection as optional evidence, validates matching symbol/identity/current cut, and exposes provenance. It does not itself read this table or require persisted registry selections for normal attention evidence.
- `attention_fetch_outcomes` foreign-key parent contract and associated Journal methods depend on this table. Existing tests are contract consumers.
- `learning/consumers.py:attention_context` uses the label `attention_selection` for learned context; it does not query `attention_selections`. The similar name is not a table dependency.
- Persistence backup includes `data/luffy.db`; therefore any future rows are copied as journal history, in addition to independently retained attention evidence.

Comparison with attention.db and current selection:

Current Kernel uses `Collector.begin` on already observed broad frames and membership receipts, then collector cause packets. `Collector.begin` calls `capture` without supplemental arguments. Current config enables admission and attention capture, but has no registry/manual supplemental setting. Deterministic attention admission and investigation allocation operate on observed scans and their own receipts; this table is not their allocation ledger.

`attention.db` stores scans, candle versions, scan-version membership, causes, compressed evidence blobs and source receipts. Its ranked/selected scan rows describe salience/investigation observations; they are distinct from a rotating registry symbol chosen outside strategy coverage. It neither provides a durable registry cursor nor replaces the atomic selection/cursor commit. Its configured pruning is 512 scans, seven days and a 64 MiB main-file ceiling; those limits do not govern this luffy.db table. Merging would require cross-database atomicity or moving the coupled cursor/outcome contract as a unit, and would mix pruning policies. No merge is justified.

Measured costs:

| Measure | Evidence |
| --- | --- |
| Production rows / canonical payload / duplicates | 0 / 0 bytes / 0 rows. No historical selection to compare across production scans. |
| Table allocation | 1 page, 4096 bytes, 0 payload, 4088 unused bytes. |
| Primary-key index allocation | 1 page, 4096 bytes, 0 payload, 4088 unused bytes. |
| Total attributed allocation | 8192 bytes. Dropping alone does not shrink a SQLite file or reclaim filesystem bytes. |
| Production count query | Approximately 4.5 ms at inspection; subsequent empty-table queries approximately 0.1 ms. |
| Fixture duplication | Three-candidate selection: canonical JSON 605 bytes, projected non-JSON textual values 266 bytes. The latter includes 64-byte identity; approximately 202 bytes mirror payload values. This is a synthetic value-length measure, not SQLite on-disk overhead or a live registry forecast. |
| Fixture CPU | Selection validation plus canonical reconstruction averaged 111.6 microseconds over 1000 repetitions in this environment. No provider processing or runtime cycle benchmark. |
| Shadow query plan | `SCAN attention_selections`; `USE TEMP B-TREE FOR ORDER BY`. |
| Retention / processing | No selection pruning; one transaction per inserted registry selection plus later fetch outcome transaction. Canonical serialization/hash/round-trip and repeating candidates/exclusions scale with registry population. Current observed table growth is zero. |

Lead independently found this table empty in the operational frozen-demo copy and named recovery candidate, each with the same 8192-byte allocation. Dedicated `/mnt/luffy-recovery/recovery` differs from legacy recovery under `/mnt/luffy-data`; legacy recovery shares operational mount and does not create extra headroom. Lead owns the definitive headroom/cleanup report.

FIX_STATUS: DOCUMENTATION_ONLY

DECISION: REMOVE_CANDIDATE

Remove only through the existing registry-removal owner after resolving retained callable boundary, atomic cursor/outcome relationship, optional shadow evidence and historical readers/backups. Keep schema and API compatible today. No generic retention policy, projection removal, index, new writer wiring or database mutation was justified by the measured empty table. This recommendation does not overlap implementation of that unfinished work.

TESTS:

`./venv/bin/python -m pytest -q tests/test_attention_selection_persistence.py tests/test_attention_kernel_wiring.py tests/test_opportunity_context.py tests/test_opportunity_live_integration.py` — **203 passed in 36.28 seconds** on this agent's working snapshot. This included `test_no_kernel_wiring`; no test exclusion was used. Coverage includes transactional insert/cursor rollback, duplicate replay without rewind, cursor compare-and-swap, reopening/subprocess durable state, corruption/projection/hash refusal, separate fetch outcomes, explicit boundary capture ordering, old supplemental-mode spelling compatibility, optional registry provenance and opportunity identity/freshness constraints. Live history compatibility is vacuous for the measured zero-row source; fixture persistence and integrity contracts remain passing. No new tests or runtime edits.

The passing `test_no_kernel_wiring` is not an exhaustive wiring proof: its import helper records `ImportFrom.module`, so `from trader.observability import selection_persistence as sp` is recorded as `trader.observability`, not the imported submodule alias. The no-runtime-writer conclusion relies on the explicit call-site and kernel/collector trace above. No stale guard edit was made because that touches registry-removal scope.

COMMIT: Lead integration pending; this agent did not commit. No graphify update required for this documentation-only review; no code was modified.

CLOSURE: Table review complete; retained contracts verified offline. Removal remains explicitly proposed and gated on registry owner coordination. No service/provider/trading action taken. FROZEN, operator hold and research DENIED preserved; definitive control/headroom checks belong to lead. The test command above used default pytest temporary storage (no TMPDIR override or --basetemp). Its task-owned fixture run was `/tmp/pytest-of-sarmad/pytest-199`, timestamp 13:18, also the resolved pytest-current target at verification. All 147 real fixture directories matched test-name prefixes from the four suites; no unmatched fixture names. Lead measured and removed this unique run, reclaiming 113,688,576 allocated bytes, and removed its matching current symlink. No database copies or other disposable files were created by this agent. Agent-direct space reclaimed: 0 bytes; lead reports actual fixture cleanup separately.
