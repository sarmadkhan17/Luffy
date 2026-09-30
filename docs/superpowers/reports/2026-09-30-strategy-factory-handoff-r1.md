# LUFFY-STRATEGY-FACTORY-HANDOFF-R1

Date: 2026-09-30 · Branch: `strategy-factory-r1` (base `intelligence-spine-r1`
a2d44dc) · Verdict: **PASS (TESTED, NOT_DEPLOYED)** · Trading behavior changed: **NO**

## What was built

The SDD v3.2 §14.2 path from referee pass to first-live eligibility, stopping
before activation: `trader/strategy/factory_handoff.py`.

| Edge | Reused | Added |
|---|---|---|
| candidate → referee pass | `research_candidates` state + gate JSON; the error budget's `research_tests` gate1 row | `gate_evidence()` re-reads and cross-checks them |
| → immutable version | `Combination.to_spec()` + declared universe exactly as `kernel._research_handoff` builds it; `compile_spec` | `strategy_versions` (append-only, UPDATE/DELETE abort) |
| → validation receipt | same evidence | `strategy_validation_receipts`, one per version |
| → probation | config `strategies.paper_probation_trades / paper_min_winrate / paper_min_profit_factor` ("before live eligibility"); `promotion.stats_of` (extracted from `_stats_for`, behavior identical); existing paper install via `upsert_spec` | `strategy_probation_receipts` |
| → approval request | — | `strategy_approval_requests`, filed atomically with SATISFIED probation |
| → owner decision | `engine.state.OWNER_ACTORS` | `strategy_approval_decisions`, one per request |
| → eligibility | — | `eligible_for_first_live()` (read-only) |

Lifecycle log `strategy_version_events` uses SDD §14.3 vocabulary:
`VALIDATED → SHADOW → APPROVAL_REQUIRED → APPROVED_FIRST_LIVE`, plus
`REJECTED`, `DEGRADED`, `RETIRED`, and `PROPOSED` for derived edits.
`CANDIDATE` is the `research_candidates` row itself.

## Binding rules

- `version_id = sha256(schema, strategy_id, spec_hash, parent_version_id, source)`;
  `spec_hash = sha256(canonical compiled spec)`. Retry → `duplicate`; nothing is ever overwritten.
- A candidate qualifies only in `referee_passed | reason_passed | admitted`, with exactly one
  gate1 look in `research_tests` that rejected at its own alpha, candidate gate1 equal to that
  look, gate3 `passed: true`, and a rebuilt combination that reproduces the hash.
- Any other source is refused; an investigation research record gets `predictive_edge_not_established`.
- Edited spec → `derive_version()` → new version with parent lineage and **no** inherited receipt,
  probation or approval.
- Approval binds version_id, spec_hash, validation receipt, probation receipt, request time,
  decision, decision time, and actor ∈ `OWNER_ACTORS`. A second, different decision is refused.
- `eligible_for_first_live` re-verifies all of these against current storage, including
  ledger evidence, probation trades, and config policy. It fails closed if the version is
  degraded, retired or rejected, or if the spec's `data_requires` isn't asserted available.
  It writes nothing.

## Tests

`tests/test_strategy_factory_handoff.py` has **33 passed**. It covers:

- the full trace with the exact stored ids at every edge;
- spec parity with the kernel handoff;
- refusals: candidate not referee-passed (6 states), missing or inconsistent evidence (4),
  unknown source, and a real SUPPORTED `investigation_volume_anomaly` result;
- immutability and idempotency;
- a changed spec hash;
- an edit that needs a new approval;
- an approval bound to another version;
- rejection;
- non-owner actor and decision-before-request;
- degraded or retired versions;
- evidence withdrawn after approval;
- probation outcomes: no policy, not installed, installed spec differs, window, insufficient,
  not satisfied, and changed evidence or policy;
- inputs;
- read-only eligibility, byte-identical to the DB dump;
- no trading writes or callers, and `research.referee` / `research.handoff` still false.

Adjacent suites run: `test_promotion_rules`, `test_single_creation_path`, `test_research_ledger`,
`test_research_referee`, `test_compile` and `test_investigation_research_family` all pass.
`test_research_runner.py` has 18 failures that are **pre-existing at a2d44dc**: the same 18
fail with this change stashed.

## Candidate source and production

The production journal holds **0** `research_candidates` rows and **0** `research_tests` rows.
`research.referee=false` and `research.handoff=false`, so this is the recorded stop. No real
version was created. The trace runs on a fixture candidate stored exactly as research phase 3
stores one. No production DB was written.

## Gaps (next Stage-5 work)

1. **Capacity = UNAVAILABLE.** SDD Stage 5 requires a capacity estimate, and no truthful
   estimator exists. It is recorded on every version and request, and the owner sees it. This is
   the next concrete Stage-5 gap.
2. **Exact-version paper install.** `kernel._install_spec` may overwrite `timeframe`
   (`chosen_timeframe`) and set measured `regime_filter` before install. If it does, the paper
   spec's hash differs from the referee-bound version. Probation then records
   `INSTALLED_SPEC_DIFFERS` and never accrues evidence. The handoff must install the exact
   version, or version the admitted spec; that is a design decision.
3. `trades` carries no spec hash. Probation binds strategy_id plus the installed-spec hash at
   evaluation, so an edit that is later reverted within the window is not detectable.
4. Owner identity is role-level (`OWNER_ACTORS`), not per person. No authenticated gateway or
   UI calls `record_owner_decision`, which is out of scope.
5. Input availability is asserted by the caller (`available_inputs`), not measured.
6. Automatic degradation/retirement of versions is recordable (`retire_version`) but not wired
   to `analyst.review_deployed`.

Stage 5 is **not complete**. Gaps 1, 2 and 6 remain.
