# LUFFY-KERNEL-BOOT-RECOVERY-FIX-V1

Date: 2026-09-27 · Implementer: Claude (Opus) · Reviewer: Astra (pending)
Base: `d46d045` · Worktree: `/home/sarmad/trader-boot-recovery-fix`
(branch `kernel-boot-recovery-fix-v1`, uncommitted)
Follows: `2026-09-27-kernel-restart-risk-v1.md` (production checkout).

Nothing was committed, deployed or restarted. Production files, the watchdog
and the production DB/venue were not written. Venue access was a read-only
allowlist snapshot. Test state lived in pytest temp dirs under `/dev/shm` and
in minimal `/dev/shm` journals; no production DB copy was made.

## A. Code change

Contract check:
- `RecoveryResult` (`trader/engine/supervisor.py`) defines `outcome`
  (`SAFE`/`RECOVERING`/`DEGRADED`/`NEEDS_OWNER`) and no `status`.
- `outcome` is what `_save()` persists into `supervisor_status`, and what the
  existing tests assert.
- No consumer anywhere reads a `status` key from a recovery result.
  `Supervisor.status()` is an unrelated method that reads the persisted blob.

```diff
--- a/trader/kernel.py
+++ b/trader/kernel.py
@@ -285,7 +285,7 @@ class Kernel:
         if recovery is not None and recovery.reasons:
-            self.notifier.send(f"🔒 recovery {recovery.status}: {', '.join(recovery.reasons)}")
+            self.notifier.send(f"🔒 recovery {recovery.outcome}: {', '.join(recovery.reasons)}")
```

Nothing else in production code changed. Supervisor, recovery, control-state,
protective-stop and reconcile code are untouched.

## B. Boot/recovery matrix (`tests/test_kernel_boot_recovery.py`)

Each case builds a real `Journal` (temp DB), `ControlStateMachine`, `Executor`
and `Supervisor` against a recording venue, then calls the real `Kernel.boot()`
(case A uses `Kernel.run()`). Only thread starts, signal handlers and the stall
monitor are recorded instead of executed. "Workers" = the four always-on boot
threads plus the stall monitor. "Entry probe" = a real `Executor.open()` call
after boot, which only the recovery ledger or the control fence can stop.

| Case | State after boot | Outcome | Reasons | Notification | Workers | Entry probe | Venue mutations |
|---|---|---|---|---|---|---|---|
| A clean (`run()`) | ACTIVE (A→F→R→A) | SAFE | — | none | started | — | none |
| B venue timeout | RECOVERY | NEEDS_OWNER | `venue_state_unreadable` | `🔒 recovery NEEDS_OWNER: …` | started | blocked `state=RECOVERY` | none (only the probe's pre-fence `set_leverage`) |
| C pending entry, order status timeout | RECOVERY | NEEDS_OWNER | `entry_recovery_pending`, `entry_recovery_owner_required`, `entry_owned_exposure_pending` | sent | started | blocked `execution_recovery_pending`; intent kept, never resubmitted | none |
| D wrong-side stop | RECOVERY | NEEDS_OWNER | `position_unprotected:BTC/USDT` | sent | started | blocked | none |
| E loosened stop | RECOVERY | NEEDS_OWNER | `position_unprotected:BTC/USDT` | sent | started | blocked | none |
| F owner FROZEN, clean protection | FROZEN (no transition) | DEGRADED | `control_state_not_supervisor_owned` (checks pass) | sent | started | blocked `state=FROZEN` | none |
| G repeated boot | see C | | | | | | |

**Regression proof:** with the one-line fix reverted, 7 of the 12 new tests
fail with `AttributeError: 'RecoveryResult' object has no attribute 'status'`
at `trader/kernel.py:288`, and case A passes. The existing
`test_kernel_boot_uses_supervisor_without_real_services` covered only the
clean case, which is why the defect shipped.

Observation, pre-existing and out of scope: `Executor._open_locked` calls
`_ensure_leverage` (`set_leverage`) **before** the control fence. In a
blocked state the kernel gate (`entry_allowed=False`) keeps it from being
reached, and the direct probe shows it is the only call made. No order is
sent.

## C. Repeated boot

- **Fault cleared between boots** (`test_g_repeated_boot_after_persisted_recovery_then_owner_resume`):
  - Boot 1 (venue timeout) persists RECOVERY + NEEDS_OWNER.
  - Boot 2 (venue healthy) makes no state transition and stays in RECOVERY,
    with `reasons == ["owner_resume_required"]` even though
    `checks.entries_safe` is true. It notifies, starts workers, and makes no
    mutation.
  - Owner `/resume` then gives RECOVERY→ACTIVE (actor `operator`).
  - Boot 3 records `owner_ack_control_event_id`, re-proves
    (A→F→R→A) and ends SAFE.
- **Fault persisting** (`test_g_repeated_boot_with_fault_persisting_stays_contained`):
  three consecutive boots with a loosened stop. Containment is recorded once
  (A→F, F→R) and then held. Each boot completes, starts workers and makes
  zero mutations.

Result: a watchdog restart after a persisted RECOVERY no longer crash-loops.
The kernel runs in RECOVERY, exits and protection continue, entries stay
blocked, and the hold stays sticky until the owner acts.

## D. Mixed-version recovery (51101d0 unchanged)

This used the existing `/home/sarmad/trader-population` worktree (HEAD
`51101d0`, `trader/` clean, bytecode writing disabled) against a `/dev/shm`
journal with `control_state=RECOVERY`:

| 51101d0 path | Result |
|---|---|
| `ControlStateMachine(journal)` (kernel and dashboard init) | `ValueError: 'RECOVERY' is not a valid ControlState` |
| dashboard `/api/chat` → `ChatEngine._set_state("ACTIVE")` | same ValueError; state stays RECOVERY |
| dashboard GraphQL `set_control_state` (resolver body; wired from `index.html`) | same ValueError; state stays RECOVERY |

Consequences:
- The **running production dashboard (51101d0) cannot move the system out of
  RECOVERY**.
- A 51101d0 kernel booted after d46d045 has persisted RECOVERY would fail in
  `Kernel.__init__`.

## E. Owner recovery interface

- **State that must exist:** `control_state=RECOVERY` with `supervisor_status`
  showing `needs_owner=true` (outcome NEEDS_OWNER), after the owner has
  confirmed the underlying fault (venue, stop, intent) is resolved.
- **Action that clears it:** a transition to ACTIVE by an owner actor
  (`operator`/`dashboard`/`chat`) through **d46d045** `ControlStateMachine`.
  The next boot/trigger pass records the acknowledgement and re-proves before
  ACTIVE can persist (case G).
- **Interfaces:**
  - Kernel Telegram `/resume` (actor `operator`). It runs inside the d46d045
    kernel process, so it works once boot completes; before this fix it could
    never start. Verified in case G.
  - d46d045 dashboard GraphQL/chat (not deployed).
  - Production 51101d0 dashboard: **cannot** (D).
- **Limits of `/resume`:**
  - It is an untyped override. It resumes entries at once, without re-running
    the proof (`supervisor.cycle()` only runs in RECOVERY). Re-proof happens
    only at the next boot or trigger.
  - Telegram is configured (token and chat id present in `.env`; no
    "unconfigured" log line). Reachability and polling are **unverified**: I
    did not send any message.
- **Typed recovery control:** none exists (for example, "re-verify now, then
  resume only if proven"). It is not required to escape the hold, but it
  would be the safe form of owner recovery.

## F. Protective / reconciliation matrix

| Case | Where | Result | Mutations |
|---|---|---|---|
| captured 8-position state (fresh read-only snapshot 2026-09-27T15:13:31Z; minimal fixture = 8 open trade rows) | fixed `Kernel.boot()` replay | ACTIVE, SAFE, no notification | **none** |
| missing stop | unit + replay (SUI) | re-armed, proved same pass → ACTIVE | 1 `create_order` stop at venue precision |
| orphan stop (flat symbol) | unit | swept → ACTIVE | 1 `cancel_algo` |
| journal/venue ID mismatch | unit | cancel venue stop + re-arm, journal id updated → ACTIVE | `cancel_algo`, `create_order` (pre-existing 51101d0 behavior) |
| loosened stop | unit (E) + replay (SUI) | RECOVERY, NEEDS_OWNER `position_unprotected:*` | none |
| incomplete venue listing (malformed algo snapshot + an orphan present) | unit + replay | RECOVERY, NEEDS_OWNER (`orphan_stop_sweep_unresolved`, `protection_snapshot_unreadable…`) | **none** (no destructive sweep) |

Test fixture note: `protective.cancel_stop` casts `algoId` to `int` (real
Binance ids are numeric). With non-numeric fake ids, the cancel fell through
to `cancel_order`. The fixtures use numeric ids.

## G. Focused tests (worktree code, venv from production)

| File | Tests |
|---|---|
| test_supervisor.py | 51 |
| test_control_state_recovery.py | 20 |
| test_entry_control_fence.py | 10 |
| test_entry_recovery.py | 25 |
| test_protective_stops.py | 23 |
| test_reconcile.py | 5 |
| test_reconcile_alignment_commits.py | 10 |
| **test_kernel_boot_recovery.py (new)** | 12 |
| **Total** | **156 passed, 0 failed** |

The full repository suite was not run; there was no focused failure to
require it.

## H. Negative controls

- With the fix reverted: **8 of 12 tests fail and 4 pass** (corrected
  2026-09-27; an earlier version of this report said 7 failures). The
  failures are the AttributeError at `kernel.py:288`, and the clean case
  passes. The tests detect the defect. Re-verified in
  `/home/sarmad/trader-owner-recovery`: reverting only
  `recovery.outcome`→`recovery.status` gives `8 failed, 4 passed`.
- The recording venue catches real actions: missing stop → `create_order`,
  orphan → `cancel_algo`, ID mismatch → `cancel_algo` + `create_order`, and
  the replay's missing SUI stop → `create_order`. The entry probe records the
  pre-fence `set_leverage` call, showing that probe calls are captured.
- A test-harness error was caught and fixed: non-numeric algo ids routed
  cancels to `cancel_order` (see F).

## I. Disk

| | `/` free | `/dev/shm` free |
|---|---|---|
| Before | 3.5 GB | 3.9 GB |
| After | 3.3 GB | 3.9 GB |

The worktree is 199 MB on `/`. The rest of the change is ordinary production
growth. `/dev/shm/luffy-bootfix` holds about 17 MB: the replay/mixed-version
scripts and the snapshot JSON. Scratch DBs were deleted after each run. No
unrelated files were touched.

## J. Deployment blockers

1. **Owner recovery path is not verified end to end.** After a failed boot,
   the only interface that can clear RECOVERY without deploying the dashboard
   is Telegram `/resume`. Its reachability is unverified, and it resumes
   entries without re-proof. Before deploying, one of these is needed:
   - the owner confirms Telegram control works (e.g. `/status` reply); or
   - the d46d045 dashboard is co-deployed (separate, not authorized); or
   - a typed "re-verify then resume" control is added.
2. **Mixed-version rollback hazard.** Once d46d045 has persisted RECOVERY,
   rolling the kernel or dashboard back to 51101d0 fails (D). A rollback plan
   must first return `control_state` to a 51101d0 state through d46d045 code.

The crash-loop defect itself is fixed.

## K. Changed files (worktree only)

- `trader/kernel.py` — one line (`status` → `outcome`)
- `tests/test_kernel_boot_recovery.py` — new, 12 tests
- `docs/superpowers/reports/2026-09-27-kernel-boot-recovery-fix-v1.md` — this report

`graphify update .` was not run: the graph lives in the production checkout,
and this change is not deployed.
