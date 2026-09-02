<!-- refreshed: 2026-09-03 -->
# Architecture

**Analysis Date:** 2026-09-03

## System Overview

Luffy is a dual-process autonomous trading system: a **Kernel** (writer of truth, trade loop + daemon threads) and a **Dashboard** (read-mostly, FastAPI + GraphQL + WebSocket) that share a single SQLite database as their only inter-process communication channel.

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│                              Two Processes                                    │
├─────────────────────────────────────────────┬─────────────────────────────────┤
│  KERNEL (`trader/kernel.py`)                │  DASHBOARD (`trader/dashboard`) │
│  - Trade loop (60s cycle)                   │  - FastAPI + GraphQL + WS       │
│  - Daemon threads (7)                       │  - Read-only to journal         │
│  - Single writer of truth                   │  - Control flags → state_kv     │
│  - Updates: cycles, votes, decisions,       │  - Journalled decisions/trades  │
│    outcomes, trades, equity                 │                                 │
└─────────────────────────────────────────────┴─────────────────────────────────┘
                                  ▲
                                  │
                                  ▼
                    ┌─────────────────────────────┐
                    │   data/luffy.db (SQLite)    │
                    │   - Single source of truth  │
                    │   - tables: cycles, votes,  │
                    │     decisions, outcomes,    │
                    │     trades, equity,         │
                    │     brain_events,           │
                    │     strategies, state_kv    │
                    └─────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **Kernel** | Trade loop orchestration; daemon threads; write truth to journal | `trader/kernel.py` |
| **Universe** | Market selection (3 majors, up to 12 scanned alts); symbols + timeframes | `trader/data/feed.py` |
| **DataFeed** | Candle history + live prices (production API, never demo); multi-timeframe batching | `trader/data/feed.py` |
| **Seven Analysts** | Vote on market mechanisms: structure, momentum, value, rotation, flow, positioning, depth | `trader/agents/*.py` |
| **Orchestrator** | Aggregate analyst votes + strategy signals; apply regime-fit and dynamic thresholds; journal Decision | `trader/engine/orchestrator.py` |
| **RiskManager** | Sizing, heat cap (8 slots, 0.5% risk each), daily loss breaker | `trader/engine/risk.py` |
| **Executor** | Market order placement; native STOP on exchange; fill resolution | `trader/engine/executor.py` |
| **ExitEngine** | Partial (1.5R), trailing stop, time exit; in-process management only | `trader/engine/exits.py` |
| **Scraper** | Read TradingView library, RSS feeds, crawl seeds → queue ideas | `trader/brain/scraper.py`, `trader/brain/crawler.py` |
| **Strategist** | Write StrategySpec from queued idea + doctrine + coverage brief | `trader/brain/spec_writer.py`, `trader/brain/strategist.py` |
| **Analyst** | Backtest spec, measure regimes, cross-symbol consistency, admit or refuse | `trader/brain/analyst.py`, `trader/strategy/spec_evidence.py` |
| **ExitEngine** | Partial (1.5R), trailing stop, time exit; in-process management only | `trader/engine/exits.py` |
| **Librarian** | Write human-readable narrative to Obsidian vault (`knowledge/`) | `trader/knowledge/vault.py` |
| **Theorist** | Autopsy losing clusters → update `data/doctrine.json` | `trader/brain/theorist.py` |
| **Manager** | Review book every 6h, weekly meta-review; retire decayed specs | `trader/brain/judge.py` |
| **Dashboard** | HTTP + GraphQL + WebSocket; control mutations write state_kv flags | `trader/dashboard/server.py` |

## Pattern Overview

**Overall:** Reactive event loop with in-memory strategy population, delegated trading decisions, and single-database synchronization.

**Key Characteristics:**
- **One trade cycle per 60 seconds:** Every cycle votes, decides, executes, exits, and journals atomically.
- **Analyst → Orchestrator → Executor pipeline:** No vetoes — risk is the only hard gate; all opinions flow through orchestration.
- **Snapshot-driven evaluation:** Each symbol gets a point-in-time Snapshot (OHLCV, indicators, BTC context, regime); analysts evaluate it independently.
- **Specification-first strategy creation:** Scraper → Strategist → Analyst (one path only); strategies own entry/exit logic, analysts supply coins + direction.
- **Strategy language (DSL):** Restricted expression over 71 registered features; no dynamic code execution.
- **Journal-centric truth:** Every decision, vote, outcome, trade, and control state change is journalled immediately, visible to dashboard in real time.
- **Daemon threads (7):** Telegram listener (4s), derivs recorder (15m), strategy-mechanism (12h), scraper (4h), crawler (6h), brain-judge (6h), agent-validator (at boot).

## Layers

**Presentation (Dashboard):**
- Purpose: HTTP API, GraphQL schema, WebSocket push, control mutations
- Location: `trader/dashboard/`
- Contains: FastAPI app, GraphQL resolver, web frontend
- Depends on: Journal (read-only), state_kv for control flags
- Used by: Web UI, external API consumers

**Trade Execution (Engine):**
- Purpose: Decide, execute, risk-gate, exit-manage
- Location: `trader/engine/`
- Contains: Orchestrator (aggregation), RiskManager (sizing), Executor (orders), ExitEngine (exits), Protective (ALGO stops)
- Depends on: Analysts (votes), Strategies (signals), Journal, Exchange (CCXT)
- Used by: Kernel cycle

**Market Intelligence (Agents/Brain):**
- Purpose: Analyst votes, strategy generation, backtesting, learning
- Location: `trader/agents/`, `trader/brain/`
- Contains: Seven analysts (structure, momentum, value, rotation, flow, positioning, depth), Macro/NewsGuard, Scraper, Strategist, Analyst (spec evaluator), Theorist
- Depends on: Snapshots, DataFeed, Exchange (API), LLM (DeepSeek for spec writing)
- Used by: Kernel cycle, daemon threads, Dashboard (read)

**Strategy Language & Backtesting:**
- Purpose: Compile specs to executable code; backtest specs against history
- Location: `trader/strategy/`
- Contains: DSL (dsl.py), feature registry (features.py, features_deriv.py, features_xs.py), vector backtest, null baseline
- Depends on: Candles database, feature computation
- Used by: Analyst (admission), Theorist (autopsy), Dashboard (replay)

**Data & Persistence:**
- Purpose: Candle history, derivatives (funding/OI/taker/basis), journal (trades/votes/decisions)
- Location: `trader/data/`, `trader/core/`
- Contains: DataFeed (candles.db, live API), Derivatives recorder, Journal (luffy.db)
- Depends on: Exchange API (Binance), MCP servers (Coinalyze)
- Used by: All layers

**Knowledge:**
- Purpose: Human-readable narrative, organized by date and theme
- Location: `knowledge/`, `trader/knowledge/`
- Contains: Obsidian vault, daily notes, trade records
- Depends on: Journal, Librarian (vault.py)
- Used by: Human review, eventual research

## Data Flow

### Primary Request Path (60s Cycle)

1. **Fetch balance** (`kernel.cycle()`) — Get account equity from exchange
2. **Universe selection** (`Universe.symbols()`) — Determine which markets to scan (3 majors + config)
3. **Snapshot construction** (`_snapshot_for()`) — Build point-in-time Snapshot for each symbol with:
   - OHLCV (from DataFeed, production API, closed bars only)
   - Feature context (EMA, ATR, VWAP, etc.)
   - BTC context (regime, correlation)
   - Funding, OI, taker (from derivatives database)
4. **Analyst votes** (`orchestrator.decide()`) — Seven analysts evaluate Snapshot in parallel
   - StructureAnalyst: levels/breaks
   - MomentumAnalyst: rate of change
   - ValueAnalyst: VWAP mean reversion
   - RotationAnalyst: BTC-led catch-up
   - FlowAnalyst: taker buy/sell imbalance
   - PositioningAnalyst: funding + OI
   - DepthScout: order book
5. **Strategy signals** (`PopulationInference`) — Active specs (if any) emit boolean signals
6. **Orchestration** (`Orchestrator.decide()`) — Aggregate votes + strategy signals:
   - Apply regime-fit multipliers
   - Apply agent calibration (conviction signed correction)
   - Weight by measured accuracy (from validation)
   - Apply dynamic threshold (agreement tightens, conflict loosens)
   - Apply HTF veto (strong 4h trend refuses counter-trend)
   - Apply news blackout (raise threshold during macro events)
7. **Journal Decision** — Record the aggregated score, entry allowed, conviction, confidence
8. **Risk gate** (`RiskManager.check_entry()`) — Validate sizing, heat cap, daily loss breaker
9. **Execution** (`Executor.open()`) — Market order + native STOP_MARKET on exchange
10. **Exit management** (`ExitEngine.manage()`) — In-process trail, partial, time exit (not exchange-managed)
11. **Outcome resolution** (`resolve_pending()`) — Detect fills via exchange polling; backfill PnL and mark closed

### Data Persistence Points

- **Every cycle (60s):** Journal cycles, votes, decisions, outcomes, trades, equity
- **Every 15m:** Derivatives recorder writes funding, OI, taker, long/short, basis
- **Every 60 cycles (~1h):** Brain/vault tick (resolve outcomes, update strategies)
- **Every 360 cycles (~6h):** Theorist autopsy (cluster losses, update doctrine)
- **Every 12h:** Strategy-mechanism thread retires decayed specs, writes one new candidate
- **Every 4h:** Scraper reads external sources, queues ideas
- **Every 6h:** Crawler deep-reads queued ideas, Judge reviews the book

### State Machine

```
ACTIVE → entries allowed, exits auto-managed
  ↓
FROZEN → no new entries, exits auto-managed (operator, macro guard, daily loss breaker)
  ↓
HALTED → neither, exchange stops stay armed
```

Control transitions are journalled via `state_kv` table. Dashboard writes control flags; Kernel picks them up next cycle.

## Key Abstractions

**Snapshot:**
- Purpose: Point-in-time market and context data for one symbol, one execution timeframe
- Fields: symbol, side (LONG/SHORT), close, open, high, low, volume, regime, features, BTC context, funding, OI
- Evaluation: Analysts read Snapshot; strategies read Snapshot (via rolling/live evaluator)
- Location: `trader/core/types.py`

**Vote:**
- Purpose: Analyst's scored opinion on a market
- Fields: agent name, symbol, side (LONG/SHORT/FLAT), conviction (-1..+1), confidence (0..1), rationale, metadata
- Never a veto — always feeds orchestration
- Location: `trader/core/types.py`

**Decision:**
- Purpose: Orchestrator's aggregated opinion and risk check result
- Fields: symbol, action (BUY/SELL/HOLD), conviction, confidence, entry_allowed (yes/no), blocked_reason, strategy_signal (optional)
- Always journalled even if rejected by risk
- Location: `trader/core/types.py`

**StrategySpec:**
- Purpose: Executable strategy definition
- Fields: id, entry expression (DSL), exit (target RR, trail geometry), universe (which symbols), state (proposed/backtest/paper/active/retired), validated metrics (PF, null_p)
- Immutable once admitted; state changes only
- Location: `trader/strategy/spec.py`

**PopulationInference:**
- Purpose: Live evaluation context for active strategies
- Provides: Signal (boolean entry) for each symbol each cycle
- Location: `trader/engine/orchestrator.py`

## Entry Points

**Kernel:**
- Location: `trader/kernel.py`
- Triggers: `python -m trader.kernel` or `python -m trader.kernel --status` or `python -m trader.kernel --panic`
- Responsibilities: Boot, run trade loop, manage daemon threads, handle signals, flatten on panic

**Dashboard:**
- Location: `trader/dashboard/server.py`
- Triggers: `python -m trader.dashboard.server` (runs on :8080)
- Responsibilities: HTTP/GraphQL/WebSocket, read journal, write control flags

**Brain (Strategy Mechanism):**
- Location: `trader/brain/spec_writer.py`, `trader/brain/analyst.py`
- Triggers: Daemon thread runs every 12h; manual via `brain/judge.py`
- Responsibilities: Write new spec, admit/refuse via backtesting

**Backtester:**
- Location: `trader/strategy/vector_backtest.py`
- Triggers: Analyst admission gate, Dashboard replay
- Responsibilities: Simulate spec against historical candles, compute PF, null_p, regime breakdown

## Architectural Constraints

- **Single writer:** Kernel is the only writer to luffy.db; Dashboard writes only state_kv flags, never decisions or trades
- **Point-in-time evaluation:** Features use `closed_bars` only; a forming bar is never treated as closed (this was a major bug fixed 2026-09-02)
- **No vetoes in orchestration:** Analysts vote, RiskManager gates (hard constraint on heat cap and daily loss), Executor executes or skips with reason
- **Strategy universe is fixed:** A spec's `include` field defines which symbols it trades; orchestrator enforces `ScanPlan.wants()` membership
- **No circular imports:** Strategy DSL cannot reference position state or order book history (order book is not stored anywhere)
- **Protective stops are ALGO orders:** Native STOP_MARKET on exchange; reconcile handles orphans at boot
- **Closed-bar signal persistence:** A signal from a closed bar persists for that bar's duration, so an entry delayed by risk can fill hours later
- **Feature domain validation:** A domain that real data exceeds silently mis-tunes every spec using the feature
- **ATR multiple is timeframe-specific:** A spec validated at 4h ATR 2.0 (e.g., BTC 2.12%) reads as 0.40% at 15m execution; SpecExit scales the multiple by timeframe
- **NaN is default, never 0:** Missing information is NaN; a fabricated 0.0 fires trades on non-existent data

## Anti-Patterns

### Widening the admission gate by example

**What happens:** A test fails, so the evidence bar gets lowered ("oh, just 15 trades instead of 20" or "p < 0.05 instead of 0.01").

**Why it's wrong:** The gate was calibrated against what works; a gate that rejects the book's own strategy is making a claim about the gate's power first. `screen_mechanisms.py` runs the known-good control (Donchian) through the same gate and refuses it, proving the gate has power. Lowering it hides that you are over-fitting to a small dataset, not that the gate is too strict.

**Do this instead:** Run the known-good input through the gate first. If it passes, tighten the gate. If it fails, debug the input or the feature set, never the threshold.

### Increasing position size to chase return

**What happens:** "The strategy works but yield is low, so let's raise risk from 0.5% to 1.0%, or raise the 8-slot cap to 12."

**Why it's wrong:** The deployment is already at optimum (`deployment_frontier.py`): 8 slots at 0.5% risk reads 17.3%/yr at 27.3% drawdown; 4 slots reads worse (fewer edges); 12 slots reads worse (correlated positions become one). Raising risk scales return and drawdown together at ~0.63-0.70x; there is no free return in the configuration.

**Do this instead:** Find a second mechanism. The book is signal-rich and capital-poor; the profit is in breadth, not size per trade.

### Applying a filter to improve PF

**What happens:** The strategy reads PF 1.42 on 487 trades. Add a carry condition: PF 1.43 on 415 trades (15% fewer). Conclude the filter improved PF.

**Why it's wrong:** PF is a ratio and hides what happened: the removed 72 trades carried the bulk of compounded return even though they were individually near break-even. CAGR fell from 17.3% to 7.6%. Judge refinements on compound return, not on ratios.

**Do this instead:** Report paired test: baseline vs. filtered, showing n, PF, median null-p, OOS trades, CAGR, maxDD. A real improvement lifts CAGR, not PF.

### Treating backtest results as discovery evidence

**What happens:** Mechanism screens 500+ trades on 15 symbols, reads PF 1.22, p=1.1e-03. Conclude it works.

**Why it's wrong:** Discovery evidence is a description of the symbols it was found on. Run the same spec on 9 symbols it has never seen: PF 0.65, p=0.27 (no-edge line). Split discovery from held-out in the screen; admit nothing that fails either.

**Do this instead:** Discovery universe (19 symbols) + held-out universe (17 symbols). Both gates must pass. Run the known-good control through this gate to calibrate power.

### Signalling on forming bars

**What happens:** A 4h close is coming, but live evaluator reads the last row (which is open/hi/lo/close truncated to what has traded so far) and triggers a breakout entry "across a level that retraced 5 minutes later".

**Why it's wrong:** The backtest fills at 4h close; live the entry goes out best 60s later and often much later (risk blocks it). A signal from a forming bar is not the signal that earned the statistics; it is a signal to a truncated price that does not exist yet.

**Do this instead:** Evaluate only closed bars. `core/types.closed_bars` is the definition. Live evaluator advances only when a new 4h bar opens. A signal persists for its bar's duration, so risk-delayed entries are still valid — but they are valid entries to the closed bar, not the forming one. Store `signal_bar_age_min` to measure the cost of delay.

---

*Architecture analysis: 2026-09-03*
