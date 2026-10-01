# LUFFY-OPPORTUNITY-CONTEXT-LIVE-INTEGRATION-R1

PASS: existing Opportunity Context is reused in a tested, detached live-evidence
Stage-6 integration. No commit, deployment, restart, recurring activation or
trading/Risk/control change. Stage 6 remains incomplete. Real expected economics
remain INSUFFICIENT_EVIDENCE.

Base: `luffy-expected-net-economics-r1`,
`b471b9e2170c8c5d1da3f3b5e06bc25e58613a65`. Review worktree:
`/mnt/luffy-data/luffy/workspaces/opportunity-context-live-integration-r1`.
The shared production workspace and unrelated knowledge/frontend/runtime changes
were preserved. The required local Claude execution probe returned “Not logged
in”; implementation proceeded under this session's explicit work request.

## Existing contract and producer

`trader/cognition/opportunity_context.py` is unchanged, byte for byte from the
base. Its existing `build` and `OpportunityContext.from_json` remain the owners
of opportunity-context.v1 semantics. No v2 or schema incompatibility was needed.

`trader/portfolio/opportunity_live.py` is a pure deterministic producer. A separate
immutable source-input receipt retains exact upstream payloads, source IDs/hashes,
availability and reader freshness boundaries, references the unchanged v1 object,
and binds exact-version/portfolio/economic references absent from v1. It does not
put direction, sizing, probability, scores or expected return into v1. Missing
sources and eligibility remain UNKNOWN. Historical economics receipts, when
provided, are verified references; their values are never context-derived EV.

Supplied Attention, selection, allocation, investigation/update and WorldModel
records pass their existing verifiers. WorldModel must contain this instrument
and match the Attention scan cut (or the context cut when no scan is supplied).
The context cut is the time all input evidence is known, not a claim that every
upstream observation occurred simultaneously. Old optional evidence is retained
as a hashed source with explicit stale/unknown status; stale required evidence
refuses construction and current reuse. Future observations, future availability,
wrong instrument, signal/version horizon or direction mismatch, altered source
objects and incompatible declared universe refuse construction.

Version identity and spec hashes replay through `factory_handoff.load_version`;
signal fingerprint, strategy identity, horizon, market and declared universe
must match. Frozen validation/probation/install/capacity/event inventories are
references, not eligibility assertions. This package does not promote their
mere presence into authority. Missing authoritative eligibility, feasibility,
expiry and sizing bridges remain UNKNOWN and have no size bounds.

Portfolio input is the existing verified venue-position-snapshot.v1 with its
embedded portfolio observation, including a checked projection of positions,
environment and clocks. Journal holdings are never venue truth. Fresh evidence
records SUPPORTS_EXISTING, CONFLICTS_EXISTING or NO_ACTION descriptively, with
no resize, close or switch operation.

## Registry and downstream chain

`opportunity_registry.py` implements SDD §15.7 as one reference-only table in
caller-owned shadow storage. It references existing cycles, candidates,
investigation and context IDs. It is not an evidence trace or an episode model.

Only one identical recorded signal occurrence plus instrument and exact frozen
version can prove a repeated setup. A new bar, action, horizon, fingerprint or
version creates a different opportunity. Without that proof, cycle/candidate
identity separates records. Repeated observations update the same lineage.
Explicit evidence-referenced TRADED/SKIPPED/EXPIRED/INVALIDATED resolutions are
terminal and immutable; observing evidence never automatically resolves a record.

Economics consumes the frozen v1 context and its exact producer source receipt.
Methods can declare `requires_opportunity_context`; missing/refused context
keeps economic value UNAVAILABLE. Context adds no EV estimator, reserve or cost
formula. Current verification respects required context-source expiry.

Canonical candidate creation reads only that frozen producer receipt and its
verified Economics Receipt. It does not recollect mutable evidence. Candidate
and proposal diagnostics retain exact context_id, and the allocator reconstructs
the candidate from its frozen producer receipt before accepting the economic
binding. Independent candidate fields cannot be silently substituted. Existing
legacy candidate contracts remain supported. No candidate grants trading or
Risk authority; the independent control and feasibility gates remain in force.

## Real shadow and replay

`scripts/opportunity_context_shadow.py` reads production SQLite using mode=ro,
query_only, coherent per-database transactions, a five-second SQL progress deadline,
fixed row/payload bounds and a four-context run limit. It makes no network or
venue request and never constructs Journal or creates a missing source database.
The context cut is captured after the source reads. Artifacts/registry are refused
inside the live source directories. Different stores are not represented as an
atomic cross-database snapshot; exact retained payloads and availability are bound.

Final receipt and restart audit are under
`../evidence/opportunity-context-live-integration-r1/real-shadow-final/` and
`../evidence/opportunity-context-live-integration-r1/restart-replay.json`.
Four observable Attention contexts and four separate scan/candidate registry
records were produced. Zero frozen StrategyVersions and zero strategy candidates
were observed/created. Real economic receipts cannot be produced without exact
strategy identity; economics remains UNAVAILABLE. Allocator returned
CASH / NO_ALLOCATION / INSUFFICIENT_COMPARABLE_ECONOMICS. The current venue
snapshot was fresh and contained three holdings. Missing WorldModel/strategy/
economics/eligible-version evidence was not fabricated.

Same persisted source inputs rebuild identical v1 context IDs and producer
contents in a new process. Publication verifies content-addressed filenames and
canonical bytes; source objects are reverified and missing/changed sources refuse
reuse. Archive replay is integrity evidence, not proof that historical inputs
are still current: current use requires reread trusted sources and an explicit
clock. Hashes do not authenticate venue origin.

## Validation and remaining implementation gap

Focused suites include the new live-integration tests and existing context,
context persistence, economics/inventory and allocator/shadow tests. Saved output:
`../evidence/opportunity-context-live-integration-r1/tests.txt`.
The suites cover v1 compatibility, source clocks, future leakage, WorldModel cuts,
instruments, exact registry grouping/version separation, terminal resolutions,
context/economics/candidate binding, tampering and missing evidence, current
expiry, unavailable economics and CASH, venue support/conflict/no-action, source
SQL write denial and restart replay. Positive arithmetic is TEST-ONLY and does
not establish real calibration or eligibility. Whitespace and STATE/NEXT YAML
checks passed. Graphify updated the engineering graph using code-only AST
extraction with no semantic extraction or API cost; graph files are not package
files.

STATE records OPPORTUNITY_CONTEXT=TESTED_LIVE_INTEGRATION,
OPPORTUNITY_REGISTRY=TESTED, PORTFOLIO_ALLOCATOR=TESTED and
REAL_EXPECTED_ECONOMICS=INSUFFICIENT_EVIDENCE. No Stage-6 completion claim.

NEXT recommends LUFFY-STAGE6-CANDIDATE-FEASIBILITY-BRIDGE-R1: integrate independently
verified existing exact-version eligibility and account/Risk/capacity/size
constraints into canonical frozen candidate construction. These are actual
remaining implementation bridges. Real forward gross calibration, a calibrated
economic reserve, forward cost applicability and authoritative account/capacity
sources remain separate evidence requirements; no further economics formula or
micro-package is recommended. Recurring production operation remains undeployed.
