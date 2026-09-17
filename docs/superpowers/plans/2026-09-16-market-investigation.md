# M1 implementation plan — persistent market investigations

Status: M1.1–M1.4 ACCEPTED; M1.5 PARTIAL. See the
[implementation/deployment report](../reports/2026-09-16-market-investigation-delivery.md)
and [canonical checklist](luffy-delivery-checklist.md). The remaining exact task
is deployed authentication acceptance for the existing owner dossier. Reuse the
implemented core/consumer and evidence; do not repeat the project audit.

## Result the owner should receive

For an actual newly observed anomaly, Luffy maintains a dossier saying:

> What changed; which market/asset state it suggests; which explanations are
> compatible or contradicted; what evidence would distinguish them; what changed
> since the previous observation; what cannot currently be known; and the next
> research question or reason to wait.

The implementation must demonstrate evidence-driven assessment changes. Producing
another static anomaly list or another continuation/reversal count does not meet
M1. It does not yet claim superior trading performance or replace admission.

Reuse the reasoning semantics and paired demonstrations in the
[existing investigation design](../specs/2026-09-15-cognition-investigation.md).
That document's old fixed priority/offline-only delivery sequencing is superseded
by this roadmap: develop against synthetic/offline inputs first, then connect a
bounded forward consumer and owner view. Preserve its frozen v1 artifacts and
point-in-time safeguards. Its already-inspected examples are development data,
never new untouched evaluation evidence.

## Boundaries

- Current crypto-futures demo scope; all deterministic risk/admission controls
  and research.referee=false / research.handoff=false remain in force.
- Read versioned attention snapshots through an adapter; do not mutate the
  deployed attention/forward forecast ledgers or their protocols.
- New investigation state has its own versioned store. No trading/risk/LLM calls
  in the producer or event-update logic. Budgeted unstructured reasoning and
  source acquisition can be added later with clear provenance.
- A RESEARCH recommendation is a specific frozen question, not a buy instruction
  or automatic strategy admission. Candidate construction connects in M3.
- Memory retrieval that changes future reasoning is M2. Do not report it complete
  merely because M1 appends investigation events.

## Inputs and records for M1.1

Proposed file locations are implementation targets, not present capabilities:

| File | Responsibility |
|---|---|
| `trader/cognition/investigation.py` | Versioned contracts and pure state/update functions |
| `trader/observability/investigation.py` | Read-only snapshot adapter, independent ledger, bounded once consumer |
| `tests/test_market_investigation.py` | Point-in-time semantics and paired behavioral demonstrations |
| `tests/test_investigation_consumer.py` | Persistence, deduplication, restart, bounds and degradation |
| Existing dashboard server/panel | Owner-readable dossiers and component health |

Prefer a small number of modules. Review the existing cognition contracts before
adding types; adapt reusable parts rather than copy the whole replay engine.

Minimum records:

- **StateSnapshot:** schema/version, source scan, symbol, frozen cohort/membership,
  as-of/observed/available timestamps, evidence/version IDs, measured state
  dimensions, transitions, contradictions, missingness and code/config version.
- **Investigation:** immutable ID, episode/source IDs, actual registration time,
  primary trigger and secondary unresolved questions, question, alternatives,
  invalidators, measurement definitions, expected inputs and deadline.
- **InvestigationUpdate:** immutable event ID, investigation ID, actual as-of and
  observation time, previous/new qualitative assessment, input IDs, reason codes,
  concise rationale and next discriminating test/action.
- **OutcomeEvidence:** target keys/versions, observed measurement, measurement
  status, resolution/availability time and remaining explanatory uncertainty.
  This is not actual or simulated P&L unless a separate contract says so.

State dimensions initially use inputs already present: breadth/market direction,
relative divergence, volatility transition and volume anomaly. Participation,
liquidity, sector and catalyst dimensions remain unknown when unavailable. A
world-state label must expose its exact evidence/rule; adding a label is not a
learned regime model. Freeze rules as prototype definitions, not profitable
thresholds.

## Investigation behavior for M1.2–M1.3

Use the existing dominant trigger to choose one primary question family; retain
other components as context/contradictions rather than expanding combinations.
Volume, volatility and relative-return events must get different relevant
questions and measurements. Distinguish observable predictions from explanations
of who acted or why. Volume alone cannot establish informed flow or liquidation.

Each alternative has explicit support, contrary evidence, predicted consequence,
invalidation, evidence still needed and confidence status. Use untested,
compatible, contradicted, indistinguishable, unresolved/not-testable semantics.
Probabilities remain null. Present every alternative; a category that matches a
subsequent path is not a successfully predicted causal explanation.

Every update chooses at most one bounded next action:

| Action | Required reason |
|---|---|
| WAIT | A specified future observation is required, with earliest usable time |
| ACQUIRE | A named missing source could distinguish specified alternatives; recommendation only in M1 |
| RESEARCH | A mechanical, falsifiable question is supported by the available prefix and can be frozen for M3 |
| UNASSESSABLE | No feasible discriminator exists within current data/constraints |

No new investigation for the same open episode on every scan. Repeated or
irrelevant evidence cannot manufacture a changed assessment. Retain prior
updates and distinguish new information from a revision of an existing input.

### Forecast publication and measurement rules

Separate retrospective state description from predictions. Freeze a new catalog
version and measurement protocol in M1.1 before applying it to outcomes.

The existing offline catalog is a development reference, not permission to grade
a new live prediction using a bar already forming at registration. A live test's
entire target window must start after registration/publication. Define its exact
bar keys, cohort, frozen baseline/scales, deadline, guard interval and missing-data
expiry before registration. Use a guard longer than the worker's enforced maximum
runtime so the commit precedes the first target bar.

State explicitly whether a measurement uses future intrabar returns, successive
closes, a frozen baseline or a relative cohort. Required keys must follow that
choice. Volume/volatility require the full declared window; cohort-relative tests
require their declared frozen cohort. Missing keys prevent resolution. No nearest
bar substitution, silent subset, partial-window final grade or later revision of
a past assessment. Later-arriving data gets its actual observation timestamp.

This new protocol does not change the already registered 16 price forecasts or
their first deadline. Their outcomes cannot be used to choose new thresholds and
then be presented as untouched validation of those thresholds.

## Runtime and owner integration for M1.4–M1.5

Use a separate opt-in once consumer reading a coherent completed scan. Reuse the
existing attention store reader pattern. Planned command shape:
`./venv/bin/python -m trader.observability.investigation --once`.
Implemented CLI requires `--once --enable`; the watchdog invokes it only while
`data/investigation.enabled` exists. See the delivery report for tested bounds
and activation evidence. The remaining text records the original implementation contract.

Before enabling the consumer, define and test bounds for active investigations,
updates per invocation, maximum alternatives (initially three), one next action
per update, DB bytes/retention, read/write deadlines and diagnostic retention.
Use the existing capped capture scope initially; do not claim venue-wide coverage.
Persist activation time and reject pre-activation/stale/future scans. Make repeated
invocations and restarts idempotent. Record exhaustion/degradation instead of
silently dropping cases. Use separate health with freshness, last successful scan,
errors/drops and counts. A logging failure cannot hold up fast safety.

The panel should answer the result paragraph above and show source references,
version and assessment time. Display observable facts separately from proposed
explanations and clearly name unknown inputs. Keep internal implementation detail
out of ordinary owner prose; detailed diagnostics remain inspectable.

## Ordered implementation tasks

1. **M1.1 — contracts and adapter.** Freeze the prototype catalog and live target
   publication contract; implement pure typed records and snapshot conversion.
   Tests: identical prefix yields identical state; input availability and revision
   rules; source/ID round trip; malformed/missing inputs stay explicit.
2. **M1.2 — state and questions.** Implement evidence-backed market/asset state,
   trigger-relevant question families and contradictions. Tests: volume-only and
   volatility-only alerts receive relevant questions; missing participation never
   supports a participant claim; zero/unusable scales are not testable.
3. **M1.3 — continuing assessments.** Append updates and next tests; preserve all
   alternatives and frozen measurements. Tests: append distinguishing evidence
   to one of two identical prefixes and only later assessments diverge; irrelevant
   prose does not change the structured result; partial windows cannot resolve.
4. **M1.4 — bounded consumer.** Implement ledger/CLI/health, then test restart,
   repeat-scan deduplication, stale/future/pre-activation refusal, retention,
   resource limits and unreadable sources. No trading/network dependency in the
   pure core. Verify watchdog integration in isolation before demo activation.
5. **M1.5 — demonstrate and deploy.** Render the dossier; test auth/text safety,
   then coordinate any needed demo restart under CLAUDE.md. Register actual new
   investigations and verify source joins, runtime isolation and owner visibility.
   Export labelled synthetic paired cases plus actual forward-opened examples.

A task can span sessions. Commit status to the checklist only after its acceptance
is evidenced; do not turn a partial implementation into a completed milestone.

## Review and completion evidence

The M1 report must include a before/after owner walkthrough, immutable source IDs,
exact code/catalog/config versions, relevant test commands/results, measured worker
cost/latency and limits, deployment verification where applicable, and unresolved
limitations. Include at least one case where the correct next action is WAIT or
UNASSESSABLE. Verify the old trading/risk behavior remains bounded and the new
component has no admission authority.

Do not run held-out financial evaluation to validate software behavior. Keep
synthetic semantic tests, offline development examples, actual forward behavior
and later predictive-quality assessment separately labelled. M1 is complete when
its five checklist acceptances are met; predictive superiority remains M4/M7.
