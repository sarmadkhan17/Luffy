# LUFFY-PROTECTION-SNAPSHOT-R1 — Revision 2 (Astra's confirmed blockers)

> **Parts of this report are superseded by
> [R3](2026-09-29-protection-snapshot-r3.md).** Astra found these R2 elements
> defective, and R3 replaces them:
>
> - the trading-key fallback (§B);
> - the "held throughout" proof and the ABA residual (§C);
> - the invalidation-based late-worker mechanism (§G/§L).

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
Worktree: `/home/sarmad/trader/.claude/worktrees/owner-frontend-live-binding`
(branch `owner-frontend-live-binding-v1`, HEAD `7840965`, on top of the
uncommitted OWNER-FRONTEND-V2.1 candidate and the blocked R1 package).

Not done, by instruction: no commit, deployment, restart, watchdog change,
`graphify update .`, or live venue call. Production was not touched: R1's
read-only test against `data/luffy.db` was **removed**, because this package
says not to touch production. `graphify-out/cache/last_query_stamp` was
already modified and is unrelated. No `graphify query` was run.

**CURRENT_LIVE_STATUS = NOT_ESTABLISHED.** The 2026-09-28 8-position capture
remains compatibility evidence only (see K below).

Results: pytest manifest (37 files) **1058 passed in 129.88s (0:02:09)**; frontend typecheck **PASS**; Vitest **51/51**; Playwright full suite **67/67** (3.6 min, includes the new `protection-r2.spec.ts`); `git diff --check` clean; timing-sensitive tests repeated 5× — all passed.

---

## A. Read-only capability design (blocker 1)

The R1 name-filter wrapper (`ReadOnlyVenue`) is deleted. Snapshot code
(`observe`, a reader, and `evaluate`, a pure function) receives one
`Observer` (`trader/engine/protection_snapshot.py`). The `Observer` is
immutable, uses `__slots__`, and holds exactly these four things:

| Dependency | What it can do |
|---|---|
| `reads: VenueReads` (`trader/engine/venue_reads.py`) | `positions()`, `algo_orders()`, `open_orders(symbol)`, each returning copied plain dicts or lists; `precision: TickPrecision` (a pure tick table); `rate_state()` |
| `journal: JournalReads` | Holds only a path. Each `facts()` call opens a SQLite `mode=ro` connection with an authorizer that allows only SELECT, READ, FUNCTION and TRANSACTION, and `SQLITE_LIMIT_ATTACHED=0`. It runs one read transaction and then closes. |
| `supervisor_busy` | The Supervisor pass lock's `locked` builtin. Its `__self__` is a `threading.Lock`, not the Supervisor. |
| `unavailable` | A string reason when the reader could not be built |

The snapshot has no Journal, Supervisor, state machine, Executor, recovery
ledger (the entry-recovery key is read through the read-only connection),
close queue or kernel exchange. `evaluate` performs no I/O. Protective
interpretation passes already-read data to `protective.open_stops` through a
data-only view. It uses `protective.protection_match` with `TickPrecision` as
its `price_to_precision`.

**Honest bound.** Python cannot make an object unreachable. The dedicated ccxt
client is still held privately in `VenueReads._client`, so an introspection
escape can reach it. That client is therefore made harmless at the transport
(section B). Every mutation path through it is refused before any byte is
sent.

**Structural proof** (`tests/test_protection_snapshot.py`):
`reachable()` walks attributes, `__dict__`, `__slots__`, containers, closure
cells, defaults, bound-method `__self__`/`__func__`, builtin-method
`__self__`, `functools.partial`, and `__wrapped__`. `violations()` fails on
any of the following that is reachable:

- a `Journal`, `Supervisor`, `ControlStateMachine`, `Executor`,
  `EntryRecovery`, `Kernel`, `ExitEngine`, `RiskManager` or venue stand-in;
- any `sqlite3.Connection`;
- any exchange-like object that is not the ccxt client with a `GetOnlySession`.

The kernel-built observer passes this check
(`test_snapshot_dependencies_have_no_mutation_authority`,
`test_kernel_wiring_builds_a_dedicated_read_only_observer`).
`test_journal_reads_cannot_write` shows the read-only connection refuses
INSERT, UPDATE, DELETE, PRAGMA, ATTACH and CREATE.

## B. Exchange client / threading design (blocker 2)

- **Dedicated client.** `make_venue_reads(kernel_exchange)` runs once, on
  the boot thread, inside `Kernel._start_protection_monitor`. It builds a new
  `ccxt.binanceusdm`, which is never the kernel's instance:
  - It is used only by the snapshot worker thread, and only one worker at a
    time (single flight). The kernel's synchronous client is therefore never
    shared across threads by this feature.
  - Its markets are a `deepcopy` of the kernel's already-loaded markets, so no
    extra exchangeInfo request is made. `load_markets()` runs on the boot
    thread only if the kernel has not loaded them.
  - It must address the same `fapiPrivate` URL as the kernel (demo vs.
    production). Otherwise construction fails with
    `snapshot_venue_differs_from_kernel_venue`. When construction fails the
    monitor still runs and publishes `UNREADABLE venue_reader_unavailable:<reason>`.
  - The per-request ccxt timeout is 8 s (lowered from the 10 s default), and
    ccxt's per-instance `enableRateLimit` is on.
- **GET-only transport.** `GetOnlySession` overrides `send` and mounts a
  `GetOnlyAdapter` whose `send` is also guarded. Both refuse any request
  before it is sent unless all three hold:
  - the method is GET;
  - the host is the configured fapi host;
  - the path is in `ALLOWED_PATHS` (`/fapi/v3/positionRisk`,
    `/fapi/v2/positionRisk`, `/fapi/v1/openAlgoOrders`, `/fapi/v1/openOrders`,
    `/fapi/v1/leverageBracket`).

  `tests/test_venue_reads.py` drives a real `binanceusdm` end to end through
  a fake inner transport, with no network:
  - The three reads work, and ccxt issues exactly the allow-listed paths.
  - These calls are refused, and the guard recorded the refusal:
    `create_order` (plain and `stopLossPrice`), `cancel_order`,
    `cancel_all_orders`, `edit_order`, `set_leverage`,
    `fapiPrivateDeleteAlgoOrder`, raw `fapiPrivatePostOrder` and
    `fapiPrivateDeleteAllOpenOrders`.
  - These escape paths are also refused, with nothing sent:
    `ccxt.Exchange.fetch(client, url, "POST")`, `session.request("DELETE")`,
    `session.get_adapter(url).send(POST)`, a GET outside the allow-list, and a
    GET to another host.
  - A read slot rebound to `create_order` still cannot create an order.
- **Credentials.** If `BINANCE_READ_API_KEY`/`BINANCE_READ_SECRET_KEY` are
  set (a Binance key with only "Enable Reading"), they are used and
  `credential_scope=read_only_key`. Otherwise the trading key is used
  internally and `credential_scope=trading_key_behind_get_only_transport`: the
  transport, not the key, is the order-authority boundary. That is a residual,
  stated in O. Keys never reach the snapshot, the dashboard or the frontend,
  and are never logged.
- **Aggregate rate coordination.** See J.

## C. Coherent observation mechanism (blocker 3)

```
J1 journal facts → P1 positions → A1 algo stops → P2 positions → A2 algo stops → J2 journal facts
```

Each venue read is one request (the algo listing is global), so each is an
instant.

- `P1 == P2` (symbol, side, size, entry price) proves the positions held
  throughout [P1, P2].
- `A1 == A2` (id, symbol, side, quantity, trigger, type, reduce-only) proves
  the stops held throughout [A1, A2].
- The two intervals overlap on [A1, P2]. For that whole interval one venue
  state held: these positions with these stops. No position from one moment
  is judged against a stop from another.
- `J1 == J2` covers open trades (id, symbol, side, amount, stop, stop id,
  market type, opened_at), `control_state`, re-arm evidence, the
  entry-recovery ledger, `supervisor_status` and the latest `control_events`
  id. Every Supervisor pass (`_save`), trigger, owner recovery and
  reconciliation writes at least one of these.
- A pass that still holds the lock when the bracket ends is refused as an
  overlap (`supervisor_busy()` read after J2).
- A slot that finds a pass running defers, as in R1.

Any difference gives `PARTIAL` with `observation_inconsistent:{venue_positions_changed |
stop_listing_changed | journal_changed | supervisor_pass_overlap}`, never
VERIFIED. Ordinary orders are read after J2 for cleanliness only. They never
support a protection claim.

| Case | Test | Result |
|---|---|---|
| A snapshot starts, then a Supervisor pass starts | `test_a_snapshot_starts_then_supervisor_starts` (real `Supervisor.pass_once`) | not VERIFIED, `supervisor_pass_overlap` |
| B pass running, then snapshot | `test_b_supervisor_starts_then_snapshot_defers` | deferred, 0 reads; next slot VERIFIED |
| C owner recovery mid-snapshot | `test_c_owner_recovery_mid_snapshot_is_never_verified` (real `request_owner_recovery`) | not VERIFIED, `journal_changed` |
| D reconciliation mid-snapshot | `test_d_reconciliation_mid_snapshot_is_never_verified` (real `reconcile_futures(verify=True)` re-arms) | not VERIFIED, `journal_changed`; **NC**: the same observation with J2 := J1 reads VERIFIED |
| E position changes between reads (Astra's q=1 / q=2 case) | `test_e_position_changes_between_reads_is_never_verified` | PARTIAL, `venue_positions_changed`; **NC**: with P2 := P1, A2 := A1 (R1's single read) it reads VERIFIED |
| F stop changes between reads | `test_f_stop_changes_between_reads_is_never_verified` | PARTIAL, `stop_listing_changed` |

ABA residual:

- A position that closes and reopens at the *identical* size and entry price
  inside the ~1 s window is not distinguishable by value.
- Stops cannot ABA, because algo IDs are unique.
- Any in-process actor (executor, Supervisor, reconcile) is still caught by
  the journal bracket. The residual is an external actor reproducing an exact
  fill price within about a second.

## D. Protection semantics (blocker 4)

Per venue position, `verified` requires all of the following:

- a journal row exists (otherwise `untracked_venue_position`);
- the sides agree;
- the journal quantity is finite and positive (otherwise
  `journal_quantity_unknown`, with no skipped comparison);
- the venue quantity agrees within reconcile's own alignment tolerance
  (otherwise `journal_size_drift`);
- the re-arm evidence is valid under reconcile's rule and has no record for
  this symbol. Otherwise the reason is `protection_rearm_evidence_unreadable`,
  or `protection_rearm_pending` plus `protection_rearm_evidence_mismatch` when
  the trade id differs;
- no entry recovery is pending for the symbol;
- an adequate stop exists in the atomic algo listing
  (`protective.protection_match`);
- `precision_status == VALID`: the trigger is on the venue's exchangeInfo
  `PRICE_FILTER.tickSize`.

If the tick is unknown, the result is `precision_status=UNKNOWN`,
`price_precision_unknown`, and `stop_adequacy_unknown`. It is never inferred,
and there is no six-decimal fallback, because `TickPrecision.price_to_precision`
raises. A trigger off the tick gives `INVALID`.

The book is `VERIFIED` only when every check is true: `venue_positions`,
`observation_consistent`, `reconciliation` (journal and venue agreement),
`venue_protection` and `precision_known`, with a complete listing and no
reasons. `UNREADABLE` means positions or listings could not be read.
`PARTIAL` covers everything else.

## E. Reconciliation-cleanliness semantics (blocker 5)

`cleanliness` has status `CLEAN | INFO | ATTENTION | UNKNOWN`, with items,
coverage `position_symbols` and unread symbols. It **never changes protection
status**.

| Item class | Severity | Source |
|---|---|---|
| `reduce_only_close` | info | reduce-only closing TP or limit close (algo or ordinary); `sweep_may_cancel` flagged when it has a stop price |
| `entry_order` | attention | ordinary, not reduce-only, same side as the position |
| `reversal_capable_order` | attention | ordinary, not reduce-only, closing side (could flip the position) |
| `ordinary_stop_order` | attention | ordinary `STOP`/`STOP_MARKET`. Never counted as protection. |
| `stop_id_mismatch` | attention | the adequate stop is not the journal's ID, so a reconcile sweep would cancel and re-arm it |
| `unrecognised_stop` | attention | an extra stop that a sweep would cancel |
| `orphan_stop` | attention | a stop on a flat symbol |
| `stale_rearm_evidence` | attention | a re-arm record for a symbol with no position |

A harmless TP no longer lowers protection
(`test_harmless_tp_does_not_lower_protection`: VERIFIED plus cleanliness
INFO). A TP-only position is UNPROTECTED (`test_tp_only_is_unprotected`).

## F. Persistence generation and order (blocker 6)

Evidence is stored in a new table, `protection_evidence`, not `state_kv`. It
has one row (`slot = 1`) and typed columns:
`boot INTEGER CHECK typeof='integer' AND >0`, `seq INTEGER CHECK …`, and
`value TEXT CHECK json_valid`.

Identity is **(boot, seq)**:

- **boot** comes from `protection_boots` (`INTEGER PRIMARY KEY AUTOINCREMENT`).
  It is allocated once per monitor, so it is monotonic across restarts, and
  never reused. Rows older than 16 boots are pruned.
- **seq** is an in-process counter.

No wall clock and no value parsed out of the JSON is used for ordering. The
single upsert accepts a row only if it is strictly newer (equal is rejected).

A stored row never blocks a newer one if:

- its identity is not an integer;
- its JSON is invalid;
- its boot is beyond the allocated counter;
- it is this boot's row but has a seq this process never issued.

Tests: `test_generation_order_newest_wins_equal_rejected`,
`test_malformed_stored_identity_never_blocks` (a row claiming an unallocated
boot, an unissued seq, and a legacy untyped table with text identity and
invalid JSON), `test_wall_clock_rollback_does_not_block_new_evidence` (clock
moved back 1 h across a restart: accepted and fresh). The reader validates
`schema == 2` and that the JSON `generation` equals the typed columns;
otherwise it returns `protection_snapshot_unreadable`.

## G. Publication-failure behavior (blocker 7)

- Freshness is the stored snapshot's own `checked_at`: the wall time at J1,
  before any venue read. It is never the request time or the write time. The
  API threshold is now `SNAPSHOT_STALE_S = 120 s` (2 × cadence;
  `STALE_AFTER_S` in the kernel module must match it, and a test asserts
  equality). The Supervisor's 180 s is unchanged.
- A failed publication renews nothing: the old row keeps its `checked_at` and
  reads STALE 120 s after it.
- **Late workers.** At the timeout the worker's seq is added to the
  invalidated set, under the publication lock, *before* the UNREADABLE
  timeout evidence is attempted. A worker publishes only while holding that
  lock and re-checks validity immediately before `COMMIT`. A worker whose
  generation was invalidated therefore never becomes current, even when the
  timeout publication itself failed.
  - `test_failed_timeout_publication_fails_closed_and_late_worker_is_rejected`:
    the DB is locked during the timeout, the old VERIFIED stands, the API reads
    it STALE at `checked_at + 121 s`, and the late worker is rejected.
  - **NC** `test_nc_without_invalidation_the_late_worker_would_publish`:
    with invalidation removed, the late worker wins.
- The frontend expires evidence locally (K).

## H. Journal-contention behavior (blocker 8)

- **Own connection.** `SnapshotStore` opens its own short-lived connection
  per publication: `BEGIN IMMEDIATE`, busy timeout 2 s (configurable), one
  upsert, commit, close. It never touches `Journal._write_lock` or
  `Journal._tx`. Its SQLite write lock is held for one small upsert.
- `test_journal_write_lock_never_blocks_the_kernel_beyond_bound`:
  - A production-like writer holds `Journal._tx` open. `run_once` still
    completes the check, the publication fails in about 0.25 s, the whole call
    returns in under 1.5 s, `publish_failed == 1`, and nothing is stored.
  - Then, with the monitor publishing continuously, 30 kernel `kv_set` writes
    each take under 0.5 s.
- **Bench, journal write latency** (offline): baseline median
  0.178 ms (p99 0.251, max 0.276). With the monitor publishing continuously: 0.183 ms (p99 1.144, max 1.711).
- **Shutdown and timeouts stay bounded.** Waits are bounded by the busy
  timeout, and the publication-lock acquire times out at busy timeout + 1 s.

## I. Cadence (blocker 9)

Scheduling is fixed-start on the monotonic clock: slot *k* starts at
t0 + k·60 s, and the next slot does not depend on completion.

- **Single flight.** When a slot arrives while a worker is still alive, the
  monitor publishes `UNREADABLE previous_check_still_running` and starts no
  worker.
- **No replay.** Slots missed during a long check are skipped, never replayed
  (`skipped_slots`), so a long check causes no burst.
- **Supervisor deferral.** A Supervisor pass defers the check by 5 s *within*
  the same slot. If the slot runs out, it is skipped.

Tests (fake monotonic clock):

- `test_cadence_is_fixed_start_not_completion_plus_interval`: with 20 s checks
  the starts are 0, 60, 120, 180, 240.
- `test_long_check_skips_slots_without_a_burst`: with 130 s checks the starts
  are 0, 180, 360, with 2 slots skipped per check.
- **NC** `test_nc_completion_plus_interval_would_drift`: R1's
  completion + 60 s schedule, patched in, gives 0, 80, 160, 240, 320 and fails
  the expectation.

## J. Aggregate venue request budget (blocker 10)

**Per check (snapshot).** The bench counts requests offline. Weights are
Binance's documented USD-M weights, not measured live: positionRisk 5,
openAlgoOrders without a symbol 40, openOrders with a symbol 1.

| Open positions | Requests / check | Weight / check |
|---|---|---|
| 0 | 4 | 90 |
| 1 | 5 | 91 |
| 5 | 9 | 95 |
| 8 | 12 | 98 |

This is R1 (2 + N requests, weight about 45 + N) plus one extra positionRisk
and one extra algo listing, which the consistency proof needs (+45 weight). I
chose per-symbol ordinary reads (weight 1 each) over one global `openOrders`
(weight 40) because they are cheaper by weight for N < 40. They are capped at
16 symbols; beyond that, cleanliness is `UNKNOWN`. The snapshot runs once per
60 s.

**Aggregate estimate.** This is a static source-inspection estimate, not
measured, and it counts only the demo fapi host. Market data (OHLCV) comes
from the production public host, which has a separate limit.

| Reader | When | Weight / min (8 positions, ~15-symbol universe) |
|---|---|---|
| Protection snapshot | every 60 s, all states | ≈ 98 |
| Kernel cycle: order book (limit 20, weight 2, 45 s cache) × scanned symbols; premiumIndex × universe; `fetch_balance` 5; `fetch_positions` 5 | every 60 s | ≈ 55 |
| Executor / exits / recovery `fetch_order`, trade accounting | per order event | sporadic, ≈ 0–10 |
| Rent (`income`, weight 30) | per rent interval (hours) | ≈ 0–1 |
| Supervisor pass, `reconcile_futures(verify=True)`: positions 5 + 3 global stop listings (120) + per-symbol `open_stops` (40 + 1) × 8, plus tickers | boot, owner request, and **every 60 s only in RECOVERY** | ≈ 453 in RECOVERY, else 0 |
| **Total** | ACTIVE / FROZEN / HALTED | **≈ 160 / 2400 (≈ 7 %)** |
| **Total** | RECOVERY, worst case | **≈ 610 / 2400 (≈ 26 %)** |

Two mechanisms keep the snapshot out of the way of other traffic:

- **The snapshot yields to other traffic.** Every snapshot response's
  `X-MBX-USED-WEIGHT-1M` header (IP-wide: kernel, Supervisor and snapshot
  together) updates `RateGuard`. While the last value from the past 60 s is
  at or above 1200 (50 % of the limit), every further snapshot request is
  refused before sending. Such a check reads `UNREADABLE venue_rate_budget`.
  The kernel's own client is not throttled by this guard, by design: trading
  traffic has priority. (`test_aggregate_weight_guard_yields_to_other_traffic`)
- **No overlap with Supervisor passes.** The snapshot defers while a
  Supervisor pass holds its lock, so its reads do not add to the
  heaviest (RECOVERY) bursts except when a pass starts mid-bracket. That
  observation is then refused as an overlap anyway.

No Binance limit or timeout was raised. The ccxt timeout for this client was
lowered to 8 s.

## K. Frontend freshness (blocker 11)

**Adapter (`frontend/src/adapters/live.ts`):**

- The adapter computes `expiresAt` in browser-clock milliseconds:
  `checked_at + stale_after_s − (server generated_at − browser receipt time)`.
- If `expiresAt` cannot be computed, the result is STALE, never VERIFIED.
- VERIFIED now also requires `observation_consistent` and `precision_known`.

**Expiry (`frontend/src/protectionExpiry.ts`, `applyProtectionExpiry`):**

- It is re-applied every 1 s by a local clock in `Overview.tsx`,
  independent of polling. React-query keeps `q.data` after a failed refetch,
  and that data is now aged locally.
- Once `expiresAt` passes, the book and every position become STALE, with
  `lastReported` and `verification_stale_locally`. This applies to VERIFIED,
  PARTIAL, UNREADABLE and UNPROTECTED alike.

**Tests:**

- Vitest `protectionSnapshot.test.tsx`:
  - VERIFIED → poll failures → clock +125 s → STALE, in a rendered `Overview`;
  - VERIFIED → UNREADABLE on the next successful poll;
  - pure transitions: VERIFIED → UNREADABLE, PARTIAL → VERIFIED,
    missing → first snapshot;
  - server clock offset.
- Playwright `protection-r2.spec.ts`:
  - `page.clock` plus aborted `/overview` polls: VERIFIED at +70 s, STALE at
    +130 s, positions STALE with "Last reported: VERIFIED";
  - VERIFIED → UNREADABLE;
  - missing → VERIFIED; PARTIAL → VERIFIED.
- **NC (both levels, run manually and then restored):** with the expiry
  bypassed in `Overview.tsx`, the Vitest test fails, and the Playwright test
  fails exactly at `toHaveText("STALE")` (received VERIFIED).

**Historical compatibility (not live status):** the 2026-09-28 capture
replays as PARTIAL when there are no tick sizes, which shows precision is no
longer inferred. It replays as VERIFIED with ticks *labelled as inferred* from
the echoed triggers (`test_captured_2026_09_28_state_is_compatible_but_not_live_status`).

## L. Timeout / shutdown (blocker 13)

- **Budget.** Reads stop at 75 % of the 20 s timeout. At 20 s the generation
  is invalidated and a newer UNREADABLE `venue_timeout` is published. The
  wait polls the stop event every 50 ms, so `stop()` interrupts a running
  check.
- **Tests:**
  - `test_hung_venue_single_flight_no_accumulation_bounded_shutdown`: with a
    hung venue across about 20 slots, at most 1 worker is alive, 1 venue
    request is made in total, the maximum in-flight is 1, UNREADABLE is
    stored, shutdown takes under 0.5 s, and the late result is rejected.
  - `test_shutdown_interrupts_a_running_check`
  - `test_concurrent_run_once_is_single_flight`
- **Repeated runs:** run 1: 8 passed, 48 deselected in 5.33s; run 2: 8 passed, 48 deselected in 5.48s; run 3: 8 passed, 48 deselected in 5.48s; run 4: 8 passed, 48 deselected in 5.33s; run 5: 8 passed, 48 deselected in 5.30s (`selection: -k 'hung or shutdown or single_flight or late_worker or fails_closed or write_lock or cadence or long_check'`; log `repeat-timing.log`).
- **Bench:** a hung `run_once` returns in 2.004 s with a 2 s timeout, and
  the late result is rejected.

## M. Exact focused-test manifest and results (blocker 14)

The manifest is immutable and lives at
`docs/superpowers/reports/2026-09-29-protection-snapshot-r2-evidence/pytest-manifest.txt`
(37 files). The command is:

```
/home/sarmad/trader/venv/bin/python -m pytest -q -p no:cacheprovider $(cat …/pytest-manifest.txt)
```

**1058 passed in 129.88s (0:02:09)** — log `pytest-manifest.log`. Covers the protection snapshot and venue reads (71), protective/reconciliation, Supervisor/owner recovery/control recovery/entry recovery/fence, kernel boot, exits, owner frontend API and read cache, owner interface r1–r6/boundary/Telegram, dashboard auth/pipeline/JS, activation baseline v1/v2, candle-store transaction safety/symbol keys/cache reuse, single creation path.

Frontend: `npx tsc --noEmit` PASS; `npx vitest run` 8 files / 51 tests PASS; `LUFFY_PYTHON=/home/sarmad/trader/venv/bin/python npx playwright test` 67/67 (log `frontend/evidence/protection-snapshot-r2/playwright-full.log`). As in earlier packages, the Playwright suite rewrites its own V2 screenshot/performance evidence under `frontend/evidence/` (already-modified files) as a side effect.

Bench (offline, `performance.json`): CPU-only check median 1.567 ms (p95 2.399); 60 ms/request serial 731.4 ms for 12 requests; stand-in trade loop median 2.784 ms baseline vs 2.774 ms with the monitor looping continuously vs 2.761 ms with a hung venue (1 request total); tracemalloc growth 124872 B over 1000 checks incl. publication (R1 range; `starts`/`refusals` bounded as deques); one stored row of 6468 B.

## N. Negative controls (blocker 15)

| Required control | Where it fires |
|---|---|
| Exchange object exposed through snapshot | `test_nc_exchange_object_exposed_through_snapshot` |
| Bound-method `__self__` mutation | `test_nc_bound_method_self_escape` (Supervisor through `pass_once`; venue through a bound read) |
| Read alias points to order creation | `test_nc_read_alias_points_to_order_creation` (structural) and `test_read_alias_to_order_creation_is_refused` (transport) |
| Mutation-capable callback | `test_nc_mutation_capable_callback_and_wrapped_read` (closure over Journal; `functools.wraps` read that cancels) |
| Mixed-moment false VERIFIED | E, D and F above, each with an in-test NC showing R1's single read would say VERIFIED |
| Missing journal quantity verifies | `test_missing_journal_quantity_never_verifies` |
| Malformed re-arm evidence verifies | `test_malformed_rearm_evidence_never_verifies` (5 malformed shapes), `test_rearm_evidence_is_per_symbol` |
| Fake precision verifies | `test_precision_unknown_is_never_inferred`, `test_off_tick_trigger_is_invalid`, the capture replay without ticks, `test_tick_precision_is_authoritative_or_unknown` |
| Harmless TP lowers protection | `test_harmless_tp_does_not_lower_protection` |
| Malformed sequence blocks updates | `test_malformed_stored_identity_never_blocks` |
| Wall-clock rollback blocks new evidence | `test_wall_clock_rollback_does_not_block_new_evidence` |
| Failed UNREADABLE publication leaves VERIFIED visually fresh | `test_failed_timeout_publication_fails_closed…`, `test_r2_snapshot_reader_contract`, and frontend K |
| Late worker overwrites failed timeout generation | the same test, plus `test_nc_without_invalidation_the_late_worker_would_publish` |
| DB write lock blocks kernel beyond bound | `test_journal_write_lock_never_blocks_the_kernel_beyond_bound` |
| Browser polling failure leaves VERIFIED fresh | Vitest and Playwright in K, each with a manual NC |
| Cadence drifts completion + 60 | `test_nc_completion_plus_interval_would_drift` |
| Venue request storm / overlapping checks | `test_hung_venue_single_flight…`, `test_concurrent_run_once_is_single_flight`, `test_aggregate_weight_guard_yields_to_other_traffic` |
| (kept) the owner API never reaches the venue | `test_owner_api_never_reaches_the_venue` (`observe`, `evaluate`, `run_once`, `open_stops`, and ccxt reads booby-trapped) |

## O. Remaining blockers and residuals

1. **Live status is not established.** No live venue call was authorized.
   Before deployment, a pre-deploy gate must confirm three things against the
   demo venue:
   - ccxt 4.5.75's `fetch_positions` uses only the allow-listed paths there
     (proven offline against canned responses only);
   - the demo host returns `X-MBX-USED-WEIGHT-1M`. Without it the aggregate
     guard is inert, and the snapshot is bounded only by its own cadence and
     request count;
   - the first published snapshot. Observations of changes between the two
     position reads would show up as a persistent `venue_positions_changed`;
     entry price is expected to be stable between fills.
2. **Trading-key residual.** No read-only key exists in `.env` (by variable
   name only; no values were read or printed). Until the owner creates one,
   the GET-only transport is the boundary. Someone with deliberate code
   access could still sign a request with the key, which is forgery, not a
   reachable method.
3. **Python reachability.** The dedicated client is reachable by
   introspection (`VenueReads._client`). It is neutralised by the transport,
   not hidden.
4. **ABA** on identical size *and* entry price inside the bracket (C).
5. **Budget numbers are estimates.** Weights are documented values, and the
   kernel's rates come from static inspection. They are not measured live.
6. **Tick metadata is from boot.** Ticks are read from the kernel's
   markets at boot. A venue tick change is not seen until restart; the
   visible symptom would be `INVALID` or `UNKNOWN`, never a false VALID for
   an unknown tick.

## P. Deployment recommendation

**Do not deploy yet.** The code-level blockers are closed with tests and
negative controls. Deploy only after Astra's review and an explicitly
authorized pre-deploy gate:

- a read-only run of `make_venue_reads` plus one `observe`/`evaluate` against
  demo (no publication);
- confirmation of the used-weight header and the request paths;
- the first live snapshot, observed.

Deployment would then follow the recorded restart and `data/watchdog.off`
procedure. Optionally, create a Binance "Enable Reading"-only key as
`BINANCE_READ_API_KEY`/`BINANCE_READ_SECRET_KEY` first.

## Q. Exact changed files (this revision)

New:
- `trader/engine/venue_reads.py`
- `tests/test_venue_reads.py`
- `frontend/src/protectionExpiry.ts`
- `frontend/tests/browser/protection-r2.spec.ts`
- `docs/superpowers/reports/2026-09-29-protection-snapshot-r2.md`
- `docs/superpowers/reports/2026-09-29-protection-snapshot-r2-evidence/` (`performance.json`, `pytest-manifest.txt`, `pytest-manifest.log`, `repeat-timing.log`)
- `frontend/evidence/protection-snapshot-r2/` (`playwright-full.log`)

Rewritten (R1 files, uncommitted):
- `trader/engine/protection_snapshot.py`
- `tests/test_protection_snapshot.py`
- `scripts/bench_protection_snapshot.py`
- `frontend/tests/protectionSnapshot.test.tsx`

Modified:
- `trader/kernel.py`: `_start_protection_monitor` now uses `make_monitor`
  with a dedicated reader and the pass lock's `locked`.
- `trader/dashboard/owner_api.py`: reads `protection_evidence`, applies
  `SNAPSHOT_STALE_S` 120, validates generation and schema, adds the
  `observation_consistent`/`precision_known` checks, and passes through
  `cleanliness`.
- `tests/owner_frontend_fixture.py`: schema 2, `store_protection_snapshot`.
- `tests/test_owner_frontend_api.py`
- `tests/test_owner_read_cache.py`
- `frontend/src/adapters/live.ts`
- `frontend/src/adapters/contracts.ts`
- `frontend/src/views/Overview.tsx`
- `frontend/tests/v21.test.tsx`

Unchanged: `protective.py`, `reconcile.py`, `supervisor.py`, `recovery.py`,
the executor, Risk, `journal.py`, `config.yaml`, the watchdog, the candle
store, and the V2.1 fixes (their tests are in the manifest and the Playwright
suite).
