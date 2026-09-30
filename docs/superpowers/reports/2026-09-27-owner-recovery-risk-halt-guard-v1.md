# LUFFY-OWNER-RECOVERY-RISK-HALT-GUARD-V1

Date: 2026-09-27 · Implementer: Claude (Opus) · Reviewer: Astra (pending)

**Worktree:** `/home/sarmad/trader-owner-recovery` (base `d46d045`). It
builds on:
- the staged boot fix;
- [recheck v1](2026-09-27-owner-recovery-recheck-v1.md);
- [concurrency hardening v1](2026-09-27-owner-recovery-concurrency-hardening-v1.md).

Everything is uncommitted.

**Not done:** no production edit, no deploy, no restart, no watchdog change,
and no Telegram message. All test and replay state lived in
`/dev/shm/luffy-owner`. The replay opened production `data/luffy.db`
read-only. It did not touch `.env` or credentials: the tests stub the equity
read and replace `Env.binance_keys` with dummies.

**Confirmed defect:** HALTED (drawdown) → `/unhalt` → RECOVERY → safety
passes → ACTIVE, while `update_equity()` still reported `halt_breached=True`.
The same happened through HALTED → owner FROZEN → `/resume`.

## A. Risk-release contract (written before coding)

ACTIVE is reached from RECOVERY, FROZEN or HALTED only by the Supervisor's
single CAS in `_pass_once`. That CAS requires all of the following **in the
same pass, just before the CAS**:

1. The fresh venue, protection and entry-intent proof is safe
   (`checks.entries_safe`, not `needs_owner`), and the state is
   supervisor-owned.
2. **Risk permits ACTIVE now.** That means a fresh
   `RiskManager.release_check(fresh venue equity)` returns `allowed=True`.
3. No intent event has appeared since the bound watermark (the existing CAS).

Rules:
- **Fresh.** Risk is asked once per activation attempt, in that pass. No
  earlier clearance is stored or reused. `supervisor_status` records it only
  as evidence.
- **Active breach** → contained: `reasons += risk_halt_active` and
  `checks.risk_release=False`.
- **Unreadable** → contained. This covers:
  - equity unread, 0, NaN or ±inf, or non-numeric (`risk_equity_unreadable`);
  - durable baseline missing or corrupt (`risk_baseline_unreadable`);
  - Risk raising, or an untyped answer (`risk_state_unreadable`);
  - no gate wired (`risk_gate_unconfigured`).

  Nothing unknown becomes safe.
- **HALTED + explicit unhalt** is permission to *try*.
  - At intake, Risk is asked first (outside the fence, because it is a venue
    read). If Risk does not allow release, the request is `REFUSED <risk
    reason>`, the state **stays HALTED**, and there is no reconciliation.
  - If Risk allows it, HALTED→RECOVERY, and the pass asks Risk **again**
    before the CAS. The intake check is only advisory.
- **FROZEN cannot hide a Risk halt.** FROZEN→RECOVERY is allowed, but the
  activation CAS needs the same fresh Risk release. The next kernel cycle
  re-halts (`RECOVERY→HALTED` by `risk_engine`).
- **Cadence, boot and owner request share one rule.** All three reach
  ACTIVE only through that one CAS. Every Supervisor pass on an ACTIVE state
  first contains it (ACTIVE→FROZEN→RECOVERY), so no pass has a
  non-Risk-checked route to ACTIVE.
- **A newer HALTED always wins** (concurrency v1). A Risk clearance followed
  by a newer HALTED still loses the CAS.
- **Baselines are never reset.** `release_check` is read-only. The peak and
  the day baseline move only through the kernel cycle's normal
  `update_equity()`.

## B. Authoritative Risk check

Risk HALTs on exactly one condition:
`drawdown = (peak − equity) / peak ≥ halt_drawdown_pct`.
- `update_equity()` computes it as `halt_breached`, and the kernel cycle sets
  HALTED.
- `check_entry()` raises `RiskError` on the same flag, and the kernel sets
  HALTED.

The daily-loss breaker only blocks entries. It never HALTs, so it is not a
release condition.

No read-only API existed. `update_equity()` mutates the peak and the day
baseline. I added the smallest one:

`RiskManager.release_check(equity) -> RiskRelease(allowed, reason, equity,
peak_equity, drawdown_pct, halt_drawdown_pct)`

- It uses the same halt rule.
- The peak is **max(durable `risk_state.peak_equity`, in-memory peak)**, the
  more conservative of the two.
- It fails closed when the durable baseline is missing or corrupt, even if
  the in-memory peak is healthy. The first test run found an in-memory
  fallback on a `NaN`/`-1` durable peak; that was fixed before these results.
- It fails closed when equity is not a finite positive number.
- It never writes.

**Fresh equity.** `Kernel._fetch_balance()` silently falls back to the last
journalled equity. That is acceptable for sizing, but not for Risk release.
- I split out `Kernel._fetch_balance_fresh() -> float | None`: the venue read
  only, same v3 → ccxt order, same retry.
- `_fetch_balance()` is now `fresh or _last_equity_fallback()`, so its
  behavior is unchanged.
- `Kernel._risk_release()` is `self.risk.release_check(self._fetch_balance_fresh())`.
  It is wired as `Supervisor(..., risk_release=self._risk_release)`.

## C. Exact code changes

`trader/engine/risk.py`:
- `RiskRelease` dataclass;
- `RiskManager.release_check()`.

`trader/engine/supervisor.py`:
- **Constructor.** `Supervisor(..., risk_release=None)`. With no gate it fails
  closed (`risk_gate_unconfigured`).
- **`_risk_now()`.** Calls the gate. An exception or a non-`RiskRelease`
  answer means unreadable.
- **`_pass_once`.** Immediately before the PROVED save and the activation
  CAS, when the pass is otherwise safe, owned and in RECOVERY, it:
  - calls `_risk_now()`;
  - records `checks["risk_release"]` and `actions["risk_release"]`;
  - if Risk refuses, sets `safe = False` and adds the Risk reason.
- **`request_owner_recovery`.** For HALTED + `allow_unhalt`, Risk is checked
  before the fence. A refusal leaves the state HALTED, typed as `REFUSED
  <reason>`. Every result row now carries a `risk_release` audit dict.

`trader/kernel.py`:
- `_fetch_balance_fresh()` split out;
- `_risk_release()`;
- the Supervisor wiring.

Tests:
- `tests/test_supervisor.py`: its 6 constructions pass an explicit
  `risk_release=RISK_OK`, because those tests exercise venue safety only.
- `tests/test_kernel_boot_recovery.py`: the `_kernel` fixture now wires the
  **real** `RiskManager` and the real `Kernel._risk_release`. Only
  `_fetch_balance_fresh` is replaced, by `venue.equity`, and the baseline is
  seeded by one `update_equity(1000)`, as the first kernel cycle would.
  `RecordingVenue.equity` was added.
- New: `tests/test_owner_recovery_risk_guard.py` (47 tests).

## D. Active-breach results (peak 1000, halt 20 %, equity 700 = 30 %)

| Case | Result |
|---|---|
| A: risk HALTED → Telegram `/unhalt` | `REFUSED risk_halt_active`. The state never leaves HALTED (no transitions), with 0 venue reads and 0 mutations. The reply is "still HALTED: REFUSED — risk_halt_active". The audit carries `risk_release.drawdown_pct=30.0` |
| A2: Risk clear at unhalt intake, breach again during the pass | HALTED→RECOVERY, then `CONTAINED risk_halt_active`, `checks.risk_release=false`. The next kernel Risk step moves `RECOVERY→HALTED` (`risk_engine`) |
| H2: boot pass while breached (was ACTIVE) | RECOVERY with `risk_halt_active`, never ACTIVE |

## E. Cleared-breach positive result

- **Risk-halted, then equity 900 (10 %), then `/unhalt`.**
  - Transitions are exactly `HALTED→RECOVERY` (operator), then
    `RECOVERY→ACTIVE` (supervisor).
  - `checks.risk_release=true`, `entries_safe=true`,
    `risk_release.drawdown_pct=10.0`.
  - The Risk snapshot is identical before and after (durable blob, peak, day
    baseline, day key).
- **The FROZEN path (E2)** also activates once Risk clears.

## F. Unreadable-risk result

Each case: owner FROZEN, then `/resume` (and a cadence `cycle()`). It ends
CONTAINED in RECOVERY with the exact reason, and never ACTIVE:

| Fault | Reason |
|---|---|
| venue equity read fails (`None`) | `risk_equity_unreadable` |
| `risk_state` missing | `risk_baseline_unreadable` |
| `risk_state` corrupt JSON | `risk_baseline_unreadable` |
| `release_check` raises | `risk_state_unreadable` |
| gate not configured | `risk_gate_unconfigured` |
| gate returns an untyped dict | `risk_state_unreadable` |

Additional coverage:
- **Unit.** `None`, `0`, `-5`, `NaN`, `inf` and `"x"` equity all give
  `risk_equity_unreadable`. Durable `null`, `"NaN"`, `-1`, missing and
  corrupt baselines all give `risk_baseline_unreadable` even when the
  in-memory peak is healthy. A durable peak (1200) above the in-memory peak
  is honored.
- **The real `_fetch_balance_fresh`.** With `requests.get` failing (stubbed)
  and no ccxt balance, it returns `None`, and `_risk_release()` fails closed.
  A healthy stale equity row in the journal is still used by
  `_fetch_balance()` for sizing, but **never** by Risk.
- **Boot with unreadable equity** stays RECOVERY with `risk_equity_unreadable`.

**Behavior note.** On a brand-new journal with no `risk_state`, the boot pass
now stays contained until the first kernel cycle writes the baseline. The
same cycle's cadence pass can then activate. This fails closed by design.

## G. FROZEN-bypass result

Risk HALTED → `/freeze` (HALTED→FROZEN, operator) → `/resume`:
- the state goes FROZEN→RECOVERY and is `CONTAINED risk_halt_active`;
- there is **no ACTIVE**;
- the entry probe is blocked (`state=RECOVERY`);
- the next Risk step re-halts it.

## H. Cadence result

After the FROZEN-bypass attempt, the state is supervisor-owned RECOVERY with
`needs_owner=false`, so it is eligible for cadence activation.
- Three cadence `cycle()` passes each return `risk_halt_active`, with no
  ACTIVE.
- After the equity clears to 900, the next `cycle()` activates. The owner and
  cadence paths share one rule.

## I. Previously-approved-entry result

Two variants were run: unhalt, and FROZEN + resume.
1. `RiskManager.check_entry` approves ETH while ACTIVE (`sizing.ok`).
2. The drawdown moves the state to HALTED.
3. The owner tries to recover, and Risk still blocks. The unhalt is REFUSED
   (state HALTED), and the FROZEN + resume variant is CONTAINED (state
   RECOVERY).
4. The approved work reaches `Executor.open`.

Result:
- **0 `create_order`**, with `skip_reason` `state=HALTED` or
  `state=RECOVERY: entries blocked`, and never ACTIVE.
- **Known and recorded separately** (LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1): the
  only venue mutation is `set_leverage`, applied before the final fence. The
  test pins this so a later fix is visible. The activation guard alone
  prevents the order, so leverage ordering was not changed here.

## J. Rollback impact

**No Risk requirement was added to `prepare_rollback`, deliberately.**
- READY means "own FROZEN hold, fresh venue-safety proof, no newer event".
- FROZEN is not ACTIVE: it grants no entry authority. The Supervisor cannot
  activate an owner FROZEN, and it cannot hide a Risk halt. Risk re-halts
  from FROZEN on its next cycle (FROZEN→HALTED is valid in both d46d045 and
  51101d0).
- Leaving FROZEN later needs an owner request, which passes the Risk-gated
  CAS in this kernel. In an old 51101d0 kernel, an owner resume is that
  kernel's own authority, and its cycle re-halts on a breach.
- Requiring a Risk release for READY would block a safe rollback during a
  drawdown for no safety gain.

Verified:
- with a breach active, rollback gives READY/FROZEN;
- `cycle()` returns None, and `pass_once()` keeps FROZEN;
- the Risk step re-halts it;
- the Risk baseline is unchanged across rollback.

## K. Focused test totals

| Suite | Tests |
|---|---|
| **test_owner_recovery_risk_guard.py (new)** | 47 |
| test_owner_recovery_concurrency.py | 31 |
| test_owner_recovery.py | 31 |
| test_kernel_boot_recovery.py | 12 |
| test_supervisor + control_state_recovery + entry_control_fence + entry_recovery + protective_stops + reconcile + reconcile_alignment_commits | 144 |
| **Total** | **265 passed, 0 failed** |

Also run:
- The risk-guard and concurrency files together were run 5 consecutive times
  (78/78 each).
- `test_risk_state_persistence.py` 8/8.
- `test_attention_telemetry`, `test_margin_caps` and `test_phase0` pass.

**Pre-existing, unrelated failure:**
`test_partial_close_shrinks_notional.py` gives 4 failed. It fails
identically (4 failed) on the unmodified d46d045 boot-fix worktree. The full
repository suite was not run.

## L. Negative controls

**Behavioral mutants.** Each ran through the same scenario check, and each
fails at the intended assertion:

| Mutant | Check | Fails with |
|---|---|---|
| omit the fresh Risk check (gate always allows) | active breach + `/unhalt` | `'ACTIVE' == 'HALTED'` |
| omit the fresh Risk check | FROZEN bypass | "FROZEN hid an active risk halt" |
| treat unreadable Risk as safe (equity, baseline, raise, unconfigured) | unreadable ×3 | "unreadable Risk treated as safe" |
| FROZEN bypass (Risk only at unhalt intake, not at activation) | FROZEN bypass | "FROZEN hid an active risk halt" |
| cadence activation without Risk clearance | cadence ×3 | cycle activated, no `risk_halt_active` |
| stale Risk-safe reuse (first allowed answer cached) | stale clearance | "stale Risk clearance authorized ACTIVE" |

Each check also passes on the real code (`test_checks_pass_on_real_code`).

**Source-level mutations.** Each was applied to the real code, run over the
risk-guard, owner, concurrency and boot suites (121 tests), then restored and
byte-compared:

| Mutation | Failed |
|---|---|
| M1: activation Risk gate removed | 19 |
| M2: unhalt intake pre-check removed | 4 |
| M3: unreadable equity returns allowed | 11 |
| M4: corrupt durable baseline falls back to memory | 8 |
| M5: kernel Risk uses the fallback `_fetch_balance()` | 1 |
| M6: gate exception treated as allow | 2 |

**Replay.** The captured 8-position snapshot was replayed through the real
Risk wiring. Venue equity is **synthetic**: baseline 1000, not the real
account.
- All earlier protective cases are unchanged:
  - clean case: 0 mutations;
  - missing stop: one re-arm;
  - orphan: one cancel;
  - ID mismatch: cancel plus re-arm;
  - loose stop and incomplete listing: RECOVERY with 0 mutations;
  - owner request activates or stays contained;
  - rollback READY or BLOCKED.
- **Breach at 700:** `/unhalt` REFUSED `risk_halt_active`. At 750 it is still
  REFUSED/HALTED. At 900 it is ACTIVATED. The peak stays 1000, with 0
  mutations.

## M. Kernel-only deployment status

**Not deployed.** It is reconsiderable only after all of:
- Astra reviews concurrency v1 and this package;
- explicit authorization;
- the owner confirms Telegram `/status` works.

## N. Dashboard deployment status

**BLOCKED, unchanged.** The d46d045 dashboard `set_control_state` and chat
"resume" still `set(ACTIVE)` directly, **bypassing both** the recovery proof
and the Risk release. They must become kernel-executed calls to
`request_owner_recovery(OwnerContext(...))`, which is
LUFFY-OWNER-INTERFACE-GATEWAY-V1. No transport was added here.

## O. Remaining blockers

1. **Dashboard direct-ACTIVE setters** (§N) and
   **LUFFY-OWNER-INTERFACE-GATEWAY-V1** (OpenClaw/WhatsApp/Telegram/dashboard
   convergence; not started).
2. **LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1.** `set_leverage` runs before the
   final entry fence. It was observed in the approved-entry test and did not
   reach an order.
3. **Other direct ACTIVE setters, inventoried and not changed:**
   - **MacroGuard auto-resume** (FROZEN→ACTIVE). It only runs when MacroGuard
     owns the freeze and there is no owner hold. In `cycle()` the Risk halt
     step runs first and turns a breach into HALTED, so MacroGuard's
     `_cur == FROZEN` guard then skips. It is not Risk-gated itself. Review
     should decide whether to route it through the same rule.
   - **Spot `/resume`** (`set(ACTIVE)`, non-futures only; outside the current
     futures demo scope).
4. **Fresh journal.** Boot stays contained until the first cycle writes the
   Risk baseline (§F).
5. The Risk release trusts `RiskManager`'s durable peak. A corrupt
   `risk_state` at kernel start still makes `_load_state()` "start clean"
   (pre-existing behavior). Release fails closed only until the next cycle's
   `update_equity()` re-seeds the peak at current equity. That pre-existing
   baseline-reset-on-corruption path was not changed here.
6. `graphify update .` was not run. `graphify-out/cache/last_query_stamp` was
   already dirty when this package started and was left as it was.

## Hygiene corrections made

- **Concurrency report §H.** "61 tests" was accurate for the run at the time
  (31 + 30). The report now says the pair is 62 (31 + 31), after the
  transport-neutrality test was added, and that the source mutations were
  not re-run with it.
- **Concurrency report §L4.** The "explicit unhalt of a drawdown halt" item
  is now marked as a confirmed defect, fixed here.
- **Boot-fix report.** The 8 failed / 4 passed correction from the previous
  package stands.

## P. Exact changed files (this package; worktree only, uncommitted)

- `trader/engine/risk.py`
- `trader/engine/supervisor.py`
- `trader/kernel.py`
- `tests/test_supervisor.py`
- `tests/test_kernel_boot_recovery.py` (the `_kernel` fixture wires real
  Risk; `RecordingVenue.equity`)
- `tests/test_owner_recovery_risk_guard.py` (**new**)
- `docs/superpowers/reports/2026-09-27-owner-recovery-risk-halt-guard-v1.md`
  (**new**)
- `docs/superpowers/reports/2026-09-27-owner-recovery-concurrency-hardening-v1.md`
  (§H wording, §L4 status)

Scratch space (RAM), `/dev/shm/luffy-owner/`:
- `replay_boot.py` (plus Risk cases);
- `*.bak` restore copies;
- the pytest basetemp.
