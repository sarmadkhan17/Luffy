# Handoff — owner end goal now governs

Date: 2026-09-16. Purpose: point the next session at the right objective.

## Latest implementation update

The first forward attention/diagnostics slice is now implemented and tested,
**disabled and not deployed**. Continue from
[implementation and rollout handoff](2026-09-16-attention-implementation.md).
That report supersedes the earlier "neither implemented" status below; the
hypothesis/outcome loop remains unimplemented. The owner end goal still governs.

## What changed

The owner restated Luffy's end goal in full: a complete autonomous trader
("Jarvis specialized in trading") that continuously understands markets,
allocates attention, forms and tests competing hypotheses, chooses the best
expression (long, short, pairs, hedge, cash), executes safely under
deterministic risk control, learns from outcomes including non-trades, and
eventually covers its own operating costs without ever forcing trades.

Authoritative record:
[`docs/superpowers/specs/2026-09-16-luffy-end-goal.md`](../specs/2026-09-16-luffy-end-goal.md).

**This supersedes the narrower earlier framing** of Luffy as a crypto-futures
strategy discovery/evaluation system. Crypto futures on demo remains the
current implementation scope; it is no longer the statement of identity.

**Gate 2 is a subordinate research task**, not the goal. Do not reflexively
resume bootstrap or evidence-only work, or treat passing a gate as the
objective, simply because that was the last active thread.

## No status claims

Nothing in the spec is implemented by virtue of being written there. This
documentation change implements **no runtime capability and no logging**. Treat
the spec as requirements.

## Status of the capability assessment

Item 1 below is **done as a source audit** and its findings are recorded in
[`2026-09-16-luffy-capability-audit.md`](2026-09-16-luffy-capability-audit.md)
(requirement matrix §1–24, logging/diagnostics inventory, ranked bottlenecks,
and a bounded next slice).

Its standing and its limits, precisely:

- It is a **read-only inspection of `trader/` and `tests/`**. 89 tests were
  executed (73 cognition + 16 creation/budget), all passing.
- **No deployed state was verified.** No trading service, production database,
  log, ledger, artifact or market data was inspected. Some existing test helpers
  load repository configuration/environment; tests use temporary journals and
  mocks, and no credentials were displayed. Which cfg-gated
  branches are actually live — notably `scouts.strategy_leads` and
  `require_strategy_signal` (respectively score ownership and the independent
  direction gate) — is **unverified** and must be established separately before any
  behavioural claim about the deployed system.
- Every "wired"/"reachable" statement in it means a code path exists, not that
  it runs.

## Next bounded work

**One observable forward slice: shadow attention capture plus typed diagnostic
cause recording, with an owner-visible view — trading behaviour isolated.**

Concretely, and in this order:

- **Milestone 1 — foundation and attention.** Capture, off the trading path,
  what the scan actually looked at and what happened to each candidate: forward
  `first_seen`/provenance (never backdated, never taken from bar close times),
  selected *and* not-selected rows, typed causes distinguishing ineligible,
  evaluated-no-signal, missing-data and evaluation-failed, a scope label stating
  the selection is a strategy/volume-selected subset rather than the venue-wide
  universe, and both an API and a rendered dashboard panel. Default off. No
  trading semantics change; no attention influences any trade.
- **Milestone 2 — hypothesis and outcome.** Only then, competing hypotheses with
  falsifiers and deadlines, built on the existing `cognition.contracts`
  templates, joined to honestly collected outcomes.

The full end-to-end chain the spec asks for is large; it is deliberately split
so that neither milestone is claimed to deliver the whole cognition loop.
**Neither is implemented.** Milestone 1 is a design in the audit, not code.

Explicitly **not** the next step: standing up ~15 disconnected agents. The
component list in the spec is a set of conceptual domains, not a build order.

## Authorization and expectations

- The owner authorized broad research, design and architecture/process changes
  toward this goal.
- The owner must stay in the loop and be able to tell what Luffy is doing and
  why.
- Comprehensive troubleshooting logging is required (see the acceptance
  contract in the spec).
- **No new live-money launch and no spending commitment was specified.** The
  current demo scope and existing risk controls stand unchanged, along with the
  recorded research stop and frozen artifacts.

## Note

An earlier report exists:
[`2026-09-16-gate2-assumptions-first-design.md`](2026-09-16-gate2-assumptions-first-design.md).
It predates the owner's full restatement, so the owner spec and this handoff —
not that report — govern the objective and priorities. It remains a conditional
statistical reference where its Gate 2 analysis is relevant; do not discard
useful prior evidence.

The audit and 89 tests were completed by Claude. Claude reached its session
limit during final revision; the coordinator disclosed the fallback and finished
the review corrections directly under the owner's process-change authorization.
No runtime implementation or deployment occurred in this assessment.
