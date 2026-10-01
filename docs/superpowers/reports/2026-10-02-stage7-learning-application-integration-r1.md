# Stage 7 learning application integration R1

Package: **PASS**, application authority and first registered closed loop **TESTED**.
Validation: **232 focused tests passed**, `git diff --check` **PASS**.

Base: `503d93c`, latest committed verified-outcome producer branch.
Worktree: `/mnt/luffy-data/luffy/stage7-learning-application-r1`.
Branch: `luffy-stage7-learning-application-integration-r1`.
Committed on the package branch for review; no deployment, restart, production learning write or order.

## Application contract

`trader.learning.application` consumes verified LearningEvidence and an exact
LearningUpdateProposal; it does not choose what to learn. It re-verifies the
original outcome, attribution, replay and evidence through `verified_history`,
recomputes the exact registered proposal, and binds the current evaluator,
application code, current Risk/config policy and Governor target identity.
The proposal's lifecycle precondition is paired with a Governor history hash,
so a return to the same named state does not conceal an intervening change.

Only the already registered `existing-recent-decay.v1` can apply. Its existing
implementation authorizes `RETIRED`, not invented DEGRADE/PAUSE actions. Its
historical evaluator recomputes the declared-universe evaluation and existing
configured sample/coverage requirements. No new rule, threshold, reward,
confidence adjustment or allocation weight was added. The other six SDD
categories retain explicit registry entries without application rules and
return UNREGISTERED_RULE. Risk/control/capacity/spec targets are unregistered.
A proposal accepted by the evaluator can still receive CONFLICT if the current
Governor lifecycle does not permit its transition; learning cannot widen that
transition vocabulary.

## Owning authority and receipts

Application reads the target and writes its receipt inside BEGIN IMMEDIATE.
The existing Strategy Governor accepts the exact expected target and uses
that same connection for its existing transition. Governor reads, transition
and receipt therefore share a single commit/rollback boundary. Learning never
inserts lifecycle events directly. The old in-memory fixture helper delegates
to this single authority rather than maintaining a separate application path.

`learning-application-receipt.v1` records application/evidence/proposal ids,
rule and evaluator version, target identity, previous/requested/resulting
values, Governor receipt, application time, source hashes, result and reason.
SQLite update/delete guards make receipts immutable; successful proposal ids
are unique. Application ids bind evidence, proposal, target history and exact
current source versions. A retry returns the original receipt, including its
original timestamp. Another request for an applied proposal cannot reapply or
reverse it. A failed gate may append a diagnostic receipt but changes no target.
A crash after Governor insertion rolls both records back.

## Evidence and forbidden effects

One verified historical evaluation is consumed per proposal. The application
never counts outcomes as samples or pools repeated/shared-lineage records.
Only the unchanged registered evaluator determines trade sufficiency. Stage 6
exact-root evidence grouping remains available and is checked with its existing
verified lineage tests; shared roots produce one group, with independent
sample count unknown and confidence aggregation NONE. No statistical
independence or sample correction was invented.

Execution-quality evidence has no lifecycle eligibility and no registered
execution/cost target in R1. It cannot update predictive confidence. Failed or
inconclusive Research Bank recall retains context-only status, and unregistered
suppression is refused. StrategySpec, owner Risk limits, catastrophe boundary,
leverage, control state, capacity and owner approval remain outside learning's
mutation vocabulary. Application can only retire an already governable exact
version; it cannot activate anything.

## Real read-only shadow

The shadow uses a query-only SQLite snapshot of the real production journal.
It invokes no production constructors, migrations, Governor writes, venue
calls, backfill or runtime flags. Only retained captured evidence is inspected;
legacy decisions without outcome captures do not become invented proposals.
At the frozen as-of `1790899200000`, proposals, replay-complete,
registered-rule eligible, applicable, would_apply, insufficient, stale/conflict
and unregistered counts are all **0**. Production mutations and venue calls
are **0**. Real application count is **0** because there is no retained
replay-complete LearningEvidence population. This is evidence status, not a
claim that Stage 7 or live learning is complete.

## Acceptance and review

Deterministic tests cover historical evaluation -> exact replay -> evidence ->
registered proposal -> application -> Governor retirement -> durable receipt ->
future Governor state, with unchanged StrategySpec and Risk/config. They also
cover same-id retries, reopened file-backed retry, concurrent workers, failure
rollback, changed state and ABA history, insufficient registered samples,
forged evidence/proposals, exact version/source binding, forbidden targets,
execution/confidence separation, failed-research context, shared lineage and
zero-write shadow execution. Existing factory boundary checks allow only the
explicit learning application consumer and separately verify its direct-write
boundary.

Evidence directory: `docs/superpowers/evidence/stage7-learning-application-integration-r1/`.
Graphify refresh is code-only AST, without semantic API extraction.
Stage 7 remains incomplete. The next recommended package is
`LUFFY-STAGE7-IMPLEMENTATION-CLOSURE-R1`.

Closure: raw runtime/shadow data and graphify-out artifacts are excluded from the package commit. The real shadow summary above is retained as review evidence.
