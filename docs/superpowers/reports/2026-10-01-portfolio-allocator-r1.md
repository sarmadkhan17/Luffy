# LUFFY-PORTFOLIO-ALLOCATOR-R1

PASS for detached proposal infrastructure, TESTED only. Production remains
FROZEN. No runtime integration, restart, deployment, orders, strategy activation,
Risk-limit change, frontend/M4 work or commit occurred. Pre-existing workspace
changes were preserved. Claude execution probe returned “Not logged in”; work
continued directly under the current implementation request.

## Contract and behavior

`trader/portfolio/allocator.py` defines frozen typed candidate, evidence,
economics, size-bound, source, portfolio and proposal contracts. Canonical
instrument/market identity, opportunity/version/spec identity, explicit times,
validation/probation, economic provenance and comparison basis, costs, capacity,
account/Risk eligibility, evidence quality, regime/world, position interaction,
relationship evidence, expiry and source IDs/hashes are preserved. Unknown
context stays UNKNOWN. Nested mutable contract fields are refused.

Feasibility is COMPARABLE, INCOMPLETE, CONFLICTED or BLOCKED before ranking.
Required missing evidence is an explicit refusal, never a low score. Stale
portfolio/candidate evidence, invalid versions, incompatible authorities and
control state fail closed. Portfolio and Risk/control blockers are retained
also when the candidate set is empty. Cash/no-trade always exists; its yield
is unavailable, with no invented cash return.

Expected economics must already be established with provenance. Value, currency,
horizon, quantity/capital basis, estimator contract and cost basis must be
comparable. Exact Decimal comparison and opportunity/version tie-breaks give
deterministic priority. No WR/PF/confidence/Attention conversion, weighted score,
LLM, invented diversification penalty or new sizing model exists. Callers are
responsible for truthful authoritative evidence: hashes establish integrity,
not origin authenticity. The production shadow reads actual recorded sources;
it cannot populate the hypothetical complete synthetic economics fixtures.

R1 selects at most one new instrument position, conservatively avoiding aggregate
allocation of independent funding/capacity bounds. Strategies for the same
instrument/direction contribute evidence without stacking positions or summing
returns. Valid current opposing strategies conflict even if their economic
records are incomplete. Existing holdings derive SUPPORTS_EXISTING or
CONFLICTS_EXISTING from the frozen venue book; support records KEEP_EXISTING,
never a numeric resize. FLAT records NO_ACTION. Legacy plan identity remains
unavailable rather than reconstructed.

Size is the smallest of strategy-requested, Risk-permitted, account-funding,
capacity and portfolio constraints, in the same unit with each authority
established. Missing/invalid bounds or capacity prohibit a number. Proposed
numbers grant no order authority and require independent Risk approval at use.
Common-factor/relationship evidence is retained descriptively without a penalty.
A better opportunity is recorded only when comparable holding economics exist;
switching remains SWITCH_ECONOMICS_UNAVAILABLE / KEEP_EXISTING, never turnover.

Every proposal freezes its entire inputs, portfolio, cash, candidate decisions
and exact refusal reasons, economic ordering, conflicts, descriptive context,
size bounds, unresolved evidence, sources, allocator version and as-of time.
Content-addressed artifacts publish atomically without overwrites. Verification
recomputes from caller-reread current inputs; changed strategy/economics/capacity/
portfolio/relationship/Risk/source/control facts refuse reuse. Persisted-input
replay is an offline integrity check, not evidence of current freshness.

## Expected economics inspection

Inspected `strategy/spec_evidence.py`, `strategy/portfolio_evidence.py`,
`strategy/factory_handoff.py`, `strategy/capacity.py`, the Stage-5 activation
report and actual Stage-5 journal inventory. Backtest metrics/portfolio curves
and exact-version probation's realized cost-adjusted WR/PF are historical
statistics. They do not supply a validated forward comparable expected net
economic value. EXPECTED_ECONOMICS = UNAVAILABLE. No estimator was invented.
Capacity has no registered liquidity/impact model; account eligibility,
commission authority and leverage bracket evidence remain gaps. Observed depth
and funding do not establish those authorities.

## Tests and bounded real shadow

Focused allocator/shadow tests and existing portfolio-evidence tests:
`./venv/bin/python -m pytest tests/test_portfolio_allocator.py tests/test_portfolio_allocator_shadow.py tests/test_portfolio_evidence.py -q --tb=short`.
See `../evidence/portfolio-allocator-r1/tests.txt`. All 13 requested acceptance
cases are covered, plus incomparable bases, nonpositive economics, source and
identity refusal, precision independence, no turnover, empty-set blockers,
shadow read-only database preservation, missing-database refusal and refusal to
manufacture current opportunities when version inventory becomes nonempty.

Final bounded real shadow:
`../evidence/portfolio-allocator-r1/final/shadow-1790868385193.json` and its
content-addressed proposal `70db3192a8d3bcbf0f8224b474c3bcdcbd817c6fe2ae305a767db2c3b3a93071`.
A single read-only SQLite snapshot observed a fresh verified venue book with
three LONG holdings (AVAXUSDT, LINKUSDT, SOLUSDT), FROZEN control and zero frozen
strategy versions, validation/probation/approval/governor/capacity records.
The exact-version candidate set was empty. Existing decisions/research were
not fabricated into opportunities. Result: NO_ALLOCATION / CASH, reason
INSUFFICIENT_COMPARABLE_ECONOMICS. Expected economics unavailable; no relationship
model or historical entry-plan identity was invented. No authenticated request
or production mutation occurred. Earlier development shadow artifacts remain
preserved separately; the final receipt and proposal above are authoritative
for this package. Future nonempty version inventory is explicitly refused until
an authoritative current-opportunity adapter exists.

STATE marks PORTFOLIO_ALLOCATOR TESTED only. NEXT recommends the single next
observed Stage-6 gap, LUFFY-EXPECTED-NET-ECONOMICS-R1, and preserves prior frozen
M3.2 pilot/validation restrictions. This does not claim the whole Stage-6 exit,
PROVEN/ACTIVE maturity or real allocation readiness.

## Isolated recovery

Recovered onto `luffy-stage5-closure-evidence-flow-r1` at
`0b23ab3f645efe4301ceabf9399405ab60d38e56` in
`/mnt/luffy-data/luffy/workspaces/portfolio-allocator-r1-clean`, branch
`luffy-portfolio-allocator-r1`. All required repository imports exist in the
authoritative base; no dependency was transferred from the dirty production
worktree. Allocator code, scripts, tests and frozen shadow evidence retain
the reviewed bytes. Only allocator changes in STATE/NEXT were transplanted,
preserving the base's Stage-5 closure, authority fences and parked records.
The package manifest retains original reviewed-source hashes separately from
the recovered-file hashes.

Recovery checks: 38 allocator and shadow tests passed; direct imports resolve
to this worktree; shadow CLI startup, frozen proposal replay, YAML validation
and git diff --check passed. No full suite, production shadow run, runtime
data transfer, deployment, restart or main/frontend commit merge occurred.
