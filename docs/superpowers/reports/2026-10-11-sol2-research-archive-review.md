# SOL2 — research_archive review (2026-10-11)

Research remains DENIED. Audit and synthetic compatibility tests only; no real research run, provider request, collection, operational write, trading change, restart or deployment. Native agent assigned exclusively to this table. Claude execution availability was checked by the lead and returned `Not logged in`; this work uses the explicitly requested native-agent fallback. Sol1 and unrelated preserved work were untouched.

SCHEMA:

Canonical definition: `trader/research/ledger.py:82`; operational SQLite schema matches exactly.

| Column | Type / constraints | Purpose |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Stable archive row identity; aliases SQLite rowid and prevents reuse of committed deleted IDs. No separate index. |
| `tf` | TEXT, nullable, no default | Horizon of the source rows, e.g. `4h`; enables horizon-specific historical inspection. |
| `vintage` | TEXT, nullable, no default | Caller-supplied identifier for the old measurement generation. CLI currently supplies an ISO UTC timestamp immediately before remeasurement. |
| `table_name` | TEXT, nullable, no default | Distinguishes archived `research_combos` from `research_controls`; these are the sole canonical writer values, though no CHECK constrains them. |
| `row_json` | TEXT, nullable, no default | JSON object containing every source SQL column and value, generated from cursor column names. Preserves the stored source row including nested JSON strings and SQL nulls. No archive-specific truncation. |
| `archived_at` | TEXT, nullable, no default | One ISO UTC archival timestamp shared by rows moved in a single invocation. Separate from caller-defined vintage. |

No foreign keys, secondary indexes, triggers or views reference this table in the inspected operational database. Nullable historical shape is preserved. AUTOINCREMENT uses the database-wide `sqlite_sequence`; its shared page is not charged wholly to this table.

WHOLE_TABLE_NECESSITY:

Challenged removal: the table is empty and has no automated production reader, so it contributes no current strategy or dashboard functionality. Nevertheless, its canonical writer preserves the only old combo/control row copies before deletion during explicit rekey. Removing it would discard those historical records, or break the existing rekey path. A lack of active readers does not establish that an append-only audit record is dispensable. Its current 4 KiB page offers negligible savings; merging it would introduce migration and compatibility risk without a demonstrated benefit. Keep the whole table and its current format.

ISSUE:

No proven current defect warrants code or database mutation. Future growth is unbounded: there is no expiry, deduplication or purge path. `archive_horizon()` fetches all matching source rows into Python memory and serializes each row while holding a write transaction; peak memory grows with the largest source-table horizon and transaction time with total archived bytes. Historical limitation: it archives combos and controls, not the old gauges or slices, so it preserves source rows rather than a complete reproducible measurement snapshot. CLI subsequently deletes gauges in another transaction. Correcting that would require a deliberately scoped research protocol/schema review, not an incidental table cleanup while research is denied.

DUPLICATION:

`row_json` duplicates source values only during the same transaction; after success, the source horizon rows are deleted. Current `research_combos`/`research_controls` therefore do not duplicate the archive's historical function. `research_tests`/`research_candidates` preserve other referee records, not copies of these complete old rows. `research_unreadable_*` stores preserve different quarantined objects and are outside this agent's ownership. Whole-database copies naturally include the archive but are not a versioned row archive substitute. No equivalent canonical per-vintage historical combo/control store was found.

FIX:

No code correction or index added: an empty table with no production query workload does not justify extra write/storage cost. This report supplies the previously missing table-level contract and bounded cost evidence. No operational database writes, copies, vacuuming, retention changes or deletions occurred.

Canonical WRITER, READER and ALL CONSUMERS:

- WRITER: `Ledger.archive_horizon(tf, vintage)` at `trader/research/ledger.py:321`. In one `Journal._tx()` it selects all horizon rows from combos and controls, inserts their full JSON objects into the archive, then deletes their source rows. Canonical production caller: `trader/research/__main__.py:152`, reached only by explicit `--measure --rekey` when the horizon already has combos. This command was not run.
- READER: no dedicated production archive reader or restoration API exists. `Journal.query()` is the generic SQL access primitive, used by the two direct regression-test consumers below. The writer does not read previous archive rows.
- ALL discovered direct consumers: `tests/test_research_ledger.py:150` tests the move of both source table types; `tests/test_research_cli.py:87` tests CLI rekey archival. `scripts/candidate_dossier.py:63` lists the archive in `NOT_READ`, and its SQL authorizer does not allow this table, so it is an explicit non-consumer. Dashboard/owner research views read current combos/controls/batches/candidates/tests and do not read archive rows. CLI consumes returned moved counts to print its report, not archive rows.
- Verification: Graphify query first, then focused `graphify explain archive_horizon`, followed by exact text searches across tracked and nonignored source/script/test/frontend/unit files. All direct table/method references are the schema, writer, CLI caller, these two tests and dossier denial. Exact operational schema inspection found no hidden SQL trigger/view consumer. External ad-hoc SQL users cannot be ruled out by repository inspection.

Storage, processing and retention costs:

- Production `data/luffy.db`, URI `mode=ro`, `PRAGMA query_only=ON`, memory temp storage and bounded progress deadline: **0 rows**, **0 JSON payload bytes**, no vintages or archive timestamps. Exact `dbstat` name lookup: **1 page / 4,096 bytes**, payload 0, unused 4,088 bytes. No secondary indexes.
- Lead independently measured operational/recovery database copies: each likewise 0 rows / 4,096 bytes; three-copy sum **12,288 bytes**. Copies are distinct storage, not evidence of duplicated logical table responsibilities.
- Production empty-table aggregate took approximately 0.05 ms; exact page lookup approximately 0.16 ms. These are one-shot audit timings, not load benchmarks. `EXPLAIN QUERY PLAN` shows archive horizon queries scan the archive; the source combos selection uses `idx_research_combos_pick(tf, ...)`. With zero archive rows and no production reader there is no measured benefit from an archive index.
- Per successful rekey: one INSERT per archived combo/control plus horizon DELETEs; JSON bytes, SQLite/WAL traffic and backup cost grow with preserved source rows. The source row set is fully materialized in Python, so temporary memory is O(largest selected source-table horizon). No real-data rekey or benchmark was authorized or executed.
- Retention is indefinite by implementation. Historical records must be preserved; no disposable archive rows or reclamation opportunity were established. Dropping an empty table does not establish that filesystem space would be returned without further database maintenance.

DECISION:

**KEEP**. Retain historical compatibility and the transactional preservation function; no evidence supports SIMPLIFY, MERGE or REMOVE_CANDIDATE now.

TESTS:

Before tests, root free was **3,562,741,760 bytes**, above the required 2 GiB guard; operational/recovery mount free was **5,314,838,528 bytes**. Tests used `./venv/bin/python`, recovery `TMPDIR` and `--basetemp`, `PYTHONDONTWRITEBYTECODE=1`, and disabled pytest cache. Only synthetic databases and monkeypatched CLI runners were used.

Command:

```text
TMPDIR=/mnt/luffy-data/luffy/recovery/sol2-next-three-archive PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python -m pytest tests/test_research_ledger.py::test_measure_may_be_refused_until_the_horizon_is_archived tests/test_research_cli.py::test_measure_refuses_a_horizon_that_already_holds_scored_combos tests/test_research_cli.py::test_measure_rekey_archives_then_proceeds --basetemp=/mnt/luffy-data/luffy/recovery/sol2-next-three-archive/pytest -p no:cacheprovider -q
```

Result: **3 passed in 8.09s**. Covers historical combo/control move, refusal without rekey, and mocked CLI rekey compatibility. These existing tests are appropriate to the documentation-only decision; no additional runtime testing or real evaluation was needed.

COMMIT:

No agent commit or staging. Sole owned repository artifact is this report; lead owns integration and final commit reporting. No code changed, so no code Graphify update is necessary for this agent's work.

CLOSURE:

Table audit closed; historical schema and writer semantics unchanged. Owned recovery pytest files were removed after completion: 9 files, **4,185,368 logical bytes / 4,190,208 allocated bytes**. Owned scratch directory is empty; no other agent or preserved work was cleaned. No deployment. Bootstrap timer diagnosis and final aggregate capacity/commit evidence are lead-owned.
