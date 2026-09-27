# LUFFY-OWNER-RECOVERY-RECHECK-V1

Date: 2026-09-27 · Implementer: Claude (Opus) · Reviewer: Astra (pending)

> **Partly superseded by
> [LUFFY-OWNER-RECOVERY-CONCURRENCY-HARDENING-V1](2026-09-27-owner-recovery-concurrency-hardening-v1.md).**
> That package changes three things described below. HALTED is no longer
> released by an ordinary request; it needs explicit `allow_unhalt`.
> The API signature is now `request_owner_recovery(ctx: OwnerContext, *,
> allow_unhalt)` / `prepare_rollback(ctx)`. Rollback preparation now keeps
> its own hold identity. Where they differ, that report governs.
Worktree: `/home/sarmad/trader-owner-recovery` (branch
`owner-recovery-recheck-v1`, base `d46d045`, uncommitted).

- The boot fix (LUFFY-KERNEL-BOOT-RECOVERY-FIX-V1) is kept as a separate
  **staged** change: `git diff --cached` shows it.
- This package is the **unstaged** change: `git diff`, plus the untracked
  test file.
- The original boot-fix worktree was left untouched.

## A. Recovery transition contract (written before coding)

Operation: `Supervisor.request_owner_recovery(actor, *, source, request=None)
-> OwnerRecoveryResult`. `actor` must be in `OWNER_ACTORS`
(`operator`/`dashboard`/`chat`). There is no state argument: the only target
is a *guarded* ACTIVE.

| State at intake (read under the control fence) | Step 1: contain | Step 2: fresh proof | Step 3: activate |
|---|---|---|---|
| `ACTIVE` | none | none | none → `ALREADY_ACTIVE` (no rewrite) |
| unreadable | none | none | none → `REFUSED control_state_unreadable` |
| `FROZEN` / `HALTED` (owner or other hold) | CAS `state→RECOVERY` (actor = owner); the request **is** the owner releasing the hold into containment, never into ACTIVE. CAS loses → `REFUSED control_state_changed` | fresh `pass_once` | supervisor CAS `RECOVERY→ACTIVE` |
| `RECOVERY` | none; watermark = latest `state_change/state_hold` id | fresh `pass_once` | supervisor CAS `RECOVERY→ACTIVE` |

Rules:

1. **Audit first.** `owner_recovery_requested` (actor, source, request
   metadata, observed state) is written before any change. Every request ends
   with `owner_recovery_result` (outcome, reasons, activated, final state).
2. **The owner acknowledgement clears only the approval hold.** The request
   replaces `supervisor_status` with a record that has `needs_owner=false`,
   `safe_to_activate=false` and `outcome=RECOVERING`, and hands containment
   ownership to the Supervisor, bound to the watermark event id. Nothing about
   the prior status (including a stale `SAFE`) is carried into the decision.
3. **Fresh proof.** `pass_once(advance_entry=False)` rereads positions, the
   ledger, the orphan sweep, and every position's listed protection. It never
   ticks entry recovery (the kernel cycle does that under its own locks).
4. **Activation happens only inside that fresh pass.** It uses the existing
   supervisor CAS `set_if_current(RECOVERY, ACTIVE,
   expected_control_event_id=watermark)`, after the proof is persisted. So:
   - a fresh `NEEDS_OWNER`/`DEGRADED`/`RECOVERING` result leaves the state
     contained;
   - any `state_change`/`state_hold` after the watermark (a concurrent owner
     FROZEN/HALTED, a new containment, another transition) makes the CAS fail
     and there is no activation;
   - a control-state change observed during the pass counts as a lost race.
5. **No ACTIVE except through step 4.** The request never calls
   `set(ACTIVE)`. The state is non-ACTIVE from intake until the guarded CAS,
   so the executor fence blocks every entry in between.
6. **Serialization.**
   - One request at a time per process: a second request gets
     `REFUSED request_in_progress`.
   - `pass_once` is serialized by a supervisor pass lock. The kernel's
     cadence `cycle()` skips (non-blocking) while another pass runs, so two
     reconciles in one process can never race to re-arm the same stop.
   - Cross-process races are covered only by the CAS and re-arm evidence.
7. **Non-serious unproved results** (for example
   `protection_awaiting_verification`) stay supervisor-owned RECOVERY. They
   may activate at a later cadence pass, but only on that pass's own fresh
   proof. That is existing Supervisor semantics.

Rollback conversion: `Supervisor.prepare_rollback(actor, *, source)`.

1. CAS the current `ACTIVE`/`RECOVERY` state to owner `FROZEN`. If the state
   is already `FROZEN`, record an owner `state_hold`.
2. Run a fresh pass in that owner-held FROZEN. The Supervisor cannot activate
   an owner hold.
3. Result:
   - **READY** only if `checks.entries_safe` is fresh and true, and no state
     event followed the hold. The state stays `FROZEN`, which 51101d0 can
     parse.
   - Otherwise **BLOCKED**, and the state is CAS'd back to `RECOVERY` so the
     d46d045 kernel keeps re-verifying.
4. `HALTED` is refused, because it is manual-only.
5. Audit events: `rollback_prepare_requested` / `rollback_prepare_result`.

Design choices for review:

- **`/resume` from a pre-existing FROZEN/HALTED.** It is treated as the
  owner releasing that hold *into containment*, never directly to ACTIVE.
  d46d045 `/resume` released it straight to ACTIVE. "Owner hold wins" is
  enforced against any hold recorded **after** the request's watermark.
- **After an owner request,** a non-serious unproved result stays
  Supervisor-owned. It may activate at a later cadence pass on that pass's
  own fresh proof.

## B. Production-code changes (unstaged diff on top of the staged boot fix)

`trader/engine/supervisor.py` (+164/−2):
- `OwnerRecoveryResult`: frozen dataclass with a typed `status`.
- `_pass_lock`: `pass_once()` is now a locked wrapper around the unchanged
  body, renamed `_pass_once()`. `cycle()` takes the lock non-blocking and
  skips while another pass runs.
- `_request_lock` for owner requests.
- `request_owner_recovery()`, `prepare_rollback()`, and the helpers
  `_intake`, `_finish` and `_latest_state_event`.
- Nothing in the existing pass logic, activation CAS or reconcile changed.

`trader/kernel.py` (+23/−3, unstaged):
- The listener passes the Telegram update to `_handle_tg_command(msg, base,
  update=None)`.
- Futures `/resume` calls
  `supervisor.request_owner_recovery("operator", source="telegram",
  request={command, update_id, message_id, sender_id})` and replies with the
  typed result. It never calls `set(ACTIVE)`.
- The non-futures (spot) `/resume` path is unchanged, because spot has no
  Supervisor recovery.
- The chat-ID check is unchanged.

No dashboard or HTTP code changed. No generic RECOVERY→ACTIVE path was added.

## C. Owner-recovery matrix (`tests/test_owner_recovery.py`, real Supervisor + Executor, recording venue)

| Case | Result | State | Mutations |
|---|---|---|---|
| A RECOVERY, fault cleared | ACTIVATED (SAFE); fresh venue read asserted; only transition `RECOVERY→ACTIVE` by `supervisor`; request + result audit events | ACTIVE | none |
| B fault remains (loose stop) | CONTAINED, NEEDS_OWNER, `position_unprotected` | RECOVERY | none; entry probe blocked |
| C stale reason, fresh state safe | exactly one `pass_once(advance_entry=False)`, then ACTIVATED | ACTIVE | none |
| C2 stale persisted SAFE status + loose stop | CONTAINED, NEEDS_OWNER | RECOVERY | none |
| G loosened protection | CONTAINED | RECOVERY | **zero** |
| H unresolved entry intent (order status timeout) | CONTAINED, `entry_recovery_pending`; ledger unchanged (verified, not ticked) | RECOVERY | none |
| I repeated requests (fault) | 2× CONTAINED; 2 request + 2 result audit events | RECOVERY | none |
| I repeated requests (missing stop) | ACTIVATED then ALREADY_ACTIVE | ACTIVE | exactly one re-arm |
| J already ACTIVE | ALREADY_ACTIVE; no transition, status blob untouched, no venue read | ACTIVE | none |
| owner FROZEN / HALTED at intake | `hold→RECOVERY` (owner), then fresh proof, then `RECOVERY→ACTIVE` (supervisor) | ACTIVE | none |
| non-owner actor (`supervisor`, `macro_guard`) | `ValueError` | — | — |

## D. Concurrency

| Case | Result |
|---|---|
| D FROZEN (`dashboard`) injected during the recheck's venue read | FROZEN kept; `control_state_changed_during_pass`; no activation |
| D2 FROZEN (`chat`) injected after the proof is persisted, before the CAS | FROZEN kept; `control_state_changed_during_activation` |
| E new containment (FROZEN→RECOVERY by supervisor) during recheck | RECOVERY; never `RECOVERY→ACTIVE` (watermark CAS) |
| E2 new fault (stop loosened) during recheck | RECOVERY, NEEDS_OWNER |
| F venue unreadable during recheck | RECOVERY, `venue_state_unreadable`, no mutation |
| I2 second owner request + kernel cadence `cycle()` inside a running request | inner request `REFUSED request_in_progress`; `cycle()` returns None; outer ACTIVATED; exactly one re-arm |

## E. Entry safety (real `Executor.open`, the final entry boundary)

- **During the recheck**, an entry is offered on every per-symbol listing:
  all blocked (`state=RECOVERY: entries blocked`), 0 orders.
- **After a refusal:** blocked, 0 orders.
- **After ACTIVATED:** the same probe submits exactly one ordinary entry
  (`ETH/USDT buy`, `reduceOnly=False`) under the normal guards.
- **Kernel-level gating:** `entry_allowed = state==ACTIVE` is already covered
  by `test_kernel_cycle_in_recovery_blocks_entries_but_manages_exits`.
  `Kernel._try_enter` itself was not driven.
- **Not fenced (known, unchanged):** `_ensure_leverage` / `set_leverage` runs
  before the fence and shows up in the probe's recorded mutations. That
  remains a separate issue.

## F. Telegram handler

- `/resume` with a loose stop keeps RECOVERY. Reply:
  `🔒 still RECOVERY: CONTAINED NEEDS_OWNER — position_unprotected:BTC/USDT`.
- After healing: `🙂 ACTIVE — fresh recovery check proved safe.` Again:
  `ℹ️ already ACTIVE — nothing changed.`
- Audit detail is
  `{"command": "/resume", "update_id": 77, "message_id": 5, "sender_id": 42}`
  with `source=telegram`. The bot token (in `base`) appears in no
  control-event row.
- Listener chat-ID authorization: an update from a foreign chat never reaches
  the handler, and the configured chat's update does, with its update object.
- Every reply was captured by a stubbed `requests.post`. No Telegram message
  or request was sent.

## G. Rollback compatibility

`prepare_rollback()` behavior:

| Case | Result |
|---|---|
| RECOVERY, fault cleared | **READY**: `RECOVERY→FROZEN` (operator); fresh `checks.entries_safe=true`; `rollback_prepare_*` audit rows; `supervisor_status` kept |
| ACTIVE, clean | READY: `ACTIVE→FROZEN`. A later d46d045 boot keeps FROZEN (the Supervisor never takes over an owner hold) |
| fault remains | **BLOCKED**, returned to RECOVERY, no mutation |
| HALTED | REFUSED `halted_manual_only` |

Isolated 51101d0 startup ran as a subprocess, using the `trader/` package
extracted with `git archive 51101d0` into pytest temp, against the same
journal:

- **Before conversion (RECOVERY):** it fails with `'RECOVERY' is not a valid
  ControlState`.
- **After conversion (FROZEN):**
  - `ControlStateMachine` loads FROZEN, `can_enter=False`,
    `manages_exits=True`;
  - 51101d0 `reconcile_futures` makes 0 mutations;
  - the old owner resume (`set(ACTIVE, "dashboard")`) works.

Not exercised: the full 51101d0 `Kernel.__init__` (feeds, LLM, Telegram).

**Rollback: conditionally safe.** It is safe only through
`prepare_rollback()` → READY. Otherwise it stays BLOCKED.

> **V2 contract change (2026-09-27).** READY now also requires a verified Risk
> baseline, because 51101d0 re-seeds a corrupt `risk_state`. The sequence is:
> 1. A fresh authoritative Risk check: the baseline must be valid and
>    unlatched. A latched corruption needs `repair_baseline()` first;
>    otherwise the result is BLOCKED with no venue pass.
> 2. The fresh protection and reconciliation pass.
> 3. Under the fence: verify the intake containment is still authoritative,
>    re-verify the baseline, then establish a new final exact FROZEN hold.
>    Verify that final hold and retain the Risk/journal transaction through
>    its READY audit. A newer intent always blocks finalization.
>
> See
> [hardening V2 §F](2026-09-27-activation-risk-baseline-hardening-v2.md).

## H. Boot-fix regression

- The staged diff still carries exactly the
  `recovery.status` → `recovery.outcome` line.
- All 12 boot tests pass here. They also pass (12/12) unchanged in
  `/home/sarmad/trader-boot-recovery-fix`.
- One intentional follow-on edit (unstaged) to boot test G: its `/resume`
  step now expects guarded activation by `supervisor` (was a direct
  `operator` ACTIVE). It stubs `requests.post`; the earlier version made an
  outbound attempt to `telegram.invalid`.

## I. Protective / reconciliation regression (owner-recovery code)

Unit level (boot tests): missing stop → one re-arm → ACTIVE; orphan → one
algo cancel; ID mismatch → cancel + re-arm; loosened → RECOVERY, no mutation;
incomplete listing → no sweep, RECOVERY.

Replay of a fresh read-only snapshot (2026-09-27T15:32:12Z, 8 positions,
8 algo stops) with a minimal `/dev/shm` journal holding only the 8 open
trades:

| Case | Result | Mutations |
|---|---|---|
| captured state | ACTIVE, SAFE | **none** |
| missing SUI stop | ACTIVE | one re-arm at venue precision |
| loose SUI stop | RECOVERY, NEEDS_OWNER | none |
| incomplete listing | RECOVERY, NEEDS_OWNER | none |
| owner request after a contained boot | ACTIVATED | none |
| owner request with loose SUI stop | CONTAINED | none |

## J. Focused tests

| File | Tests |
|---|---|
| test_supervisor.py | 51 |
| test_control_state_recovery.py | 20 |
| test_entry_control_fence.py | 10 |
| test_entry_recovery.py | 25 |
| test_protective_stops.py | 23 |
| test_reconcile.py | 5 |
| test_reconcile_alignment_commits.py | 10 |
| test_kernel_boot_recovery.py | 12 |
| **test_owner_recovery.py (new)** | 31 |
| **Total** | **187 passed, 0 failed** |

## K. Negative controls

Each mutant is run through the same scenario check, and the check must fail
(`pytest.raises(AssertionError)`, all pass):

| Mutant | Caught by |
|---|---|
| direct `set(ACTIVE)` from `/resume` | fault-remains check |
| activation without a fresh Supervisor pass (ack + CAS only) | fault-remains check |
| activation despite a concurrent FROZEN (plain `set(ACTIVE)` on fresh `entries_safe`) | concurrent-hold check |
| reuse of a stale persisted SAFE | stale-SAFE check |

Also:
- The real d46d045 `/resume` handler body, substituted at runtime, is caught.
  The guarded handler passes the same check.
- Source-level: with the old direct `/resume` restored in `kernel.py`, 3 tests
  fail (both Telegram tests and boot test G). With the guarded code restored,
  all pass.

## L. Disk

| | `/` free | `/dev/shm` free |
|---|---|---|
| Before | 3.3 GB | 3.9 GB |
| After | 3.1 GB | 3.9 GB |

- The new worktree is 199 MB on `/` (now **94 %** used).
- `/dev/shm/luffy-owner` is 16 KB (replay script and snapshot).
- No DB copies were made. Scratch journals were deleted after each run.
- A hook rewrote `graphify-out/cache/last_query_stamp` in both worktrees; I
  restored it with `git checkout`.
- No unrelated files were cleaned.

## M. Remaining deployment blockers

1. **d46d045 dashboard owner controls are still unguarded.** GraphQL
   `set_control_state(state)` and chat "resume" still call `set(ACTIVE)`
   directly and accept RECOVERY→ACTIVE. Before any dashboard deployment they
   must route to `request_owner_recovery`. That needs a design decision,
   because the dashboard process has no venue or Supervisor, so it would
   probably enqueue a request for the kernel to execute. Not done here, as
   instructed.
2. **Telegram reachability is unverified** (no live message sent). The owner
   should confirm `/status` works before relying on `/resume` as the recovery
   interface.
3. **Kernel deployment and restart are not authorized.** The production
   running dashboard (51101d0) still cannot act on RECOVERY.
4. **Serialization is per process only.** A future cross-process requester is
   covered only by the CAS and re-arm evidence.
5. **Known, unchanged:** leverage is set before the entry fence.

The boot crash-loop fix and guarded owner recovery are ready for Astra review.

## N. Changed files (worktree only, uncommitted)

Staged (boot fix, distinct):
- `trader/kernel.py` (`status`→`outcome`)
- `tests/test_kernel_boot_recovery.py`
- `docs/superpowers/reports/2026-09-27-kernel-boot-recovery-fix-v1.md`

Unstaged / untracked (this package):
- `trader/engine/supervisor.py`
- `trader/kernel.py` (Telegram)
- `tests/test_kernel_boot_recovery.py` (test G resume step)
- `tests/test_owner_recovery.py` (new)
- `docs/superpowers/reports/2026-09-27-owner-recovery-recheck-v1.md`
