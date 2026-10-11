# Review 03 remediation — Execution, Prospective Evidence, Accounting Observer

Base: review commit 8439917. Scope: Execution-owned files only. Offline fakes only; no venue calls,
restarts, migrations, threshold or authority changes. **No claim of operational readiness.**

## Verdicts

| ID | Defect | Verdict |
|---|---|---|
| C-1 (Execution half) | `flatten_all` cancelled the stop before the close, sized from the journal, assumed request success | **FIXED_AND_TESTED** |
| C-1 (Kernel half) | `_drain_panic` compare-and-clears `panic_requested` after one call regardless of result | **BLOCKED_WITH_DEPENDENCY** (Sol1: `trader/kernel.py:2150-2154`) |
| P-1 | Native-stop fallback could stack a second stop after an ambiguous first placement | **FIXED_AND_TESTED** |
| P-2 | Definitive reduce-only rejection left a TP1 intent that blocked all entries | **FIXED_AND_TESTED** |
| P-3 | Stop-move journal failure after venue swap left `sl_order_id` naming a cancelled stop | **FIXED_AND_TESTED** |
| P-4 | Non-verify vs verify protection matching | **NO_FIX_REQUIRED_WITH_EVIDENCE** for the re-arm decision (identical in both modes); changing it to place stops against inadequate stops is **DEFERRED_WITH_OWNER_REASON** (protection semantics) |
| Stage-5 boot exception | `start()` raised into `Kernel.boot` on invalid/future/over-long window file | **FIXED_AND_TESTED** (inside `start()`; call site untouched) |
| Stage-5 expired-window DDL/threads | `start()` still runs idempotent DDL and the paper-funding thread | **NO_FIX_REQUIRED_WITH_EVIDENCE** + dependency note (below) |
| Accounting permanent retry | non-transient reasons retried forever, spending the global attempt cap | **FIXED_AND_TESTED** (bounded budget; evidence retained) |
| Accounting broad exception handling | `except ValueError` stored `str(exc)` incl. JSON decode errors | **FIXED_AND_TESTED** |
| Broken evidence references | `depth_evidence` cited non-existent `scripts/capacity_depth_shadow.py`; accounting docstring stale | **FIXED_AND_TESTED** (docstrings only) |
| Status files go stale after a block (`status.json` vs `public.status.json`) | **DEFERRED_WITH_OWNER_REASON** — which file is authoritative is an owner/dashboard decision |
| Dead helpers / `replay_observations` / `execution-evidence.db` second fill store | **DEFERRED_WITH_OWNER_REASON** — removal/wiring is a design choice; no runtime impact |

## C-1 — what changed (`trader/engine/reconcile.py`)
`flatten_all` now:
1. reads the venue position; sizes the reduce-only close from **venue** contracts (journal amount only if the venue is unreadable — reduce-only bounds it);
2. sends the close and **re-reads the venue**; only a venue-confirmed flat position is booked and its stop released (stop cancel happens *after* booking);
3. on rejection/timeout/ambiguity re-reads the venue and retries from the **remaining** size (max 3 attempts); a lost-ack that actually filled is detected as flat and not resent; an unreadable venue after a send is never blind-resent;
4. leaves the stop standing and journal row open for any unconfirmed/partial/refused symbol;
5. reports venue positions with no journal row (`unjournaled`) but does **not** close them (no authority extension);
6. returns `FlattenResult(int)` — still equals the closed count, with `.complete`, `.remaining`, `.unjournaled`; records a `panic_flatten_incomplete` control event and notifies when not complete.

Existing test `test_flatten_cancels_sl_then_closes` asserted the old (unsafe) order; it is now
`test_flatten_closes_then_cancels_sl`. The strict xfail for the Execution half was replaced with 7 passing tests.

### Cross-lane contract (Sol1 — Kernel/Safety)
Required change in `Kernel._drain_panic`: when `getattr(n, "complete", True)` is False, **do not** compare-and-clear
`panic_requested`; keep it pending (state remains contained/FROZEN) and surface `n.remaining`/`n.unjournaled`.
Compatibility: the result is an `int` subclass, existing stubs returning plain ints keep working
(`getattr(..., True)`). Pinned by strict xfail `tests/test_review03_flatten_panic_contract.py::test_incomplete_flatten_keeps_panic_pending`
— flip it to a normal test when Sol1 lands the change. **C-1 is therefore NOT fully resolved.**
Interim exposure: after one call the kernel clears panic and applies FROZEN; remaining positions keep their venue stops
(which is the improvement), the journal rows stay open, and reconcile/Supervisor see them. They are no longer naked,
but the panic is not retried automatically until Sol1's change lands.

## Other changes
- P-1 `executor._place_native_stop`: before the STOP_MARKET fallback, adopts a venue stop that already `protection_match`es; unreadable listing keeps the fallback (duplicate is survivable, naked is not).
- P-2 `partial_intent.reject` + `executor.close_partial`: `ccxt.InvalidOrder`/`InsufficientFunds` (same classification as entry) deletes the never-acknowledged intent and logs a `partial_exit_rejected` control event; all other errors stay pending. (A new state value was rejected because `kernel.py:1835` reads `state!='CONSUMED'`.)
- P-3 `exits._move_stop`: journal the new stop id **before** cancelling the old one; on journal failure withdraw the new stop and keep the old one (journal and memory stay consistent). `tests/test_stop_move_safety.py` fake journal gained `_tx`.
- Stage-5 `start()`: window-file errors log + write `EXTERNAL_BLOCKED` status and return.
- Accounting: `MAX_CONFLICT_ATTEMPTS=3`; a reason set containing no `*_retry`/`capture_failed`/`attempt_unavailable_retry` entry after ≥3 attempts → job `conflict` (not due, evidence kept, visible in health counts). Delivery stores only a lowercase reason code or the exception type, never message text.

## Not changed, with evidence
- **P-4:** `covered` (reconcile.py ~L350) is shared by both modes; `test_p4_rearm_criterion_is_identical_in_verify_and_non_verify` pins that both place nothing against an undersized stop and verify reports `position_unprotected`. Placing a second stop would be a protection-authority change.
- **Paper-funding thread after window expiry:** `Kernel._paper_funding_recorder` (kernel.py:1645) is window-independent, only open paper positions trigger requests, and it is started *only* from `stage5_activation.start`. Gating it would stop paper-funding evidence; its coupling to a Stage-5 file is a **Kernel-lane dependency** (start it from boot independent of the file).
- `reconcile._now` (unused) and `risk._position_risk(mark)` unused arg left; no behavioural value in touching safety files.

## Test evidence
Before → after (offline fakes):
- `tests/test_reconcile.py::test_flatten_failure_keeps_the_protective_stop` strict xfail → replaced by `test_flatten_refused_close_keeps_stop_and_reports_remaining` etc. (7 flatten tests pass).
- `test_review03_stop_fallback::test_reached_stop_is_adopted_not_duplicated`: FAILED on HEAD executor, PASSES now.
- `test_stop_move_safety::test_journal_failure_keeps_the_old_stop_and_withdraws_the_new_one` and `::test_journal_is_written_before_the_old_stop_is_cancelled`: FAILED on HEAD exits.py, PASS now.
- `test_review03_partial_rejection` (3), `test_review03_stage5_start` (5), `test_review03_accounting_classification` (7): new.
- Full owned set (77 files incl. new ones): **1544 passed, 1 xfailed, 0 failed** (`TMPDIR`/basetemp on /mnt/luffy-recovery).
Pre-existing, out of ownership, unchanged: `test_owner_interface_r3/r5` fail to *collect* on this branch (`trader/dashboard/web/index.html` missing — frontend redesign branch), `test_attention_telemetry`, `test_stage8_owner_os`, `test_strategy_factory_handoff`.
The `_drain_panic` interaction with the new result type could therefore not be exercised through r3/r5; only through the contract test above (which builds on `test_owner_interface`).

## Priority 3 — simplification candidates (nothing refactored)
1. One `core` canonical-JSON/digest/number helper replacing ~9 copies (needs Shared owner; byte-identical output must be proven per module first).
2. Fold `trade_accounting`/`conversion_evidence`/`native_exit_provenance`/`emergency_whole_accounting` skeleton (`capture→assess→replay`) into one parameterised scaffold; keep the six CLIs as thin wrappers.
3. Remove dead depth/calibration helpers after confirming no scripts import them; merge `execution_shadow` into `depth_evidence`.
4. Decide authoritative Stage-5 status file.
None touches a runtime safety path.

## Cross-lane dependencies / integration risks
- **Sol1 (Kernel):** `_drain_panic` honouring `FlattenResult.complete`; start paper-funding recorder independent of the Stage-5 file; `kernel.py:1186` message when blocked by unreadable persisted state.
- Behavioural note for operators: panic now issues up to 3 reduce-only sends per symbol and ≥3 venue position reads per symbol within one call (time cost under venue slowness; no sleeps added).
- Risk: if `fetch_positions` is slow/rate-limited during panic, closes complete but are reported incomplete (stop left standing) — fail-safe direction.
- `FlattenResult` is an `int` subclass; any code that `json.dumps` or `is`-compares the result still behaves as an int.
