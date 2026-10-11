# SOL2 attention table integration review — 2026-10-11

LEAD: SOL2 native lead with three native agents, one per table. Reviewed source at `f579723685886e983b6383eb7174290bdb42dff1`; no runtime implementation was warranted within this task's ownership boundary.

| Table | SCHEMA | WHOLE_TABLE_NECESSITY | ISSUE | FIX_STATUS | DECISION |
| --- | --- | --- | --- | --- | --- |
| attention_cursor | Namespace and next instrument ID; full column inventory in [cursor report](2026-10-11-sol2-attention-cursor-review.md) | Needed only by retained supplemental compatibility; active Kernel does not use it | Dormant persistence, not a cursor correctness defect | DOCUMENTATION_ONLY | REMOVE_CANDIDATE |
| attention_selections | Fourteen columns storing canonical selection and its projections; [selection report](2026-10-11-sol2-attention-selections-review.md) | Dormant canonical writer; a shadow-script reader still uses selection provenance | Unbounded historical contract and redundant projections if reactivated; no measured live duplication | DOCUMENTATION_ONLY | REMOVE_CANDIDATE |
| attention_fetch_outcomes | Seven columns storing ordered attempts and selection linkage; [outcomes report](2026-10-11-sol2-attention-fetch-outcomes-review.md) | Retained direct-call compatibility only; no normal Kernel writer or production reader found | Dormant append-only attempt ledger; no retention policy if reactivated | DOCUMENTATION_ONLY | REMOVE_CANDIDATE |

All candidates depend on the separately owned unfinished registry-removal work. No DROP, migration, test rewrite, workflow removal, merge into attention.db, retention deletion, service restart, deployment, provider call or trading change was performed. Keeping the compatibility schema now costs very little. Moving cursor and selection into different databases would break their atomic transaction; attention.db serves retained scan/candle evidence and has different retention semantics.

## Read-only storage and historical evidence

The lead independently queried existing databases using SQLite URI `mode=ro`, `query_only=ON`, one-second lock timeout and progress-handler deadlines. Database inspection used sqlite3 directly, not Journal initialization. Initial broad dbstat OR filters exceeded the bounded deadline; narrower exact-name dbstat queries succeeded without changing any database.

| Existing database context | Path | Rows in each of the three tables | Allocation per table including its index |
| --- | --- | --- | --- |
| Production source | `production/data/luffy.db` | 0 | 8,192 B |
| Operational frozen epoch | `operational/frozen-demo-de6b35fc83e8/data/luffy.db` | 0 | 8,192 B |
| Legacy recovery candidate | `recovery/research-state-transfer-r1/candidate/luffy.db` | 0 | 8,192 B |
| Certified historical archive on dedicated recovery mount | `/mnt/luffy-recovery/recovery/historical-archive-20261009/databases/luffy.db` | 0 | 8,192 B |

Each table and index occupy one 4,096 B page. In the first three contexts, each has zero payload and 4,088 B unused per page. Total allocation is 24,576 B per database, 98,304 B across four existing contexts. Dropping tables would make pages reusable within SQLite; these values are not filesystem reclamation estimates. Shared sqlite_sequence and database/WAL overhead cannot be exclusively attributed to this review's tables. Empty tables do not establish nonempty historical conversion compatibility: no conversion was attempted, and synthetic/reopen contract tests provide the nonempty evidence.

The operational database still records `control_state=FROZEN`, `macro_guard_operator_hold=1`, and `research_epoch_authority.v1.status=DENIED`. Production source retains FROZEN and the operator hold. This task did not write control state, config, research authority or operational databases.

## Focused verification

TESTS: **203 passed in 36.28s**, covering `tests/test_attention_selection_persistence.py`, `tests/test_attention_kernel_wiring.py`, `tests/test_opportunity_context.py` and `tests/test_opportunity_live_integration.py`. Reused the selections agent's combined result for integration instead of rerunning. Additional bounded cursor subset: 31 passed, 27 deselected; fetch subset: 10 passed, 94 deselected; direct registry suite: 46 passed. Those counts overlap the combined 203 and are not additive.

These isolated fixture tests cover canonical integrity, projections, foreign-key linkage, atomic cursor/selection writes, CAS and replay ordering, reopen persistence, failed writes, separate fetch outcomes, mocked transport boundaries and downstream context compatibility. No provider calls were made. The passing `test_no_kernel_wiring` guard records ImportFrom module names, not imported aliases, so it does not itself prove absence of `from trader.observability import selection_persistence as sp`. Actual source/call-site tracing establishes current Kernel disconnection. No registry-owned architectural test was changed.

INTEGRATION_CONFLICTS: None introduced. Existing dirty graph outputs and unrelated untracked files were preserved. Concurrent Sol1 tests/processes were observed and left running. No Sol1, Opus or unfinished registry-removal source was edited. All changes in the integration commit are these four review documents. No graphify update is needed because no code changed; graph queries were used and their pre-existing shared outputs remain outside this commit.

SPACE_RECLAIMED: No operational database space reclaimed. Cursor agent removed only its owned disposable pytest/cache directories: 37,006,973 logical bytes; allocated blocks were not measured, so that number is not an exact filesystem-space claim. The lead additionally verified and removed only task-owned pytest runs 197, 198 and 199 under `/tmp/pytest-of-sarmad`, measuring **155,729,920 allocated bytes (148.52 MiB)** before deletion, plus run 199's matching current symlink. Shared parents, older runs and unrelated files remain. Cursor logical cleanup is additional and is not included in that allocated-byte figure. Existing unowned temporary directories, recovery artifacts and concurrent agents' files were preserved.

ROOT_FREE: Initial 3,548,471,296 B on `/dev/sda2` (3.30 GiB).

OPERATIONAL_FREE: Initial 5,424,304,128 B on `/dev/sdb1` (5.05 GiB).

RECOVERY_FREE: Dedicated `/mnt/luffy-recovery/recovery` initially 86,810,742,784 B on `/dev/sdc1` (80.85 GiB). The legacy `/mnt/luffy-data/luffy/recovery` directory shares `/dev/sdb1` with production and operational paths and has the same 5.05 GiB headroom; it is not an independent recovery capacity. No full database copies were created.

COMMIT: The dedicated documentation integration commit containing this file and the three linked table reports; its hash is recorded in the final response.

CLOSURE: Each table's review, measurements, consumer trace and focused compatibility checks are complete. Each removal remains proposed and owned by registry-removal closure. This task is documentation-only and does not claim that schema removal, deployment or registry-removal closure occurred.

Final ROOT_FREE: 3,526,877,184 B (3.28 GiB), measured after owned cleanup.

Final OPERATIONAL_FREE: 5,424,132,096 B (5.05 GiB), measured after owned cleanup.

Final RECOVERY_FREE: 87,765,508,096 B (81.74 GiB), measured after owned cleanup.
