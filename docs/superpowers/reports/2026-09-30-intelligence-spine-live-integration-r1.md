# LUFFY-INTELLIGENCE-SPINE-LIVE-INTEGRATION-R1

Date: 2026-09-30 · Branch: `intelligence-spine-r1` (from `world-model-foundation` @ `1eb00e1`)
Verdict: **BLOCKED** (the research leg). WorldModel → Attention → Investigation is TESTED end to end in shadow mode.
Runtime deployment: **NOT_DEPLOYED** (`attention.world_model: false`). Trading behaviour changed: **NO**.

## What now runs as one shadow pipeline

```
closed-bar candles captured by the Attention scan (version ids in the scan payload)
  → closed_bar_volume_window Observation (source_ref hashes the exact candle rows)
  → perceive(attention_volume_z v1) → volume_anomaly Observation (z_score, INTRADAY)
  → WorldModel (world.model.v1) at the exact scan cut, archived as WorldModelRecord
  → Attention evaluate(…, world_model) through the existing seam
  → scan payload + world-model-live-receipt.v1 (model_id, record_id, record_json)
  → investigation adapt(): stored record verified AND re-derived from the replayed candles
  → frozen allocation → investigation registration (existing families only)
  → opportunity-context.v1 with world_model AVAILABLE (model_id, record_id)
  → intelligence-trace.v1 (durable, same transaction, own savepoint)
  → research_question: UNAVAILABLE (no_registered_research_family_accepts_investigation_source)
  → plan / evidence / result / bank: NOT_RUN (upstream_research_question_unavailable)
```

## Components

| Stage | Result | Where |
|---|---|---|
| WorldModel live producer | TESTED, NOT_DEPLOYED | `trader/observability/world_producer.py` |
| Attention consumes live WorldModel | TESTED | `trader/observability/store.py` (worker child), `attention.py` settings flag |
| Investigation trigger | TESTED (existing families; exact model replay) | `trader/observability/investigation.py` `adapt`/`allocate`/`registration_context` |
| Research chain (Q/P/E/R/Bank) | **BLOCKED** | see Blocker |
| Referee / result | NOT_RUN for this spine; existing evidence predicates untouched | — |
| End-to-end trace | TESTED; research stages truthfully UNAVAILABLE/NOT_RUN | `trader/observability/intelligence_trace.py` |
| Shadow runtime | TESTED through the Kernel's collector → worker child | `tests/test_intelligence_spine.py::test_kernel_collector_worker_child_maintains_the_world_model` |

### Why the WorldModel adds no new salience

The only WorldModel kind the tested Attention seam accepts is one INSTRUMENT/INTRADAY
`volume_anomaly` z-score. The producer's perception transform is Attention's own
`volume_z` formula over the same N+1 closed-bar volumes (same arithmetic order), so an
accepted `world_volume_anomaly` component equals the candle `volume_anomaly` component
exactly. Ties break to `volume_anomaly`, so rank, selection, reason, salience and the
dominant (registration) family are unchanged. Verified by
`test_world_component_is_the_existing_volume_component_and_changes_no_ranking`
(world-on rows minus the world fields == legacy rows; market and observations identical).
The WorldModel therefore contributes **provenance**, not ranking evidence.

### Failure semantics (all tested)

| Case | Behaviour |
|---|---|
| WorldModel disabled / absent | legacy payload byte-identical; trace `world_model: UNKNOWN world_model_not_supplied`; context `world_model: UNKNOWN` |
| Anchor bar absent | `volume_anomaly` STALE, value None |
| Hole / short window | MISSING, value None |
| Negative / NaN volume | INVALID `malformed_volume` |
| Zero variance | INVALID `undefined_zero_or_nonfinite_variance` |
| Producer exception | receipt `unavailable producer_failed:<Type>`; legacy Attention output |
| Attention evaluation with the model raises | receipt `refused attention_world_evaluation_failed:<Type>`; legacy Attention output |
| Stale model (cut ≠ scan cut) | receipt `refused world_model_cut_mismatch`; legacy Attention output |
| Tampered / corrupt receipt | investigation refuses the scan (`world_model_record_corrupt`, `…_receipt_mismatch`, `…_receipt_invalid`); 0 registrations |
| Self-consistent fabricated model | refused `world_model_rederivation_mismatch` |
| Attention no selection | 0 cases, 0 traces |
| Registration failure | skip reason recorded; no case, no trace |
| Trace refused | registration and context kept; `intelligence_trace.refused{reason}` |
| Duplicate retry | allocation execution closed; trace table unchanged; identical re-persist = no-op; conflicting = refused |
| Restart / replay | context and trace rebuild byte-for-byte from persisted evidence |
| Evidence incomplete | `investigation_outcome.latest_evidence_status = unresolved`, `terminal = false`; no result claimed |
| Corrupt stored trace | `view` refuses (`intelligence_trace_corrupt`) |

## Blocker (exact)

No registered research family accepts an investigation (or an Attention selection) as a
question source. `research_families.FAMILIES` = `strategy_decay`
(`research-question.v1`) and `strategy_health_unreadable`
(`strategy-health-unreadable-question.v1`); both derive only from
`strategy-health-observation.v1` rows, and `research_question.py` states it is "not an
Attention trigger". The Research Bank, plan, evidence and result modules are
family-specific to that chain, and `research_sources.py` marks
`internal.worldmodel_regime_state` UNAVAILABLE. The package forbids a new trigger family
and parallel research objects, so Investigation → Q/P/E/R cannot be linked honestly.
Also: no falsifier predicates are registered (`research_result` is always INCONCLUSIVE /
`no_registered_falsifier_predicates`), so even a linked chain could not yield
SUPPORTED/REFUTED today.

Resolution requires an owner decision to authorize an investigation-sourced research
family with frozen question/plan/evidence/result contracts and registered falsifier
predicates (NEXT.yaml).

## Bounds and limitations

- Producer runs inside the existing timeout-bounded (10 s) Attention worker child, not on
  the trading thread; measured about +22 ms per scan at 16 symbols (machine under
  concurrent test load), about 42 KB of record JSON per scan. That counts against the
  Attention store's existing byte budget, so size-based pruning may retain fewer scans.
- No LLM, network, paid source, clock read, journal, DataFeed, Risk, Execution, order,
  allocation or control-state path is reachable (`test_spine_modules_import_no_trading_or_llm_path`;
  Kernel/orchestrator/risk/executor/exits reference neither new module).
- Investigation cases already stored keep their identities; world-enabled scans add the
  truthful `world_volume_anomaly:<sign>` transition to new case states.
- The investigation consumer remains opt-in and unscheduled; the flag is off.

## Verification

- `tests/test_intelligence_spine.py`: 26 passed.
- Affected regression (65 observability/attention/investigation files + world tests):
  2350 passed, 11 failed; the same 11 fail with identical messages on a clean
  checkout of `1eb00e1` (pre-existing guard/golden failures, see the other worktree's
  uncommitted guard edits). Zero new failures.
- Full-suite comparison: see "Full suite" below.

## Full suite

`pytest tests/ --continue-on-collection-errors`, run concurrently on a clean checkout of
`1eb00e1` and on this branch:

- base: 67 failed, 5653 passed, 12 errors
- branch: 68 failed, 5676 passed, 12 errors (+23 passed = new spine tests at that time)
- The failure/error sets differed by exactly one test,
  `test_strategy_decay_research_bank.py::test_nothing_in_the_live_path_calls_it`: a textual
  guard matched the trace's `research_bank` stage-name string (no call). Renamed the stage key
  to `research_bank_object`; the guard now passes unchanged (110 passed across the spine and
  bank tests). Net: **zero new failures**; all 67 failures / 12 errors are pre-existing at base.
- The fallback hardening (`attention_world_evaluation_failed`) and the stage rename landed after
  the full run; the spine, bank and affected tests were rerun afterwards.
