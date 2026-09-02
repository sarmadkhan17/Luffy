# External Integrations

**Analysis Date:** 2026-09-03

## APIs & External Services

**Exchange & Market Data:**
- Binance USDM Futures - market data, live orders, position management
  - SDK/Client: `ccxt.binanceusdm` (unified exchange API via `ccxt` 4.0+)
  - Auth: `BINANCE_API_KEY`, `BINANCE_SECRET_KEY` (optional `.env`)
  - Endpoints: 
    - `/fapi/v1/klines` - OHLCV via `trader/data/feed.py::DataFeed.fetch_multi()`
    - `/fapi/v1/fundingRate` - perp funding history (years of data)
    - `/fapi/v1/order`, `/fapi/v1/openOrders` - order placement and management
    - `/fapi/v1/algo/*` - conditional (STOP_MARKET) orders for protective stops
    - `/futures/data/openInterestHist`, `/takerlongshortRatio`, `/topLongShortPositionRatio` - derivatives analytics (capped at ~30 days)
    - `/fapi/v1/ticker/24hr` - universe scanning (min volume filter)
  - Mode: Production (BINANCE_DEMO=false, unverified) or Demo (BINANCE_DEMO=true, default)
  - Implementation: `trader/data/feed.py`, `trader/engine/executor.py`, `trader/engine/protective.py`
  - Trade execution: market orders only; stops are native Binance STOP_MARKET algo orders

**Binance Spot API (basis calculation):**
- Endpoint: `/api/v3/klines` (spot OHLCV)
- Purpose: Compute spot-perp basis history for strategies that trade carry
- Implementation: `trader/data/derivatives.py::DerivFeed.basis_history()`

**LLM / Code Generation:**
- DeepSeek (via OpenAI SDK)
  - Auth: `DEEPSEEK_API_KEY` (required for strategy generation; optional for trader)
  - Endpoint: `https://api.deepseek.com/v1/` (or OpenAI compatible)
  - Client: `openai.OpenAI` (drop-in replacement for DeepSeek)
  - Implementation: `trader/brain/llm.py::BrainLLM` (async wrapper)
  - Purpose: Generate strategy specs, analyze backtest results, theorist autopsies

**Notifications:**
- Telegram Bot API
  - Auth: `TELEGRAM_TOKEN` (bot token), `TELEGRAM_CHAT_ID` (destination)
  - Endpoint: `https://api.telegram.org/bot{token}/sendMessage`
  - Implementation: `trader/notify/telegram.py::Telegram`
  - Messages: trade alerts, state changes (/panic, /halt, /freeze, /resume), status queries
  - Bidirectional: `/tg-listener` daemon thread reads commands from chat, writes intents to `state_kv`

## Data Storage

**Databases:**
- SQLite 3 (local)
  - `data/luffy.db` (WAL mode)
    - Journal: cycles, votes, decisions, outcomes, trades, equity
    - Control: `state_kv` (shared channel for dashboard ↔ kernel)
    - Brain: `brain_events`, `control_events`, `strategies`
  - `data/candles.db`
    - OHLCV: 5y at 4h, 3y at 1h, 1y at 15m; all closed bars only
    - Incremental fetches from Binance with overlap validation
    - No ORM; raw SQL via `trader/core/journal.py`
  - `data/derivs.db`
    - Funding (4-5y, 32 symbols), basis (2y, 29), OI/taker/long-short (~32d, 5)
    - Tables auto-created on first use

**File Storage:**
- Local filesystem only
  - `data/macro_calendar.json` - cached US economic calendar (ForexFactory or Finnhub)
  - `data/doctrine.json` - versioned operating beliefs (Theorist autopsies)
  - `data/agent_weights.json` - measured analyst accuracy (weekly)
  - `data/ewa_state.json` - online expert weights (EWA algorithm state)
  - `data/agent_calibration.json` - per-agent conviction calibration
  - `knowledge/` - Obsidian vault (human-readable narrative) via `trader/knowledge/vault.py`

**Caching:**
- In-memory TTL caches
  - DataFeed: OHLCV cache (TTL-based, 15m-1h frames)
  - DerivFeed: symbol mapping cache
  - MacroGuard: calendar cache (6h TTL, max age 7d)

## Authentication & Identity

**Auth Provider:**
- Custom (stateless)
  - Dashboard: token-based via `DASH_TOKEN` (default "luffy" if unset)
  - Kernel: Binance API keys (`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`)
  - LLM: DeepSeek key (`DEEPSEEK_API_KEY`)
  - Telegram: bot token (`TELEGRAM_TOKEN`)
  - Macro calendar: optional Finnhub key (`FINNHUB_API_KEY`); free tier fails on calendar endpoint
  - Optional deep history: Coinalyze key (`COINALYZE_API_KEY`)

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, New Relic, etc.)
- Logged to `logs/luffy.log` and journaled to `data/luffy.db::control_events`

**Logs:**
- File: `logs/luffy.log` (RotatingFileHandler, multi-threaded, may contain NUL bytes)
- Console: startup, fatal errors
- Database: `data/luffy.db::control_events` for state machine events
- Telegram: alerts (configurable silent mode)

**Heartbeat/Watchdog:**
- `data/heartbeat_luffy.json` - kernel liveness check (timestamp)
- Implementation: `trader/engine/watchdog.py::Heartbeat`
- Dashboard polls this to show kernel age

## External Data Sources (Web Scraping)

**Strategy Ideas:**
- TradingView Pine Script Library
  - Scraper queries tag pages: `btcusdt`, `ethusdt`, `solusdt`, `altcoin`, `scalping`, `breakout`, `trend-trading`
  - Method: HTTP + regex extraction (not API)
  - Implementation: `trader/brain/scraper.py::scrape_tradingview()`
  - Relevance filter: STRATEGY_TERMS regex for cheap screening
  - Queued to `brain_events` table for Strategist consumption

**RSS Feeds (12 default):**
- Cointelegraph, CoinDesk, Decrypt, NewsBTC, CryptoPotato, Medium (algo-trading), Dev.to, arXiv (quantitative finance), Reddit r/algotrading
- Method: raw HTTP + regex extraction (RSS/Atom)
- Implementation: `trader/brain/scraper.py::scrape_feed()`, `trader/brain/crawler.py`
- Two-stream queue: `strategy` (setups) and `research` (market claims)
- Min text length: 80 chars (quality floor)

**US Economic Calendar:**
- **Primary:** Finnhub API (if `FINNHUB_API_KEY` set; free tier 403s on calendar endpoint)
  - Endpoint: `https://finnhub.io/api/v1/calendar/economic`
  - Implementation: `trader/agents/macro_guard.py`
- **Fallback:** ForexFactory (free, keyless)
  - Endpoint: `https://nfs.faireconomy.media/ff_calendar_thisweek.json`
  - Always available if Finnhub fails or key is unset
- Purpose: MacroGuard hard-freezes entries during high-impact US events
- Cache: 6h TTL, backup to `data/macro_calendar.json` (max age 7d)

**Strategy Backtesting (TradingView MCP):**
- Provider: TradingView Pine Script Backtest Engine (via MCP)
- Method: Streamable HTTP JSON-RPC 2.0 (custom MCPClient in-process)
- Implementation: `trader/brain/tv.py::TVClient`, `trader/brain/tv_harness.py`
- Data: Yahoo Finance OHLCV (not Binance)
- Features: walk-forward backtest, robustness verdict, out-of-sample return
- Purpose: Independent second judge for genome validation (TV-valid gate)

**MCP Servers (Generic):**
- Protocol: Streamable HTTP (JSON-RPC 2.0)
- Implementation: `trader/data/mcp_client.py::MCPClient` (lightweight, no SDK)
- Scope: TradingView MCP (mandatory), CoinGecko MCP (optional, research)
- Transport: POST to MCP server with `.mcp.json` configuration
- Timeout: 30 seconds per request
- Session management: auto-reestablish on failure

## Webhooks & Callbacks

**Incoming:**
- Dashboard control mutations → `state_kv` table writes
  - `/panic`, `/halt`, `/freeze`, `/resume` (state changes)
  - Strategy admission/retirement (signal mutations)
  - Leverage, risk, position adjustments

**Outgoing:**
- None detected (no outbound webhooks to external systems)
- Telegram: one-way push (bot sends messages, no callback subscriptions)

## Venue-Specific Constraints

**Binance USDM Futures:**
- Protective stops: STOP_MARKET algo orders (return algoId, not orderId)
  - Reconciliation: `/fapi/v1/algo/*` endpoints (not `/fapi/v1/order`)
  - Lifecycle: Armed at entry, cancelled on exit or timeout, re-armed if naked on boot
  - Implementation: `trader/engine/protective.py` (handles full stop lifecycle)
- Position limits: max 8 concurrent (via config `max_open_positions`)
- Leverage: 5x (configurable, set via `/fapi/v2/leverage`)
- Fees: taker 0.04% (demo measured; production unverified, published VIP0 is 0.05%)
- Funding: charged per 8h settlement (backtest model); real settlement times vary
- Basis: computed from spot + perp klines; Binance starts 2022-08-31 (candles start 2021-08-26)
- OI/taker/long-short: API-capped at 500 rows, ~30d retention (vs. 90d backtest floor) — Coinalyze fills the gap

## Live Trading Constraints

**Execution Frame:** 15m candles (evaluation), 60s cycle interval
**Order Type:** market entry, STOP_MARKET protective stops, reduce-only limit TP (if venue supports)
**Slippage Model:** 0.015 bps/fill (~4 bps at median 4h ATR); deliberately 6x measured slippage to account for volatility expansion on entry
**Cost Model:** 0.04% taker (demo) + funding (venue real series, signed) + slippage
**State:** single writer (kernel), read-mostly dashboard/agents
**Control Channel:** `state_kv` flags (intent-only, kernel executes)

---

*Integration audit: 2026-09-03*
