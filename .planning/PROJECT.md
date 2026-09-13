# Luffy

## What This Is

Luffy is an autonomous crypto futures trading system (Binance USDT-M perps,
currently routed to Binance Demo via `BINANCE_DEMO=true`). It reads the
market and the internet, writes its own strategies, tests them against
stored history, trades the ones that work **now**, and retires them when
they stop. It runs as two processes — a Kernel (sole writer of truth) and a
Dashboard (read-mostly) — sharing one SQLite journal (`data/luffy.db`), on a
single always-on Linux VM (2 cores, ~8GB RAM, no GPU).

## Core Value

No edge lasts. Selection asks "is this working now," never "did this
survive five years" — and forward (live) performance, not backtest, is the
only discriminator the book's one live strategy has left to prove itself
against.

## Business Context

- **Customer**: Sarmad (solo operator) — Luffy is a personal automated
  trading system, not a product with external users.
- **Revenue model**: net trading proceeds (realized P&L, commission,
  funding) off the venue's own account.
- **Success metric**: the rent check — ≥$50/week net off the venue's income
  ledger (`fapiPrivateGetIncome`), judged strictly week by week (a good week
  never carries a bad one). First week starts 2026-09-14. Reports only,
  never stops the system — death is a human act.
- **Strategy notes**: `CLAUDE.md` (repo root, reconciled against the code
  2026-09-13) is the authoritative, living system doc; `docs/superpowers/specs/`
  holds the design docs behind each subsystem.

## Requirements

### Validated

<!-- Shipped and confirmed built, per CLAUDE.md 2026-09-13. Brownfield system — this is existing capability, not roadmap work. -->

- ✓ Two-process architecture: Kernel (sole writer) + Dashboard (read-mostly),
  sharing `data/luffy.db` via the `state_kv` table (control flags only).
- ✓ 60s trade cycle: 7 analysts vote (never veto) → `Orchestrator.decide()`
  lets the strategy signal set the score (votes journalled) → `RiskManager` gates → `Executor` places
  order + native exchange STOP → `ExitEngine` manages target/trail in-process.
- ✓ Strategy-first cutover is LIVE: `require_strategy_signal: true` and
  `strategy_leads: true` — once a strategy signals, it sets the score. The
  7-analyst blend still computes and journals every vote, for measurement
  only. Both flags stay operator-reversible config, never auto-flipped.
- ✓ Single strategy-creation path: Scraper queues → Strategist writes
  `StrategySpec` → Analyst admits, guarded by
  `tests/test_single_creation_path.py`. The legacy Scraper→genome path and
  the legacy Strategist "mutate" review path were both deleted 2026-09-11
  after the latter resurrected a retired genome into all 8 trade slots.
- ✓ Admission gate (current form): pooled PF ≥1.15 over ≥20 trades in a
  90-day-doubling window (capped 365d), signal overlap <0.6 against the
  book, cross-symbol rotation-null consistency p<0.01; fewer than 4 testable
  symbols is REFUSED, not silently admitted (closed the 2026-09-11
  admission hole).
- ✓ Retirement: 30-day PF <0.85 over ≥10 trades is decay; too few trades is
  idle, not decayed. No lifetime pass/fail gates — under one, all 27
  candidates scored zero.
- ✓ Strategy language: restricted expression DSL over a 73-feature registry
  (`strategy/features.py`, `features_deriv.py`, `features_xs.py`); missing
  data is always NaN, never a fabricated default; every evaluation is
  point-in-time (`core.types.closed_bars`); `ref(key, expr)` (2026-09-11)
  aligns cross-asset reference markets the same way.
- ✓ Risk envelope: max 8 open trades, 0.5% risk each — measured optimum
  (`deployment_frontier.py`); more slots (tested to 12) or more risk per
  trade both make return and drawdown worse.
- ✓ Protective stops are native exchange ALGO orders (`STOP_MARKET`),
  placed/cancelled/enumerated exclusively through `trader/engine/protective.py`;
  target and trail are worked in-process by `ExitEngine` — if the kernel
  dies, only the stop protects.
- ✓ Rent check daemon: weekly $50 net bar read from the venue's own income
  ledger (never `trades`), Telegram tally + Monday verdict, reports only.
- ✓ Executor's overall exit-P&L booking (2026-09-11 fix): `close()`/
  `close_partial()` book the order's own fills and re-base the trade to
  `venue_realized_pnl`; an estimate is booked (and flagged via a
  `pnl_estimated` event) only when the venue won't answer after 3 retries.
- ✓ Research pipeline phases 0-2: data-only postmortem/doctrine (LLM Judge
  and Theorist removed), the reference-market store + `ref()` operator, and
  the combinatorial search machine (vocabulary, slices, thresholds,
  combo/evaluate/growth/control/ledger/planner/job/runner) — measured
  against the real store 2026-09-13 (4h horizon POWERED, 1h/15m
  UNDERPOWERED and refused). Ships disabled (`research.enabled: false`)
  pending operator review of that measurement.
- ✓ Watchdog supervision: `scripts/watchdog.sh` (cron, `@reboot` + every 5
  min) restarts a dead kernel/dashboard or one whose heartbeat is stale.

### Active

<!-- Genuinely open work, grounded in CLAUDE.md's "Open faults" / measurement sections. Full detail in REQUIREMENTS.md. -->

- [ ] FIX-01: `macro_guard` wall-clock-dependent test passes reliably
- [x] FIX-02: Executor books the venue's real per-fill commission (verified already in place 2026-09-13)
- [ ] FIX-03: Production (non-demo) taker fee is measured before any live-mode switch
- [ ] FIX-04: `vector_backtest.WARMUP` no longer silently discards higher-timeframe test data
- [ ] FVAL-01: Donchian Breakout Trail's forward trades tracked against its backtest read
- [ ] FVAL-02: The null-less admitted funding-filtered spec's forward trades tracked against its admission read
- [ ] FVAL-03: `signal_bar_age_min` measured to quantify signal-to-fill delay cost
- [ ] RPIPE-01: Research pipeline Referee (gate 1, LORD++ ledger/brake, gate 3, handoff to `_mechanism_once`)
- [ ] RPIPE-02: Research pipeline Reason (LLM thesis, gate 2, `UNEXPLAINED`)
- [ ] RPIPE-03: Research pipeline weekly reporting + Strategist LLM-path cutover
- [ ] RAGENT-01: Researcher agent (Thesis pre-registration, confound battery, hand-off to Strategist)

### Out of Scope

- Reintroducing lifetime pass/fail admission gates — governing premise: no
  edge lasts; under a lifetime gate all 27 candidates scored zero.
- Raising risk/position caps above 8 slots / 0.5% risk — measured optimum;
  more slots or more risk per trade makes both return and drawdown worse.
- A second strategy-creation path — deleted 2026-09-11 after it resurrected
  a retired genome into all 8 trade slots; guarded by
  `tests/test_single_creation_path.py`.
- Building or enabling 1d (or higher) timeframe strategy trading — 2026-09-13
  measurement found the declared edge collapses to no-edge at 1d. FIX-04
  fixes backtest-engine correctness only, not new 1d strategies.
- Flipping `research.enabled: true` — an operator decision pending review of
  the 2026-09-13 power measurement; this roadmap builds the capability, not
  the go-live call (same manual-cutover pattern as strategy-first).
- Setting `BINANCE_DEMO=false` / moving to real-money live trading —
  separate operator decision; FIX-03 only prepares the fee data that
  decision needs.
- Verifying Agents Command Deck / Talk-to-Luffy current ship status — the
  ingest found design/spec-complete records but no independent CLAUDE.md
  confirmation of shipped state; not investigated by this roadmap.

## Context

**The firm** (`org.yaml`, work flows top to bottom): Scraper reads the
outside world and queues ideas (never creates strategies) → Strategist
writes a `StrategySpec` → Strategy Analyst backtests/admits/refuses →
Trader executes → Risk Officer sizes/caps/blocks → Librarian writes the
vault record → Theorist checks live strategies against their admission
envelope (data only, no LLM) → Manager (`kernel.py`/`orchestrator.py`)
coordinates. The LLM Judge was removed 2026-09-11 (56 of 58 reviews applied
nothing).

**Two known unresolved questions sit ahead of this roadmap's open work:**
1. Donchian Breakout Trail (the book's only live strategy) reads no-edge
   (median PF 0.93, p=8.8e-01) on 19 comparable symbols it was never scored
   on, while scoring strongly (p=3.5e-04) on its own declared 16 — and the
   declared set sits in the top 0.8% of 4000 random draws by consistency p.
   In-sample testing cannot separate "genuinely works here" from "was fitted
   to these markets"; only forward trading can (tracked by FVAL-01).
2. `spec_funding_filtered_trend_pullback` was admitted 2026-09-11 with its
   cross-symbol null run on **0 symbols** (untestable, not refused, under
   the rule in force at the time — since closed for new admissions). Left
   trading by operator decision; its forward record is the only way to know
   if its 20-trade admission stats meant anything (tracked by FVAL-02).

**Historical incidents that shaped current invariants:** the 2026-09-11
second-creation-path incident (legacy `Strategist.review()` filled all 8
slots with a resurrected retired genome); the pre-2026-09-02 candle store
serving forming bars as closed bars (fabricated a Donchian breakout); the
journal P&L being wrong until 2026-09-02 (voids live PF conclusions before
that date); pseudo-replicated analyst significance (decisions/outcomes must
be clustered by episode, not counted per-cycle).

**Timing note:** the rent check's first judged week starts 2026-09-14 — the
day after this roadmap was written.

## Constraints

- **Tech stack**: Python, local venv, SQLite (WAL) as the only inter-process
  channel; FastAPI + GraphQL (Strawberry) + WebSocket for the dashboard.
- **Deployment environment**: single Ubuntu VM, 2 cores / ~8GB RAM, no GPU,
  always-on — local LLM inference is off the table by hardware, not choice.
- **Secrets**: `.env`, never committed. Required: `BINANCE_API_KEY`,
  `BINANCE_SECRET_KEY`, `DEEPSEEK_API_KEY`, `TELEGRAM_TOKEN`,
  `TELEGRAM_CHAT_ID`, `BINANCE_DEMO=true`. Optional (degrade to silent
  no-op): `FINNHUB_API_KEY`, `COINALYZE_API_KEY`.
- **Governing premise is architectural**: no edge lasts, so no future work
  may reintroduce a lifetime pass/fail gate anywhere in the admission or
  retirement path.
- **Known-good invariants that must never break**: `scripts/backtest_equivalence.py`
  stays PASS; `scripts/bench_vector_backtest.py` stays above 20x;
  `spec_evidence` reports UNTESTED (never a score) when data is absent or
  short of the frame; backtests read `DataFeed.cached_ohlcv()`, never
  unbounded `fetch_ohlcv`; the orchestrator's `spec:{id}` strategy-eligibility
  registration is load-bearing; `trader/brain/scraper.py` must keep importing
  the queue module as `from . import ideas` (a local variable named `ideas`
  silently broke the queue once, for the file's entire life).
- **LLM spend is per-purpose budgeted**: every `BrainLLM` call passes
  `purpose=`; `brain.purpose_budgets` reserves slices no other consumer can
  eat; exhausted budget goes read-only, never halts mid-position.
- **Single writer**: Kernel is the only writer to `data/luffy.db`; Dashboard
  writes only `state_kv` control flags.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| No lifetime pass/fail admission gates | Under one, all 27 candidates scored zero; edges decay, so selection must ask "is this working now" | ✓ Good |
| Strategy-first cutover (`require_strategy_signal` + `strategy_leads`) is live and kept manually reversible | A blended score buried Donchian's edge under 7 measured anti-edges; a lone strategy at 0.6 confidence scored under every threshold the blend produces | ✓ Good |
| One strategy-creation path only (Scraper → Strategist → Analyst) | The legacy "mutate" review path resurrected a retired genome into all 8 trade slots (2026-09-11 incident) | ✓ Good |
| Admission gate: PF≥1.15/≥20 trades, overlap<0.6, null p<0.01, <4 symbols=REFUSED | Closed the 2026-09-11 hole where silence (0 symbols scored) let a spec through with no null evidence at all | ✓ Good |
| Risk caps: 8 slots, 0.5% risk each | Measured optimum (`deployment_frontier.py`); 12 slots and 0.75%/4-slots both score worse on return AND drawdown | ✓ Good |
| Rent verdict reads only the venue's income ledger, never `trades`; death is a human act | Journal booked +$46.33 vs. the venue's real +$11.83 on the 2026-09-11 incident, before the booking fix | ✓ Good |
| Research pipeline ships disabled (`research.enabled: false`) | Search machine (phases 0-2) is built and measured, but proposes nothing yet; only the 4h horizon is powered (1h/15m underpowered) | — Pending operator review |
| `promotion.py` scoped to legacy genomes only | The Analyst's rolling admission/decay gate now owns specs; all legacy genomes are retired, so `promotion.py` currently governs nothing live | ✓ Good |
| Donchian Breakout Trail's universe-generalization is unresolved | Reads no-edge (p=8.8e-01) on 19 comparable symbols never scored on; in-sample tests can't separate "works" from "was fitted"; forward trading is the only remaining discriminator | ⚠️ Revisit (tracked by FVAL-01) |

---
*Last updated: 2026-09-13 after initial roadmap creation from CLAUDE.md ingest*
