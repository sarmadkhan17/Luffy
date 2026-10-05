# Stage 7 learning target authority framework R1

Verdict: **PASS**. Software build complete; calibration/policy and real-evidence maturity remain blocked.

Base: latest committed Stage-7 closure, `9d2a1b8`. Review worktree:
`/mnt/luffy-data/luffy/workspaces/stage7-learning-target-authority-framework-r1`.
Branch: `luffy-stage7-learning-target-authority-framework-r1`.
No commit, deployment, restart, production target write or trading change.

## Framework and authority boundaries

`LearningTargetRef` binds category, exact target identity, owner, canonical current
state, version/history, hash, approved mutation shape and provenance. Contextual
coordinates bind subject/dimension/context; a mutable field must match that exact
dimension. Mutation accepts exactly shape/dimension/value. No free-form patch,
configuration mutation, StrategySpec writer or order interface is exposed.

One immutable authority map covers all seven categories. Adapters are stateless;
there is no parallel calibration/allocation/priority/weight/WorldModel state store.
The six production adapters deliberately expose no approved mutation boundary.
Configuration validity bounds and legacy formula constants are not adaptation
policy. A missing adaptive value is null and explicitly marked UNAVAILABLE, never
an invented numeric starting state. Attention/research/portfolio reads retain their
existing owner configuration input snapshot, not a new adaptive state. Calibration
can read the existing exact legacy agent fit; its file cannot be written through
this transaction protocol until its owner has an approved atomic update policy.

| Category | Single authority | Existing owner / read boundary | Production update boundary |
|---|---|---|---|
| confidence_calibration | AgentCalibration | agents.calibration.load; exact dimension/context | POLICY_UNAVAILABLE; no registered rule |
| strategy_allocation | PortfolioAllocator | portfolio.allocator.Inputs/allocate/verify | POLICY_UNAVAILABLE; no registered rule |
| attention_priority | Attention | cognition.attention / observability.attention | POLICY_UNAVAILABLE; no adaptive fields approved |
| research_priority | ResearchScheduler | research.runner / research.planner | POLICY_UNAVAILABLE; no registered rule |
| evidence_weights | ContextualEvidenceWeights | explicit contextual boundary around existing weights_online/blend owners | POLICY_UNAVAILABLE; global legacy weights are not contextual calibrated weights |
| world_model_probabilities | WorldModel | immutable world.model.WorldModel | TARGET_NOT_SUPPORTED; no mutable probabilistic field |
| strategy_lifecycle | StrategyGovernor | factory_handoff.lifecycle_target / govern_version | existing registered retirement only |

For confidence and contextual evidence weights, the explicit boundary isolates a
single dimension/context. It cannot change another dimension even if the owner
writer returns a broader mutation: postcondition verification aborts and rolls back
the entire transaction. Existing WorldModel descriptive states remain descriptive.
Research Bank recall grants no suppression or no-repeat authority. Allocator
remains proposal-only; learning has no sizing, live-resizing, Risk, capacity or
execution grant.

Exact dispatch is `(rule_id, rule_version, target_category)`. The compiled extension
registry is empty, immutable and has no runtime registration API or test-mode flag.
Only existing-recent-decay.v1 remains registered in production. Future reviewed
registrations must supply an explicit eligibility predicate and deterministic
`Mutation` evaluator; merely declaring a category cannot authorize anything.

Common application checks reverify historical replay, evidence/proposal binding,
application clock and exact current source/policy versions. The owner then rereads
and compares the entire target, including policy/provenance/history, validates the
requested dimension/value, and writes only through its owner method in the locked
receipt transaction. Stale state, ABA history and changed policy refuse updates.
A private application gate keeps the public adapter operation inside the receipt
transaction; it is not a security claim against arbitrary code running in-process.
Future owner writers must honor the shared atomic transaction protocol; legacy
file writers have no mutable production policy in R1.

Adapters return the common frozen `AuthorityReceipt`, containing application ID,
category/id, owner, prior state/version, typed requested mutation, resulting
state/version, exact rule, evidence, timestamp and provenance. Canonical serialized
receipts use the existing append-only learning_application_receipts table, hash
verification, immutable SQL triggers and unique applied-proposal constraint. Retry
returns the original receipt without reevaluation or another mutation. Legacy
stored receipts remain readable and are not rewritten.

Lifecycle keeps its existing historical evaluator/replay/sample gates and
Strategy Governor retirement. The common typed read/proposal API also supports
this existing path. Governor event and learning receipt roll back together. No
activation or owner-approval/capacity bypass is added.

## Acceptance and Stage-7 exit

All six categories satisfy deterministic production refusal (acceptance B), and
also exercise acceptance A using synthetic policies/rules/owner tables defined
only under tests/. Normal runtime imports no test registration; fresh-process
verification observes an empty extension registry. An applied synthetic receipt
can be replayed after restart without installing its test rule again, while new
TEST_ONLY proposals cannot execute through the production registry.

The Stage-7 A–O implementation criteria from the closure package are rerun across
outcomes, attribution, replay, immutable evidence, proposals, application,
Governor, failed-research memory, idempotency and cross-stage safety. These plus
the new category framework close the remaining software gap. Build closure does
not require a calibrated production rule for every target.

MISSING_IMPLEMENTATION: NONE.
STAGE7_BUILD_COMPLETE: YES.
STAGE7_REAL_WORLD_MATURE: NO.

Calibration/policy blocks remain exactly: no registered calibrated rules for
confidence calibration, strategy allocation, Attention priority, research priority,
evidence weights or WorldModel probabilities; no approved adaptive policy for
those targets; no mutable probabilistic WorldModel field; no registered failed
research suppression/no-repeat policy. Existing sample and coverage gates still
apply. Real retained captured population and undeployed prospective integrations
remain evidence/maturity blocks, not framework software gaps.

Next recommended implementation package:
LUFFY-STAGE8-EXPLAINABILITY-OWNER-CONTROL-FOUNDATION-R1. Ground owner queries and
approval surfaces in typed evidence/authority receipts; this grants no runtime or
trading authorization.

## Validation

- Initial focused run: 66 passed.
- Target run before the immutable adapter-result refinement: 40 passed.
- Final authority/receipt/lifecycle run: 72 passed in 47.95 s. Includes all seven
  categories, exact dispatch, replay, concurrency, rollback, immutable receipts,
  stale/ABA/policy/context changes, unrelated dimensions and direct-call refusal.
- Broad Stage-7 regression: 335 passed in 564.69 s, with one import-allowlist
  assertion identifying the newly relocated Governor adapter. The exact approved
  adapter path was added and its Governor attribute surface restricted to
  lifecycle_target/GOVERNOR_ALLOWED/RETIRED/govern_version. That check then passed
  in 1.37 s. The original broad output is retained in tests.txt; the correction is
  recorded in factory-boundary-followup.txt. All behavioral regression checks pass.
- Across the requested closure coverage plus the new category tests: 425 distinct
  checks pass, with the one inherited historical source-snapshot failure below.
  Overlapping focused reruns are not additional distinct checks.
- Inherited source-snapshot regression: 87 passed, 1 failed. The unchanged
  `test_decay_journal_readers_are_unchanged` compares `_record_registered` against
  an older Git base without the already committed capture `on_insert` callback.
  This was recorded by the prerequisite closure; neither source nor assertion was
  changed to conceal it.
- Python compilation, YAML parse and whitespace checks: PASS.
- Graphify updates use AST extraction only, with no semantic extraction/API use.

Evidence: `../evidence/stage7-learning-target-authority-framework-r1/`.
