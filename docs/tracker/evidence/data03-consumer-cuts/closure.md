# DATA-03 — consumer source-cut audit

Engineering closure: **CLOSED**, 2026-10-06. Engineering/evidence only. DATA-02 is closed; shared revision-reader
baseline is DATA-05 `fc16e07`. Control-plane reconciliation pending: YES.
No tracker YAML, STATE.yaml, NEXT.yaml, or manifest is edited.

## Consumer map before changes

PROVEN means a traced production boundary has named executable negative evidence.
IMPLEMENTED_BUT_UNMAPPED means enforcement exists but the all-consumer claim is
not established by an exact end-to-end test. ACTUAL_GAP requires a reproducer.
These classifications describe this audit, not canonical tracker statuses.

| Consumer | Boundary and shared enforcement | Initial classification / negative evidence |
|---|---|---|
| Candles: current, exact-cut, cache, restart | `DataFeed.fetch_ohlcv`, `latest_ohlcv`, `ohlcv_asof`, `cached_ohlcv`, `replay_ohlcv` → `market_provenance.load/eligible_frame/usable_current` | PROVEN: `test_stage1_data_provenance_pit.py`, `test_data05_revision_truth.py`; future cache, rollback, failed refresh, late correction, selected index corruption, reopen |
| Attention normal capture + replay | `observability.attention.capture/_history` → receipt qualification → `cognition.contracts.Dataset.bar_asof/members_asof` → `attention.evaluate` | PROVEN: `test_stage1_pit_blocker_fix.py`, `test_cognition_replay.py`; future members, late bars, membership removal, prefix invariance |
| Broad admission | `data.broad_crypto.observe/project` → exact-cut cached bars → `attention_admission.admit/verify_receipt` | IMPLEMENTED_BUT_UNMAPPED: cut-bound rows and immutable replay exist; bulk-acquisition clock rollback and metadata acquisition clocks need exact tests |
| Configured universe / historical scan | `Universe.symbols/membership_receipts` → `Kernel._scan_symbols` | ACTUAL_GAP: current configured majors are labelled observed at *any requested historical cut*. Strategy include declarations are also labelled at that cut |
| Auto-scan universe / volume / listing | `Universe._rescan/_old_enough/membership_receipts/volumes` | PROVEN for retained scan receipt: `test_stage1_pit_blocker_fix.py`; failed refresh cannot rewrite selection, future receipt/component clocks rejected. Missing earlier history fails unavailable |
| Strategy decision / scouts | `Kernel._snapshot_for` → shared `usable_current`; `CompiledStrategy.to_evaluator` → `eligible_frame` → `FeatureCtx` | PROVEN at supplied normal snapshot boundary: `test_kernel_snapshot_actual_boundary`, `test_unqualified_snapshot_cannot_use_latest`, `test_cross_section_rejects_delayed_peer_at_historical_cut`; standalone scout entry points rely on snapshot producer |
| Strategy admission / research / historical simulation | `spec_evidence.load_frames/load_derivs/load_refs`, analyst and research readers → `cached_ohlcv` replay; `vector_backtest._require_replay`, `backtest.simulate`; derivative/reference alignment | PROVEN shared engines: `test_current_receipts_fenced_from_historical_engines`, `test_stage1_pit_engine_gates.py`; IMPLEMENTED_BUT_UNMAPPED for historical universe declaration/version across every discovery CLI |
| Features / BTC / cross-sectional context | `FeatureCtx.__post_init__/temporal_identity`, `features_deriv.align`, HTF/ref alignment | PROVEN: `test_stage1_data_provenance_pit.py`, `test_pit_alignment.py`; future peers, delayed HTF, invalid quality, cache identity, funding corrections |
| Derivatives / funding / basis | `DerivFeed.load` → shared `load/eligible_frame`; `funding_series` → shared `align` | PROVEN: stage1 provenance + blocker suites; both basis legs, late correction, future receipt, reopen, UNKNOWN and stale |
| References / derived indexes | `ReferenceStore.load` → shared `load/eligible_frame`; `ref_sources.refresh_all` stamps derived observations at acquisition | PROVEN shared reader: `test_reference_revisions_reopen_and_stale_quality`; IMPLEMENTED_BUT_UNMAPPED for each provider refresh/reversed-clock combination |
| Portfolio context/economics/allocation | `source_adapter.contexts/economic_inputs` → `opportunity_live.produce/replay`; `allocator.allocate/replay` verifies source cut, TTL, binding and book | PROVEN: `test_opportunity_live_integration.py`, `test_portfolio_allocator.py`, `test_runtime_portfolio_integration.py`; future/stale/missing sources, wrong identity, exact snapshot, restart |
| Risk | `risk_intent.evaluate` → exact proposal/input binding + `runtime_metrics.project`; entry path uses supplied safe snapshot and `entry_authority` | PROVEN frozen Portfolio/Risk boundary: runtime Portfolio and RISK-01/RISK-02 tests; IMPLEMENTED_BUT_UNMAPPED for every standalone legacy Risk caller's market-source ancestry |
| Learning capture, replay, parent audit | `capture_runtime.frame_chunks` → immutable frame chunks; `capture.validate_chain/verify_ancestors` → exact blobs | ACTUAL_GAP: outer availability is stamped at cut without examining inner market receipt clocks; future primary/derivative/reference/peer/anchor frames can be frozen |
| Governed learning → Attention / World / Portfolio | `learning.consumers`, `targets.read/temporal_state`; frozen target revisions | PROVEN: `test_final_audit_learning_time.py`, `test_final_audit_attention_allocation_time.py`; future applied revisions, frozen replay and restart |
| Owner / audit market reads | `dashboard.owner_reads` → DATA-02 `resolve_lineage`; DATA-05 index/receipt consistency | PROVEN: `test_data02_decision_lineage.py`, `test_data05_revision_truth.py`; missing receipt, future revision, wrong instrument/schema/transform/hash, corrupted index, reopened store |
| World Model source/claim reads | observation quality + world exact-cut capture/reconstruction; learned revisions qualified at model cut | PROVEN bounded source boundary: stage1 provenance, final learning-time, stage7 world/replay suites; separate World row closure not implied |

## Reproducers and changes

The inventory is [direct-consumers.md](direct-consumers.md), with 215 candidate
calls in `trader/` and `scripts/`. The names are an intentionally broad search inventory, not proof
that every attribute call targets a DataFeed. Generic `load`, `evaluate`,
`observe` and `verify` calls include unrelated persistence/control operations;
only the market/source consumers below grant relevant numerical authority. Store `load` methods, direct SQL,
Portfolio/Risk source envelopes and learned-target/replay paths were traced
separately. Raw candle projection SQL in research coverage/predictive freezing
is inventory/copy work: numerical scoring reads retained replay revisions,
never those projection rows as historical receipt truth. Discovery symbol lists
and spec universe declarations define the evaluation protocol; they do not
establish historical venue membership. Historical membership replay uses the
retained scan/input record; a current Universe cannot invent missing history.

The initial IMPLEMENTED_BUT_UNMAPPED entries were resolved by tracing their
normal producer or shared reader and mapping the negative tests below. During
that trace, additional ACTUAL_GAP controls failed before their respective fixes:

1. **Capture rebasing:** ten controls across primary, derivative, reference,
   peer and anchor chunks accepted a receipt with availability or local
   observation at `cut + 1`. `frame_chunks` now uses the shared
   `eligible_frame(..., final=False)` boundary to refuse ineligible/partial
   receipt metadata while preserving exact captured bytes. Main replay and
   parent audit repeat that check. Capture and replay also refuse a snapshot
   timestamp after its source cut. Partial bars can be retained as raw
   observations; this is not a claim that they were final decision bars.
2. **Universe declaration rebasing:** a Universe constructed at `T + Q`
   returned configured BTC membership at `T`, and rewrote observation time on
   every call. Configuration members now keep the initial observation clock;
   earlier cuts return unavailable membership. An explicit historical Kernel
   scan with live spec declarations refuses
   `historical_strategy_universe_unavailable`; frozen replay supplies the
   recorded declaration/membership instead. No speculative historical config
   or venue-membership migration is performed.
3. **Bulk acquisition rollback:** a ticker request started at `CUT`, ended at
   `CUT - 1000`, and admitted rows at that earlier clock. The request now
   refuses reversed time. The regression leaves metadata independently
   eligible so it specifically exercises the bulk request fence. The legacy
   Universe scan also published component volume receipts before its final
   rollback check; its new control failed with a VALID volume at the reversed
   clock. It now refuses before component publication and checks each component
   against request start. Existing receipts are not restamped.
4. **Undated venue metadata:** broad observation accepted mutable market
   metadata with no receipt, or metadata first known at `CUT + 1`. Boot now
   appends the existing `load_markets` acquisition to the existing Journal
   `market_revisions`/raw-source tables with `annotate/append`, a local snapshot
   event basis and `venue_metadata.v1` transform. Its normalized close field is
   the observed record count, not a market price. Broad observation uses
   DATA-05 `load` plus `eligible_frame`, verifies source/instrument/kind/
   transform and requires this boot's receipt. Missing, future, malformed or
   foreign receipts return UNAVAILABLE. It decodes the retained raw metadata,
   not the mutable exchange cache. Old boot metadata does not qualify newer
   live configuration/specs at a prior cut. Frozen admission replay retains its
   own original observation and receipt. Admission replay additionally qualifies
   the embedded metadata clocks and reconstructs its receipt hash with shared
   `prepare/eligible_frame`; a correctly hashed outer cohort cannot certify a
   missing or future inner receipt. Undated original production cohorts become
   unavailable rather than gaining invented ancestry. Empty UNAVAILABLE
   observations still preserve the independent mandatory-exposure path. No
   extra venue request is introduced.
5. **Analyst replay leader clock:** `validate_symbol` supplied BTC whose
   availability was at the simulated close but whose local observation was
   one millisecond later. The leader is now qualified at each simulated close
   through `eligible_frame`; missing receipt fields in a retained frame cannot
   fall back to a timestamp-only read.
6. **Outcome observation cut:** `resolve_pending(..., now_ms=cut)` graded a
   5m candle first received at `cut + 1`. Explicit calls now request the exact
   retained feed cut and requalify the returned frame. Ordinary acquisition
   uses one post-receipt measurement clock for grading, resolution timestamp
   and capture. `grade`, HOLD backfill and 24h upgrades qualify frames at their
   observation cut. STALE/UNKNOWN prices remain ungraded. An isolated import of
   the original `HEAD` backfill source returned `(fwd_ret_4h=1.0,
   correct_4h=1)` for VALID, STALE and UNKNOWN future receipts; all three
   controls now return unknown. Frozen forward-outcome replay also checks
   target receipt eligibility at the retained measurement clock.

All point-in-time selection uses the DATA-02/DATA-05 shared readers. There is
no second revision store or selection algorithm. Numerical unqualified test
fixtures and prospective raw observations preserve their existing semantics;
they do not establish market-receipt ancestry. Production market producers
deliver qualified frames, and durable owner/audit reads require exact receipts.
Missing historical data is unavailable, never a fabricated market value.

## Final consumer classifications

| Normal / replay family | Final engineering classification | Closing evidence |
|---|---|---|
| Attention / admission / membership | PROVEN | new DATA-03 rollback, metadata source/cut/schema/transform, mutable-cache and boot/reopen controls; hierarchical admission and cognition replay |
| Strategy / decision / analyst replay | PROVEN | snapshot qualification, replay-only historical engines, scoped feature contexts, new local-observation leader control, engine equivalence gates |
| Portfolio | PROVEN | immutable context/source TTL and exact binding; allocation replay, independent metrics and restart |
| Risk | PROVEN | safe market snapshot for normal entry, exact frozen Portfolio/Risk input binding, authority/restart refusals |
| Learning / outcome / replay / parent audit | PROVEN | future chunk refusals for all five frame groups, frozen parent refusal, exact bytes after reopen, explicit outcome cut, late correction/restart, frozen target qualification |
| Owner / audit | PROVEN | DATA-02 exact lineage + DATA-05 index/receipt consistency; missing/future/foreign/schema/transform/hash refusals and reopened stores |
| Reference / derivative / HTF / BTC / cross-sectional contexts | PROVEN | shared readers/annotate request clock, alignment, component receipt maxima, quality/staleness, late correction and future peer controls |

These classify normal producer paths and immutable replay contracts, not every
possible third-party invocation of a numerical helper with invented inputs.
Acquisition and maintenance scripts in the inventory do not independently
grant decision/admission/learning authority. Their scoring paths use the
same retained feeds, contexts and historical engine guards. No strategy
performance, held-out market evaluation or live incident is claimed.

## Validation

Final successful commands and outputs are retained in [validation.md](validation.md).

The new module is `tests/test_data03_consumer_cuts.py`. It covers all five
captured frame groups, both receipt clocks, missing receipt fields, frozen
parent and main replay, exact-byte restart, config membership before
observation, live-strategy historical refusal, rollback, undated/future/foreign
metadata, metadata correction/reopen, mutable cache invariance, analyst leader
observation time, future outcome prices, STALE/UNKNOWN grading and late
outcome correction/restart. Existing DATA-02/DATA-05 suites provide the
durable instrument/schema/transform/hash/index and revision-selection controls.

The broad regression batch passed **450 tests**. Later focused batches passed
**172 tests**, **121 capture/replay integration tests**, **19 alignment/context/
offline engine-gate tests**, **35 derivative/reference tests**, and **32 tests**
in the new module before its last positive restart control was added. These
counts overlap and are not a unique total. An obsolete admission test double
was repaired to include its existing FROZEN control-state field; mutation tests
were updated to target the stricter membership return. Numerical contracts
and original negative assertions were preserved.

The initial all-module run exhausted its test process's 1,024-file soft limit
near completion (`Errno 24`, then fixture/teardown failures); it is not counted
as a pass. The second combined run raised the process-local limit to 8,192 and reached
495 passes before `/tmp` disk exhaustion (`database or disk is full` followed
by fixture creation failures; 1 failed, 95 errors). Neither resource-exhausted
run is counted as a pass. A **154-test** focused run passed after the last
component-publication and admission-replay fixes. The affected remaining
modules are rerun with dedicated `--basetemp` directories on `/mnt/luffy-data`
and the process-local limit of 8,192. No production limit or source change
was needed, and other agents' temporary artifacts were left untouched.
Recovery validation passed: **76 tests in 69.75s** for readers/alignment/
outcome backfill and **74 tests in 484.64s** for World replay/historical
capture/decision sources. A final **154 tests in 21.94s** focused run also
passed on the current workspace. These three final batches cover 304 distinct
tests; they overlap earlier broad validation. No demonstrated DATA-03 gap
remains in the mapped normal/replay consumer boundaries.
No Kernel/Dashboard boot, real venue request, production-store
mutation, real-market scoring, or external message was performed. The local
Claude execution probe returned `Not logged in`; work continued under the
explicit engineering instruction. Graphify query mapped the existing graph;
refresh is deferred because `graphify update .` writes a manifest, forbidden
by this session. No semantic extraction was run.

## Cross-tracker impact — read-only

| Rows | Effect / limit |
|---|---|
| DATA-01 / DATA-02 / DATA-05 | Canonical identity, exact receipt reconstruction and append-only revision contracts are reused. DATA-05 `fc16e07` remains the reader baseline; no resolver/index/selection change or reopening of those rows |
| DATA-04 | HTF partial/final, constituent availability and native historical engine gates pass; this is supporting evidence for its own alignment mapping, not automatic row closure |
| DATA-06 | Venue metadata now has source-cut-qualified ancestry. Crypto classification metadata does not grant account trading eligibility or execution authority |
| DATA-07 / DATA-08 / DATA-09 / DATA-10 | Equal payloads still retain distinct observations under the existing append policy. Retry, retention, prospective collection and historical coverage claims remain separate; missing pre-observation membership is not backfilled |
| WRLD-01 / WRLD-03 / WRLD-05 / WRLD-06 | Current/historical source and learned-revision cut guards pass. World/Attention learned state and frozen replay stay cut-qualified; no claim semantics or learning authority changed |
| PORT-01 through PORT-08 | Context, economics, allocation, whole-book metrics, Risk and restart regressions pass. No scoring/economics model, ranking rule, size bound or execution capability is added |
| OUT-01 / OUT-03 / LRN-01 | Capture/parent/forward replay now refuse unavailable inner receipts; later corrections cannot be substituted at an earlier outcome observation cut. Old invalid captures become unavailable rather than being repaired into verified evidence |
| LRN-02 / LRN-03 / LRN-04 | Registered learning rules, owning-authority apply and future consumer policies remain unchanged. Existing Attention/World/Portfolio future-revision tests pass; no automatic learning apply occurs |

Control-plane reconciliation pending: **YES**. This report does not edit or
close any other row. Next critical engineering item after DATA-03 closure:
**DATA-04** native/aggregate availability mapping. Graph refresh belongs to the
authorized control-plane reconciliation workflow because of its manifest write.
