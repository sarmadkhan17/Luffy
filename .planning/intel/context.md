# Context

Topic-keyed notes from DOC-classified sources (implementation plans, handoffs,
and CLAUDE.md's own narrative sections). CLAUDE.md is precedence 0 and is the
current statement of fact; other DOC plans (precedence 4-7) are largely
historical implementation records for work that is now described, in its
current/settled form, by CLAUDE.md.

## Open work: Researcher agent not yet built
- source: CLAUDE.md ("Open faults"); docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md
- note: The Researcher agent still does not exist. `research`-stream ideas queued via `brain/ideas.py` sit unconsumed. The spec's full agent contract (Thesis pre-registration, six thesis sources, mechanical confound battery, TRADE/ADAPT/RESEARCH/WAIT/ACQUIRE outputs) is designed but not implemented.

## Open work: research pipeline phases 3-5 not built
- source: CLAUDE.md; docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md; docs/superpowers/plans/2026-09-12-research-phase2-search.md
- note: Phases 0 (groundwork), 1 (reference markets/`ref()`), and 2 (the search machine) are built and measured against the real store as of 2026-09-13. `research.enabled: false` — the search proposes nothing yet. Phase 3 (referee: gate 1, LORD++ ledger/brake, gate 3, handoff into `_mechanism_once`), phase 4 (Reason: LLM thesis, gate 2, UNEXPLAINED), and phase 5 (weekly reporting, then removing the Strategist's LLM path) remain to be built. Measured 2026-09-13: 4h horizon is POWERED (plain-window control p=2.4e-03 on the declared 16), 1h and 15m are UNDERPOWERED and refused (`research.horizons: ["4h"]` only).

## Open work: legacy second-creation-path incident (2026-09-11)
- source: CLAUDE.md
- note: The legacy `Strategist.review()` path resurrected a retired genome (`strat_606048ec95_m237`, retired 09-02 for live PF 0.168) straight into `paper` with no backtest/no Analyst, filling all 8 trade slots with 7 correlated shorts plus a BTC short. Fixed same day: the kernel no longer runs the legacy review; `strategist.py` is keep/retire only over live legacy genomes, never a spec; `strategy/proposer.py` deleted (no caller). The journal booked the incident's exit P&L wrong (+$46.33 booked vs +$11.83 venue-actual) because `executor.close()` booked `order["average"] or order["price"] or price_hint` rather than the fill — fixed 2026-09-11 as part of the rent-check work (see `docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md`). Rows closed before the fix are still flattered.

## Open work: Donchian's universe generalization is unresolved
- source: CLAUDE.md
- note: The book's only live strategy (Donchian Breakout Trail) reads median PF 1.42 / consistency p=3.5e-04 on its declared 16 symbols, but median PF 0.93 / p=8.8e-01 (no-edge) on 19 comparable perps it was never scored on, and the declared set sits in the top 0.8% of 4000 random 15-symbol draws by consistency p. Two measurements (stable across train/test halves; the extreme percentile) are consistent with BOTH "genuinely works on these markets" and "was fitted to them" — in-sample testing cannot separate the two. The only remaining discriminator is forward performance, and as of the CLAUDE.md snapshot the strategy had taken zero live trades under correct geometry (kernel uptime gaps consumed the window; see "Nothing has yet traded under correct geometry" in CLAUDE.md).

## Open work: failing test tied to wall-clock drift
- source: CLAUDE.md
- note: `test_macro_guard::test_a_restart_reuses_the_cached_calendar` fails on the wall clock since the week of 2026-09-04 passed (cache freshness reads real time while the test pins `_now`). Still open as of the CLAUDE.md snapshot.

## MacroGuard implementation (complete per its own plan; calendar source superseded)
- source: docs/superpowers/plans/2026-08-29-macro-guard-agent.md (precedence 5); CLAUDE.md
- note: The plan built `trader/agents/macro_guard.py` (check() → cached-state pattern like NewsGuard), hard-freezes via `ControlStateMachine`, auto-resumes via a `macro_guard_froze` KV flag, reactivated the RSI Exhaustion Reclaim strategy (demoted 2026-08-28) and annotated 5 affected trades. All tasks self-reported complete (pre-window 30min/post-window 2h configurable, fail-open on network errors, config wired, env key documented). The plan's primary calendar source was Finnhub; CLAUDE.md's current statement is that the default calendar is the free, keyless ForexFactory feed, with Finnhub tried first only if a key is present (the free Finnhub tier cannot read the required endpoint, HTTP 403) — see INGEST-CONFLICTS.md.

## Talk-to-Luffy voice analyst implementation (self-reviewed complete)
- source: docs/superpowers/plans/2026-08-29-talk-to-luffy-analyst.md (precedence 5); docs/superpowers/specs/2026-08-29-talk-to-luffy-analyst-design.md (precedence 3)
- note: Implementation plan's self-review reports 7 of 9 toolbox tools shipped (`search_knowledge` and `get_price` deliberately deferred as low-risk, addable later without breaking the registry-parity test). Read-only agent loop, Motion-based breathing orb (idle/listening/thinking/speaking states), Web Speech STT/TTS, no live API in CI. No independent CLAUDE.md confirmation of current ship status was found in the read sources.

## Agents Command Deck implementation (design only; no build-completion record found)
- source: docs/superpowers/specs/2026-08-30-agents-command-deck-design.md (precedence 3)
- note: Wires the finished command-deck cockpit mock (`design/agents-cockpit-v2/command-deck.html`) into the dashboard's OS→Agents tab via an enriched `/api/org` endpoint (no new `/api/deck` endpoint). Dashboard-only change (read-only FastAPI, no kernel/trade-loop/order-path touch). No corresponding DOC-type implementation plan was found in the classified set, and CLAUDE.md does not describe the shipped Agents tab in detail, so ship status is unconfirmed by this ingest.

## Strategy Foundation implementation (StrategySpec/DSL/compiler/vector backtester)
- source: docs/superpowers/plans/2026-08-31-strategy-foundation.md (precedence 5); docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md (precedence 3)
- note: Phase-1 implementation plan for `StrategySpec`, the DSL, compiler and fast vectorized backtester, plus starting the derivatives recorder. Explicitly scoped to representation only — "nothing routes through the spec path yet" at the end of this plan; a later plan (not present in this classified set) covers the Researcher prompt change, `Strategist.write_spec()`, `brain/analyst.py`, and the cutover. CLAUDE.md's current invariants (73 registered features, `scripts/backtest_equivalence.py`, `scripts/bench_vector_backtest.py`) confirm this representation is now the live one.

## Feature layer implementation (vocabulary widened to ~68-73 features)
- source: docs/superpowers/plans/2026-09-01-feature-layer.md (precedence 5); docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md (precedence 2)
- note: Adds Group A (~12 drop-in features: `efficiency_ratio`, `bars_since`, `volume_z`, `rel_volume`, wick/body fractions, `streak`, `dd_from_high`, swing levels, `macd`, `keltner_*`, `atr_pct_rank`, `funding_pct`, `basis_slope`) and Group B (cross-sectional: widens `FeatureCtx` to carry the universe, adds `xs_rank`/`breadth`/`dispersion`). Explicitly excludes Group C (order-book depth, liquidations, spot/perp split — Harvester work) and populating `FeatureCtx.market` (stays `None` until market-structure series exist — later filled by the `ref()` work). Motivated by the measured strongest single predictor at the time, `rel_strength_btc(24)`, IC -0.15 to -0.17 in every regime. CLAUDE.md confirms the registry now stands at 73 features.

## Scraper refactor (two labelled idea streams, Job B deleted)
- source: docs/superpowers/plans/2026-09-01-scraper-outside-world.md (precedence 5); docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md (precedence 2)
- note: Retargets the Scraper at `tradingview.com/scripts/` (public Pine library, not `/ideas/`), stores up to 4,000 characters of description (previously truncated to 600), and queues on two labelled streams (`strategy` for the Strategist, `research` for the not-yet-built Researcher). Deletes Job B (`extract_batch` → `_dual_gauntlet` → `_deploy`), the second strategy-creation path that wrote legacy genomes directly into the population. `MIN_TEXT = 80` quality floor preserved. CLAUDE.md confirms the current scraper description (16 RSS feeds, 12 crawl seeds, "It does not create strategies") and the per-stream consumption model (`brain/ideas.py`).

## Rent-check implementation (built; matches design)
- source: docs/superpowers/plans/2026-09-11-luffy-pays-rent.md (precedence 4); docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md (precedence 1)
- note: `trader/engine/rent.py` (pure functions over the venue income ledger), `Kernel._rent_loop` daemon thread `rent-check`, `state_kv["rent_state"]`/`rent_tally_day`, `/rent` Telegram command, executor fill-booking fix. CLAUDE.md's daemon-thread table and `state_kv` key list both confirm this is live (`rent-check` every 1h; `rent_state`, `rent_tally_day` present).

## Research pipeline phase 0/1/2 implementation (built; matches CLAUDE.md's current daemon/config state)
- source: docs/superpowers/plans/2026-09-11-research-phase0-groundwork.md; docs/superpowers/plans/2026-09-11-research-phase1-pieces.md; docs/superpowers/plans/2026-09-12-research-phase2-search.md (precedence 4); docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md (precedence 1)
- note: Phase 0 replaced the LLM Theorist with a data-only `brain/postmortem.py` + read-only `brain/doctrine.py`, removed the LLM Judge (`/judge` now reads the post-mortem/monitor verdict), added `purpose`-tagged `BrainLLM` budgets, and a `core/child.py` spawn-based runner. Phase 1 added the reference store (`data/candles.db` → `refs` table, NOT the `candles` table — `core.types.norm_symbol` splits on `:` which would collapse every `ref:<key>` to one key `ref`) and the `ref()` DSL operator, with several measured deviations from the spec text (per-market `max_stale`, CoinGecko recorded forward-only, daily bars known 24h after their stamp, `alts` close-only). Phase 2 built the search machine (vocabulary, slices, thresholds, combo/evaluate/growth/control/ledger/planner/job/runner) and measured it against the real store 2026-09-13 (19/19 discovery symbols on all three horizons after Task 1's universe-deepening; 4h POWERED, 1h/15m UNDERPOWERED). CLAUDE.md's `research` daemon thread entry and "Ships DISABLED" note both match this build state.

## NEXT-SESSION-TODO.md — superseded historical handoff (2026-08-31)
- source: docs/superpowers/plans/NEXT-SESSION-TODO.md (precedence 7, lowest in the manifest)
- note: A session handoff snapshot describing the state immediately after the Strategy Foundation build: kernel running with `population=19` (15 legacy genomes + 4 compiled specs), 414 tests passing, pending tasks including basis data widening, retiring the legacy `Genome`/`FAMILY_GENE_SPECS`/`proposer._sandbox_evaluator` path (only after the spec population "has traded enough paper to trust"), and backfilling the 99,651 HOLD decisions for `brain/meta_label.py`. This is explicitly a point-in-time snapshot; CLAUDE.md's current state (single creation path enforced, 748 tests, legacy genomes now all retired) supersedes essentially all of it. Kept here for provenance only, not as a current source of fact.
