# ATT-03 — warm-up history and existing ranking audit

Terminal result: **BLOCKED_CALIBRATION_EVIDENCE** at architecture/offline scope.
ATT-01 and DATA-04 are CLOSED. Their original conditions and ATT-04 admission
policy are reused; no replacement scoring system or runtime activation is added.

## Demonstrated gap and correction

Baseline 6e4f0fa: five new regression cases failed, two passed. Insufficient
eligible history already returned `warmup`, with no negative salience, but its
rows omitted explicit null scores/components and candidate-visible history
requirements. Required history existed only in a separate candle-window
observation. The implementation commit adds row history evidence and carries
it through the existing admission receipt and typed investigation candidate.

History records timeframe, requested source cut, required/eligible bar counts,
first/anchor opens, missing opens, actual available receipt maximum and the
history eligibility cut. Incomplete windows have `eligible_at_ms: null`; this
is never an estimated future completion date. Missing, stale, gap and malformed
inputs retain existing statuses, issues and rejected-input reasons. Valid
history with degenerate scales remains unrankable; history eligibility alone
is not score eligibility or trading permission.

The default main window requires 26 consecutive eligible closes (20 + 5 + 1).
Correlation's optional 151-close requirement and existing positioning/cohort
rules remain unchanged. Missing components remain None. Advancing the calendar
cannot supply missing bars or finalize a partial receipt. Late leading history
qualifies only at its actual acquisition/observation cut; rewinding reproduces
warm-up. Capture input clocks and frames are unchanged by evaluation.

No score, weight, threshold, rotation, event reservation, minimum-slot policy,
learned influence or decision authority is changed. Existing receipts without
history fields retain the existing admission/candidate projection.

## Remaining original condition

Calibrated ranking is **not established**. The retained cognition specification
`docs/superpowers/specs/2026-09-15-cognition-offline.md`, Known limitations,
explicitly calls thresholds 2.0/1.5/0.75/1.0 unexamined defaults, not calibrated.
`trader/cognition/attention.py` documents correlation change as a deterministic
Fisher-scaled feature, not a calibrated test. SDD 8.4 adopts the existing salience
semantics and thresholds, but adoption does not supply calibration evidence for
the original ATT-03 requirement. SDD 8.2 requires calibrated weights.

Mapped ranking remains the existing maximum absolute available component, with
stable symbol ties, the adopted admission threshold/reservations and persistent
rotation. The tests establish deterministic policy behavior, not calibration.
No new economic-relevance/urgency/cost score or arbitrary weights are introduced,
and existing feature scores are not relabeled calibrated. Closing ATT-03 needs
approved calibration evidence for the exact existing components, scales and
thresholds, or an explicit owner revision of this requirement. This package
neither supplies that approval nor spends held-out data to obtain it.

## Validation

- New ATT-03 module: **8 passed**. Includes normal broad observation → persisted
  admission → candidate history without per-symbol network fetch, late receipt
  exact-boundary qualification, missing/invalid versus warm-up, partial/date-only
  refusal, null optional components, unchanged max rule, zero/forced-slot refusal,
  JSON replay and reopened-Journal ranking/eligibility identity.
- Downstream regression batch: **279 passed, 35 failed**. All 35 failures were
  reproduced by the exact same test IDs against isolated untouched 6e4f0fa source:
  32 legacy Kernel test fixtures lack state-machine `.state`, one compiled-spec
  diagnostic expectation differs, and two research-job fixtures fail. They are
  baseline limitations, not passing checks and not repaired in this task.
- **287 passing distinct cases** across the new module and regression batch.
- `graphify update .` completed AST-only; no semantic extraction or LLM labeling.
- Claude execution probe returned Not logged in; authorized work used Sol.

## Cross-tracker impact

ATT-02: capture coverage/completion and performance requirements remain separate;
null/history diagnostics add no capture-completeness or runtime proof. Relevant
telemetry tests are included above; the 33 baseline telemetry failures remain.

Research: existing loaders, World context and job suites exercised; two job
failures reproduce on baseline. No history becomes fabricated, no hypothesis is
admitted, and referee/handoff operational stops remain untouched.

Strategy: compiler/evaluation-context and DATA-04 regression suites pass. The
change adds evidence fields without changing numerical features, signals,
universes, declared scope or strategy gates.

DEC: Opportunity live-integration and normal ATT-01 candidate delivery tests
pass. A candidate remains INVESTIGATE_ONLY; warm-up evidence cannot fill missing
required analyst inputs or close DEC-01/DEC-04. Their rows remain unchanged.

No other requirement row closes. No Kernel/Dashboard launch, provider/venue call,
production-store write, market evaluation or external message is performed.
Next critical ATT-03 work: exact calibrated-ranking evidence under the existing
semantics; no successor is selected by this terminal checkpoint.
