# LUFFY-ACTIVATION-AND-RISK-BASELINE-HARDENING-V2

2026-09-27. Initial implementer: Opus. Initial independent review: Astra.
Final implementer and verification: Astra, explicitly authorized by the owner
after Opus reached its limit. No separate independent reviewer is claimed for
Astra's final edits.
Worktree: `/home/sarmad/trader-owner-recovery`, HEAD
`d46d045af38497712ae5fbcfcc9b3bdeb78feee5`, starting from the uncommitted V1 candidate.

**PACKAGE VERDICT: PASS — all six scoped defects closed and focused validation passed.**
**DEPLOYMENT VERDICT: NOT_DEPLOYED.** No commit, production edit, deployment,
restart, watchdog change, or Telegram command. Dashboard/OpenClaw/WhatsApp and
leverage-before-final-fence remain separate. Runtime/test/replay state was RAM-backed.

Opus hit its session limit after applying the last Risk fixes. The owner then
explicitly transferred implementation to Astra. Astra completed final rollback
ordering, deterministic publication and bounded CAS negative controls, permanent
last-fix regressions, full validation and this report. Package PASS concerns
this isolated candidate only; it does not approve deployment or adjacent work.

## A. Risk-proof identity mechanism

`RiskRelease` carries authoritative equity, decision, RiskManager instance UUID,
evaluation generation, SHA-256 of the exact persisted baseline, and monotonic
check time. Proof age is limited to 30 seconds. Updates, corruption, repair and
release reevaluations supersede proofs. The last patch captures the generation
of the issuing evaluation while holding the Risk lock; it cannot borrow the
generation of a later refused reevaluation.

Final activation holds the control fence, Risk lock, and a SQLite
`BEGIN IMMEDIATE` transaction. Risk is revalidated and the ACTIVE transition
and its audit use that same connection. Separate journal connections cannot
change persisted Risk between verification and CAS. This is used by Supervisor
boot/cadence/owner recovery, futures MacroGuard, spot owner release and spot
MacroGuard. A changed baseline, generation, latch or control watermark refuses.

The first V2 draft held only an in-process lock. Astra reproduced an external
writer committing corruption before ACTIVE persistence. Opus corrected that
gap. The final regression includes independent Journal and RiskManager writers.

## B. First-init marker contract

`risk_baseline_marker` is written atomically with successful first baseline
persistence. Failed equity reads or failed initial persistence do not mark
initialization. Equity telemetry alone is never initialization evidence.
Missing baseline plus marker, or an established baseline in memory, is corrupt.
Absent baseline without either is legitimately uninitialized. A valid legacy
baseline loads and its marker is backfilled on a successful update.

## C. Corruption-latch behavior

Detection by load/update/release/final validation latches memory and persists
`risk_state_corrupt_latch`. A plausible external replacement does not clear it,
including after restart. Successful explicit `repair_baseline()` is the only
normal clearing path. Unreadable storage refuses without treating a failed
read as proof of corruption. If latch persistence itself fails, the in-memory
latch still contains that running instance; durable write failure is logged.

## D. Hardened repair_baseline contract

Rejects bool, nonnumeric/nonfinite/nonpositive peak, and empty/whitespace actor
or reason. A transactional journal is required. Under the Risk lock and one
IMMEDIATE transaction it reads the highest valid historical equity, memory,
valid durable peak and retained latch peak. Unreadable history refuses.
SQLite storage types and the finite-float maximum are checked before aggregation;
malformed text/bytes/infinity cannot hide a valid historical maximum. Invalid
history-row count is audited. The day baseline is valid positive finite equity
from today or absent. The proposed repaired record is parsed before writing.

State, marker, latch removal and repair audit commit together. Audit failure
rolls back repair. Memory publication follows commit while still holding the
Risk lock. Repair does not activate control state. The deterministic
lock-release regression and its outside-lock source mutant both pass (§K).

## E. ACTIVE inventory strategy

`tests/active_setter_inventory.py` performs AST analysis with parameter-forwarding
discovery of setters/wrappers, keyword-only arguments, method/import/module/value
aliases, alias chains, annotated assignments, partial calls, CAS calls, keyed
persistence and SQL writes. Approved ACTIVE sites are exactly Supervisor
`_pass_once` and `_guarded_activate`; primitive wrappers have a separate explicit
allowlist. Dashboard/chat writers are inventoried separately as out of scope.
Fixtures exercise both injected files and additions to existing kernel code.
Dynamic computed dispatch/exec and writers outside the scanned tree remain
static-analysis limitations, not claimed coverage.

## F. Rollback corrupt-state rule and runbook

Unrepaired corruption returns BLOCKED. `prepare_rollback()` now verifies Risk
before fresh reconciliation/protection; it verifies the same baseline again
under the final transaction through the READY audit and verifies its exact
FROZEN hold watermark. Corruption or a changed baseline prevents readiness.

**Required operational sequence remains:** explicit hardened repair if latched;
verify repaired Risk; fresh protection/reconciliation; establish the exact
rollback FROZEN hold; verify its authority; only then READY_FOR_OLD_VERSION.
If any step cannot be proved, rollback is BLOCKED. This report does not authorize
execution of that sequence or a rollback. The current API calls its success
status `READY`.

Early containment remains at intake. Its exact event must still be authoritative
after fresh checks, or a newer owner/Risk intent wins. Only then, under the
control fence and verified Risk transaction, preparation creates a **new final
FROZEN hold**, verifies that exact watermark, and writes READY in the same
transaction. The returned/audited `hold_event_id` is the final hold, not the
intake containment. Both ordering and newer-intent preservation are tested.

Exact `51101d0` tests load the repaired baseline with FROZEN, entries disabled,
and exits managed. The final captured eight-position replay also reports zero
old-code venue changes. Replay precision is inferred from captured stop strings;
equity is synthetic. The new-kernel replay performs one SUI stop cancel and
replacement (two venue-stub mutations); zero applies to the old-code reconcile.
The final replay uses the existing RAM journal snapshot and captured venue data,
not fresh production reads. The earlier Opus replay had read production trades
read-only into RAM; no production writes occurred.

## G. Stale-Risk race results

Final suite passes the six activation paths with safe→corrupt, higher baseline,
reevaluation, concurrent `_risk_step`, and unchanged success cases, including
MacroGuard and spot interleavings. Separate-connection writers wait through CAS;
mutations that land before final proof validation refuse. A mutation that lands
after the serialized CAS is observed on the next Risk check.

Astra also independently reproduced the older-evaluation/newer-refusal generation
race, returned it to Opus, and re-probed the last patch: the older proof now
returns `risk_proof_superseded`.

## H. Full first-init results

Both futures and spot execute full `Kernel.cycle()` across unreadable, NaN,
infinite, zero, negative, text and bool first equity; telemetry rows can already
exist; reboot occurs before the first successful read. Later valid equity
initializes without false corruption. Marker-present lost/corrupt baselines
remain corrupt across restart and never silently reseed. Atomic marker-write
failure and legacy valid-baseline cases pass.

## I. Repair concurrency/audit results

Final tests pass higher concurrent peak, intervening durable/history peak,
unreadable history, audit-trigger failure, invalid inputs, below-floor refusal,
equal/above-floor repair, and contained-until-guarded-recovery cases.
Astra independently checked mixed valid/text/infinite history and finite equity
`1.5e308`: a lower repair is refused and repair at the true finite floor succeeds.
The old timing-based memory-publication check missed its mutant. The final
check schedules a new corruption detector at the precise Risk lock-release
boundary: correct code preserves that newer latch; outside-lock publication
overwrites it and is caught. The CAS connection check asserts before any nested
write, catching the dropped-connection mutant without a deadlock.

## J. Exact focused totals

Final Astra run on the last applied code: **600 passed, 0 failed, 19 files,
58.12 seconds**. Full repository suite was not run.

| Test file (under tests/) | Count |
|---|---:|
| test_activation_risk_baseline_v2.py | 178 |
| test_activation_risk_baseline.py | 61 |
| test_owner_recovery_risk_guard.py | 47 |
| test_owner_recovery_concurrency.py | 31 |
| test_owner_recovery.py | 31 |
| test_kernel_boot_recovery.py | 12 |
| test_risk_state_persistence.py | 8 |
| test_supervisor.py | 51 |
| test_control_state_recovery.py | 20 |
| test_entry_control_fence.py | 10 |
| test_entry_recovery.py | 25 |
| test_protective_stops.py | 23 |
| test_reconcile.py | 5 |
| test_reconcile_alignment_commits.py | 10 |
| test_attention_telemetry.py | 50 |
| test_margin_caps.py | 6 |
| test_phase0.py | 15 |
| test_entry_geometry_end_to_end.py | 6 |
| test_single_creation_path.py | 11 |
| **Total** | **600** |

RAM evidence: `/dev/shm/luffy-v2-review/astra-full.log`;
file list `/dev/shm/v2snap/focused.txt`. Runner uses
`/home/sarmad/trader/venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`,
RAM TMPDIR/basetemp, and disabled pytest cache. Earlier 532/535/589 totals describe
intermediate candidates, not the final files. Final collection is retained at
`/dev/shm/luffy-v2-review/astra-collection.txt`. Final replay output:
`/dev/shm/luffy-v2-review/replay-final.log`.

## K. Negative controls

Final suite includes behavioral controls for stale proof reuse, equity-row
first-init inference, release-check unlatching, higher concurrent peak overwrite,
unreadable history acceptance, repair without audit, bool/empty actor/empty
reason, ACTIVE multiline/wrapper/alias additions, and corrupt rollback readiness.
Additional controls cover separate-connection writes and malformed history.

The revised source-mutation run produced assertion failures for **16 of 18**
variants: M1–M14, M16 and M17 (CAS proof/refusal bypass, latch/marker bypass,
repair serialization/history/input bypass, rollback bypass, unreadable containment,
untyped history, in-process-only hold, reevaluation not superseding).

The original **M15 survived** and **M18 was interrupted** by Astra after a
nested-transaction deadlock. Neither was counted as caught in that run.
Both gaps are now closed with permanent source-mutant regressions:

- `test_negative_publication_after_unlock_is_caught`: M15 fails on
  `older repair overwrote a newer latch in memory`.
- `test_negative_cas_without_held_connection_is_caught`: M18 fails immediately
  on `CAS dropped held transaction`, before a nested write can deadlock.
- `test_negative_rollback_reuses_intake_hold_is_caught`: reusing intake containment
  fails the required `intake → checks → final_hold` order assertion.
- `test_negative_release_borrows_newer_generation_is_caught`: an older proof
  borrowing a later refused evaluation's generation fails the stale-proof check.
- `test_negative_history_truncates_valid_finite_peak_is_caught`: truncating valid
  historical equity at `1e308` fails the below-floor refusal check.

These five source mutants compile the actual modified method source **in memory**,
use the same positive scenario assertions, and restore through pytest monkeypatch;
no checkout source is rewritten by these controls. They pass in the final
600-test run, alongside the ordinary scenario and earlier behavioral/AST controls.
Thus the original 18 variants have observed catches (16 in the earlier source
run, two now repaired), with three additional caught source variants. This is
not a claim that the entire earlier 18-variant shell harness was rerun against
the final snapshot. No timeout is counted as a successful control.

Earlier source-run evidence: `/dev/shm/v2neg/mutate.py`,
`/dev/shm/v2neg/mutate.out`; the source files were restored with hashes verified.
Final reproducible controls are in `tests/test_activation_risk_baseline_v2.py`.

## L. Kernel-only deployment status

**NOT_DEPLOYED; scoped package PASS.** No operational changes. Tests do not
authorize deployment, restart, watchdog changes, rollback, or trading.

## M. Dashboard deployment status

**NOT_DEPLOYED; separate gateway work remains BLOCKED/out of scope.** No dashboard,
OpenClaw or WhatsApp implementation changes. Leverage before the final fence
remains separate.

## N. Remaining blockers and limits

**No remaining blocker within the six-defect V2 scope.** The final rollback
sequence, publication check, bounded CAS check and permanent generation/finite
history regressions are complete. Final focused validation is 600/600; this is
not a full repository validation. Astra completed implementation at the owner's
explicit direction; final edits were verified by their implementer, not a new
independent reviewer.

Deployment remains separately unauthorized. Dashboard/chat direct activation,
OpenClaw/WhatsApp gateway work and leverage-before-final-fence remain separate.
The internal repair primitive has no new owner transport. Static analysis does
not prove arbitrary dynamic code safe. Losing all Risk records including its
marker/latch still cannot be distinguished from a new journal. A durable latch
write failure retains only the running instance's latch until storage recovers.
These limits are not deployment approvals or newly implemented gateway features.

## O. Exact V2 changed files

Compared with the pre-V2 uncommitted snapshot, not merely HEAD:

- `trader/engine/risk.py`
- `trader/engine/state.py`
- `trader/engine/supervisor.py`
- `trader/kernel.py`
- `tests/active_setter_inventory.py` (new)
- `tests/test_activation_risk_baseline_v2.py` (new)
- `tests/test_activation_risk_baseline.py`
- `tests/test_owner_recovery_risk_guard.py`
- `tests/test_owner_recovery_concurrency.py`
- `tests/test_supervisor.py`
- `docs/superpowers/reports/2026-09-27-activation-risk-baseline-hardening-v1.md`
- `docs/superpowers/reports/2026-09-27-owner-recovery-recheck-v1.md`
- `docs/superpowers/reports/2026-09-27-activation-risk-baseline-hardening-v2.md` (this report)

The V1 report now explicitly corrects its first-init evidence, latch, repair,
inventory, stale-release and corrupt rollback claims. Its historical body is
not final V2 evidence. Preexisting staged/unstaged changes remain uncommitted.
`graphify-out/cache/last_query_stamp` matches the saved pre-V2 bytes exactly;
it remains preexisting dirty work, and no historical value was reconstructed.
Graphify query ran against a RAM copy; **no graphify update and no Graphify
cleanliness claim**. Snapshot: `/dev/shm/v2snap/dirty.tar` and `hashes.txt`.
