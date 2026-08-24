# LUFFY — Build Roadmap

Working doc of phases, deliverables, and exit criteria. Spec: REQUIREMENTS.md.

## Phase 0 — Skeleton (days 1–2)
- [x] Repo scaffold `~/trader`
- [ ] Package layout + config loader + .env (live keys already copied)
- [ ] Journal schema v1 (cycles/votes/decisions/outcomes/trades/equity/brain_events)
- [ ] Data feed (ccxt live) + universe scanner
- [ ] Risk manager: heat cap, staged de-risk, daily breaker
- [ ] Control state machine (ACTIVE/FROZEN/HALTED) + panic flatten
- [ ] Watchdog heartbeat + auto-restart + exchange reconciliation on boot

**Exit:** Luffy starts/stops/kills safely; crash mid-position reconciles cleanly;
journal writes verified. No trading yet.

## Phase 1 — Trade & Record · GOES LIVE (days 3–7)
- [ ] 5 analysts + regime layer
- [ ] Seed strategies (fixed params): ema_trend, vwap_fade, breakout_retest,
      sweep_reversal, rotation_momo
- [ ] Orchestrator (regime-weighted aggregation, no vetoes)
- [ ] Live executor: spot+futures, native SL/TP at entry, fees+slippage+funding math
- [ ] Outcome resolution job (1h/4h/24h)
- [ ] Minimal dashboard: equity, positions, decision feed, controls
- [ ] Telegram alerts + /panic

**Exit:** live trades executing with 0.5× proving sizing; every decision +
rejection journaled; restart-safe. Runs ≥48h unattended without incident.

## Phase 2 — Learn (week 2)
- [ ] Vectorized backtester w/ costs; walk-forward harness
- [ ] Genome schema + seed-family parameterization
- [ ] Anti-overfit gauntlet (proposal caps, holdout, lineage flags)
- [ ] Strategist loop (event-driven reviews → promote/mutate/retire)
- [ ] Statistical demotion engine
- [ ] Strategy lab UI

**Exit:** first brain-mutated strategy passes probation → ACTIVE.

## Phase 3 — Research (week 3)
- [ ] MCP client manager + obsidian-mcp (+ file fallback)
- [ ] Vault scaffolding + note writers (theories/postmortems/playbooks)
- [ ] MarketTrace conditional base-rates into doctrine
- [ ] Theorist internal loop: journal mining → doctrine.json + vault
- [ ] TradingView public MCP harvesting → genome proposals

**Exit:** vault grows autonomously; ≥1 harvested concept reaches backtest.

## Phase 4 — Become Jarvis (week 4)
- [ ] Intent profile (owner goals/constraints) referenced by all layers
- [ ] Conversation engine (Telegram + dashboard chat) over GraphQL tools:
      explain decisions, run commands, report state — grounded in journal
- [ ] Morning/evening briefings; proactive anomaly alerts
- [ ] Self-monitoring: behavior-drift detection → auto-FROZEN when confused

**Exit:** owner interrogates any decision; gets cited, honest answers.

## Phase 5 — Trust & Scale (ongoing)
- Metric-gated capital scaling; weekly auto-postmortems
- Optional: distill custom model from journal corpus (cloud GPU)

## Salvaged patterns from the old project
Heartbeat watchdog · exchange reconciliation · DeepSeek usage tracking.
Everything else (gate stacks, static weights, coordinator) stays dead.
