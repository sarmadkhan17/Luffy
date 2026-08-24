# LUFFY — Requirements Specification v1.0 (FROZEN)

> Personal autonomous crypto-trading OS. Named Luffy. Lives on the owner's
> laptop (VM), trades Binance spot & USDT-M futures **live**, learns from every
> decision, explains itself when asked.

## 1. Identity & Mission

Luffy is not a signal bot. It is a closed-loop trading organism:

1. **Observe** — analyst agents measure market mechanisms, never decide
2. **Decide** — strategies + orchestrator reach a weighted consensus
3. **Act** — execute on Binance with real money, protective stops native on-exchange
4. **Record** — every cycle, vote, decision (including rejections), outcome journaled
5. **Learn** — outcomes feed statistical demotion, brain verdicts, and research
6. **Explain** — answer any question about its own behavior from its own data

## 2. Capital & Deployment

| Item | Decision |
|---|---|
| Capital | $2,000+ real USDT |
| Mode | LIVE from Phase 1 (no demo phase) |
| First 30 live trades | Automatic 0.5× position-size multiplier (proving period) |
| Host | Owner's laptop VM (Ubuntu, 2 cores / 8GB RAM / no GPU), always-on |
| Crash safety | All SL/TP placed as **exchange-native orders** at entry — laptop death does not unprotect positions. Heartbeat watchdog + auto-restart + exchange reconciliation on boot |

## 3. Autonomy & Control

Fully autonomous within three operator-controlled states, reachable via
dashboard buttons **and** natural language (identical semantics):

| State | New entries | Open positions | Brain loops |
|---|---|---|---|
| `ACTIVE` | yes | managed normally | running |
| `FROZEN` | **blocked** | managed to natural close (SL/TP/exits live) | running |
| `HALTED` | blocked | exits manual-only; exchange stops remain armed | paused |

Global actions: `close_all`, `close_symbol(X)`, `switch spot⇄futures`
(applies to new entries only; existing positions wind down under their native
market), `kill/pause strategy`, `resize risk`. Panic button flattens everything.

## 4. Markets & Universe

- Binance spot + USDT-M futures (existing live API keys)
- Universe: majors BTC/ETH/SOL always + top-12 USDT alts by quote volume,
  rescanned 4h; filters: ≥$100M daily volume, price ≥$0.5, stablecoins excluded,
  coins <90d listing age excluded (insufficient history)
- Timeframes: 15m execution; 1h/4h context; 60s scan interval
- Strategy genes declare market compatibility (spot cannot short)

## 5. Agent Architecture (both layers)

### Layer 1 — Analysts (measure mechanisms, output Votes: conviction/confidence/evidence)

| Analyst | Theory | Measures |
|---|---|---|
| structure | Auction Market Theory / Wyckoff | liquidity sweeps, BOS/CHoCH, effort-vs-result absorption |
| flow | Market microstructure | order-book imbalance, CVD divergence, funding crowding, OI shifts |
| momentum | Behavioral finance | EMA structure + ADX-quality trend, breakout quality |
| value | Statistical arbitrage | z-score vs anchored VWAP, vol-normalized extremes |
| rotation | Cross-asset capital flows | BTC.D rate-of-change, ETH/BTC risk appetite, beta grouping |

Regime layer above them (ADX + volatility clustering) classifies
TRENDING_UP/DOWN, RANGING, VOLATILE and scales conviction by mechanism-fit.
Analysts have **no veto power**; weights reflect measured track records.

### Layer 2 — Strategy Genome System (brain-composed, evolutionarily selected)

```yaml
StrategyGenome:
  hypothesis: str          # must cite its inefficiency; vague = rejected
  family: momo|meanrev|breakout|rotation|carry
  regime_filter: str       # regimes where edge is paid
  entry/exit/universe: dict genes (constrained schema)
  invalidation: str        # falsification clause — required
```

Lifecycle: `PROPOSED → BACKTEST → PAPER(probation) → ACTIVE → DEMOTED/RETIRED`

## 6. Learning System

- **Journal** (SQLite/WAL): cycles, votes, decisions incl. skipped setups,
  outcome resolution at 1h/4h/24h forward returns, trades, equity curve,
  brain events. Rejected setups tracked with identical rigor.
- **Statistical layer** (instant, deterministic): demote on drawdown /
  consecutive losses; agent accuracy feeds vote weighting.
- **Brain layer** (event-driven): strategy review fires at ≥8 new closed
  trades OR 7 days elapsed — never pure clock. DeepSeek R1 autopsy →
  promote / mutate / retire with written rationale.
- **Anti-overfit gauntlet**: ≤6 proposals/day; walk-forward with untouched
  holdout; PF ≥1.15, ≥30 trades, DD ≤12%; lineage tracking flags families
  that pass together then fail together.
- **Doctrine**: machine-readable `doctrine.json` (brain's operating beliefs,
  versioned) + human narrative in Obsidian vault.

## 7. LLM Strategy

- Provider-swappable interface. **DeepSeek V3 (`deepseek-chat`) for proposals/
  summaries, R1 (`deepseek-reasoner`) for autopsies/verdicts** at launch.
- Personalization via RAG over journal + vault from day one.
- Token budgets: per-loop caps + daily hard stop; exhausted brain goes
  read-only — seed strategies keep trading, nothing halts mid-position.
- Phase 5 option: fine-tune owner's own model from accumulated journal corpus
  (cloud GPU rental); local-GPU inference out of scope (VM has none).

## 8. MCP Integration (slow loops only — never the hot trading path)

| Server | Role |
|---|---|
| `tradingview-mcp` (public, MIT) | TA, screeners, backtests with walk-forward overfit verdicts |
| `tradingview-mcp` (authenticated) | Pine-script source harvest, community ideas *(Phase 3+, owner TV account)* |
| `obsidian-mcp` (Python variant) | vault read/write/search; direct-file fallback coded in |
| MarketTrace (hosted, free) | cross-exchange funding/OI/liquidation context + conditional base rates |

Hot path (OHLCV, funding, OI for live decisions) = direct ccxt/Binance fapi.

## 9. Knowledge Vault (Obsidian)

`~/trader/knowledge/` is an Obsidian vault: theories, regime playbooks,
postmortems, strategy lineages, daily reviews — YAML frontmatter + wikilinks.
Narrative layer over `doctrine.json`; owner-readable anytime.

## 10. Risk Envelope (moderate, amended for correlation reality)

| Rule | Value |
|---|---|
| Risk per trade | 5% equity (×0.5 during first-30 proving period) |
| Portfolio heat cap | ≤15% total open risk |
| Per-symbol cap | ≤8% |
| Max open positions | 10 |
| Daily loss circuit breaker | −6% stops new entries until UTC midnight |
| Staged de-risk | −8% DD → sizes ×0.5; −12% → ×0.25; −20% → HALTED |
| Costs modeled from day 1 | taker fees + ATR-scaled slippage + perp funding in all P&L math |

## 11. Interfaces

- **GraphQL API** (Strawberry/FastAPI): queries (equity, decisions, votes,
  agents, strategies, trades, brain events) + mutations (state, close-all,
  market switch, strategy lifecycle). Serves dashboard AND Luffy's own
  assistant tool-calls.
- **Dashboard web**: equity/P&L analytics, live decision feed ("why we
  traded/skipped"), strategy lab (lineage, probation stats, brain verdicts),
  control buttons, mode switch.
- **Telegram (equal citizen)**: full conversation, morning/evening briefings,
  trade alerts, `/panic`, all state commands.

## 12. Success Criteria (v1, 4 weeks live)

- ≥40 executed trades; net profit factor >1.15 **after fees/slippage/funding**
- Max drawdown <10%; zero unreconciled positions after any restart
- 100% decision-journal coverage (executed AND skipped)
- ≥3 strategies surviving natural selection; ≥1 brain-born through probation
- Owner can interrogate Luffy about any decision and receive a grounded,
  cited answer from its own journal
