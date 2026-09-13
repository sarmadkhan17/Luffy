# Roadmap: Luffy

## Overview

Luffy's core trading system is built and running: the strategy-first
cutover is live, the single creation path is guarded, the admission and
decay gates are measured, and the research pipeline's search machine
(phases 0-2) is built and measured against the real store. What remains is
the genuinely open work CLAUDE.md's "Open faults" section still flags: a
handful of small correctness fixes that everything downstream should be
able to trust; turning the book's live-trade history into a real forward
verdict on its one strategy and its one null-less admission, rather than a
single backtest snapshot; finishing the research pipeline's remaining
build-out (Referee → Reason → Reporting/cutover); and building the
Researcher agent so external research ideas stop sitting unconsumed in the
queue. Phases are ordered so correctness is trustworthy before it's relied
on, and so each research-pipeline layer is built on the one before it.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Correctness & Booking Fixes** - Close the small, independent correctness gaps everything else in this roadmap depends on trusting
- [ ] **Phase 2: Forward-Validation & Signal-Timing Tracking** - Track the live book's actual forward performance instead of relying on a single backtest snapshot
- [ ] **Phase 3: Research Pipeline — Referee** - Gate machine-found candidates (held-out look, LORD++ ledger, overlap/testability) before they can reach admission
- [ ] **Phase 4: Research Pipeline — Reason** - Attach an interpretable thesis (or an explicit UNEXPLAINED flag) to every candidate that clears the Referee
- [ ] **Phase 5: Research Pipeline — Reporting & Strategist Cutover** - Surface pipeline results weekly and retire the Strategist's now-redundant LLM path
- [ ] **Phase 6: Researcher Agent** - Consume queued external research ideas and hand validated mechanisms to the Strategist

## Phase Details

### Phase 1: Correctness & Booking Fixes
**Goal**: The system's core correctness invariants — test suite, live fee/P&L booking, production cost assumptions, and backtest bar accounting — are trustworthy enough to base the rest of this roadmap's forward-validation and go-live decisions on.
**Depends on**: Nothing (first phase)
**Requirements**: FIX-01, FIX-03, FIX-04 (FIX-02 verified already complete 2026-09-13)
**Success Criteria** (what must be TRUE):
  1. `tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar` passes without being coupled to the real wall clock, and the full suite is green.
  2. Binance's production (non-demo) taker fee has been read and recorded from a live production-key source, so the true fee is known before `BINANCE_DEMO` is ever set to `false`.
  3. `vector_backtest.WARMUP` no longer silently discards a large fraction of a higher-timeframe (1d+) test slice — fixed to scale with duration, or explicitly guarded so it fails loudly instead of underpowering the null silently.
**Plans**: TBD

### Phase 2: Forward-Validation & Signal-Timing Tracking
**Goal**: The operator can tell, from real trade data rather than backtest, whether the book's live strategies are actually working and how much signal-to-fill delay costs.
**Depends on**: Phase 1 (forward-trade P&L must be booked correctly before it's trustworthy evidence)
**Requirements**: FVAL-01, FVAL-02, FVAL-03
**Success Criteria** (what must be TRUE):
  1. Donchian Breakout Trail's live/forward trades are tracked and reported against its declared-universe backtest read (median PF 1.42, p=3.5e-04) and its no-edge undeclared-universe read (median PF 0.93, p=8.8e-01), giving a running forward-vs-backtest comparison instead of a single snapshot.
  2. `spec_funding_filtered_trend_pullback`'s forward trades are tracked and reported against its original admission read (pooled PF 2.46 over 20 trades, untestable null at admission), so the operator can see whether its forward record looks like its admission stats or like noise.
  3. `signal_bar_age_min` is measured across a representative sample of live decisions, producing a quantified answer for how much signal-to-fill delay costs the tested edge.
**Plans**: TBD

### Phase 3: Research Pipeline — Referee
**Goal**: Machine-found candidates from the already-built search (phases 0-2) can be statistically gated end-to-end and hand themselves to admission — no human re-types the rule, and nothing bypasses the one admission gate.
**Depends on**: Nothing new — builds on the already-shipped research pipeline phases 0-2 (outside this roadmap)
**Requirements**: RPIPE-01
**Success Criteria** (what must be TRUE):
  1. A held-out look (gate 1) is scored for a machine-found combination and recorded in the online FDR ledger, spending from the LORD++ wealth budget (target FDR 10%, initial wealth 0.05).
  2. The supervisory brake fires correctly: once ≥5 admitted strategies have completed forward windows and >10% failed, the per-test significance level halves until the realized failure rate falls back under target.
  3. Gate 3 (signal overlap <0.6 against the book; untestable — <4 symbols carrying a null percentile — REFUSED) is applied to machine candidates using the same admission-hole-fixed logic as the Analyst's existing gate.
  4. A machine-found combination that clears gates 1 and 3 is handed into `_mechanism_once` and reaches `analyst.admit()` carrying its exact rule and evidence, with the `strategies` table still written only there.
**Plans**: TBD

### Phase 4: Research Pipeline — Reason
**Goal**: Every machine-found candidate that reaches admission carries an interpretable story, or is explicitly flagged as unexplained, before it can affect the live population.
**Depends on**: Phase 3
**Requirements**: RPIPE-02
**Success Criteria** (what must be TRUE):
  1. An LLM thesis-writing step runs on each candidate that clears the Referee's gates, producing a mechanism narrative the operator can read.
  2. Gate 2 (the prediction test) is applied and spends from the same LORD++ budget as gate 1, per the error-budget design.
  3. A candidate whose mechanism the LLM cannot articulate is marked `UNEXPLAINED` rather than silently admitted or silently dropped.
**Plans**: TBD

### Phase 5: Research Pipeline — Reporting & Strategist Cutover
**Goal**: The research pipeline's results are visible on a weekly cadence, and once machine-found candidates are the trusted source, the Strategist's now-redundant LLM path is retired — without opening a second creation path.
**Depends on**: Phase 4
**Requirements**: RPIPE-03
**Success Criteria** (what must be TRUE):
  1. A weekly report of what the pipeline found, admitted, and refused — plus the current LORD++ ledger state — reaches the operator through an existing channel (e.g. Telegram or the vault).
  2. The Strategist's LLM-based spec-writing path is removed (or demoted to a documented fallback) once the machine-search path is the trusted source, and `tests/test_single_creation_path.py` still passes.
**Plans**: TBD

### Phase 6: Researcher Agent
**Goal**: External research (scraped market claims queued on the `research` stream) is formally tested rather than sitting unconsumed, and survivors reach the Strategist as validated mechanisms.
**Depends on**: Phase 3 (reuses the confound-battery / significance-testing infrastructure built there)
**Requirements**: RAGENT-01
**Success Criteria** (what must be TRUE):
  1. `research`-stream items queued via `brain/ideas.py` are consumed by a Researcher role rather than sitting unconsumed indefinitely.
  2. Each claim is pre-registered as a Thesis (mechanism, prediction, kill_condition) before being tested, not tested first and rationalized after.
  3. A Thesis is tested against a mechanical confound battery and reaches one of the designed outputs (TRADE/ADAPT/RESEARCH/WAIT/ACQUIRE).
  4. A Thesis that survives is handed to the Strategist as a validated Mechanism; the Researcher itself never writes to the live population, preserving the one-admission-gate invariant.
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Correctness & Booking Fixes | 0/? | Not started | - |
| 2. Forward-Validation & Signal-Timing Tracking | 0/? | Not started | - |
| 3. Research Pipeline — Referee | 0/? | Not started | - |
| 4. Research Pipeline — Reason | 0/? | Not started | - |
| 5. Research Pipeline — Reporting & Strategist Cutover | 0/? | Not started | - |
| 6. Researcher Agent | 0/? | Not started | - |
