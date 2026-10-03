# LUFFY-STAGE1-DATA-PROVENANCE-PIT-R1

Result: **PASS — implementation tested and ready for independent review.**
Branch: `luffy-stage1-data-provenance-pit-r1`.
Base: `8d49c6976dc7359aed9451c4b3fd5c9bf55b0198`.
Uncommitted, undeployed. No startup/service operation or real order submission performed.
Independent review has **not** been performed for this package.

MI-2 and MI-3 close. Stage1 `build_complete` remains **false**: exactly MI-6 and MI-7 remain implementation blockers. No backup/restore or critical storage/heartbeat alert implementation is included.

## Contract and reuse

`trader/data/market_provenance.py` adds shared metadata and revision helpers to the existing candle/reference/derivative SQLite stores. It reuses `InstrumentId`, `MarketType`, the existing World `Quality` vocabulary, timeframe contracts and SQLite transaction helper. It does not add a second feed, database service, market-state architecture or trading authority.

Normal candle receipts retain canonical instrument, exact provider/environment source, observation kind, interval event time, locally measured receipt time, first usable availability, request identity/start, raw source record, content hash, local revision identity, supersession, quality and derivation identity. The existing Registry supplies typed bindings when CCXT market metadata is unloaded. Missing identity stays UNKNOWN. A typed market-data binding grants no account/trade capability.

Event, receipt and availability are separate fields. Candle event time remains the source's interval open. Source publication/revision guarantees are not invented; local receipt establishes conservative first obtainability. Absent auxiliary provider event time stays null with an explicit UNKNOWN event-time basis. CoinGecko snapshot stamps are labelled local capture in the retained raw recipe, rather than invented exchange event stamps. Existing registered reference close-delay policy remains conservative and explicit.

Existing `candles`, `derivs` and `refs` one-row tables remain compatibility/inventory projections. Qualified reads use append-only `market_revisions` and retained raw source records. A correction does not overwrite the earlier receipt/revision. Source content/revision hashes are checked on retained reads. The exact selected revision survives store reopen.

`VALID`, `STALE`, `MISSING`, `INVALID`, `INCOMPLETE` and `UNKNOWN` remain distinct. Unusable values are unavailable/NaN, never fabricated current zero. Historical/current auxiliary reads obey registered freshness/cadence. Unknown flow cannot become an aggressive-sell vote through pandas' missing-value sum; it returns an explicit abstention. Missing leader/regime/funding inputs no longer report neutral measured numbers.

## Time and bar semantics

`ohlcv_asof(..., as_of_ms=T)` is offline and selects only receipt/availability eligible at T. It does not invoke latest-now or REST. `latest_ohlcv` is explicit. The existing `cached_ohlcv` compatibility API now means retained bar-cut replay unless an explicit as-of is supplied. Default historical callers therefore cannot accept a fresh retrospective backfill as proof of what LUFFY knew earlier.

A native final bar requires interval completion plus actual receipt/availability. Partial bars are retained separately as PARTIAL/INCOMPLETE and cannot become the later finalized candle through an older read. Current decision frames exclude partial and degraded truth.

Local aggregation uses open-labelled `[start,end)` intervals, exact constituent revision identities, derivation version, quality, receipt and availability. Complete aggregation cannot precede either interval close or the latest required constituent availability. Mixed canonical instruments/providers are invalid; constituent quality propagates deterministically. Incomplete buckets remain partial.

HTF, reference, derivative, cross-sectional and funding-cost alignment use availability against each decision cut. Later corrections of older events cannot displace a newer eligible event. Rolling HTF ancestry cannot reach forward. Boolean UNKNOWN survives comparisons and negation, so `not (unknown > threshold)` cannot manufacture a valid entry.

Caches validate cut, source/identity and source revisions rather than process order alone. Future timestamped feed, feature, universe/listing, reference/derivative and auxiliary cache entries cannot answer an older cut. Qualified current/as-of frames are fenced from historical engines that require bar-cut replay.

## Actual caller audit

The structured audit is [caller-audit.json](../evidence/stage1-data-provenance-pit-r1/caller-audit.json), with source locations and caller classifications.

- Kernel acquires market frames, establishes a post-acquisition cut, rejects future/stale/invalid frames and mismatched canonical execution binding, and attaches exact revision lineage to `Snapshot`. Decision journal records preserve that lineage. Auxiliary book/funding/OI evidence is retained before use, with source identity, request/receipt clocks and raw content.
- Compiled live evaluators reject absent/relative cuts and unqualified frames. Feature contexts and caches bind source revisions/as-of; emitted signals preserve temporal source receipts for OpportunityContext.
- Attention normal capture rejects unqualified/future feed frames. Supplemental and declared forward acquisition retain raw source receipts and truthful post-request clocks. World/perception retain existing temporal ancestry checks and source observation identities. World routing keys remain unchanged; canonical identity is recoverable through frozen source receipts/refs.
- Attention positioning uses retained revisions through a read-only connection, replacing the overwrite-only derivative SQL path. Historical unqualified positioning records are UNKNOWN. Per-scan acquisition receipts are persisted separately from unchanged content versions; archive reconstruction rebinds exact receipts without querying latest data and rejects future joins.
- OpportunityContext rejects a signal cut or source receipt/availability after the context cut, as well as temporal ancestry beyond the signal's own cut. Existing World/scan/Registry restrictions remain.
- Strategy/research/referee candle loaders use retained replay. The evidence loader's latest-network fallback is removed. Historical analyst validation also uses replay, truthful completed HTF aggregation and explicit bar-derived cuts.
- `research.predictive_experiment` still copies its old raw candle inventory projection. That snapshot has no retained market revisions and is fenced by the existing evaluate/referee DataFeed contract: it produces unavailable/data-insufficient PIT evidence, not admission proof. Explicit research history collection is a current acquisition operation; it does not backdate historical availability.
- Outcome collection is explicitly post-decision realized-label collection, not information presented to the earlier decision. Pure numerical helpers remain available to synthetic fixtures; normal live and historical data readers enforce the receipt/replay boundary.

No remaining normal live-capable PIT bypass was found in the audited paths. This is a source/caller and offline-test conclusion, not live venue or operational maturity evidence.

## A–Q acceptance proofs

The machine map is [acceptance-map.json](../evidence/stage1-data-provenance-pit-r1/acceptance-map.json). All task safety proofs use fixed timestamps, fake feeds, temporary stores and controlled clocks.

| Required proof | Tests | Result |
|---|---|---|
| A | test_normal_feed_source_canonical_identity_and_raw<br>test_normal_derivative_adapter_preserves_raw_receipt | PASS |
| B | test_receipt_availability_not_event_time<br>test_request_clock_reversal_never_qualifies | PASS |
| C | test_unusable_quality_never_numeric_current<br>test_unknown_flow_and_leader_never_become_neutral_market_values | PASS |
| D | test_stale_failure_does_not_restamp_current<br>test_reference_revisions_reopen_and_stale_quality<br>test_auxiliary_stale_and_future_book_fail_closed | PASS |
| E | test_revision_retention_pit_and_reopen<br>test_derivative_revisions_quality_and_pit | PASS |
| F | test_revision_retention_pit_and_reopen<br>test_reference_revisions_reopen_and_stale_quality | PASS |
| G | test_future_observation_and_cache_rejected | PASS |
| H | test_htf_final_cannot_appear_early<br>test_htf_delayed_receipt_not_nominal_close | PASS |
| I | test_partial_final_lineage_and_eligibility | PASS |
| J | test_aggregate_constituent_availability_and_identity<br>test_aggregation_propagates_constituent_quality<br>test_aggregation_cannot_mix_canonical_instruments | PASS |
| K | test_future_observation_and_cache_rejected<br>test_kernel_historical_read_bypasses_future_memory_cache<br>test_listing_cache_future_receipt_rejected | PASS |
| L | test_latest_asof_and_replay_separate<br>test_unqualified_snapshot_cannot_use_latest<br>test_current_receipts_fenced_from_historical_engines | PASS |
| M | test_feature_provenance_and_future_cache_binding<br>test_cross_section_rejects_delayed_peer_at_historical_cut | PASS |
| N | test_kernel_snapshot_actual_boundary<br>test_world_capture_rejects_future_and_preserves_lineage<br>test_world_state_refuses_future_and_quality_survives<br>test_opportunity_refuses_future_signal_source | PASS |
| O | test_replay_original_revision_not_later_backfill<br>test_funding_cost_reader_respects_revision_availability<br>test_replay_export_reconstructs_inputs_and_evidence_without_dangling_ids | PASS |
| P | test_revision_retention_pit_and_reopen<br>test_reference_revisions_reopen_and_stale_quality<br>test_auxiliary_receipt_durable_and_unknown_provider_clock | PASS |
| Q | test_negative_control_event_clock_detected<br>test_negative_control_overwrite_detected<br>test_negative_control_future_cache_detected<br>test_negative_control_early_htf_detected | PASS |

The four executable mutants deliberately replace availability with event time, overwrite retained revisions, reuse a future cache, and expose a native final HTF bar early. Each is detected by its intended safety assertion. Event-reader and cache assertions inspect the actual returned evidence clocks; they do not merely distinguish None from an empty frame. These are component negative controls; independent outer fences can provide additional protection.

## Recorded verification

| Campaign | Exact result | Evidence |
|---|---|---|
| Focused data/PIT/feature/replay/World/Opportunity/collection regressions | 649 passed in 116.62s | focused-tests.txt |
| Final A–Q acceptance and four mutants | 49 passed in 2.77s | acceptance-tests.txt |
| Persisted normal World/learning/replay boundary regressions | 14 passed in 239.05s | world-regressions.txt |
| Exact repository equivalence and benchmark scripts, synthetic offline harness | 2 passed in 49.39s | engine-gates.txt |

The 49 acceptance cases overlap the focused batch; counts must not be added as distinct cases. Final acceptance was rerun after strengthening mutant assertions. All four accepted campaigns report **zero HTTP/socket attempts and zero production-store connection attempts**. The guard fails the whole campaign if a swallowed attempt occurs. No production journal or venue data is required by accepted tests.

The exact repository engine equivalence harness reports PASS across five seed families using the same synthetic signal arrays; the rotation family has zero fixture signals, which is disclosed in the log. Benchmark PASS: 5 synthetic runs, old 14.79s, new 0.093s, measured **159.9×**, above the documented 20× gate. This is numerical engine parity/performance, not strategy edge, profitability or real-order evidence.

A preliminary run entered an existing unmocked derivative-basis HTTP path and was interrupted (3 failed, 112 passed in 238.81s); successful venue contact was not established. A subsequent guarded preliminary campaign reported 238 case passes but had **37 denied network attempts**, so it was explicitly invalidated. Those campaigns are **not closure evidence**. The fixture was repaired and final campaigns deny network and production-store access. Exact disclosure is in [test-run-history.json](../evidence/stage1-data-provenance-pit-r1/test-run-history.json); no claim of zero attempted network access across every preliminary run is made.

Python compilation and scoped `git diff --check` pass. Graphify refresh is AST-only; generated `graphify-out/**` is excluded from package files. No semantic extraction or paid API is used. The CLI review helper was unavailable (`Not logged in`); implementation/review preparation proceeded directly. There is no independent-review PASS claim.

## Authority, limits and control plane

Risk policy/formulas and final permission, Execution authority/idempotency, StrategyVersion authority, Portfolio authority, Learning limits, Owner OS, first-live approval and the zero-LLM normal live loop remain unchanged. Protected authority modules are byte-identical to HEAD. Orchestrator only journals market-source lineage; Kernel changes concern market truth and degradation. Real order submissions: **0**. No Scheme-D, deployment, startup, restart or service mutation was performed.

Historical receipts cannot be manufactured for old overwrite-only tables, recent backfills, unqualified imports or data deleted under existing retention. Those reads remain explicitly unavailable. Provider publication/revision guarantees are not inferred. Restoring/archiving critical state and detecting missing storage/heartbeat remain MI-6/MI-7, not accomplishments of this package. Final tests do not establish real account eligibility, production usefulness, sustained runtime safety or real-venue correctness.

STATE records MI-2/MI-3 closure against exact artifacts, preserves Stage1 build_complete=false and leaves exactly MI-6/MI-7 open. NEXT selects `LUFFY-STAGE1-RECOVERY-MONITORING-FOUNDATION-R1` as RECOMMENDED_NOT_STARTED. Combining those gaps is coherent because critical-state survivability and storage/heartbeat failure detection use existing Journal/critical-store, deterministic Supervisor and recovery boundaries (SDD sections 24/27); it requires no new trading or market-data architecture.

The exact coherent package file set is [files-changed.json](../evidence/stage1-data-provenance-pit-r1/files-changed.json). Unrelated frontend/M4, knowledge, prior reports, generated Graphify files, runtime DB/logs and secrets are excluded. No staging or commit is performed.
