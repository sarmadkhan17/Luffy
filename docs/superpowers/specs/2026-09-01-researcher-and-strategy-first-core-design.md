# Strategy-first decision core + the Researcher

**Status:** design agreed 2026-09-01 (Sarmad + Claude), all open questions
resolved the same day. Choices are marked DECIDED with their rationale, so a
later reader can tell a decision from an assumption.

## Why this exists

An audit on 2026-09-01 found that Sarmad's model of Luffy and the code had
diverged completely. He believed strategies decided entries and exits. They
do not: `Orchestrator.decide()` blends 7 analyst votes and any firing
strategy signal into one score and trades the score. Measured over 115,446
decisions, only 10.3% carried any strategy signal, and **53% of the 93
directional decisions had none at all**.

This document fixes the target architecture so that divergence cannot recur.

## Governing principle

> Strategy generation is not the objective. Improving Luffy's understanding of
> the financial market is the objective; strategies are a consequence of that
> understanding.

## Target architecture

```
Scraper ──┬── prebuilt strategies (Pine, explicit rules) ─────────→ Strategist
          └── research claims (papers, quant posts) ──→ Researcher ─┤
                                                            │       │
Harvester numerics ─────────────────────────────────────────┤       │
Luffy's own outcomes / decays / refusals ───────────────────┤       │
                                                            ↓       │
                                                  validated Mechanisms
                                                                    ↓
                                                            StrategySpec
                                                                    ↓
                                                   Analyst (adjudicate, admit)
                                                                    ↓
                                              Trader → Risk Officer → venue
                                                                    ↓
                                                              Librarian
```

Feedback edge: every decay, refusal and null-test failure returns to the
Researcher as evidence about the world model.

## Migration: the blend is temporary

The current blended formula (7 analyst votes + strategy score into one
threshold crossing) **keeps running unchanged** until the Strategist has
enough validated material to build its own strategies. Strategy-first is the
destination, not a big-bang cutover.

**DECIDED — the cutover is manual.** No automatic criterion. Both paths get
built and journalled, plus a comparison view showing what the strategy-only
book would have done against what the blend actually did. Sarmad flips the
switch. Rationale: an automatic rule would need a threshold nobody can justify
yet, and the comparison is the thing actually worth looking at.

Implication for the build: strategy-only decisions must be **computed and
journalled in shadow** from the moment the Strategist produces usable
mechanisms, so the comparison has history behind it when the switch is
considered.

## Agent contracts

### Scraper — the outside world, nothing else
Reads prebuilt strategies (Pine scripts, explicit rule sets) and research
claims (papers, quant blogs). Queues them. It does not create strategies.

- Target `tradingview.com/scripts/` (the public Pine library), **not**
  `/ideas/` — the latter is chart commentary. Verified live: both respond to
  the existing regex with real descriptions.
- **Stop truncating the description.** All 452 items ever queued are bare
  headlines because the old harvester stored title+source only; the current
  code then caps whatever it does capture at 600 characters. Measured on
  2026-09-01, listing-page descriptions run 1,537 / 5,708 / 26,910 characters
  (min/median/max) — TradingView embeds the full write-up in the listing JSON,
  so no separate per-script fetch is needed.
- **Delete Job B** — `extract_batch` → `_dual_gauntlet` → `_deploy` is a second
  strategy-creation path that writes legacy genomes straight into the
  population, bypassing Strategist and Analyst. One creation path only.
- Two consumers, so **two scorers**. The existing `idea_score` rewards
  strategy jargon (`entry`, `stop-loss`, `backtest`) which is right for
  prebuilt strategies and wrong for research prose. Research material scores
  on claim language instead: does it assert an observable predicts something,
  with scope and ideally magnitude? An item may score on both.
- Optional Pine **source** fetch for top-ranked scripts via the existing
  Playwright harness, capped (see settings).

### Harvester — the numbers
Unchanged remit: venue APIs and MCP servers, plus the coverage brief that
tells the Strategist what can actually be scored. Extended to pull CoinGecko
market structure through the client it already has.

**DECIDED — it stays a separate role.** It now feeds two consumers, the
Researcher and the Strategist, so folding it into either would make one agent
depend on a module living inside a peer. Extended with CoinGecko market
structure through the MCP client it already has.

### Researcher — NEW, sits above the Strategist
Objective: move beliefs from unexamined to survived-attack. It never writes to
the live population; it advises.

**Thesis** — the unit of work:
```
question | origin (anomaly|failure|contradiction|gap|unexploited|external)
mechanism (the proposed WHY) | prediction (PRE-REGISTERED) | kill_condition
experiments[] | scope | status: OPEN→TESTING→SUPPORTED→PROMOTED|KILLED
```
Pre-registration is what separates this from data mining: prediction and kill
condition are written before data is touched and are immutable after.

**Six thesis sources**, all computable today:
| source | example question from current data |
|---|---|
| outcome anomaly | SELL is 22.6% correct (19/84), BUY 47.4% (37/78). Real, cost artefact, or regime artefact? |
| regime collapse | TRENDING_DOWN hit rate 0.141 over 71 samples |
| contradiction | doctrine says edge is in derivatives; measured IC says cross-sectional (−0.15) and `oi_z` is flat |
| portfolio gap | nothing in the book pays in VOLATILE |
| unexploited signal | `rel_strength_btc` IC −0.15 in every regime, used by no strategy |
| external claim | imported from the Scraper's research stream |

**The challenge step is mechanical, not a prompt.** LLMs produce the shape of
skepticism, not skepticism. A thesis must survive a fixed battery:
regime confound · symbol confound (sign holds on ≥4/5) · era confound (sign
holds in both halves) · cost confound (decile spread > round-trip cost at that
timeframe) · crowding confound · null test (same exits, random entries, 200
shuffles). The LLM proposes the mechanism; the machine tries to kill it.

**Five outputs:** TRADE · ADAPT (modify a live strategy — no code path exists
today) · RESEARCH · WAIT · **ACQUIRE** (the question is unanswerable with the
data held; task the Harvester rather than fake an answer on thin data).

**Scored on:** belief survival rate · decision impact (did a belief change a
live decision, and did that decision do better) · portfolio diversity (mean
pairwise signal correlation should fall) · **WAIT accuracy** — an agent
rewarded only for TRADE will manufacture edges.

**External reach:** granted, fed by the Scraper. Rule: **external research
sets the agenda; internal data sets the truth.** An imported claim enters as a
Thesis with zero confidence and faces the identical confound battery. Source
reputation affects priority, never the bar.

### The handoff — a validated Mechanism
```
claim | why (who is on the other side, why they act)
observable   → a DSL-expressible quantity, e.g. rel_strength_btc(24)
direction    | scope (regimes/symbols/timeframes that survived)
horizon      → where IC peaks before decaying
strength     → decile spread net of round-trip cost, per timeframe
survived[]   | confidence (decays if not re-challenged) | provenance
```
Three fields the Strategist currently guesses become measurements:
`scope → spec.regime_filter`, `horizon → spec.exit.time.max_bars`,
`observable → the core term of entry_long/entry_short`.

Alongside mechanisms the Researcher publishes **portfolio gaps** (a
commission, not just information) and the **graveyard** — killed mechanisms
hashed by mechanism rather than wording, with cause of death, so the
literature's dozen rephrasings of one effect are tested once.

### Strategist — numbers only, expresses rather than discovers
Takes a Mechanism and engineers it into a `StrategySpec`: the exact trigger
(level vs cross vs z-threshold), the filters, the stop/target geometry, and
which timeframe wins once costs are priced. Self-tests before queueing — the same bar the Analyst applies (recent pooled
PF and trade count) plus the null test, so it never queues what will be
refused. This is not duplicate work: the Strategist asks *"is this
individually sound?"*, the Analyst asks *"is this better than what we already
trade, and does the book need it?"* The first is a property of the candidate;
the second is a property of the portfolio. Outside-world material is the
Scraper's job; the Strategist reads only numbers.

**DECIDED — bounded search.** Given a Mechanism, the Strategist tries a small
fixed set of trigger forms (level test, cross, z-threshold) crossed with
filter and exit shapes, scores them by walk-forward, and caps the sweep at a
few dozen candidates. The null test is the overfit guard. It does not search
thousands: the Researcher has already established *that* the effect exists, so
this is engineering the expression, not hunting for edge.

### Analyst — adjudicate
Compile to Pine where possible, test on TradingView or on cached data, compare
against the book, check the candidate against doctrine and autopsies, and
decide which strategies to trade **for the current regime**. It is a **set**,
not a single pick.

The Analyst's output means different things either side of the cutover. While
the blend runs, the selected set is the *eligible* set whose signals enter the
orchestrator's sum. After the cutover, the selected set is what actually
trades, and the analyst votes fall away. Same computation, different
authority.

**Admission becomes comparative.** Today `admit()` checks only recent pooled
PF ≥ 1.15 over ≥20 trades and signal overlap < 0.6, in isolation. With
`max_specs: 8` the book fills on absolute merit and then stops accepting. New
rule: when the book is full, a candidate must beat the weakest member on
current-regime evidence to displace it.

**DECIDED (D8) — the Analyst owns specs, `promotion.py` owns legacy genomes.**
`promotion.evaluate_population` currently runs hourly over *every* row,
including `kind='spec'`, applying lifetime profit factor — the instrument this
design rejects. Fix: exclude `kind='spec'` from it. Each population keeps the
instrument built for it, and the eight legacy genomes doing all the live
trading keep the safety rules they have been running under.

### Trader — execute the decided strategy
**DECIDED — deliberately thin.** Takes the selected spec plus the Risk
Officer's levels and size, places the entry and the protective orders, reports
fills. No discretion and no signal logic: every judgement has already been made
upstream, and a Trader that second-guesses it reintroduces the blend by the
back door.

### Risk Officer — size and protect
Decided: **initial margin ≤ 15% of equity** per position; **max 4 open**.
Converts the strategy's exit *shape* (its `ExitSpec`: stop 2.5 ATR, target 3R,
max 64 bars) into real price levels and a real size under those caps. The
strategy owns the shape; the Risk Officer owns the arithmetic and the caps.

### Librarian — record everything
**DECIDED — extend the existing vault.** Theses, Mechanisms and belief changes
get markdown cards alongside today's strategy cards, autopsies and daily
reviews, so the research history is readable by a human and not only queryable
by SQL. The Researcher's world model is the part most worth being able to read.

## Attribution (D9)

**DECIDED.** Strategy-first makes attribution exact by construction — the
strategy that fired is the strategy that traded. Until cutover, a trade is
attributed to a strategy only when that strategy's signal alone crossed the
threshold; otherwise it is attributed to `orchestrator`. Today the loudest
signal in the cycle is credited even when analyst votes carried the entry, and
promotion, demotion and blend weights all consume that attribution.

## Feature vocabulary

**DECIDED — A + B + C.**

- **A** — ~12 drop-in features: `efficiency_ratio`, `bars_since`, `volume_z`,
  `rel_volume`, wick/body fractions, `streak`, `dd_from_high`, swing levels
  (`indicators.swing_highs_lows` already exists and is not exposed to the
  DSL), `macd`, `keltner_*`, `atr_pct_rank`, `funding_pct`, `basis_slope`.
- **B** — widen `FeatureCtx` from "this symbol + BTC" to include the universe
  and global series, unlocking `xs_rank`, `breadth`, `dispersion` and the
  CoinGecko market-structure features. This is where the strongest measured
  signal already lives (`rel_strength_btc`, IC −0.15 in every regime).
- **C** — begin recording order-book depth and imbalance, liquidations, and
  the spot-vs-perp volume split. None of it is backtestable for months, which
  is exactly why it starts now: history not recorded is permanently lost, the
  same argument that justified the derivatives recorder. It also finally makes
  the `depth` mechanism testable rather than live-only.

## Settings changed
| setting | from | to |
|---|---|---|
| `tv_harness.budget_enabled` | false | **true** (the cap is currently ignored) |
| `tv_harness.daily_runs` | 20 | **2** |
| `mechanism.interval_minutes` | 180 | **720** (≈2 candidates/day) |
| `scraper.ideas_per_cycle` | 12 | **4** |

## Safeguards
1. Pre-registration before data is touched
2. **Global** multiple-testing budget; required effect size escalates with
   tests already run against the same slice. External claims draw from the
   same budget — no allowance for "but this one is from a paper"
3. A permanent hold-out slice the Researcher may never query; only the final
   kill test may
4. Mechanism-hashed graveyard so rephrased retries are caught
5. Cost floor at the candidate's own timeframe (~18.5% of risk staked at 15m,
   ~7.8% at 4h)
6. The Researcher never writes to the live population
7. Belief confidence decays when unchallenged, so the world model cannot
   calcify
8. Its own token allocation inside the 200k/day budget, which is already
   exhausted 3 days in 8

## Known risks
- **Convergence.** A growing pile of unresolved theses. Mitigation: a thesis
  names its next experiment and its cost when opened; one that has not
  advanced in N cycles is auto-killed as unresolvable; open theses are capped.
- **Thin data.** 172 graded outcomes and 31 days of positioning history. Most
  interesting questions are currently unanswerable, which is why ACQUIRE is
  not optional.
- **Scraper dependency.** External reach is worth nothing until the Scraper
  fetches bodies and targets the right sources.

## Out of scope
- A1 dashboard authentication ("ignore for now")
- E2 partial-close fee double-count (explicitly excluded)
