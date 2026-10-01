# LUFFY-EXPECTED-NET-ECONOMICS-R1

PASS for TESTED_CONTRACT. REAL_ECONOMIC_VALUE = UNAVAILABLE;
ECONOMIC_MODEL_STATUS = INSUFFICIENT_EVIDENCE. Stage 6 remains incomplete.
No trading behavior, Risk/control authority, orders, activation, commit,
deployment or restart changed.

The requested base `luffy-portfolio-allocator-r1` / `a4432ba244a474ec48f7a9f0cc59fb6f7833aa61`
exists ("Establish truthful portfolio allocation proposals"). The shared
workspace was already dirty, including detached allocator/cost infrastructure
and STATE/NEXT. Work used that existing infrastructure without checkout/reset
or alteration of unrelated changes. Local Claude execution was attempted and
returned "Not logged in"; implementation continued under the current request.

## Receipt and comparison

`trader/portfolio/economics.py` freezes opportunity, exact strategy/version/spec,
canonical instrument/market, direction, horizon, as-of, context, unit,
quantity/capital basis, horizon interpretation, cost/uncertainty treatment and
freshness semantics. Six components retain status, value or null, units,
provenance, source IDs/hashes, method/version, timestamp/authority expiry,
freshness semantics and limitations. Unknown values never become zero.
Only proven NOT_APPLICABLE cost components contribute a zero to subtraction;
their component value remains null. Funding/borrow additionally requires proven
non-borrowing applicability; borrowed-cost economics are unavailable until the
shared protocol and an authoritative bridge can establish them.

Economic statuses are POSITIVE, NON_POSITIVE, UNAVAILABLE, STALE or
INCOMPATIBLE_CONTEXT. Net is established only when every required component is
established or a cost is proven not applicable. Positive means exact net > 0;
zero and negative mean NON_POSITIVE. Decimal arithmetic uses sufficient local
precision; no confidence, reliability, evidence-quality or regime multiplier
exists. Context remains frozen descriptive evidence.

Comparison requires complete current verified receipts with the same units,
quantity/capital basis, horizon and interpretation, cost treatment, uncertainty
treatment, freshness semantics and exact as-of. This deliberately conservative
as-of rule refuses cross-clock comparisons rather than inventing normalization.
Incompatible or incomplete records return INCOMPARABLE. Neither receipt building
nor comparison chooses trades or sizes positions.

Every artifact is content-addressed and append-only, atomically published outside
production storage. Same frozen inputs replay identically, including across a
new process with the same code-owned adapters. Verification recomputes from
caller-reread current evidence and an explicit current clock; stale, changed or
tampered strategy, gross, cost, reserve, horizon, context and timestamps refuse
reuse. Hashes provide integrity, not venue authenticity. Trusted source readers
and registered authority/calibration validators remain mandatory. Persisted
embedded evidence permits offline replay, not proof that evidence is current.

## Gross, reserve and cost evidence inspection

Inspected `strategy/spec_evidence.py`, `strategy/portfolio_evidence.py`,
`strategy/factory_handoff.py`, `research/referee.py`, `research/evaluate.py`,
`observability/execution_calibration.py` and `engine/paper_cost_evidence.py`.
Walk-forward PF/statistics and historical portfolio curves do not define a
conditional forward expectation. Referee gates test admission hypotheses;
PASS is not an economic estimator. Probation's cost-adjusted WR/PF and realized
P&L assess observed trades, not calibrated forward opportunity economics.
Execution calibration explicitly states no validated cost model. No existing
calibrated economic uncertainty-reserve mapping was found.

Production gross/reserve/forward-cost-scope model registries are empty. No
return or haircut was invented. A future registered gross/reserve method must
prove actual forward/conditional calibration, exact context/version/horizon,
evidence/sample period available before capture, economic units, method/version,
authoritative freshness, limitations and cost exclusion/baseline. Reserve
sources additionally bind the gross and cost evidence hashes. The production
code cannot use the synthetic adapters; those live only in test fixtures.

Costs replay through `paper_cost_evidence.verify`, including source validators,
identity binding, both legs, executable commission notional, slippage baseline
and funding-event applicability. Cost calculations were not copied or changed.
A historical realized cost receipt is not a future cost forecast. It requires a
code-owned calibrated forward scope bridge proving applicability to the current
opportunity, gross baseline, quantity, units, environment and horizon. No such
production bridge exists. Signed cost values are preserved. A missing cost or
reserve prevents established net value.

## Allocator integration

The existing detached Economics seam now carries the canonical receipt. A
supplied scalar or ESTABLISHED flag alone is refused. The allocator replays the
receipt, checks current expiry, exact candidate identity/context, matching
projection and matching frozen receipt source. Valid complete comparable
receipts alone enter ranking; incomparable complete candidates leave cash
selected. Existing feasibility, sizing, Risk final authority and control gates
remain in place. Holding opportunity-cost observations also require current
verified complete receipts; no resizing or switching authority is added.
This is detached seam integration; the production trading loop is untouched.

## Actual evidence and remaining requirements

Final read-only inventory:
`../evidence/expected-net-economics-r1/final/real-evidence-1790871096535.json`.
It records source-set and inspected-file hashes, zero frozen versions,
validation/probation/capacity receipts, research candidates/tests, versioned cost
receipts/sources, zero established current expected-net receipts and FROZEN
control. No opportunities were fabricated.

Actual detached allocator shadow:
`../evidence/expected-net-economics-r1/allocator-shadow/shadow-1790870817352.json`,
proposal `e624b352563938abde5ed90062456df4b385b048c6e473f662c97d2b96720701`.
Three holdings remained in the observed book; the portfolio observation was
stale. Shadow records BLOCKED / PORTFOLIO_EVIDENCE_STALE and retains
CASH / NO_ALLOCATION / INSUFFICIENT_COMPARABLE_ECONOMICS, with replay PASS,
zero candidates, zero authenticated requests and zero production mutations.
Staleness is an additional live-allocation blocker, not failure of this tested
contract. No current opportunity can truthfully establish expected net value.

Required real evidence:

- Exact-version conditional forward outcome distribution or equivalent validated
  estimator, with horizon/regime, sample period, economic gross units,
  cost exclusion/baseline and independently measured calibration.
- Calibrated economic reserve with measured error/coverage, method/version,
  sample period, exact context and gross/cost source binding.
- Commission authority, validated forward execution/slippage, funding/borrow
  applicability and calibrated scope bridge to verified shared cost receipts.
- Authoritative event-driven exact-version opportunity registry and frozen
  Opportunity Context; fresh portfolio/account/Risk/capacity authorities remain
  separate allocator requirements.

No further economics formula or micro-package is recommended. NEXT identifies
LUFFY-OPPORTUNITY-CONTEXT-R1, the actual missing event-driven opportunity set and
frozen context (SDD §15.7 and Stage 6). This report records requirements without
authorizing research evaluation, calibration collection, trading or deployment.

## Validation and changed files

Focused suites: `test_expected_net_economics.py`,
`test_expected_net_economics_inventory.py`, `test_portfolio_allocator.py`,
`test_portfolio_allocator_shadow.py`, `test_portfolio_evidence.py`.
Final result: 92 passed in 4.83s. See
`../evidence/expected-net-economics-r1/tests.txt` for the saved output.
Coverage includes all requested cases, missing costs/borrow/reserve, exact
TEST-ONLY arithmetic, independent precision, incompatible horizon/treatment/
freshness semantics, stale and changed-context refusal, inner cost replay
against a rehashed forgery, immutable publication/restart replay, descriptive
quality context, verified-only allocator ranking, truthful unavailable receipts
and CASH, read-only DB preservation and no execution/Risk/control imports.

Package files: `trader/portfolio/economics.py`,
`trader/portfolio/allocator.py`, `scripts/expected_net_economics_inventory.py`,
`tests/economics_fixtures.py`, `tests/test_expected_net_economics.py`,
`tests/test_expected_net_economics_inventory.py`,
`tests/test_portfolio_allocator.py`, this report, package evidence, STATE.yaml
and NEXT.yaml. Existing unrelated files were preserved. Graphify code-only
update completed without semantic extraction or API cost. `git diff --check`,
package-file whitespace checks (including untracked files) and STATE/NEXT YAML
parsing passed. Earlier inventory artifacts are preserved; the final audit
linked above records hashes of the final implementation.
