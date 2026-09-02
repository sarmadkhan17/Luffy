# CLAUDE.md

Guidance for Claude Code working in this repository.

## What Luffy is

An autonomous crypto futures trading system. It reads the market and the
internet, writes its own strategies, tests them against stored history, trades
the ones that work **now**, and retires them when they stop.

**The governing premise is that no edge lasts.** Selection asks "is this
working now", never "did this survive five years". Do not reintroduce lifetime
pass/fail gates — under one, all 27 candidates scored zero.

**The intended shape is strategy-first:** strategies own entry and exit,
analysts supply which coins and which direction. The code does not do that
yet — `Orchestrator.decide()` still blends 7 analyst votes with strategy
signals into one score. That blend is **temporary**, kept until the Strategist
has enough validated material to stand alone, and the cutover is manual. See
`docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md`.

## Commands

Everything runs through the local venv.

```bash
./venv/bin/python -m trader.kernel            # start trading
./venv/bin/python -m trader.kernel --status   # print state, no boot
./venv/bin/python -m trader.kernel --panic    # flatten everything, go FROZEN
./venv/bin/python -m trader.dashboard.server  # dashboard on :8080
./restart.sh kernel | dashboard               # kill the old PID, start detached

./venv/bin/python -m pytest tests/            # 748 tests
./venv/bin/python -m pytest tests/test_phase0.py -k test_state_transitions

./venv/bin/python -m trader.brain.tv_harness --login   # one-time TV login
tail -f logs/luffy.log
```

Note: `logs/luffy.log` can contain NUL bytes from concurrent writers, so plain
`grep` treats it as binary and prints nothing. Use `grep -a`.

## Environment

Secrets in `.env`, never committed:
`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`, `DEEPSEEK_API_KEY`,
`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `BINANCE_DEMO=true`.

Optional — each degrades to a silent no-op:
- `FINNHUB_API_KEY` — **not required.** MacroGuard's default calendar is the
  free, keyless ForexFactory feed. A Finnhub key is tried first only if
  present, and the free tier cannot read that endpoint (HTTP 403), so the
  fallback is the normal path.
- `COINALYZE_API_KEY` — deep history for open interest / taker / long-short,
  which Binance truncates at ~30 days.

`BINANCE_DEMO=true` routes every order to Binance Demo Trading. Trades are
journalled `exec_mode="live"`, meaning real orders were sent — to a demo venue.

All knobs live in `config.yaml`; `trader/core/config.py` loads both.

## Two processes, one database

They never call each other. They share `data/luffy.db` (SQLite, WAL).

- **Kernel** (`trader/kernel.py`) — the only writer of truth. Trade loop plus
  seven daemon threads.
- **Dashboard** (`trader/dashboard/server.py`) — FastAPI + GraphQL + WebSocket,
  read-mostly. Buttons write a flag into the **`state_kv`** table (not `kv`)
  that the kernel picks up next cycle.

## The firm

`org.yaml` holds the roster. Work flows top to bottom.

| role | modules | job |
|---|---|---|
| Scraper | `brain/scraper.py`, `crawler.py`, `ideas.py` | Reads the outside world — TradingView's **Pine script library**, 16 RSS feeds, 12 crawl seeds. Queues what it finds. **It does not create strategies.** |
| Harvester | `brain/harvester.py`, `data/derivatives.py`, `mcp_client.py` | Reads numbers from venue APIs and MCP servers, and publishes the coverage brief saying which series are deep enough to score against. |
| Strategist | `brain/spec_writer.py`, `strategist.py` | Turns a queued item plus doctrine plus the coverage brief into a `StrategySpec`. |
| Strategy Analyst | `brain/analyst.py`, `strategy/spec_evidence.py` | Backtests it, measures which regimes it pays in, checks it isn't a near-copy, admits or refuses. |
| Trader | `engine/executor.py`, `exits.py` | Places the order and arms the protective stop. |
| Risk Officer | `engine/risk.py`, `agents/macro_guard.py`, `news_guard.py` | Sizes, caps, and blocks. Max **8** open trades, 0.5% risk each. |
| Librarian | `knowledge/vault.py` | Writes the human-readable record into `knowledge/`. |
| Theorist | `brain/theorist.py` | Autopsies losing clusters into `data/doctrine.json` (versioned beliefs). |
| Manager | `brain/judge.py` | Reviews the book every 6h, weekly meta-review. |

**There is exactly one strategy-creation path**: Scraper queues → Strategist
writes a spec → Analyst admits. The old path, where the Scraper mapped scraped
text onto legacy genomes and wrote them straight into the population, was
deleted. Do not reintroduce a second creation path.

### The idea queue

`brain/ideas.py` stores items in `brain_events`, labelled by **stream**:

- `strategy` — trade setups, consumed by the Strategist
- `research` — market claims, for the Researcher agent that does not exist yet

One item may carry both. Consumption is **per stream**, so the Strategist
spending an item does not destroy it for the research side. `MIN_TEXT = 80` is
the quality floor; do not lower it.

## One trade cycle

Every 60s, over 3 majors plus up to 12 scanned alts.

```
Universe.symbols()        → which markets
DataFeed.fetch_multi()    → candles: RAM → candles.db → exchange
7 × Analyst.evaluate()    → each returns Vote(conviction, confidence)
Orchestrator.decide()     → weighted votes + strategy signals vs threshold
RiskManager.check_entry() → sizing, heat cap, breakers, 4-trade cap
Executor.open()           → market order + native STOP on the exchange
ExitEngine.manage()       → 1.5R partial, trailing stop, time exit
Journal.log_*()           → every decision AND every rejection
```

The seven analysts read: **structure** (levels/breaks), **momentum**,
**value** (VWAP mean reversion), **rotation** (BTC-led catch-up), **flow**
(aggressor imbalance, from the venue's REAL takerBuyBaseAssetVolume
since 2026-09-02 — it was a candle-shape proxy before that),
**positioning** (funding + OI), **depth** (order book).

Only the **stop** is armed on the exchange. The target and trail are worked
in-process by `ExitEngine`; if the kernel dies, only the stop protects.

## Daemon threads

| thread | every | job |
|---|---|---|
| `tg-listener` | 4s | Telegram commands |
| `derivs-recorder` | 15m | funding, OI, taker, long/short, basis |
| `strategy-mechanism` | 12h | retire decayed specs, write one new one |
| `scraper` | 4h | scrape and queue |
| `crawler` | 6h | deep-read and queue |
| `brain-judge` | 6h | review the book |
| `agent-validator` | at boot | **runs once and exits** — it is not a loop |

Three more ride the cycle counter: outcome resolution (~12 cycles), the
brain/vault tick (~60 cycles), the Theorist autopsy (~360 cycles).

## Strategy lifecycle

`proposed → backtest → paper → active → demoted/retired`

- **Admission** (Analyst): pooled PF ≥ 1.15 over ≥ 20 trades, signal overlap
  < 0.6 against the book, AND a cross-symbol rotation-null consistency
  p < 0.01 (`select_null_max_p`). Fewer than 4 symbols carrying a percentile
  is silence, not a pass, and does not block on its own.
  The evidence window **starts at 90 days and doubles** until it holds 20
  trades, capped at `select_max_days` (365). A fixed calendar window is
  8,640 bars of 15m and 540 of 4h, so it asks a different question of each
  timeframe — Donchian takes 15 trades in 90d and was refused for it.
  Retirement never widens: too few recent trades is idle, not decayed.
- Every spec is judged over **its own declared universe**, not the five
  backtest symbols in config. Donchian reads null p=0.094 on those five and
  9.2e-05 on the sixteen it trades.
- **Probation** (`promotion.py`): 15 trades, WR ≥ 40%, PF ≥ 1.15.
- **Decay** (Analyst): 30-day PF < 0.85 over ≥ 10 trades. Too few trades is
  *idle*, not decayed.

`promotion.py` applies **lifetime** PF and is scoped to the legacy genomes;
the Analyst's rolling gate owns the specs. Keep them separate.

Population changes take effect within one brain tick (~1h), no restart.

## The strategy language

A spec's logic is a restricted expression DSL over a feature registry —
**71 features** in `strategy/features.py`, `features_deriv.py`, `features_xs.py`.
A strategy can only express a mechanism whose observables are registered, so
the registry is the ceiling on what the system can discover.

Two rules govern every feature:

1. **Missing information is NaN, never a fabricated default.** A fabricated
   `0.0` reads as a real measurement and fires trades on data that does not
   exist. This has been violated and fixed several times; check every
   degenerate case.
2. **Point-in-time.** A bar may only use information that had closed by its
   own close. `dsl._eval_htf` is the reference:
   `searchsorted(..., side="right") - 1`. The same rule governs the newest
   bar: a forming candle has a real open and a truncated high/low/close, so
   reading it as final fabricates a measurement. `core.types.closed_bars` is
   the one definition, used by both the candle store and the live evaluator.

`domain=` and `arg_specs=` are load-bearing — they drive automatic parameter
range inference for the optimizer. A domain real data exceeds silently
mis-tunes every spec using the feature.

**Cross-sectional** features (`xs_rank`, `breadth`, `dispersion`) are markers
special-cased in `dsl._eval_xs`, like `htf(tf, expr)`, because their inner
expression must be evaluated once per universe member. They need
`FeatureCtx.universe` and `symbol`, supplied by `rolling.py` in the backtest
and by `Snapshot` live.

## Journal schema

`data/luffy.db` tables: `cycles`, `votes`, `decisions`, `outcomes`, `trades`,
`equity`, `brain_events`, `control_events`, `strategies`, **`state_kv`**.

`state_kv` is the kernel↔dashboard channel: `control_state`,
`panic_requested`, `news_guard_state`, `macro_guard_state`.

**`Journal.query()` does not commit.** It runs on a thread-local connection
with no transaction wrapper, so an INSERT/UPDATE through it stays uncommitted —
invisible to other connections and holding a write lock — until some later
`_tx()` on the same thread happens to commit it. Use `_tx()` for writes.

## Data files

| file | holds |
|---|---|
| `data/luffy.db` | the journal — single source of truth |
| `data/candles.db` | OHLCV: 5y at 4h, 3y at 1h, 1y at 15m. **Production**
  public data — until 2026-09-02 it was Binance Demo, whose volume is
  simulated (15x inflated) though its prices track to ~0.04%. Rebuilt; the
  old store is kept as `candles.demo-backup-*.db`. One market = one key:
  the store normalises `XAU/USDT:USDT` to `XAU/USDT`, having previously
  split the same market's history across both. **Closed bars only** — a
  forming bar is not a bar; see `core.types.closed_bars` and
  `scripts/repair_partial_bars.py`. |
| `data/derivs.db` | funding (4-5y, 32 symbols), basis (2y, 29), OI/taker/long-short (~32d, 5) |
| `data/doctrine.json` | versioned operating beliefs |
| `data/agent_weights.json` | measured analyst accuracy (weekly) |
| `data/ewa_state.json` | online expert weights |
| `data/agent_calibration.json` | per-agent conviction calibration |
| `knowledge/` | Obsidian vault — the human-readable narrative |

A derivative series must span **75 days** (90% of the backtest frame) before a
spec built on it can be scored rather than refused. `funding` and `basis`
clear it; `oi`, `taker_ratio` and `ls_ratio` sit at ~32 days and cannot reach
75 from Binance alone.

## Control states

`ACTIVE` → entries allowed · `FROZEN` → no new entries, exits managed ·
`HALTED` → neither, though exchange stops stay armed.

Telegram: `/panic /halt /freeze /resume /status /news /scouts /judge /tv`

## Protective stops are ALGO orders

Binance USDM books a reduceOnly `STOP_MARKET` as a **conditional/algo**
order: it returns an `algoId`, and it lives behind `/fapi/v1/algo/*`, not
`/fapi/v1/order`. So `cancel_order()` answers -2011 on every stop, and
`fetch_open_orders()` returns none of them — a position can look naked while
it is in fact stopped. Every place, cancel and enumeration goes through
`trader/engine/protective.py`; do not call ccxt directly for a stop.

Reconcile sweeps orphans at boot. It refuses to strip a live position whose
stop id the journal has lost — the venue's stop is then the only protection
there is.

## Known-good invariants — do not break

- `scripts/backtest_equivalence.py` must stay PASS.
- `scripts/bench_vector_backtest.py` must stay above 20x.
- `spec_evidence` reports UNTESTED, never a score, when data is absent or does
  not span the frame. Do not "fix" a failing test by relaxing this.
- Backtests must read `DataFeed.cached_ohlcv()`, never `fetch_ohlcv(limit=20000)`.
- `orchestrator.py`'s strategy-eligibility gate is load-bearing: specs reach it
  by registering their evaluator under `f"spec:{id}"`.
- `trader/brain/scraper.py` imports the queue module as `from . import ideas`.
  Never create a local variable of that name — shadowing it silently broke the
  queue for the entire life of the file.

## Open faults

`docs/superpowers/plans/` carries the current plans. The 2026-09-01 audit
register's highest-severity items were all closed on 2026-09-02; see
`git log 413da6e..` for the evidence behind each. What replaced them:

- **The book's only strategy does not generalise off its declared universe,
  and only forward trading can now settle it.** Donchian Breakout Trail scores
  median PF 1.42 and cross-symbol null consistency p=3.5e-04 over its 16
  declared symbols. Over 19 comparable perps — same liquidity bar, same 3+
  years, same period — that it has never been scored against, the identical
  rule reads median PF 0.93, median null percentile 53% (the no-edge line),
  10/19 above the median, p=8.8e-01, and compounds at **-4.6%**. The gap is
  not a liquidity artifact: the illiquid unseen names did BETTER, and the
  liquid ones are ADA, LTC, TRX, DOT, BCH, XLM, XMR — majors by any reading.
  Two further measurements narrow it:
  - The gap is **stable in time**: on the earlier train half the declared set
    reads p=7.8e-04 against the unseen set's 2.5e-01; on the test half,
    3.5e-04 against 8.8e-01. So it is not an artifact of the window.
  - The declared set sits in the **top 0.8%** of 4000 random 15-symbol draws
    from the same 34-symbol pool by consistency p (`universe_selection_test`).
    A set that extreme was chosen knowing the outcome.

  Those two together do NOT separate "works on these markets" from "was
  fitted to these markets" — both hypotheses predict exactly this. All the
  history was on disk when the universe was written, so no in-sample test
  can. The only remaining discriminator is forward performance, and the
  strategy has taken **zero live trades**. Everything known about it is
  backtest.
- **A strategy was being scanned on markets it was never validated on.**
  `plan_scan` computed `(liquid | include) - exclude`, so a spec naming 16
  symbols was ALSO handed every venue candidate over its volume floor, and
  `ScanPlan.wants()` — written with the docstring "A strategy must never be
  evaluated on a market it refused" — had **zero callers**. Live at 14:00 on
  2026-09-02 the kernel was scanning Donchian over 23 symbols including XAU,
  XAG, SAMSUNG and SKHYNIX. Fixed: a non-empty `include` now DEFINES the
  universe, and `orchestrator.symbol_allows` enforces per-strategy
  membership. The scan is now exactly the declared 16.
- Funding covers **32 symbols** (~91k settlements) and basis 29, but only
  ~80% of each 4h frame — the store starts 2022-08-31 and the candle frame
  reaches back to 2021-08-26. Earlier bars still pay the flat conservative
  `abs(funding_8h)`. HYPE has no Binance spot pair, so it has funding but no
  basis.
- `promotion.py` still applies lifetime PF to the legacy genomes. All of them
  are retired, so it currently governs nothing.
- The Researcher agent still does not exist; `research`-stream ideas queue up
  unconsumed.
- **(CLOSED 2026-09-02 18:48)** A $5.74 dust position from a RETIRED genome
  sat naked on UNI/USDT and blocked its symbol. Closed by hand at 5.821; the
  venue is flat and the journal holds zero open trades. What it taught: A
  trailing stop filled 302.87 of 303.87 coins; the 1.0 residue belongs to
  `strat_606048ec95` ("ema_trend variant (22.0)", retired). Reconcile reports
  `naked: N` at ERROR but **places nothing** — and the reason recorded for
  that, that the residue was under the venue's minimum, is **wrong**. UNI's
  filters are `MIN_NOTIONAL 5`, `LOT_SIZE minQty 1 step 1`, and the residue
  is $5.74 on 1.0 coin: both clear. `min_notional_usdt: 10` in `config.yaml`
  is OUR entry-sizing floor (`risk.check_entry`), not the venue's, and the
  two were conflated. Nothing ever attempted a stop — there is no failure in
  the log, only the report.
  Reconcile now RE-ARMS a naked position rather than only reporting it,
  sized from the venue's contracts and not the journal's amount — a stop for
  the journalled 303.87 against a real 1.0 is refused, which is how a residue
  comes to hold no protection in the first place. A stop already through its
  level is still not armed (Binance answers -2021 "order would immediately
  trigger"); those are counted separately as `unarmable` and logged as
  needing a CLOSE, which stays the executor's call. Summary keys are now
  `naked`, `rearmed`, `unarmable`.
  The block on UNI was also **lucky**: while it held, UNI read as past its
  Donchian break on a fabricated candle (see the forming-bar entry below).
  On repaired data it was 11.23% away, not through it.
- The wide search has now run **once** at adequate power (24 mechanisms x 2
  geometries x 19 discovery + 17 held-out symbols, with cross-sectional,
  carry, basis, BTC-relative and volatility-regime families included) and
  found no second mechanism. Two Donchian variants are the closest:
  `carry_break` (breakout, skip when funding is crowded) reads discovery
  p=5.9e-02 / held-out PF 1.47, p=1.3e-02 under the trail, and `break_hivol`
  reads 2.9e-02 / PF 1.55, p=3.2e-02. Both fail the discovery gate, both are
  the same mechanism the book already trades, and the paired filter test
  below says neither is worth adding.
- **Nothing has yet traded under correct geometry.** The 15m-ATR fault above
  was fixed on 2026-09-02 and Donchian has taken no trade since, so the
  validated geometry has still never met the venue.
- The seven analysts show a **negative point estimate and no established
  significance**. The earlier reading — "-0.695% a call, t=-3.84, losing on
  BOTH sides" — was **pseudo-replication** and does not survive de-duplication.
  The kernel writes a decision every 60s, so one persistent analyst view
  becomes dozens of near-identical `outcomes` rows that the resolver grades
  independently: all 30 AAVE rows are a single SELL view from 22:32-23:01 on
  2026-08-24, one per cycle, with the same forward return. Of 224 rows there
  are **143 distinct episodes** (same symbol+action, gaps under 30 min):

  | | rows, as reported | independent episodes |
  |---|---|---|
  | all | n=224, -0.737%, t=-3.98 | n=143, -0.545%, **t=-2.36** |
  | BUY | t=-1.59 | 93 eps, -0.757%, **t=-2.30** |
  | SELL | t=-6.55 | 50 eps, -0.151%, **t=-0.63** |

  The SELL side carried the whole result and collapses to noise; it was one
  AAVE episode counted 30 times plus a ZEC episode counted 8. Only the BUY
  side survives, weakly, and even 143 episodes over 9 days on correlated
  symbols are not independent, so t=-2.36 is an **upper bound** on
  significance. The analysts still look bad; the confidence with which they
  were dismissed was manufactured by counting one decision thirty times.
  A stale-candle bias was ruled out as the cause: journalled decision prices
  sit a median 2 bps from the venue's, worth about -0.09% on BUY and -0.02%
  on SELL — an order of magnitude too small.
  `agents/validate.py`, which sets `data/agent_weights.json`, is a historical
  replay over candles and does NOT read `outcomes`, so the live weighting
  path is unaffected. Any NEW measurement taken off `decisions`/`outcomes`
  must cluster by episode first.
  They are still evaluated, journalled and graded; they no longer set the
  score.

## What measurement established (do not re-litigate)

Written 2026-09-02, after the candle store turned out to be simulator data.
These are facts about the search space, not beliefs the Theorist may rewrite.

- **A profit factor alone is a statement about arithmetic.** Exit geometry
  sets a win rate by itself: TP 4.5 ATR over SL 2.5 ATR pays a coin flip 35.7%
  of the time whatever the entry says. ALWAYS-LONG scored PF 1.28 on real
  candles purely on drift. The gauntlet now reports `null_percentile` — how
  often a spec beats a circular rotation of its OWN entries. A spec that
  cannot beat its own signals fired at a random offset has no edge, and no
  amount of parameter tuning or cheaper fees will give it one.
- **A percentile per symbol is noise; the SHAPE across symbols is the test.**
  Under no edge, per-symbol null percentiles are uniform, so counting how
  many clear each of three cuts and reading the binomial tail gives a p-value
  rather than a threshold someone picked
  (`null_baseline.consistency_p`, false-positive rate 0.1-0.4%). Donchian
  sits at p=3.5e-04, and each half of its universe clears independently
  (1.2e-02 / 3.9e-02). The rule this replaced — "beats the 90th percentile on
  ≥60% of symbols" — REJECTS Donchian at 7/15.
- **Discovery evidence is a description of the symbols it was found on.**
  `momo_persist` (`ret(24) > 0.03 and close > ema(100)`, both ways) was the
  best result in the mechanism screen: PF 1.22, median null percentile 87%,
  p=1.1e-03 over 501 trades. On 9 symbols it had never seen it fell to 65%
  and p=0.27. Nothing about the first number was wrong; it just was not
  evidence. `screen_mechanisms.py` now splits DISCOVERY from HELDOUT and
  admits nothing that fails either.
- **11 mechanisms x 2 exit geometries on 4h: none survive both universes.**
  The trail is not universally better either — momo_persist drops from PF
  1.22 to 0.91 under it. It is better for the mechanism it was chosen for.
- **23 specs were screened this way and most sat AT or BELOW the no-edge line
  of ~0.76.** A Bollinger fade landed at the 10th percentile over 272 trades —
  materially worse than entering at random. Single-indicator reversion on 15m
  crypto is the most heavily mined space there is.
- **Only both directions worked.** Long alone won 2 of 5 yearly windows, short
  alone 3 of 5, the pair 4 of 5 — the legs are anti-correlated across regimes.
  A one-directional trend rule is usually a market-direction bet wearing a
  strategy's clothes.
- **A fixed target amputates the tail a continuation mechanism lives on.**
  Every sweep before 2026-09-02 used `trail={"kind":"none"}` with a fixed RR
  target and a short hold cap, and the engine had supported ATR trailing all
  along. That single omission hid the one mechanism that works.
- **An ATR multiple is meaningless without the bar it was measured on.**
  The live path read every ATR off the 15m execution frame — entry stop, the
  sizing that follows from it, and the trail — while a spec declares its
  geometry against its own timeframe. 4h ATR runs 5-8x the 15m figure, so a
  spec validated with a 2.0x4h stop (BTC: 2.12%) traded behind 0.40% and was
  trailed at 0.38% where it was validated at 4.23%. Spec-aware exits
  (`SpecExit`) are cosmetic unless the frame travels with the multiple.
- **Funding is signed, and charging `abs()` to both sides is not neutral.**
  The flat model bills 10.95%/yr to longs AND shorts where BTC's own mean
  settlement is 6.96%/yr paid by longs — over-charging one side by half and
  mis-signing the other. Switching Donchian to the venue's real series moved
  median PF 1.39 → 1.42 and the median null percentile 88% → 90%, on every
  symbol and in the same direction; p stayed 3.5e-04. A cost correction that
  moves every symbol a little and the verdict not at all is the shape a
  correct one should have.
- **4h beat 1h on every mechanism tested**, because cost is charged per round
  trip.
- **Judge a small account by compound growth, not yield on starting capital.**
  Income withdrawn from a fixed base is the wrong model and makes any edge
  look pointless.
- **A diversified mechanism must be judged as a portfolio**
  (`strategy/portfolio_evidence.py`), not by worst symbol. Per-sleeve
  arithmetic understates return on capital by roughly N; an uncapped shared
  account overstates it, because 8 simultaneous 1%-risk positions in
  correlated markets is one 18%-risk position.
- **A blend can bury the one thing that works.** Pooling analyst votes with
  strategy signals put a measured edge into a weighted average with seven
  measured anti-edges. A lone signal at 0.6 confidence reached
  0.6*0.45/1.45 = 0.186 — under every threshold the system produces — so
  Donchian signalled for a week and took zero trades while reading as "the
  setup has not appeared". `scouts.strategy_leads` completes the cutover:
  once a strategy speaks it sets the score. `require_strategy_signal`
  refuses any direction no strategy proposed.
- **The candle store was serving partially-formed bars as closed bars, and
  the live evaluator was signalling on them.** Two faults, one root: nothing
  distinguished a bar that had closed from a bar still open.
  (1) `fetch_ohlcv` extended the store with `since = last + tf_ms`, one bar
  past the newest row it held — but `_merge_save` had already written that
  row while the bar was forming. The one bar that could be wrong was the one
  bar never requested again, so each fetch froze another partial candle. On
  2026-09-02, 104 of 123 (symbol, timeframe) tails disagreed with the venue,
  up to four 4h bars deep; each frozen bar carried 4-16% of the venue's
  volume with the open exact and high/low/close truncated to whatever had
  traded by the snapshot. UNI/USDT 4h stored `6.298/6.307/6.260/6.304`
  against a real `6.298/6.373/5.692/5.742`. That fabricated close read as a
  Donchian breakout over a 100-bar high of 6.222 — and the high was wrong
  too, being a max over truncated highs. **The book's one strategy appeared
  to be one dust position away from its first live trade, into a bar that
  fell 9%.** All corruption began at 2026-09-01 23:30, when the store was
  rebuilt from production, so the bulk-fetched history the backtests read is
  clean and only what the kernel wrote live was affected.
  (2) `to_evaluator` read `lo[-1]`, and the live frame's last row is the
  forming bar, so the live rule was "price is beyond the level right now"
  while the rule that earned the statistics is "the bar CLOSED beyond it"
  (`vector_backtest.py:115` fills at `closes[i]`). For a breakout mechanism
  those differ by exactly the population its edge excludes: the intrabar
  poke that retraces.
  Fixed: `core.types.closed_bars` is the single definition of "this bar has
  ended", the store persists only closed bars and re-reads its tail on every
  incremental fetch (`STORE_OVERLAP_BARS`), and the live evaluator judges
  closed bars. `scripts/repair_partial_bars.py --check` audits the store
  against the venue; it repaired 997 bars and now reports zero.
  **Still open:** a closed-bar signal persists for the life of its bar, so an
  entry blocked by risk can fill hours after the close the backtest paid.
  Signals now carry `signal_bar_age_min` so the right cutoff can be measured
  rather than guessed.

- **Ask the venue, not the journal.** Three separate live faults — 24
  uncancellable stops, R multiples of 83, a half-closed position still
  charging full heat — were all invisible from inside the system and obvious
  the moment the exchange was queried directly. `scripts/monitor.py` is that
  query.
- **A search that returns "nothing works" is a claim about the tool first.**
  `screen_mechanisms.py` printed that verdict for its whole life, and four
  independent faults produced it, each failing silently and each in the same
  direction. (1) It passed no `universe`, `derivs` or `btc` to the evaluator,
  so every cross-sectional, carry and BTC-relative feature evaluated to NaN
  and the mechanism took **zero trades** — which reads in a results table as
  "no result", not as "could not be evaluated". Control: 285 trades either
  way; `xs_rank` 0 vs 874, funding 0 vs 561, BTC-relative 0 vs 417. (2) The
  rotation null was charged the flat `abs(funding)` while the actual profit
  factor paid the venue's real signed series — a control made more expensive
  than the thing it controls. (3) Two thresholds were absolute constants over
  distributions that never reach them: over 88k 4h bars `taker_buy/volume`
  runs p1=0.431, p50=0.492, p99=0.553, so `> 0.62` fires on 0.005% of bars
  and the whole flow family was untested while appearing in the table. (4) It
  ran on eight usable symbols — see the next bullet.
- **The size of the discovery universe sets what the test can DETECT, not
  just how noisy it is.** `consistency_p` reads a binomial tail, so at n=8
  eight symbols all above the no-edge median still score p=1.2e-02 and the
  0.01 gate is mathematically unreachable; only a narrow, very strong edge
  registers. Donchian Breakout Trail is the opposite shape — 14 of 15 above
  the median, 4 above the 90th — and scored **REFUSED (p=1.2e-02)** on the
  screen's own universe against 3.5e-04 on its declared 16. ADA and LTC were
  named in that universe and absent from the candle store, so they dropped
  silently, and XAU/XAG made up the count while firing ~20 trades each. The
  store now carries 32+ crypto perps at 4h and the screen runs 19 discovery /
  17 held-out.
- **The screen refuses the book's own strategy, and that is the screen being
  right.** 38 mechanisms x 2 geometries at 19 discovery / 17 held-out on 4h
  (2026-09-02) admitted nothing. Before reading that as a fact about the
  market, the known-good input was run through the same gate:
  `donchian_hi(100)` both ways under the trail geometry scores **discovery
  p=2.3e-01, held-out p=8.1e-02** against a gate of 0.01/0.05 — REFUSED.
  The gate is not merely too strict. Splitting that single run by whether
  the spec NAMED the symbol, with identical geometry, period, split, cost
  model and null:

  | | n | median PF | median null-pctile | above median | consistency p |
  |---|---|---|---|---|---|
  | declared by the spec | 15 | 1.42 | 87% | 13/15 | 3.5e-04 |
  | never declared | 20 | 0.93 | 52% | 10/20 | 9.7e-01 |

  10 of 20 above the median is the coin flip. The gate clearly has power —
  it hands the declared set 3.5e-04, reproducing the recorded headline
  exactly — so the honest reading is that **"nothing survives" includes the
  incumbent**, and the book has zero mechanisms that generalise off a
  hand-picked symbol list, not one. This is the earlier universe-extension
  result under a much tighter control: previously the comparison spanned
  separate scripts and runs, so a difference in geometry or period could not
  be excluded. Here nothing differs but the symbol list.
  `CONTROL_donchian100` is now the first row of `screen_mechanisms.py` and
  must stay there: a screen that prints "nothing survives" is making a claim
  about its own power before it makes one about the market.
  It still does not separate "works on these markets" from "was fitted to
  them" — both predict this — and forward performance remains the only
  discriminator.

- **1d is a closed door, not an unexplored one.** "4h beat 1h because cost is
  charged per round trip" does NOT extend to 1d. Built daily bars from 4h
  (six bars per UTC day, partial days dropped; verified field-exact against
  the venue on 499 overlapping days for BTC/SOL/LINK) and ran the same gate.
  The literal `donchian(100)` at 1d takes 2-7 test trades a symbol — **zero
  of 35 symbols carry a null percentile, not scored**, which is silence, not
  a failure. The duration-matched channel (100 4h-bars = ~17 days) IS
  scoreable and reads nothing: `donchian(20)` declared p=1.0e+00,
  `donchian(50)` declared p=1.0e+00. Compounded, declared falls from
  **+16.8%/yr at 4h to +1.6%/yr at 1d**. Even the in-sample full-frame
  reading, run deliberately to buy back the power the 1d split costs and
  therefore generous to 1d, tops out at p=2.7e-01 against 4h's 1.1e-04.
  The interesting part is HOW it fails: at 1d the declared/undeclared split
  essentially disappears (1.6%/yr vs 1.0%/yr) — but by the declared set
  collapsing to the undeclared level, not by the undeclared set rising. The
  undeclared group never clears the coin flip at any channel length. So 1d
  offers nothing to generalise with.
  The run also re-derived the 4h control to the digit (declared PF 1.42,
  pctile 87%, 13/15, p=3.5e-04; undeclared 0.93, 53%, 10/19, p=8.8e-01;
  +16.8%/yr vs **-4.4%/yr at 49.9% maxDD**), so the instrument is the same
  instrument.

- **`vector_backtest.WARMUP = 210` is a BAR COUNT, not a duration.** `simulate`
  skips the first 210 bars of every slice it is handed, including the test
  half. At 4h that is 35 days and harmless. At 1d it discards 210 of a
  550-bar test half — 38% — and collapses the rotation null, whose offsets
  are drawn from `[WARMUP+1, n-WARMUP-1]`: only 129 distinct offsets remain
  for 60 draws. Any future higher-timeframe work through this engine loses
  210 bars per slice silently. Unfixed; it is not a fault at 4h or below.
  Related: `backtest.resample()` uses `closed="right", label="right"`, which
  is wrong against the store's open-time labels — do not use it to build
  higher timeframes.

- **The registry was never the ceiling; the mechanism list was.** 71 features
  are registered and the screen reached about twenty of them.
  `efficiency_ratio`, `corr_btc`, `vol_of_vol`, `streak`, `bars_since`,
  `swing_high/low`, the wick/body shape family and `rel_volume` had never
  appeared in a single screened mechanism, so 14 new ones were added without
  inventing a feature. None survived. What the DSL genuinely CANNOT express,
  verified rather than assumed: order-book/microstructure (no book history is
  stored anywhere, so the `depth` analyst's domain is unbacktestable);
  open interest and long/short ratio (33.5 days over 5 symbols against a
  75-day floor, and Binance's public OI endpoint caps at 30 — `COINALYZE_API_KEY`
  is unset and is the one concrete unlock); multi-leg construction
  (`StrategySpec` is one symbol, one direction, one exit, so `xs_rank` can
  select but never hedge); cross-asset context beyond BTC; event time (
  MacroGuard holds the calendar, the DSL cannot see it); and position state
  (entries are stateless boolean arrays). Flow is NOT a gap — `taker_buy`
  rides in the klines with full history.

- **No filter improves the one mechanism that works, and profit factor cannot
  see why.** Paired test over the 16 declared symbols, same base rule, same
  geometry, with and without the condition:

  | variant | PF | null p | OOS trades | CAGR | maxDD |
  |---|---|---|---|---|---|
  | baseline | 1.42 | 3.5e-04 | 487 | 17.3% | 27.3% |
  | carry | 1.43 | 3.5e-04 | 415 | 7.6% | 25.8% |
  | hivol | 1.44 | 1.1e-02 | 307 | 6.3% | 25.0% |
  | breadth | 1.42 | 3.8e-02 | 466 | 17.1% | 25.3% |
  | rel_btc | 1.33 | 1.8e-04 | 426 | 9.6% | 23.3% |

  Removing 15% of trades removed more than half the compounded return, so the
  cut trades carried a disproportionate share of the fat right tail. PF
  barely moves because those trades are near break-even *individually*.
  Judge a refinement on compounded return over one account, never on a ratio
  that improves while the trade count falls.
- **The deployment is already at its own optimum; the return is not a knob.**
  Walking the mechanism's 1724 real fills against one compounding balance
  under the live concurrency cap (`scripts/deployment_frontier.py`), 8 slots
  refuses 17% of signals and 4 slots refuses 46% — the book is signal-rich
  and capital-poor. Relaxing the cap makes it WORSE (12 slots: 11.6%/yr at
  34.2% drawdown against 17.3%/27.3% at 8), because concurrent positions in
  correlated markets are one position. Raising risk scales return and
  drawdown together at a flat ratio of ~0.63-0.70 (1.0%/8: 33.6%/yr at 47.6%
  drawdown). There is no free return in the configuration. NOTE: these
  figures omit the derisk ladder and the halt breaker, which the spec's own
  `portfolio_expectation` models — that is why it reads 9.6%/yr where this
  reads 17.3%. The comparison between rows is what this measures.

- **Validate the config, not just the strategy.** At the old 1.5% risk with 4
  positions, Donchian tripped `halt_drawdown_pct` and stopped for good. 0.5%
  with 8 beats 0.75% with 4 on BOTH return and drawdown: a trend book earns
  its Sharpe from breadth, not size per trade.
