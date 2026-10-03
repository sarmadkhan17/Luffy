# Stage7 normal learning loop closure R1

Package `LUFFY-STAGE7-NORMAL-LEARNING-LOOP-CLOSURE-R1`. Uncommitted, undeployed. Worktree
`workspaces/stage7-normal-learning-loop-closure-r1`, branch `luffy-stage7-normal-learning-loop-closure-r1`,
base `3bf9f87` (contains b56682e Stage7 owners, Stage6 normal sources, predictive bridge).
`main` does not contain the Stage 7 lineage.

## Closure of the four Astra R3 gaps
| Gap | Status | Mechanism |
|---|---|---|
| A decision/outcome continuity | CLOSED | Portfolio stage registers on the REAL decision id/cycle (`decision:<id>#allocation:<proposal>:<version>`, linked parent registration). Proposal, intent and the portfolio RiskDecision bind to it; a requested open that Risk does not approve resolves RISK_BLOCKED on the same chain. No `allocation:` identity, no fabricated HOLD; absent origin decision is an explicit capture failure. Trades naming a TradeIntent resolve that stage (`capture.trade_event`); others stay on the original decision. Optional `snap.learning_sources` removed; scan decisions state un-consulted later stages explicitly. |
| B generic rule dispatch | CLOSED | `learning/dispatch.py`: verified evidence -> production registry -> exact kind/source-version/context/sufficiency screen -> rule-made proposal -> queue -> existing checkpoint. Kernel runs it each cycle. Production registry is empty, so result is NO_PROPOSAL/UNREGISTERED. Idempotent per (outcome, registry digest). |
| C contextual vote consumption | CLOSED | Analysts declare `evidence_timeframe` (depth: none). Votes carry only known context; unknown horizon/regime => no adjustment. Verified through `Orchestrator.decide` -> `net_score`; no state => identical to baseline. |
| D WorldModel learned confidence | CLOSED (seam) | `WorldModel.effective_claims/effective_claim` + `consumers.world_claims`: base claim plus separate exact-context overlay; snapshots/claims untouched. |

Exit semantics are bound per candidate/version (`exit_binding`, cut bindings, per-signal bindings on scan decisions); missing => UNAVAILABLE.

## Limits (stated, not hidden)
- Execution is not routed from intents (`execution_routed=False`); the chain resolver is in place and tested with fixture trades.
- No production code emits WorldClaims; the overlay has no real claims to read yet.
- Risk-blocked / missed / data-quality outcomes are UNASSESSABLE with no registered future measurement, so they are not replay-complete evidence (NO_PROPOSAL). CASH counterfactual and execution-quality are replay-complete.
- Production adaptive rules: none (POLICY_CALIBRATION_BLOCKED). Real evidence: none captured (REAL_EVIDENCE_BLOCKED).

## Validation
- New suite `tests/test_normal_learning_loop_closure.py`: 37 tests pass (six targets through the normal loop, idempotency, restart, stale, rule-version, hash/policy mismatch, lifecycle regression, legacy isolation, AST safety audit).
- Related suites (learning foundation/application, real integration, decision sources, verified outcomes, historical capture): pass after final refactor.
- Full suite (6 files un-collectable in this worktree, environment): 142 failed / 5419 passed. 139 of the failures also fail on the base 3bf9f87 (environment: venue/web assets, M3.2 etc.); 3 candidates unique to this branch were re-run: 2 passed (flaky), 1 was a real regression (runtime.py gained `test_registry`), fixed and now passing. Lists in the evidence directory.
- Real read-only shadow: all counts 0 (production DB has no learning tables); 0 mutations, 0 venue calls.
Trading behavior changed: NO. Real order submissions: 0.
