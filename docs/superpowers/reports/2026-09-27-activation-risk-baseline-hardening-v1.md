# LUFFY-ACTIVATION-AND-RISK-BASELINE-HARDENING-V1

Date: 2026-09-27 · Implementer: Claude (Opus) · Reviewer: Astra (pending)

> **Correction (V2, 2026-09-27).** Several V1 statements below are inaccurate
> and are superseded by
> [hardening V2](2026-09-27-activation-risk-baseline-hardening-v2.md):
> - §D/§E: equity rows are **not** evidence that a baseline existed. A failed
>   first cycle writes them too, so V1 could turn a failed first
>   initialization into false corruption. V2 uses a durable marker (V2 §B).
> - §A static guard: the line regex missed multiline, aliased and wrapped
>   setters. It is replaced by an AST inventory (V2 §E).
> - §D: the latch was memory-only, and `release_check` detected corruption
>   without latching it (V2 §C).
> - §D `repair_baseline`: it accepted `True`, had no actor/reason check, read
>   its floor outside any serialization, and wrote state and audit
>   separately (V2 §D).
> - §K: "READY/FROZEN with a corrupt baseline" was the defect, not a safe
>   result. A 51101d0 kernel re-seeds a corrupt `risk_state`. V2 BLOCKS it
>   (V2 §F).
> - §B/§C: the Risk release was not bound to the final ACTIVE compare-and-set
>   (V2 §A).

**Worktree:** `/home/sarmad/trader-owner-recovery` (base `d46d045`). It builds
on the uncommitted boot fix, recheck, concurrency hardening and the
[risk-halt guard](2026-09-27-owner-recovery-risk-halt-guard-v1.md). Everything
is uncommitted.

**Not done:** no production edit, no deploy, no restart, no watchdog change,
and no Telegram message. Test and replay state lived in `/dev/shm/luffy-owner`.
Production `data/luffy.db` was opened **read-only** twice: once for the replay,
and once to check that the live `risk_state` blob is valid under the new
parser. No credentials were read: tests stub the equity read.

**The rule enforced.** No kernel path may grant ACTIVE while:
- Risk refuses a fresh release;
- the Risk baseline is corrupt or not yet established;
- a newer owner or Risk containment event exists.

A genuine first initialization still works without the owner.

## A. Kernel ACTIVE-path inventory

| # | Path | Before | Now |
|---|---|---|---|
| 1 | Supervisor `_pass_once` CAS RECOVERY→ACTIVE (boot, cadence, owner recovery/unhalt, futures MacroGuard) | guarded | **guarded**, unchanged. Needs fresh venue/protection proof, a fresh `release_check`, and an exact watermark CAS |
| 2 | Futures `/resume` and `/unhalt` → `request_owner_recovery` | guarded | guarded. Now reached via `Kernel.owner_resume` |
| 3 | **MacroGuard auto-resume** `set(ACTIVE,"macro_guard")` on an observed FROZEN | **unguarded** | **Removed.** Now `Supervisor.request_macro_release` (§B) |
| 4 | **Spot `/resume`** `set(ACTIVE,"operator")` from any state, HALTED included | **unguarded** | **Removed.** Now `Supervisor.request_owner_release` (§C) |
| 5 | `Supervisor._guarded_activate` `f.apply(ACTIVE)` (new) | — | Fresh Risk, exact watermark and expected state under one fence hold. Used only by spot owner release and spot MacroGuard release |
| 6 | `ControlStateMachine` / `persisted_state` default `ACTIVE` when `control_state` is absent | implicit | **Intentional exception**, see below |
| 7 | `_acknowledge` / `_owner_ack` | reads owner ACTIVE events | not a setter |
| 8 | `prepare_rollback` | FROZEN/RECOVERY only | cannot activate (§K) |
| 9 | Dashboard `set_control_state`, chat `_set_state("ACTIVE")` | unguarded | **Out of kernel scope, still blocked** (§O) |

**Why #6 is safe.** It is the first-run default only.
- **Futures:** the boot pass contains it before any entry (ACTIVE→FROZEN→RECOVERY).
- **Spot:** the first cycle's Risk step runs before any entry. `check_entry`
  refuses unless `risk_state == "ok"`, and the kernel also blocks entries
  when `risk_state != "ok"`.

**Static guard.** `test_every_kernel_active_setter_is_inventoried` scans
`kernel.py`, `engine/`, `agents/` and `core/` for any ACTIVE setter.
- It requires exactly the two Supervisor sites (#1 and #5).
- It requires that no `"macro_guard"` or `"operator"` ACTIVE setter remains
  in `kernel.py`.
- `test_guarded_activate_is_only_reached_through_fresh_risk` pins the
  callers of #5 and its Risk and watermark checks.

## B. MacroGuard fix

The block moved from inline `cycle()` code to `Kernel._macro_step(macro)` and
`_macro_release()`.

**Freeze.**
- It is `set_if_current(ACTIVE→FROZEN, "macro_guard")`, not a blind `set`.
- Its exact event id is stored in the new kv key `macro_guard_freeze_event_id`.
- A legacy freeze with only `macro_guard_froze=1` is owned only if the latest
  intent event is still MacroGuard's own `state_change→FROZEN`. Otherwise it
  is disowned.

**Release.** MacroGuard only **requests** it:
`Supervisor.request_macro_release(freeze_event_id, venue_recovery=futures)`.
1. It takes the pass lock without blocking. If the lock is busy it returns
   `BUSY` and retries on the next cycle.
2. It reads the state and watermark under the fence. If the state is not
   FROZEN, or the latest intent event is not MacroGuard's freeze, it returns
   `REFUSED macro_freeze_not_owned`. Any newer HALTED or FROZEN wins.
3. It asks Risk freshly.
4. **Futures:** under the fence it re-checks ownership, then checks the Risk
   answer.
   - If Risk refuses, the result is `REFUSED <reason>` and FROZEN is
     untouched.
   - Otherwise it applies FROZEN→RECOVERY by `macro_guard`, saves Supervisor
     ownership bound to that event, and runs a fresh `_pass_once`. Only
     #1's CAS can activate, and it needs venue and protection proof plus Risk
     **again**.
5. **Spot:** `_guarded_activate` runs one fenced CAS FROZEN→ACTIVE, bound to
   the freeze event id and the fresh Risk answer.

**Kernel bookkeeping.**
- **Risk refusal:** MacroGuard keeps its freeze and retries. It audits
  `macro_release_refused` **only when the reason changes**, so there is no
  per-cycle noise.
- **Any other outcome** (activated, contained, superseded): the flags are
  cleared and `macro_release_result` is audited.

| Case | Result |
|---|---|
| A: breach (700) after the Risk step, before release (futures and spot) | FROZEN kept, 0 transitions, `macro_release_refused risk_halt_active`. The next Risk step moves it to HALTED |
| A2: clear at intake, breach inside the pass | RECOVERY, `CONTAINED risk_halt_active`, `checks.risk_release=false` |
| B: owner HALTED lands during the Risk read (futures and spot) | HALTED preserved. `REFUSED macro_freeze_not_owned`, flag disowned, never resumes later |
| C: owner FROZEN lands during the Risk read (futures and spot) | FROZEN (owner) preserved, the same as B |
| C′: direct stale call with the old freeze id after an owner re-freeze | `REFUSED macro_freeze_not_owned` |
| D: safe (futures) | `FROZEN→RECOVERY (macro_guard)`, then `RECOVERY→ACTIVE (supervisor)`, with `risk_release=true` and `entries_safe=true` |
| D: safe (spot) | `FROZEN→ACTIVE (macro_guard)` via the Risk-gated CAS |
| E: 5 attempts with unreadable equity | 0 transitions, 1 audit event, 0 venue mutations. It activates once equity is readable |
| E2: macro FROZEN, then Risk HALTED | Not retried, stays HALTED |

## C. Spot resume fix

**Intended semantics.** Spot now matches futures:
- `/resume` releases FROZEN or RECOVERY;
- HALTED needs an explicit `/unhalt`;
- both need a fresh Risk release, and a newer intent event wins.

**Transport neutrality.**
- `Kernel.owner_resume(ctx, allow_unhalt)` is the single typed entry point.
  Futures go to `request_owner_recovery`, and other markets go to
  `request_owner_release`.
- The Telegram adapter now only builds the `OwnerContext` and calls
  `owner_resume`.
- `test_spot_adapter_holds_no_safety_logic` asserts that the adapter's
  resume block contains no `state_machine` and no `MarketType`.

**`request_owner_release`.**
- It audits the request (`owner_recovery_requested`) and binds the watermark.
- It reads Risk fresh, outside the fence.
- Under the fence it runs `_guarded_activate`. A HALTED state without unhalt
  gives `halted_requires_explicit_unhalt`, but only if nothing newer has
  arrived. Otherwise the result is `control_state_changed`.
- It audits the result, including the `risk_release` dict.

| Case | Result |
|---|---|
| spot HALTED + drawdown: `/resume` | REFUSED `halted_requires_explicit_unhalt` |
| spot HALTED + drawdown: `/unhalt` | REFUSED `risk_halt_active` |
| spot FROZEN + `/resume` + drawdown | REFUSED `risk_halt_active` (drawdown 30.0) |
| spot safe (900): `/unhalt` | exactly `HALTED→ACTIVE (operator)`; the Risk snapshot is unchanged |
| newer HALTED (risk_engine) during spot `/resume` | HALTED wins, REFUSED `control_state_changed` |
| newer owner HALTED during spot `/unhalt` | HALTED wins |

## D. First-init vs corrupt-state contract

`RiskManager._classify()` returns one of three results:

| Class | Condition | Behavior |
|---|---|---|
| **ok** | `risk_state` parses: finite peak > 0; day start null or finite > 0; day_key null or string | normal |
| **uninitialized** | `risk_state` absent **and** no `equity` rows **and** never established in this process | Waits for the first **authoritative** (fresh venue), finite, positive equity. Release reason is `risk_baseline_uninitialized` |
| **corrupt** | Any of: present but unparseable/invalid; absent while `equity` rows exist; absent after this process established it; kv read raised | **Latched.** See §F |

**When corrupt:**
- `update_equity()` never re-seeds or saves. It returns `risk_state="corrupt"`
  with `halt_breached=False` and null drawdown and daily fields.
- `release_check()` returns `risk_state_corrupt`.
- `check_entry()` refuses (`risk_state=corrupt: entries blocked`).
- The kernel's `_risk_step()` contains an ACTIVE state:
  `set_if_current(ACTIVE→FROZEN, "risk_engine", "risk_state_corrupt")`.
  FROZEN keeps exits managed. It is FROZEN rather than HALTED because the
  true drawdown is unknown, not proven breached. A persisted HALTED stays
  HALTED.
- The latch clears **only** through explicit
  `RiskManager.repair_baseline(peak, actor=, reason=)`.
  - The peak must be at least the proven floor: the maximum of the journal's
    equity history and any peak still held in memory. A repair therefore
    cannot forget a drawdown.
  - The day baseline is rebuilt from today's first journalled equity.
  - The repair is audited as `risk_state_repaired`, including the prior
    corruption detail.
  - No transport is wired to it. That belongs to the Owner Interface gateway.

## E. Durable evidence and marker used

**No new schema and no new marker for Risk.**
- The evidence is the existing `equity` table. Only `Kernel.cycle()` writes
  to it (`log_equity`), and always after `update_equity()`. Any row therefore
  proves that a baseline was established.
- Production has a valid `risk_state` and 35 664 equity rows (read-only
  check). It classifies as **ok**, so no migration is needed.

**New kv key for MacroGuard: `macro_guard_freeze_event_id`.**
- It is not a schema change.
- The legacy value `macro_guard_froze=1` is handled as described in §B.

**Residual limit.** If `risk_state` and the entire `equity` table were both
lost, the journal is indistinguishable from a new one and would
first-initialize. Closing that gap would need a marker held outside the
journal.

## F. Corrupt-state matrix

Setup: peak 1000 established, 3 equity rows, the blob corrupted, then a
kernel restart at equity 700.

Corruptions tested:
- `malformed_json`
- `null_peak`
- `nan_peak`
- `negative_day`
- `missing_with_history`

| Path | Result (every corruption) |
|---|---|
| boot | RECOVERY, `risk_state_corrupt` |
| cycle Risk step ×3 | `risk_state=corrupt`, no HALT, no reseed |
| cadence ×3 | `risk_state_corrupt` each pass |
| owner `/resume` | CONTAINED `risk_state_corrupt` |
| owner `/unhalt` | REFUSED `risk_state_corrupt` |
| equity 1200 (above the old peak), then the above again | still never ACTIVE |
| running kernel or spot (ACTIVE, no boot pass) | `ACTIVE→FROZEN (risk_engine)`, then never ACTIVE; `check_entry` refuses |
| MacroGuard release (blob corrupted at runtime) | FROZEN kept; `macro_release_refused risk_state_corrupt` audited once |
| runtime deletion after init | corrupt, not first-init |
| valid-looking low blob (peak 700) written after the latch | still corrupt |
| repair at 700 | `ValueError` (below the proven floor of 1000); still corrupt, blob untouched |
| repair at 1000 | the next Risk step at 700 HALTs (drawdown remembered). `/unhalt` at 700 is refused; at 900 it activates |

In every case:
- the blob is byte-identical;
- `_peak_equity` is never 700;
- there is exactly **one** `risk_state_corrupt` audit event, carrying the
  cause, the raw blob (truncated), and `equity_rows=3, max_equity=1000`.

## G. First-initialization result

Fresh journal (no `risk_state`, no equity rows), for both futures and spot:
1. **Futures boot** gives RECOVERY with `risk_baseline_uninitialized`, not
   corrupt.
2. **First cycle, venue read fails:** the result is `uninitialized`. Nothing
   is written, and nothing is invented from the fallback of 0.
3. **Next cycle, fresh 1000:** the baseline is established once (peak 1000,
   drawdown 0).
4. **Activation:** futures cadence activates. Spot stays ACTIVE with no
   deadlock.

Also:
- 0 `risk_state_corrupt` events;
- the peak later advances normally to 1100;
- `check_entry` returns ok.

`update_equity` refuses to initialize from 0, −1, NaN or inf, or with
`authoritative=False`.

## H. Cadence result

| Case | Result |
|---|---|
| active breach, several passes | contained, `risk_halt_active` (existing `test_h`) |
| corrupt baseline, several passes | contained, `risk_state_corrupt`, never cleared (§F) |
| real first init | baseline established once, then cadence activates (§G) |
| genuine recovery | activates only with fresh proof (existing `test_h` and the stale-clearance test) |

The mutant in which "cadence heals corrupt" is caught (§M).

## I. Previously-approved-entry result

For each of three paths (macro, spot `/resume` + `/unhalt`, owner unhalt):
1. `check_entry` approves while ACTIVE.
2. A drawdown moves the state to HALTED.
3. A release is attempted.
4. `Executor.open` is called.

Result:
- never ACTIVE;
- `skip_reason=state=HALTED`;
- **0 `create_order`**.

`set_leverage` still runs before the final fence. It is pinned and unchanged
(LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1).

## J. Baseline-preservation result

Peak 1000 and the day baseline stay unchanged across all of these, both
refused and successful:
- MacroGuard refusal;
- owner unhalt refusal;
- spot unhalt refusal;
- spot release;
- futures macro freeze and release.

`release_check` stays read-only. Existing test `test_i` (owner, cadence and
rollback paths) still passes.

## K. Rollback regression

`prepare_rollback` is unchanged.

**Tests:**
- With a corrupt baseline, it gives READY/FROZEN.
- `cycle()` returns None; `pass_once()` and `_risk_step()` keep FROZEN.
- There is no ACTIVE, and the blob is not reseeded.
- The existing breach-rollback test and the replay (READY / BLOCKED) are
  unchanged.

**Rationale.** READY is an owner FROZEN hold. It grants no entry authority,
and in this kernel only #1 and #5 can leave it, both Risk-gated.

**Caveat for Astra.** Rolling back to the old `51101d0` kernel removes this
protection:
- its `_load_state` "starts clean" on a corrupt blob and re-seeds on its
  first cycle;
- its `/resume` sets ACTIVE directly.

Rollback preparation does not activate, but the old kernel's own owner resume
could. A corrupt baseline should be repaired **before** any rollback, and
that should be stated in the runbook.

## L. Focused test totals

| Suite | Tests |
|---|---|
| **test_activation_risk_baseline.py (new)** | 61 |
| test_owner_recovery_risk_guard.py | 47 |
| test_owner_recovery_concurrency.py | 31 |
| test_owner_recovery.py | 31 |
| test_kernel_boot_recovery.py | 12 |
| test_risk_state_persistence.py | 8 |
| supervisor, control_state_recovery, entry_control_fence, entry_recovery, protective_stops, reconcile, reconcile_alignment_commits, attention_telemetry, margin_caps, phase0, entry_geometry_end_to_end, single_creation_path | 232 |
| **Total** | **422 passed, 0 failed** |

**Pre-existing, unrelated:** `test_partial_close_shrinks_notional.py` gives
4 failed, the same as on the unmodified base. The full repository suite was
not run.

**Existing tests changed. None was weakened:**

`test_owner_recovery_risk_guard.py`:
- The reason `risk_baseline_unreadable` became the more specific
  `risk_state_corrupt`.
- `risk_cycle` now calls the real `Kernel._risk_step` instead of a copy.

`test_risk_state_persistence.py`:
- `test_corrupt_state_does_not_crash_the_risk_manager` asserted the defect
  itself: that corrupt state re-seeds to drawdown 0.
- It was replaced by `..._and_is_never_reseeded`, which asserts no crash, no
  reseed, and that the corrupt blob is not overwritten.

`test_control_state_recovery.py` and `test_attention_telemetry.py`:
- Their Risk doubles stubbed the old interface (`_fetch_balance` and a
  one-argument `update_equity` without `risk_state`).
- They now stub `_fetch_balance_fresh` and return `risk_state="ok"`. Their
  assertions are unchanged.
- Without this update, the real cycle hit the network code path, which
  includes a 3-second retry.

**Product behavior change:** `check_entry` refuses while the baseline is
uninitialized or corrupt. A caller that never ran a cycle and has no journal
still initializes on its first call, so backtests and `RiskManager(cfg, None)`
are unaffected.

## M. Negative-control results

**Behavioral mutants** (same scenario checks; each fails at its assertion,
and each check passes on the real code):

| Mutant | Check |
|---|---|
| MacroGuard direct `set(ACTIVE)` | macro breach; newer-FROZEN |
| MacroGuard Risk-checked but not watermark-bound (stale observation) | newer-FROZEN |
| MacroGuard without fresh Risk | macro breach |
| spot direct `set(ACTIVE)` | spot breach; spot newer-HALTED |
| spot without fresh Risk | spot breach |
| corrupt treated as "start clean" (re-seeded) | corrupt strict |
| missing-with-history treated as first init | missing first-init |
| cadence/cycle "heals" corrupt state | corrupt strict |
| corrupt with Risk bypassed | corrupt strict |

Result: 11 of 11 caught.

**Source mutations.** Each was applied to the real code and run over 190
tests (new, risk-guard, owner, concurrency, boot, persistence). Each file was
then restored and SHA-256 verified.

| Mutation | Failed |
|---|---|
| S1: `_macro_release` sets ACTIVE directly | 14 |
| S2: `_guarded_activate` ignores Risk | 7 |
| S3: `_guarded_activate` ignores the watermark | 3 |
| S4: macro intake ignores Risk (futures) | 4 |
| S5: missing blob classified as first init | 7 |
| S6: `update_equity` re-seeds when corrupt | 19 |
| S7: no ACTIVE→FROZEN containment on corrupt | 2 |
| S8: spot `owner_resume` sets ACTIVE | 10 |
| S9: spot release binds the current watermark instead of the observed one | 1 |
| S10: `release_check` ignores the corrupt latch | 1 |

**Replay.** The captured 8-position snapshot (synthetic equity) gave results
identical to the previous package. That covers all protective cases, owner
ACTIVATED/CONTAINED, rollback READY/BLOCKED, and breach 700/750 REFUSED with
900 ACTIVATED at peak 1000.

## N. Kernel-only deployment status

**Not deployed.** It is reconsiderable only after all of:
- Astra reviews concurrency v1, the risk-halt guard and this package;
- explicit authorization;
- the owner confirms Telegram `/status` works.

Deploy impact:
- Production's `risk_state` is valid, so no false corruption on boot.
- The MacroGuard `macro_guard_froze` value is currently `0`, so there is no
  legacy freeze to adopt.

## O. Dashboard deployment status

**BLOCKED, unchanged.** Dashboard `set_control_state` and chat `resume` still
set ACTIVE directly, from other processes. They must call the kernel-side
`owner_resume(OwnerContext(...))`. That work is LUFFY-OWNER-INTERFACE-GATEWAY-V1.

## P. Remaining blockers

1. Dashboard and chat direct ACTIVE setters, and the Owner Interface gateway
   (not started).
2. LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1: `set_leverage` runs before the final
   fence.
3. **No owner transport for `repair_baseline`.** A corrupt baseline stays
   contained until a gateway, or an authorized one-off script, calls it.
   This is intended fail-closed behavior, but it needs a runbook.
4. The rollback-to-51101d0 caveat (§K).
5. **Pre-existing, not changed:** for spot, `_fetch_balance_fresh` tries the
   futures `fapi/v3/account` endpoint before the ccxt spot balance, so a spot
   kernel could read futures equity. The same applies to spot sizing. Spot is
   outside the current demo scope.
6. The §E residual: losing both `risk_state` and all equity rows looks like
   a first run.
7. `graphify update .` was not run. `graphify-out/cache/last_query_stamp` was
   already dirty.

## Q. Exact changed files (this package)

- `trader/engine/risk.py`: `_classify`, `_parse`, `_prior_baseline_evidence`,
  `_mark_corrupt`, `repair_baseline`, corrupt-safe `update_equity(authoritative=)`,
  `release_check` reasons, and the `check_entry` refusal
- `trader/engine/supervisor.py`: `_guarded_activate`,
  `request_owner_release`, `request_macro_release`
- `trader/kernel.py`: `_risk_step`, `_macro_step`, `_macro_release`,
  `_macro_disown`, `_macro_freeze_event_id`, `owner_resume`, the Telegram
  adapter, and the cycle wiring (`risk_state` entry block, None-safe daily
  P&L)
- `tests/test_activation_risk_baseline.py` (**new**)
- `tests/test_owner_recovery_risk_guard.py`
- `tests/test_risk_state_persistence.py`
- `tests/test_control_state_recovery.py`
- `tests/test_attention_telemetry.py`
- `docs/superpowers/reports/2026-09-27-activation-risk-baseline-hardening-v1.md`
  (**new**)

Scratch space (RAM), `/dev/shm/luffy-owner/`:
- `mutate.py` (the source-mutation harness);
- `run*.txt`;
- the pytest basetemp.
