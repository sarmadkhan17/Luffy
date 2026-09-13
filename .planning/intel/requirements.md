# Requirements

Source: `REQUIREMENTS.md` — "LUFFY — Requirements Specification v1.0
(FROZEN)", classified PRD, manifest precedence 6 (outranked by CLAUDE.md
precedence 0 and every SPEC/most DOC plans). Many sections below describe a
state later superseded by measurement and architecture change recorded in
CLAUDE.md; see `INGEST-CONFLICTS.md` for the specific auto-resolved
contradictions. Requirement text is preserved verbatim from the source per
extraction discipline — supersession is recorded as a conflict, not by
editing the requirement.

## REQ-identity-mission
- source: REQUIREMENTS.md
- description: Luffy is a closed-loop trading organism, not a signal bot — observe (analysts measure, never decide) → decide (strategies + orchestrator reach weighted consensus) → act (execute on Binance with real money, protective stops native on-exchange) → record (every cycle/vote/decision/outcome journaled) → learn (outcomes feed statistical demotion, brain verdicts, research) → explain (answer any question about its own behavior from its own data).
- acceptance: absent (identity statement, not independently testable in the source)
- scope: system identity, decision loop

## REQ-capital-deployment
- source: REQUIREMENTS.md
- description: Capital $2,000+ real USDT; mode LIVE from Phase 1 (no demo phase); first 30 live trades get an automatic 0.5x position-size multiplier (proving period); host is the owner's laptop VM (Ubuntu, 2 cores/8GB RAM/no GPU), always-on; all SL/TP placed as exchange-native orders at entry so laptop death does not unprotect positions; heartbeat watchdog + auto-restart + exchange reconciliation on boot.
- acceptance: absent
- scope: capital, deployment mode, crash safety

## REQ-autonomy-control-states
- source: REQUIREMENTS.md
- description: Fully autonomous within three operator-controlled states (ACTIVE, FROZEN, HALTED), reachable via dashboard buttons and natural language with identical semantics. Global actions: close_all, close_symbol(X), switch spot⇄futures (new entries only), kill/pause strategy, resize risk. Panic button flattens everything.
- acceptance: state table — ACTIVE: new entries yes, positions managed normally, brain loops running. FROZEN: new entries blocked, positions managed to natural close, brain loops running. HALTED: new entries blocked, exits manual-only (exchange stops remain armed), brain loops paused.
- scope: control states, autonomy

## REQ-markets-universe
- source: REQUIREMENTS.md
- description: Binance spot + USDT-M futures; universe = majors BTC/ETH/SOL always + top-12 USDT alts by quote volume, rescanned 4h; filters ≥$100M daily volume, price ≥$0.5, stablecoins excluded, coins <90d listing age excluded; timeframes 15m execution, 1h/4h context, 60s scan interval; strategy genes declare market compatibility (spot cannot short).
- acceptance: absent
- scope: markets, universe, timeframes

## REQ-agent-architecture
- source: REQUIREMENTS.md
- description: Two-layer agent architecture. Layer 1 — five analysts (structure, flow, momentum, value, rotation) output Votes (conviction/confidence/evidence); a regime layer (ADX + volatility clustering) classifies TRENDING_UP/DOWN, RANGING, VOLATILE and scales conviction by mechanism-fit; analysts have no veto power, weights reflect measured track record. Layer 2 — a Strategy Genome System (`hypothesis`, `family: momo|meanrev|breakout|rotation|carry`, `regime_filter`, entry/exit/universe gene schema, `invalidation`), lifecycle PROPOSED → BACKTEST → PAPER(probation) → ACTIVE → DEMOTED/RETIRED.
- acceptance: absent
- scope: analyst layer, strategy genome layer

## REQ-learning-system
- source: REQUIREMENTS.md
- description: Journal (SQLite/WAL) records cycles, votes, decisions (incl. skipped setups), outcome resolution at 1h/4h/24h forward returns, trades, equity curve, brain events, with rejected setups tracked with identical rigor. Statistical layer demotes on drawdown/consecutive losses and feeds agent-accuracy vote weighting. Brain layer (event-driven) fires strategy review at ≥8 new closed trades OR 7 days elapsed (never pure clock); DeepSeek R1 autopsy produces promote/mutate/retire with written rationale. Anti-overfit gauntlet: ≤6 proposals/day, walk-forward with untouched holdout, PF ≥1.15, ≥30 trades, DD ≤12%, lineage tracking flags families that pass together then fail together. Doctrine: machine-readable `doctrine.json` (versioned) plus human narrative in the Obsidian vault.
- acceptance: absent
- scope: journal, statistical layer, brain layer, doctrine

## REQ-llm-strategy
- source: REQUIREMENTS.md
- description: Provider-swappable LLM interface; DeepSeek V3 (`deepseek-chat`) for proposals/summaries, R1 (`deepseek-reasoner`) for autopsies/verdicts at launch. Personalization via RAG over journal + vault from day one. Token budgets: per-loop caps + daily hard stop; exhausted brain goes read-only, seed strategies keep trading, nothing halts mid-position. Phase 5 option: fine-tune owner's own model from the journal corpus (cloud GPU rental); local-GPU inference out of scope.
- acceptance: absent
- scope: LLM provider, token budgets

## REQ-mcp-integration
- source: REQUIREMENTS.md
- description: MCP servers used only in slow loops, never the hot trading path: `tradingview-mcp` (public, MIT) for TA/screeners/backtests with walk-forward overfit verdicts; `tradingview-mcp` (authenticated) for Pine-script source harvest / community ideas (Phase 3+); `obsidian-mcp` (Python variant) for vault read/write/search with a direct-file fallback; MarketTrace (hosted, free) for cross-exchange funding/OI/liquidation context. Hot path (OHLCV, funding, OI for live decisions) uses direct ccxt/Binance fapi.
- acceptance: absent
- scope: MCP servers, data sourcing

## REQ-knowledge-vault
- source: REQUIREMENTS.md
- description: `~/trader/knowledge/` is an Obsidian vault holding theories, regime playbooks, postmortems, strategy lineages, daily reviews (YAML frontmatter + wikilinks) — a narrative layer over `doctrine.json`, owner-readable anytime.
- acceptance: absent
- scope: knowledge vault

## REQ-risk-envelope
- source: REQUIREMENTS.md
- description: Risk per trade 5% equity (x0.5 during first-30 proving period); portfolio heat cap ≤15% total open risk; per-symbol cap ≤8%; max open positions 10; daily loss circuit breaker -6% stops new entries until UTC midnight; staged de-risk -8% DD → sizes x0.5, -12% → x0.25, -20% → HALTED; costs (taker fees + ATR-scaled slippage + perp funding) modeled from day 1 in all P&L math.
- acceptance: absent
- scope: risk management, position sizing, circuit breakers

## REQ-interfaces
- source: REQUIREMENTS.md
- description: GraphQL API (Strawberry/FastAPI) with queries (equity, decisions, votes, agents, strategies, trades, brain events) and mutations (state, close-all, market switch, strategy lifecycle), serving both the dashboard and Luffy's own assistant tool-calls. Telegram as an equal citizen: full conversation, morning/evening briefings, trade alerts, `/panic`, all state commands. Dashboard (FastAPI + HTMX/Alpine + TradingView lightweight-charts, WebSocket push, Tailscale-VPN-only, login-protected) with five tabs: Overview, Trades, System, OS (Decisions / Strategy Lab / Agents / Brain sub-tabs), Luffy (chart + conversation split pane).
- acceptance: absent
- scope: GraphQL API, Telegram, dashboard tabs

## REQ-success-criteria-v1
- source: REQUIREMENTS.md
- description: v1 success over 4 weeks live — ≥40 executed trades; net profit factor >1.15 after fees/slippage/funding; max drawdown <10%; zero unreconciled positions after any restart; 100% decision-journal coverage (executed AND skipped); ≥3 strategies surviving natural selection, ≥1 brain-born through probation; owner can interrogate Luffy about any decision and receive a grounded, cited answer from its own journal.
- acceptance: as stated in description (this section IS the acceptance criteria)
- scope: v1 success criteria
