# Luffy — repository guidance

Read this guide once. Open only task-relevant source and linked documents;
do not preload the historical reference. Current user instructions take
precedence. Preserve unrelated workspace changes and frozen research artifacts.
Keep session progress in reports/handoffs, not in this file.

## Purpose and boundaries

Luffy's end goal is a complete autonomous trader: understand markets, allocate
attention, test competing hypotheses, choose the best expression (long, short,
pairs, hedge or cash), execute under deterministic risk control, learn from
outcomes including non-trades, and eventually cover its own operating costs
without forcing trades. Authoritative requirements:
[owner end goal](docs/superpowers/specs/2026-09-16-luffy-end-goal.md).

Crypto-futures strategy discovery/evaluation on the existing **demo** setup is
the current *implementation scope*, not Luffy's identity. Evaluate whether an
edge works recently; do not replace rolling spec admission/decay with lifetime
performance gates. Legacy genome promotion is a separate path. Keep the demo
scope unless the user explicitly changes it. `BINANCE_DEMO=true` routes orders
to demo; journal `exec_mode="live"` means orders were sent, not that real money
was used. Read configuration before operational actions. Never print or commit
`.env` credentials.

The owner goal above is the governing objective; sequence work by value and
dependency rather than a fixed priority order. Neither the legacy research
process nor Gate 2 is the goal — Gate 2 is a subordinate research task, and
design may change in service of the goal. What does not change: deterministic
risk control, honest evidence, frozen research artifacts, and the recorded
operational stop. A well-formed thesis is untested, not admitted. Keep
`research.referee=false` and `research.handoff=false`; the recorded
dependence-corrected gate stop must not be bypassed or weakened to obtain
trades.

Keep the owner able to see what Luffy is doing and why, and prefer structured,
end-to-end diagnostics — correlation IDs and reason codes that trace event →
evidence → hypothesis → decision → order/fill → outcome — over ad-hoc prints.
Record observable evidence and concise rationale; never log secrets.

## Commands

Use `./venv/bin/python`; run tests relevant to the change.

```bash
./venv/bin/python -m pytest tests/<relevant_test>.py
./venv/bin/python -m pytest tests/                  # full suite when warranted
./venv/bin/python -m trader.kernel --status        # status, no boot
./venv/bin/python -m trader.research --status
rg -a 'PATTERN' logs/luffy.log                     # logs may contain NULs
```

Operational commands, only within the authorized task:

```bash
./venv/bin/python -m trader.kernel
./venv/bin/python -m trader.dashboard.server       # port 8080
./restart.sh kernel                              # or: ./restart.sh dashboard
./venv/bin/python -m trader.kernel --panic         # flatten and freeze
```

`scripts/watchdog.sh` restarts stopped/stale processes. Before an intentional
stop, create `data/watchdog.off`; account for that flag when restoring service.
`ACTIVE` permits entries; `FROZEN` manages exits only; `HALTED` manages neither.
Exchange protective stops remain independent of these states.

## Architecture and source map

Paths below are relative to `trader/` unless otherwise stated.

| Concern | Source / contract |
|---|---|
| Trading loop | `kernel.py`; `engine/orchestrator.py`, `risk.py`, `executor.py`, `exits.py` |
| Strategy creation | `brain/scraper.py` / `crawler.py` queue → `spec_writer.py` writes → `analyst.py` admits |
| Strategy evidence | `strategy/spec_evidence.py`, `portfolio_evidence.py`, `vector_backtest.py` |
| DSL and features | `strategy/dsl.py`, `features.py`, `features_deriv.py`, `features_xs.py` |
| Research | `research/`: discovery, ledger, referee, thesis contract |
| Market data | `data/feed.py`, `derivatives.py`, `references.py`, `ref_sources.py` |
| Journal / dashboard | `core/journal.py`; `dashboard/server.py` |
| Configuration | root `config.yaml`, loaded by `core/config.py`; roster in root `org.yaml` |

- Keep one strategy-creation/admission path. Scrapers queue ideas; legacy
  mutation must not insert unvalidated tradable strategies. Guard:
  `tests/test_single_creation_path.py`.
- With `scouts.strategy_leads`, emitted strategy signals set the score;
  analysts remain evaluated/journalled. `require_strategy_signal` requires a
  strategy-proposed direction. Both are enabled in the inspected config;
  do not describe the old blended-score behavior as unconditional.
- Specs register evaluators as `spec:{id}` and must pass per-strategy symbol
  eligibility. A nonempty declared include list defines the universe.
- Kernel and dashboard share SQLite WAL. Dashboard controls use `state_kv`,
  not `kv`. `Journal.query()` does **not** commit writes; use `_tx()`.
- Idea consumption is per stream (`strategy`, `research`); preserve
  `brain/ideas.py:MIN_TEXT`. Do not shadow the scraper's imported `ideas`.
- Pass `purpose=` to `BrainLLM`; preserve per-purpose budget isolation.
  `data/doctrine.json` is frozen; do not rewrite it.

## Trading and evidence invariants

- Admission must have actual testable evidence on the spec's own universe.
  Missing coverage/null evidence is `UNTESTED` or refusal, never a pass.
  Read current thresholds from `config.yaml` and `brain/analyst.py` instead
  of copying historical numbers. Too few recent trades means idle, not decay.
- Position limits and risk come from config, not old examples. Verify
  portfolio behavior with concurrency, correlation, costs and compounding;
  improved PF alone does not justify a filter or risk increase.
- ATR geometry must use the spec's declared timeframe in sizing, stops and
  trails. Backtest close fills do not establish achievable live execution;
  account for signal age and delay when evaluating execution.
- Use `engine/protective.py` for stop placement, cancellation and enumeration:
  Binance protective stops use conditional/algo IDs, not ordinary order IDs.
  Never remove a venue stop merely because its journal ID is missing.
- Targets/trails run in-process; the exchange stop is the protection when
  the kernel is down. Reconciliation must use actual venue positions/size.
- Book actual fills, commissions and venue P&L where available; label
  estimates explicitly. Journal records alone do not establish venue state.

## Data and feature invariants

- Missing data is **NaN**, not fabricated zero. Use fully closed bars via
  `core.types.closed_bars`; preserve incremental tail refresh in the store.
- Features must be point-in-time. `htf()` / `ref()` align by the time a source
  bar becomes available, not merely its timestamp. Stale references are NaN.
  Preserve feature `domain`, `arg_specs` and `data_requires` contracts.
- Cross-sectional features require `FeatureCtx.universe` and `symbol`;
  pass BTC, derivative and reference contexts where required.
- Backtests use `DataFeed.cached_ohlcv()`. Preserve `_trade()` as the shared
  trade definition and `walk_table`/`simulate` equivalence. `WARMUP=210` is
  a bar count; `warm_window` supplies prior history and `score_from` limits
  scoring to the requested window. Do not use legacy `backtest.resample()` for open-labelled
  higher-timeframe bars without correcting/verifying alignment.
- Funding is signed and actual/null runs must use the same cost model.
  `ls_ratio` (positions) and `ls_account_ratio` (accounts) are different
  observables. Coinalyze units/venue mappings need verification; `deepen()`
  deliberately does not replace Binance funding history.
- `data/luffy.db`: journal and research ledger. `data/candles.db`: production
  OHLCV plus separate `refs` table. `data/derivs.db`: derivatives. Normalize
  symbols consistently. Demo volume and production volume are not equivalent.
- Current reference membership does not prove historical point-in-time
  membership. Coverage and provider retention must be measured when needed.

## Research safeguards

- Discovery uses one shared calendar cut per horizon; per-symbol cuts leak
  eras. Learn thresholds only on discovery, with separately measured mirrors.
- Discovery ranks candidates; it does not grant admission. Preserve ablation
  requirements and window power controls. Underpowered is not evidence of
  no edge. Config currently restricts research to `4h`.
- Market-wide signals repeat across correlated symbols. Preserve gate 1's
  dependence correction and common-rotation check; never substitute the raw
  pooled consistency p-value. Repeated analyst decisions need episode-level
  clustering before evaluation.
- Freeze the thesis and protocol before evaluation. Proposers see discovery
  evidence only; do not expose held-out numbers or repurpose inspected history
  as untouched data. Separate verified numeric claims from unverified prose.
- Every held-out look spends the registered error budget. No retry, missing
  data, renamed slice or persuasive explanation may silently waive a gate.
  `reason_passed` requires actual registered gate evidence.
- For backtest-engine changes, preserve
  `scripts/backtest_equivalence.py` PASS and the documented >20x benchmark in
  `scripts/bench_vector_backtest.py`. Do not run real-data evaluation as an
  incidental check during discovery-only thesis work.

## Read on demand

- Project delivery: [roadmap](docs/ROADMAP.md),
  [canonical checklist](docs/superpowers/plans/luffy-delivery-checklist.md), and
  [next-session entry point](docs/NEXT_SESSION.md). Use these for the active
  work queue; keep evidence and progress in their linked reports.
- Earlier owner-goal context:
  [end-goal handoff](docs/superpowers/reports/2026-09-16-end-goal-handoff.md).
- Earlier demo-first design (historical sequencing; owner end-goal governs
  priorities):
  [demo-first spec](docs/superpowers/specs/2026-09-15-demo-first-intelligence.md).
- Discovery dossier contract:
  [candidate report](docs/superpowers/reports/2026-09-15-candidate-dossier.md).
- Referee protocol/recorded stop:
  [phase-3 plan](docs/superpowers/plans/2026-09-14-research-phase3-referee.md).
  Contains evaluation results; do not load into discovery-only proposal work.
- Current task status: use its explicit handoff and relevant report under
  `docs/superpowers/reports/`; do not infer completion from a design document.
- Historical investigations and full pre-condensation guide:
  [archived guide](docs/reference/claude-guide-history-2026-09-16.md).
  **Historical, not current authority. Contains held-out results.** Open only
  relevant sections when authorized; never preload into thesis generation.

## Working roles and token efficiency

Claude handles implementation, edits, tests, simulations, routine
investigation and report generation, and other labour. The coordinating
agent handles bounded planning, statistical judgment, independent review and
verification. Check Claude availability through the local Claude CLI even if
the built-in agent roster lacks a Claude entry. If Claude cannot execute a
task, report the limitation; do not silently transfer the labour to another
model.

For efficiency: give bounded briefs with exact relevant paths, avoid broad
history/context preloads, avoid duplicate implementations or reviews of the
same work, reuse existing artifacts and passing checks, keep results concise,
and match effort/budget to the size of the task. This does not impose new
permission steps and makes no pricing claims.
