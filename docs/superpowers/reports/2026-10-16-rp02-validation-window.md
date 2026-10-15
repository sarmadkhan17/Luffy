# RP-02 — predictive validation window

Baseline: unified candidate `6bc0f4c` + RP-03 (`2e44748`). Offline, synthetic evidence only. No protected
held-out read, Referee run, alpha spend, provider call or production write.

## Requirement trace (all from existing constants)

| requirement | source | value |
|---|---|---|
| engineering warmup | `vector_backtest.WARMUP` | 210 bars |
| slice floor | `slices.MIN_SLICE_BARS = WARMUP + 210` | 420 bars |
| calendar split | `slices.DISCOVERY_FRAC` | 70 / 30 |
| **1,400 bars** | 420 protected-B bars / 0.30 | 233.3 days of 4h |
| 210-bar | warmup, and the room-to-trade half of 420 | engineering |
| trade counts | `MIN_SYMBOL_TRADES 8`, `MIN_PROJECTED_TRADES 8`, `referee.MIN_TRADES 60` | statistical |
| market counts | `min_discovery_symbols 16`, `MIN_PROJECTED_MARKETS 4`, `null_baseline.MIN_SYMBOLS 4` | statistical |
| alpha budget | LORD++ `fdr_target 0.10`, `w0 0.05` -> alpha_1 = 0.0025 | budget |
| prospective independence | window starts after `generation_end`, cut frozen at registration, B after cut | protocol |

## Findings

1. **Duplicated floor (not changed).** Held-out B is judged with `B_CONTEXT_BARS` of pre-cut context, so the referee
   itself needs only 210 scored B bars, yet registration (`translate`) and discovery counts require 420 and the
   projection subtracts WARMUP again. Removing it would shorten 1,400 -> 700 bars. That changes the null's support
   and statistical identity, so it needs owner authority; it is not implemented.
2. **Proven defect — insufficient, not excessive.** The referee's rotation range is `[WARMUP+1, n-WARMUP-1]`.
   At 420 B bars it is empty (Gate 1 untestable, p=1.0 charged, protected B consumed). At 423 bars it holds one
   offset repeated for every draw: a no-edge fixture read p=2.7e-5. `null_baseline.assess` already refuses a
   null with fewer distinct offsets than draws; `referee.consistency` and `portfolio_null.common_rotation` did not.
   Exact calculation: with D distinct offsets the test size at alpha is >= 1/(D+1) (D=28: 6.7% at nominal 5%;
   D=1: 50%). 233 days therefore cannot support the locked referee.
3. **Locked-constant minimum.** `common_rotation` never runs below 199 draws, a null needs >= draws distinct
   offsets, margins are 2x211: B >= 621 bars = **2,070 total bars = 345 days**. This follows from existing
   constants; no new statistical authority.
4. **Beyond the minimum (owner policy).** Resolving alpha_1 = 0.0025 needs 399 distinct offsets: B >= 821,
   total 2,737 bars (456 days); later LORD++ levels need more. The look is DEFERRED (existing rule) until the
   frozen window can resolve it.
5. **Power** (baseline referee, 10 synthetic symbols, 30% informed entries, 40 runs each): rejection at 0.05 is
   25-48% for B 450-980 bars and 80% at 1,400; no-edge rejection <= 1/40 at B >= 480. Trades, not offsets, drive
   power. Raw rotation p<=0.005 under no edge: 2/40 at 450 bars (D=28), 0 at B>=560. Dependence deflation held:
   median mean-rho ~0 here, and the dependence-corrected consistency never rejected under no edge at B>=480.
   (`2026-10-16-rp02-synthetic-power.json`)

## Corrections (implementation inconsistencies with the locked rules)

- `portfolio_null.rotation_offsets`; `common_rotation` and `referee.consistency` refuse a null narrower than
  its draws (UNTESTED, same rule as `null_baseline.assess`).
- `referee.examine` counts bars in SQL before loading any price (`rotation_capacity`) and returns
  `deferred` (no look, no LORD++ step, held-out unspent); the runner records candidate state `deferred`.
  Draws are fitted to `min(referee_max_draws, offsets)`.
- `predictive_experiment.translate` returns `DATA_INSUFFICIENT/referee_rotation_floor` until every leg the
  referee would load carries >= 621 protected bars. `evidence_policy` is unchanged, so every previously
  registered experiment still validates and replays.

## Not changed

Warmup, 70/30 split, trade/market floors, LORD++ budget, draws floors, frozen cuts, preregistration,
identities, RP-03 handoff authority. Spent looks stay spent; no statistic was relabelled.
