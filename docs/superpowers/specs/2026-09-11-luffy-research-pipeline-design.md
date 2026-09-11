# The research pipeline — discover, explain, prove, repeat

**Status:** design approved 2026-09-11 (Sarmad + Claude). Choices are marked
DECIDED with who made them, so a later reader can tell a decision from an
assumption. Where the concrete form here differs from what was said in the
conversation, it says so.

## Why this exists

Sarmad's goal: Luffy should trade like a professional — technical and
fundamental, across horizons, reading money flow and several markets at once.
His framing of the problem is the design's centre:

> "We have several puzzle pieces that are useless alone but together they
> create the best art."

So the product is not more pieces. It is the thing that **combines pieces,
compares every combination on one yardstick, and keeps only what pays.**

- DECIDED (Sarmad) — pieces combine at **both layers**: inside a trade (an
  idea may need several conditions at once) and across trades (a book of
  ideas, funded by what adds return).
- DECIDED (Sarmad) — pieces include **market-wide gauges** (BTC dominance,
  USDT dominance, market cap, the S&P 500, …), the usual indicators applied
  to them (EMAs, MAs, …), and the **relationships** between them — "one going
  up while another goes down".
- DECIDED (Sarmad) — **the machine finds combinations too**, not only
  reasoned ones; the combinations it finds are then reasoned about.
- DECIDED (Sarmad) — success is **a steady idea pipeline that searches
  continuously** and keeps re-proving what it found.

### What measurement already says, and why it shapes this

Recorded in CLAUDE.md, not re-litigated here:

- Every opinion-voting analyst measured as a net anti-edge, and **blending
  their votes buried the one strategy that works** for a week. Intelligence
  here means more hypotheses tested honestly, never more voices averaged.
- **Adding "confirming" filters to Donchian halved its compounded return**
  (17.3% → 6-9%/yr) while PF held. A piece must *earn* its place, measured
  on compounded return, not on a ratio.
- **A search over many combinations manufactures winners.** At the admission
  gate's p < 0.01, 1,000 combinations with no edge still pass ~10. The design
  counts every look at held-out data and prices it.
- **Discovery evidence describes the symbols it was found on**
  (`momo_persist`: p=1.1e-03 on discovery, 0.27 on unseen symbols). Nothing
  is admitted on discovery evidence alone.
- **A gate that cannot see the incumbent inside a window cannot refute a
  challenger inside it** (the positioning screen, 2026-09-11). Every search
  window carries its own control.

## Part 1 — architecture

- DECIDED (Sarmad; Claude recommended a separate process) — **hosted inside
  the kernel.** A new daemon thread, `research`, sits beside
  `strategy-mechanism`. It owns scheduling and budgets and does no
  number-crunching itself.
- DECIDED (Claude, within Sarmad's choice) — each batch runs in a
  **kernel-spawned child process** at `nice 19` with a wall-clock limit. The
  kernel's pandas work holds the GIL, so a CPU-heavy search thread would
  slow the trade loop directly, and a crash in it would take trading down.
  The child reads the candle, derivatives and reference stores **read-only**
  and returns results over a pipe. **It never writes the database**; the
  thread writes through `_tx()`, so the kernel remains the only writer of
  truth.
- DECIDED (Sarmad) — **machine-found candidates go straight to
  `analyst.admit()`** inside `_mechanism_once`, carrying their exact rule and
  their evidence. The Strategist's LLM is not asked to re-type a rule it was
  handed. The invariant becomes **"one admission gate, many proposers"**:
  nothing writes the `strategies` table except `_mechanism_once` after
  `admit()` returns ok, and `tests/test_single_creation_path.py` is extended
  to guard exactly that.

```
research thread (kernel)                   search child (nice 19, time-boxed)
────────────────────────                   ──────────────────────────────────
next batch from the search plan    ──▶     DISCOVER on D × T_early; count tries
                                   ◀──     survivors + try count
top-M to held-out                  ──▶     GATE 1 held-out; GATE 3 adds-to-book
                                   ◀──     verdicts
REASON (LLM, research token slice)
                                   ──▶     GATE 2 test the predictions
                                   ◀──     verdicts
queue candidate (rule + evidence + thesis)
_mechanism_once → analyst.admit() → GATE 4 → paper (demo) → forward
LEDGER: every combination, every look, every verdict, pass or fail
```

## Part 2 — the pieces

### `ref(key, expr)` — any indicator, on any reference

One new DSL marker, special-cased in `dsl.py` exactly as `htf(tf, expr)` is:
the inner expression is evaluated on the reference instrument's frame and
aligned onto the base index with the same point-in-time rule
(`searchsorted(close_times, bar_close, side="right") - 1`). The whole
56-indicator OHLCV vocabulary then applies to every reference, with no
per-gauge features:

```
ref("btcdom", slope(close, 20)) < 0 and ref("alts", close > ema(50))
ref("stables", ret(30)) > 0.02 and ref("spx", close < ema(100))
ret(24) - ref("eth", ret(24)) < -0.05
```

`FeatureCtx.market` — plumbed through `compile`, `vector_walk_forward` and
`null_baseline` since the feature layer was built, read by nothing and
populated by nothing — carries the reference frames. `data_requires` gains
`ref:<key>` entries so `spec_evidence.missing_data` reports a thin reference
as UNTESTED, never a score, and `tests/test_series_map_covers_registry.py`
extends to cover them.

### The reference store

References live in `data/candles.db` under the key namespace `ref:<key>`,
closed bars only, through the same store code (`core.types.closed_bars`,
`STORE_OVERLAP_BARS`).

| key | source | native bars | depth |
|---|---|---|---|
| `btcdom` | Binance `BTCDOM/USDT` index klines | 4h | since 2021-06 |
| `alts` | computed: equal-weight index of the stored crypto perps, ex-BTC, ex-stablecoins | 4h | 5y |
| `stables` | DefiLlama `stablecoincharts/all` (total circulating USD) | 1d, close only | since 2017-11 |
| `spx` `dxy` `gold` `us10y` `vix` `oil` | Yahoo chart API | 1h (2y) and 1d (10y) | as stated |
| `btc` `eth` … | the traded crypto perps already stored | 4h/1h/15m | 5y/3y/1y |
| `cg_btc_d` `cg_usdt_d` `cg_total` `cg_total2` | CoinGecko `/global`, **recorded from 2026-09-11** every 4h, plus a 365-day daily backfill of per-coin market caps | 4h/1d | grows; UNTESTED until it spans a frame |

Measured 2026-09-11: CoinGecko's global market-cap chart is PRO-only (401),
and the free per-coin history stops at 365 days, daily — one regime. That
is why `btcdom`, `alts` and `stables` exist: each has years of free depth.

### Rules that keep a reference from lying

- **A bar exists only once it has closed, in the source's own clock.** Yahoo
  stamps an S&P daily bar at the 13:30 UTC *open*; its close is known at
  20:00 UTC. Every source declares its close offset; aligning on the stamp
  would let every S&P signal see 6.5 hours ahead.
- **Missing is NaN.** Weekends and holidays legitimately carry the last
  close — that value was known. A reference silent for longer than its
  declared `max_stale` (default 3 days for daily, 6 hours for hourly) reads
  NaN.
- **Close-only series** (`stables`) have no high/low; indicators needing them
  return NaN, not an exception.
- **Coverage is honest.** A spec using an hourly reference can only be
  scored over that reference's 2 years; the daily resolution (10 years)
  exists for depth, the hourly one for timing.

## Part 3 — the search

- DECIDED (Claude) — a **combination** is one *trigger* (what fires: a
  breakout, a cross, a threshold) plus context conditions (what must also be
  true), on the traded market or a `ref()`.
- DECIDED (Sarmad) — **up to 6 parts** (1 trigger + 5 context). Growth is
  earned, not fixed; see the growth rules.
- **Both directions, mirrored.** Every rule is screened as a long rule and its
  mirror-image short. Only both directions ever worked; a one-way rule is
  usually a market-direction bet.
- **Exits are not searched.** Every combination runs under the two fixed
  geometries already in `screen_mechanisms.GEOS` (fixed 3R target; 2 ATR stop
  with a 4 ATR trail armed at 1R). Exit geometry alone sets a win rate.
- **Horizons:** 15m, 1h, 4h. 1d stays closed (measured); scalping is out of
  scope (it needs maker execution).
- **Thresholds are measured, never guessed.** Every threshold is the 10th,
  25th, 75th or 90th percentile of that indicator on the discovery slice.
  A hard-coded `> 0.62` once fired on 0.005% of bars and made a whole family
  look tested while it never traded.

### Slices

| slice | markets | time | used for |
|---|---|---|---|
| discovery | D: the 19 `screen_mechanisms.DISCOVERY` symbols | earliest 70% | search and ranking only |
| held-out A | H: the 17 `HELDOUT` symbols | full frame | gate 1 |
| held-out B | D | latest 30% | gate 1 |
| forward | the admitted spec's universe | after admission | paper on demo |

### Rounds

1. **Singles** — every condition alone, both directions, both geometries,
   each horizon. At ~5 s per evaluation on one niced core, the first full
   pass takes days; after that the pipeline runs continuously.
2. **Grow** — a combination of k parts is extended to k+1 only from
   combinations that carried information at k.
3. **Seeded** — context conditions are also tried around proven book
   strategies (Donchian + X), judged with/without on compounded return.

### Growth rules — a part joins only if it earns its place

On the discovery slice, the rule **with** the new part must beat the same
rule **without** it on **both**:
- the rotation null (`consistency_p` improves), and
- compounded return over one account (`portfolio_curve`).

And the rule must stay **testable**: its discovery trade rate must project
at least 8 test-slice trades on at least 4 held-out markets (the floor
`_null_percentiles` enforces). A combination too picky to be judged stops
growing; it is never admitted on silence.

Why this matters at 4h, measured 2026-09-11: Donchian alone takes 541 trades
over the 19 discovery markets — ~28 per market over 5 years, ~8 in each test
slice. The book's best rule sits *at* the testability floor, so deep
combinations will mostly live on 15m and 1h, where the cost yardstick then
decides between them.

**Ties go to the simpler rule.** Every survivor ships with an **ablation**:
remove each part in turn and record the drop. That shows which parts carry
the edge and is what the Reason step explains.

### The ledger — the part that keeps it honest

- Every evaluated combination is recorded under a **canonical hash** (parts
  normalised and sorted, thresholds as percentile ranks, direction, geometry,
  horizon), kept or not. A restart resumes from it; nothing is re-tested or
  re-counted.
- **Held-out results never feed back into the search.** They go to the
  ledger and the report only. If held-out failures steered the next round,
  held-out data would silently become discovery data.
- Only the top **M** per round (default 5) are sent to held-out; each look
  spends evidence.

### Every window carries its own control

A search restricted to a reference's span (e.g. 2 years of hourly S&P) also
runs the known-good rule — `close > donchian_hi(100)` both ways under the
trail — confined to that same window. If the control cannot pass gate 1
there, results in that window are labelled **UNDERPOWERED**, not "no edge".

## Part 4 — the referee

Gates run cheapest first; the first failure sends the candidate to the
ledger with its reason.

**Gate 1 — held-out.** Held-out A **and** held-out B must both pass: the
rotation null across markets (`consistency_p`) at the per-test level set by
the error budget below, plus minimum trades.

**The error budget.** DECIDED (Sarmad, as "a budget that tightens on junk
and loosens on real finds"); concrete form (Claude): **LORD++ online
false-discovery control**, target FDR 10%, initial wealth 0.05. Every
held-out look (gate 1) and every prediction test (gate 2) is one test in the
sequence and spends from the budget; each discovery earns wealth back, so a
pipeline that keeps sending junk faces a falling per-test level and one that
finds real edges can keep testing. *Differs slightly from the conversation,
which said wealth is earned back on forward confirmation:* standard LORD++
earns it at discovery. The forward link is a **supervisory brake**: once ≥5
admitted strategies have completed their forward window, if more than 10% of
them failed it, the per-test level is halved until the realised rate falls
back under target. The ledger stores the sequence, so the budget survives
restarts.

**Gate 2 — the reasoning test.** Part 5.

**Gate 3 — adds money to the book.** `portfolio_curve` at the live settings
(8 slots, 0.5% risk, real signed funding, measured costs) over the common
period: the book **with** the candidate must beat the book **without** it on
compounded return, with a return/drawdown ratio no worse. A good strategy
that only duplicates Donchian's trades fails here.

**Gate 4 — the Analyst**, unchanged as the single admission authority:
- "works now" — pooled PF ≥ 1.15 over ≥ 20 trades in the 90-day doubling
  window;
- signal overlap with the book < 0.6;
- for machine candidates, a ledger record showing gates 1-3 passed;
- **the admission-hole fix**, DECIDED (Sarmad) for *every* spec from every
  proposer: when fewer than 4 markets carry a null percentile,
  `consistency_p` is None and the verdict is **REFUSED — untestable**. This
  overturns the documented rule "silence does not block admission", which is
  exactly what admitted `spec_funding_filtered_trend_pullback` on 20 trades
  with the null run on 0 symbols. That spec keeps trading by Sarmad's
  decision; the fix governs new admissions only.

**Forward.** Admitted specs trade on demo as `paper`; the Analyst's decay
review retires them (30-day PF < 0.85 over ≥ 10 trades). A spec completes
its forward window at 20 live trades.

## Part 5 — the Reason step

- DECIDED (Sarmad) — combinations found by the machine are then reasoned.
- **Blind by design.** The LLM sees the rule, the ablation, and
  **discovery-slice evidence only** — where it pays, long vs short, regimes,
  horizon, cost profile — and the fact that held-out *passed*, never the
  held-out numbers. Otherwise it could "predict" what it just read.
- **It must return a thesis** (the shape drafted in the 2026-09-01 Researcher
  spec):
  - `mechanism` — who is on the other side and why they act;
  - `predictions` — 2 to 4 **new** consequences, each machine-checkable:
    `{subset | condition, metric: mean_r | null_pctile | hit_rate,
    relation: > | <, baseline: complement | all}`, where a subset is a set
    of markets, a regime, a leg (long/short) or an era, and a condition is a
    DSL expression;
  - `kill_condition` — the forward result that would retire it.
- **The machine polices predictions before testing.** Rejected: restating
  evidence the LLM was shown; anything not expressible in the grammar;
  anything trivially implied ("it makes money on held-out"). The thesis is
  hashed at write time and never edited.
- **Verdict.** Predictions are tested on held-out data (each test spends
  from the error budget). A thesis needs ≥ 2 valid predictions and must pass
  at least two-thirds of them. Otherwise the combination is **UNEXPLAINED**
  and not admitted; one retry with a fresh prompt is allowed and is charged
  against the budget like any other look.
- **Model.** `model_deep` (deepseek-reasoner) for the thesis, which has no
  JSON mode, so the reply is parsed against a strict text schema, with
  `model_fast` in JSON mode as the fallback formatter.
- **Graveyard by mechanism.** Each thesis carries a mechanism tag; a new
  survivor whose tag matches a killed thesis is flagged, so one effect in
  a dozen disguises is tested once.
- **Record.** Theses go to `brain_events` (kind `thesis`) and a vault note
  through the Librarian. Only after a spec completes its forward window is
  its mechanism offered to doctrine.

## Part 6 — LLM removals, reporting, failure handling, testing, build order

### LLM removals

DECIDED (Claude, delegated by Sarmad: "you call … while removing make sure
if required an alternate should be there"). Measured spend since 2026-08-25:
1.17M tokens over 213 calls.

| remove | share | what it produced | replacement, in place first |
|---|---|---|---|
| Theorist autopsy + doctrine rewrites; dead `brain/rules.py` | ~55% | 24 prose rewrites; `rules.py` has **no reader** and `doctrine.json` holds `"rules": null` | the research ledger (theses + causes of death) and a data-only post-mortem on the same 6h cadence: each strategy's live record against its validated envelope, as `scripts/monitor.py` section 3 computes it. Doctrine is frozen at v25. |
| Judge meta-review | ~6.5% | 56 of 58 reviews applied nothing; its promote/demote writes went through the non-committing `Journal.query()`; `active` changes no sizing (proving size counts the account's closed trades, `risk.py:174`) | monitor verdict + rent state; `/judge` on Telegram reports those |
| LLM idea screening | ~8% | the idea flow feeding the Strategist | `ideas.score()`, the existing heuristic |
| `strategy/proposer.py` | 0 | no caller | — |
| Strategist LLM spec-writing | ~23% | 6 admissions ever; the one still trading has no null evidence | the machine search — **removed last**, in phase 5, once the search is proposing |
| — keep Talk-to-Luffy chat | on demand | — | — |

The ~8% screening share was attributed by log adjacency, not by caller.
Phase 0 makes every `BrainLLM` call carry a `purpose` (needed anyway for
per-purpose budgets), which pins the exact caller before it is removed.

Freed budget: ~95% of the 200k-token day. `brain_usage.json` gains
per-purpose totals; `research` gets its own slice (`research.token_budget`).

### Reporting

Weekly, with the rent verdict: a ranked report to Telegram, the vault and
the dashboard — combinations tried, held-out looks spent, current per-test
level, survivors per gate, theses written, UNEXPLAINED, admitted, and each
book strategy's forward record. This is the success measure Sarmad chose.

### Failure handling

| failure | behaviour |
|---|---|
| child crashes or exceeds its time limit | batch marked failed in the ledger; next batch resumes from the ledger |
| a reference source is down or changes format | series goes stale → NaN → specs using it read UNTESTED; logged once per source per day |
| LLM unavailable or out of research budget | candidate waits; **nothing is admitted without a tested thesis** |
| database locked | the child never writes; the thread retries its `_tx()` next batch |
| CPU contention | `nice 19`; the thread skips a batch if the last trade cycle took > 45 s |

### Testing

- Unit tests per module; property tests for `ref()`: point-in-time (no bar
  sees a reference value from after its own close), per-source close
  offsets, NaN on stale and close-only.
- **Known-good calibration:** Donchian Breakout Trail, fed as a seeded
  candidate over its 16, must clear gates 1 and 3. A pipeline that cannot
  admit the incumbent has no power.
- **Known-bad calibration:** the full pipeline over synthetic random-walk
  candles must admit essentially nothing (realised FDR at or under target).
- The invariants hold: `scripts/backtest_equivalence.py` PASS,
  `scripts/bench_vector_backtest.py` above 20x.
- `tests/test_single_creation_path.py` asserts "one admission gate".

### Build order

Each phase leaves Luffy running and Donchian trading.

0. **Groundwork** — REFUSED-on-untestable for all specs; remove Theorist,
   Judge, `rules.py`, `proposer.py`, LLM screening (with replacements);
   `purpose` + per-purpose budgets in `BrainLLM`; the child-process runner.
1. **Pieces** — reference store and fetchers, CoinGecko recorder, the `alts`
   index, `ref()` in the DSL, `missing_data` for references.
2. **Search** — condition generator with measured thresholds, rounds and
   growth rules, ablation, the ledger, the `research` thread, window
   controls.
3. **Referee** — gate 1, LORD++ ledger and brake, gate 3, the handoff into
   `_mechanism_once`, guard test.
4. **Reason** — thesis prompt, prediction grammar and policing, gate 2,
   UNEXPLAINED.
5. **Reporting** — weekly report; then remove the Strategist's LLM path.

### Configuration

New `research:` block in `config.yaml`: `enabled`, `nice` (19),
`batch_seconds`, `max_parts` (6), `heldout_top_m` (5), `fdr_target` (0.10),
`lord_w0` (0.05), `token_budget`, `max_stale_hours` per source.

## Out of scope

- **Hedged pairs / multi-leg trades.** `StrategySpec` stays one symbol, one
  direction; relationships are used as signals for single-market trades.
- **Scalping** — needs maker (limit-order) execution; a separate project.
- **Order-book history** — nothing stores it; recording it is its own project.
- **Trading the non-crypto perps.** Binance lists 190 (crude, gold, silver,
  SPY, QQQ, NVDA, …), but their median listing age is ~4 months — too short
  to validate on. They enter here as *references* through Yahoo's long
  history; trading them needs its own spec.
- **1d horizon** — measured closed.

## Known risks

- **Compute.** Two cores. The first singles pass takes days; deep
  combinations are costly. Mitigated by rounds, the testability floor and
  `nice 19`; measured, not assumed, in phase 2.
- **Yahoo's chart API is unofficial** and can change or refuse without
  notice. It fails to NaN → UNTESTED, never to fabricated values.
- **Held-out reuse.** The held-out markets and era are the same data every
  round; the ledger and LORD++ exist to price that. Time is the only
  genuinely fresh test set, and forward trading is where it is spent.
- **LLM rationalisation.** A fluent story can be written for luck; the
  prediction policing and the blind design exist for exactly that.
- **Shorter windows lose power.** Reference-restricted searches carry their
  own control and report UNDERPOWERED rather than a false negative.
