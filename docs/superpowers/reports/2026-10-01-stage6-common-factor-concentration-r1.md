# LUFFY-STAGE6-PORTFOLIO-COMMON-FACTOR-CONCENTRATION-AUTHORITY-R1

Verdict: PASS for the deterministic context and constraint contract. This is
not calibrated portfolio optimization or Stage-6 completion. No commit, order,
resizing, activation, deployment, restart or production Risk/control change.

Base: latest committed Stage-6 candidate chain,
`6bb99e541e9d9ce583cc4a283f7db685f9235b96`.
Worktree: `/mnt/luffy-data/luffy/workspaces/stage6-common-factor-concentration-r1`.
Branch: `luffy-stage6-portfolio-common-factor-concentration-authority-r1`.
Local Claude execution returned “Not logged in”; implementation proceeded
under the owner's explicit package request. Production sources were read only.

## Venue exposure and source authority

`trader/portfolio/common_factor.py` builds a content-addressed immutable receipt
from the existing venue-position-snapshot.v1. It replays the original observation
producer, verifies both identities, checks complete one-way book projection,
canonical instrument/market/side/quantity and the existing freshness bound.
The allocator's portfolio projection must match that exact venue book.
Journal records are never substituted. Changed, future, incomplete, malformed,
stale or mismatched venue evidence refuses construction/current reuse.

Each holding retains canonical instrument, side, quantity, market type, observed
clock, provenance, completeness and freshness. The retained producer has entry
price, but no current mark or proven StrategyVersion attribution. Quantity times
that exact entry price is labeled **entry-basis notional**, in USDT; current
marked notional remains null. Strategy/version attribution remains UNKNOWN.
Entry basis does not supply margin usage, Risk heat or a current size bound.

## Exact redundancy and lineage

Same instrument/direction is EXACT_REDUNDANT: one expression, no stacking.
Opposite direction on the same instrument is a recorded conflict. Exact
Factory parent chains are read/reverified through existing load_version with
bounded queries and a 64-version traversal bound. Missing parents, cycles,
identity/spec changes, future records or stale reader authority refuse replay.
The recorded query answers permit archive reconstruction; trusted current
readers must supply the reread sources for current use. Hashes are integrity,
not authentication. No names/text similarity or statistical cutoff is used.

Shared roots are SHARED_LINEAGE and EXACT_SHARED_EXPOSURE. Distinct proven
roots remain distinct lineage. Different instruments without exact lineage or
measurement have UNKNOWN common-factor relationship; DISTINCT redundancy means
only that deterministic duplicate identity was not established.

Duplicate confidence context records members and one representative per exact
root. `shared_lineage_count` counts extra variants within those roots (three
siblings: two extra variants, one evidence group). Unknown lineage cannot vote.
`independent_evidence_count` is null with NOT_ESTABLISHED status, including for
distinct roots: distinct lineage does not prove statistical independence.
The allocator deduplicates selected-expression evidence contributors and never
sums votes, confidence or position sizes. Original candidate rows remain visible.

## Measured relationships and common-factor result

WorldModel records replay through the existing WorldModelRecord verifier. Each
relationship retains exact endpoints/scopes, kind, horizon, cut, value, method,
source/evidence identity, original observation windows and reader expiry.
Expired observations or explicitly STALE evidence are refused. Missing quality
or freshness authority remains UNKNOWN. Noncanonical endpoint names remain
scope context; they are not guessed into canonical instruments.

The existing Attention correlation adapter reconstructs joined scan inputs
through investigation.adapt and retains its original baseline/recent windows,
method, values and frozen reference membership. Its correlation is to a
leave-one-out peer basket, never a fabricated pairwise correlation between
holdings. Canonical own-instrument identity is projected only from the verified
venue book. No new correlation calculation, threshold, PCA/ML or classifier.

Typed results are EXACT_SHARED_EXPOSURE, MEASURED_RELATIONSHIP,
NO_ESTABLISHED_RELATIONSHIP and UNKNOWN. Measurements are CONTEXT_ONLY; no
haircut, diversification bonus, penalty, covariance optimizer or portfolio score
exists. FACTOR_MODEL = NOT_JUSTIFIED: no registered calibrated factor authority
or validation evidence was supplied.

## Concentration and allocator boundary

Only existing configured owner limits are considered. Current open-position
count is measured from the complete venue book and checked against
`risk.max_open_positions`; prospective additions require actual headroom.
A count above that limit is EXCEEDS_EXISTING_LIMIT. A missing configured policy
is UNAVAILABLE. No absent policy limit is invented.

Configured heat, per-symbol risk and position/total margin limits are retained,
but their venue-bound metrics are unavailable in the retained snapshot.
Their checks and aggregate concentration remain UNAVAILABLE; known violations
take precedence. Neither entry notional nor journal stops are promoted into
current heat/margin truth. Risk remains the independent final authority.
Unavailable concentration blocks new proposals from this context.

Existing exact bridge candidates require the exposure receipt; omitting it
blocks allocation. A supplied receipt is recomputed against the current input
candidate set, venue projection, policy, lineage and relationship sources before
use. Tampering or changed current sources blocks proposals. Legacy contracts
remain readable; the detached current shadow uses the new context explicitly.
Single-expression, opposing-direction, existing-plan/no-resize, economics,
capacity and control gates remain in force. Context never supplies expected EV.

## Final real shadow

Final audit:
`../evidence/stage6-common-factor-concentration-r1/final-shadow/shadow-1790876551459.json`.
New-process replay:
`../evidence/stage6-common-factor-concentration-r1/restart-replay.json`.

The bounded read-only shadow observed three LONG venue holdings: AVAXUSDT,
LINKUSDT and SOLUSDT. Venue snapshot cut: `1790876465680`; consumer cut:
`1790876551459`. Four current opportunity contexts, zero frozen StrategyVersions,
zero actual candidates and FROZEN control. No candidates were fabricated.
Entry-basis gross/long notional was `1741.6690300000000577 USDT` and entry-basis
short notional was zero; these are **not current marked exposure**.

All three pair relationships are UNKNOWN, with no exact strategy attribution
or current relationship measurements. The scan carried no correlation input
or WorldModel relationship edges. Common LONG direction is descriptive and
never becomes SAME_BET. Position count is WITHIN_EXISTING_LIMITS (3 of 8);
aggregate concentration is UNAVAILABLE because heat/margin truth is missing.
Allocator retained CASH / NO_ALLOCATION / INSUFFICIENT_COMPARABLE_ECONOMICS.
Zero authenticated requests and production mutations. Archive replay and a
fresh Python process produced the identical proposal ID. That replay proves
frozen-input integrity, not continued freshness or real candidate maturity.

Readers use mode=ro, query_only, bounded rows/payloads and SQL deadlines. Outputs
are refused within production source directories. Separate source stores are
not represented as an atomic cross-database cut; their captured source clocks
and records remain explicit. Changed scan/version inventory is refused.

## Validation and next package

207 focused tests passed (28.96 seconds). Saved validation is in
`../evidence/stage6-common-factor-concentration-r1/tests.txt`.
It covers the requested duplicate/opposing expressions, sibling and distinct
lineage, unknown independence, correlation/beta retention without thresholds,
existing owner-count constraints, missing/stale/tampered/current-source refusal,
CASH with unavailable economics, identical/new-process replay, SQL write denial,
source/control preservation and no mutation calls. Synthetic positive economics
and synthetic policies are labeled TEST-ONLY and prove no real calibration.
STATE/NEXT YAML and whitespace checks pass. Graphify receives only its code AST
update; graph output is excluded from package scope.

STATE records PORTFOLIO_COMMON_FACTOR_CONTEXT, EXACT_REDUNDANCY_AUTHORITY and
CONCENTRATION_BOUNDARY as TESTED. The single next recommendation is
STAGE6_IMPLEMENTATION_CLOSURE_R1: consolidate the SDD Stage-6 BUILD inventory and
trace every implemented source/authority/refusal path before claiming closure.
This package does not establish another numerical model requirement. Missing
forward calibration, actual costs, account/capacity maturity, marked/stop/margin
source truth and proven independence remain explicit evidence/authority gaps.
The closure audit must identify any concrete additional BUILD gap it finds;
Stage 6 and calibrated portfolio optimization are not marked complete here.
