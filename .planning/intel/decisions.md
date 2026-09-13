# Decisions

This repo has no ADR-classified documents. CLAUDE.md (precedence 0) functions
as the locked record of current architecture: its "Known-good invariants" and
"What measurement established (do not re-litigate)" sections are treated as
locked decisions per ingest guidance. Decisions proposed in SPEC documents are
recorded as `status: proposed` unless CLAUDE.md independently confirms them,
in which case they are `status: locked` with both sources cited.

## Governing premise: no lifetime pass/fail gates
- source: CLAUDE.md
- status: locked
- decision: No edge lasts. Strategy selection asks "is this working now", never "did this survive five years"; lifetime pass/fail gates must not be reintroduced — under one, all 27 candidates scored zero.
- scope: strategy selection, admission gates

## Strategy-first core — the cutover is live
- source: CLAUDE.md (corrected 2026-09-13 against `trader/engine/orchestrator.py` and `config.yaml`); docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md (precedence 2, DECIDED Sarmad)
- status: locked
- decision: Strategies own entry and exit, analysts supply coins/direction. The cutover the 2026-09-01 spec described as manual has been made: `require_strategy_signal: true` refuses any direction no strategy proposed, and `strategy_leads: true` means that once a strategy signals, `strategy_score()` sets the score rather than the 7-analyst blend. `Orchestrator.decide()` still computes the blend and journals every vote so analyst skill stays measurable; both flags remain operator-reversible config, never flipped automatically.
- scope: orchestrator decision core, strategy-first cutover

## One strategy-creation path only
- source: CLAUDE.md
- status: locked
- decision: There is exactly one strategy-creation path — Scraper queues, Strategist writes a spec, Analyst admits. The legacy Scraper→genome path and the legacy Strategist "mutate" review path were both deleted (2026-09-11) after the latter filled all 8 trade slots by resurrecting a retired genome. `tests/test_single_creation_path.py` guards this; do not reintroduce a second creation path.
- scope: strategy creation pipeline

## Machine-found candidates go straight to admission
- source: docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md (precedence 1, DECIDED Sarmad)
- status: proposed (build order phases 0-2 underway per CLAUDE.md daemon thread table; `research.enabled: false` — not yet live)
- decision: Machine-found combinations go straight to `analyst.admit()` inside `_mechanism_once`, carrying their exact rule and evidence; the Strategist's LLM is not asked to re-type a rule it was handed. Invariant: "one admission gate, many proposers" — nothing writes the `strategies` table except `_mechanism_once` after `admit()` returns ok.
- scope: research pipeline, admission gate

## Demo venue routing
- source: CLAUDE.md
- status: locked
- decision: `BINANCE_DEMO=true` routes every order to Binance Demo Trading. Trades are journalled `exec_mode="live"` (meaning real orders were sent, to a demo venue).
- scope: environment, deployment mode

## Risk caps: 8 open trades, 0.5% risk each
- source: CLAUDE.md
- status: locked
- decision: Risk Officer caps at max 8 open trades, 0.5% risk each. Measured: 0.5% risk with 8 slots beats 0.75% with 4 slots on both return and drawdown — a trend book earns its Sharpe from breadth, not size per trade. Relaxing the cap above 8 (tested to 12) makes the outcome worse.
- scope: risk management, position sizing

## Missing information is NaN, never a fabricated default
- source: CLAUDE.md
- status: locked
- decision: A feature must return NaN when information is missing, never a fabricated default such as 0.0 — a fabricated value reads as a real measurement and fires trades on data that does not exist. This has been violated and fixed several times.
- scope: feature registry, DSL

## Point-in-time evaluation is mandatory
- source: CLAUDE.md
- status: locked
- decision: A bar may only use information that had closed by its own close. `dsl._eval_htf`'s `searchsorted(..., side="right") - 1` is the reference rule; the same rule governs the newest (possibly-forming) bar. `core.types.closed_bars` is the single definition, used by both the candle store and the live evaluator.
- scope: DSL, backtest engine, live evaluator

## `domain=` and `arg_specs=` are load-bearing
- source: CLAUDE.md
- status: locked
- decision: A feature's `domain=` and `arg_specs=` drive automatic parameter-range inference for the optimizer. A domain that real data exceeds silently mis-tunes every spec using that feature.
- scope: feature registry

## `ref(key, expr)` point-in-time alignment
- source: CLAUDE.md; docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md (precedence 1)
- status: locked
- decision: `ref(key, expr)` evaluates any OHLCV indicator on a reference market and gives each base bar the value known at its close (reference stamp + `close_after_ms` against base stamp + bar length, `searchsorted(side="right") - 1`); past `max_stale_ms` it is NaN, not a fabricated value. Only OHLCV features may appear inside `ref()`.
- scope: DSL, reference markets

## Admission gate (current form)
- source: CLAUDE.md; docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md (precedence 1, "the admission-hole fix", DECIDED Sarmad)
- status: locked
- decision: Admission requires pooled PF ≥ 1.15 over ≥ 20 trades, signal overlap < 0.6 against the book, AND cross-symbol rotation-null consistency p < 0.01 (`select_null_max_p`). Fewer than 4 symbols carrying a percentile is untestable and REFUSED (since 2026-09-11) — this overturns the earlier rule "silence does not block admission." The fix governs new admissions only; one spec admitted under the old rule keeps trading by Sarmad's decision.
- scope: strategy admission, Analyst role

## Retirement: decay, not lifetime, and idle is not decayed
- source: CLAUDE.md
- status: locked
- decision: Decay retirement fires on 30-day PF < 0.85 over ≥ 10 trades. Too few recent trades is idle, not decayed; retirement never widens the evidence window.
- scope: strategy lifecycle

## `promotion.py` is scoped to legacy genomes only
- source: CLAUDE.md; docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md (precedence 2, DECIDED "D8")
- status: locked
- decision: The Analyst owns specs (rolling admission/decay gate); `promotion.py` applies lifetime PF only to legacy genomes (`kind='spec'` rows excluded from `promotion.evaluate_population`). All legacy genomes are now retired, so `promotion.py` currently governs nothing live.
- scope: strategy lifecycle governance

## Only the stop is armed on the exchange
- source: CLAUDE.md
- status: locked
- decision: Only the protective stop is armed natively on the exchange. The target and trailing stop are worked in-process by `ExitEngine`; if the kernel dies, only the stop protects the position.
- scope: exits, exchange integration

## Protective stops are conditional/algo orders
- source: CLAUDE.md
- status: locked
- decision: Binance USDM books a reduceOnly `STOP_MARKET` as a conditional/algo order (returns `algoId`, lives behind `/fapi/v1/algo/*`), not a regular order. Every place/cancel/enumeration of a stop must go through `trader/engine/protective.py`; never call ccxt directly for a stop.
- scope: exchange integration, protective stops

## `Journal.query()` does not commit
- source: CLAUDE.md
- status: locked
- decision: `Journal.query()` runs on a thread-local connection with no transaction wrapper; an INSERT/UPDATE through it stays uncommitted until a later `_tx()` on the same thread happens to commit it. Writes must use `_tx()`.
- scope: journal/database

## LLM spend is per-purpose
- source: CLAUDE.md; docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md (precedence 1, Part 6)
- status: locked
- decision: Every `BrainLLM` call passes `purpose=`; `brain.purpose_budgets` reserves slices (research 120k, chat 30k) that no other consumer can eat, and the rest share the remainder of `daily_token_budget`. `data/brain_usage.json` keeps the per-purpose tally.
- scope: LLM budget management

## Rent rule: $50/week off the venue ledger, death is a human act
- source: docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md (precedence 1, DECIDED Sarmad); confirmed by CLAUDE.md (`rent-check` daemon thread, "Reports only — never stops Luffy")
- status: locked
- decision: The unit judged is Luffy as a whole, not each strategy. The bar is $50 net per week, judged strictly week by week (a good week does not carry a bad one). Verdict comes from the venue's income ledger, never from the `trades` table (journal booked +$46.33 vs the venue's +$11.83 on 2026-09-11 before the fix). An unreadable ledger reads UNKNOWN, never PASS. Death is a human act — Luffy reports the verdict and never shuts itself down or deletes anything.
- scope: rent check, kill/keep decision authority

## Research pipeline ships disabled
- source: CLAUDE.md; docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md (precedence 1)
- status: locked
- decision: `research.enabled: false` in `config.yaml` — the search machine exists (phases 0-2 measured against the real store as of 2026-09-13) but proposes nothing yet; it is not turned on pending the operator's review of the 2026-09-13 measurement (4h horizon POWERED at p=2.4e-03, 1h/15m UNDERPOWERED, `research.horizons: ["4h"]` only).
- scope: research pipeline activation

## Strategy representation: StrategySpec/DSL replaces Genome
- source: docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md (precedence 3); confirmed implemented by CLAUDE.md ("74 features", "restricted expression DSL", `scripts/backtest_equivalence.py`)
- status: locked (implemented)
- decision: A strategy is one self-contained `StrategySpec` (thesis, invalidation, universe, DSL entry/filter expressions, `ExitSpec`) compiled from a safe AST-whitelisted expression language, replacing `Genome`/`FAMILY_GENE_SPECS`/`library.EVALUATORS`. The equivalence harness (`scripts/backtest_equivalence.py`) proving trade-for-trade parity with the old engine was mandatory before cutover, not optional.
- scope: strategy representation, DSL, backtester

## Talk-to-Luffy stays read-only
- source: docs/superpowers/specs/2026-08-29-talk-to-luffy-analyst-design.md (precedence 3); docs/superpowers/plans/2026-08-29-talk-to-luffy-analyst.md (precedence 5, self-review: complete)
- status: proposed (implementation plan self-reviewed complete; no independent CLAUDE.md confirmation found)
- decision: The voice/tool-calling analyst is strictly read-only — no LLM-driven ops (freeze/halt/close/panic stay on the deterministic `detect_ops()` layer), no agency/action-taking. DeepSeek via `BrainLLM.chat_tools()`, provider kept swappable.
- scope: chat/voice analyst

## Agents Command Deck (locked per source spec)
- source: docs/superpowers/specs/2026-08-30-agents-command-deck-design.md (precedence 3, self-labeled "Decisions (locked)")
- status: proposed (spec's own internal lock; no independent CLAUDE.md confirmation of ship status found)
- decision: The command-deck cockpit replaces the OS→Agents tab's analyst stats table. Data fidelity: real backend data only, no frontend-faked fields. Old `#agents-tbl` view removed. No new `/api/deck` endpoint — enrich `/api/org` in place.
- scope: dashboard Agents tab

## Researcher agent contract (not yet built)
- source: docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md (precedence 2, DECIDED Sarmad + Claude)
- status: proposed (CLAUDE.md confirms as of 2026-09-13: "The Researcher agent still does not exist")
- decision: A new Researcher role sits above the Strategist, moves beliefs from unexamined to survived-attack via pre-registered Theses (mechanism, prediction, kill_condition) tested against a mechanical confound battery, never writes to the live population, and hands the Strategist validated Mechanisms. External research sets the agenda; internal data sets the truth.
- scope: Researcher agent, thesis pipeline
