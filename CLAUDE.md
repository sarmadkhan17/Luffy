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

- **Admission** (Analyst): 90-day pooled PF ≥ 1.15 over ≥ 20 trades, AND
  signal overlap < 0.6 against the book.
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
   `searchsorted(..., side="right") - 1`.

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
  split the same market's history across both. |
| `data/derivs.db` | funding (4y), basis (2y), OI/taker/long-short (~32d) |
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

- **Nothing in the book has a live track record.** Donchian Breakout Trail is
  the only strategy that beat both a rotation null and an always-long/
  always-short control, and it has taken **zero live trades**. Everything
  known about it is backtest.
- Both engines charge funding through `abs(funding_8h)`, billing a short for
  funding it would actually receive. Conservative, so it stays until it can be
  replaced with the real per-symbol series rather than a flat rate.
- `promotion.py` still applies lifetime PF to the legacy genomes. All of them
  are retired, so it currently governs nothing.
- The Researcher agent still does not exist; `research`-stream ideas queue up
  unconsumed.
- The seven analysts have **no measured directional edge**. Over 204 live
  decisions (2026-08-24..09-02) the blend returned -0.695% a call at the 4h
  horizon and won 34.6%, t=-3.84, losing on BOTH sides — which market drift
  cannot produce. They are still evaluated, journalled and graded so this
  stays measurable; they no longer set the score.

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
- **Ask the venue, not the journal.** Three separate live faults — 24
  uncancellable stops, R multiples of 83, a half-closed position still
  charging full heat — were all invisible from inside the system and obvious
  the moment the exchange was queried directly. `scripts/monitor.py` is that
  query.
- **Validate the config, not just the strategy.** At the old 1.5% risk with 4
  positions, Donchian tripped `halt_drawdown_pct` and stopped for good. 0.5%
  with 8 beats 0.75% with 4 on BOTH return and drawdown: a trend book earns
  its Sharpe from breadth, not size per trade.
