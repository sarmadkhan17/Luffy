# LUFFY-OWNER-RECOVERY-CONCURRENCY-HARDENING-V1

Date: 2026-09-27 · Implementer: Claude (Opus) · Reviewer: Astra (pending)

**Worktree:** `/home/sarmad/trader-owner-recovery` (base `d46d045`).
Everything is uncommitted. On top of the base sit:
- the staged boot fix;
- the unstaged owner-recovery package
  ([recheck v1](2026-09-27-owner-recovery-recheck-v1.md));
- this package, which is also unstaged.

**Not touched:** production, the deploy, any restart, the watchdog, and
Telegram (no message was sent). All test and replay state lived in
`/dev/shm/luffy-owner`. Production `data/luffy.db` was opened read-only
(`mode=ro`) by the replay only.

It also includes the owner's mid-task **architecture correction**:
- the owner interface is transport-neutral;
- Telegram is only an adapter;
- OpenClaw/WhatsApp come later (§K, §L).

## A. Exact concurrency fixes

| Blocker | Fix |
|---|---|
| 1. HALTED → RECOVERY → newer HALTED → ACTIVE | **(a)** Intake is one `ControlStateMachine.fenced()` step: read the state, read the intent watermark, journal the request, and move the hold into RECOVERY. No `set()` from any process or actor can land between the read and the release. **(b)** A same-state owner `HALTED` now records a `state_hold`, as FROZEN already did, so it bumps the watermark. **(c)** A HALTED after intake is a `RECOVERY→HALTED` state change. The pass then fails `control_state_changed_during_pass`, or the activation CAS fails `control_state_changed_during_activation`. **(d)** A default request never releases HALTED (§B). |
| 2. Ack outside the pass lock | Intake, acknowledgement (`supervisor_status` OWNER_REQUESTED) and the fresh pass all run under `Supervisor._pass_lock`. The request calls `_pass_once` directly. Lock order is request → pass → fence. Nothing takes the fence and then the pass lock, so there is no deadlock. A cadence `cycle()` already running finishes and saves first; the request then waits (blocking acquire, bounded by `request_wait_s=300`, audited `REFUSED supervisor_pass_busy` on timeout) and acknowledges after it. `trigger()` also takes the pass lock now, so it cannot overwrite an acknowledgement either. |
| 3. Unaudited overlap | Every refusal before intake (`request_in_progress`, `supervisor_pass_busy`) writes its own `*_requested` + `*_result` pair (§D). |
| 4. Rollback overwrote newer intent | Intake applies the operation's **own** FROZEN inside the fence and keeps that event's exact id. ACTIVE/RECOVERY→FROZEN gives a `state_change`; FROZEN→FROZEN by an owner gives a `state_hold`. It never calls an unconditional `set()` and never reads "latest" to find its hold. The final decision is re-checked under the fence (§E). |

`cycle()` is unchanged. It takes the pass lock non-blocking and **skips**
while any pass or owner request holds it, and never queues.

## B. HALTED contract

| State at intake | `request_owner_recovery(ctx)` (default) | `request_owner_recovery(ctx, allow_unhalt=True)` |
|---|---|---|
| ACTIVE | `ALREADY_ACTIVE`, no writes | same |
| FROZEN | fenced `FROZEN→RECOVERY` (owner), then fresh pass, then guarded CAS | same |
| RECOVERY | fresh pass, then guarded CAS | same |
| **HALTED** | **`REFUSED halted_requires_explicit_unhalt`**. State stays HALTED. **No pass, no venue read, no reconciliation mutation.** Audited. | fenced `HALTED→RECOVERY` (owner, operation `owner_unhalt`). The same fresh-proof requirement applies before ACTIVE. |
| unreadable | `REFUSED control_state_unreadable` | same |

- `allow_unhalt` is only reachable through an `OwnerContext` whose `actor`
  is in `OWNER_ACTORS`. `supervisor`, `risk_engine` and `macro_guard` raise
  `ValueError`, and so does any untyped input.
- A newer HALTED at any point wins:
  - before intake, it is simply the observed state;
  - during intake, it cannot happen (fence);
  - after intake, it is a state change that defeats both the pass and the
    CAS.
- Rollback preparation still refuses HALTED (`halted_manual_only`).
- Telegram:
  - `/resume` maps to the default operation. While HALTED it replies
    "still HALTED … send /unhalt".
  - `/unhalt` maps to `allow_unhalt=True`.
  - Both go through the listener's existing chat-ID authentication.
- Another explicit path already exists: owner `/freeze` (HALTED→FROZEN) and
  then `/resume`.

## C. Control-event identity strategy

- **Intent events** are `state_change` and `state_hold`
  (`control_fence.INTENT_EVENTS`). The **watermark** is the max id among
  them (`latest_intent_event_id`).
- **Every operation binds to an exact id.**
  - Owner recovery binds to its intake release event (`FROZEN/HALTED→RECOVERY`)
    or, for RECOVERY at intake, to the fenced intake watermark.
    `containment_event_id` in the acknowledgement records that id. Activation
    is `set_if_current(RECOVERY, ACTIVE, expected_control_event_id=that id)`.
  - Rollback binds to its own hold id (`OwnerRecoveryResult.hold_event_id`,
    audited as `own_hold_event_id`).
- **Same-state visibility.** `ControlStateMachine._set_fenced` records
  `state_hold` for a same-state **FROZEN or HALTED** set by an **owner
  actor** (`operator`/`dashboard`/`chat`). This is the smallest boundary:
  every owner channel funnels through it.
- **No noise.** A non-owner same-state reassertion records nothing. The
  example is the kernel's per-cycle drawdown `risk_engine` re-halt; a test
  shows 5 re-halts produce 0 events. It cannot hide intent from an owner
  operation, for two reasons:
  - intake and release are one fence step;
  - once released, any HALTED is a state change.
- **API added:**
  - `ControlStateMachine.fenced()` / `FencedControl` (`state`, `watermark`,
    `apply()` → exact event id);
  - `set()` now returns the event id (it previously returned `None`; no
    caller used the value).

## D. Owner-request audit behavior

**Every** request produces exactly one `owner_recovery_requested` (or
`rollback_prepare_requested`) row and one matching `*_result` row. This holds
when it succeeds, is refused at intake, overlaps
(`request_in_progress`), or times out (`supervisor_pass_busy`).

| Field | Where |
|---|---|
| request ID | the request row's `control_events.id`. It is returned as `request_event_id` and referenced by the result row |
| actor | `control_events.actor` (owner authority class) |
| channel / user identity | `channel`, `principal`, `request_ref`, `meta` from `OwnerContext` |
| requested operation | `operation`: `owner_recovery` \| `owner_unhalt` \| `rollback_prepare` |
| outcome / refusal | result `status`, `outcome`, `reasons` |
| watermark | `watermark_control_event_id` on both rows. On the result it is the bound id (release event or intake watermark). For an overlap refusal it is an unfenced snapshot, which is informational only |
| rollback hold | result `own_hold_event_id` |

- `OwnerContext` is transport-neutral. The core never sees a Telegram update.
- The Telegram adapter maps an update to:
  - `channel="telegram"`;
  - `principal=str(sender_id)`;
  - `request_ref="<update_id>:<message_id>"`;
  - `meta={"command": ...}`.
- The bot token never reaches `control_events`. An existing test asserts
  this.

## E. Rollback behavior (`prepare_rollback(ctx)`)

1. **Intake, under the fence.**
   - Read the state and the watermark, and journal the request.
   - HALTED or unreadable: REFUSED, with nothing changed.
   - Otherwise apply its own FROZEN and keep that exact event id as `hold`.
2. **Fresh pass** in that owner FROZEN. The Supervisor cannot activate an
   owner hold.
3. **Final check, under the fence:**
   `own = state == FROZEN and latest_intent_event_id == hold`.
   - `proved and own` → **READY** (`hold_event_id=hold`). `proved` means
     fresh `checks.entries_safe is True` and no lost race.
   - `own` but not proved:
     - an ACTIVE/RECOVERY start goes back to RECOVERY (CAS inside the same
       fence);
     - a pre-existing owner FROZEN start **stays FROZEN**. It never
       releases an owner hold into RECOVERY. This is a behavior change from
       recheck v1, which went to RECOVERY.
   - Not `own`: **BLOCKED** with `control_state_changed`, and the newer
     state is left exactly as it is.
4. **It never:**
   - adopts a newer event;
   - overwrites HALTED;
   - returns READY on parseability alone;
   - activates.

## F. Adversarial test matrix (`tests/test_owner_recovery_concurrency.py`, 31 tests)

| # | Scenario | Result |
|---|---|---|
| A | HALTED + `allow_unhalt`; owner HALTED injected right after intake (at the ack save) | HALTED, CONTAINED, 0 activations, 0 mutations |
| A2 | …HALTED injected during the fresh venue read | HALTED, `control_state_changed_during_pass` |
| B | HALTED injected after PROVED is persisted, before the CAS | HALTED, `control_state_changed_during_activation` |
| — | same-state owner HALTED/FROZEN (×3 owner actors) → `state_hold`; a CAS bound to the older watermark fails | pass |
| — | 5× non-owner `risk_engine` same-state HALTED → 0 events (no noise) | pass |
| C | FROZEN (dashboard) after intake, then same-state FROZEN (chat) during the pass | FROZEN; latest intent = chat hold; later `cycle()` and `pass_once()` don't take it over |
| D | cadence `cycle()` blocked mid-venue-read (real threads); owner request contends for the pass lock | request waits; cycle saves NEEDS_OWNER first; request intake id > cycle save id; ACTIVATED |
| E | owner request blocked mid-pass; cadence `cycle()` from another thread | cycle returns `None` immediately; exactly 1 `_pass_once`; naked stop re-armed exactly once |
| F | two owner requests overlap (threads) | 2nd `REFUSED request_in_progress` with its own request + result rows (actor, channel, principal, request_ref, operation, watermark) |
| F2 | rollback requested during an owner recovery | refused and audited as `rollback_prepare_*` |
| G | rollback observes FROZEN; newer HALTED (thread, blocked by the fence) | HALTED preserved, BLOCKED; no transition out of HALTED |
| H | rollback's own same-state FROZEN hold, then newer FROZEN (chat) | BLOCKED; `hold_event_id` ≠ newer; newer hold still latest; state FROZEN |
| I | fresh proof `entries_safe=True`, then newer FROZEN or HALTED before finalization (×2) | BLOCKED; newer state kept |
| J | clean rollback from RECOVERY | READY; `hold_event_id` = latest = its own `RECOVERY→FROZEN` row with detail `rollback preparation #<request id>` |
| J2 | clean rollback from owner FROZEN | READY with its own new `state_hold` id |
| — | rollback BLOCKED from owner FROZEN | stays FROZEN, 0 mutations |
| K | HALTED, default request, naked position | HALTED, REFUSED, 0 venue reads, 0 mutations, 0 transitions |
| L | HALTED + `allow_unhalt`, loose stop | `HALTED→RECOVERY` (operator) only; CONTAINED. After healing, only `RECOVERY→ACTIVE` by supervisor |
| — | non-owner actors with `allow_unhalt` | `ValueError`, HALTED kept |
| — | Telegram `/resume` while HALTED, then `/unhalt` | HALTED + hint, then ACTIVATED; `meta.command` audited |
| — | transport neutrality: `OwnerContext("chat", "openclaw", principal, request_ref)` | same operation, identity audited; untyped dict refused |

Stability: the file was run 15 consecutive times, 31/31 each time
(about 2.4–3.2 s). Interleavings are forced with events and hook points. The
only timed wait is a 1 s `join` in G/H, which lets an unfenced mutant land. It
does not affect the real code path.

## G. Focused test totals

| File | Tests |
|---|---|
| test_owner_recovery.py | 31 |
| **test_owner_recovery_concurrency.py (new)** | 31 |
| test_kernel_boot_recovery.py | 12 |
| test_supervisor.py | 51 |
| test_control_state_recovery.py | 20 |
| test_entry_control_fence.py | 10 |
| test_entry_recovery.py | 25 |
| test_protective_stops.py | 23 |
| test_reconcile.py | 5 |
| test_reconcile_alignment_commits.py | 10 |
| **Total** | **218 passed, 0 failed** |

Existing tests changed in this package:
- `test_owner_recovery.py`:
  - moved to the `OwnerContext` API;
  - test C patches `_pass_once` (the request no longer goes through the
    public locked wrapper);
  - the hold-release parametrization is now FROZEN (default) and HALTED
    (`allow_unhalt=True`);
  - I2 now also asserts the refused inner request is audited;
  - the Telegram audit assertion now uses channel, principal and
    request_ref.
- The full repository suite was not run.

## H. Negative controls

**Behavioral mutants.** Each mutant restores one race. Its scenario check
must fail, and each was confirmed to fail **at the intended assertion**:

| Mutant | Fails at |
|---|---|
| invisible same-state HALTED (`_set_fenced` drops the hold) | "same-state owner HALTED left no intent event" |
| acknowledgement outside the lock (the pre-fix protocol) | `state RECOVERY != ACTIVE`: the older cycle overwrote the ack, so the request stayed NEEDS_OWNER |
| adopting the latest FROZEN (the recheck-v1 `prepare_rollback`) | "rollback adopted a newer hold as its own" |
| lost HALTED during rollback (recheck-v1 `prepare_rollback`) | "newer HALTED was overwritten" |
| unaudited overlapping request | "refused overlap has no request record" |
| HALTED auto-release without explicit authorization | `ACTIVE != HALTED` |
| rollback without the final own-hold check | "READY despite a newer control event" |

**Source-level mutations.** Each was applied to the real code. The
owner-recovery and concurrency suites were then run, and the file was
restored and byte-compared. That run covered 61 tests: 31 + 30, before the
transport-neutrality test was added. The pair is now **62** (31 + 31). The
failure counts below are from that 61-test run. These mutations were not
re-run with the added test.

| Source mutation | Result |
|---|---|
| same-state hold only for FROZEN | 3 failed |
| intake/ack outside the pass lock | 3 failed |
| HALTED released without `allow_unhalt` | 2 failed |
| overlap refusal without audit | 3 failed |
| final check ignores own-hold identity | 2 failed |

**Boot-fix report correction.** Reverting only
`recovery.outcome`→`recovery.status` gives **8 failed, 4 passed** (not 7
failures). This was re-verified here, and the boot-fix report was corrected.

## I. Protective / reconciliation regression

Unit level:
- missing stop → one re-arm;
- orphan → one algo cancel;
- ID mismatch → cancel + re-arm;
- loosened → RECOVERY with no mutation;
- incomplete listing → no sweep, RECOVERY.

All pass within the 218.

**Replay.** The captured snapshot (2026-09-27T15:32:12Z: 8 positions, 8 algo
stops) plus the 8 open trade rows (production DB read-only) was replayed into
a `/dev/shm` journal:

| Case | State | Mutations |
|---|---|---|
| captured 8 positions | ACTIVE, SAFE | **none** |
| missing SUI stop | ACTIVE | one re-arm, `SUI sell 293.6 @1.0774` |
| orphan stop (DOGE, no position) | ACTIVE | one `cancel_algo 999001` |
| ID mismatch | ACTIVE | `cancel_algo` of the mismatched stop + one re-arm |
| loose SUI stop | RECOVERY, NEEDS_OWNER | none |
| incomplete listing | RECOVERY, NEEDS_OWNER | none |
| owner request after a contained boot | ACTIVATED | none |
| owner request with loose SUI | CONTAINED | none |
| rollback, captured state | **READY**, FROZEN, own hold id 9 | none |
| rollback, loose SUI | BLOCKED, RECOVERY | none |

No regression in protection behavior.

## J. Kernel-only deployment status

**Not deployed. It will be reconsidered after these local races pass Astra
review.** The four confirmed blockers are fixed and tested locally. Still
outstanding before any kernel deployment:
1. Astra review.
2. An explicit deployment authorization.
3. Owner confirmation that Telegram `/status` is reachable, because Telegram
   is the only current kernel-executed owner channel.

## K. Dashboard deployment status

**BLOCKED.** d46d045 dashboard GraphQL `set_control_state` and chat "resume"
still call `set(ACTIVE)` directly. They must be replaced with
**kernel-executed** calls to the same typed operation
(`request_owner_recovery(OwnerContext(...))`), for example as a request the
kernel consumes. The dashboard process has no venue or Supervisor. No
dashboard transport was implemented here.

## L. Remaining blockers and follow-ups

1. **LUFFY-OWNER-INTERFACE-GATEWAY-V1 (new follow-up, not implemented).**
   - Converge dashboard, LUFFY chat, Telegram and WhatsApp (via OpenClaw as
     the remote/mobile gateway) on one typed LUFFY Owner Interface API.
     `OwnerContext` + `request_owner_recovery` / `prepare_rollback` is its
     first slice.
   - Telegram stays an adapter/fallback channel.
   - Gateways receive **no** Binance credentials, `.env` contents, or
     direct Execution/Risk authority. They submit typed owner requests that
     the kernel executes.
   - Open questions for that package:
     - principal-to-actor authorization (which channel principal may hold
       `allow_unhalt`);
     - request idempotency via `request_ref`;
     - cross-process request transport.
2. **LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1 (recorded, not fixed here).**
   `_ensure_leverage` / `set_leverage` runs before the entry fence.
3. **Serialization is per kernel process.** A future cross-process requester
   is covered only by the fence, the intent-watermark CAS and the re-arm
   evidence. Owner requests should therefore be executed by the kernel
   (follow-up 1).
4. **Explicit unhalt of a drawdown halt.** An explicit owner `/unhalt` of a
   `risk_engine` drawdown HALTED can activate on a fresh SAFE proof. The next
   kernel cycle re-halts if the drawdown is still breached (a visible state
   change), but ACTIVE can exist for up to one cycle. This is owner
   authority by design; review should confirm it is acceptable.
   **Rejected by Astra (confirmed defect), fixed by
   [LUFFY-OWNER-RECOVERY-RISK-HALT-GUARD-V1](2026-09-27-owner-recovery-risk-halt-guard-v1.md):**
   every activation now needs a fresh Risk release.
5. `graphify update .` was not run in the worktree, to keep the graph cache
   untouched.

## M. Exact changed files (this package, worktree only, uncommitted)

- `trader/engine/control_fence.py`: `INTENT_EVENTS`, `latest_intent_event_id`
- `trader/engine/state.py`:
  - `FencedControl`, `ControlStateMachine.fenced()`;
  - `set()` returns the event id;
  - same-state owner HALTED `state_hold`;
  - shared watermark helper.
- `trader/engine/supervisor.py`:
  - `OwnerContext`, `OwnerRecoveryResult.hold_event_id`;
  - `trigger()` under the pass lock;
  - `_audit_request` / `_finish` / `_refused` / `_serialized`;
  - rewritten `request_owner_recovery(ctx, *, allow_unhalt)` and
    `prepare_rollback(ctx)`;
  - `_owned` uses the shared watermark helper.
- `trader/kernel.py`: the Telegram `/resume` + `/unhalt` adapter builds an
  `OwnerContext`, with no recovery semantics in the handler; plus the
  `OwnerContext` import.
- `tests/test_owner_recovery.py`: API migration and assertions (§G).
- `tests/test_owner_recovery_concurrency.py`: **new**.
- `docs/superpowers/reports/2026-09-27-owner-recovery-concurrency-hardening-v1.md`:
  **new** (this report).
- `docs/superpowers/reports/2026-09-27-owner-recovery-recheck-v1.md`:
  superseded-by note.
- `docs/superpowers/reports/2026-09-27-kernel-boot-recovery-fix-v1.md`:
  §H corrected to 8 failed / 4 passed (unstaged edit on the staged file).

Scratch space (RAM): `/dev/shm/luffy-owner/`:
- `replay_boot.py` (extended);
- the snapshot;
- `*.bak` restore copies;
- the pytest basetemp.

No database copies were kept.
