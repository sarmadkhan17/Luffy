# Stage7 normal replay and WorldModel consumer closure R2

Base: `50a4545484798207e4b126a0b7a0547ea9b0ec79`, requested
`luffy-stage7-normal-learning-loop-closure-r1`. Implementation is isolated at
`/mnt/luffy-data/luffy/stage7-r2`, branch
`luffy-stage7-normal-replay-world-consumer-closure-r2`. The shared production
workspace was on `main` at `1bed395` with unrelated changes and was preserved.
Claude's required minimal execution probe returned `Not logged in`; implementation
continued using the available Codex runtime.

Only the two R4 defects are in scope. No commit, deployment, order submission,
StrategySpec mutation, Risk change, target-owner change, registered-rule change,
or predictive research bridge change is part of this package.

## Decision, outcome and replay

New registrations freeze `decision-stage-contract.v1`. SCAN requires the exact
cycle, decision, data, configuration, control and reasons actually used there.
Any additional supplied dependency remains required, including an unavailable
supplied source. A gated HOLD with exactly one consulted strategy signal binds
that signal's exact version and exit authority; ambiguous multiple-signal cases
remain unavailable rather than selecting a version after the fact. Strategy identity implies its exact exit authority. The scan
stage does not consult a trading-venue position/environment identity; that
identity belongs to the allocation/trading stage, not a raw candle scan.
`not_consulted` never changes requirements. Legacy registrations retain their
original broad contract and are never backfilled or rewritten.

ALLOCATION freezes the original world/model record, Opportunity Context with its
live receipt, complete portfolio observation, economics and capacity references
inside the exact proposal inputs, TradeIntent, strategy/exit authority,
configuration, control and original market snapshot. The existing production
`current.checkpoint -> deliver_allocation -> allocation_sources` caller delivers
these objects. Tests use persisted context/economics publications and this exact
reader; they never call `journalize_allocation` or insert a source manifest.

Risk remains a typed, exact action on that allocation registration. Replay
re-evaluates the captured `portfolio-risk-decision.v1` input binding using the
unchanged isolated Risk adapter, checks its proposal, intent, cut and result,
and joins them to the allocation sources. Only a requested OPEN refusal is a
Risk-blocked outcome. A NO_ACTION refusal cannot change HOLD/CASH into a block.

Forward resolution selects the unique linked allocation registration for the
root decision. Ambiguity fails closed rather than choosing the latest stage.
Normal caller delivery failures retain the attempted terminal identities, so
forward resolution cannot fall back to a complete scan when allocation source
delivery failed. The root's scheduled prediction is retained verbatim, including its original
availability clock; the allocation owns the terminal action. A future target
cannot precede the governing allocation. Execution still requires the exact
entry TradeIntent identity and never implicitly adopts an unrelated allocation.

Each new outcome freezes `terminal-source-contract.v1` with its kind-specific
requirements. Detached replay retains parent registrations, source manifests,
source bytes and frame chunks and verifies their ids, hashes and chronology.
Missing used sources remain UNAVAILABLE. Unassessable refusal/status observations
remain incomplete until a genuinely registered forward measurement exists.
No execution cost, monetary outcome or counterfactual profit is invented.

R4 reproduction checks prove the earlier scan cannot supply an exact portfolio
opportunity and identify the four legacy missing roles: world, context,
portfolio, derivative_identity. The correct linked allocation supplies them.
A scan-only HOLD uses no fabricated opportunity; missing control still produces
REPLAY_PARTIAL. Normal portfolio HOLD and requested OPEN/refusal are separately
measured through `engine.outcomes.resolve_pending` and produce verified
LearningEvidence when all genuinely required sources exist.

## WorldModel normal consumer

The established normal query is Attention's world-volume component, reached
through `world_producer.produce/evaluate -> evaluate_snapshot ->
attention.evaluate -> _world_volume`. It now queries effective exact-scope,
INTRADAY claims for that existing volume-anomaly dimension and presents their
confidence alongside the immutable claim as Attention reasoning input.

This is a descriptive Attention query: regime, direction and strategy family
are explicitly NOT_APPLICABLE. It cannot consume a named-regime or directional
strategy overlay. Scope, horizon, dimension and evidence source remain exact;
no fuzzy fallback exists. Claim confidence is not converted into a new salience
formula, hard Risk, signal or economic estimate.

`WorldQueryReader` retains the exact governed revision consumed by a live query.
Historical investigation/Attention re-evaluation consumes those frozen revisions,
not today's owner state. No applicable overlay preserves baseline confidence.
The normal-consumer tests use an upstream TEST-ONLY claim emitter and a
TEST-ONLY governed application; no production emitter or learning rule is added.
They cover exact matching and mismatches in instrument, horizon, regime,
evidence type, direction and strategy family, immutable snapshots and frozen
query replay. Existing production WorldModel observations and selection behavior
remain unchanged today because the normal producer emits no WorldClaims.

## Evidence and boundaries

Artifacts: `../evidence/stage7-replay-world-closure-r2/`.
The bounded read-only shadow inspected the production journal and 64 retained
Attention scans with `mode=ro`, `query_only` and a stable read transaction.
It found zero scan/allocation/Risk-linked registrations, resolved outcomes,
replay-complete/partial samples, claim queries and applicable overlays.
Exact missing roles: empty because no registered population exists. Production
mutations and real order submissions: zero. These zeros do not establish real
learning eligibility or calibrated intelligence.

Calibration/rules remain unavailable for contextual reliability, evidence
weights, Attention priorities, research priorities, allocation learning and
WorldModel confidence. Real complete samples, a production WorldClaim emitter,
and intent-routed execution evidence remain absent. These are policy/evidence
limits, separate from this package's two architecture defects.

The base's `test_attention_kernel_wiring.py` cannot collect because its imported
`tests.test_attention_supplemental` helper is absent. No unrelated helper was
invented. The runtime portfolio suite contains an independent normal Kernel
checkpoint/execution-capability check. Other relevant Stage5/6/7, Attention and
Predictive Research Bridge regressions are recorded in the validation artifacts.

Graphify was updated with the code-only AST command (no semantic extraction,
no API cost). Generated graph outputs are included.

Validation status: **PASS**. A = CLOSED; B = CLOSED; MISSING_ARCHITECTURE = NONE.

The final strengthened normal-adapter suite passed 14/14, including parent
signal-version provenance for HOLD and BUY, both Risk REFUSE and APPROVE,
LearningEvidence, exact overlay matching and six context mismatches.
The regression run passed 370 of 372 initially. The direct Risk-import boundary
and the obsolete NO_ACTION-as-Risk-block fixture expectation were corrected;
both targeted reruns passed. Thus 372 regression checks and 14 distinct closure
checks pass (386 unique checks). Three final focused signal-binding/lifecycle
regressions also pass. Initial failure logs are retained transparently alongside
the resolved reruns in `validation.json`. The excluded historical module is
identified above; it was not counted as passing.

STAGE7_BUILD_COMPLETE = YES for this package's two architecture defects.
TRADING_BEHAVIOR_CHANGED = NO; REAL_ORDER_SUBMISSIONS = 0.
STATE_UPDATED = YES; NEXT_UPDATED = YES; SAFE_TO_REVIEW = YES.
Next recommended package: LUFFY-STAGE5-7-FINAL-INTELLIGENCE-GATE-R4B.
