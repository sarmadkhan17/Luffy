# SOL1 — one strategy lifecycle (2026-10-16)

```
CREATE -> BACKTEST PASS -> >=10 PAPER TRADES -> OWNER APPROVAL + LIVE SAFETY -> BOUNDED LIVE ELIGIBILITY
```

Offline only. No protected held-out read, Referee run, error-budget spend, provider call, Kernel/Dashboard
start, production write, Factory activation or deployment. Real first-live execution stays disabled: it needs a
separate owner deployment receipt that nothing in the Kernel, Dashboard or learning writes.

Baseline: RP-02 head `e9f639e` (RP-03 + SG-03 phase 1 `9bdc981` already in its ancestry) on the unified INT-01 candidate.

## 1. Why a created strategy never reached paper (traced)

| Stage | Before | Cause |
|---|---|---|
| Analyst admission (`Analyst.admit`) | passed, then `Kernel._install_spec` wrote a **bare, unversioned** paper row ("RESEARCH_PAPER_ONLY_LEGACY") | no Factory version, so no exact install, no versioned paper executor, no probation, and `live_entry_block` = `VERSIONED_AUTHORITY_REQUIRED` forever |
| Factory `create_version` | accepted **only** a `research_candidate` with a registered Referee gate1 look + gate3 | `research.referee=false`, `research.handoff=false` (config, preflight-asserted); nothing could ever reach `VALIDATED` from a created strategy |
| `Kernel._install_version` (exact paper install) | had **no caller** | dormant |
| Backtest evidence vs. runtime | Analyst measured the legacy simulator; paper/live run the Factory closed-bar exit engine; writer prompt taught trailing stops the engine refuses | evidence described a different strategy than the one installed |
| Backtest vs. runtime regime | backtest ignores `regime_filter`; runtime gates on it (and legacy install overwrote it with an advisory measurement) | unmeasured guess changed behaviour |
| Probation | `paper_probation_trades: 15`; legacy `promotion.py` carried a second set of thresholds (15 trades, WR .40, PF 1.15, 6 losses, PF .85/20, 14 d) | duplicate/conflicting; the legacy branch could never mutate state (every `transition()` raised `replay_complete_learning_authority_required`) |
| First live | `live_entry_block` returned a fixed refusal for every `ACTIVE` version | pathway never completed |

## 2. Check classification (every check on the path)

| Check | Where | Class |
|---|---|---|
| DSL/compile, data coverage (`missing_data`), supported exit whitelist | `compile_spec`, `spec_evidence`, `exit_policy.unsupported` | BACKTEST |
| Recent pooled PF after modelled costs, ≥ `select_min_trades`, window widening | `rolling.recent_verdict` | BACKTEST |
| Dependence-aware cross-symbol rotation null + common-calendar joint control | `Analyst._null_evidence`, `portfolio_null`, `dependence` | BACKTEST (robustness) |
| Signal-redundancy against the book (`MAX_SIGNAL_OVERLAP`) | `Analyst.redundancy` | BACKTEST |
| Persistence, regime fitness, TradingView second opinion | `Analyst.persistence/regime_fitness/confirm_on_tv` | RESEARCH-ONLY (advisory, recorded, never gate) |
| Referee gate1/gate3, LORD++ budget, frozen cut, protected held-out | `research/*`, `factory_handoff.gate_evidence` | RESEARCH-ONLY (retained; no longer a promotion prerequisite) |
| Immutable version, exact install, receipts, SG-03 current-install resolution | `factory_handoff` | PAPER (identity) |
| ≥ N closed paper trades with exact version/install/identity, complete cost + exit evidence, WR, net PF | `evaluate_probation/_assess` | PAPER |
| Win-rate/PF over a rolling recent window for deployed specs, decay | `Analyst.review_deployed`, `rolling.has_decayed`, Governor retire | ONGOING MONITORING |
| Learning replay gates (`refuse_legacy_learning`) | `learning/*` | ONGOING MONITORING |
| Exact owner decision bound to config; request/decision/receipt hash chain | `record_owner_decision`, `verify_owner_configuration` | LIVE SAFETY |
| Governor activation: Risk release, control ACTIVE, capacity, inputs, allocation ceiling | `govern_version` | LIVE SAFETY |
| Capacity receipt (account, instrument, liquidity, leverage, funding) | `strategy/capacity.py` | LIVE SAFETY |
| Risk sizing, EntryAuthority, Execution, protective stops, recovery, FROZEN/HALTED | `engine/*` | LIVE SAFETY |
| Legacy genome promotion rules (15 / .40 / 1.15 / 6 / .85 / 20 / 14 d) | `promotion.evaluate_population` | **REDUNDANT** (dead and conflicting) — removed |
| Bare unversioned paper install of admitted specs | `Kernel._install_spec` | **REDUNDANT** (cannot ever be promoted) — removed |
| Referee look as the only way to mint a version | `create_version` | **REDUNDANT as a mandatory gate** — now one of two sources of the same receipt |
| Keys `tv_validation`, `auto_demote_drawdown_pct`, `max_active` | `config.yaml` | **REDUNDANT** (no reader) — removed |

## 3. Backtest PASS contract (`strategy/qualification.py`)

* Not a new test. It is `Analyst.admit` — the same recent-PF, rotation-null and redundancy decision — run by
  `Analyst.qualify` with the **Factory exit semantics bound** so the numbers describe the strategy that is installed.
  Unsupported exits are refused before evaluation (`unsupported_versioned_exit`).
* The record (`strategy-backtest-qualification.v1`, scope `PAPER_PROBATION_ENTRY_ONLY`) stores the numbers, the
  thresholds in force, the evaluated-spec hash, the exit-semantics id and the `quant_contract` run identity
  (spec, data, config, engine digests). Its id is the hash of its content.
* `verify` **re-derives** the verdict from those numbers (trades ≥ min, PF ≥ floor, both rotation p-values finite and
  ≤ `null_max_p`, overlap ≤ max, per-symbol sums reconcile with the pooled figures). A flag cannot make a PASS.
* `create_version` additionally requires thresholds at least as strict as the configuration now, the frozen spec to be
  exactly the evaluated spec up to two declared freezes (universe := scored markets when none was declared; the
  unmeasured regime guess dropped), and a supported exit. Anything else is another strategy.
* Reproducible: the same data/config/spec yields the same record id (verified twice on the real engine).
* Consumes no research error budget and reads no protected slice. A Referee-registered candidate remains a second
  *source* of the same version/receipt (research lineage); it is not required.
* Fix found on the way: `Analyst.null_max_p` read `cfg["strategy"]` (wrong key); the configured value is under
  `strategies`. Same value today; now read from the right place.

## 4. Ten-trade paper PASS contract

`strategies.paper_probation_trades: 10` (was 15). Unchanged and still required together with it:
`paper_min_winrate: 0.40`, `paper_min_profit_factor: 1.15` on **net** P&L; only closed trades of the exact
installed version/install with VERIFIED entry identity, complete exit-semantics evidence and complete cost evidence;
trades of other versions or unproven identity make probation `INCOMPLETE_IDENTITY`; trades before the exact install
do not count. Nine → `INSUFFICIENT_TRADES`; ten failing WR or net PF → `NOT_SATISFIED`; missing cost →
`INCOMPLETE_COST_EVIDENCE`. Probation is trade-counted: there is no minimum elapsed time.
Success files the exact-version approval request in the same transaction.

## 5. Live safety contract

Unchanged: exact owner decision bound to config, Governor activation (Risk release, control ACTIVE, capacity,
inputs, allocation ceiling), Capacity, Risk sizing, EntryAuthority, Execution, protective stops, recovery, FROZEN/HALTED.
New (dormant, isolated-test complete): `record_first_live_deployment` — the owner's separate, immutable,
exact-version deployment receipt (owner actor, guarded writer, raw SQL refused). `live_entry_block` returns `None`
only when that receipt exists and still matches the owner decision, spec and Risk configuration **and** the full
chain re-verifies now (control ACTIVE, bounded allocation, validation/install/probation/approval, current ESTABLISHED
capacity, inputs). Otherwise a stable HOLD reason; the strategy keeps paper-trading. It is checked at the Kernel, in
`Executor.open` and twice in EntryAuthority. In production no receipt exists, so real execution stays disabled.
Deliberately not a config flag: approvals bind the config hash, so a flag flip would void every approval.

## 6. Removed

`Kernel._install_spec` (unversioned install); `promotion.py` legacy thresholds and dead transition logic
(statistics/`stats_json` refresh retained); writer prompt's trailing-stop schema; three unread config keys;
tests that asserted only the dead behaviour.

## 7. Verification (offline, isolated storage)

* `tests/test_sol1_strategy_lifecycle.py` — 64 tests: forged/resealed/tampered PASS refused; PASS cannot attach to
  another strategy, universe, regime guess, timeframe or unsupported exit; valid strategy -> PASS -> exact paper
  install; nine trades insufficient; ten pass; ten failing win rate / net PF / cost evidence / identity refused;
  successful probation -> exact owner request -> approval; approved + capacity + Risk -> `live_entry_block` None only
  with the owner deployment receipt; 12 HOLD cases (FROZEN, HALTED, stale capacity, changed Risk config, changed
  install, corrupt validation/probation/approval/deployment evidence, PAUSED, RETIRED); unsafe size refused by Risk;
  edited strategies inherit nothing; no unqualified path; Kernel installs only versions; real `Analyst.qualify` on
  synthetic candles issues a reproducible PASS and refuses a no-edge series and an unsupported exit.
* Regression: 372 test files, baseline `e9f639e` vs candidate, per file. No new failure. Baseline failures are
  pre-existing and unchanged (105 in the 304 files outside the lifecycle surface; 13 in the 72 inside it, after the
  tests of the removed dead behaviour were deleted).

## 8. Remaining gaps / owner decisions

1. **Paper PASS is impossible in production until a cost source exists (OWNER_DECISION).** Net P&L requires an
   authoritative commission rate and executable-price source per paper trade; the only registered adapter is
   no-funding-crossing. Account fees do not prove hypothetical paper fees (module contract). Options: accept the
   backtest's labelled `quant_contract.cost_model` as an ESTIMATED paper cost source, or build a venue-schedule
   adapter. Not invented here.
2. The Backtest PASS has no drawdown or win-rate gate (recorded, not gating): none existed to reuse. New thresholds
   are an owner decision.
3. Dropping the writer's regime guess changes behaviour for newly created strategies (previously overwritten by an
   advisory measurement); regime fitness remains advisory.
4. No owner-facing command writes `record_first_live_deployment` yet; deliberately.
5. Per-version allocation ceiling bounds Governor activation; Risk still sizes from configuration.
6. The Referee-sourced version path remains `VALIDATED`-only (nothing installs it); research handoff stays closed.
7. A PASS is a hash-chained, re-derived record, not a signature; same trust boundary as the rest of the journal.
8. Trailing stops are unrepresentable under the Factory exit contract; the one historically strong mechanism used one.
