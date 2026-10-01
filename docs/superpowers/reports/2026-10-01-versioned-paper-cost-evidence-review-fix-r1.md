# LUFFY-VERSIONED-PAPER-COST-EVIDENCE-REVIEW-FIX-R1

PASS; ready for independent final recheck. This fixes only the commission-notional P1 in /mnt/luffy-data/luffy/cost-evidence-r1. No commit or deployment.

Commission now consumes the already verified execution evidence for its own entry or exit leg. Its notional is that leg's validated executable price multiplied by the proven quantity used for executable economic P&L. The receipt retains executable price, quantity, notional, applicable rate/classification, computed amount, and executable source provenance including ID/hash. The commission-rate source remains independently bound to the exact trade/version/install. Rates and TEST-ONLY rate fixtures are unchanged.

A known commission rate without an ESTABLISHED executable price/quantity basis yields COMMISSION UNAVAILABLE, reason EXECUTABLE_COMMISSION_NOTIONAL_UNAVAILABLE. It retains rate provenance but no numeric fee or notional. There is no fallback to paper, reference, decision or signal prices. Net P&L remains UNAVAILABLE and economic probation INCOMPLETE_COST_EVIDENCE; no approval request is created.

The existing reviewer fixture is reproduced directly: quantity 1, paper entry 100.00, validated executable entry 100.01; paper exit 110.00, validated executable exit 109.99. Both fees use their executable notionals and original fixture rates. Tests independently calculate the two fees, total commission, achievable-fill net P&L and net profit factor across all 15 trades with strict numeric checks. The old paper-notional net factor is explicitly unequal to the corrected result.

Replay recomputes each commission from frozen execution and fee sources. Rehashed receipt tampering of either leg's executable price, quantity, notional, rate, amount or execution-evidence hash is refused by replay and owner approval. Changing either leg's executable source also invalidates the receipt. Existing finalized receipts are not mutated or upgraded; incompatible prior complete receipts fail replay.

Funding logic and execution/slippage arithmetic are unchanged. The exact identity, exit-semantics, both-leg requirements, TEST-ONLY isolation and existing approval re-verification remain intact. Existing paper/live fence tests and complete-cost approval paths remain covered by the requested suites; capacity remains UNAVAILABLE and first-live false. No Risk, capacity, approval architecture, probation thresholds, production source adapters, frontend or Graphify changes.

Focused validation: 186 passed in 54.58 seconds across tests/test_paper_cost_protocol.py, tests/test_strategy_factory_handoff.py, tests/test_versioned_paper_execution.py, tests/test_versioned_paper_probation_truth.py and tests/test_exit_semantics_probation_binding.py. The paper cost protocol alone also passed 44 tests immediately after the arithmetic change, before the new regression cases were added. git diff --check passes. No full suite was run.

Files changed for this fix only: trader/engine/paper_cost_evidence.py, tests/test_paper_cost_protocol.py and this report.

## Final surgical recheck and closure authorization

PAPER_COST_EVIDENCE_PROTOCOL_R1_REVIEW = PASS
SAFE_TO_COMMIT = YES
P0 = NONE
P1 = NONE

Final recheck inspected the corrected commission arithmetic and frozen execution-source binding, and reran all 60 paper cost protocol tests successfully (19.91 seconds). The strict reviewer regression verifies both executable notionals and corrected net profit factor. Missing executable evidence remains unavailable; rehashed basis/source corruption refuses replay and approval. git diff --check passes. This supersedes the earlier commission-basis P1 review finding.

The owner authorized closing this package with a package-only commit and branch push using message Establish truthful paper cost evidence protocol. Graphify artifacts, knowledge churn, frontend/M4 and runtime/shadow data are excluded. Deployment, main merge and production restart remain unauthorized.
