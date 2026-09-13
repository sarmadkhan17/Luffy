# Phase 1: Correctness & Booking Fixes - Context

**Gathered:** 2026-09-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Three independent correctness fixes the rest of the roadmap relies on:

- **FIX-01** — `tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar`
  passes regardless of wall-clock date; full suite green.
- **FIX-03** — Binance's production (non-demo) taker fee is read from a live
  production-key source and recorded, before `BINANCE_DEMO` is ever `false`.
- **FIX-04** — `vector_backtest.WARMUP` bar accounting no longer silently
  discards scored data. **Widened during discussion (operator decision
  2026-09-13):** the fault is live at 4h, not only at 1d+ — see D-05.

FIX-02 is already complete (verified 2026-09-13) and is not work here.
Setting `BINANCE_DEMO=false`, enabling 1d trading, and flipping
`research.enabled` all stay out of scope.

</domain>

<decisions>
## Implementation Decisions

### FIX-01 — MacroGuard cache clock
- **D-01:** Fix the production code, not the test. The root cause is that
  `MacroGuard._load_cached()` (`trader/agents/macro_guard.py` ~L133-165)
  measures cache age with `time.time()` and the event horizon with
  `datetime.now(timezone.utc)`, bypassing the injectable `self._now()` that
  the test pins. Route both through `self._now()` so the guard has one clock.
  Do not monkeypatch `time`/`datetime` inside the test to paper over it.
  Check the rest of the class for other raw-clock reads on the same path
  (`_fetch`'s TTL uses `time.time()` for fetch recency — decide whether that
  belongs on the same clock; the cache-age and horizon reads definitely do).
- **D-02:** "Full suite green" means the whole `tests/` directory, run from
  a checkout that has `venv/` and the real `data/` stores. Measured
  2026-09-13: from this git worktree (no `venv/`, no `data/candles.db`) the
  suite reads 15 failed / 1203 passed, but 14 of those
  (`test_research_runner.py` x12, `test_research_universe_depth.py` x2) are
  environment artifacts: the same files pass 29/29 in the main checkout. The
  one genuine failure is the macro_guard test. Verify green in an
  environment with the data, and say which one was used.

### FIX-03 — Production taker fee
- **D-03:** Source is a **read-only production Binance API key** created by
  the operator: "Enable Reading" only — no trading, no withdrawals —
  IP-restricted to this VM, stored in `.env` under names distinct from
  `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` (so nothing in the kernel can pick
  it up by accident). A one-shot script calls `commissionRate`
  (`fapiPrivateGetCommissionRate`) against production for every declared
  symbol (the declared 16 at minimum) and records the per-symbol maker/taker
  rates with a UTC timestamp. The script must never be able to place an
  order; the kernel must never read the production key. Creating the key is
  a human step — the plan needs a checkpoint for it.
  — **Reversibility:** reversible — the key can be revoked on Binance at any time.
- **D-04:** **Record only — no boot guard.** The operator explicitly declined
  a kernel startup refusal on `BINANCE_DEMO=false` without a recorded fee.
  Record the measured rates in a durable data file plus CLAUDE.md (replace
  the "Production fees are UNMEASURED" note with the measured figures, date,
  and the demo-vs-production comparison). Do NOT change `config.yaml`'s
  `taker_fee_pct` (0.04, demo-measured) — switching cost assumptions belongs
  to the go-live decision, which is out of scope.

### FIX-04 — Warmup bar accounting (widened)
- **D-05:** Scope widened from "guard 1d+" to **fix the fault at 4h in this
  phase** (operator decision 2026-09-13). Finding that drove it, confirmed by
  reading the code:
  - `Analyst.review_deployed` → `rolling.has_decayed(recent_days=30)` →
    `_score_window` slices `bars("4h", 30)` = **180 bars**, and `simulate`
    returns an empty result when `n <= WARMUP + 1` (211). **A 4h spec's decay
    check can never see a trade**, so it always reads "idle — too few trades
    to judge": Donchian Breakout Trail, the book's live 4h strategy, cannot
    be retired by decay.
  - Admission at 4h/90d is 540 bars with the first 210 (39%) unscored,
    which pushes the 90-day window to double more often than the data
    warrants.
  - `rolling_windows` at 4h/60d is 360 bars, 58% unscored; at 1h the 30-day
    decay slice loses 29%.
  - CLAUDE.md's "harmless at 4h", "not a fault at 4h or below" is therefore
    wrong and must be corrected.
  — **Reversibility:** costly — changes what admission and retirement see for every live spec; undoing it re-blinds the decay gate.
- **D-06:** Fix shape: **indicators warm up on bars BEFORE the scored window;
  only the window's own bars are scored.** Do NOT scale `WARMUP` by
  duration. It is an indicator lookback and correctly a bar count, and
  tying it to 35 days would make it 3,360 bars at 15m and change every 15m
  result. Where no prior history exists (the start of a symbol's store), the
  unscorable prefix stays unscored — it is not fabricated.
- **D-07:** A loud guard remains for the case the fix cannot cover. When a
  slice still cannot yield enough scored bars, or the rotation null
  (`null_baseline.null_pfs`, offsets drawn from `[WARMUP+1, n-WARMUP-1]`)
  has too few distinct offsets for its draws, the result must surface as
  UNTESTED/untestable, never as a score and never silently empty. This
  covers 1d+ and any short slice.
- **D-08:** Before/after evidence is part of the deliverable. Record, before
  and after the change: the decay verdict and admission window/evidence for
  Donchian Breakout Trail and `spec_funding_filtered_trend_pullback`, plus
  the Donchian declared-16 headline (median PF, null percentile,
  consistency p). The operator was told the first decay sweep after the fix
  could retire a live strategy — that outcome is legitimate, not a
  regression to suppress. Surface the before/after readings to the operator
  before the kernel runs a decay sweep on the fixed code.
- **D-09:** Invariants that must hold: `scripts/backtest_equivalence.py`
  PASS, `scripts/bench_vector_backtest.py` > 20x, `spec_evidence` UNTESTED
  semantics unchanged. Research must check whether the equivalence script's
  own `range(WARMUP, n - 1)` loop is part of the contract or something to
  move with the fix. The research pipeline's `slices.py`
  (`MIN_SLICE_BARS = WARMUP + 210`, `WARMUP`-based bar counting) and the
  2026-09-13 power measurement it produced must be re-examined: if scored
  bars change, say whether that measurement still stands.

### Claude's Discretion
- The FIX-01 clock refactor details, the fee script's file name and output
  format, the exact D-07 guard thresholds (to be MEASURED against the live
  callers, not picked), and how the warmup prefix is threaded through
  `simulate`/`rolling`/`null_baseline`/`research.evaluate` callers.
- Plan split: the three fixes are independent. FIX-03 blocks on a human key
  step, so it should not gate FIX-01/FIX-04.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project scope
- `.planning/ROADMAP.md` §Phase 1 — goal and success criteria
- `.planning/REQUIREMENTS.md` — FIX-01, FIX-03, FIX-04 (FIX-02 complete)
- `CLAUDE.md` §"Known-good invariants", §"Open faults" (macro_guard test
  note), §"What measurement established" (fee/slippage bullet: "Production
  fees are UNMEASURED"; the `WARMUP` bullet — to be corrected per D-05)

### FIX-01
- `trader/agents/macro_guard.py` — `_load_cached`, `_save_cached`, the
  calendar fetch path, `_now`
- `tests/test_macro_guard.py` — `test_a_restart_reuses_the_cached_calendar`,
  `_guard_ff` helper

### FIX-03
- `trader/engine/executor.py`, `trader/engine/risk.py`,
  `trader/strategy/vector_backtest.py`, `trader/strategy/backtest.py` —
  consumers of `taker_fee_pct` (read to understand, do not change)
- `trader/engine/protective.py` / `scripts/monitor.py` — existing direct
  venue-query patterns
- `config.yaml` `risk.taker_fee_pct` — stays 0.04

### FIX-04
- `trader/strategy/vector_backtest.py` — `WARMUP`, `simulate` (early return
  at `n <= WARMUP + 1`, `cursor = WARMUP`)
- `trader/strategy/null_baseline.py` — `null_pfs` offset range
- `trader/strategy/rolling.py` — `bars`, `rolling_windows`, `_score_window`,
  `recent_verdict`, `has_decayed`
- `trader/brain/analyst.py` — `review_deployed` (~L500), admission
  (`select_recent_days`, ~L134)
- `trader/strategy/spec_evidence.py` — `run_gauntlet` (split=0.7, part="test")
- `trader/research/slices.py`, `trader/research/evaluate.py` — search-side
  WARMUP consumers
- `scripts/backtest_equivalence.py`, `scripts/bench_vector_backtest.py` —
  invariants
- `config.yaml` — `decay_recent_days: 30`, `decay_min_trades: 10`,
  `decay_floor_pf: 0.85`, `select_recent_days`, `select_max_days`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `MacroGuard._now()` — the existing clock seam; FIX-01 extends its use.
- `reconcile.venue_realized_pnl` / `protective.py` — ccxt-over-Binance
  patterns for authenticated reads.
- `null_baseline.consistency_p` / `edge_percentile` — already return None
  on an empty null; D-07's UNTESTED path should build on that, not invent a
  parallel signal.

### Established Patterns
- Missing information is NaN/UNTESTED, never a fabricated default.
- `_score_window` deliberately evaluates entries with the FULL universe
  frames for cross-sectional alignment while slicing the base frame, which is
  a precedent for "compute on more history, score on the window".
- Retirement never widens its window (`has_decayed` passes
  `max_days=recent_days`). The fix must keep that: warmup prefix bars are
  for indicators only, never scored trades.

### Integration Points
- `simulate(...)` has callers in `rolling.py` (3), `vector_backtest.py` (2),
  `null_baseline.py`, `research/evaluate.py`, `scripts/mech.py`,
  `scripts/backtest_equivalence.py`.
- The kernel's hourly brain tick runs `review_deployed`, so the fixed code
  goes live on the next tick after deploy/restart (see D-08 about surfacing
  readings first).

</code_context>

<specifics>
## Specific Ideas

- Operator preference (standing): Claude makes the engineering calls, with
  evidence. Ask the operator only about keys, money, live-book behaviour
  changes, and overturning documented claims.
- Name the production-key env vars so they cannot be confused with the demo
  key (e.g. a `BINANCE_PROD_READONLY_` prefix).

</specifics>

<deferred>
## Deferred Ideas

- Kernel boot guard refusing `BINANCE_DEMO=false` without a measured
  production fee: offered and **declined** by the operator (D-04). Not a
  backlog item unless raised again.
- Switching `taker_fee_pct` to the production figure: part of the go-live
  decision (out of scope).

</deferred>

---

*Phase: 01-correctness-booking-fixes*
*Context gathered: 2026-09-13*
