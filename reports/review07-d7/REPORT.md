# Review 07 D7 — independent implementation and D6 handoff

This is an **offline D7 implementation**, not a deployment or a qualification of Donchian. D6's shared backtester interface patch is supplied but deliberately **not applied**. The checkout therefore does not yet establish end-to-end canonical trailing backtests or executable signal-exit fills; integrate the dependent patch together with D6 before adopting these changes.

IMPLEMENTATION_COMMITS:
- `daf14dc` — shared frozen-exit preflight and initial synthetic parity probes.
- `90f3338` — declared entry direction/dependencies, nullable sequence conditions, exact version binding, next-bar ATR policy and adapters.
- `e1f6cde` — approved signal-decision ordering and explicit D6 execution barrier.
- `764cebc` — optional blank signal parity with the compiler.
- Base is `4cda84d` (D5 evidence), containing `dcb5c1a`, `94b9000` and running release `ef9fca8`. D6's inspected starting head was `65f00d9`. No branch reset, modification of Opus's worktree, or change to the running release occurred. Exact ancestry and source hashes are in `integration-safety.json` and `verification.json`.

ENTRY_MECHANISMS_VERIFIED:
- StrategySpec stores literal long/short DSL expressions, filters, timeframe and exit geometry. `compile_spec` parses the expressions, derives actual dependencies and gives each compile an identity. `entries_detail` is the common vector/live evaluator; opposite simultaneous signals produce no trade.
- Breakouts use the declared Donchian lookback (current bar excluded); reversals/crossings use exact `prev` comparisons. AND/OR/NOT, multiple indicators and ANDed filters retain their declared parameters. `bars_since(condition) <= n` expresses a bar-based validity window, including expiry; `streak` expresses consecutive conditions.
- Research `Combination.to_spec` binds compatible exits, `research.evaluate` compiles the spec, and canonical simulation/trade tables share `_trade`. Factory freezes the exact evaluated spec and current exit semantics. Paper recompiles the exact installed version and re-evaluates its exact occurrence before booking. Execution intent remains subject to existing authority.
- StrategySpec has no pending entry order, limit-entry, stop-entry or explicit wall-clock signal-TTL schema. Execution submits market orders through its existing authority contract. A breakout expression is a condition on a completed bar, not an invented stop-entry order. No alternate entry mechanism or order semantics were added.

ENTRY_PARITY_FIXES:
- A populated opposite expression could previously fire or suppress the permitted side even in a one-direction spec. Both expressions are syntax-checked, but only the permitted side contributes evaluation trees and data requirements. Serialized declared expressions are preserved.
- Nullable boolean comparisons during warmup made `bars_since` and `streak` raise when filling with numeric 0. Their boolean masks now retain unknown output and use a valid false mask internally; neither warmup nor a missing condition fabricates a signal.
- Compiler identity advances to v3 (v2 was the intermediate D7 entry fix); feature identity advances to v2. An old compile is not silently represented as the current implementation.

FIXED_EXIT_SUPPORT:
- Existing ATR/pct initial stops; RR/ATR/pct/no target; existing unconditional `max_bars`. Existing 0.4% reference-price stop floor remains part of the shared contract, including initial-R sizing. Declared geometry is not normalized to another strategy.
- Open gaps precede range checks; an ambiguous stop/target range collision chooses the prior stop and records ambiguity. Price exits precede the existing time limit. There is no entry-bar exit or same-exit-bar re-entry.
- Legacy research swing stops remain explicitly unsupported by the shared Factory/versioned contract. Declared signal exits now have shared decision semantics, but executable fills remain dependent on D6; neither mechanism is silently replaced.

TRAILING_EXIT_SUPPORT:
- Owner explicitly approved **NEXT-BAR TRAIL**. The v2 exit contract evaluates a completed bar against its previously active stop/target. Only a surviving bar updates favorable best price, arms at declared `arm_at_r × initial_r`, and ratchets using declared multiple × ATR(14). That stop applies to the following bar only.
- Initial stop remains frozen in Policy; effective protection is persisted in State.active_stop. Arming is sticky. Protection can only tighten. Long/short behavior is symmetric; no absolute adverse excursion can arm a trail.
- Both `mult` and `arm_at_r` must be explicitly valid; missing activation is refused, not defaulted. Zero arming is supported only when explicitly declared.
- ATR uses the declared timeframe and an intact 15-bar input window supplying 14 true ranges. Missing/invalid/partial inputs cannot update a surviving trail. An already due valid price/time exit does not depend on unavailable trail ATR, so initial protection stays effective.

COMBINED_EXIT_SUPPORT:
- Initial stop alone with time limit; stop + target; stop + trail; stop + target + trail. Optional declared signal-exit decisions compose with each of these. No target is removed when a trail arms, and no target/trail/partial/breakeven overlay is automatically added.
- Tests exercise arming, next-bar activation, non-loosening under expanded ATR, target retention, collisions, gaps, duplicate observations and persisted restart/retry state.

STRATEGY_VERSION_BINDING:
- `compile_version` now verifies the version's exit binding and retains its canonical exit-policy marker. Previously it authenticated the version but could send its backtest down the legacy exit route.
- Paper now uses `compile_version`, and rejects explicitly conflicting incoming version/install/compile identities as well as the existing spec-hash and signal-bar mismatches. Accepted entries retain the exact installed immutable identity.
- Factory's frozen preflight now delegates to the same validator as policy initialization; its weaker duplicate stop/target-only validator and unused constants were removed.
- Exit contract identity advances from closed-bars v1 to v2. Historical records are not edited, migrated, resealed, or accepted as v2 evidence. Old-semantic receipts require explicit re-evaluation under the integrated implementation; they are never silently relabeled.

RESEARCH_BACKTEST_PAPER_PARITY:
- Fixed policy, simulation, trade tables and versioned paper agree on tested synthetic intent/state. Modeled fees, slippage and signed funding are included in simulation/table equivalence. Paper cost labels and its recorded cost model remain intact.
- Next-bar trails are independently implemented in exit_policy and the shared versioned adapters. Paper persists state and records ATR observations for exact replay. Exit observations refuse nonfinal/invalid/stale metadata, receipts later than the existing snapshot cut, and retrospective observations with no execution authority.
- `PENDING-D6-vector-ATR-observations.patch` adds ATR, completion and signal-availability evidence to canonical `_trade` observations, computes intact ATR arrays once per simulation/table, and plumbs compiled signal validity through vector/walk-forward consumers. A deliberate signal-fill barrier refuses to score an unintegrated same-close signal fill. `verify_shared_vector.py` validates that exact diff and runs it **in memory** against handoff and existing vector/null/cost tests. It never writes the shared source file.
- Owner-approved signal ordering is protective price checks → exact completed-bar signal condition → max_bars → next-bar trail update. The compiler exposes an availability mask so unknown signal evidence cannot mean hold. A due signal stores a terminal decision with **reference_exit unset**; it is never a simulated fill. Paper persists the decision and leaves the position/protective levels open; LiveExitAdapter persists intent and refuses submission with `signal_exit_execution_timing_requires_D6`. This is deliberately incomplete executable signal support until the D6 handoff exists.
- These candidate results are not an applied shared-source implementation, not registered held-out results and not profitability evidence. Full merged research/backtest/paper parity is conditional on D6 reconciliation and its legitimate decision/fill timing.

LIVE_ADAPTER_LIMITATIONS:
- LiveExitAdapter is tested only with a no-venue submission probe. It records the same terminal strategy intent and retries the caller's remaining quantity after restart/failed submission. Its market fill hint is the actual decision snapshot, not a claimed fill at a historical stop/target.
- Native protective stop and existing Risk/recovery authority remain independent. This change does not move venue stops, grant live authority, activate strategies or prove executable venue economics. **No claim of live execution support or equivalent venue fills.**

DONCHIAN_COMPATIBILITY:
- The original declared shape is preserved: 4h, both directions, Donchian 100, stop 2 ATR, target none, trail 4 ATR armed at 1R, max500. It compiles and is structurally supported by the new shared policy. Tests also exercise Factory/versioned-paper installation with unchanged declared trail parameters on explicit synthetic qualification fixtures.
- Those fixtures test contracts; their recorded qualification numbers are test-only. No genuine Donchian qualification receipt was created and no old strategy requirements were changed to obtain a pass.

TEST_RESULTS:
- Applied-source suite: **410 passed, 2 expected skips**. Final signal tests: **8 passed** (including the additional blank-signal regression). Exact shared-file candidate: **26 passed**. DSL/sequence checks: **59 passed**; corrected Factory/lifecycle negatives: **7 passed**. Suites overlap; counts are not added together. See raw logs and `verification.json`. Expected skips are the two combinations asking for a target outcome when no target was declared. They are not missing cases for declared mechanisms.
- Signal tests prove priority over time/trail, priority of existing protection, unavailable-evidence refusal, exact multi-condition compilation, restart-persistent pending decisions and absence of any same-close fill/submission.
- Existing lifecycle negatives were updated to reject attaching a valid trailing strategy to another strategy's qualification by exact spec mismatch. Missing trail activation remains an unsupported negative case.
- Existing compiler identity tests now change from the current version rather than hardcoding v2 as an assumed future version.
- Existing backtest equivalence script: PASS on explicit synthetic data with nonzero signals for four families; one context-dependent family has zero signals, as recorded. No historical data stores were opened.

PERFORMANCE:
- Existing benchmark script on deterministic synthetic 1200-bar inputs: **117.0×**, above its 20× gate. This is the legacy throughput regression check, not a benchmark or economic claim for the new trail path.
- `performance.json` records preflight and pure-policy transition microbenchmarks. Candidate shared ATR masking is computed once per simulation/table, not repeatedly per trade. No statistical threshold or CI change.

FILES_REMOVED_OR_MERGED:
- Removed SpecExit._unsupported and its two unused constants after checking consumers; frozen preflight now calls exit_policy.unsupported. No production module removed. The temporary entry-patch verification helper was removed after its changes were applied and covered by ordinary tests.
- Entry signal evaluation remains in the compiler; trailing transitions remain in exit_policy. No new strategy engine/framework/approval machinery.

NET_COMPLEXITY_CHANGE:
- Production: **179 added / 46 removed, net +133 lines across eight existing files**. No production module added. Exact test additions/deletions are recorded in `verification.json`. Added state/observation fields, one shared ATR adapter helper, stricter version binding and focused tests are required for restart, symmetric trails and parity. Generated Graphify files remain local and are excluded from implementation commits.

UNRESOLVED_POLICY_DECISIONS:
- Next-bar trail ordering is resolved by the owner's explicit decision in this session.
- The owner additionally approved protective checks → declared completed-bar signal → max_bars → next-bar trail update, with signal execution at the first legitimately executable price after the signal and all required inputs are available. **No policy choice is outstanding for that scope.** Implementing the transport and validation of that legitimate execution observation requires the D6 timing interface; a snapshot price or same candle close was not substituted.
- Wall-clock entry TTL and pending limit/stop-entry behavior have no StrategySpec contract. A future owner-defined contract is required to add them. Existing DSL validity windows and authority freshness checks are preserved.

D6_INTEGRATION_DEPENDENCIES:
1. Reconcile/apply the supplied shared vector patch inside Opus's final `_trade`, simulate and trade_table implementation. Preserve D6's decision/fill clock, cost timing and availability checks. The patch supplies semantic observations only; it deliberately chooses no alternate fill clock. For signal exits, replace its explicit fill barrier only with the first legitimate executable price after the maximum availability of the decision inputs, and apply identical cost timing to actual and null runs.
2. D6's historical readers/evaluators must provide legitimate completed, available observations, the declared timeframe, prior ATR history, and unchanged spec/exit identity to the canonical route. Event timestamps alone cannot establish historical availability. No fabricated availability or protected data was used here.
3. Connect the pending signal decision in PaperExecutor and LiveExitAdapter to D6's legitimate executable-price observation. Preserve initial/native protection until confirmed execution. Extend existing paper exit evidence replay to bind decision and fill separately, validate actual fill time/price/availability, and retry partial/failed submissions. The current closed-trade verifier must never label a pending decision as filled. No new interface shape was invented here.
4. Run `verify_shared_vector.py` before reconciliation only; after integrating the diff, run its handoff tests directly on actual merged source, the complete focused suites and the D6 replay/timing suite, including executed signal exits and their costed outcomes. Update the candidate barrier-negative test to the final D6 interface while continuing to reject missing or same-close execution evidence. Do not reapply the diff blindly after Opus edits.
5. `integration-safety.json` proves no edits to feed, market_provenance, rolling, vector_backtest, spec_evidence or research/evaluate at delivery.

D7_STATUS: Independent approved entry, ATR and signal-decision semantics/adapters implemented; canonical ATR/signal observation patch isolated; executable signal fills and full merged parity remain dependent on D6 timing reconciliation.

READY_FOR_INTEGRATION: Ready for coordinated D6 reconciliation, **not** for standalone deployment or a claim of complete merged cross-engine/live parity. Adopt the shared interface and repeat merged-source checks before qualification or activation.
