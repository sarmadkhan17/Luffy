# M3.2 evidence machinery corrections — September 19, 2026

Bounded offline implementation. No M3.2 search, collection activation, venue call,
memory import, runtime restart, Gate 2, research-flag, trading, risk or exit change.
Existing untracked M3.2 code and unrelated worktree changes were preserved.

## Defects corrected

- `m32_evidence.independence_groups` merges original groups, market-wide padded
  spans, shared underlying/case/input identities and sequence/parent links across
  windows. Administrative period names cannot add groups. Duplicate rows cannot
  inflate counts; conflicting duplicates, absent parents and missing clocks fail
  closed. Optional period bounds widen dependencies only. Output counts are upper
  bounds, not estimated effective sample size or independence certificates.
- Regime coverage counts distinct labels only after validating a
  `pit-regime-receipt.v1` with registration/recording clocks, classifier version,
  full closed input-bar versions and receipt digest. Bare, late, tampered or
  observation-only labels remain unknown. No regimes were backfilled. This adds
  validation only; no collector was modified to emit regimes.
- `_complete_actual` uses canonical typed-outcome replay, including complete
  whole-trade fill/fee/funding/ownership evidence. The PIT dataset adapter now
  supports `source.whole_trade_capture` and preserves its source for replay.
- Accounting counts are joined by venue/environment/symbol/trade and registration
  clock to eligible PIT execution episodes, cut-filtered using local import and
  availability clocks, and deduplicated across embedded receipts, typed stores,
  accounting stores and sequence copies. Conflicting receipt versions refuse the
  trade. Diagnostic captures are excluded. Unknown original labels remain unknown;
  the audit never modifies the historical artifact or imports a receipt.
- Reading `execution_accounting` now requires a canonical record envelope and
  explicit local import clock. Partial legacy accounting and booking legs remain
  insufficient. No clock is inferred from the time of this audit.
- Power audit retains the existing >=3 group, >=3 month, >=2 distinct PIT regime,
  and nonzero verified-outcome floors, and additionally refuses an inference claim
  until grouping/null assumptions and power are validated. Passing necessary
  floors is not a calibrated power result. Effective sample size stays null.
- Base persistence outcomes are counted separately from linked sequence copies.
  The persistence search path, pattern IDs, frozen vocabulary, budgets and support
  floors were not edited.

## Validation

Synthetic/offline tests: **162 passed in 12.56 seconds**:

```text
./venv/bin/python -B -m pytest -q tests/test_m32_correction.py tests/test_pit_dataset.py tests/test_m32_protocol.py tests/test_historical_pit.py tests/test_whole_trade_accounting.py tests/test_pit_population.py tests/test_population_rollforward.py
```

Includes overlapping/identical/duplicated periods, transitive market-wide overlap,
all cross-window identity/link types, missing-parent refusal, PIT regime receipts,
canonical accounting replay, tampering, cut/local-clock/universe/episode joins,
conflicting receipts, cross-store deduplication, read-only store preservation,
forward dataset replay, and persistence-family/pattern canonicalization.

`git diff --check` passed. The canonical historical input SHA-256 remains
`3a5ea9bd5a8a36ccfb0c50f0f6a3b17fdcb618d27922f92b7028edef10a0f7a8`.
Untouched search source SHA-256:
`1081e3482c0d709b81b4b93db2fa94437ebead2e4bc92c78b9b359f7030a827b`.
Untouched protocol source SHA-256:
`ade9d184c8e84a2e0be2219c106e6b77403089637dabd19f33bd34995be1e3bd`.

## Corrected current evidence

New immutable [v2 audit](../artifacts/m32-search/2026-09-19-m32-corrective-sufficiency-audit-v2.json):
227 historical rows, **1 dependence component**, **0 eligible verified execution
outcomes**, **0 distinct PIT-recorded regimes** (227 unknown rows), **1 month**.
There are **94 base price-persistence outcomes**; they are not execution P&L.
The historical artifact and original corrective audit were not rewritten.
The completed controlled diagnostic accounting sample remains engineering evidence,
not a natural strategy-performance observation.

## Grouping/null decision and next task

The existing conservative group rule is retained. It is not an independence proof.
The existing per-symbol null does not preserve whole episode chains or common
cross-symbol shocks and must not be certified for inference.

See the [exact block-sampling/null proposal](../specs/2026-09-19-m32-block-sampling-proposal.md):
global 28-day blocks, 17-day bounded source buffer, seven-day intake, one-day maturity
tail, three-day embargo; complete chain/market tensors move together under a
topology/stratum-preserving null. The proposal explicitly requires exchangeability
validation, rejects unsupported lookbacks/dependencies, and may prove too sparse
or too costly. It is NOT frozen or a new collection instruction.

**Single next task:** build the pure synthetic block-null/coverage/power calibration
harness, retaining existing thresholds and including adversarial common shocks,
serial dependence, missingness and topology mismatch. Do not collect or rerun
M3.2 before the protocol and its calibrated stopping rule are separately reviewed.
M3.2 remains partial; no acceptance checkbox or performance claim is added.
