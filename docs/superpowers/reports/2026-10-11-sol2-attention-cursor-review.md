# SOL2 attention_cursor review — 2026-10-11

SCHEMA:

`data/luffy.db` defines `attention_cursor(name TEXT PRIMARY KEY, value TEXT NOT NULL)` in `trader/core/journal.py:216`. It is a rowid table with `sqlite_autoindex_attention_cursor_1`; SQLite reports `name` as nullable because a non-integer primary key does not imply NOT NULL here. The canonical adapter never supplies a null name. Adding a constraint or converting to WITHOUT ROWID would require a migration for an 8 KiB empty table and is not worthwhile in this task.

| Column | Actual purpose |
| --- | --- |
| `name` | Namespace and unique lookup/CAS key. The sole canonical namespace is `registry-attention-selector` (`selection_persistence.CURSOR_NAME`). The Journal primitive accepts an explicit namespace, but no other production caller was found. |
| `value` | Next starting canonical InstrumentId for deterministic supplemental round-robin traversal, not the last fetched symbol or a trading admission. `None` means no row, never SQL NULL. `registry_selector.select` uses `bisect_left`, wraps traversal, and returns the next candidate ID; missing/delisted cursor IDs remain usable insertion points. |

WHOLE_TABLE_NECESSITY:

The active Kernel does not instantiate `RegistryAttention`, call the selection adapter, or perform registry supplemental selection. It initializes `Collector` at `trader/kernel.py:159` and captures the existing broad scan at `:1234`. Current `config.yaml:479` has no registry/manual supplemental settings. Searches of `trader/` and `scripts/` found no `RegistryAttention(...)` or `RegistryRefresher(...)` constructors. The surviving workflow is exercised directly by tests. The reviewed live cursor and selections tables are empty.

Therefore the entire table is unnecessary for the currently wired scan/admission flow. It remains necessary to preserve the retained registry supplemental contract until separately owned, unfinished registry-removal work establishes its final compatibility disposition. This review does not remove that owner's schema or implementation.

ISSUE:

The retained cursor/schema/API are disconnected persistence for an unused runtime workflow. Reconstructing the cursor from `attention_selections` would save only one bounded row and two pages while complicating atomic CAS, replay, restart and retention; it is not a worthwhile merge. Moving it to `attention.db` would split selection and cursor across databases and lose the present single-transaction guarantee. No proven defect in the cursor contract warrants a runtime correction.

Writer, reader and consumer trace:

1. `RegistryAttention.run` (`attention.py:461`) reads the injected provider's latest snapshot, then calls `selection_persistence.load_cursor` (`:157`). That adapter reads `Journal.attention_cursor` (`journal.py:974`); malformed canonical IDs fail closed and disable that slot for the process without repair.
2. Pure `registry_selector.select` (`:184`) consumes the cursor plus snapshot, cycle cut, freshness limit and full strategy scan. It excludes already covered scan symbols and maps only proven USD-M perpetual identifiers. It never fetches or grants trading permission.
3. `selection_persistence.record_selection` (`:127`) validates the selection and its SHA256 identity, then invokes the sole canonical writer, `Journal.record_attention_selection` (`journal.py:938`). Under BEGIN IMMEDIATE, it checks existing selection identity, checks the stored cursor against `cursor_before`, inserts the selection, and sets/deletes the cursor atomically. Identical duplicates do not write or rewind state; mismatching duplicates and stale writers are refused.
4. Only after commit may `RegistryAttention.run` perform the mocked-in-tests supplemental fetch and record its outcome separately. A failed fetch cannot undo cursor advancement or starve the next symbol. Its telemetry includes before/after cursor values and its returned capture provenance includes the selection ID, rather than another cursor copy.
5. `Journal.attention_cursor` has no other production consumer. `opportunity_context.py` reuses selection validation, not cursor reads. Dashboard, current portfolio, attention admission, scan-source publication and learning consumers read scan/evidence objects; no direct cursor consumer was found. Tests read/write the cursor to verify failure, concurrency and historical behavior. Existing evidence snapshots contain historical source copies, not runtime consumers.

Comparison and costs:

`attention.db` holds scans, candle versions, scan-version links, causes and scan-source evidence, not a registry rotation cursor. `Collector` receives already assembled broad frames, publishes evidence and applies separate retention. Configured scan retention is 512 scans, seven days and a 64 MiB main-file ceiling. Existing admission/deep-slot selection uses broad scan evidence, not this supplemental round-robin state. Neither active scan coverage nor those retention rules need this table.

Read-only measurement used SQLite URI `mode=ro`, `PRAGMA query_only=ON`, timeout 1 second and a progress-handler deadline of 15 seconds; no Journal was opened against the operational database and no migration/checkpoint/vacuum was performed:

| Measurement | Observed value |
| --- | --- |
| Cursor rows / user data | 0 / 0 bytes |
| Table allocation / root page | 4,096 bytes / 86 |
| Unique index allocation / root page | 4,096 bytes / 87 |
| Combined page payload / unused bytes | 0 / 8,176 |
| Empty cursor SELECT elapsed, after schema load | 1.623 ms (single observation; not a workload benchmark) |
| Lookup plan | SEARCH using `sqlite_autoindex_attention_cursor_1 (name=?)` |
| Latest selection cursor_after | No row |

Per retained workflow invocation, the cursor costs one indexed load and one indexed CAS read within the selection transaction. New committed selections also perform one INSERT OR REPLACE or DELETE; duplicates/conflicts do not mutate cursor state. No additional cursor transaction or provider call is needed. Current canonical storage is bounded to one row, so there is no time-based cursor pruning or append-history growth. Projected `cursor_before`/`cursor_after` and canonical JSON in selections duplicate historical cursor values intentionally; this table serves the authoritative current CAS state. Historical duplication growth belongs to selection history, not this bounded table. Current active Kernel cursor processing and duplication are zero.

Removing the empty table would make at most 8,192 bytes reusable inside luffy.db; it would not prove that many filesystem bytes reclaimed without separately authorized database compaction. No removal, compaction, relocation or retention change was attempted.

FIX_STATUS: DOCUMENTATION_ONLY

DECISION: REMOVE_CANDIDATE, conditional on completion and compatibility review of the separately owned registry-removal workflow. Preserve the table and APIs during this review.

TESTS:

`./venv/bin/python -m pytest tests/test_attention_selection_persistence.py -q -k "cursor or duplicate or matching or selection_row_and_cursor or failure_after_selection" --basetemp /tmp/sol2-cursor-pytest --cache-clear -o cache_dir=/tmp/sol2-cursor-cache` — **31 passed, 27 deselected in 4.38s**. These checks exercise atomic selection/cursor writes, rollback on cursor INSERT/DELETE failures, reopen persistence, missing/malformed values, selection advancement, stale/no-candidate unchanged state, CAS conflicts, two-Journal races, immediate-lock ordering, exact late replay without rewind, and fetch persistence failures leaving committed cursors intact. Only isolated test databases were written. Full direct registry workflow compatibility tests are owned by the sibling fetch-outcomes review and reused by the lead.

COMMIT: No independent commit; this report is handed to the SOL2 lead for an integration commit.

CLOSURE:

Cursor inventory and compatibility review complete. No runtime source, operational database, schema, service, provider, trading, FROZEN/operator-hold or research DENIED state changed. Owned disposable pytest directories are removed after results are recorded. Removal itself remains a documented candidate for the separately owned workflow cleanup; it is not implemented or claimed closed here.
