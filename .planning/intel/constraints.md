# Constraints

Extracted from SPEC-classified documents.

## StrategySpec / ExitSpec schema
- source: docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md
- type: schema
- content: `StrategySpec` fields — `id, name, thesis (>=80 chars), invalidation (>=30 chars), provenance{source_url,source_kind,author,parent_id,harvested_at}, universe{include,exclude,min_volume_usdt}, timeframe, direction(long|short|both), entry_long, entry_short, filters[], exit: ExitSpec, regime_filter[], markets[], generation, parent_id, data_requires[] (compiler-derived, never LLM-declared)`. `ExitSpec` fields — `stop{kind:atr|pct|swing,...}, target{kind:atr|rr|none,...}, trail{kind:none|atr,mult,arm_at_r}, time{max_bars}, signal_exit`. Storage: nullable `spec_json TEXT` column on `strategies`, `kind="spec"` rows route to the compiler.

## DSL safety whitelist
- source: docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md
- type: protocol
- content: Expressions parsed with `ast.parse(expr, mode="eval")`. Allowed AST nodes only: `Expression, BoolOp(And|Or), UnaryOp(Not|USub), BinOp(Add|Sub|Mult|Div), Compare(Lt|LtE|Gt|GtE|Eq|NotEq), Call (callee must be a registered feature name), Name (0-arity feature only), Constant (int/float/str)`. No attribute access, subscripts, lambdas, comprehensions, imports, or assignment. Anything else raises `SpecError`.

## Feature registry schema
- source: docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md
- type: schema
- content: `Feature{name, fn: Callable(ctx,*args)->pd.Series, arg_specs: list[tuple] (drives literal tuning), domain: tuple|None (drives threshold tuning), requires: list[str] (["ohlcv"]|["funding"]|...)}`. Numeric literals in a spec are collected as tunable parameters: an argument position uses the feature's `arg_specs`; a comparison threshold uses the other side's declared `domain`.

## Equivalence and speed invariants
- source: docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md
- type: nfr
- content: The equivalence harness (old vs new backtester over identical frames, 8 seed specs) is mandatory before any cutover — "without it the cutover is faith-based." Speed target: the 5-symbol x 8000-bar gauntlet must clear >=50x versus the old per-bar evaluator (later invariant in CLAUDE.md: `scripts/bench_vector_backtest.py` must stay above 20x).

## Derivatives point-in-time alignment
- source: docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md
- type: nfr
- content: Every derivative series is reindexed onto the bar index using the same `searchsorted` discipline as `backtest.ctx_at()`, forward-filling only observations already settled at that bar. Funding especially: an 8-hourly rate is knowable at settlement, not before, so it is shifted. A lookahead test (shifting a series must change backtest results) is required.

## `ref()` reference-market rules
- source: docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md
- type: protocol
- content: `ref(key, expr)` is a DSL marker special-cased like `htf(tf, expr)`; the inner expression is evaluated on the reference instrument's frame and aligned via `searchsorted(close_times, bar_close, side="right") - 1`. A bar exists only once it has closed in the source's own clock (e.g. Yahoo stamps S&P daily bars at the 13:30 UTC open, final at 20:00 UTC). Missing is NaN — a reference silent longer than its declared `max_stale` (default 3 days daily, 6 hours hourly; per-market calendar overrides measured and applied in the phase-1 implementation) reads NaN, never a stale-but-plausible value. Close-only series (e.g. `stables`, `alts`) return NaN for indicators needing high/low.

## Research pipeline: search scope and growth rules
- source: docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md
- type: nfr
- content: A combination is up to 6 parts (1 trigger + 5 context), both directions mirrored, evaluated under two fixed exit geometries only (exits are not searched). Horizons 15m/1h/4h (1d closed by measurement, scalping out of scope). Thresholds are always measured percentiles (10th/25th/75th/90th) of the discovery slice, never hard-coded constants. A part joins a combination only if it improves BOTH the rotation null (`consistency_p`) and compounded return on the discovery slice, and the combination must stay testable (>=8 projected test-slice trades on >=4 held-out markets) or it stops growing.

## Research pipeline: error budget (LORD++)
- source: docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md
- type: nfr
- content: Online false-discovery control via LORD++, target FDR 10%, initial wealth 0.05. Every held-out look (gate 1) and prediction test (gate 2) spends from the budget; each discovery earns wealth back. A supervisory brake halves the per-test level once >=5 admitted strategies have completed forward windows and more than 10% failed it, until the realised rate falls back under target. The ledger persists the sequence across restarts.

## Research pipeline: admission gate 4 (untestable = refused)
- source: docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md
- type: nfr
- content: Gate 4 (the Analyst) requires pooled PF >= 1.15 over >= 20 trades in the 90-day doubling window; signal overlap with the book < 0.6; for machine candidates, a ledger record showing gates 1-3 passed; and — the admission-hole fix — when fewer than 4 markets carry a null percentile, `consistency_p` is None and the verdict is REFUSED — untestable. This overturns the earlier rule that silence does not block admission.

## Rent ledger invariants
- source: docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md
- type: nfr
- content: The rent verdict is read only from the venue's income ledger (`fapiPrivateGetIncome`), never from `trades`. Net counts only `REALIZED_PNL`, `COMMISSION`, `FUNDING_FEE`; `TRANSFER` and any deposit/withdrawal never count; any other type is surfaced under `uncounted`, never silently added or dropped. Verdict: ledger read and net >= bar => PASS; ledger read and net < bar (including a week with zero rows) => FAIL; ledger could not be read => UNKNOWN, never PASS. One verdict per week, restart-safe/idempotent. `week_bounds` = Monday 00:00 UTC to the next Monday 00:00 UTC. Config: `rent: {weekly_usdt: 50, first_week_start: "2026-09-14"}`.

## Executor P&L booking (fills, not hints)
- source: docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md
- type: nfr
- content: `close()` must book the exit price as the volume-weighted price of the order's own fills (`fetch_my_trades`, filtered to the order id), and the trade's `realized_pnl` from `reconcile.venue_realized_pnl` (venue realized P&L minus commission over every fill since open). `close_partial()` books the partial's own fills the same way. If fills cannot be confirmed after 3 retries (1s backoff), book the current estimate, log a WARNING, and write a `pnl_estimated` brain event — an estimate must never be indistinguishable from a confirmed fill.

## Talk-to-Luffy tool contract
- source: docs/superpowers/specs/2026-08-29-talk-to-luffy-analyst-design.md
- type: api-contract
- content: The analyst agent loop is capped at ~5 steps, budget-guarded via `BrainLLM`. Tools are a fixed whitelist of typed Python functions — never arbitrary SQL. Every figure the analyst states must come from a tool result; the system prompt forbids inventing numbers. Target toolbox (~9 read-only tools): `get_positions, get_trades, get_pnl, get_equity_curve, get_decisions, get_strategy_performance, get_agent_stats, search_knowledge, get_price`. `detect_ops()` (freeze/halt/panic/resume) runs deterministically before the agent loop and the LLM never triggers ops.

## Agents Command Deck: category vs node-color separation
- source: docs/superpowers/specs/2026-08-30-agents-command-deck-design.md
- type: schema
- content: `category` (six values: BRAIN, ANALYST, RESEARCH, RISK, EXECUTION, KNOWLEDGE) drives filter chips and lives in `org.yaml`, read by `trader/org.py`. Node color is a separate per-role map kept on the frontend only, preserving distinct hues per role regardless of category grouping. Category is metadata-only and must never touch decision logic. `build_company()` per-employee fields: `category (str), stats ([[label,value]]x3), bar (int 0-100), signal ([int]~24 hourly histogram), core ([[label,value]]x4, Manager only)`.

## Risk Officer sizing shape (spec-era; superseded numeric caps — see INGEST-CONFLICTS.md)
- source: docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md
- type: nfr
- content: The strategy owns the exit *shape* (its `ExitSpec`: e.g. stop 2.5 ATR, target 3R, max 64 bars); the Risk Officer owns the arithmetic that converts that shape into real price levels and size, under caps. The design's own numeric caps (initial margin <= 15% of equity, max 4 open) are superseded by CLAUDE.md's current 8-slot/0.5%-risk caps — see conflicts report.
