# Stage6 normal source integration R1

Worktree: `/mnt/luffy-data/luffy/workspaces/stage6-runtime-portfolio-integration-r1`.
Base: `1bed3958740ec2d0d575aabc817978086d31d390` plus the retained uncommitted runtime corrective implementation. No commit, deployment, order, Stage7 implementation or Risk/allocator formula change.

## Source delivery

`source_adapter.py` reads the existing content-addressed publications written by `opportunity_live.persist` and `economics.persist`. It creates no store and has no publication capability. Normal routing defaults to `contexts/` and `economics/` beside the Journal. Existing publication directories can be routed with `portfolio.source_directories.contexts` and `.economics`; these are paths, not authority or model switches. Readers use at most 64 publications per directory, bounded payloads, exact canonical publication identities and replay. The existing Journal remains the capacity/account/funding/factory/Risk store.

A stored Opportunity Context is used at its original cut while all required bounded sources remain valid at read time and its exact portfolio matches the current reader. No context, economic model or authority is rebound to a later cut. Old mixed economics for the same opportunity refuses; duplicate matching publications refuse. Economics inputs, including exact gross/cost/reserve and context/allocation sources, come from the existing persisted economic receipt and are rebuilt by the existing model validators. The current factory bridge is reread and must equal the receipt's bound bridge source. Unregistered production methods remain unavailable.

Raw normal decision and StrategyVersion records have no authoritative expiry. Their existing `opportunity_live` freshness remains UNKNOWN. The candidate now explicitly requires an integer, valid expiry for every required CURRENT role. Unknown-expiry candidates remain incomplete and do not enter `freeze_lineage`; the checkpoint continues to NO_ALLOCATION. Economics and allocation expiry constrain candidate expiry without any new TTL. Portfolio freshness is also checked at actual read time against the existing venue contract.

The actual decision-store integration exposed and repaired the factory inventory clock mapping: lifecycle events use `at_ms`, governor events carry `at_ms` in their verified canonical record, and other receipt rows use `recorded_at_ms`. Missing clocks refuse with a reason instead of a KeyError.

Normal lineage freezing binds the candidate/context/spec/economics/capacity/source identities as well as the factory version chain. Replay reconstructs the candidate from those exact frozen authorities. Wrong cuts, changed context identity and stale evidence refuse. The prior lineage-only API remains supported for existing detached consumers.

## Acceptance evidence

Final focused regression: **296 passed, 1 deselected** (92.81s). Final normal-source integration: **6 passed** (64.82s). The deselected baseline failure is documented below, not counted as a pass.

`tests/test_stage6_normal_sources.py` persists a real factory-created synthetic StrategyVersion, verified synthetic capacity receipt in the existing Journal, Opportunity Context and complete synthetic economics in their existing publication stores. Test-only calibrations complete sources that do not exist in production; they never replace the reader, candidate constructor, common-factor reader, allocator, TradeIntent builder or Risk evaluator. Market snapshots enter through the normal runtime parameter and required OHLCV availability is derived by the normal reader, with no injected available-input assertion.

The normal reader constructs a complete candidate, freezes bound lineage, runs the runtime event gate/allocator, emits COMPLETE OPEN TradeIntent and reaches deterministic RiskManager evaluation (APPROVE or REFUSE with actual sizing/reason). Durable replay and duplicate suppression are checked. Separate tests read unavailable economics and unknown-expiry contexts and reach NO_ALLOCATION. The original Journal decision + actual Attention store path is also exercised through `current.checkpoint`, with no injected requests or ready downstream objects. Mixed economics and lineage/context identities refuse.

The broader regression invocation also exposed an existing unrelated failure in `test_opportunity_context.py::test_replay_from_persisted_forms_reproduces_context`: the base Journal lacks `record_attention_selection`. The same test fails in isolation before any source adapter is called. Neither Journal nor selection persistence was edited by this package. That failure is retained in `preexisting-selection-failure.txt`; final focused checks explicitly deselect it, rather than marking it passed.

## Real shadow

One bounded read-only checkpoint against production source stores passed, with zero exact StrategyVersions, capacity receipts or probation receipts; neither normal publication directory exists and all production gross/cost-scope/reserve/allocation registries are empty. Four descriptive contexts and three current holdings were observed. Result: zero candidates, NO_ALLOCATION, zero TradeIntents/Risk approvals/orders, no authenticated requests and no production mutations. Missing current marked exposure and actual initial-margin utilization remain unavailable. The complete persisted-source test distinguishes missing real authority from a broken delivery adapter.

Evidence: `docs/superpowers/evidence/stage6-normal-source-integration-r1/` (final-focused-tests.txt, original regression result, isolated pre-existing failure, real-shadow-summary.json and verified-real-shadow/).

Stage6 source architecture closure does not establish real trading readiness. Stage7 remains a recommendation only: `LUFFY-STAGE7-REAL-LEARNING-AUTHORITY-CONSUMER-INTEGRATION-R1`.

## Package return

STAGE6_NORMAL_SOURCE_INTEGRATION_R1: PASS
FRESHNESS_CONTRACT: PASS
NORMAL_ECONOMICS_SOURCES: PASS
NORMAL_CAPACITY_SOURCES: PASS
NORMAL_PORTFOLIO_SOURCES: PASS
SOURCE_ADAPTER: PASS
FREEZE_LINEAGE: PASS
NORMAL_PATH_TO_RISK: PASS
INCOMPLETE_PATH: PASS
RUNTIME_REGRESSION: PASS (296 focused checks; one unrelated base failure documented)
REPLAY: PASS
REAL_SHADOW: PASS
REAL_SHADOW_RESULT: 0 candidates; CASH / NO_ALLOCATION
MISSING_ARCHITECTURE: NONE within this package
REAL_EVIDENCE_BLOCKED: exact StrategyVersions/current bounded opportunities; calibrated gross/forward-cost/reserve evidence; established capacity/venue leverage/account eligibility/liquidity evidence; allocation authority/bounds; marked exposure/actual initial-margin fields; calibrated materiality authority.
STAGE6_BUILD_COMPLETE: YES (architecture; real allocation remains inactive)
TRADING_BEHAVIOR_CHANGED: NO
REAL_ORDER_SUBMISSIONS: 0
STATE_UPDATED: YES
NEXT_UPDATED: YES
NEXT_RECOMMENDED_PACKAGE: LUFFY-STAGE7-REAL-LEARNING-AUTHORITY-CONSUMER-INTEGRATION-R1
SAFE_TO_REVIEW: YES

Files changed by this package: trader/portfolio/source_adapter.py, candidate_bridge.py, current.py, opportunity_live.py; scripts/opportunity_context_shadow.py, runtime_portfolio_shadow.py; tests/test_stage6_normal_sources.py; STATE.yaml, NEXT.yaml; this report, package evidence and AST graph updates. Existing uncommitted Risk, Kernel, runtime, measurement and other corrective work was retained.
