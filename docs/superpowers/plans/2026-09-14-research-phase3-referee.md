# Research pipeline — Phase 3: the referee — Implementation Plan

> **For agentic workers:** execute task by task, test first. Steps use
> checkbox (`- [ ]`) syntax. Spec: `docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md`, Part 4.

**Goal:** Take the search's discovery survivors to the two slices it has
never read — held-out markets (A) and the later era (B) — under an online
false-discovery budget (LORD++) that every look spends from. Then ask whether
a candidate adds compounded return to the live book, and hand what passes to
`_mechanism_once` → `analyst.admit()`. Gate 2, the reasoning test, is phase 4.
Until it exists **nothing is admitted**: the spec says "nothing is admitted
without a tested thesis". Phase 3 builds and calibrates the handoff, but it
stays closed.

**Out of scope:** the thesis/prediction step (phase 4) and weekly reporting
(phase 5).

## State at start (measured 2026-09-14)

| measurement | value |
|---|---|
| ledger | 2,212 combinations at 4h: 101 survivor, 57 grow, 2,054 prune; k ≤ 2 so far |
| batches | 335, 273 failed. **All 273 were one fault**, fixed in `e10d58a`: control batches were judged against `min_discovery_symbols=16`, but HYPE's 4h history starts after the cut, so the incumbent's declared 16 loads 15 |
| top survivors | every one of the top 5 contains `r_alts_z96>p90` (`ref("alts", zscore(close, 96))`), discovery p=4.2e-15, ~210%/yr, maxDD ~14% |
| look-ahead check on the top survivor | delaying the alt index by one bar gives p=1.1e-11, +148%; by two bars p=4.2e-15, +178%. **This is not look-ahead in `ref()`** |

### What the top survivors change about this phase

`consistency_p` assumes each symbol's null percentile is an independent
vote. A market-wide condition breaks that assumption. "Alts as a group are
trending" is true or false for all 19 discovery symbols at once, and all 19
are members of the alt index. The per-symbol rotation null moves each
symbol's entries in time out of the alt-trend regime. So every symbol beats
its rotation for the same reason, and the binomial counts one effect 19
times. That is the same shape as "one decision counted thirty times", this
time across symbols instead of cycles.

The p=4e-15 is therefore a statement about the market regime, not 19
confirmations. The edge may still be real, since alt-index time-series
momentum is plausible. But the referee needs a test whose unit of evidence
is the whole book of entries, not the symbol. **Task 1 builds that test
before any gate uses a p-value.**

### A contamination to respect

Donchian's declared 16 includes UNI, SUI, TAO, ZEC, NEAR, FIL, AAVE and HYPE.
All eight are in `universe.HELDOUT`. A known-good calibration on held-out A
is therefore in-sample for the incumbent. **Donchian calibrates on held-out
B (the later era) only.**

## Decisions (made here, with reasons)

1. **Slices.** Held-out A = `HELDOUT` symbols, bars before the cut (same
   era, unseen markets). Held-out B = discovery + held-out symbols, bars
   from the cut on (unseen era). Both come from the calendar cut already in
   `research_slices`, so B cannot leak era across symbols.
2. **One candidate spends one test.** The gate-1 p-value is the **maximum**
   of the p-values it has to clear (A consistency, B consistency, B
   common-rotation). That max is a valid p-value for the intersection
   hypothesis "it works on both", so LORD++ is charged once, not three
   times.
3. **The common-rotation null** (Task 1) shifts every symbol's entries by
   the *same* offset, which keeps cross-symbol clustering intact. Its
   statistic is the portfolio's compounded return under the live cap
   (`portfolio_curve`, 8 slots, 0.5%), because that is the quantity the
   book is judged on. It gives one percentile per candidate. With D draws
   its resolution is 1/(D+1), so the draw count follows the level being
   tested: `D = ceil(2/alpha_t) - 1`, capped at `referee_max_draws` (1000).
   If `alpha_t < 2/(cap+1)`, the look is **deferred** and nothing is spent,
   because the test could not reject anything at that level.
4. **LORD++**, target `fdr_target=0.10`, `lord_w0=0.05`, with the
   γ-sequence γ_j ∝ log(max(j,2)) / (j·e^√log j), normalised to sum to 1
   (Javanmard & Montanari 2018):
   `α_t = γ_t·W0 + (α−W0)·γ_{t−τ1} + α·Σ_{j≥2} γ_{t−τj}`,
   where τ_j is the step of the j-th rejection. The sequence lives in the
   ledger and survives restarts.
5. **The forward brake.** Once ≥ 5 machine-admitted specs have finished
   their forward window, `α_t` is halved while more than 10% of them failed.
   A window finishes at 20 live trades, or earlier if the spec is retired.
   It failed if it was decay-retired before 20 trades, or if its live PF
   over those 20 trades is below 1.0. Live P&L for this is read from the
   venue-rebased `trades.realized_pnl`, never the pre-fix rows.
6. **Choosing who gets a look.** At most `heldout_top_m=5` per (tf, geo) per
   referee round, ranked by discovery compounded return among survivors
   with `testable=1`. A candidate whose discovery entries overlap a
   previously refereed candidate's by > 0.6 is **not looked at**. It is
   recorded `twin_of=<hash>` and costs no budget. Without this, the budget
   is spent 40 times on one alt-trend regime.
7. **Gate 3 runs on held-out B.** That is the only period no one chose
   anything on. The book with the candidate must beat the book without it
   on compounded return, and its return/drawdown ratio must be no worse.
   The book is today's `paper`/`active` specs, each simulated on its own
   declared universe.
8. **The handoff is closed.** `research.handoff: false`. The kernel reads
   only candidates whose `referee_state='reason_passed'`. No code in phase 3
   writes that state; phase 4 does. Tests inject it.

## Files

- Create `trader/research/portfolio_null.py`: the common-rotation null.
- Create `trader/research/fdr.py`: LORD++ as pure functions, plus the brake.
- Create `trader/research/referee.py`: candidate selection, gate 1, gate 3.
- Modify `trader/research/evaluate.py`: `load_heldout(tf, part, …)`. The
  discovery loader keeps reading held-out bar counts only.
- Modify `trader/research/ledger.py`: tables `research_tests` and
  `research_candidates`.
- Modify `trader/research/job.py`: `referee_job`, the only child that reads
  held-out prices.
- Modify `trader/research/planner.py` and `runner.py`: a `referee` batch kind.
- Modify `trader/research/__main__.py`: `--status` shows wealth, α_t, tests
  spent and per-gate counts.
- Modify `trader/kernel.py` `_mechanism_once`: the research source, behind
  `research.handoff`.
- Modify `config.yaml` `research:`: `fdr_target`, `lord_w0`, `heldout_top_m`,
  `referee_max_draws`, `referee_min_trades`, `handoff`.
- Tests: `tests/test_research_portfolio_null.py`, `test_research_fdr.py`,
  `test_research_referee.py`, plus extensions to `test_research_runner.py`
  and `test_single_creation_path.py`.

## Tasks

### Task 1 — the common-rotation null

- [ ] Test on synthetic data: 19 random-walk symbols driven by one shared
  regime series, with a rule "long when the shared series' z > 1". The
  per-symbol `consistency_p` reads < 1e-4, and the common-rotation
  percentile reads uniform over 200 seeds (KS p > 0.05). This is the fault
  the task exists for, reproduced.
- [ ] Test: a rule with a planted per-symbol edge (entries that precede a
  real drift on each symbol independently) reads a common-rotation
  percentile ≥ 0.95 in ≥ 80% of seeds.
- [ ] Implement `portfolio_null(compiled, bundle, draws, seed) -> {percentile,
  p, actual_total_pct, null_median, draws}`. It draws one offset from
  `[WARMUP+1, n−WARMUP−1]` of the shortest common clock, applies it to every
  symbol's long/short masks, re-runs `simulate` with the same signed
  funding, and walks `portfolio_curve`. `p = (1 + #null ≥ actual) / (D + 1)`.
- [ ] Run it on today's top survivor (`bb20>p75,r_alts_z96>p90`, trail,
  discovery slice) and on Donchian's declared 16. Record both in this plan.
  **The decision point:** if Donchian cannot reach p ≤ 0.01 at 1000 draws
  on its declared 16 over its full frame, this null has no power, and
  Task 1 is redesigned before anything else is built on it.

### Task 2 — held-out slices

- [ ] Test: `load_heldout("4h", "a")` returns only `HELDOUT` symbols, every
  bar before the cut. `"b"` returns every symbol, every bar at or after the
  cut. The warmup comes from the bars *before* the cut, so a B slice does
  not start 210 bars late. Use `frames_for` on `[cut − WARMUP bars, end]`,
  and have `simulate` open trades only from the cut on.
- [ ] Test (a guard that already holds; keep it): `evaluate_job` and
  `measure_job` never call `cached_ohlcv` on a held-out symbol for price
  data after the cut.
- [ ] Implement it, including B's derivs/market frames through
  `slices.after`.

### Task 3 — LORD++ and the ledger sequence

- [ ] Test the pure functions against a hand-computed 6-step sequence: no
  rejection, rejection at t=2, rejection at t=5. Check `α_t`, the
  never-negative wealth, and the fact that `α_t ≤ α` always holds.
- [ ] Test the known-bad calibration: 2000 uniform p-values through
  `fdr.run` give realised FDR ≤ 0.10 across 200 seeds, where false
  discoveries are all rejections. A mix of 90% null and 10% p~Beta(0.1,1)
  gives FDR ≤ 0.10 and power > 0.
- [ ] Test the brake: 4 finished windows with 4 failures means brake OFF
  (fewer than 5). 5 windows with 1 failure (20% > 10%) means brake ON, α_t
  halved. 10 windows with 1 failure (10%, not above) means brake OFF.
  Assert all three exactly.
- [ ] Schema for `research_tests` (seq INTEGER PK, hash, tf, geo, gate,
  p, alpha_t, rejected, braked, at). `Ledger.next_alpha()` reads the
  sequence, and `Ledger.record_test()` appends a row in one `_tx()`.
- [ ] Test: a restart (a new `Ledger` on the same db) resumes at the same
  `α_t`.

### Task 4 — candidate selection

- [ ] `research_candidates` (hash PK, tf, geo, state, twin_of, gate1 JSON,
  gate3 JSON, referee_state, updated_at). States: `queued`, `twin`,
  `deferred`, `gate1_fail`, `gate3_fail`, `referee_passed`,
  `reason_passed` (phase 4), `handed_off`, `admitted`, `refused`.
- [ ] Test: of 10 survivors whose entries are pairwise overlapping > 0.6,
  exactly one is queued and 9 are `twin`, and `research_tests` has no rows.
- [ ] Test: `testable=0` survivors are never queued, and a combination
  that already has a candidate row is never re-queued (a repeat look would
  spend the budget twice on one question).
- [ ] Implement `referee.select(ledger, tf, geo, m)`. Overlap is Jaccard on
  discovery entry bars across symbols, the same measure
  `analyst.redundancy` uses. Compute it in the child and store it; never
  recompute it in the kernel.

### Task 5 — gate 1

- [ ] Test with a mocked bundle: A consistency p=0.001, B consistency
  p=0.002 and B common-rotation p=0.004 at α_t=0.005 gives one test row
  with p=0.004 and rejected=1, and the state becomes `gate1_pass`.
  Changing B common-rotation to 0.02 gives one row with rejected=0 and
  `gate1_fail`, with the reason naming which p failed.
- [ ] Test: fewer than `referee_min_trades` (60) on either slice, or fewer
  than `null_baseline.MIN_SYMBOLS` symbols carrying a percentile, gives
  `gate1_fail: untestable`. **This still spends a test.** The look happened
  and the held-out prices were read, so it cannot be taken back.
- [ ] Test: when α_t is below the draw cap's resolution, the state is
  `deferred`, no test row is written, and no held-out price is read (the
  check happens before the child is spawned).
- [ ] Implement `referee.gate1` inside `referee_job`.

### Task 6 — gate 3

- [ ] Test: a candidate whose B-slice fills exactly duplicate the book's
  fails, because compounded return does not rise. A candidate with
  uncorrelated positive fills passes. A candidate that raises return but
  doubles drawdown, lowering return/DD, fails.
- [ ] Implement `referee.gate3(candidate_fills, book_fills)`. Book fills are
  built in the child from `journal.list_specs(["paper","active"])`, each on
  its declared universe over B, using the same `simulate`/funding path as
  the evaluator.
- [ ] Gate 3 spends **no** FDR budget. It is a decision about the book, not
  a hypothesis test.

### Task 7 — runner, planner, child

- [ ] The planner emits a `referee` batch for (tf, geo) when it has queued
  candidates and no control is missing. Referee batches take priority over
  new growth rounds, because a queued candidate is a pending decision.
- [ ] `referee_job` handles one candidate per batch (≈1000 draws ×
  2 slices), under `batch_seconds`, niced, read-only. The kernel thread
  writes all ledger rows.
- [ ] Test: a crashed referee child writes no test row and leaves the
  candidate `queued`. An evaluation that fails **after** held-out prices
  were read (child reports `looked=true`) writes a test row with p=1.0.
- [ ] Test: the stand-down rule (last trade cycle > 45s) covers referee
  batches too.

### Task 8 — the handoff (built, closed)

- [ ] `_mechanism_once`: when `research.handoff` is true, and before the
  LLM writer runs, take the oldest `reason_passed` candidate and build its
  `StrategySpec` from `Combination.to_spec()`, with the universe set to
  discovery + held-out symbols. Pass it through **`analyst.admit(spec,
  book)`**, unchanged, and record `admitted`/`refused` with the evidence.
- [ ] `tests/test_single_creation_path.py`: assert that the research
  handoff reaches `strategies` only through `analyst.admit`, and that no
  module under `trader/research/` writes `strategies`.
- [ ] Test: with `handoff: false`, or with no `reason_passed` rows,
  `_mechanism_once` behaves byte-for-byte as today (mocked writer called,
  same events).

### Task 9 — calibration on two known answers

- [ ] **Known-good:** Donchian (`donchian_hi(100)` both ways, trail),
  seeded as a candidate on its declared 16 (15 loadable). On **B only**:
  gate 1 common-rotation p ≤ 0.01, and gate 3 against an empty book passes.
  If it fails, the referee has no power at this depth. Record that and
  stop; do not relax the gate.
- [ ] **Known-bad:** 200 random-walk symbols with 50 random 2-part
  combinations seeded through select → gate 1 → budget give zero
  rejections, or a realised FDR ≤ 0.10 across seeds.
- [ ] `scripts/backtest_equivalence.py` PASS, and
  `scripts/bench_vector_backtest.py` > 20x.

### Task 10 — measure on the real store, then deploy

- [ ] `python -m trader.research --referee --once --tf 4h` against the live
  ledger. Record: candidates queued vs twins, and for each look p(A),
  p(B), common-rotation p, α_t, gate 3 delta. **Especially record whether
  the `r_alts_z96` family survives the common-rotation null on B.**
- [ ] `--status` prints wealth, α_t, tests spent, and per-state counts.
- [ ] Full suite green apart from the known `test_macro_guard` wall-clock
  failure.
- [ ] Update CLAUDE.md's "The search" section with the referee, the
  dependence finding, and the measured numbers. Keep `handoff: false`.
- [ ] Restart the kernel (`./restart.sh kernel`) and confirm a referee batch
  completes in `research_batches`.

## Risks

- **Budget exhaustion before the first real look.** LORD++ spends fastest
  early. Twin-dedupe (Decision 6) and the untestable pre-check exist to
  keep junk from spending it. If α_t drops below the draw cap's resolution
  with no rejection, the pipeline defers forever. `--status` must say so
  plainly rather than read as "working".
- **Held-out B is one era**, about 18 months at 4h and mostly one market
  regime. A regime-timing rule can pass B because B happened to trend. The
  forward brake is the only control that survives that.
- **The common-rotation null may be too weak** for per-symbol mechanisms.
  It keeps the market's clustering, so a rule that merely trades more
  during volatile eras is compared against itself. Task 1's decision point
  on Donchian settles that before anything depends on it.

## Execution record (2026-09-14)

Tasks 1–8 are built and committed (`39b2b1b`, `8925756`, `a7dd560`). Task 9
hit its stop rule, and Task 10 was deployed with `referee: false`.

### Changes from the plan, each for a measured reason

- **Exact draws, not an extrapolated tail.** LORD++ levels are tiny: α₁ =
  0.0025, and α₂ ≈ 0.0005 with no rejection. An empirical p from 199 draws
  bottoms out at 0.005. A normal fit on log growth was tried and refused:
  under no edge it reads P(p ≤ 1e-4) = 0.0036 with a heavy-tailed null,
  36× the nominal rate. Instead, one trade is now `vector_backtest._trade()`,
  shared by `simulate` and a per-bar `trade_table`, and `walk_table`
  reproduces `simulate` exactly (tested on both geometries with
  NaN-gapped funding). A draw costs about 21 ms at 4h over 19 symbols, so
  19,999 draws fit one child batch. Equivalence PASS, bench PASS.
- **Held-out B starts at the later of the recorded and recomputed cut.**
  `load_bundle` recomputes the cut every batch and it drifts forward as the
  store grows (recorded 2025-03-08, recomputed 2025-03-09).
- **The referee sits in `runner.py`, not `planner.py`.** The planner's
  contract is that it never reads a held-out result.

### Task 1 — measured

| input | slice | trades | actual | null median | common-rotation p |
|---|---|---|---|---|---|
| Donchian, declared set (16 legs) | full frame | 1,725 | +195% | −35% | 0.005 (199 draws, beats all) |
| `bb20>p75,r_alts_z96>p90`, trail | discovery | 1,689 | +214% | −33% | 5.0e-04 (1,999 draws, beats all) |

The alt-index survivor is not *only* the 19-votes artefact. It beats a null
that keeps its cross-symbol clustering. It has not been looked at on
held-out data, and must not be outside the budget.

### Task 9 — STOP: the gate cannot see the incumbent on B

Donchian on held-out B: 15 legs (HYPE has no pre-cut context), 541 trades,
cut 2025-03-09.

| reading | p |
|---|---|
| per-symbol consistency | 9.2e-05 |
| common rotation, capped compounded return | **0.0485** |
| common rotation, mean R | 0.057 |
| common rotation, sum R uncapped | 0.075 |
| gate 3 vs empty book | pass (+65.7%, maxDD 15.1%) |

On discovery (same 15 legs) the same three statistics read 0.0015, 0.024
and 0.032. Compounded return is the most powerful of them, so the weakness
is not the choice of statistic.

**What it means.** Once every symbol's entries move together, the evidence
for Donchian over 18 months is about p=0.05. Crypto trend-following over
these markets is largely one bet on a few market-wide trend episodes. The
per-symbol p of 9e-05 counts those episodes 15 times over, which is the same
dependence the phase was written to catch, now found in the incumbent. That
agrees with CLAUDE.md's standing verdict that the prior on Donchian "should
be lower than it was".

**Consequence.** As built, gate 1 (p = max of three, charged to LORD++)
cannot admit the known-good rule: 0.0485 against α₁ = 0.0025. Switching the
referee on would spend the budget on looks that cannot succeed. Per this
plan's own rule, the gate was not relaxed. The known-bad calibration waits
on the gate decision, because it has to calibrate whichever gate is chosen.

### Open decision (operator)

1. **Keep the dependence-respecting gate, and accept that it admits
   nothing.** The honest reading is that no crypto mechanism, the incumbent
   included, is established at FDR 10% over one 18-month era. The referee
   stays off, or runs purely to record.
2. **Consistency p carries the LORD++ test; the common rotation is a fixed
   filter** (for example p ≤ 0.10). This is calibrated on the known-good:
   Donchian passes (9e-05 ≤ 0.0025, 0.049 ≤ 0.10). It is weaker: the FDR
   claim rests on a p that overstates evidence under cross-symbol
   dependence, and the filter only removes pure regime artefacts.
3. **Correct consistency p for dependence** by estimating the effective
   number of independent symbols (from cross-symbol correlation of trade
   outcomes), and charge that. This is the principled middle, but it is new
   statistics and must itself be calibrated on the shared-regime no-edge
   case and on Donchian before use.
