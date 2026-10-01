# LUFFY-VERSIONED-PAPER-PROBATION-TRUTH-FIX-R1

Scope: the two independent-review P1 defects over the current uncommitted
PAPER-EXECUTION-R1 worktree on e60e045. No commit or deployment.

Probation now requires per-trade explicit factory version/install identity,
matching strategy/hash, explicit paper mode, closed status and the exact
install window. Missing/ambiguous identity yields INCOMPLETE_IDENTITY;
mixed-version paper evidence cannot produce an approval request. Legacy
compiler provenance alone is insufficient. The production paper ledger
already persists every required field and immutable entry identity; no
schema change or historical backfill was needed.

No authoritative production paper-cost rule exists. `engine.paper` records
commission/slippage/funding UNAVAILABLE; `trade_provenance` contains venue
commission evidence, price references and unavailable per-trade funding,
which cannot establish the costs of isolated paper executions. No existing
rule proves cost non-applicability for these trades. Production cost evidence
therefore remains unavailable, including funding; unknown never becomes zero.

INCOMPLETE_COST_EVIDENCE leaves qualifying stats unset and records separately
labeled GROSS_ONLY / NOT_ECONOMICALLY_VALIDATED diagnostics. SATISFIED uses
net P&L after all required evidenced costs, with unchanged 15/40%/1.15
thresholds. Evidence binds the trade, version and install, with explicit
USDT units. Signed funding is preserved. Cost evidence participates in the
receipt's evidence hash; owner approval, eligibility and probation retries
reverify it. Identity insufficiency takes deterministic precedence.

The actual compiler → Kernel paper entry → persistent paper state → paper
close → probation acceptance runs twice. Fifteen real production-path
paper closes with unknown costs produce INCOMPLETE_COST_EVIDENCE and no
approval request. A deterministic TEST-ONLY provider installed by pytest
monkeypatch produces complete evidence, net-qualified SATISFIED and test
owner approval. Fixture amounts are observations for testing, not a fee,
slippage or funding model. Production has no registration/configuration hook
and never imports the test provider. No closed trade rows are inserted in
these acceptance paths. Synthetic rows in separate regression tests exercise
identity and evidence corruption only, not execution acceptance.

Paper/live fences remain at Kernel and Executor; no venue submission.
Restart/replay and unsupported-exit refusals remain covered. Effective
capacity stays UNAVAILABLE; first-live eligibility stays false. Paper exit
mechanics are TESTED; live runtime exit parity remains unresolved. No progress
increase, Risk/capacity change or Intelligence Spine modification.

Validation: 460 focused regressions passed (14 task-relevant test modules).
YAML parses and git diff --check pass. Required Graphify code-only AST
update completed (22,591 nodes, 56,821 edges); no semantic extraction or paid
API was used. It reported 98 unclassified files skipped and regenerated
existing graph/cache artifacts. Local Claude execution
check returned Not logged in; implementation proceeded directly within the
authorized package. No process restart or operational write was performed.
