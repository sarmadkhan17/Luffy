# Luffy — current capability and logging audit against the owner end goal

Date: 2026-09-16. Scope: **source/test inspection** of `trader/` and
`tests/`, with a targeted import check in `scripts/`, assessed against
[owner end goal](../specs/2026-09-16-luffy-end-goal.md) §1–24 and its
acceptance contract A–D.

**What this audit is not.** No trading process, production database, ledger,
log file or market data was inspected, and nothing was executed against the
venue. Configuration and credentials were not displayed or assessed. Some
existing test helpers do call `load_config()`/`Env`, which load repository
configuration and environment; their database writes use temporary fixtures.
This is not a claim that every test was isolated from configuration reads. Every "wired" statement below means *the call exists on a code path
that boot/cycle can reach*, never *it is running now*. Many behaviours are
branch-selected by `cfg` flags whose deployed values were not verified;
where relevant, **both branches are described** and neither is claimed to be
active. Tests used synthetic inputs, temporary journals and mocked dependencies. This is a source audit, not a
certification of live correctness, venue behaviour or risk adequacy.

Status vocabulary: **impl** (implemented and reachable from the kernel),
**partial**, **offline** (real code, standalone, no kernel path),
**design-only**, **not-found** (not found in the paths inspected here — a
scoped search result, not a proof of non-existence).

---

## 1. Requirement matrix (owner §1–24)

| § | Requirement | Status | Evidence (file:line) | Runtime wiring | Tests | Specific gap |
|---|---|---|---|---|---|---|
| 1 | Complete autonomous trader | partial | composite of rows below; branch logic `orchestrator.py:442–449`, `:488–489` | kernel cycle `kernel.py:793` | — | Attention, world model, hypotheses and expression are absent from the live loop. What *scores* a symbol is branch-dependent and **not determined here** (see row 3): either the weighted analyst blend or a strategy-authored score, with a further independent strategy gate on direction |
| 2 | Economic survival, never force trades | partial | `engine/rent.py:16` `COUNTED`, `:20–26` `week_bounds`, `:78` `verdict`; `rent_keeper.py:38` `tick` | `kernel.py:653` `_rent_loop`, started `kernel.py:277` (cfg-gated on `rent.weekly_usdt`) | `test_rent.py`, `test_rent_keeper.py`, `test_rent_wiring.py` present, **not executed here** | Bar is a single config USDT figure; only venue income types count (`rent.py:16`). Infra/data/LLM/service costs never enter, so the verdict measures venue income, not economic viability. The window is a **calendar week** — `week_bounds` (`:20–26`) returns [Monday 00:00 UTC, +7d); the owner requirement of a **rolling** week is not met. Report-only by design (`rent_keeper.py:5`) — correctly never forces a trade and no escalation path forces one |
| 3 | Not majority-vote agents / not LLM decider | partial | `orchestrator.py:84` `net_score`, `:442` blend, `:447–449` strategy-led override, `:451–454` agreement→threshold, `:146`/`:488–489` `strategy_gate`; no LLM import in `engine/` | wired; **which branch is live is cfg-gated and unverified** | `test_orchestrator_agreement.py`, `test_strategy_signal_gate.py` present | Two independent, separately configured mechanisms: **(a)** if `scouts.strategy_leads` is on *and* at least one strategy signal survives `:421–438`, `net` is replaced by `strategy_score(sigs, sw)` (`:448–449`) — analyst votes are still computed, still journalled, and still feed `agreement_fraction` (`:451`), which sets the threshold the strategy score must clear, so analysts remain influential even when they do not author the score; if the flag is off, or no signal exists, the weighted analyst blend at `:442` sets the score. **(b)** independently, `require_strategy_signal` (`:488–489`) passes the already-chosen direction through `strategy_gate`, which can veto it or demote it to a gated lean. Neither flag's value was read. In the blend branch, seven analyst convictions are reduced to one weighted scalar |
| 4 | Whole accessible dynamic universe; reference ≠ executable | partial | `data/feed.py:490` `Universe` (majors + top-N by USDT quote volume, `:497–510`); `kernel.py:284` `_filter_universe_to_venue` uses `exchange.load_markets()`; `data/references.py:39` `Ref(source, tf, close_after_ms, max_stale_ms)`, `REFS` `:59–68` (spx, dxy, gold, us10y, vix, oil, btcdom, TOTAL/TOTAL2) | wired (venue filter at boot) | `test_universe_wiring.py`, `test_declared_universe_is_traded.py`, `test_ref_sources.py` present | The inspected futures path uses venue-supported USDT contracts. Those may include traditional-asset exposures; current listings and account permissions were not checked. No general multi-venue, account-aware expression/universe service was found. Reference data and executable contracts have separate paths; exchange metadata provides tradability checks, but there is no unified cross-market capability contract |
| 5 | Attention allocation | **offline** | `cognition/attention.py:93` `evaluate`; salience = max(\|volume_z\|,\|vol_trans_z\|,\|div_z\|) `:17–36` | **no kernel path found** — used by offline modules, tests and `scripts/cognition_history.py:38–40` / `scripts/cognition_coverage_audit.py:43–45`; `kernel.py` imports no cognition module | `test_cognition_attention.py` **executed, pass** | Live scan is `kernel.py:1070` `_scan_symbols` = universe ∪ spec preferences, all of it then scored. No anomaly detection, no ranking, no "deserves attention" output on the kernel path |
| 6 | Why this asset (cause attribution) | offline/partial | `attention.py:31–33` broad-vs-isolated; `replay.py:91` `market_state`/`cohort_ok`; `hypotheses.py:37` templates | none | covered by executed cognition tests | Only market-wide vs asset-specific. No sector-rotation, derivatives/liquidation-driven or macro-driven classification anywhere |
| 7 | The questions Luffy must ask | design-only (live) / partial (offline) | `contracts.py:126` `Hypothesis(prediction, falsification, deadline_ms)`; live rationale surfaces: `votes.rationale`/`votes.meta` (`journal.py:45–46`, written `:229–238`), `decisions.signals_json` (`journal.py:167`, written `:243`), `decisions.skip_reason` (`:61`), `strategies.hypothesis`/`invalidation` (`:146–147`) | none | executed | The kernel does persist per-analyst rationale, per-signal payloads and skip reasons — `skip_reason` is **not** the only rationale. What is missing is a single record per decision that states what is happening, what is expected, and what would falsify it: the pieces are scattered across four tables with no unified hypothesis narrative, no prediction, no falsifier and no deadline on the live path |
| 8 | Competing hypotheses | offline | `contracts.py:126–137` incl. `supporting_ids`, `contradicting_ids`, `probability: float \| None = None` ("unset until calibrated") | none | executed | Three templates (`market_continuation`, `asset_divergence`, `unknown`); no probability updating. **Positive:** the code refuses to emit an uncalibrated number rather than faking one |
| 9 | Contradiction is informative | offline | contradictions retained per `test_cognition_attention.py` docstring | none | executed | Disagreement survives as individual `votes` rows either way, and `agreement_fraction` (`orchestrator.py:451`) turns it into a threshold adjustment — so it is not discarded. But it is never recorded *as a contradiction*: in the blend branch conflicting convictions collapse into one scalar (`:84`, `:442`); in the strategy-led branch (`:448–449`) the blend is computed and then not used for the score. Neither branch stores which claims opposed which, or what the disagreement implied |
| 10 | World model as state | **not-found** (as a durable object) | nearest: `agents/regime.py:20` `classify` → `{regime, adx, vol_ratio}`, persisted as `cycles.regime/adx` (`journal.py:23–33`); `replay.py:91` transient `market_state` | regime wired | `test_regime_series.py` present | No persisted world-state, no transitions, no liquidity/positioning state, no analogues. Indicators are stored as scalars on a cycle row, i.e. evidence used *as* state |
| 11 | Automatic pattern discovery | partial | `research/planner.py:231` `next_batch`, `vocab.py:45` `Gauge`/`:54` `Part`/`:204` `gauges_for`; ML: `brain/meta_label.py:117` `_fit` (logistic), `agents/calibration.py:33` `_fit_logistic`, `agents/weights_online.py:51` `update` | `kernel.py:411` `_research_loop` (cfg-gated); meta/calibration refits `kernel.py:1287–1303` | `test_research_planner.py`, `test_research_combo.py` etc. present | Search is over a **hand-authored** gauge/expression vocabulary, not clustering, change-point, sequence or hidden-state models. Failure-condition discovery is not an explicit target |
| 12 | Surprise drives curiosity | not-found | — | — | — | Nothing measures surprise, and nothing feeds research priority from it. `planner.py:198` `_grow_children` is beam/ablation driven |
| 13 | Attention itself learns | not-found | `attention.py:50` `CognitionConfig` is fixed constants | — | — | No fitting of scanner parameters. The `baseline_samples` emitted by `replay.py` are the right raw material for this, but nothing consumes them |
| 14 | Strategy emergence pipeline | partial | `kernel.py:448` `_mechanism_once` (ideas → `spec_writer` → `analyst.admit` → `:530` `_install_spec`); `brain/analyst.py:352` `admit`; `research/planner.py:231` `next_batch` over market series | wired; scraper/crawler cfg-gated (`kernel.py:272/275`) | `test_idea_pipeline.py`, `test_spec_writer.py`, `test_analyst.py` present | Two entry paths exist. The research planner **does** search observed market data for patterns independently of any text idea (row 11), and its candidates reach the same `admit()` via `_research_handoff` (`kernel.py:475`). The LLM path originates in scraped text. The gap is therefore not "no market-pattern generation" but the **absent link**: nothing routes an anomaly or a surprising observation into attention → hypothesis → a targeted research question; the planner's search is driven by its own beam/ablation schedule, not by what the market just did |
| 15 | Strategy population / genome | partial | `journal.py:138–157` `strategies` (hypothesis, invalidation, regime_filter, markets, generation, parent_id, stats_json, spec_json); `strategy/promotion.py:50` `evaluate_population`; `health.py:56` `assess_health` | `kernel.py:1313` | `test_promotion_rules.py`, `test_strategy_health.py` present | No first-class mechanism, cost-sensitivity or payoff-profile fields; no combine/specialize operations |
| 16 | Direction and expression | partial | `core/types.py:30` `Action = BUY \| SELL \| HOLD` | single-leg decisions wired | — | Long, short and no-trade outputs exist. No general comparison of alternative instruments, pairs, relative-value, hedge or convex expressions was found in the inspected decision path |
| 17 | Portfolio intelligence | partial | `engine/risk.py:137` `check_entry`: max positions `:154`, one-position-per-symbol `:156`, heat cap `:160–170`, margin caps `:185–199`; `brain/analyst.py:279` `redundancy` | wired | `test_margin_caps.py`, `test_portfolio_evidence.py` present | **No factor-aware concentration or marginal-risk model.** The limits are per-position and count-based: BTC+ETH+SOL consume three of the position slots, and the heat cap sums each position's standalone stop distance. Covariance between positions is not modelled at all — neither to inflate a correlated book's risk nor to net an offsetting one — so portfolio risk is a sum of standalone risks, and no marginal-contribution or cluster limit exists. `redundancy` runs at admission only, never on the live book. `portfolio_evidence.py:36` is backtest-side |
| 18 | Deterministic risk constitution | **impl (as source)** | `engine/risk.py` (no LLM import): FROZEN/HALTED `:143–145`, drawdown halt `:149`, daily breaker `:153`, heat, margin, min-notional `:200`; native stops `executor.py:240` `_place_native_stop`, `:191` `_recover_unprotected_entry`; `reconcile.py:69` adopt/align/ghost, `:215` NAKED POSITION re-arm; `protective.py:117` `sweep_orphans`; `reconcile.py:271` `flatten_all` | boot `kernel.py:252`, panic `kernel.py:805`, risk-halt on `RiskError` `kernel.py:892`, gate `kernel.py:966` | `test_protective_stops.py`, `test_naked_position_is_rearmed.py`, `test_reconcile.py`, `test_margin_caps.py` **present, not executed here** | The structural separation holds in source: the deterministic checks are wired and do not depend on an LLM's approval. **This is not a risk certification.** Live behaviour, venue-side stop placement and rejection handling, partial fills and failure modes under a degraded venue were not audited; the named tests were read by name only. Known design gaps: no covariance/marginal-risk model (row 17), and no fast tier between cycles (row 23) |
| 19 | 12–15 components | partial | `kernel.py:44` `AGENTS` (structure, flow, momentum, value, rotation, positioning, depth) + `macro_guard.py`, `news_guard.py`, `regime.py`; brain: analyst, strategist, spec_writer, scraper, crawler, harvester, postmortem, doctrine; `org.py:80` `analysts_from_code` keeps the roster derived from code | wired | `test_org.py`, `test_company.py` present | Missing as **runtime roles in the kernel roster**: World Model, Hypothesis Engine, Attention, Critic/Counter-Thesis, Opportunity Ranking, RV/Expression. Some of the underlying code exists as modules (`cognition/`, `research/planner.py`, `knowledge/vault.py`) — what is absent is their unified presence as components of the running organisation, addressable and accountable like the seven analysts. The count is met; the **domains** are not |
| 20 | Allocation of work (det./ML/LLM) | largely impl | deterministic `engine/`; ML `meta_label.py`, `calibration.py`, `weights_online.py`; LLM `brain/llm.py` with per-purpose budgets `:23`, `:68` `budget_left` | wired | `test_llm_purpose_budget.py` **executed, pass** | LLM correctly absent from risk/execution. Gap: a failed LLM call is indistinguishable from an exhausted budget — both return `None` (`llm.py:80`, `:105`, `:131`) |
| 21 | Memory chain | partial | `journal.py` tables; `knowledge/vault.py:143` `Vault` → markdown (`:338` `daily_review`, `:427` `agent_ledger`, `:455` `incident_note`, `:372` `chain_dailies`) | `kernel.py:1279–1335` | `test_org_vault.py` present | The chain the owner asks for (state+event+hypothesis+counter+evidence+decision+execution+outcome+aftermath) does not exist: there are **no hypothesis and no evidence tables**, and the only join is `cycle_id`/`decision_id` across four tables |
| 22 | Learning from everything incl. non-trades | partial (**strong**) | `orchestrator.py:585` `journalize` → `:596` schedules an outcome for **skipped** directional decisions, `:600–608` grades gate-suppressed leans, `:612` `_maybe_shadow_outcome`; `outcomes.py:18` `resolve_pending`; `outcome_backfill.py:79` `upgrade_24h`, `:136` `backfill_holds` | `kernel.py:1249` `_maybe_resolve_outcomes` | `test_outcome_backfill.py`, `test_outcome_24h_upgrade.py` present | The most directly owner-aligned mechanism found in this audit, and it grades non-trades. Gaps: outcomes are forward returns only (correctly **not** simulated PnL); only *decided* symbols are graded, so anything the scan never reached, or that was dropped silently (§2 below), is invisible; nothing surfaces "what I learned" to the owner |
| 23 | Fast / medium / slow horizons | partial | medium: `kernel.py:1518` `run` loop at `timeframes.scan_interval_seconds`; slow: daemon threads `kernel.py:258–284` (derivs, refs, mechanism, validator, scraper, crawler, rent, research) | wired | — | There is **no fast tier**: order book is polled inside the cycle (`kernel.py:734`), and risk checks run in the medium loop. Between cycles the exchange stop is the only protection (a documented invariant). LLM is confined to slow threads — consistent with the requirement |
| 24 | Economic survival brain | partial | `rent.py` venue side (`REALIZED_PNL`, `COMMISSION`, `FUNDING_FEE`); tokens `llm.py:56` `_spend` → `data/brain_usage.json` | `_rent_loop`; dashboard `server.py:838–847` | `test_rent*.py` present | Honest **venue-income** accounting — realized PnL, commission and funding, over a calendar week (Monday-anchored, `rent.py:20–26`), never a deposit. That is progress toward, but not the same as, **economic viability**: the cost half is absent (data, infra, LLM and service costs never reach the verdict; token spend is never converted to USD or joined to income), the required window is a *rolling* week, and there is no calibrated statement that expected return is sufficient against risk taken. **Positive:** no coverage probability is claimed, and nothing escalates into forced trades when the bar is missed |

---

## 2. Logging and diagnostics inventory

**Correlation IDs.** `cycle_id` is minted in `orchestrator.py:382` (`new_id("cyc")`)
**per symbol per `decide()` call** — it is an evaluation ID, not a scan ID; no
identifier groups all symbols examined in one kernel cycle. The chain that works
by schema is `cycles → votes → decisions → outcomes` on `cycle_id`
(`journal.py:38,53,72`) and `decisions → trades` on `trades.decision_id`
(`journal.py:87`). **The guarantee stops at the event tables.** `brain_events`
(`journal.py:115–121`) and `control_events` (`:123–131`) have no correlation
*column*: `subject`, `detail` and `control_events.detail` are free text/JSON and
**can** carry a decision, trade or spec id — `_install_spec` writes `spec.id` as
`subject` (`kernel.py:545–547`), and reconciliation/executor events embed
identifiers in text. So joining is possible case by case, but there is **no
uniform, schema-level join**: a reader must know each writer's string format,
and any writer that omits the id is unjoinable. Reconciliation results
(`reconcile.py:266`), naked-position re-arms (`:215–253`), unconfirmed fills
(`executor.py:152`) and estimated P&L (`executor.py:319`) should be checked
individually rather than assumed joinable. Separately, there is no observation or
evidence record on the live path, and no live hypothesis row — hypothesis text
exists as `strategies.hypothesis`/`invalidation` (`journal.py:146–147`) and in
the offline `cognition` contracts — so the *unified* `event → evidence →
hypothesis` chain of contract B has no live storage.

**Reason codes.** Live reasons are free English assembled by string join —
`orchestrator.py:582` `"; ".join(skip_bits)`, risk refusals `risk.py:143,154,156,171,188,200`,
entry denials `kernel.py:962,983`. They can be grouped as strings, but there is no
stable taxonomy: the wording is composed at the call site, interpolates values,
and can change without a version, so counts are fragile and distinct causes can
share a prefix while one cause spells itself several ways. The only enumerated
vocabularies found are offline: `contracts.py:119` observation status
`ok|missing|stale|warmup|gap|degenerate|insufficient_cohort` and `:147` outcome
status `confirmed|falsified|not_testable|measured|unresolved`.

**Errors and unknowns — inconsistent, not absent.** Some failures *are*
persisted: a failed spec write becomes a `brain_event` (`kernel.py:498`
`spec_write_failed`, with the writer trace), a rejected spec likewise (`:514`),
and a `RiskError` during entry raises a state change to HALTED through
`state_machine.set(..., "risk_engine", str(e))` (`kernel.py:892–893`), as does a
drawdown breach (`:799–801`) — both land in `control_events`. Other paths are
silent or log-only, and these are the ones that matter for "why did nothing
happen": the top-level cycle exception is `log.exception` only
(`kernel.py:1538`); a failed analyst is `log.warning` + `continue`
(`orchestrator.py:400–402`); a failed strategy evaluation likewise
(`:434–436`); a universe frame that fails to fetch is `log.warning` + `continue`
(`kernel.py:1061–1063`); and a symbol whose snapshot is `None` is skipped with
**no record at all** (`kernel.py:869–871`) — it is simply missing from `cycles`,
indistinguishable from never having been in the scan. An owner reading the
database cannot tell a failed cycle from a quiet one.

**Timestamps, freshness, provenance, versioning.** Journal rows carry a single
ISO `ts` — no separate event time vs observation time, no availability time, no
provenance, no missingness. Real freshness policy exists but is not journalled:
`references.py:39` `Ref(source, close_after_ms, max_stale_ms)` decides staleness
per reference, and `core/types.py:248` `closed_bars` enforces closed-bar
discipline. The fixed columns of the trading tables carry **no standard code,
model, prompt, input or schema version field**. Some JSON payloads and spec
lineage do carry identity — `strategies.spec_json` (`journal.py:185`),
`generation`/`parent_id` (`:150–151`), `decisions.signals_json` (`:167`,
strategy ids per signal) — and the offline module versions itself explicitly
(`cognition/__init__.py:9` `SCHEMA_VERSION`, `replay.py:59`
`config_id = stable_id(...)`). So lineage is partial and per-writer; what does
not exist is a uniform version/hash contract that lets a reader say which code
and config produced any given row.

**LLM purpose / usage / cost / failures.** `llm.py:23` per-purpose reservations,
`:68` `budget_left`, `:56` `_spend` writes tokens by day and purpose to
`data/brain_usage.json`, `:100` logs tokens per call. Not journalled; no USD, no
latency, no per-call record, no link to a decision or spec. Failures are
`log.warning` + `None` (`:105`, `:131`), and empty-content truncation is warned at
`:110`. `_spend` wraps everything in `except: pass` (`:57–59`), so token accounting
can silently lose spend.

**API health and retries.** Retry logic exists — `executor.py:217` `_confirm_fill`,
`:284` fill-history backoff (`:63` `fill_retry_s`), `feed.py:422` dead-symbol retry
delay. There are **no** health counters, error rates or journalled API failures,
so a degraded provider is invisible except in the text log.

**Structured logs, rotation, retention, drops, redaction.** `kernel.py:52`
`setup_logging` installs a `RotatingFileHandler` on `logs/luffy.log`
(`logging.max_bytes` default 10 MB × `backup_count` default 5) plus a stdout
handler, with a **plain-text** format (`:56`) — not structured, not JSON, so the
trace in contract B cannot be machine-reconstructed from the log. Log files
rotate; **no pruning or retention was found for the SQLite journal** (no
`DELETE`/`VACUUM` on trading tables; the only deletes found are research-ledger
resets, `research/ledger.py:332`). No drop counters or capture of logging
failures were found — contract D's "no silent loss of critical audit records" is
not demonstrably met. **No redaction layer was found.** Credentials appear safe by convention, not
enforcement: `llm.py` holds `self._key` and never logs it, and the dashboard's
`TokenGuard` (`server.py:426–441`) compares tokens without logging them.

**Owner visibility.** Dashboard `server.py:26` `create_app`: `/api/summary` `:51`,
`/ws/live` `:118`, `/api/pipeline` `:248`, `/api/org` `:332`, `/api/doctrine` `:346`,
`/api/chat` `:356`, `/api/brain/autopsy` `:370`, `/api/logs` `:417` (raw tail of
`luffy.log`), plus `build_company` `:623` with analyst/events/trader/risk panels and
a rent line at `:838–847`. One page, `dashboard/web/index.html`. Telegram
listener `kernel.py:1421` with status/accuracy/summary commands `:1443`.
Against acceptance contract A, the missing narrative items are: what Luffy is
focused on, what attention promoted or ignored and why, active hypotheses and
confidence updates, why an expression was chosen, and a full economic cost
breakdown.

---

## 3. Bottlenecks, ranked by owner value and dependency

1. **No typed record of why a symbol produced nothing** — the top logging
   priority, and a prerequisite for everything below it. Concretely: a strategy
   dropped for ineligibility, market type, regime or symbol scope writes no row
   at all (`orchestrator.py:421–431`); `strat_lib.evaluate` returning `None` and
   `evaluate` raising are both `continue` and are indistinguishable afterwards
   (`:432–438`), since `signals_json` records only signals that were produced;
   an analyst returning `None` and an analyst raising are likewise both
   `continue` (`:400–404`); and a symbol whose snapshot is `None` vanishes
   entirely (`kernel.py:869–871`). The consequence is that **absence of a signal
   is currently read as "no edge"** when it may be ineligibility, missing data or
   a crash. Needed: an enumerated cause per (symbol, strategy/analyst) —
   `evaluated_no_signal`, `ineligible:<which rule>`, `missing_data:<what>`,
   `evaluation_failed:<exc type>`, `not_evaluated` — written on the same pass,
   with "no genuine edge" reserved for `evaluated_no_signal` alone. This is the
   **co-deliverable** with item 2: attention that records what it looked at is
   only interpretable next to a record of what happened to each candidate.
2. **There is no attention layer on the kernel path** (§5, §12, §13). The kernel
   scans a volume-ranked, spec-extended universe and asks every strategy about
   every symbol. This is the owner's stated *first* intelligence problem, and it
   blocks §6, §7, §12 and §13. Working, tested code already exists offline.
3. **No live unified event → evidence → hypothesis chain** (§7–§9, §21,
   contract B). The parts are not missing from the repo — offline `cognition`
   models observations and hypotheses, `research/` carries a stated thesis and
   gate record, `strategies.hypothesis`/`invalidation` store a mechanism's claim
   — but nothing links a live event to the evidence read and the claim made, and
   no live surface answers "why did you do that" as one narrative.
4. **Event tables have no correlation column and reason strings no taxonomy**
   (contract B). Joins to `brain_events`/`control_events` are per-writer string
   conventions, not guaranteed. Cheap to fix incrementally, and it is what makes
   troubleshooting hard today.
5. **No factor-aware concentration or marginal-risk model** (§17). The only item
   on this list with a direct loss-exposure consequence: risk across positions is
   summed standalone, with covariance unmodelled in both directions.
6. **No expression layer** (§16). Large and valuable, but it depends on 2–3
   existing first; building it now would have nothing to express.
7. **Operating costs never enter the survival verdict, and the window is a
   calendar week** (§2, §24). Small, bounded, and needed before any rent verdict
   can be called a viability statement.
8. **Pattern search is disconnected from observation** (§11, §14). The planner's
   market-data search is real work, correctly gated and properly stopped at the
   recorded gate decision; what is missing is anything that points it at what
   just happened.

---

## 4. Recommended next slice — milestone 1 (**not implemented on this pass**)

**Forward-captured attention + typed evaluation causes, off the trading path,
rendered for the owner. It decides nothing and claims nothing about edges.**

**Scope, labelled honestly.** The candidate set is `_universe_frames(_scan_symbols())`
(`kernel.py:864–865`): majors + top-N by USDT quote volume, ∪ symbols specs ask
for, venue-filtered, then reduced to symbols that have the execution timeframe
(`:1066`). That is a **strategy- and volume-selected subset**, not the venue-wide
universe. Every row and every surface carries a `scope` label naming the
selection rule, the caps and the exclusions, so nothing can read as "Luffy looked
at everything". A cheap venue-wide scout (ticker-level, no per-symbol frame
fetch) is separate later work and must not be conflated with this expensive
strategy scan. **The current strategy scan is preserved unchanged** regardless of
what shadow attention says.

**Availability and provenance — forward capture only.**
- **Do not set `available_ms = close_ms`.** The candle store (`feed.py:103–106`)
  keys on `(symbol, tf, ts)` with OHLCV only — no arrival time, no version, no
  record of what was seen when. Bar close time is *not* availability.
- Record `first_seen`/`received_at` at the moment the collector actually holds
  the value, with provenance (which exchange instance/endpoint, which cache
  layer) and the snapshot's `as_of`. Keep the venue's event/close timestamp as a
  **separate field**; never conflate the two.
- For history already in the store, `first_seen` is **now** — the moment this
  mechanism first observed it. Availability before that is `unknown` and is
  labelled so; it is never backdated or inferred.
- **Revisions:** a later fetch returning a different value for an already-seen
  `(symbol, tf, ts)` appends a new version row with its own `first_seen` and a
  pointer to the prior version. Nothing is overwritten; a published snapshot is
  immutable.
- No held-out data is read and no experiment is implied. This is collection
  design only; the recorded research gate stop is untouched.

**Off the hot path — no synchronous evaluate-and-journal.** A broad `except`
around in-cycle work does not make it latency-free, so it is not proposed.
- The scan publishes an **immutable observation snapshot** (already-built
  frames, scan metadata, `as_of`) to a bounded queue without waiting. Evaluation
  later publishes separate immutable cause records linked by the same `scan_id`;
  it never mutates an already-published snapshot. That publish is the only
  hot-path cost and needs a **measured budget** (state a p99 per cycle and test
  against it), not an assumption.
- One bounded worker consumes snapshots, runs `attention.evaluate`, and writes.
  Hard caps on queue depth, per-snapshot timeout, rows per scan and wall-clock
  per scan. On overflow it drops and increments a drop counter; it never blocks
  the producer and the producer never waits on it.
- Failure is visible, not swallowed: counters for dropped / failed / timed-out /
  sink-unavailable, plus a worker heartbeat with last-success time. A journal
  sink or storage failure degrades the worker alone — no exception, retry or lock
  may reach the risk, execution or exit path, and a fast risk action must never
  wait on a diagnostic write.
- Typed evaluation causes must be produced on the evaluation pass, which knows
  the cause, and published as separate immutable events for the worker to write.
  A dedicated bounded telemetry store avoids high-volume writes or retention
  locks contending with the trading journal. Only the small forward `scan_id`
  link belongs in the existing decision write. Separate threads alone do not
  isolate SQLite lock contention; this must be demonstrated in failure tests.

**A complete record — selected and not selected.** Per scan, one row per
candidate including the lowest-ranked: `scan_id`, `as_of`, `symbol`, `selected`,
`rank`, `salience`, component z-scores or `NULL`, `status` (the `contracts.py:119`
vocabulary), the typed cause per strategy/analyst, an `observation_id` per input
with its payload/provenance reference, cohort size and missingness counts,
`scope`, `schema_version`, `config_id` and a code hash. Not-selected rows and
null components are first-class data; nothing is filled with 0.

**Identity and joins.** One `scan_id` per scan, minted fresh each scan;
deduplication is within a scan, by `(scan_id, symbol, subject)`. `decisions` gets
a **nullable** `scan_id`, populated only for decisions made from a scan going
forward — historical rows stay `NULL` and **no historical join is fabricated**.
Determinism is semantic: the same frozen inputs, config and `as_of` produce the
same ranks, salience and statuses — *not* identical independently minted ids.

**Owner-readable view.** An API alone does not mean the owner sees anything.
Deliver both: a read endpoint (`/api/attention/latest`) **and** a rendered panel
in `dashboard/web/index.html` showing the latest scan — promoted, not promoted,
the cause for each, freshness and scope. Stale, unknown and error states render
explicitly as such with their `as_of`; the UI must never show the last successful
scan as if it were current.

**What milestone 1 must not claim.** It records attention and causes. It invents
no hypothesis text, no confidence number and no result. Hypotheses, competing
claims, falsifiers and outcomes are **milestone 2**, built on the existing
`cognition.contracts` templates plus honest outcome collection. This slice does
not close the cognition loop and must not be described as doing so.

**Acceptance cases** (synthetic; no network, no real data)
1. **Determinism:** frozen inputs + config + `as_of` ⇒ identical ranks, salience
   and statuses; ids differ per scan, which is expected and asserted.
2. **Availability:** no record ever takes its availability from a bar close;
   store-sourced history gets `first_seen = capture time` and prior availability
   `unknown`.
3. **Revision / late arrival:** a changed value for a published
   `(symbol, tf, ts)` appends a version row and leaves the earlier snapshot's
   rows bit-identical; a late arrival cannot alter a prior scan.
4. **Unknowns:** missing anchor bar → `stale`, hole → `gap`, short history →
   `warmup`, cohort below `min_cohort` → `div_z NULL`, unrecognised → `unknown`.
   Never 0, never silently dropped.
5. **Isolation:** with the sink unavailable, and separately with the queue at
   cap, a cycle that must close a position still closes it on its normal path,
   and the error/drop counters increment and are readable.
6. **Trading-behaviour equality** vs the flag-off path: identical sequence of
   decision actions, risk-check calls and order calls. Explicitly **excluded**
   from the comparison: the intentionally new diagnostic/attention writes.
   Timing is *not* asserted by equality — it is measured separately against the
   stated hot-path budget.
7. **Causes:** ineligible, evaluated-no-signal, missing-data and raised-exception
   each yield a distinct typed cause. Even a successful evaluation returning no
   signal is not evidence that no economic edge exists; never label it that way.
8. **Joins:** every `scan_id` resolves, forward decisions carry it, no
   observation/evidence reference dangles, historical decisions stay `NULL`.
9. **UI:** symbol and cause strings are escaped as data, not markup; a stale or
   failed scan renders as stale/failed.
10. **Flag off:** zero new rows, zero new calls, no worker started.

**Retention.** A per-scan row cap bounds one scan, not the table. Operational
attention and cause events need bounded retention (age *and* size, enforced and
tested), separate from **pinned, immutable research snapshots** exempt from
pruning. The journal has no pruning at all today (§2), so this ships with the
feature, not after it.

**Likely files** — `trader/cognition/adapter.py` *(new, pure: frames + provenance
→ `Dataset`)*; `trader/cognition/collector.py` *(new: bounded queue, worker,
counters, heartbeat)*; a dedicated telemetry store (versioned observations,
`attention_rows`, `eval_causes`, retention); `trader/core/journal.py` (nullable
`decisions.scan_id` only); `trader/kernel.py:793`
(publish only); `trader/engine/orchestrator.py:400–438` (emit typed causes into
the snapshot); `trader/dashboard/server.py` + `dashboard/web/index.html`
(endpoint **and** panel).

**Tests** — `tests/test_cognition_adapter.py`, `tests/test_attention_collector.py`
(caps, drops, sink failure, isolation), `tests/test_eval_causes.py`,
`tests/test_attention_view.py`, following the `no_network` fixture pattern at
`tests/test_cognition_contracts.py:57–62`. Re-run unchanged:
`test_single_creation_path.py`, `test_orchestrator_abstention.py`,
`test_forming_bar_never_stored.py`.

**Rollout.** Additive schema, no migration of existing rows, no change to
admission, gate, sizing or risk paths; `research.referee`/`research.handoff`
untouched. Config default **off** — merging code is not deployment, and no live
telemetry runs until a deliberate, verified rollout enables it under the
owner's existing demo/change authorization. This is not an additional permission
requirement; inspect operational configuration and unrelated workspace changes
before deployment. Attention output is
labelled an **observation**, never a signal, on every surface.

**Explicitly: attention stays out of the decision.** This slice makes attention
*observable*; it does not let it narrow the scan or influence any trade. That
would be a separate, evidenced change.

---

## 5. Limitations of this audit

- **No deployed-state verification.** No trading service was started or queried,
  and no production database, log, ledger, artifact or market data was inspected.
  Test helpers read configuration/environment via `load_config()` and `Env`;
  tests use temporary journals. No credential values were printed. Every
  cfg-gated branch and loop (`scraper`, `crawler`, `research`, `references`,
  `rent`, `scouts.strategy_leads`, `require_strategy_signal`) is reported as
  **unverified**, not as on or off, and every "wired"/"reachable" claim describes
  a **code path**, not a running system.
- Test support is distinguished as *present* vs *executed*. Executed here, all
  passing: `tests/test_cognition_contracts.py tests/test_cognition_attention.py
  tests/test_cognition_replay.py tests/test_cognition_history.py` — **73 passed**;
  `tests/test_single_creation_path.py tests/test_llm_purpose_budget.py` —
  **16 passed**. The `no_network` autouse fixture at
  `tests/test_cognition_contracts.py:57–62` blocks `socket.connect` and
  `socket.create_connection` within its module and is imported by the other
  cognition test modules. The creation/budget tests do not share that fixture:
  they use temporary journals/usage files, mocked admission or LLM objects, and
  a budget-exhaustion case that returns before creating a network client.
  Their helpers also load repository configuration/environment; they are not
  fully configuration-isolated. All other tests named
  in this report were inspected by name only.
- No market-performance claim is made or implied anywhere in this report, and no
  research gate is weakened or reinterpreted. The recorded dependence-corrected
  gate stop stands.
- Absence statements are scoped: "not-found" means not found in `trader/` and
  `tests/` by the searches run for this audit — a bounded negative result, not a
  proof of non-existence, and not a claim about the whole repository.
- No runtime source, test, configuration or frozen research artifact was changed.
  Existing unrelated workspace changes were preserved. Only this report and the
  handoff note were written.

Claude performed the inventory and tests. The coordinator independently reviewed
key source paths and completed final documentation corrections after Claude hit
its session limit; that fallback was disclosed to the owner.
