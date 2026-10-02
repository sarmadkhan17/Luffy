# Stage-7 verified outcome producer integration R1

Implementation in the isolated `luffy-stage7-verified-outcome-producer-integration-r1`
worktree, based on committed decision-replay integration `1c77fdb`. Committed for review; not
deployed. Production checkout changes were preserved. Claude CLI execution was
unavailable (`Not logged in`); implementation used the available local tools.

The existing off-path accounting worker now delivers its completed whole-trade
receipts into capture and the immutable learning chain. Delivery is recovered
from retained complete attempts after restart, without recapturing venue history.
It requires the persisted VERIFIED entry identity to agree with the original
manifest's decision, cycle, StrategyVersion and spec hash. Legacy receipts
without that binding receive an unbound queue disposition, never a fabricated
registration. Only learning tables are written; no trading/control table is
modified. The existing `accounting.enabled` operation boundary is preserved.

Verified monetary outcomes retain gross/net P&L, actual commissions, signed
funding, fill-based holding duration, recorded exit reason and recorded
intervention. Absent slippage remains UNAVAILABLE. Booking-only money remains
UNAVAILABLE. Paper receipts remain simulated or unassessable.

Rejection reasons come from the original immutable registration. Exact recorded
Risk denial is classified separately; mutable later decision text cannot rewrite
it. Later forward measurements remain COUNTERFACTUAL, SIMULATED / UNREALIZED.
CASH requires an exact registered live candidate/context; no candidate means no
outcome measurement. Registered opportunity resolutions automatically produce
status evidence, including SKIPPED, EXPIRED or INVALIDATED. A status observation
has no simulated price target and remains UNASSESSABLE; it cannot claim missed
profit. The allocator capture adapter registers its exact supplied candidate and
records NO_ALLOCATION as SKIPPED. There is no price-based opportunity backfill.

Research uses the existing verified question/plan/evidence/result/run/Bank chain.
INCONCLUSIVE, REFUTED and SUPPORTED are retained as recorded research results,
without predictive-edge authority. Execution quality has its own outcome kind
and verifies the exact booking/order/fill evidence; incomplete fill provenance
cannot become authoritative. Supplied submission, requested quantity, spread,
slippage, depth, partial and rejection fields remain absent when unavailable.
Raw fill timestamps, quantities, prices and fees remain retained. Predictive
quality is NOT_APPLICABLE and causal effect UNKNOWN.

Recorded operational/data failures have a distinct data-quality kind and
NOT_APPLICABLE market conclusions. Missing snapshots are data incidents rather
than manufactured missed opportunities. Execution incidents retain their
operational incident category.

Finalized captures automatically persist Outcome, Attribution, ReplayManifest,
LearningEvidence and LearningUpdateProposal. Identity binds the original
manifest, original event/version, exact source identities and measurement cut.
Retries and fresh-process replay preserve identity. Incomplete replay is
explicitly REPLAY_INCOMPLETE in the chain and cannot authorize learning. Only
existing-recent-decay.v1 is requested; its existing evidence and replay guards
remain authoritative. No learning application API is invoked, and no new rule,
weight, confidence change, StrategySpec change or causality claim is introduced.

The real shadow uses SQLite URI mode=ro, query_only and one read snapshot. It
observed 705,084 retained decisions without original learning registrations and
zero eligible captured outcomes in all eight producer categories. All 705,084
unregistered decisions are counted as replay-incomplete inventory faults, not
invented outcomes. Learning evidence created: 0. Applicable updates: 0. Venue
calls, source migrations, live learning applications and production mutations: 0.
This is a retained-journal inventory, not proof of historical completeness or
production deployment. The normal deployed kernel's missing WorldModel/context
sources remain missing.

Evidence: `../evidence/stage7-verified-outcome-producer-integration-r1/` contains
real-shadow.json, focused final regression output, follow-up checks and code-only
Graphify output. Validation: 212 focused tests passed, followed by 4 checks covering final
incident and research adjustments. YAML parsing and git diff --check passed.
The graph refresh uses AST extraction only; no semantic extraction or API use.

Stage 7 remains incomplete. The single remaining implementation package is
LUFFY-STAGE7-LEARNING-APPLICATION-INTEGRATION-R1: integrate applicable replayed
proposals with existing authority, preserving exact-version freshness,
idempotency, replay verification and the no-unregistered-rule boundary. This
recommendation grants no runtime, trading, deployment or restart authorization.
