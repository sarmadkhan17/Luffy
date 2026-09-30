# LUFFY-EXACT-VERSION-PAPER-PROBATION-R1

Date: 2026-09-30 · Branch: `strategy-factory-r1` (base bc16c9b) ·
Verdict: **PASS (TESTED, NOT_DEPLOYED)** · Trading behavior changed: **NO**

Owner decision: **the frozen StrategyVersion is authority.**

## Exact paper install

`kernel._research_handoff` now:

1. freezes the candidate as a StrategyVersion (`factory_handoff.create_version`);
2. passes a **copy** of the frozen spec to `analyst.admit`;
3. installs **that exact spec** through `_install_spec(..., version_id=)` → `_install_version`.

The handoff builds no spec of its own any more; `factory_handoff.candidate_spec` is the only
construction.

`_install_version` never rewrites a field that contributes to `spec_hash`:

- **timeframe:** if admission picks a different timeframe, the version is refused
  (`spec_install_refused`, `timeframe_differs_from_version`) and the candidate is marked refused.
  It is never refitted.
- **regime_filter:** `set_measured_regimes` runs on a copy. It would also have written
  `provenance.regime_evidence`. The measured result is recorded as
  `regimes_advisory_not_applied` and never applied.
- **anything else:** a spec that is not the frozen one is refused.

After `Journal.upsert_spec`, `record_exact_install` reads the row back exactly as the running
population loads it. The row's compiled spec must be canonically identical to the frozen spec
(same bytes, so the same hash). If it isn't, the result is `INSTALLED_SPEC_DIFFERS`: probation
does not start and the Kernel retires the row.

The table `strategy_version_installs` is append-only. It binds:

- strategy_id and version_id;
- spec_hash and the exact installed spec;
- installed_at_ms and mode = paper.

Unversioned installs are unchanged: strategist-admitted specs keep the existing timeframe/regime
install behavior, because they have no frozen version.

## Probation binding

- Probation starts only at the exact install: `VALIDATED → SHADOW`, with `ref_id` set to the
  install_id and `since_ms` set to installed_at_ms.
- A receipt binds version_id, spec_hash, install_id and the probation window.
- If the installed spec is later replaced, probation stops advancing (`INSTALLED_SPEC_DIFFERS`).
- The policy is unchanged: config `paper_probation_trades` 15, `paper_min_winrate` 0.40 and
  `paper_min_profit_factor` 1.15.

## Trade version identity

This package adds no parallel schema. Trade Provenance (`trade-provenance-r1`, 54aefc5) already
defines per-trade version identity. Its `trades.entry_identity_json` follows
`trade-entry-identity.v1`, and its `spec_sha256` hashes the compiled spec the evaluator ran with
the same canonical JSON as `spec_hash`. Probation reads that field only:

| Trade identity | Probation |
|---|---|
| VERIFIED, kind `spec`, same strategy_id, `spec_sha256 == spec_hash` | counted |
| VERIFIED, other `spec_sha256` (parent, derived, other version) | excluded `other_version` |
| NULL / UNKNOWN / AMBIGUOUS / malformed, same strategy_id in window | `INCOMPLETE_TRADE_IDENTITY` (fail closed) |
| column absent | `TRADE_IDENTITY_UNAVAILABLE` |

**Integration gap:** Trade Provenance is not on this branch. Until it is integrated, no trade
carries identity, so any real probation reports `TRADE_IDENTITY_UNAVAILABLE`. The tests add the
column in Provenance's shape.

## Version change and eligibility

A derived version B does not inherit anything from A:

- B has no install, probation, request or approval.
- B cannot be installed without its own validation.
- Trades recorded under B's hash are never counted for A.
- A's probation receipt still verifies after B replaces A in paper. From then on A fails
  eligibility with `installed_version_differs`.

`eligible_for_first_live` now also requires the exact install record, and requires that the spec
currently installed is still that version. It still grants eligibility only.

## Tests

- `tests/test_strategy_factory_handoff.py`: **51 passed**. New tests cover:
  - byte/hash-identical install;
  - modified installs refused (timeframe, regime_filter, logic, provenance);
  - nothing installed;
  - Kernel exact install, where the regime filter is not replaced;
  - Kernel timeframe refusal;
  - Kernel refusal of a non-frozen spec;
  - legacy unversioned install unchanged;
  - probation needs an exact install, and stops when the install is replaced;
  - exact vs other-version trades;
  - unattributed trades give INCOMPLETE (4 variants);
  - a missing identity column gives UNAVAILABLE;
  - derived version inherits nothing;
  - eligibility requires the version to still be installed;
  - the Kernel reaches the factory only from `_research_handoff` / `_install_version`.
- `tests/test_single_creation_path.py`: the handoff fixture now carries registered gate evidence.
  A new test shows that a `reason_passed` candidate without that evidence is refused.

Capacity is still **UNAVAILABLE**. No real probation has run: production has 0 candidates, and
`research.referee` / `research.handoff` are false. Stage 5 is **not complete**.
