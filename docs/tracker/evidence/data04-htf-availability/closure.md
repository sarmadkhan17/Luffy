# DATA-04 — native and aggregated higher-timeframe availability

Engineering closure: CLOSED, 2026-10-06. Control-plane reconciliation pending: YES.
Scope: DATA-04 only. DATA-03 baseline: `6171687`; DATA-05 reader baseline:
`fc16e07`. No tracker YAML, STATE.yaml, NEXT.yaml or manifest edits. Offline
temporary databases and synthetic fixtures only; no provider calls, production
store writes, runtime boot, performance evaluation or other row closure.

## Existing evidence mapped before implementation

| Boundary | Existing implementation and exact evidence |
|---|---|
| Native closed versus partial | `market_provenance.annotate` marks observations PARTIAL/INCOMPLETE before nominal close. `DataFeed._store_save` retains the raw receipt separately from the closed-only candle projection. `eligible_frame` requires FINAL, eligible receipt/observation/event clocks and nominal close. `test_partial_final_lineage_and_eligibility`, `test_forming_bar_never_stored.py`, `test_spec_signals_on_closed_bars.py` |
| Native historical selection | `DataFeed.ohlcv_asof/latest_ohlcv/replay_ohlcv/cached_ohlcv` use DATA-05 `load`; historical requests bypass REST and mutable cache. `test_receipt_availability_not_event_time`, `test_future_observation_and_cache_rejected`, `test_latest_asof_and_replay_separate`, DATA-05 `test_exact_cuts_latest_lineage_and_restart` |
| Aggregate ancestry and availability | `backtest.resample` uses interval-open buckets, constituent revision IDs, maximum availability and observation clocks, quality propagation and nominal parent close. `test_aggregate_constituent_availability_and_identity`, `test_aggregation_propagates_constituent_quality`, `test_aggregation_cannot_mix_canonical_instruments` |
| Feature inputs / HTF alignment | `FeatureCtx` qualifies all contexts at its cut; DSL `htf` aligns at source availability/nominal close, propagates rolling dependencies and masks non-VALID/non-FINAL values. `test_htf_final_cannot_appear_early`, `test_htf_delayed_receipt_not_nominal_close`, `test_feature_provenance_and_future_cache_binding`, `test_negative_control_early_htf_detected` |
| Late correction and backfill | Append-only receipts retain original revisions; replay selects original bar-close eligibility rather than latest corrections. `test_replay_original_revision_not_later_backfill`, `test_revision_retention_pit_and_reopen`, DATA-05 revision-truth suite |
| World / Attention | `observability.attention.capture` requires eligible FINAL/VALID receipts before producing candles/history; cognition bars carry their own availability. `test_world_capture_rejects_future_and_preserves_lineage`, `test_attention_unqualified_frame_is_explicitly_missing`, `test_cognition_replay.py` |
| Historical engines / consumers | Strategy evidence and analyst contexts share `resample`; historical numerical engines require replay-qualified evidence. `test_current_receipts_fenced_from_historical_engines`, `test_stage1_pit_engine_gates.py`, DATA-03 consumer inventory and closure |

Existing tests established most clock/retention boundaries, but did not prove
every missing-child position, sparse qualified frame inference, a complete
timestamp grid containing an unfinished child, or the feature frame builder's
sparse-grid bypass. These were mapped before changes rather than assumed closed.

## Demonstrated gaps and bounded changes

1. Exact-cut feed reads lost their source timeframe (`load` sets timeframe to
   the replay argument, which is absent in as-of mode). Two retained 15m bars
   at offsets 0 and 30m therefore inferred a 30m child interval and became a
   VALID/FINAL 1h aggregate despite missing two 15m children. `ohlcv_asof` now
   preserves the requested series timeframe. Qualified `resample` requires
   a declared supported child timeframe rather than guessing it from gaps.
2. `resample` checked the timestamp grid but not constituent bar states.
   A complete four-child grid containing PARTIAL produced FINAL. Normal
   partial quality was INCOMPLETE, so existing downstream quality gates masked
   its numbers, but the parent's closed-state claim was wrong. An inconsistent
   VALID/PARTIAL child also reproduced numeric eligibility. Parent completeness
   now requires every child to be FINAL. Missing or unfinished children produce
   PARTIAL and cannot become closed evidence simply because time advances.
3. During consumer impact tracing, `spec_evidence.frames_for` inferred sparse
   15m receipts at offsets 0, 1h and 2h as native 1h, bypassing aggregation and
   returning their VALID/FINAL child values. The exact new feature-frame test
   failed before the fix. `detect_tf` now prefers the declared supported interval;
   sparse qualified source bars pass through aggregation and remain partial.

These are reproducible engineering gaps, not proof of a live trading incident.
The only code edits are the three boundaries above. Availability, observation,
event clocks, append-only storage, replay selection, quality policy and receipt
schema remain the DATA-03/DATA-05 model; no alternative clock or store is added.
Complete valid aggregate revision IDs and constituent ancestry remain unchanged.

## Required negative proof

New module: `tests/test_data04_htf_availability.py` (16 cases).

| Requested failure | Exact evidence |
|---|---|
| Partial current HTF bar | `test_native_partial_future_backfill_replay_and_reopen` for 1h and 4h: raw partial retained, closed historical read unavailable, time alone cannot finalize its receipt |
| Future-available native bar | Same test refuses final receipts before their exact acquisition cut, preserves original at earlier correction cuts; existing native delayed-feature test verifies source receipt availability beyond nominal close |
| Aggregate with unfinished child | `test_real_unfinished_child_stays_partial_after_nominal_parent_close`; `test_complete_grid_with_partial_child_never_final` covers both normal INCOMPLETE and contradictory VALID child quality |
| Missing child | `test_each_missing_child_keeps_parent_partial` covers all four positions; `test_sparse_asof_children_cannot_infer_coarser_timeframe` covers alternating missing children; unknown qualified timeframe is refused |
| Late correction/backfill | `test_aggregate_receipt_maxima_feature_cut_and_late_revision`; `test_late_missing_child_backfill_never_repairs_earlier_cut` covers 1h/4h, including availability exact boundaries |
| Replay before later availability | Native and aggregate correction tests retain original replay IDs; a late previously missing child cannot repair bar-close replay. Replay and exact as-of views intentionally differ |
| Feature inputs | Correction test exercises DSL `htf` with the same later aggregate at cut minus one and exact cut; `test_aggregate_observation_clock_fences_feature_inputs`; sparse `frames_for` bypass regression |
| Restart/reopen | Native original/partial/correction/backfill selection and deterministic aggregate derivation are repeated against reopened temporary stores. Original constituent ancestry and revision identity match |

All values in these tests are synthetic. Unknown history stays unavailable;
missing children are never filled or interpolated. Native history read after
backfill can expose that data at the later cut; it cannot certify the earlier
decision. Qualified aggregation without declared timeframe now fails explicitly.

## Validation

See [validation.md](validation.md) for final commands and outcomes. The new
negative controls reproduced the gaps before their respective fixes. Fixture
corrections (test clock advancement, ndarray indexing and a closed base bar)
were resolved separately and are not implementation-gap claims.

## Cross-tracker impact after engineering closure (read-only)

| Consumer/rows | Impact and limit |
|---|---|
| DATA-03 / DATA-05 / DATA-02 | Reuse their receipt readers, retained revisions and exact cuts. Regression suites cover future selection, corrupt indexes, lineage and reopen. No reader clock/storage migration or row reopening |
| DATA-06 / DATA-07 | No registry, acquisition universe, payload deduplication, retention or savings behavior changed; their independent proofs remain required |
| Feature / QNT-01 and Strategy evidence | Sparse child frames no longer acquire HTF authority from observed spacing. `frames_for`, analyst `validate_symbol`, strategy evidence contexts and historical aggregate contexts inherit the stricter aggregation boundary. DSL/spec evidence and offline equivalence/benchmark gates validate compatibility; this does not close quantitative parity or Strategy rows |
| World Model / WRLD-04, WRLD-05, WRLD-06 | Qualified HTF source data remains FINAL/VALID only after availability; source capture and world-to-Attention/replay integration tests pass. Claim lifecycle and full World row prerequisites remain separate |
| Attention / ATT-03 | Missing/partial HTF rows cannot enter closed history; capture retains explicit unavailable/warmup/gap behavior. Late-data cognition replay, Attention kernel wiring and world-to-Attention consumer tests validate dependent boundaries. Calibrated ranking and full ATT-03 closure are not claimed |
| Strategy consumers | Closed-bar signals, native/aggregate evidence construction and numerical historical engine gates pass. No strategy performance, admission threshold, execution behavior or live operational certification is asserted |

Local Claude execution probe returned `Not logged in`; implementation proceeded
under this session's explicit engineering authorization. Graphify query mapped
the existing paths. Graph refresh is deferred because `graphify update .` writes
a manifest prohibited by this session. Commits disable hooks to prevent incidental
graph/manifest mutation; other agents' shared workspace changes are preserved.

Next critical item: owner control-plane reconciliation of this DATA-04 evidence,
then ATT-03 warmup/availability evidence mapping using this dependency proof.
ATT-03 still requires its independent calibrated-ranking closure. No next-work
pointer is edited here.
