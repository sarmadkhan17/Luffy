# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity

**Luffy** — an autonomous crypto trading OS. It observes markets via analyst agents, decides via a strategy genome system, executes on Binance futures/spot, journals every decision (including rejections), and learns through statistical demotion + LLM-driven strategy review. "Brain" = the background loops (harvester, strategist, judge) that run as daemon threads in the kernel.

## Commands

All commands use the local venv:

```bash
# Run the trading kernel (main process)
./venv/bin/python -m trader.kernel

# Run with --status or --panic flags (no full boot)
./venv/bin/python -m trader.kernel --status
./venv/bin/python -m trader.kernel --panic

# Run the dashboard (separate process)
./venv/bin/python -m trader.dashboard.server

# Restart either process (kills old PID, starts detached)
./restart.sh kernel
./restart.sh dashboard

# Run all tests
./venv/bin/python -m pytest tests/

# Run a single test file
./venv/bin/python -m pytest tests/test_phase0.py

# Run a single test by name
./venv/bin/python -m pytest tests/test_phase0.py -k test_state_transitions_and_persistence

# TradingView harness — first-time login (run once, session persists)
./venv/bin/python -m trader.brain.tv_harness --login

# Scripts (run from repo root)
./venv/bin/python scripts/validate_population.py     # rerun analyst validation
```

Log tail: `tail -f logs/luffy.log`

## Environment

Secrets live in `.env` (never committed). Required keys:
- `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`
- `DEEPSEEK_API_KEY`
- `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- `BINANCE_DEMO=true` (set to `false` for production keys)
- `FINNHUB_API_KEY` — free-tier key from finnhub.io; powers MacroGuard economic calendar (optional; MacroGuard is no-op without it)

All config knobs are in `config.yaml`. `trader/core/config.py` loads both.

## Architecture

### Process Model

Two independent processes talk only through `data/luffy.db` (SQLite WAL):
- **Kernel** (`trader/kernel.py`) — the only writer of truth; runs the trade loop + brain daemon threads
- **Dashboard** (`trader/dashboard/server.py`) — FastAPI read-mostly; mutations write KV flags that kernel picks up next cycle

### Core Data Flow (one kernel cycle)

```
Universe.symbols()
  → DataFeed.fetch_multi()          # OHLCV from 3-tier cache: RAM → candles.db → ccxt
  → 7 Analyst.evaluate()            # each returns a Vote(conviction, confidence, rationale)
  → Orchestrator.decide()           # regime-weighted vote aggregation + strategy signals
  → RiskManager.check_entry()       # heat cap / daily breaker / sizing
  → Executor.open()                 # ccxt order + native SL/TP on exchange
  → ExitEngine.manage()             # trailing stop / time exit per open trade
  → Journal.log_*()                 # everything written; decisions AND rejections
```

### Layer Map

| Package | What it does |
|---|---|
| `trader/core/` | `Journal` (SQLite, single writer, WAL), `types` (dataclasses), `config`/`Env` |
| `trader/data/` | `DataFeed` (3-tier OHLCV cache), `Universe` (top-N alts scanner), `make_exchange` |
| `trader/agents/` | 7 `Analyst` subclasses + regime classifier + calibration (conviction rescaling from journal outcomes) |
| `trader/engine/` | `Orchestrator`, `RiskManager`, `Executor`, `ExitEngine`, `ControlStateMachine`, `Heartbeat`/watchdog, reconciliation |
| `trader/strategy/` | `Genome` schema + gene specs, `library` (signal generators for each family), `backtest`, `promotion` (statistical demotion) |
| `trader/brain/` | `Harvester` (scrape → extract → gauntlet → paper), `Theorist` (autopsy), `Strategist` (review), `BrainJudge` (portfolio decisions), `TVHarness` (Playwright browser for TradingView), `BrainLLM` (DeepSeek), `pine` (PineScript forge) |
| `trader/knowledge/` | `Vault` — writes Obsidian markdown notes from journal data; the human narrative layer |
| `trader/dashboard/` | FastAPI + GraphQL (Strawberry) + WS push, single `web/index.html` frontend |
| `trader/notify/` | Telegram alerts + command listener (`/panic`, `/freeze`, `/resume`, `/status`, `/judge`, `/scouts`, `/tv`) |

### Journal Schema (`data/luffy.db`)

Tables: `cycles`, `votes`, `decisions`, `outcomes` (forward returns at 1h/4h/24h), `trades`, `equity`, `brain_events`, `strategies`, `kv`.

The `kv` table is the kernel↔dashboard control channel: `panic_requested`, `news_guard_state`, `market_type`, `control_state`.

### Strategy Lifecycle

`PROPOSED → BACKTEST → PAPER (probation: 15 trades, WR≥40%, PF≥1.15) → ACTIVE → DEMOTED/RETIRED`

The gauntlet is dual: Yahoo Finance pre-filter (cheap) then TradingView Strategy Tester via Playwright (budget-capped at `tv_harness.daily_runs`; gracefully degrades to Yahoo-only when down). Results cached by PineScript semantic hash in `data/tv_cache.json`.

**Hot reload**: population changes (promote/demote/new strategy) take effect within one brain tick (~hourly) without restarting the kernel.

### Regime System

`trader/agents/regime.py` classifies each symbol into `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`, or `VOLATILE` using ADX + volatility clustering. The orchestrator scales each analyst's conviction by `fit_multiplier(regime, agent.regime_affinity)`.

### Analyst Weights

Default weights in `orchestrator.py:_DEFAULT_WEIGHTS`. Overridden by measured accuracy from `data/agent_weights.json` (written by `agents/validate.py`, refreshed weekly). Per-regime fit multipliers come from the same file. Calibrated conviction corrections live in `data/agent_calibration.json` (refitted every 6h from journal outcomes).

### Brain Daemon Threads (in kernel)

- **harvester** (every 4h): scrapes TradingView ideas + RSS → LLM extracts genome → dual gauntlet → paper
- **crawler** (every 6h): deep-reads finance sites/papers → mines strategy rules
- **brain-judge** (every 6h): DeepSeek reviews strategy book → promote/demote/hold with written rationale; weekly meta-review writes to vault
- **agent-validator** (weekly, at boot if stale): replays analyst decisions vs outcomes, updates weights
- **theorist autopsy** (every ~6h, inside cycle): mines journal for edge decay, updates `doctrine.json`
- **strategist review** (every ~1h, inside cycle): rule-based + LLM strategy promotions

### Key Data Files

| File | Purpose |
|---|---|
| `data/luffy.db` | Journal — the single source of truth |
| `data/candles.db` | Persistent OHLCV cache (survives restarts) |
| `data/doctrine.json` | Brain's versioned operating beliefs |
| `data/agent_weights.json` | Measured analyst accuracy weights |
| `data/agent_calibration.json` | Per-agent conviction calibration |
| `data/tv_cache.json` | TradingView Strategy Tester result cache |
| `data/heartbeat_luffy.json` | Process liveness (age checked by stall monitor) |
| `knowledge/` | Obsidian vault (theories, strategies, postmortems, daily reviews) |
| `logs/luffy.log` | Rotating log (5×10MB) |

### Control States

`ACTIVE` → entries allowed · `FROZEN` → no new entries, exits managed · `HALTED` → no entries, no exits (exchange stops remain armed). Changed via dashboard, Telegram commands, or `kernel --panic`.
