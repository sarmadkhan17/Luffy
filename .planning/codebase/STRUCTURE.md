# Codebase Structure

**Analysis Date:** 2026-09-03

## Directory Layout

```
trader/
├── trader/
│   ├── agents/              # Analyst agents: 7 vote-only analysts + risk guards
│   │   ├── base.py
│   │   ├── structure.py     # StructureAnalyst (levels/breaks)
│   │   ├── flow.py          # FlowAnalyst (taker imbalance)
│   │   ├── momentum.py       # MomentumAnalyst + RotationAnalyst + ValueAnalyst
│   │   ├── positioning.py    # PositioningAnalyst (funding+OI)
│   │   ├── orderbook_depth.py # DepthScout
│   │   ├── macro_guard.py    # Freeze on macro events
│   │   ├── news_guard.py     # Raise threshold during news churn
│   │   ├── calibration.py    # Conviction sign correction, regime-fit
│   │   ├── indicators.py     # Technical indicators (RSI, ADX, VWAP, etc.)
│   │   ├── regime.py         # Market regime (trending up/down, ranging, volatile)
│   │   ├── validate.py       # Validation replay; measured agent accuracy
│   │   └── weights_online.py # EWA expert weights
│   │
│   ├── api/                 # Dashboard API
│   │   └── graphql_schema.py # GraphQL resolver
│   │
│   ├── brain/               # Strategy generation, analysis, learning
│   │   ├── scraper.py       # Read TradingView library, RSS feeds
│   │   ├── crawler.py       # Deep-read queued ideas
│   │   ├── ideas.py         # Idea queue (brain_events table)
│   │   ├── spec_writer.py   # Strategist: convert idea + doctrine → StrategySpec
│   │   ├── strategist.py    # Strategist role delegation
│   │   ├── analyst.py       # Analyst: admit/refuse via backtesting
│   │   ├── judge.py         # Manager: review book, track performance
│   │   ├── theorist.py      # Theorist: autopsy losses → doctrine
│   │   ├── llm.py           # LLM calls (DeepSeek)
│   │   ├── pine.py          # TradingView Pine script parser/compiler
│   │   ├── tv_harness.py    # TradingView browser automation
│   │   ├── tv.py            # TradingView login/session
│   │   ├── meta_label.py    # Label strategy intent (trend/reversion/carry)
│   │   └── rules.py         # Baseline rules (e.g., ALWAYS_LONG control)
│   │
│   ├── chat/                # Chat interface (not used in kernel loop)
│   │
│   ├── core/                # Shared types and config
│   │   ├── types.py         # Vote, Decision, Snapshot, StrategyState, ControlState
│   │   ├── journal.py       # SQLite writer; thread-safe query wrapper
│   │   └── config.py        # Load config.yaml + .env
│   │
│   ├── dashboard/           # HTTP/GraphQL/WebSocket server
│   │   ├── server.py        # FastAPI app, routing, auth
│   │   └── web/             # Frontend assets (HTML, JS, CSS)
│   │
│   ├── data/                # Data layer: feeds, derivatives, MCP
│   │   ├── feed.py          # DataFeed (candles.db + live API); Universe selection
│   │   ├── derivatives.py   # Funding, OI, taker, long/short, basis (derivs.db)
│   │   ├── coinalyze.py     # Coinalyze API for deep history
│   │   └── mcp_client.py    # MCP server client (LLM context)
│   │
│   ├── engine/              # Trade execution and decision
│   │   ├── orchestrator.py  # Aggregate votes + signals → Decision
│   │   ├── executor.py      # Open/close orders, market execution
│   │   ├── exits.py         # ExitEngine: partial 1.5R, trail, time exit
│   │   ├── protective.py    # ALGO STOP_MARKET management (not ccxt)
│   │   ├── risk.py          # RiskManager: sizing, heat cap, daily breaker
│   │   ├── reconcile.py     # Reconcile venues vs. journal; re-arm naked
│   │   ├── state.py         # ControlStateMachine (ACTIVE/FROZEN/HALTED)
│   │   ├── outcomes.py      # Outcome detection (fill vs. miss)
│   │   ├── outcome_backfill.py # Historical outcome grading
│   │   ├── watchdog.py      # Heartbeat, stall monitor
│   │   └── scan_plan.py     # Which symbols to evaluate per cycle
│   │
│   ├── knowledge/           # Vault interface
│   │   └── vault.py         # Write human-readable record to Obsidian
│   │
│   ├── notify/              # Notifications
│   │   └── telegram.py      # Send/receive Telegram messages, commands
│   │
│   ├── strategy/            # Strategy language, backtesting, library
│   │   ├── spec.py          # StrategySpec dataclass
│   │   ├── dsl.py           # Expression language parser + validator
│   │   ├── features.py      # 71 registered features (EMA, ATR, VWAP, etc.)
│   │   ├── features_deriv.py # Derivative features (funding, OI, basis)
│   │   ├── features_xs.py   # Cross-sectional features (xs_rank, breadth)
│   │   ├── compile.py       # DSL → executable bytecode
│   │   ├── vector_backtest.py # Vectorized backtest engine
│   │   ├── backtest.py      # Backtest harness
│   │   ├── rolling.py       # Rolling-window evaluation context
│   │   ├── null_baseline.py # Circular-rotation null (consistency_p)
│   │   ├── spec_evidence.py # Admission gate: PF, null_p, regimes, overlap
│   │   ├── portfolio_evidence.py # Multi-symbol judgment
│   │   ├── health.py        # Strategy health metrics
│   │   ├── library.py       # Strategy library (DNA → phenotype)
│   │   ├── genome.py        # Legacy genome (deprecated)
│   │   ├── pine_spec.py     # Pine script → StrategySpec conversion
│   │   ├── blend.py         # Blend multiple strategies (unused)
│   │   ├── promotion.py     # Legacy: lifetime PF gate (governs nothing now)
│   │   ├── proposer.py      # Legacy: genome proposer (deleted path, do not restore)
│   │   ├── seed_specs.py    # Seed strategies list
│   │   └── evidence.py      # Evidence types (UNTESTED, PASS, FAIL)
│   │
│   ├── kernel.py            # Kernel entry point; main trade loop
│   └── org.py               # Org chart (metadata only, no trade logic)
│
├── data/                    # Runtime data (gitignored)
│   ├── luffy.db             # Journal: cycles, votes, decisions, outcomes, trades, etc.
│   ├── candles.db           # OHLCV: 5y@4h, 3y@1h, 1y@15m (closed bars only)
│   ├── derivs.db            # Funding (4-5y), basis (2y), OI/taker/ls (~32d)
│   ├── doctrine.json        # Versioned operating beliefs (Theorist-written)
│   ├── agent_weights.json   # Measured analyst accuracy (validation replay)
│   ├── ewa_state.json       # Online expert weights
│   ├── agent_calibration.json # Per-agent conviction calibration
│   ├── seed_specs/          # Pre-authored specs
│   ├── authored_specs/      # User-written specs
│   └── pine/                # Downloaded Pine scripts (versioned)
│
├── knowledge/               # Obsidian vault
│   ├── 50 Daily/            # Daily notes (created by Librarian)
│   ├── 60 Archive/          # Older notes
│   ├── 70 Backtest/         # Spec evidence, test results
│   ├── Analyst Records/     # Per-analyst performance
│   └── ...
│
├── scripts/                 # One-off tools, audits
│   ├── backtest_equivalence.py # Backtest harness truth test
│   ├── bench_vector_backtest.py # Performance benchmark
│   ├── repair_partial_bars.py # Fix truncated candles
│   ├── monitor.py           # Query venue state (ask venue, not journal)
│   ├── deployment_frontier.py # Frontier of risk/return/slot configs
│   ├── screen_mechanisms.py # Mechanism discovery screen
│   ├── universe_selection_test.py # Evaluate universe fit
│   └── ...
│
├── tests/                   # Test suite (748 tests)
│   ├── test_phase0.py       # State machine, control states
│   ├── conftest.py          # pytest fixtures
│   └── ...
│
├── docs/                    # Documentation
│   └── superpowers/         # Implementation plans, audit registers
│
├── .planning/               # This codebase mapping
│   └── codebase/            # ARCHITECTURE.md, STRUCTURE.md, etc.
│
├── config.yaml              # Runtime configuration (database URLs, timeframes, feeds)
├── org.yaml                 # Organization chart (metadata)
├── CLAUDE.md                # Project instructions (authoritative)
├── REQUIREMENTS.md          # Deployment requirements
├── ROADMAP.md               # Project roadmap
└── requirements.txt         # Python dependencies

```

## Directory Purposes

**`trader/agents/`**
- Purpose: Analyst agents that vote on markets
- Contains: Seven scoring analysts (structure, flow, momentum, value, rotation, positioning, depth), macro/news guards, calibration, regime detection, indicator library
- Key files: `base.py` (Analyst contract), each analyst in separate file

**`trader/brain/`**
- Purpose: Strategy generation, testing, and learning
- Contains: Scraper (read external), Crawler (deep-read), Strategist (write specs), Analyst (admit/refuse), Judge (manage), Theorist (autopsy)
- Key files: `scraper.py` (TradingView + RSS), `spec_writer.py` (Strategist), `analyst.py` (Analyst admission gate)

**`trader/core/`**
- Purpose: Shared types and configuration
- Contains: Vote, Decision, Snapshot, Strategy states, Control states; Journal (SQLite writer)
- Key files: `types.py` (vocabulary), `journal.py` (persistence), `config.py` (load config.yaml)

**`trader/engine/`**
- Purpose: Trade execution and decision-making
- Contains: Orchestrator (aggregate), Executor (orders), ExitEngine (exits), RiskManager (sizing), reconcile (venue truth)
- Key files: `orchestrator.py` (decision aggregation), `executor.py` (market orders), `protective.py` (ALGO stops)

**`trader/strategy/`**
- Purpose: Strategy language, backtesting, admission
- Contains: DSL (parser, validator), features (71 registered), backtest engine, null baseline, admission evidence
- Key files: `dsl.py` (expression parser), `features.py` (feature registry), `vector_backtest.py` (simulator), `spec_evidence.py` (admission gate)

**`trader/data/`**
- Purpose: Market data and exchange APIs
- Contains: DataFeed (candles + live), Derivatives (funding/OI), Universe selection, MCP client
- Key files: `feed.py` (DataFeed + Universe), `derivatives.py` (funding/OI/taker)

**`trader/dashboard/`**
- Purpose: Web interface (HTTP/GraphQL/WebSocket)
- Contains: FastAPI app, GraphQL schema, authentication, frontend assets
- Key files: `server.py` (FastAPI setup), `web/` (static assets)

**`trader/knowledge/`**
- Purpose: Vault writer (human-readable narrative)
- Contains: Librarian interface
- Key files: `vault.py` (Obsidian vault writer)

**`data/`**
- Purpose: Runtime data (all gitignored)
- Contains: luffy.db (journal), candles.db (OHLCV), derivs.db (funding/OI), doctrine.json, calibration state
- Generated: Yes
- Committed: No

**`scripts/`**
- Purpose: One-off tools and audits
- Contains: Backtest validation, monitoring, deployment frontier analysis, mechanism screening, universe evaluation
- Key files: `monitor.py` (query venue), `backtest_equivalence.py` (truth test)

**`tests/`**
- Purpose: Test suite (748 tests)
- Contains: Unit tests, integration tests, control flow tests
- Run: `pytest tests/` or `pytest tests/test_phase0.py -k test_state_transitions`

**`docs/superpowers/`**
- Purpose: Implementation plans and audit registers
- Contains: Spec design, audit registers, issue tracking

## Key File Locations

**Entry Points:**
- `trader/kernel.py` — Main kernel (run: `python -m trader.kernel`)
- `trader/dashboard/server.py` — Dashboard (run: `python -m trader.dashboard.server`)

**Configuration:**
- `config.yaml` — All runtime knobs (timeframes, feeds, limits, risk)
- `.env` — Secrets (API keys, tokens)
- `org.yaml` — Organization chart (metadata, no trade logic)

**Core Logic:**
- `trader/core/journal.py` — SQLite writer (single writer, thread-local reader)
- `trader/core/types.py` — Vote, Decision, Snapshot, StrategyState
- `trader/engine/orchestrator.py` — Vote aggregation + decision
- `trader/engine/executor.py` — Order placement
- `trader/agents/` — Seven analysts

**Strategy Language:**
- `trader/strategy/dsl.py` — Expression parser
- `trader/strategy/features.py` — Feature registry (71 features)
- `trader/strategy/vector_backtest.py` — Simulator

**Data Layer:**
- `trader/data/feed.py` — DataFeed (candles + live), Universe
- `trader/data/derivatives.py` — Funding, OI, taker, basis

**Testing:**
- `trader/strategy/spec_evidence.py` — Admission gate (PF, null_p, regimes)
- `scripts/backtest_equivalence.py` — Simulator validation
- `scripts/monitor.py` — Venue state query

## Naming Conventions

**Files:**
- Python: `lowercase_with_underscores.py` (PEP 8)
- Configuration: `lowercase_with_underscores.yaml`
- Data: Underscore-prefixed for internal/temporary: `_name_value.json`

**Functions/Methods:**
- Private: `_name()` (leading underscore)
- Constants: `UPPERCASE_CONSTANT`
- Abbreviations: Full words preferred (e.g., `orchestrator`, not `orch`)

**Variables:**
- Single-letter loop: `i`, `j`, `k` only
- Dataclass fields: `snake_case`
- Enums: `UPPER_CASE` values (e.g., `ControlState.ACTIVE`)

**Types/Classes:**
- Dataclasses: `PascalCase` (e.g., `Vote`, `Decision`, `Snapshot`)
- Enums: `PascalCase` (e.g., `Side`, `Action`, `ControlState`)
- Analysts: `CamelCaseAnalyst` (e.g., `StructureAnalyst`, `MomentumAnalyst`)

**Database:**
- Tables: `snake_case` (e.g., `brain_events`, `state_kv`)
- Columns: `snake_case`

## Where to Add New Code

**New Analyst (voting agent):**
- Primary code: `trader/agents/your_analyst.py`
- Register in: `trader/kernel.py` (AGENTS dict)
- Base class: `trader/agents/base.py` (Analyst ABC)
- Tests: `tests/test_agents_your_analyst.py`

**New Strategy Feature:**
- Feature code: `trader/strategy/features.py` (or `features_deriv.py` for derivatives)
- Registry entry: Add to `FEATURES` dict with domain, arg_specs
- Tests: `tests/test_strategy_features.py`
- Note: Feature must have associated test cases or admission gate will refuse specs using it

**New Strategy (manual author):**
- File: `data/authored_specs/my_strategy.json` or `data/seed_specs/`
- Compile with: `trader/strategy/compile.py`
- Backtest with: `trader/brain/analyst.py` (admission gate)
- Tests: `tests/test_strategy_manual_specs.py`

**New Daemon Thread:**
- Function: `trader/kernel.py` (add to `_daemon_*()` family)
- Thread: Start in `Kernel.boot()` with `threading.Thread(...).start()`
- Logging: Use `log.info()`, `log.warning()`, `log.error()`
- Database: Use `self.journal._tx()` for writes

**Dashboard Endpoint:**
- API: `trader/api/graphql_schema.py` (GraphQL resolver)
- HTTP: `trader/dashboard/server.py` (FastAPI route)
- Frontend: `trader/dashboard/web/` (HTML/JS/CSS)

**New Database Table:**
- Schema: `trader/core/journal.py` (Journal class, schema_version migration)
- Query wrapper: Add to `Journal` class
- Commit: Use `self.journal._tx()` for writes

## Special Directories

**`data/`**
- Purpose: Runtime data
- Generated: Yes (by kernel, scraper, backtest)
- Committed: No (gitignored)
- Key: All databases (.db) and state files (.json) live here

**`knowledge/`**
- Purpose: Obsidian vault (human-readable narrative)
- Generated: Yes (by Librarian, `trader/knowledge/vault.py`)
- Committed: Yes (vault is version-controlled)
- Format: Markdown files with YAML frontmatter

**`scripts/`**
- Purpose: One-off tools (not part of kernel loop)
- Committed: Yes (tested, documented)
- Run: Via command line, not auto-invoked by kernel

**`.planning/codebase/`**
- Purpose: Codebase mapping for `/gsd-*` commands
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes (for team context)
- Files: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md

---

*Structure analysis: 2026-09-03*
