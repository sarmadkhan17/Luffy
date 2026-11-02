# Guard review: two failing safety guard tests (2026-11-02)

**Status (updated 2026-11-02, after the owner decision below): the two stale guard tests were updated as test maintenance; the findings that follow are unchanged.**

## Owner decision, 2026-11-02

> **Owner-approved 2026-11-02 via the owner_demo path; not Factory-validated by owner choice.**

This applies to the three active strategies: EMA Stack Pullback, Donchian Breakout Trail and Funding Filtered Trend Pullback. The owner directed that **no new gate, check or restriction apply to them**, and that they are **not routed through the referee or Factory**. The same sentence is recorded in `CLAUDE.md` (strategy section).

What was done under that decision, and what was not:
* **Done (test maintenance only):** `tests/test_risk01_entry_binding.py` now lists `trader/engine/coordinated_venue.py` as the one other `executor.open` caller, with the same `risk_permission=` requirement as the kernel's calls; `tests/test_str04_governor_lifecycle.py` now lists `strategy/owner_demo.py` among the known `UPDATE strategies SET state` writers. Both tests pass.
* **Done (general order path only):** `tests/test_entry_fence_every_path.py` checks that a blocked persisted control state (`FROZEN`, `HALTED`, `RECOVERY`) refuses `open` and `increase` and sends nothing, with controls showing the same entries are accepted while `ACTIVE`. It says nothing about any particular strategy.
* **Not done, deliberately:** the proposal to test that `owner_demo.authorize` refuses a versioned id (it concerns the owner-demo path itself, not the general order path); tracking `executor.increase` callers in the guard; and the optional `entry_block` call at the top of `Executor.increase`. They remain available if wanted.
* **Nothing in the code under review changed.** No restriction was added to the owner-demo path or to any strategy.

Evidence tags: **VERIFIED** = read in code or measured (file:line given); **INFERRED** = reasoned from verified facts.
Scope: read-only investigation on branch `work/unblock-20261102` (base `14484f65`). Both tests fail identically on `a479b995`, `14484f65` and this branch.

**Verdicts**

| guard | verdict | restart blocker? |
|---|---|---|
| `test_risk01_entry_binding::test_entry_callers_are_closed_set` | **(a) safe caller the closed set does not list yet.** Not a bypass. Two coverage gaps found. | Not a code blocker. Fix the guard and add the behaviour test below before a restart. |
| `test_str04_governor_lifecycle::test_every_nonfactory_state_writer_is_legacy_only_and_guarded` | **(a) a legitimate, narrowly scoped legacy-row writer the static map does not list.** It does not bypass the Governor. It was the path that activated the three strategies. | Not a code blocker. One missing test. |

Both tests were last edited before the files they now flag existed (details in each section), so the failures are stale guards, not regressions in the guarded behaviour.

---

## 1. `test_entry_callers_are_closed_set`: `trader/engine/coordinated_venue.py`

### What the guard asserts (`tests/test_risk01_entry_binding.py:203-218`) — VERIFIED
1. Only `trader/engine/executor.py` imports `submit` from `entry_authority`. (This assertion **passes**.)
2. The only file calling `executor.open`, `_open_serialized` or `_open_locked` is `trader/kernel.py`. (This assertion **fails**: `coordinated_venue.py` also calls `executor.open`, line 240.)
3. Every such kernel call passes a `risk_permission=` keyword.

### History — VERIFIED (`git log`)
`coordinated_venue.py` was created in `b39f2fa6` (2026-10-30 05:45, "Dispatch coordinated intents through existing Risk and recovery") and extended in `a479b995` (2026-11-01). The guard test was last edited in `d0de3d59` (2026-10-06). The guard was never updated for the coordinated layer.

### Every path from `coordinated_venue` to an order submission — VERIFIED

```
kernel runtime  scheduling/kernel_runtime.py:219-220
   only if kernel._manages_exits() and the executor has a journal
 └─ coordinated_live.run_once                      engine/coordinated_live.py:297
      resumes ALREADY-REGISTERED OPEN books only ("does not discover, approve, allocate or start a new book"), max 2
    └─ coordinated_venue.dispatch                  engine/coordinated_venue.py:96
         holds executor._entry_lock, then:
         1. pending-instruction identity checks (oid, leg, side, qty, kind all must match the model's log)   :103-111
         2. book/version/management identity checks                                                           :122-133
         3. receipt replay: a prior terminal action is returned, never re-sent                               :134-137
         4. reductions (kind REDUCE/UNWIND or opposite-sign) -> C.close_once / U.request_close  (EXITS; see note)  :152-169
         5. entries/increases only inside CA.execution_scope(...)                                              :174
              = verify_validation + verify_install + factory_handoff.live_entry_block, else refuse
              coordinated_authority.py:48-67
         6. hedge-account-mode check, reference age <= 30 s, instruction age bound, quote identity,
            capital budget (coordinated_budget.prepare)                                                         :175-225
         7. executor.risk_manager.authorize_entry(...)  = the SAME Risk authority the kernel uses              :230
              entry_authority.authorize: control_fence + persisted control state ('control_entry_blocked'),
              unwind blocks, coordinated validate_identity                                                       entry_authority.py:326-385
         8a. new leg:  executor.open(..., risk_permission=permission)                                          :240
              refuses: adjustment permission, entry_block(persisted_state), recovery pending, exec_mode != live,
              factory_handoff.live_entry_block, owner_demo.venue_block                                          executor.py:142-168
         8b. existing leg: executor.increase(..., permission)                                                  :238
              requires an EntryPermission whose adjustment binding equals this exact trade/identity/side,
              venue_block, quantization may only shrink                                                         executor.py:178-209
 └─ both end in entry_authority.submit                                                                        entry_authority.py:529
      inside control_fence: re-reads the PERSISTED control state and refuses on any blocked state (:554-555),
      re-checks factory_handoff.live_entry_block (:562, :606), re-validates the permission against the exact
      decision/amount/stop/target/identity (validate, :437), and unwind entry blocks (:456)
```

Result: every route to a venue entry order passes through the same three controls as the kernel's own path: the Risk permission (`authorize_entry`), the version/authority fence (`live_entry_block`), and the control-state fence (`control_fence` + `entry_block`), and the last two are re-checked at the final submit boundary. Nothing in `coordinated_venue.py` calls `submit`, constructs an `EntryPermission`, or places an order directly. **It is not a bypass.**

Note on exits: reductions go through `coordinated_close.close_once` and `coordinated_unwind.request_close`. They are exits and are deliberately allowed while new-entry authority is withdrawn (the module says so at lines 98-101), consistent with `FROZEN` managing exits only.

### Residual observations (not bypasses; each is a gap in the guard or its tests)
* **R1 — `executor.increase` is not tracked.** The guard's tracked method names are `open`, `_open_serialized`, `_open_locked`. `increase` also submits entry orders. Its only caller today is `coordinated_venue.py:238` (VERIFIED, `grep`), but a new caller would trip no guard.
* **R2 — `increase` relies on `submit` for the control-state check.** Unlike `open`, `increase` does not call `entry_block(persisted_state(...))` itself (`executor.py:178-209`); protection comes from `submit`'s fence (`entry_authority.py:554-555`). Safe today but unpinned.
* **R3 — no test shows a blocked control state refusing a coordinated entry.** `grep` finds no `FROZEN`/`RECOVERY`/`HALTED` case in `tests/test_coordinated_*.py`; only the kernel path has that coverage (`test_control_state_recovery.py`).
* **R4 — `run_once` runs while the state is `FROZEN`** (`_manages_exits` is true then), so the inner fences are the only thing between a frozen kernel and a hedge enter or rebalance. That is the reason R3 matters.

### Proposed fix (for approval; not applied)
1. In `tests/test_risk01_entry_binding.py::test_entry_callers_are_closed_set`:
   * list the coordinated layer as the only other `open` caller: `assert set(open_callers) == {'trader/kernel.py', 'trader/engine/coordinated_venue.py'}`;
   * apply the existing `risk_permission=` keyword check to every call in **both** files;
   * add `'increase'` to the tracked method names and assert its callers are exactly `{'trader/engine/coordinated_venue.py'}`;
   * assert (AST) that each entry call in `coordinated_venue.py` is lexically inside `with CA.execution_scope(...)`.
2. New behaviour test `tests/test_coordinated_entry_fence.py` (new file, no existing test touched): for each persisted state in `FROZEN`, `RECOVERY`, `HALTED`, a coordinated `ENTER` and a coordinated increase through `dispatch` return a refusal, with no order submitted and no capital receipt consumed. Built on the existing `tests/test_coordinated_venue.py` fixtures.
3. Optional hardening, only if you want it: call `entry_block(persisted_state(self.journal))` at the top of `Executor.increase`, matching `open`, so R2 does not depend on a distant fence.

---

## 2. `test_every_nonfactory_state_writer_is_legacy_only_and_guarded`: `trader/strategy/owner_demo.py`

### What the guard asserts (`tests/test_str04_governor_lifecycle.py:64-73`) — VERIFIED
The set of files containing the text `UPDATE strategies SET state` must be exactly `core/journal.py`, `kernel.py` and `strategy/factory_handoff.py`. The extra file is `strategy/owner_demo.py` (line 115).

### History — VERIFIED
`owner_demo.py` arrived in `e8e6f8dc` (2026-10-25 20:38). The guard was last edited in `ef53aa3a` (2026-10-19).

### What `owner_demo.authorize` writes, and when — VERIFIED (`owner_demo.py:84-116`)
An owner-only maintenance operation. It proceeds only if **all** hold:
* the actor is in `OWNER_ACTORS` and the request time is within 5 s of now;
* `BINANCE_DEMO` is true and `config.mode` is futures/live;
* the control state is `FROZEN` **and** the operator hold (`macro_guard_operator_hold`) is `1`;
* the strategy is **not** a Factory-versioned strategy (`owner_demo_cannot_replace_factory_version`, line 94-95);
* the strategy row exists in state `paper`, `active` or `demoted`.

Then, in one transaction, it writes (1) an immutable grant row in `strategy_owner_demo_versions` (update and delete triggers forbid change) with `scope: BINANCE_DEMO_ONLY` and `validation: NOT_ASSERTED_OWNER_AUTHORIZED`, bound to the strategy's exact definition hash, and (2) `UPDATE strategies SET state='active', state_changed_at=...` (line 115).

### Was this the path that activated the three active strategies? — **Yes** (VERIFIED, journal snapshot on the system disk)
| strategy | grant timestamp | registry `state_changed_at` |
|---|---|---|
| Donchian Breakout Trail | 2026-10-25 17:40:53 UTC | 2026-10-25T17:40:53Z |
| Funding Filtered Trend Pullback | 2026-10-25 17:40:53 UTC | 2026-10-25T17:40:53Z |
| EMA Stack Pullback | 2026-10-26 00:09:01 UTC | 2026-10-26T00:09:01Z |

The three grants match the three registry changes to the second. `strategy_versions` has 0 rows and the Governor event table has 0 rows, so no Factory or Governor path was involved. The control state moved `ACTIVE → FROZEN` at 17:39:23 UTC, ninety seconds before the first two grants, as the `FROZEN` precondition requires. The control state at 00:09:01 UTC on 26 Oct was not separately checked (INFERRED from the precondition).

### Does it bypass the Governor's lifecycle rules? — **No** (VERIFIED)
* For a Factory-versioned strategy it is refused twice: by the Python check at `owner_demo.py:94-95`, and by the database trigger `version_strategy_state_authority` (`factory_handoff.py:220-223`), which aborts any state change on an id that has a `strategy_versions` row unless the Governor is the writer. The trigger path is already tested (`test_str04_governor_lifecycle.py:89-92`).
* For a legacy (unversioned) strategy there is no Governor lifecycle to bypass. This is a separate, explicit owner authority.
* It does **not** make the strategy tradable by itself. `live_entry_block` for an unversioned id returns "allowed" only with a grandfather receipt or a valid owner-demo grant (`factory_handoff.py:2158-2165`), and `owner_demo.block` additionally requires the exact definition hash, `BINANCE_DEMO`, the live/futures config and state `active`. At order time `executor.open` also runs `venue_block`, which requires the actual client to be the Demo venue, so a production client is refused (`OWNER_DEMO_ACTUAL_VENUE_REQUIRED`).

### Residual observations
* **O1 — this is a path to trading without validation, probation or approval.** By design: owner-only, demo-only, and it records `NOT_ASSERTED_OWNER_AUTHORIZED` (the module docstring says it is "not a Backtest PASS, Factory promotion or real-money permission"). The label "Registry: Active" in the dashboard is therefore not evidence of validation; the Strategies view note already says so.
* **O2 — no test proves `authorize` refuses a Factory-versioned id.** Existing tests cover environment, production venue, changed spec, non-owner and uncontained creation, actual venue and frozen startup (`test_owner_demo_activation.py`, `test_owner_demo_legacy_ema.py`). The refusal at `owner_demo.py:94-95` has no direct test (VERIFIED by grep for `cannot_replace_factory_version`).

### Proposed fix (for approval; not applied)
1. In `tests/test_str04_governor_lifecycle.py`, add `"strategy/owner_demo.py"` to the expected list, with a comment: "owner-authorized Binance Demo grant for a legacy, unversioned strategy; refused for versioned ids".
2. Add a test (new file or appended to `test_owner_demo_activation.py`) that `authorize` on a strategy with a Factory version raises `owner_demo_cannot_replace_factory_version`, and that the same `UPDATE` issued directly in SQL is aborted by the trigger (so the protection does not rest on the Python check alone).

---

## 3. Restart assessment

* Neither failure shows a bypass of entry authority, Risk, the control fence or the Governor. Both are guards that predate the files they now flag.
* Before any restart I recommend, in order: approve the two guard edits, add the FROZEN/RECOVERY/HALTED coordinated-entry test (R3) and the versioned-id refusal test (O2), then re-run the two guard tests and the coordinated and owner-demo suites. The restart gate should be those tests passing, not the allowlist edit alone.
* Open question for the owner: is a coordinated hedge entry or rebalance **meant** to be attempted while `FROZEN`? Today it is refused only by the inner fences (R4). If the intent is "no new entries at all while frozen", gating `coordinated_live.run_once`'s entry branch on the persisted state would make that explicit and visible.
