# [SOL-1][EXE-04][EXIT-PARITY-R1] CLOSED

Engineering closure for the **admitted exact-version exit path**. Canonical reconciliation has not been performed. The original BLOCKED audit and its XML remain unchanged as baseline failure evidence. Legacy unattributable records remain UNKNOWN; this closure does not retrospectively certify their geometry.

## Isolation and ancestry gate

Worktree: `/mnt/luffy-data/luffy/workspaces/sol1-exe04-exit-authority`; branch: `sol1/exe04-exit-authority`. Engineering baseline: `1ec9870a09868d108293b10c3417ffd14368ed2e`; prior audit: `0c520e23843556f3df85a642643bdfe05d8ba1de`.

The baseline contains DEC-03 position evidence, STR-04 implementation `872131f3755ab2d156e9cce73f0deae3b7630df2` and owner fix `9814fddce8f655ce73bd79050b536e6668dba55d`, STR-03 `2b119a318bdca34599eb8620c9f1f1cc62a78f7e` / `e8ad27481172f0663b9ffada4ffe3d1d365f9568`, and DEC-02 `9f36fc7384de6ecd53b1b3f85e45f8a8c6508a81` / `f1bbd7a74600ce2f8355babb337854bc6693bc35`. See the original audit for the complete ancestry record.

No Kernel, allocator, tracker, STATE, NEXT, manifest, production checkout or other worktree was edited. No Kernel run, Dashboard restart, provider/venue API, order, activation, deployment or EXE-05 verification occurred. Graph regeneration was omitted because it would modify the forbidden graph manifest.

## Historical recovery (committed objects only)

Historical worktree `/mnt/luffy-data/luffy/workspaces/admitted-exit-semantic-parity-r1` was inspected read-only. Local and remote refs name `luffy-admitted-exit-semantic-parity-r1`; HEAD is `1ab0da0cb9cb3a3c290488d7c75edf09788b6462`. Its observed dirty state was eight tracked graph files and 329 untracked files. **No dirty file was used, copied, cleaned or modified.**

The historical branch has 146 commits absent from this engineering branch; merge base is `87b9e4ba4bcbc8a9fa4f1ee8472abb7143d43a72`. No merge or cherry-pick was performed. Current architecture already incorporates the exit implementation through `1bed3958740ec2d0d575aabc817978086d31d390` (universal strategy authority), rather than through direct historical ancestry. The baseline's `exit_policy.py` and `paper_exit_evidence.py` match the historical tip byte-for-byte. Its live adapter differs only by the execution-reference addition before the changes in this package.

The following map identifies the relevant committed series. “Present” means the equivalent current implementation, not a claim of direct commit ancestry. “Safe” means the scoped idea/test can be reused under current authority; it never authorizes applying the whole old commit. “Superseded” refers to the old authority plumbing.

| COMMIT | PURPOSE | FILES (relevant scope) | TESTS / attached evidence | PRESENT | SAFE | SUPERSEDED |
|---|---|---|---|---|---|---|
| `54aefc56fe2c` | Durable trade provenance | engine trade provenance, journal/booking | provenance tests in committed tree | YES | YES, existing only | YES, current entry identity/DEC-03 |
| `bc16c9b627ff` | Factory version handoff | strategy/factory_handoff.py, promotion.py | factory handoff tests | YES | YES, existing only | YES, STR-01/03/04 |
| `a883313b9d8f` | Exact paper probation versions | factory_handoff.py, kernel.py | factory and single-creation tests | YES | YES, existing only | YES, current binding chain |
| `548ebb727e81` | Integration merge | authority/provenance integration | merged parent test series | YES | NO, wholesale merge | YES |
| `b35a4bcca29d` | Capacity contract | factory capacity admission | strategy capacity tests | YES | NO, outside EXE-04 | YES, current Portfolio boundaries |
| `6b7f0f7f434f` | Cross-stage authority gaps | executor.py, exits.py, combo.py, factory_handoff.py, kernel.py | cross-stage authority tests | YES | YES, existing ideas only | YES, current exact approvals/action authority |
| `e60e04540dde` | Capacity integration | capacity consumers/factory | capacity integration evidence | YES | NO, outside EXE-04 | YES |
| `d4de42adcef0` | Truthful versioned paper execution/probation | engine/paper.py, factory_handoff.py, kernel.py | test_versioned_paper_execution.py; test_versioned_paper_probation_truth.py | YES | YES, current paper path | YES, current admission/cost chain |
| `1ab0da0cb9cb` | Shared exit contract; research/backtest, paper/live parity; refusal; semantic validation/install/probation binding; replay | exit_policy.py, versioned_exits.py, paper_exit_evidence.py, paper.py, compile.py, vector_backtest.py, backtest.py, referee.py, runner.py, factory_handoff.py (30 files total, including forbidden control-plane files) | test_admitted_exit_semantic_parity.py; test_exit_semantics_probation_binding.py; committed report and matrix; historical report states 442 tests, not a current rerun | YES, runtime core; NO, two acceptance test files | YES, contract and narrowly adapted tests | YES, live binding superseded by DEC-03 integration here |

The recovered probation-binding test is sourced from `git show 1ab0da0cb9cb:tests/test_exit_semantics_probation_binding.py`, adapted to current fixtures and the earlier STR-01 refusal. The new cross-adapter test uses that commit's causal matrix design with actual current compilation, paper execution and DEC-03 observations. No historical runtime diff was restored wholesale.

## One unchanged deterministic contract

`factory-exit.closed-bars.v1:51758284b0b961695203b5cbc1569dfb1d5805a68506d594a40a5d5df30d3fcc`

This historical contract remains unchanged. Its pure `initialize/advance` owns geometry and ordering. StrategyVersion/spec hash, immutable validation receipt, install ID and paper probation already bind this ID. The position's immutable entry identity freezes its initial encoded policy/state. DEC-03 derives the approved plan's identity/hash from that identity, validates the exact spec/install and joins the account slot to the booked episode and independent holdings. Execution state is an observation prefix of that frozen plan, not another plan registry.

Initial reference is the signal bar close; entry ATR is rolling mean True Range(14) through that completed bar. Stops are ATR multiple or reference-price percentage, with the declared 0.4% floor. Initial R is reference-to-stop distance. Targets are R multiple, ATR multiple, percentage or NONE. Quantity intent is the full remaining position. Time cap is unconditional, an integer from 1 to 500 consecutive completed spec bars after the signal bar. Ordering is open gap, stop, target, then time cap. Range ambiguity explicitly selects stop first; no intrabar sequence is inferred.

## Actual gaps and changes

1. Live previously trusted caller geometry and did not independently consume DEC-03. `position_plan` now uses the actual booked row, the independent retained position snapshot, and DEC-03's existing capture/build verifier. It refuses wrong/missing spec/install/version/plan, wrong slot or episode, wrong side/quantity, stale/future snapshots and caller substitution. Where recorded, the original entry action/capability digest, decision and strategy binding are rechecked. No new capital permission is issued.
2. Live restart previously trusted mutable terminal state. The existing state table gains nullable causal-evidence columns without rewriting old rows. New state retains DEC-03's frozen binding, initial plan and completed observations. Every read replays against the exact spec and compares encoded state; terminal state cannot acquire later observations. Final Executor close independently verifies binding, replay, reason, full remaining quantity and cut before any exchange access.
3. `compile_version` had dropped transient exit semantics when compiling an authenticated STR-02 version. It now routes through the historical shared evaluator and retains the exact requested version/spec/install identity. Non-installed research compilation carries no install identity. Serialization/spec hashes and all approval/entry permissions remain unchanged.
4. Direct strategy stop ratchets and new partial reservations could bypass the versioned adapter. They now refuse for a booked versioned position (`trail:none`, no partial schedule). EXE-03's existing durable partial lookup/recovery precedes the admission fence, preserving an already-reserved reduction's idempotency. No new partial schedule is added.
5. Current unsupported-semantic validation already refuses before version creation/install. Exact exit-binding verification also rechecks the whitelist throughout the existing install/probation/approval/Governor consumers. There is no new lifecycle or owner-approval path.
6. Paper management and paper evidence now share the same strict initial-plan verification and causal replay helper. Existing receipt schemas and bytes remain unchanged.

## Authority outcomes

| Behavior | Current authority and outcome |
|---|---|
| Initial stop | SUPPORTED_AND_EXACT: frozen ATR/% stop, side/reference/ATR/floor/R/spec/plan verified; arbitrary confidence stop or alternate-version policy refuses |
| Final target | SUPPORTED_AND_EXACT: R/ATR/% full remaining exit; EXPLICIT_NONE has no target or hidden fallback |
| Time cap | SUPPORTED_AND_EXACT: consecutive completed spec bars; deterministic price-before-time precedence |
| Trail | EXPLICIT_NONE; never ratchets. Non-none refused at admission and direct update refused |
| Partial | EXPLICIT_NONE; no registered schedule. New ordinary versioned partial refuses; previous EXE-03 recovery remains idempotent |
| Invalidation | No executable registered position-plan contract. Prose cannot execute; signal exit/undeclared executable invalidation refused at admission. DEC-04 is untouched and supplies no authority |
| Scale-in | EXPLICIT_NONE; no registered allowance. Unsupported declarations refuse; confidence/new version cannot grant exposure. Existing Risk rejects an already exposed instrument |
| Safety | Existing authenticated OwnerService close receipt + exact manual-close event can contain the actual position even with missing strategy snapshot/state. Safety evidence is OWNER with request/principal/event, separate from STRATEGY. Owner panic preserves the existing hold. No safety plan rewrite or exposure expansion |

No new venue permission, Portfolio authority, Risk bypass, Governor permission, owner approval, replacement version, native-protection verification or scale-in capital is granted. Existing protection/recovery mechanisms retain their separate authority; this package does not claim to validate EXE-05 venue-native protection.

## Historical intended minimum comparison

| HISTORICAL_DESIGN | OLD_IMPLEMENTATION_EXISTS | CURRENT_IMPLEMENTATION / ACTION_TAKEN | PROOF |
|---|---|---|---|
| ATR/% initial stops | YES | Retained same contract; verify frozen initial policy before replay | 70-case cross-adapter matrix; substituted policy refusal |
| Target R/ATR/%/none | YES | Retained exact target; none has no target | favorable bars with none remain holding; target parity |
| Unconditional time caps | YES | Retained exact cap and precedence | long/short time and holding cases |
| Explicit none | YES | Retained target/trail none; partial/invalidation/scale-in have no executable plan | direct ratchet/partial/action refusals; admission matrix |
| No undeclared TP1/breakeven/score/default trail | YES | Versioned route skips legacy overlays, plus direct boundary fences | changed legacy defaults and opposing scores do not affect matrix |
| Final target execution intent | YES | Full remaining STRATEGY target intent verified at final boundary | paper closure/live intent vs research transition quantity/reason |
| Causal trailing or refusal | Refusal exists; no non-none causal contract | Retained admission refusal; no new trail | unsupported trail and direct update tests |
| Unsupported swing/signal/partial/flip/regime | YES, whitelist | Retained admission whitelist, reverify binding; unknown ExitSpec fields fail deserialization | unsupported validation/deserialization matrix |
| Version bound through validation/install/probation | YES | Retained current immutable chain; restored recovered tests; exact compile path repaired | probation tamper/rehashed evidence, STR-02/03/04 tests |
| Emergency protection separate | YES, independent pathways | Verify owner close receipt independently; label separately; protection unchanged | owner close with queue consumed and missing snapshot/state; panic/expansion refusal |

## Replay, lifecycle and legacy

Version A position remains bound to A when a complete installed Version B has higher confidence. B's binding and substituted geometry refuse. PAUSED/DEGRADED/RETIRED lifecycle events do not mutate A's frozen exit plan; historical install verification uses `current=False`. Future strategy evidence never reconstructs a missing historical plan.

Paper and live retain exact policy/current state across adapter reconstruction. Research, paper and live use identical causal completed observations and produce identical state, stop, target, timing, reason and full remaining quantity intent. A successful local test booking removes the open episode, so a duplicate observation does not duplicate an action. A failed submission retains the same terminal intent for retry; fill equality is outside this contract.

Legacy records and old live states lacking exact attribution/causal evidence remain UNKNOWN and refuse versioned strategy certification. No receipts are backfilled. Historical validation/probation evidence rows are byte-preserved. Existing non-versioned legacy paths remain legacy, and their caller labels cannot fabricate STRATEGY evidence. This is not a retrospective all-legacy geometry certification.

## Validation

Only `/home/sarmad/.local/bin/luffy-pytest` was used to execute tests; its wrapper manages per-run basetemp under `/mnt/luffy-data/test-tmp/sarmad`. JUnit evidence and exact commands are in `exe04-exit-parity-r1-evidence/validation.txt`. Test journals, principal files, snapshots and test-only intent booking are isolated fixtures; there are no API calls or actual execution orders.

All **408 distinct selected tests pass**: final suite 321; authenticated research/compiler suite 123 (83 shared parity cases plus 40 compiler cases); filtered factory suite 47. EXE-03 has 19 passing identity/intent/ack cases, DEC-03 has 21, and STR-03/STR-04 have 108. Full committed historical file lists and the chronological exit-related series are in `exe04-exit-parity-r1-evidence/historical-recovery.txt`.

The final admitted matrix runs the authenticated `compile_version` path, deterministic research `_trade`, actual PaperExecutor, actual ExitEngine/LiveExitAdapter and independent final-plan verification. EXE-03 partial identity/intent/ack tests, DEC-03 position evidence, STR-03 exact approvals, STR-04 lifecycle/historical binding, vector/null research and legacy stop/exit regressions are included. Kernel fixture tests are deliberately excluded from the separate factory/compiler checks.

Canonical reconciliation remains pending. EXE-05 may begin as a separate task; no EXE-05 implementation or venue protection verification is included here.
