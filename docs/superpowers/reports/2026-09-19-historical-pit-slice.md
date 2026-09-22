# M3.1-H historical PIT slice — September 19, 2026

Implemented without modifying forward collectors, live configuration, risk, exits, `research.referee`, `research.handoff`, or the exit A/B study.

## Deliverable

`trader.cognition.historical_pit` and `scripts.build_historical_pit_dataset` now:

- read retained SQLite typed-outcome stores and receipt artifacts read-only;
- require an explicit `local_imported_ms` for every accepted receipt;
- reject archives that lack that clock rather than using archive-read time;
- preserve declaration, receipt, source hashes, local clocks, membership versions and dataset receipts;
- build through the existing deterministic `trader.cognition.dataset` builder;
- replay the resulting dataset before artifact publication;
- emit a separate deterministic historical sufficiency audit;
- refuse to overwrite a differing output artifact.

The audit reports typed kinds, selected/ignored cases, failures, skips, transitions, sequences, resolved/unresolved outcomes, symbol/regime/calendar coverage, dependence groups, dataset exclusions and adapter rejections. It explicitly reports `population_sampling_claim=false`.

## Evidence

Focused and existing PIT/replay tests: **72 passed**.

The existing retained retrospective dataset replayed through the new adapter with:

- 78 rows;
- 78 skips;
- 0 selected cases;
- 0 ignored cases;
- 0 failures;
- 0 regime transitions;
- 0 sequence episodes;
- 78 resolved terminal receipts;
- 18 outside-universe and 32 post-cut exclusions.

Result: **historical sufficiency = insufficient**.

The current investigation ledger snapshot is not a substitute: all 229 retained rows are known after the old discovery cut and therefore remain explicitly excluded.

## Boundary

This slice does not implement M3.2, does not set or reinterpret global `search_ready`, and grants no search, admission, handoff, or trading authority. M3.2 remains blocked until a historical artifact satisfies the declared sufficiency policy and a separate search protocol is approved.
