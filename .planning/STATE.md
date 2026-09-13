---
gsd_state_version: '1.0'
status: planning
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-13)

**Core value:** No edge lasts — forward (live) performance, not backtest, is the only discriminator the book's one live strategy has left to prove itself against.
**Current focus:** Phase 1 — Correctness & Booking Fixes

## Current Position

Phase: 1 of 6 (Correctness & Booking Fixes)
Plan: none yet
Status: Ready to plan
Last activity: 2026-09-13 — Roadmap created from CLAUDE.md/docs ingest (`/gsd-new-project` ingest mode)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: N/A
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: none yet
- Trend: N/A

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap creation: all v1 requirements are open faults/measurement gaps
  CLAUDE.md already flags (no new scope invented) — see REQUIREMENTS.md.
- Research pipeline's `research.enabled: true` flip and `BINANCE_DEMO=false`
  are explicitly out of scope for this roadmap — both stay operator calls,
  same pattern as the strategy-first cutover.

### Pending Todos

None yet.

### Blockers/Concerns

- `tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar`
  currently fails on wall-clock drift (FIX-01, Phase 1).
- Production (non-demo) Binance taker fee is unmeasured — must be resolved
  before any future `BINANCE_DEMO=false` switch (FIX-03, Phase 1).
- `vector_backtest.WARMUP` bug blocks any future higher-timeframe (1d+)
  backtest work until fixed (FIX-04, Phase 1).
- Donchian Breakout Trail's universe-generalization question and the
  null-less admitted funding-filtered spec's admission validity are both
  unresolved and can only be settled by forward trade data (Phase 2).

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| Feature | LLM-FINETUNE-v2: fine-tune owner's model from journal corpus (cloud GPU) | Deferred | Roadmap creation | v1 |
| Feature | TALK-TOOLS-v2: add `search_knowledge`/`get_price` to Talk-to-Luffy toolbox | Deferred | Roadmap creation | v1 |

## Session Continuity

Last session: 2026-09-13
Stopped at: ROADMAP.md, PROJECT.md, REQUIREMENTS.md and this STATE.md written from the CLAUDE.md/docs ingest; awaiting orchestrator presentation and approval.
Resume file: None
