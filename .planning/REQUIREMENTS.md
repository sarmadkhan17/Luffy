# Requirements: Luffy

**Defined:** 2026-09-13
**Core Value:** No edge lasts — forward (live) performance, not backtest, is the only discriminator the book's one live strategy has left to prove itself against.

This is a brownfield system. Everything already built and confirmed by
CLAUDE.md (2026-09-13) is recorded as Validated in `PROJECT.md`, not
repeated here. The v1 requirements below are exclusively the genuinely open
work CLAUDE.md's "Open faults" and measurement sections still flag.

## v1 Requirements

### Correctness & Booking Fixes

- [ ] **FIX-01**: `tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar`
  passes reliably regardless of wall-clock date — the cache-freshness check
  must be decoupled from real time in the test (it currently reads real
  time while the test pins `_now`, and has failed since the week of
  2026-09-04 passed).
- [x] **FIX-02**: `Executor.close()`/`close_partial()` book journalled P&L
  from the venue's own per-fill `commission` field. **Verified complete
  2026-09-13:** `close_partial()` books `_fills_net(fills)` (venue commission)
  and `close()` re-bases the trade to `venue_realized_pnl`; the
  `taker_fee * notional` estimate runs only when the venue will not answer,
  and is flagged by `_book_estimate` (`pnl_estimated` event). CLAUDE.md's
  "still open" note on this was stale and has been corrected.
- [ ] **FIX-03**: Binance's production (non-demo) taker fee is read and
  recorded from a live production-key source (a demo key's
  `commissionRate` call answers -2015) before `BINANCE_DEMO` is ever set to
  `false`.
- [ ] **FIX-04**: `vector_backtest.WARMUP` (a 210-BAR count, not a duration)
  no longer silently discards up to 38% of a higher-timeframe (1d+) test
  slice and collapses its rotation null — fixed to scale by duration or
  explicitly guarded so a future 1d+ backtest cannot silently underpower
  itself the way the 1d closed-door measurement's generous re-run had to
  work around.

### Forward-Validation & Signal-Timing Tracking

- [ ] **FVAL-01**: Donchian Breakout Trail's live/forward trades (taken
  under the correct, already-fixed point-in-time geometry) are tracked and
  reported against its declared-universe backtest read (median PF 1.42,
  consistency p=3.5e-04) and its no-edge undeclared-universe read (median
  PF 0.93, p=8.8e-01), so the universe-generalization question starts being
  settled by forward evidence instead of sitting on a single CLAUDE.md
  snapshot.
- [ ] **FVAL-02**: `spec_funding_filtered_trend_pullback`'s forward trades
  are tracked and reported against its original admission read (pooled PF
  2.46 over 1-7 trades a symbol, cross-symbol null run on 0 symbols —
  untestable at admission time), so the operator can see whether its
  forward record looks like its 20-trade admission stats or like noise.
- [ ] **FVAL-03**: `signal_bar_age_min` is measured across a representative
  sample of live decisions, producing a quantified read (not a guess) of
  how much signal-to-fill delay (risk-blocked or otherwise) costs the
  tested edge.

### Research Pipeline — Phases 3-5

- [ ] **RPIPE-01**: The Referee layer scores machine-found candidates —
  gate 1 (a held-out look), the LORD++ online-FDR ledger (target FDR 10%,
  initial wealth 0.05) with its supervisory brake (halves the per-test
  level once ≥5 admitted strategies have completed forward windows and
  >10% failed), and gate 3 (signal overlap <0.6 against the book, plus the
  admission-hole-fixed testability check) — and hands survivors into
  `_mechanism_once` for `analyst.admit()`, with the `strategies` table
  still written only there (one admission gate, many proposers).
- [ ] **RPIPE-02**: The Reason layer runs an LLM thesis-writing step on
  each candidate that clears the Referee, applies gate 2 (the prediction
  test) spending from the same LORD++ budget as gate 1, and marks a
  candidate `UNEXPLAINED` — rather than silently admitting or dropping it —
  when the LLM cannot articulate its mechanism.
- [ ] **RPIPE-03**: A weekly report of what the pipeline found, admitted,
  and refused (plus the current LORD++ ledger state) reaches the operator
  through an existing channel, and the Strategist's now-redundant LLM
  spec-writing path is retired once the machine-search path is the trusted
  source — without introducing a second creation path
  (`tests/test_single_creation_path.py` still guards this).

### Researcher Agent

- [ ] **RAGENT-01**: A Researcher role consumes `research`-stream ideas
  queued in `brain/ideas.py` (currently sitting unconsumed), pre-registers
  each as a Thesis (mechanism, prediction, kill_condition) before testing
  it — never after — against a mechanical confound battery, reaches one of
  the designed outputs (TRADE/ADAPT/RESEARCH/WAIT/ACQUIRE), and hands
  survivors to the Strategist as validated Mechanisms; the Researcher never
  writes to the live population itself.

## v2 Requirements

Deferred to future release. Tracked but not in the current roadmap.

### Deferred

- **LLM-FINETUNE-v2**: Fine-tune the owner's own model from the journal
  corpus via rented cloud GPU (local-GPU inference stays out of scope — the
  dev box has no GPU).
- **TALK-TOOLS-v2**: Add Talk-to-Luffy's two deliberately deferred
  read-only tools (`search_knowledge`, `get_price`) to its ~9-tool
  whitelist.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Reintroducing lifetime pass/fail admission gates | Governing premise: no edge lasts; under a lifetime gate all 27 candidates scored zero |
| Raising risk/position caps above 8 slots / 0.5% risk | Measured optimum (`deployment_frontier.py`); more slots or more risk per trade makes both return and drawdown worse |
| A second strategy-creation path | Deleted 2026-09-11 after resurrecting a retired genome into all 8 trade slots; guarded by `tests/test_single_creation_path.py` |
| Building or enabling 1d (or higher) timeframe strategy trading | 2026-09-13 measurement: the declared edge collapses to no-edge at 1d; FIX-04 fixes backtest-engine correctness only, not new 1d strategies |
| Flipping `research.enabled: true` | Operator decision pending review of the 2026-09-13 power measurement; this roadmap builds the capability, not the go-live call |
| Setting `BINANCE_DEMO=false` / moving to real-money live trading | Separate operator decision; FIX-03 only prepares the fee data that decision needs |
| Verifying Agents Command Deck / Talk-to-Luffy current ship status | Design/spec-complete per the ingest, but no independent CLAUDE.md confirmation of shipped state was found; not investigated by this roadmap |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FIX-01 | Phase 1 | Pending |
| FIX-02 | — | Complete (verified 2026-09-13, pre-existing) |
| FIX-03 | Phase 1 | Pending |
| FIX-04 | Phase 1 | Pending |
| FVAL-01 | Phase 2 | Pending |
| FVAL-02 | Phase 2 | Pending |
| FVAL-03 | Phase 2 | Pending |
| RPIPE-01 | Phase 3 | Pending |
| RPIPE-02 | Phase 4 | Pending |
| RPIPE-03 | Phase 5 | Pending |
| RAGENT-01 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 11 total (1 already complete: FIX-02)
- Mapped to phases: 10 open
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-13*
*Last updated: 2026-09-13 after initial roadmap creation from CLAUDE.md ingest*
