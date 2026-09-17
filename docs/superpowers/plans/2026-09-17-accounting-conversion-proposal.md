# Proposed next M8.1 slice: explicit conversion evidence

Status: **OWNER APPROVED** in the current session. Manual evidence capture/replay
implemented; accepting conversion accounting remains blocked by missing provider
attribution. See [delivery](../reports/2026-09-17-conversion-evidence-delivery.md).
Runtime accounting policy remains unchanged.

## Problem and proposed boundary

Whole-trade accounting already retains native fee/funding subtotals, but refuses
scalar USDT net when a cashflow currency differs from USDT. The next bounded
slice would support explicit actual-conversion evidence. A market-price lookup
would not establish an actual conversion and would not satisfy this contract.

A conversion record must identify the original fill commission or funding income,
its immutable source version, native amount/currency, conversion transaction and
venue/account scope, actual USDT debit/credit, event and observation times, and
raw evidence needed for source-free replay. Evidence must prove attribution and
prevent reuse across cashflows; a matching amount/time alone is insufficient.
No conversion order would be placed by this accounting feature.

Provider capability and exact attribution fields must be verified before accepting
any provider record. If the venue cannot establish attribution, keep the existing
unknown/retry result; do not present a manually asserted mapping as verified.
Historical acquisition cost and spot valuation are separate accounting policies
and are outside this proposed slice.

## Proposed implementation boundaries

- Version the extended whole-trade artifact; retain unchanged v1 replay semantics
  and all frozen captures. Keep native and converted amounts separately visible.
- Add a bounded evidence adapter and strict deterministic verifier. Validate
  identity, currency, signs, quantities, clocks, source integrity, complete
  conversion costs, and one-to-one attribution; reject conflicts and duplicates.
- Integrate only into new manual captures initially. Missing conversion evidence
  keeps net unknown and learning ineligible. No automatic import or consumer
  migration; scheduled worker behavior changes require explicit reviewed scope.
- Adapt verified outcome construction without relabeling native commission as
  USDT or silently losing the raw conversion evidence.

Relevant existing paths: `trader/engine/trade_accounting.py`,
`scripts/reconcile_trade_accounting.py`, `trader/cognition/outcomes.py`,
`tests/test_whole_trade_accounting.py`, `tests/test_typed_outcomes.py`.

## Acceptance before deployment

Synthetic tests must cover positive/negative funding, fees, conversion costs,
multiple native currencies, exact arithmetic, missing/partial evidence, reused
conversion IDs, conflicting versions, wrong account/venue/currency, future
availability and tampering. Existing USDT-only fixtures and frozen v1 capture
replay must remain unchanged. Explicit typed-memory import must retain forward
activation and terminal-conflict refusal. Synthetic results are not demo-observed
accounting or profitability evidence.

## Preserved controls and next action

No kernel/risk changes, no forced trades, no research restart, no prospective
population backfill, no modification to frozen artifacts or missing-data retries.
Natural booking observation and the scheduled maturity/full-window reviews remain
as recorded in `docs/NEXT_SESSION.md`. Milestone acceptance stays 12/55.

Approval received; provider capability checked and the supported bounded
evidence-capture path implemented. No repeat approval is needed for this design. If attribution is unavailable,
report the concrete limitation before proposing a different valuation policy.
