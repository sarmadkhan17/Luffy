# LUFFY-PROTECTION-SNAPSHOT-R1 — read-only kernel protection snapshot

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
Worktree: `/home/sarmad/trader/.claude/worktrees/owner-frontend-live-binding`
(branch `owner-frontend-live-binding-v1`, HEAD `7840965`, on top of the existing
uncommitted owner-frontend V2.1 candidate, which this package extends).

Not done: no commit, deployment, restart, watchdog change, production access
beyond one read-only SQLite `mode=ro` read of `data/luffy.db` open trades,
no live venue call, and no `graphify update .`. One read-only `graphify query`
ran at the start; it touches `graphify-out/cache/last_query_stamp`, which was
already modified before this package.

## A. Snapshot contract

`trader/engine/protection_snapshot.py`. One JSON document at
`state_kv.protection_snapshot` (schema 1):

| Field | Meaning |
|---|---|
| `seq` | ns identity; newer always wins (see C) |
| `checked_at` / `completed_at` | check start (freshness basis) / end, UTC ISO |
| `source` | `kernel protection monitor (read-only venue snapshot)` |
| `status` | `VERIFIED` / `PARTIAL` / `UNREADABLE` |
| `checks.venue_positions` | `fetch_positions` read and validated as reconcile's verify mode does (finite, non-negative, one side per symbol) |
| `checks.reconciliation` | *equivalence*: nothing a mutating reconcile would act on (see below) |
| `checks.venue_protection` | complete listing and every venue position has an adequate stop |
| `complete_listing` / `listing_reason` | `protective.open_stops(strict=True)` global algo listing plus per-position ordinary listing |
| `symbols[]` | per venue position: identity (`symbol`, `venue_symbol`, journal trade id/side/amount), `side`, `quantity`, `stop_present`, `stop_id`, `stop_ids`, `stop_side`, `reduce_only`, `order_type`, `stop_kind`, `covered_quantity`, `trigger_price`, `precision_valid`, `journal_stop_id`, `stop_id_matches_journal`, `match_reason`, `verified`, `reasons` |
| `orphan_stops[]`, `ordinary_orders[]` (≤50, `ordinary_orders_truncated`) | surfaced truthfully, never acted on |
| `reasons[]` (≤64) | reason codes, e.g. `venue_timeout`, `position_unprotected:SYM`, `stop_inadequate:SYM:trigger_below_expected`, `stop_id_mismatch:SYM`, `unrecognised_stop:SYM`, `orphan_stop_present:KEY`, `untracked_venue_position:SYM`, `journal_position_absent_on_venue:SYM`, `journal_size_drift:SYM`, `protection_rearm_pending:SYM`, `entry_recovery_pending`, `stop_trigger_precision_invalid:SYM` |
| `control_state_observed`, `venue_requests`, `venue_request_names`, `duration_ms`, `mutations` (structurally 0), `pid` | diagnostics |

Status rule: `UNREADABLE` unless positions were read and the stop listing is
complete; `VERIFIED` only if all three checks are true and there are no reasons;
otherwise `PARTIAL`. Reconciliation-equivalence reports what
`reconcile_futures(verify=True)` would adopt, ghost-close, align, sweep or
re-arm, and never does it.

**Mutation is structurally impossible.** The venue is reached only through
`ReadOnlyVenue`: an allow-list of `fetch_positions`,
`fapiPrivateGetOpenAlgoOrders` and `fetch_open_orders` (memoized per symbol),
plus the local `price_to_precision`. Every other attribute, including
`create_order`, `cancel_order` and `fapiPrivateDeleteAlgoOrder`, raises
`AttributeError`. The journal is read only. The sole write is the snapshot key.

**Existing protective truth is reused.** Stop interpretation is
`protective.open_stops` (algo and ordinary normalization, completeness) and
`protective.protection_match` (side, reduce-only, `STOP_MARKET`, size,
loosened trigger, venue precision). Symbols use `norm_symbol`/`venue_key`.
There is no second Binance interpretation.

## B. Kernel cadence design

`ProtectionMonitor` runs on its own daemon thread, `protection-snapshot`. It
is started in `Kernel.boot()` after the boot Supervisor pass, for futures only.
Config is optional, `protection_snapshot.{enabled,interval_seconds=60,timeout_seconds=20}`;
`config.yaml` is unchanged, so the defaults apply.

- **All states.** It does not gate on control state, so it runs in ACTIVE,
  FROZEN, HALTED and RECOVERY.
- **One check at a time.** A non-blocking run lock allows one check. If a
  worker is still inside a venue call, the next tick makes no request and
  writes `UNREADABLE: previous_check_still_running`.
- **Bounded.** The worker stops issuing venue calls after 75% of the timeout
  (`VenueReadTimeout`). If it has not finished by the timeout, the monitor
  writes a newer `UNREADABLE: venue_timeout`; the late result is rejected by
  `seq`.
- **Supervisor.** While the Supervisor pass lock is held (boot, cadence, owner
  recovery, macro release or rollback preparation), the check is deferred for
  5 seconds. The monitor only checks `locked()`, so it can never delay a pass
  or the trading loop's `trigger()`.
- **Trading loop.** It never waits on the monitor.
- **Shutdown.** `_graceful()` calls `monitor.stop()`, and the loop exits
  within one tick.
- **Construction failure.** This is logged and boot continues.

## C. Persistence design

There is one row. It is written by one atomic SQLite upsert:
`ON CONFLICT DO UPDATE … WHERE stored.seq < new.seq`. JSON1 is guarded with
`json_valid`, so a corrupt row is replaced. An older or equal `seq` is
rejected, so a timed-out check cannot overwrite newer evidence. Storage is
bounded: about 5.4 KB for 8 positions, with no per-minute history. Transitions
are logged once per status change to `luffy.log`, not to `control_events`.

Readers can distinguish four states:

- **No snapshot yet:** the key is absent, giving `protection_snapshot_missing`.
- **Fresh:** the age is at most 180 s (3 × cadence).
- **Stale:** the age is over 180 s, or `invalid` if the timestamp is more than
  5 s in the future or cannot be parsed.
- **Failed:** `status=UNREADABLE` or `PARTIAL`, with reasons.

## D. API / read contract

`trader/dashboard/owner_api.py` (journal reads only):

- `read_protection_snapshot()` returns freshness, status, all checks,
  `complete_listing`, symbols, orphans, ordinary orders, reasons and
  diagnostics.
- `/owner-api/v1/overview` now uses the snapshot for protection. For each
  position:

  | Status | When |
  |---|---|
  | VERIFIED | Fresh, every check true, complete listing, and this position verified |
  | STALE | Evidence is aged or has an invalid time; the result is kept as `last_reported` |
  | UNREADABLE | The snapshot could not read the venue |
  | UNPROTECTED | No stop was found |
  | PARTIAL | This position's or the book's reasons prevent verification |
  | UNVERIFIED | No venue position, or the position opened after the check |
  | UNAVAILABLE | No snapshot exists: `no_protection_snapshot` |

  The book status also accounts for untracked positions and orphan stops.
  `NO_POSITIONS` requires a fresh, fully verified snapshot. The Supervisor
  status remains the source for `needs_you`.
- `/owner-api/v1/protection` is new. It returns the stored snapshot and uses
  the same auth as the other owner routes.
- **No browser venue read.** The module never imports the snapshot builder,
  `open_stops`, `reconcile_futures` or `make_exchange`. Both a runtime
  booby-trap test and a source check enforce this.

## E. Mutation-safety results

Every case uses the recording venue from the kernel-boot tests, which records
every placement, cancel, algo delete and leverage call. Each case asserts
`venue.mutations == []` and an identical full-database diff: all tables and
all `state_kv` rows except the snapshot key.

| Case | Result |
|---|---|
| A fully protected | VERIFIED, every per-symbol field asserted |
| B missing stop | PARTIAL `position_unprotected:BTC/USDT`; no re-arm; no re-arm evidence written |
| C orphan stop | PARTIAL `orphan_stop_present:SUIUSDT`, listed; not cancelled |
| D stop-ID mismatch | position `verified` (venue truth), book PARTIAL `stop_id_mismatch`; no cancel/re-arm. Extra leaked stop → `unrecognised_stop` |
| E loosened stop | PARTIAL `stop_inadequate:…:trigger_below_expected` |
| F incomplete listing (invalid algo response / no global algo endpoint / ordinary listing error) | UNREADABLE, no sweep |
| G venue timeout | UNREADABLE `venue_timeout`, including a timeout wrapped by `ProtectiveSnapshotIncomplete`; the budget stops further calls |
| H ordinary orders | surfaced with type/price/stop/reduce-only; a TP that a sweep would cancel → `unrecognised_stop`; one listing serves both uses |
| I FROZEN owner hold · J HALTED · K RECOVERY · L ACTIVE | state and every row unchanged with a naked position present; `control_state_observed` recorded |
| extra | reconciliation-equivalence (untracked/absent/drift) reported, journal untouched; pending re-arm / entry intent → not verified; precision-invalid trigger → not verified |

## F. Concurrency results

- **Supervisor pass held:** the check defers, with zero requests, and the
  monitor never takes the lock.
- **Overlap test:** a real `Supervisor.pass_once`, owner recovery
  (FROZEN → `request_owner_recovery`), `reconcile_futures(verify=True)` and
  per-cycle journal writes each ran 10 times while the monitor looped with
  interval 0. There was no deadlock and no monitor mutation, and the final
  evidence was VERIFIED with newest-wins.
- **Single flight:** 8 concurrent `run_once` calls gave maximum in-flight venue
  reads of 1.
- **Hung venue:** the first `run_once` returned in under 1 s with a 0.2 s
  timeout. The next 5 ticks made no requests (1 total). The late result was
  rejected (`persist_rejected == 1`).
- **Shutdown:** with a hung venue, the loop stopped in under 1 s.

## G. Frontend truth result

Changes are in `frontend/src/adapters/live.ts`, `contracts.ts` and
`views/Overview.tsx`. The adapter re-checks the backend: VERIFIED and
NO_POSITIONS survive only with a fresh VERIFIED snapshot where
`venue_positions`, `reconciliation`, `venue_protection` and `complete_listing`
are all true. Otherwise:

| Snapshot | Frontend shows |
|---|---|
| No snapshot | UNAVAILABLE (“NOT VERIFIED · no protection check”) |
| Aged or invalid time | STALE |
| Failed | UNREADABLE |
| Anything else unverified | PARTIAL |

`venue_protection` alone never yields VERIFIED. The evidence popover gives
check results and reasons. `UNREADABLE` was added to `ProtectionStatus`.
Labels now say “Checked <UTC>” and “kernel's last read-only venue protection
check”.

The browser shows VERIFIED for fresh fixture data. The UNREADABLE, partial
(UNPROTECTED) and missing scenarios are never mint. Page traffic stays on the
dashboard host.

## H. Performance

Full data is in `docs/superpowers/reports/2026-09-29-protection-snapshot-r1-evidence/performance.json`
and `memory-scaling.json`. It was measured offline with a simulated venue and
a temporary journal, not production.

| Measure | Result |
|---|---|
| Venue requests per snapshot | 2 + N positions (8 positions → 10); once per 60 s |
| CPU-only snapshot (8 positions) | median 0.5 ms, p95 2.5 ms, max 6.1 ms |
| With 60 ms/request latency | median 607 ms (serial) |
| Stand-in trade-loop iteration | baseline median 2.66 / p99 4.21 ms; monitor looping continuously 2.52 / 4.82 ms; hung venue 2.58 / 5.66 ms |
| Hung venue | `run_once` returns at the timeout (2.004 s with 2 s); 1 request total |
| Memory | tracemalloc net growth 91/108/115 KB after 1k/2k/3k snapshots (flattening; allocator/SQLite warm-up, not linear); stored row ~5.4 KB |

These are small samples. Loop medians within noise do not prove zero impact
under production GIL load (pandas cycles); the monitor's own CPU is sub-ms per
minute. With a failed or slow venue, the trading loop and heartbeat never
wait: the stall monitor threshold is 240 s, the snapshot timeout is 20 s, and
it runs on a separate thread.

## I. Focused tests

| Suite | Result |
|---|---|
| `tests/test_protection_snapshot.py` (new) | 35/35 |
| Focused regression: protection/reconcile, Supervisor/recovery/owner recovery/risk guard, kernel boot, entry recovery/fence, exits, owner interface r3–r6/boundary/Telegram, owner frontend API/read cache, dashboard auth/pipeline/JS, activation baseline v1/v2, single-creation path | 898/898 (includes the 35) |
| Frontend typecheck | PASS |
| Frontend unit (vitest) | 42/42 (new `protectionSnapshot.test.tsx` 7) |
| Playwright full suite | 64/64 in 3.7 min (new `protection-r1.spec.ts` 5); results in `frontend/evidence/protection-snapshot-r1/` |
| `git diff --check` | clean |

Candle-store code was not touched, so those tests were not rerun. The full
repository pytest was not rerun.

Updated existing tests to the new contract:

- `test_protection_rules` and `test_verified_requires_each_available_check`
  (now also `complete_listing`).
- The V2.1 unit and browser tests that forced `supervisor.venue_positions`
  now force `snapshot.venue_positions`.
- `live.test.tsx` fixture.
- `protection_snapshot` was added to the auth-required route list and the
  missing-data errors.

There was one pre-existing fixture flake. The realized-today trade closed
`now − 1h`, which is yesterday between 00:00 and 01:00 UTC. It is now placed
safely within today UTC.

## J. Negative controls

Each detector is shown to fire:

- **`ReadOnlyVenue`:** it refuses every mutator and `setattr`.
- **Sabotage:** a sabotaged `open_stops` that calls `cancel_stop` and
  `place_stop` inside the snapshot makes zero mutations, and the snapshot
  reads UNREADABLE.
- **Re-arm detection:** the same mutation detector flags
  `reconcile_futures(verify=True)` re-arming, and the database diff changes.
- **Control-state detection:** the database-diff detector flags a control
  state write.
- **Browser-triggered verification:** the owner routes
  (`overview`, `protection`, `system`, `trades`, `bootstrap`) return 200 while
  `build_snapshot`, `run_once`, `open_stops`, `reconcile_futures` and ccxt
  `fetch_positions`/algo listing are booby-trapped.
- **Stale labelled fresh:** at 181 s the snapshot is stale, and in the future
  it is `invalid`.
- **Older overwriting newer:** a seq-older or equal write is rejected, and the
  late hung result is rejected.

## K. Live compatibility and remaining blocker

- **Captured production venue state.** This is
  `…candle-store-transaction-safety-deployment-v1-evidence/venue_active_final.json`,
  from 2026-09-28: 8 longs, 8 native algo `STOP_MARKET`, 0 ordinary orders.
  Replayed through the snapshot, it gives **VERIFIED** (8/8 verified,
  precision-valid, IDs match the journal, 10 requests). Tick sizes are not in
  the capture, so precision is **inferred from the venue-echoed trigger
  decimals**, not read from exchangeInfo.
- **Current production journal.** This was a read-only copy of 5 open futures
  trades; production is FROZEN with no `protection_snapshot` key. A venue
  echoing these rows reads VERIFIED. This proves **journal-shape
  compatibility only**: current venue stops were not read.
- **Remaining blocker.** Current production venue truth for the 5 positions
  has not been verified under this contract. It needs either an explicitly
  authorized read-only run of `build_snapshot` against the demo venue, or
  post-deployment observation of the first snapshot. Deployment itself also
  remains unauthorized and outstanding, including a restart and the watchdog
  flag procedure.

Residual design notes for review:

- **Shared ccxt instance.** The monitor shares the kernel's ccxt instance
  across threads, as the Telegram, rent and recorder threads already do.
  `enableRateLimit` throttling is not thread-coordinated. The added load is
  about 10 lightweight GETs per minute.
- **No read during a pass.** The Supervisor lock check is advisory, so a pass
  starting mid-snapshot can overlap reads. Such a snapshot reports what it saw;
  it never repairs.
- **Strictness.** Strict evidence reports a book-level PARTIAL for any orphan,
  unrecognised stop or journal drift, even when every position is protected. A
  TP/limit ordinary order on a position symbol therefore reads PARTIAL
  (`unrecognised_stop`), because reconcile's sweep would cancel it.
- **Topology.** The System topology still shows the Supervisor node from
  `supervisor_status`. No protection-monitor node was added, which avoids
  changing the reviewed graph geometry.

## L. Exact changed files

New:
- `trader/engine/protection_snapshot.py`
- `tests/test_protection_snapshot.py`
- `scripts/bench_protection_snapshot.py`
- `frontend/tests/protectionSnapshot.test.tsx`
- `frontend/tests/browser/protection-r1.spec.ts`
- `docs/superpowers/reports/2026-09-29-protection-snapshot-r1.md`
- `docs/superpowers/reports/2026-09-29-protection-snapshot-r1-evidence/performance.json`, `memory-scaling.json`
- `frontend/evidence/protection-snapshot-r1/` (browser results JSON, full Playwright log)

Modified (tracked): `trader/kernel.py` (+33: `_start_protection_monitor`,
boot start and shutdown stop).

Modified (already-uncommitted V2.1 candidate files):
- `trader/dashboard/owner_api.py`
- `tests/owner_frontend_fixture.py`
- `tests/test_owner_frontend_api.py`
- `tests/test_owner_read_cache.py`
- `frontend/src/adapters/live.ts`
- `frontend/src/adapters/contracts.ts`
- `frontend/src/views/Overview.tsx`
- `frontend/tests/live.test.tsx`
- `frontend/tests/v21.test.tsx`
- `frontend/tests/browser/v21.spec.ts`

Unchanged: `protective.py`, `reconcile.py`, `supervisor.py`, the executor,
Risk, `config.yaml`, the watchdog and candle store. There are no
protective-stop semantic or trading-decision changes.
