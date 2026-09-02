<!-- refreshed: 2026-09-03 -->
# Codebase Concerns

**Analysis Date:** 2026-09-03

## Critical Open Issues

### Strategy Generalization Failure

**Issue:** The only live strategy (Donchian Breakout Trail) does not generalize beyond its hand-picked universe.

**Files:** `trader/strategy/spec_evidence.py`, `trader/brain/analyst.py`, `scripts/screen_mechanisms.py`

**Evidence:**
- Declared 16-symbol universe: median PF 1.42, consistency p=3.5e-04, compounds at ~17%/yr
- 19 comparable symbols never scored: median PF 0.93, p=8.8e-01, compounds at -4.6%/yr
- Declared set sits in top 0.8% of 4000 random 15-symbol draws by consistency p
- Gap is stable across time splits, not a data artifact

**Root cause:** Both hypotheses predict this result—"works on these markets" and "was fitted to these markets" are indistinguishable in backtests.

**Impact:** All evidence of edge is backtest-only; zero live trades taken as of 2026-09-03. Forward performance is the only remaining discriminator, and the prior should be lower than reported.

**What works:** No forward performance data yet exists; only live trading will settle this.

---

### Derivative Data Coverage Gaps

**Issue:** Funding and basis data do not cover the full historical frame needed for strategy validation.

**Files:** `trader/data/derivatives.py`, `trader/core/journal.py` (see strategy validation flows)

**Details:**
- Funding: 4-5 years, 32 symbols, but only ~80% coverage of each 4h frame
- Basis: 2 years, 29 symbols, only ~80% coverage
- OI/taker/long-short: ~32 days, 5 symbols — below the 75-day floor for scoring
- Store starts 2022-08-31; candle history reaches back to 2021-08-26
- Earlier bars pay flat conservative `abs(funding_8h)` (not real signed series)
- HYPE has no Binance spot pair, so funding but no basis

**Impact:** Any spec built on OI, taker_ratio, or ls_ratio cannot be scored (will report UNTESTED), only refused. Cannot unlock deeper open-interest discovery without `COINALYZE_API_KEY`.

**Fix approach:** Set `COINALYZE_API_KEY` in `.env` to deep-read OI/taker/ls_ratio history beyond Binance's 30-day public cap.

---

### Closed-Bar Signal Persistence

**Issue:** A closed-bar signal remains valid for the entire life of its bar, so an entry blocked by risk can fill hours after the bar closes.

**Files:** `trader/strategy/compile.py` (lines 125-140), `trader/engine/orchestrator.py`

**Context:** Fixed the fabrication of partial bars (2026-09-02), but signals now carry latency.

**Current state:** `signal_bar_age_min` field was added to track age and allow measurement of the right cutoff, but cutoff enforcement has not been implemented.

**Impact:** A 4h signal generated at close can re-fire at risk-pass 3 hours later, filling at a different price than the backtest. Systematically biases live fill price relative to backtest.

**Fix approach:** Measure actual signal-to-fill delay cost in live fills; decide whether to enforce signal age cutoff or bake delay into backtest.

---

### Executor P&L Computation

**Issue:** Realized P&L is computed from the fee constant, not the venue's actual commission.

**Files:** `trader/engine/executor.py:250-301`

**What happens:** 
```python
fees = self.taker_fee * (amount * entry + amount * fill)
pnl = gross - fees
```

**Why it matters:** The fee constant is now correct (0.04% measured, 0.05% published), so the error is small. But the venue returns the exact `commission` field on every fill, which is free and exact.

**Workaround:** `scripts/reconcile.venue_realized_pnl()` reads the venue's commission correctly; used in reconciliation only.

**Fix approach:** Replace constant-based calculation with venue commission field on every fill. Small magnitude but systematic.

---

### Legacy Promotion System

**Issue:** `promotion.py` still applies lifetime profit factor gates to legacy genomes.

**Files:** `trader/strategy/promotion.py`

**Current state:** All legacy genomes are retired, so this currently affects nothing.

**Risk:** If a legacy genome is ever un-retired or if new genomes enter the legacy pipeline, lifetime PF becomes the gate. This contradicts the system philosophy of "is this working NOW", not "did this survive five years".

**What's correct:** The Analyst's rolling gates in `spec_evidence.py` own the specs (StrategySpec objects). Keep the systems separate.

---

### Missing Researcher Agent

**Issue:** The Researcher agent does not exist; `research`-stream ideas queue up unconsumed.

**Files:** `trader/brain/ideas.py`, `trader/brain/strategist.py`

**Current state:** 
- Scraper queues items under `strategy` stream (consumed by Strategist) and `research` stream
- Consumption is per-stream, so Strategist spending an item does not destroy it for research
- `MIN_TEXT = 80` is the quality floor
- No agent reads the `research` stream

**Impact:** Research ideas accumulate in database but are not processed. Does not block strategy creation (Strategist reads only `strategy` stream) but research side is dead.

---

### Daily Bars Cannot Be Used

**Issue:** 1-day timeframe does not generalize at all; exploration is closed.

**Files:** `trader/strategy/vector_backtest.py` (line 656 onwards)

**What happened:** Rebuilt daily bars from 4h (6 UTC bars per day, partial days dropped, verified against venue). Ran the validated rule across 1d timeframe with same gates.

**Results:**
- Donchian(100) at 1d: only 2-7 test trades per symbol, most unscored (silence, not failure)
- Donchian(20) at 1d: p=1.0e+00, 1.6%/yr declared compound return (vs 16.8% at 4h)
- Gap is stable: 1d does NOT separate declared/undeclared symbols; both collapse

**Implication:** 1d offers no edge to generalize with. Further work on higher timeframes is possible but unprofitable to pursue.

---

### WARMUP Discards Bars Silently

**Issue:** `vector_backtest.WARMUP = 210` is a BAR COUNT, not a duration. On 1d it discards 38% of test data.

**Files:** `trader/strategy/vector_backtest.py` (line 656)

**Details:**
- At 4h: 210 bars = 35 days, harmless inside 365-day windows
- At 1d: 210 bars = 210 days, cuts into a 550-bar test half (38% loss)
- `simulate()` skips first 210 bars of EVERY slice, including test half
- Rotation null offsets are drawn from `[WARMUP+1, n-WARMUP-1]`, so at 1d only 129 offsets remain for 60 draws
- Any future higher-timeframe work through this engine silently loses 210 bars per slice

**Related issue:** `backtest.resample()` uses `closed="right", label="right"`, which is wrong against the store's open-time labels. Do not use it to build higher timeframes.

**Status:** Unfixed; not a fault at 4h or below.

---

### Feature Registry vs. Mechanism List

**Issue:** 71 features are registered, but the search mechanism list is the practical ceiling, not the registry.

**Files:** `trader/strategy/features.py`, `trader/strategy/features_deriv.py`, `trader/strategy/features_xs.py`, `scripts/screen_mechanisms.py`

**What's registered but never screened:** 
- `efficiency_ratio`, `corr_btc`, `vol_of_vol`, `streak`, `bars_since`, `swing_high/low`
- Wick/body shape family
- `rel_volume`

**What the DSL genuinely CANNOT express** (verified, not assumed):
- **Order-book/microstructure:** No book history is stored anywhere, so the `depth` analyst's domain is unbacktestable
- **OI/long-short ratio:** Only ~32 days of history over 5 symbols; Binance caps at 30 days. `COINALYZE_API_KEY` is the only unlock
- **Multi-leg construction:** `StrategySpec` is one symbol, one direction, one exit; `xs_rank` can select but never hedge
- **Cross-asset context:** Beyond BTC; no multi-symbol correlation in feature domain
- **Event time:** MacroGuard holds the calendar; DSL cannot see it
- **Position state:** Entries are stateless boolean arrays

**What is NOT a gap:** Flow is not a gap—`taker_buy` rides in klines with full history.

---

### Journal Concurrency Model

**Issue:** `Journal.query()` does not commit; reads are on thread-local connections with no transaction wrapper.

**Files:** `trader/core/journal.py:206-214`

**Details:**
```python
def query(self, sql: str, params=()) -> list:
    conn = self._conn()  # thread-local, no transaction
    return conn.execute(sql, params).fetchall()  # may hold write lock
```

**What happens:** An INSERT/UPDATE through `query()` stays uncommitted—invisible to other connections and holding a write lock—until some later `_tx()` on the same thread commits it.

**Rule:** Always use `_tx()` for writes. Use `query()` for reads only.

**Files relying on this:** All brain threads and the kernel's main loop (they each have thread-local connections).

---

### Analyst Weights Historical Pseudo-Replication

**Issue:** Analyst outcome measurements were pseudo-replicated, overstating negative significance.

**Files:** `trader/agents/validate.py`, `trader/agents/calibration.py`

**What happened:** The kernel writes a decision every 60s. One analyst view persists, creating 30+ near-identical `outcomes` rows graded independently.

**Example:** 30 AAVE rows were a single SELL view from 22:32-23:01 on 2026-08-24, one per cycle, same forward return.

**De-duplication results (224 rows → 143 independent episodes):**
- All: -0.545%, t=-2.36 (vs -0.737%, t=-3.98 when counting duplicates)
- BUY: -0.757%, t=-2.30 (weakly significant)
- SELL: -0.151%, t=-0.63 (noise)

**Status:** Fixed in theory; `agents/validate.py` replays over candles (not outcomes), so live weighting path is unaffected. But any NEW measurement off `decisions`/`outcomes` must cluster by episode first.

**Current state:** Analysts are no longer set the score; only journalled and graded for historical measurement.

---

### Data Fabrication (Closed 2026-09-02)

**Issue:** The candle store was serving partially-formed bars as closed bars.

**Files:** `trader/data/feed.py`, `trader/core/types.closed_bars`, `scripts/repair_partial_bars.py`

**What happened:**
1. `fetch_ohlcv` extended the store with `since = last + tf_ms`, but `_merge_save` had already written the forming bar
2. Frozen bar had truncated high/low/close (4-16% volume variance)
3. Example: UNI/USDT stored `6.298/6.307/6.260/6.304` vs real `6.298/6.373/5.692/5.742`
4. `to_evaluator` read the last (forming) bar, so live rule was "price is beyond the level RIGHT NOW" while backtest rule was "bar CLOSED beyond it"

**Impact:** 104 of 123 (symbol, timeframe) tails disagreed with the venue on 2026-09-02. Donchian appeared to signal a breakout on UNI that fell 9% (high was fabricated).

**Fixed:** 
- `core.types.closed_bars` is the single definition of closed
- Store persists only closed bars
- Re-reads tail on every incremental fetch (`STORE_OVERLAP_BARS`)
- `scripts/repair_partial_bars.py --check` repaired 997 bars; now reports zero

---

### Uncancellable Stops Were Invisible

**Issue:** Three separate live faults were invisible from inside the system.

**Files:** `trader/engine/protective.py`, `scripts/monitor.py`

**What happened:**
1. 24 uncancellable stops (Binance `-2011` on every attempt)
2. R multiples of 83 (stop ratcheted mid-position, shrinking denominator)
3. Half-closed position still charging full heat cap

**Why invisible:** The journal knows only its own state; the venue knows the truth. `fetch_open_orders()` returns zero stops (they live under `/fapi/v1/algo/*`, not `/fapi/v1/order`).

**Tool:** `scripts/monitor.py` queries the exchange directly; use it to audit live state.

**Fix:** Every stop operation goes through `trader/engine/protective.py`, never ccxt directly.

---

### Dust Position From Retired Genome

**Issue (Closed 2026-09-02 18:48):** A $5.74 naked position from a retired genome blocked UNI/USDT.

**Files:** `trader/engine/reconcile.py`

**What happened:**
- Trailing stop filled 302.87 of 303.87 coins; 1.0 residue remained
- Reconcile reported `naked: N` at ERROR but placed nothing
- Reason recorded—residue under venue minimum—was wrong
- UNI filters: `MIN_NOTIONAL 5`, `LOT_SIZE minQty 1 step 1`; residue is $5.74 on 1.0 coin (both clear)
- `min_notional_usdt: 10` in `config.yaml` is OUR sizing floor, not the venue's; two were conflated

**Lucky escape:** While naked, UNI read as past its Donchian break on the fabricated bar. On repaired data it was 11.23% away.

**Fixed:** 
- Reconcile now RE-ARMS a naked position (not just reports it)
- Sized from venue's actual contracts, not journal's amount
- Stops already through their level counted separately as `unarmable` (logged as needing close)

---

### Slippage Model Was Overstated (Fixed)

**Issue:** Backtest slippage was over-charging by 3-5x.

**Files:** `trader/core/config.py`, `trader/strategy/vector_backtest.py`

**What was wrong:**
```
amount = risk / (2*ATR)  # ATR cancels out
slippage_cost = slippage_atr_frac * 2 * amount * price
```
This is a flat tax of `2 * frac` on risk staked, not a volatility-scaled cost. A 0.06 value charged **12% of risk every round trip on every symbol**.

**Measured reality (199 fills, 21 symbols, all taker, all USDT):**
- Fees: Volume-weighted **4.078 bps**; config charged 0.05. Updated to 0.04
- Slippage: Of 56 orders ≥$1,000, 41 filled at a single price (median intra-order 0.00 bps)
- Orderbook walk: 0.65 bps median to fill $1,200; model charged 8.6-20.5 bps/fill
- Slippage model: Set to 0.015 (roughly 4 bps at median ATR), still 6x measured to allow for volatility expansion at entry

**Fixed:** 
- Fee: 0.05% → 0.04%
- Slippage: Tuned to measured reality

**Impact:** consistency_p stays between 4.6e-05 and 1.1e-04 across entire cost range (rotation null pays same costs), so cost accuracy doesn't create/destroy edge. What moves: mean R +0.076 → +0.134; compound return 17.2% → ~25%/yr at LOWER drawdown (27.3% → ~24%).

**Still open:** `executor.close()` computes fees from constant, not venue's `commission` field. Magnitude is small now but should be exact.

---

## Performance Bottlenecks & Scaling Limits

### Signal-to-Fill Delay Cost (Unmeasured)

**Issue:** Backtest fills at 4h close; live fills at best 60s later (hours later if risk blocks).

**Files:** `trader/strategy/compile.py` (signal_bar_age_min), `trader/engine/executor.py`

**Impact:** Unmeasured. Backtest fills at printed close; live fills at actual market one or more bars later. Breaks replay equivalence.

**Fix approach:** Use `signal_bar_age_min` to measure and quantify the delay cost on real fills, then decide on enforcement policy or backtest adjustment.

---

### Position Sizing Fragility

**Issue:** Risk-based sizing can lead to very large notional in low-volatility markets.

**Files:** `trader/engine/risk.py` (lines 1-80)

**Details:**
- Amount = risk / (2*ATR); in low-vol markets ATR shrinks, amount grows
- A 0.4% stop floor caused a $23 budget to become $5,750 notional (25% of account in one position)
- 8 simultaneous 1%-risk positions in correlated markets = one 18%-risk position
- Max 8 open trades at 0.5% risk each creates portfolio risk, not individual limits

**Mitigation in place:** Max position margin cap (`max_position_margin_pct`, default 20%), max total margin cap (default 70%). Still fragile if correlated markets spike concurrently.

**Validation:** `scripts/deployment_frontier.py` walks 8-slot cap against 1724 real fills; shows signal-rich, capital-poor book (8 slots refuses 17% of signals; 4 slots refuses 46%).

---

### Tree Search Explored Fully (No Second Edge Found)

**Issue:** Comprehensive mechanism screen found no second edge.

**Files:** `scripts/screen_mechanisms.py`

**What was run:** 
- 38 mechanisms × 2 geometries
- 19 discovery symbols + 17 held-out
- Cross-sectional, carry, basis, BTC-relative, volatility-regime families included

**Result:** Two Donchian variants were closest:
- `carry_break`: discovery p=5.9e-02, held-out PF 1.47, p=1.3e-02 (fails discovery gate)
- `break_hivol`: discovery p=2.9e-02, held-out PF 1.55, p=3.2e-02 (fails held-out gate)

Both are the same mechanism the book already trades, and paired filter tests show neither is worth adding (removing 15% of trades removed >50% of compounded return).

---

### No Filter Improves the Mechanism

**Issue:** Adding conditions to the validated rule decreases compounded return despite improving profit factor.

**Files:** `trader/strategy/spec_evidence.py`

**Example (16 declared symbols, same geometry):**

| variant | PF | null p | OOS trades | CAGR | maxDD |
|---------|------|----------|------------|------|-------|
| baseline | 1.42 | 3.5e-04 | 487 | 17.3% | 27.3% |
| carry filter | 1.43 | 3.5e-04 | 415 | 7.6% | 25.8% |
| hivol filter | 1.44 | 1.1e-02 | 307 | 6.3% | 25.0% |

Removing 15% of trades removed >50% of compounded return. Filtered trades carry disproportionate share of fat right tail (individually near break-even).

**Lesson:** Judge refinements on compounded return over one account, never on a ratio that improves while trade count falls.

---

## Data Quality Issues (Historical)

### Candle Store Was Simulator Data

**Issue:** Pre-2026-09-02 candles came from Binance Demo, whose volume is simulated (15x inflated).

**Files:** `data/candles.db`

**Details:**
- Prices track to ~0.04% accuracy
- Volume is 15x inflated
- All backtests before 2026-09-02 are invalid

**Status:** Fixed 2026-09-02; store rebuilt from production. Old simulator store kept as `candles.demo-backup-*.db`.

**Impact:** Every backtest result before 2026-09-02 is void. Only live performance since the fix is valid evidence.

---

### Market Normalization Inconsistency

**Issue:** The candle store previously split the same market's history across two keys.

**Files:** `data/candles.db`

**Example:** `XAU/USDT:USDT` was stored under both `XAU/USDT:USDT` and `XAU/USDT`.

**Status:** Fixed; store normalizes to `XAU/USDT`.

---

## Test Coverage Gaps

### Analyst System Undertested

**Issue:** Seven analyst agents (structure, momentum, value, rotation, flow, positioning, depth) are still evaluated and journalled but no longer set the score.

**Files:** `trader/agents/`, `trader/engine/orchestrator.py`

**Current role:** Journalled for historical measurement and calibration only. Strategy signals now set the score directly.

**Gap:** The analysts remain in the codebase for historical accountability, but they were measured with pseudo-replicated data before de-duplication. No forward-looking human has validated them independently of outcome data.

**Risk:** If a future change re-enables analyst signals in the score, they are untested on clean data.

---

### Cross-Asset Features Unimplemented

**Issue:** Several feature families referenced in documentation are not implemented.

**Files:** `trader/strategy/features.py`

**Missing:**
- Multi-symbol correlation (beyond BTC beta)
- Cross-asset hedging logic
- Calendar-aware entry/exit (MacroGuard knows the calendar; DSL does not)

**Impact:** Strategies cannot express multi-leg or event-driven ideas.

---

## Configuration & Deployment Issues

### Production Fees Unmeasured

**Issue:** Binance production taker fees are not confirmed against actual API rate.

**Files:** `trader/core/config.py` (fee constant), `trader/engine/risk.py`

**Current state:** Set to 0.04% (measured from 199 fills on demo). Binance published VIP0 is 0.05%.

**Risk:** If production tiering is different (e.g., VIP3+ with volume rebates), the model understates costs.

**Fix:** Call `fapiPrivateGetCommissionRate` on production account (demo key answers -2015). Do this before running with `BINANCE_DEMO=false`.

---

### Cost Model Assumptions

**Issue:** Slippage model still assumes 6x measured cost deliberately.

**Files:** `trader/strategy/vector_backtest.py`

**Reason:** Spreads widen during volatility expansion at breakout entry. This is NOT measured by static orderbook walks.

**Risk:** If actual volatility-expansion slippage is <4 bps (measured assumption) rather than the model's 15 bps, strategy is more profitable than estimated but validation gate is tighter.

**Mitigating measure:** Do not cut slippage further without measuring it during live volatility spikes.

---

## Architectural Constraints & Caveats

### Single Strategy Book

**Issue:** Only one strategy (Donchian Breakout Trail) is in production, and it has not traded live.

**Files:** `data/luffy.db` (strategies table)

**Status:** Zero live trades taken. All signals on 4h since geometry fix (2026-09-02) but risk/macro guards block every entry as of 2026-09-03.

**Implication:** The system's edge is entirely theoretical until first live trade fills.

---

### Macroguard & News Guard Hard Blocks

**Issue:** Macro and news risk gates can block ALL entries indefinitely.

**Files:** `trader/agents/macro_guard.py`, `trader/agents/news_guard.py`, `trader/kernel.py`

**Current behavior:** When macro headlines churn or drawdown is high, the orchestrator's score threshold rises to near-infinity, blocking entries.

**Risk:** During market crisis, news/macro guards become a full trading halt. Intentional, but unvalidated under live stress.

---

## Invariants That Must Be Maintained

**Do not break these:**

1. `scripts/backtest_equivalence.py` must stay PASS
2. `scripts/bench_vector_backtest.py` must stay >20x
3. `spec_evidence` reports UNTESTED (never a score) when data is absent or short
4. Backtests read `DataFeed.cached_ohlcv()`, never `fetch_ohlcv(limit=20000)`
5. `orchestrator.py`'s strategy-eligibility gate is load-bearing; specs register under `f"spec:{id}"`
6. `trader/brain/scraper.py` imports queue as `from . import ideas`; never shadow it locally
7. `core.types.closed_bars` is the single definition of "bar has closed" for both store and live evaluator
8. Only STOP_MARKET orders go on the exchange; target and trail are in-process only
9. Journal.query() is read-only; use ._tx() for writes
10. One strategy-creation path only: Scraper → Strategist → Analyst

---

*Concerns audit: 2026-09-03*
