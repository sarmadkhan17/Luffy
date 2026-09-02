# Technology Stack

**Analysis Date:** 2026-09-03

## Languages

**Primary:**
- Python 3.12.3 - entire codebase (`trader/`, `tests/`)

## Runtime

**Environment:**
- Python 3.12.3 (local venv)
- Virtual environment at `./venv/bin/python`

**Package Manager:**
- pip via `requirements.txt`
- Lockfile: Not detected (no poetry.lock, Pipfile.lock, or uv.lock)

## Frameworks

**Web/API:**
- FastAPI 0.110+ - REST API and WebSocket server (`trader/dashboard/server.py`)
- Uvicorn 0.29+ - ASGI application server
- Strawberry GraphQL 0.230+ - GraphQL schema and resolver (`trader/api/graphql_schema.py`)

**Data:**
- pandas 2.0+ - time series, dataframe operations (`trader/data/feed.py`, `trader/strategy/`)
- numpy 1.26+ - numerical arrays and calculations

**Exchange Integration:**
- ccxt 4.0+ - unified cryptocurrency exchange API (`trader/data/feed.py`, `trader/engine/executor.py`)

**Testing:**
- pytest 8.0+ - test runner (748 tests in `tests/`)

**Configuration:**
- PyYAML 6.0+ - config.yaml parsing (`trader/core/config.py`)
- python-dotenv 1.0+ - .env environment variable loading

**Web Scraping:**
- beautifulsoup4 4.12+ - RSS feed parsing (`trader/brain/scraper.py`)
- requests 2.31+ - HTTP client for feeds, APIs

## Key Dependencies

**Critical:**
- `ccxt` 4.0+ - USDM Futures API calls (market data, orders, positions). No substitute; governs the entire venue communication layer.
- `pandas` 2.0+ - OHLCV storage, time-series analysis, candle resampling. All backtest and live evaluation flows depend on it.
- `requests` 2.31+ - HTTP client for all external APIs (Binance derivatives, ForexFactory, Finnhub, Telegram, RSS)

**Infrastructure:**
- `fastapi` 0.110+ - dashboard REST endpoints and GraphQL
- `uvicorn` 0.29+ - ASGI server for dashboard (port 8080)
- `strawberry-graphql[fastapi]` 0.230+ - GraphQL schema for dashboard and agent queries

**Strategies:**
- `tradingview_mcp` - TradingView backtest engine (in-process, no version pinned; loaded dynamically from MCP server or installed module)

## Configuration

**Environment:**
- `.env` file (not committed) — secrets only, never read for configuration
- `config.yaml` (committed) — all trading parameters, universe, risk rules, fees, cost model
- Loaded by: `trader/core/config.py::load_config()`

**Key configs:**
- `mode.execution`: paper | live
- `mode.market`: futures | spot
- `universe.majors`: [BTC/USDT, ETH/USDT, SOL/USDT]
- `risk.risk_per_trade_pct`: 0.5 (as of 2026-09-02)
- `risk.max_open_positions`: 8
- `risk.taker_fee_pct`: 0.04 (production demo measured; actual production unverified)
- `timeframes.execution`: 15m (candle evaluation frame)

**Secrets (.env, never logged/journaled):**
- `BINANCE_API_KEY` - Binance USDM Futures API key
- `BINANCE_SECRET_KEY` - Binance USDM Futures secret
- `BINANCE_DEMO` - "true" (default) routes orders to Binance Demo Trading; must be explicitly set false for production
- `DEEPSEEK_API_KEY` - DeepSeek LLM via OpenAI SDK
- `TELEGRAM_TOKEN` - Telegram bot token for alerts and remote control
- `TELEGRAM_CHAT_ID` - Telegram chat ID for notifications
- `FINNHUB_API_KEY` - (optional) US economic calendar; free tier 403s on that endpoint
- `COINALYZE_API_KEY` - (optional) deep history for OI/taker/long-short; ~40 calls/minute on free tier
- `DASH_TOKEN` - (optional, default "luffy") authentication token for dashboard access

## Databases

**Primary Stores:**
- SQLite 3 (stdlib)
  - `data/luffy.db` - journal (single source of truth for decisions, trades, votes, outcomes, equity, control state)
  - `data/candles.db` - OHLCV history (5y at 4h, 3y at 1h, 1y at 15m; closed bars only)
  - `data/derivs.db` - funding, basis, OI, taker ratio, long/short ratio (asymmetric retention)

**WAL Mode:** `data/luffy.db` uses WAL (write-ahead logging) for concurrent kernel + dashboard access without blocking

**Schema Notes:**
- Journal tables: `cycles`, `votes`, `decisions`, `outcomes`, `trades`, `equity`, `brain_events`, `control_events`, `strategies`, `state_kv`
- No ORM (raw SQL via `sqlite3` stdlib)
- Thread-local connections managed in `trader/core/journal.py`
- Non-transactional reads via `Journal.query()` (see CLAUDE.md for details)

## Platform Requirements

**Development:**
- Python 3.12.3
- Linux/macOS (tested on Linux; Chrome extension for browser-based MCP interaction)
- SQLite 3 (included with Python)
- 7.7GB+ RAM (dev box limitation noted in memory; local LLMs not feasible)

**Production:**
- Python 3.12.3
- Linux (typical deployment target; no Windows testing noted)
- Binance USDM Futures venue access (live account or demo)
- Internet connectivity (exchange API, RSS feeds, MCP servers, Telegram)
- SQLite 3 (included)

## Logging

**Framework:** Python `logging` stdlib

**Output:**
- Console (startup, errors)
- File: `logs/luffy.log` (rotating, multi-threaded writes produce NUL bytes; use `grep -a` to read)

**Configuration:** Ad-hoc per module via `logging.getLogger(__name__)` — no centralized config detected

## Entry Points

**Kernel (trading loop):**
```bash
./venv/bin/python -m trader.kernel
./venv/bin/python -m trader.kernel --status   # print state, no boot
./venv/bin/python -m trader.kernel --panic    # emergency flatten
```

**Dashboard (REST + GraphQL + WebSocket on :8080):**
```bash
./venv/bin/python -m trader.dashboard.server
```

**Tests:**
```bash
./venv/bin/python -m pytest tests/            # all 748 tests
./venv/bin/python -m pytest tests/test_phase0.py -k test_state_transitions
```

**TradingView Login (one-time, interactive):**
```bash
./venv/bin/python -m trader.brain.tv_harness --login
```

---

*Stack analysis: 2026-09-03*
