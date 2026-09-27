# LUFFY-CANDLE-STORE-TRANSACTION-SAFETY-V1

Date: 2026-09-28 · Implementer: Opus (Claude Code) · Reviewer: Astra (pending)
Worktree: `/home/sarmad/trader/.claude/worktrees/candle-txn-safety`,
branch `candle-txn-safety-v1` from `f74c8b8`. **Not committed, not deployed.**
Production untouched: no restart, no watchdog change, no venue or production-DB
access (the worktree has no `data/candles.db`; every test and rehearsal used a
temporary database). Production `data/candles.db` was opened once, **read-only**
(`mode=ro`), to read `PRAGMA journal_mode` → `delete`.

**Revision 1 (same day): fixes for Astra's two blockers.** See the section
*Revision 1* below. It supersedes the `abort`/`discard` description in B, the
test totals in G and returns B, C, G, H and M. Sections D–F and the incident
boundary are unchanged.

## A. Exact transaction-cleanup defect

Python `sqlite3` (legacy isolation) opens a transaction implicitly at the first
INSERT. All three `candles.db` writers did `execute[many]` then `commit()` on a
**thread-local connection that is reused forever**, with no rollback on failure:

| Writer | Begin | Commit | Exception path (before) | Connection reuse |
|---|---|---|---|---|
| `DataFeed._store_save` | implicit at `executemany` INSERT | `self.db.commit()` | `except Exception: log.warning` — **no rollback** | same thread-local conn, forever |
| `DataFeed._note_floor` | implicit at `execute` INSERT | `self.db.commit()` | `except Exception: log.warning` — **no rollback** | same |
| `RefStore.save` | implicit at `executemany` INSERT | `self.db.commit()` | **no handler**; raises to `refresh_all.run`, which logs — **no rollback** | same (its own thread-local) |
| `DataFeed.db` / `RefStore.db` (schema) | DDL (no implicit BEGIN) | `conn.commit()` | conn unreferenced, left to GC | stored only on success |

When `commit()` fails with `database is locked` (it waited the 5 s default busy
timeout for other readers to drain), SQLite leaves the transaction **open** and
the connection keeps the **PENDING** lock it took while trying to commit. In
DELETE journal mode a PENDING lock refuses every new SHARED lock, so **every new
reader and writer on every connection fails**, and this continues after the
original contender has gone. The poisoned connection stays in `threading.local`
and releases the lock only when that same thread happens to commit again. That
next commit also commits the batch whose caller was told it failed.

Reproduced on a temp DB with plain `sqlite3`. A writer's commit fails while
another connection holds SHARED. That holder then commits and closes. After
that, a fresh reader fails with `database is locked` and a fresh writer fails
with `database is locked`. Both recover only after the stuck writer rolls back.

Other `candles.db` openers are offline `scripts/` tools (`deepen_candles.py`,
`backfill_outcomes.py`, `universe_extension.py`,
`rebuild_candles_from_production.py`, `repair_partial_bars.py`). They are out of
scope and not changed. Any of them run concurrently with the kernel could be a
contender.

## B. Exact production-code changes

- **New `trader/data/sqlite_tx.py`** contains:
  - `write_tx(local, conn, label)`: a context manager that runs the body, then
    `commit()`. On any `BaseException` in the body or the commit, it calls
    `abort()` and re-raises the original exception.
  - `abort()`: if `conn.in_transaction`, calls `rollback()`. If the rollback
    raises, or leaves `in_transaction` still true, it logs `error` and calls
    `discard()`. `abort()` never raises.
  - `discard()`: `close()`s the connection, which releases its locks, and clears
    `local.conn` only if it is that same connection. The next `.db` access
    reconnects.
  - `BUSY_TIMEOUT_S = 5.0`: sqlite3's existing default, now stated explicitly.
    It is **not increased**.
- **`trader/data/feed.py`**:
  - `_store_save` and `_note_floor` write inside `write_tx`. Their existing
    `log.warning` handlers are unchanged, so warnings stay visible.
  - `db` passes `timeout=BUSY_TIMEOUT_S` (unchanged value) and closes the new
    connection if schema setup raises.
  - `DataFeed.BUSY_TIMEOUT_S` is a class attribute so tests can shorten it.
- **`trader/data/references.py`**:
  - `RefStore.save` writes inside `write_tx`. It still raises to the caller,
    and `refresh_all` still records `error: …` per key.
  - `db` gets the same timeout and close-on-setup-failure handling.

No retry was added. Once the rollback runs, the lock is gone. The writes are
idempotent `INSERT OR REPLACE` on the primary key, and the next refresh repeats
them anyway: the stored tail stays older, so the incremental refresh re-fetches
the missing bars. A retry would only add more time blocked in the trading loop.
Read paths (`_store_load`, `_floor`, `load`, `last_ts`) open no transaction and
are unchanged.

## C. Failure-injection results (`tests/test_candle_store_transaction_safety.py`)

Faults are injected either with a **real lock** (another connection holding
SHARED, so the INSERT succeeds and the COMMIT fails `database is locked`) or
with a delegating `FaultConn` wrapper. The wrapper is needed because
`sqlite3.Connection` methods are read-only.

| Case | Test | Result |
|---|---|---|
| A commit locked (real lock) | `test_real_commit_lock_rolls_back_and_releases` | rolled back, `in_transaction=False`, fresh reader OK while the holder is still present, 0 rows | PASS |
| A commit locked (injected) | `test_injected_commit_lock_rolls_back` | rolled back, connection kept | PASS |
| B error after INSERT | `test_error_after_insert_rolls_back` | rolled back, error logged | PASS |
| C rollback raises | `test_failed_rollback_discards_connection` | connection closed and dropped, both errors logged, next save writes 5 rows on a new connection | PASS |
| C′ rollback leaves txn open | `test_rollback_leaving_transaction_open_discards` | discarded | PASS |
| original exception re-raised | `test_write_tx_reraises_the_original_error` | PASS |
| D another writer proceeds | `test_other_writer_proceeds_after_a_failure` | PASS |
| E repeated failures | `test_repeated_failures_hold_nothing` (5×) | nothing retained after each failure | PASS |
| F success after failure | `test_success_after_failure_does_not_commit_the_failed_batch` | failed batch absent; the retry lands exactly once | PASS |
| G RefStore | `test_refstore_failure_raises_and_leaves_nothing_open`, `test_refstore_failed_rollback_reconnects` | PASS |
| H note-floor | `test_note_floor_lock_rolls_back`, `test_note_floor_failed_rollback_reconnects` | PASS |
| schema-setup failure | `test_connect_failure_keeps_no_connection` | PASS |

## D. Contention results

`test_contention_recovers_after_a_held_write_lock` uses a temp DB and a 0.2 s
busy timeout. It runs 2 reader threads (`_store_load`), 2 candle-writer threads
(`_store_save`) and 1 reference-writer thread (`RefStore.save`). A separate
connection holds `BEGIN EXCLUSIVE` for 1 s, then releases it, and the workers
run for another 1 s. The test asserts:

- every path failed at least once while the lock was held;
- every path succeeded after release;
- no worker connection was left `in_transaction`;
- every thread joined within a bounded timeout;
- a fresh `BEGIN IMMEDIATE` succeeds afterwards.

PASS.

## E. Connection-reset behaviour

- `sqlite3.connect` keeps its default `check_same_thread=True`, so a
  connection cannot be used from any thread except its owner. It raises
  `ProgrammingError` rather than being shared.
- Each `DataFeed` or `RefStore` instance has its own `threading.local`.
- A connection is replaced only when rollback fails or leaves a transaction
  open. The reset is bounded: one `close()` and one reconnect on the next
  access, with no loop.
- A connection whose rollback succeeds is kept, because it is healthy.
- A connection that fails schema setup is closed and never stored.

## F. Duplicate / partial-write safety

- PK semantics are unchanged: `(symbol, tf, ts)`, `(key, ts)` and
  `(symbol, tf)` with `INSERT OR REPLACE`.
- The contention test asserts zero duplicate `(symbol, tf, ts)` groups, and
  candle and ref row counts within the distinct-key bound.
- A failed batch is never committed later (test F). On the original code the
  same test commits the failed 5-row batch alongside the next save.
- Repeated saves of the same bars give the same row count (tests F and G).
- A batch commits whole or not at all; there is no partial commit.

## G. Focused test totals

| Set | Result |
|---|---|
| New `test_candle_store_transaction_safety.py` | **15 passed** |
| New tests + existing store, reference and feed tests: `test_alts_and_refresh`, `test_candle_cache_reuse`, `test_candle_store_symbol_keys`, `test_dsl_ref`, `test_feed_pagination`, `test_forming_bar_never_stored`, `test_market_data_is_production`, `test_reference_store`, `test_ref_sources`, `test_research_job`, `test_taker_buy_real`, `test_single_creation_path` | **91 passed** |
| Kernel and universe tests that use the feed: `test_kernel_boot_recovery`, `test_declared_universe_is_traded`, `test_universe_frames_timeframes`, `test_universe_wiring`, `test_research_universe_depth`, `test_feature_ctx_universe` | **35 passed, 2 skipped** (the skips need `data/candles.db`, which the worktree deliberately lacks) |

The full repository suite was not run.

## H. Negative-control results

The same tests were run with `feed.py` and `references.py` restored to `HEAD`,
then the fix was restored:

- **13 failed, 2 passed.**
- Both passes are expected:
  - `test_write_tx_reraises_the_original_error` tests only the new helper.
  - `test_connect_failure_keeps_no_connection`: the old code already stored the
    connection only after setup succeeded.
- Typical failures on the old code:
  - `in_transaction` is still `True` after the failed commit;
  - the failed batch is committed later (`assert 5 == 0`);
  - `database is locked` for the next caller;
  - the connection is not discarded.
- Caveat: on the old code the contention test fails for a **different
  reason**. Its 5 s timeout outlasts the 1 s hold, so callers waited instead of
  failing. It checks recovery and boundedness, not the defect; tests A–H are
  the ones that detect it.

## Production-safe rehearsal (item 11)

Temp DB, production timeout (5.0 s), `journal_mode=delete`, fixed code:

```
WARNING trader.data.feed: candle store write BTC/USDT 15m: database is locked
failed save returned after 5.04s
writer conn in_transaction: False
fresh reader while original holder still present: 0      (not blocked)
fresh reader after holder released: 0
next save -> rows: 8 in_transaction: False
ref save rows: 3 in_transaction: False
```

## Live-cycle relevance (item 8), retained evidence only

- `candles.db` journal mode is `delete` (read-only PRAGMA). No `timeout=` was
  passed before, so it used sqlite3's default of 5.0 s.
- `logs/luffy.log` has **73** candle-store lock warnings: 36 `candle store
  write` and 37 `candle store read`. **All** are on 2026-09-27 between 23:24:06
  and 23:30:26, about 5 s apart, and the reads fail as well as the writes.
- The episode sits between `LUFFY BOOT` at 23:21:54 and the next `LUFFY BOOT`
  at 23:31:37. It started about 25 s after `references refreshed: 15 ok`
  (23:23:41). It stopped only when the process was replaced.
- This fits a PENDING lock held in-process by a stuck thread-local connection:
  readers blocked and the episode ended at process exit. The pre-fix code
  demonstrably produces this pattern. It is **consistent with** the evidence,
  **not proven** to be what happened.
- The separate `research step failed` / `rent check failed: database is locked`
  warnings (2026-09-22 onward, about hourly) were not attributed to
  `candles.db` and are out of scope.

## Heartbeat (item 9)

Heartbeat and watchdog thresholds are unchanged. A contended candle write can
still block its calling thread for up to the 5 s busy timeout per statement, so
many serial waits in one cycle can still delay the heartbeat and trigger alerts.
The fix removes the self-sustaining part: a failed commit no longer keeps a
lock that turns every later read and write into another 5 s wait.

## Revision 1 — Astra blockers

**Blocker 1: interrupted cleanup.** Before this revision, `write_tx` caught
`BaseException` from the body and the commit, but `abort` and `discard` caught
only `Exception`. A `KeyboardInterrupt` or `SystemExit` raised by `rollback()`
or `close()` therefore escaped. It replaced the original error and skipped
eviction, so the poisoned connection, with its open transaction, stayed cached.
A later commit on it would have written the failed batch.

Change (in `sqlite_tx.py`):
- New `close_quietly(conn, label)`: calls `conn.close()` and catches
  `BaseException`, logs it at `error`, and never raises.
- `discard()` now evicts **first** (`local.conn = None` when it is that
  connection), then calls `close_quietly`. The eviction therefore no longer
  depends on `close()` succeeding.
- `abort()` catches `BaseException` from `rollback()`, logs its type, and
  discards. It keeps the connection only when the rollback completes **and**
  `in_transaction` is false.
- `write_tx` is unchanged: `except BaseException: abort(...); raise`. It
  re-raises the original exception.
- The broad catch exists only in these three cleanup functions, and each of
  them runs only on a path that then re-raises the original error. Normal code
  paths are unchanged. There are no retries, and the 5.0 s timeout is
  unchanged.

**Blocker 2: initialization.** `DataFeed.db` and `RefStore.db` called
`conn.close()` unguarded in their `except BaseException:` setup handler. They
now call `close_quietly(conn, "init")` and then `raise`, so the setup error
always wins. The connection is stored in `self._local.conn` only after setup
succeeds, as before.

**New tests.** 20 new, so the file now has 35.

| Contract | Test | Result |
|---|---|---|
| commit locked + rollback `KeyboardInterrupt` / `SystemExit` → caller sees the same `LOCKED` object; evicted and closed | `test_write_tx_rollback_interrupt_keeps_original_and_evicts[KI, SystemExit]` | PASS |
| same, through `_store_save`: original warning logged, evicted, failed batch absent, next save on a fresh connection writes only its own rows | `test_store_save_rollback_interrupt[KI, SystemExit]` | PASS |
| same through `_note_floor` | `test_note_floor_rollback_interrupt[KI, SystemExit]` | PASS |
| same through `RefStore.save` (raises the original error, `is LOCKED`) | `test_refstore_rollback_interrupt[KI, SystemExit]` | PASS |
| rollback fails, then `close()` raises KI/SystemExit, both after the real close and with the real connection left open → still evicted, original error logged, failed batch absent, fresh connection works | `test_store_save_close_interrupt_still_evicts[4 cases]` | PASS |
| rollback "succeeds" but leaves the txn open, then close is interrupted (RefStore) | `test_rollback_ok_but_open_then_close_interrupt` | PASS |
| clean rollback → connection kept and reused | `test_clean_rollback_keeps_connection` | PASS |
| setup SQL fails, close ok / raises `RuntimeError` / raises `KeyboardInterrupt`, for DataFeed and RefStore → `ei.value is SETUP_ERR`, nothing cached, next `.db` is a fresh working connection | `test_init_setup_error_wins_and_nothing_cached[6 cases]` | PASS |

Interrupt tests wrap the call in `_contained()`. Without it, an escaped
`KeyboardInterrupt` would abort the whole pytest session instead of failing
that one test.

**Negative controls (Revision 1).** Each run mutated the fix, ran the 35 tests,
then restored the files:

| Mutant | Result |
|---|---|
| `abort` catches `Exception` only (the pre-revision code) | 8 failed: every rollback-KI/SystemExit test |
| `abort` catches `KeyboardInterrupt` but not `SystemExit` | 4 failed: every SystemExit case |
| `abort` swallows the interrupt but returns without discarding (cache left intact) | 8 failed on the eviction assertions |
| `discard` closes before evicting, catching `Exception` only (the pre-revision code) | 5 failed: every close-interrupt test |
| `discard` swallows a close interrupt and skips eviction, logging the same message | 5 failed on the eviction assertions |
| `.db` setup handler calls `conn.close()` unguarded (the pre-revision code) | 4 failed: close-Exception and close-KI, for both stores |
| fix restored | 35 passed |

**Real DELETE-journal rehearsal (Revision 1).** Temporary DB,
`journal_mode=delete`, production timeout 5.0 s. Another connection holds
SHARED, so the commit takes PENDING and fails:

```
-- case 1: real lock, clean rollback
failed save returned after 5.04s
writer conn in_transaction: False
fresh reader while holder present: 0
-- case 2: real lock, rollback interrupted (KeyboardInterrupt)
ERROR trader.data.sqlite_tx: candle store write: rollback failed (KeyboardInterrupt: ^C); discarding connection
WARNING trader.data.feed: candle store write BTC/USDT 15m: database is locked
failed save returned after 5.05s (no interrupt escaped)
cached conn after interrupted rollback: None
poisoned conn closed: True
fresh reader while holder present: 0      (PENDING released by the close)
-- recovery
next save -> rows: 8 fresh conn: True in_transaction: False
ref save rows: 3 in_transaction: False
```

**Focused totals (Revision 1).** A single run covered
`test_candle_store_transaction_safety`, `test_alts_and_refresh`,
`test_candle_cache_reuse`, `test_candle_store_symbol_keys`,
`test_feed_pagination`, `test_forming_bar_never_stored`,
`test_reference_store`, `test_ref_evidence`, `test_ref_live`,
`test_ref_recorder`, `test_ref_sources`, `test_dsl_ref`,
`test_declared_universe_is_traded`, `test_feature_ctx_universe`,
`test_universe_frames_timeframes`, `test_universe_wiring`,
`test_kernel_boot_recovery` and `test_research_universe_depth`: **130 passed,
2 skipped**. The skips need production `data/candles.db`, which is absent from
the worktree. The full suite was not run.

**Residual limits.** An interrupt that arrives *between* bytecodes in the
`except` clause of `write_tx` (outside `abort`) cannot be contained by any
Python code. If `close()` is interrupted before the underlying handle closes,
the evicted connection keeps its lock until it is garbage-collected. It is
unreachable from the cache, so it can never commit the failed batch.

## I–M. Returns

- **A.** Defect: after `commit()` fails with `database is locked`, the
  thread-local connection is left `in_transaction` holding PENDING, with no
  rollback. This affected `_store_save`, `_note_floor` and `RefStore.save`.
  New readers and writers are blocked until that thread next commits, which
  also commits the "failed" batch.
- **B.** `write_tx`, `abort` and `discard` in the new `sqlite_tx.py`, used by
  the three writers. Schema-setup failure now closes the connection. The 5.0 s
  timeout is made explicit, not increased.
- **C.** 15/15 failure-injection and helper tests pass (Revision 1: 35/35,
  including interrupted-cleanup and initialization-error tests).
- **D.** Contention: every path fails boundedly while the lock is held,
  recovers after release, leaves no open transaction, and no thread stays
  blocked.
- **E.** A connection is discarded and reopened only when rollback fails or a
  transaction is left open; this is bounded. Connections are thread-owned
  (`check_same_thread`).
- **F.** No duplicates and no late commit of a failed batch; PK/idempotency
  preserved.
- **G.** (Revision 1: 35 in the new file; 130 passed, 2 skipped across the
  store/ref/feed/kernel/universe set.) Original: 15 new; 91 passed (store, ref and feed set including the new tests);
  35 passed, 2 skipped (kernel and universe set).
- **H.** Negative control on the original code: 13 failed, 2 passed (both
  expected). Revision 1: all six cleanup mutants were detected (see *Revision 1*).
- **I.** INCIDENT_MECHANISM_ADDRESS = **PARTIALLY**. The self-sustaining
  retained-lock and uncleaned-transaction part is fixed. The initial contention
  source and the 5 s wait per contended statement remain.
- **J.** Historical holder: **UNKNOWN**.
- **K.** Deployment: suitable to deploy **after Astra review**, as a planned
  restart under the owner's existing restart procedure. It must not be bundled
  with other changes, and it must not be deployed to force a resume. The code
  is small and confined to the data layer, and it changes no trading, risk or
  exit logic. After deployment, check `rg -a "candle store .*locked|rollback
  failed|discarding connection" logs/luffy.log`.
- **L.** Owner resume: **KEEP_FROZEN**. The fix is not deployed, and it only
  narrows one contributor to the lock episode. The status of other recorded
  resume/restart blockers (for example
  `2026-09-27-kernel-restart-risk-v1.md`) was not re-verified in this package.
  The owner can reconsider after deployment and after those blockers are
  confirmed closed.
- **M.** Changed files (worktree, uncommitted):
  - `trader/data/sqlite_tx.py` (new)
  - `trader/data/feed.py`
  - `trader/data/references.py`
  - `tests/test_candle_store_transaction_safety.py` (new)
  - `docs/superpowers/reports/2026-09-28-candle-store-transaction-safety-v1.md` (new)
  - `graphify-out/*` was regenerated by an earlier `graphify update .`. It is
    unrelated, excluded from this package, and must not be staged. Revision 1
    did not run graphify.
