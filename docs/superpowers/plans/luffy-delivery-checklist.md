# Luffy delivery checklist — canonical status

Baseline: 2026-09-16. Governing [roadmap](../../ROADMAP.md) and
[owner requirements](../specs/2026-09-16-luffy-end-goal.md).

**Current count: 12 of 55 scoped acceptance items checked; 2 of 11 milestones
accepted.** Five checked items are bounded foundations, five are investigation engineering deliverables, and two are usable-memory engineering deliverables. No connected
intelligence-to-strategy milestone has been accepted. These counts are not an
estimate of overall project completion; the items differ substantially
in effort and evidence requirements. Existing partial components are credited
below without checking their unfinished end-to-end capability.

`[x]` = the exact stated acceptance has dated evidence. `[ ]` = not accepted,
including partial code, prototypes and waiting-for-evidence work. Each new check
must link a dated report and identify synthetic/offline/demo/forward/quantitative
evidence. Never count a design document as implementation evidence.

## Session update — September 21, M3.2 pre-RNG truth package frozen

[FREEZE.json](../../../scripts/m32_scheme_d_pre_rng_truth_20260921/FREEZE.json) (git tag
`m32-pre-rng-truth-freeze`). Certified base commit `4998e36`; package SHA-256
(`package/manifest.sha256`) `a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb`. 55 cells x 384 rows = 21,120 analytic truth
rows; `freeze_possible=true`; 0 unresolved rows and 0 uncertified kernels;
independent verifier PASS; 25 sources checked against 9 family manifests, every
hash recorded. Five previously untracked pinned inputs (both protocol documents,
`trader/cognition/m32_scheme_d_validation.py`, `trader/cognition/m32_search.py`)
were committed unchanged; their hashes were already verified. The package is
**immutable**: `freeze_package.py --check` and `tests/test_m32_pre_rng_freeze.py`
fail on any drift. No RNG, search, Gate 2 or protocol change.
This freezes the analytic truth tables (protocol section 14 item 4) only; the other
section 14 items remain outstanding, no validation has run, and the checklist
count is unchanged (M3.2 not accepted).

## Session update — September 20, P5/C3 population-majority freeze

Freeze commit `8f7879c16f4af3e57832e596c526e6b428dd3cd4`;
[immutable manifest](../../../scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_population_majority_freeze_manifest.json)
and [report](../../../scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_population_majority_freeze_report.md).
P5/C3 population majority is independently certified strictly above `20/31`.
Primary `224`-bit interval:
`[0.645216427771829392546478549481683481928584334281467566037294269634151944648755335137471153, 0.662187252542225967878166476727777231928584334281467566037294269634151944648755335137471153]`.
Independent `384`-bit replay interval:
`[0.645216427771705111575396679089493803661445700301031304870729099062428241714014254309347057, 0.662187252542101686907084606335587553661445700301031304870729099062428241714014254309347057]`;
replay **PASS**. The intervals overlap; neither contains the other.

Verified SHA-256 identities: certifier
`76814e9bd8a57beb3360ece0f61a69a98ff9d3e34405eb00a499f1ddb61dbf05`,
checkpoint
`3f5bdcd54590c14dae4c94670cbbb86973ba729b3b06697d9eebe34e52b59066`,
primary report
`65d2d35f4ed8fdd4d73cf6047b91f6f42d85300b20b615387361033491bcfc36`,
and frozen categorical estimand spec
`8c1ed60f8b6e9f22608c6c491506e8b0c888dd296c02b62c266a1e40ace92755`.
No RNG and no protocol change. This bounded certificate does not accept M3.2;
the 12/55 count is unchanged. **P5/C3 hit/miss certification remains
outstanding and is the single next task.**

## Current board

| Milestone | Status | Accepted | Existing work to reuse |
|---|---|---|---|
| M0 bounded foundations | ACCEPTED within its listed scope | 5/5 | Attention, receipts, shadow consumer, dashboard, entry recovery |
| M1 persistent investigations | ACCEPTED engineering scope | 5/5 | Authenticated live dossiers and bounded worker; forward outcomes pending |
| M2 memory changes reasoning | PARTIAL; M2.3/M2.4 accepted synthetically | 2/5 | Typed forecasts/trades/skips/simulations and raw replay delivered; case producers/references delivered; M3/M8 dependencies and live maturity pending |
| M3 pattern-to-candidate discovery | PARTIAL; M3.1 cohort implemented | 0/5 | PIT builder/replay, prospective capture and neutral cohort; frozen-window review complete Sep 18, coverage NOT accepted (~53% in >10-min gap intervals) |
| M4 comparative validation/population | PARTIAL components | 0/5 | Existing rolling Analyst/admission/decay; no new intelligence-derived strategy accepted |
| M5 dynamic market coverage | PARTIAL components | 0/5 | Universe/feed, derivatives, reference sources; not complete accessible-market coverage |
| M6 expression and portfolio intelligence | PARTIAL components | 0/5 | Directional strategies, deterministic portfolio limits/evidence |
| M7 evaluated adaptive learning | PARTIAL older learning components | 0/5 | Analyst calibration/meta-label/weights; not the new event-to-strategy loop |
| M8 execution/accounting completeness | PARTIAL, bounded recovery deployed | 0/5 | Executor/protection/reconcile/state; residual recovery and accounting gaps documented |
| M9 economic viability | PARTIAL components | 0/5 | Rent/cost-related code; full reconciled operating-cost contribution unaccepted |
| M10 complete autonomous demo acceptance | NOT ACCEPTED | 0/5 | Existing dashboard/watchdog; full owner goal remains unfinished |

## M0 — Bounded foundations already delivered

Evidence: [attention rollout](../reports/2026-09-16-attention-rollout.md) and
[exposure recovery](../reports/2026-09-16-exposure-recovery.md).

- [x] **M0.1** Bounded direction-independent attention capture persists scans, input versions and evaluator/decision receipts, with measured producer overhead and explicit coverage limits. Evidence: attention rollout, synthetic timing plus demo scans.
- [x] **M0.2** Separate forward-only consumer registers selected and ignored price forecasts, deduplicates pending episodes, and implements exact-target/missing-data rules. Evidence: attention rollout, synthetic resolution tests and demo pending registration. Live outcome resolution is not included in this check.
- [x] **M0.3** Owner can view authenticated attention/learning health and safe textual receipts in a rendered dashboard. Evidence: rollout browser artifacts; recovery report's deployed API verification.
- [x] **M0.4** Duplicate legacy loading of compiled specs is fixed, with a regression test and zero missing evaluators in the corrected demo scan. Evidence: attention rollout and `tests/test_population_spec_path.py`.
- [x] **M0.5** Durable futures-entry recovery blocks uncertain exposure, confirms protection/flat state and preserves operator controls; deployed without affecting existing protected demo positions. Evidence: recovery report, 24 synthetic recovery tests and deployment/venue artifacts. Full execution accounting remains M8.

## M1 — Investigations that change with evidence

Depends on M0. Immediate [implementation plan](2026-09-16-market-investigation.md).
Reuse `trader/cognition/{contracts,attention,hypotheses,replay}.py` and
`trader/observability/{store,learning}.py` through versioned adapters.

- [x] **M1.1** Define and implement versioned state/investigation/update contracts and a coherent read-only snapshot adapter. Replay the same prefix to identical records; reject future/unavailable inputs and preserve original artifacts. Evidence: [investigation delivery](../reports/2026-09-16-market-investigation-delivery.md); 161 final-source tests, synthetic paired cases and demo registration/source joins. Predictive performance unvalidated.
- [x] **M1.2** Build market/per-asset state and trigger-relevant questions for volume, volatility and relative moves, with explicit unsupported causal claims and cross-market/participation gaps. Owner can inspect facts, contradictions and unknowns for each case. Evidence: [investigation delivery](../reports/2026-09-16-market-investigation-delivery.md); 161 final-source tests, synthetic paired cases and demo registration/source joins. Predictive performance unvalidated.
- [x] **M1.3** Persist an investigation across observations. Append assessment changes, discriminating tests, invalidation and WAIT/ACQUIRE/RESEARCH/UNASSESSABLE reasons; keep all alternatives and immutable earlier updates. Paired demonstrations prove relevant new evidence changes only later assessments. Evidence: [investigation delivery](../reports/2026-09-16-market-investigation-delivery.md); 161 final-source tests, synthetic paired cases and demo registration/source joins. Predictive performance unvalidated.
- [x] **M1.4** Connect a bounded restart-safe consumer to new demo scans, isolate it from fast risk/execution, expose failures/limits and prevent duplicate investigations. No historical proposal backfill or new strategy permission. Evidence: [investigation delivery](../reports/2026-09-16-market-investigation-delivery.md); 161 final-source tests, synthetic paired cases and demo registration/source joins. Predictive performance unvalidated.
- [x] **M1.5** Render owner-readable live investigation dossiers and retain a replayable walkthrough showing what changed, why the assessment changed and what is needed next. Demonstrate volume-only, volatility-only, contradiction and missing-data cases with explicitly labelled synthetic fixtures plus actual forward-opened cases. Evidence: [dashboard/memory delivery](../reports/2026-09-16-dashboard-memory-delivery.md); fail-closed login deployed on loopback with SSH owner access, live 401 refusal and authenticated browser/API/GraphQL/WebSocket verification. Prior M1 walkthroughs retained.

## M2 — Outcomes become usable memory

Depends on M1. Reuse `core/journal.py`, cognition contracts and the separate
forward consumer. Preserve existing ledger/protocol versions.

- [ ] **M2.1** Verify actual matured forward outcomes against exact target input versions, registration times and frozen baselines; retry missing evidence explicitly and preserve immutable terminal outcomes. Preserve selected/ignored coverage. Evidence: [dashboard/memory delivery](../reports/2026-09-16-dashboard-memory-delivery.md); at 18:41 UTC, 16 pending/zero matured, exact registration keys and baseline versions verified; original scan envelopes expired, exact shared versions remain. See also [typed outcome evidence](../reports/2026-09-16-typed-outcome-memory-delivery.md). Actual outcome acceptance remains pending; first original target closes 2026-09-17 00:00 UTC. Latest 19:48 UTC check: zero matured, each original now lacks one exact baseline version; explicit missing_versions_retry. See [latest exact-source evidence](../reports/2026-09-16-memory-producer-integration.md).
- [ ] **M2.2** Connect investigation outcomes, executed-trade results, skips, missed opportunities, false signals and regime changes to typed case records. Actual P&L, simulated counterfactuals and non-economic observations remain distinct. Partial: investigation plus selected/ignored forecast, trade/skip and registered missed-opportunity simulation adapters implemented; 205 relevant tests passed, forward skip import deployed, legacy accounting remains unknown. Versioned false-signal/volatility-transition producers and an explicit forward non-trade caller now pass synthetic integration acceptance; actual reconciliation receipts remain M8-dependent. See [producer integration](../reports/2026-09-16-memory-producer-integration.md), 221 final-source tests. No live cost declaration was invented.
- [x] **M2.3** Retrieve compatible cases using actual resolution/availability time, versions and stable matching rules; log inclusion reasons and exclude future-resolved cases. Evidence: [dashboard/memory delivery](../reports/2026-09-16-dashboard-memory-delivery.md); bounded deterministic investigation-case retrieval, actual resolution/availability/import clocks, frozen context and exclusion audit; synthetic chronology tests and deployed adapter. Live resolved retrieval remains unobserved.
- [x] **M2.4** Demonstrate a completed earlier case changing a subsequent investigation's caution, counter-test or evidence request; compare with the same investigation without memory. Do not infer predictive superiority from a changed narrative. Evidence: [dashboard/memory delivery](../reports/2026-09-16-dashboard-memory-delivery.md); synthetic chronological consumer walkthrough records a later changed counter-test, exact source cases and no-memory comparator. No predictive-performance claim.
- [ ] **M2.5** Implement bounded retention/export and replay across restarts/revisions; retain failure, regime, pattern and cross-market memory links. Keep owner preferences and proposed doctrine revisions distinct from frozen doctrine. Partial: bounded typed retention with counted skip eviction, self-contained raw-input investigation/forecast export and offline replay, atomic restore with local import clocks, and versioned broader link contracts tested. Investigation/failure/regime/cohort references and observed journal decision/strategy-attribution references are now populated by tested producers; strategy definition versions stay unknown where absent. Pattern production remains M3-dependent. See [producer integration](../reports/2026-09-16-memory-producer-integration.md), 221 final-source tests.

## M3 — Automatic patterns produce mechanical strategy candidates

Depends on M1–M2. Reuse `research/`, `brain/spec_writer.py`, `strategy/dsl.py`,
feature contracts and candidate-dossier artifacts. Preserve discovery boundaries.

- [ ] **M3.1** Build a point-in-time dataset of state/transition/sequence episodes and outcomes, including ignored assets and failure cases; declare its discovery cut, coverage and dependence units before searching. Partial: versioned bounded builder and freeze/capture CLI implemented; 268 relevant final-source tests passed. Saved synthetic replay includes selected/ignored, failure, transition and sequence cases; live retrospective capture has 78 observation-only skips and zero PIT rows. Forward window September 17 00:00 to September 18 00:00 UTC frozen September 16 20:38:40 UTC. Prospective population/coverage hooks now implemented, tested (269 relevant final-source tests) and enabled in existing watchdog consumers before the window. Classification-neutral cohort/replay and local-index reconciliation now implemented (277 final-source tests). Full-window review completed September 18 01:17 UTC: capture replay passed, indices match exports (331/331, 239/239), but coverage is **not accepted** — ~53% of the window inside >10-minute observation-gap intervals (no scans after 15:20 UTC; `collector_unhealthy` from 16:00 — origin diagnosed Sep 18 (strongly supported inference): live-tree git operations removed the worker module across 37 cycles, and the cumulative error count has no recovery semantics; see [collector-health diagnosis](../reports/2026-09-18-collector-health-diagnosis.md)), every scan misses at least one declared symbol (LSK, TAO or SUI), 16/30 forecast rows overdue-unresolved at cut, typed dataset 14 rows with three declared kinds absent and 0 sequences; diagnosis done; coordinator-reviewed fix proposal ([brief](2026-09-18-collector-recovery-health-brief.md)) with implementation and runtime verification pending; a new frozen window only after healthy verification. See [window-close review](../reports/2026-09-18-m31-window-close-review.md). Classification-conditioned typed cases do not establish population rates. See [cohort delivery](../reports/2026-09-16-pit-cohort-delivery.md). See [dataset delivery](../reports/2026-09-16-pit-dataset-delivery.md) and [population delivery](../reports/2026-09-16-pit-population-delivery.md). Latest engineering: [declared-universe delivery](../reports/2026-09-18-declared-population-delivery.md), `51101d0`, 203 tests, guarded deployment and natural 16/16 capture. Preserve Sep 18→19 freeze; distinct Sep 19 08:00→Sep 20 08:00 freeze awaits activation after prior cut review. Coverage remains unaccepted.
- [ ] **M3.2** Implement the smallest useful quantitative/ML search for repeated patterns and failure conditions, with simple baselines, common calendar cuts and registered search budgets. Demonstrate discoveries beyond merely renaming existing indicators. Partial evidence: the complete 55x384 pre-RNG analytic truth package is frozen (Sept 21 update; base `4998e36`, package SHA-256 `a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb`); no RNG validation, search or Gate 2 has run, so M3.2 is not accepted.
- [ ] **M3.3** Connect an investigation/pattern to competing mechanism claims and at least one falsifier, confound or ablation test. Surprise prioritizes research; LLM prose cannot substitute for computed measurements. Evidence: pending.
- [ ] **M3.4** Produce a reproducible StrategySpec candidate containing mechanical entry/exit/invalidation, universe, timeframe, data needs, costs and lineage back to events/cases. Route through the existing spec writer/admission interface; no second creation path. Evidence: pending.
- [ ] **M3.5** Publish an owner-readable candidate dossier with source cases, exact rules, measured discovery evidence, contrary evidence and the next frozen test. Record rejected/untestable ideas as well as promising ones. Evidence: pending.

## M4 — Honest validation and an evolving strategy population

Depends on M3 and the accounting/coverage needed by each test. Reuse
`brain/analyst.py`, `strategy/{rolling,spec_evidence,portfolio_evidence,vector_backtest}.py`.
The recorded research stop remains binding.

- [ ] **M4.1** Freeze a candidate-vs-current-system evaluation contract before evaluation: versions, shared universe/time, costs, risk, meaningful endpoints, uncertainty/power and dependence handling, error budget and untouched chronology. Separate engineering experiments from admission evidence. Evidence: pending.
- [ ] **M4.2** Measure recent viability, cost/slippage/funding sensitivity, delay, robustness and counter-tests on the candidate's own supported universe. Missing/underpowered/prohibited evaluations return UNTESTED/deferred, never a pass. Evidence: pending.
- [ ] **M4.3** Emit a complete comparative verdict: candidate qualifies, loses, or remains inconclusive, with all searches/looks disclosed. Better-strategy claims require evidence after costs against the registered comparator, not raw forecasts or pooled counts. Evidence: pending.
- [ ] **M4.4** Exercise the single admission/probation/review path with current deterministic risk and portfolio redundancy rules. Trace recent specialization, weakening, recovery and retirement to evidence; too few recent trades means idle, not automatic decay. Evidence: pending.
- [ ] **M4.5** Demonstrate the end-to-end investigation-to-admission integration and a versioned legacy migration/rollback procedure in demo. Actual replacement remains conditional on a qualified candidate; explicitly record pending admission rather than inventing a winner. Evidence: pending.

M4 may finish engineering integration with a rejected candidate while comparative
performance/admission observations remain pending. Do not check M4.5's actual
migration evidence until a permitted, qualified replacement is observed. Never
resume Gate 2 or reroute stopped evaluation through a renamed experiment.

## M5 — Accessible universe and cross-market evidence

Incremental alongside M1–M4. Reuse `data/{feed,derivatives,references,ref_sources}.py`
and instrument metadata. Extend only within current authorization.

- [ ] **M5.1** Persist a timestamped instrument registry with executable/observe-only/unavailable states, venue restrictions, liquidity and contract specifications; refresh membership from metadata and expose exclusions. Evidence: pending.
- [ ] **M5.2** Connect coherent BTC/ETH/alt breadth, peer/sector and relative-strength context to investigations; retain historical membership provenance instead of assuming today's cohort existed historically. Evidence: pending.
- [ ] **M5.3** Add the specific positioning/flow/liquidation/funding evidence needed to distinguish hypotheses, with provider units, freshness, retention and coverage measured. Missing participation cannot establish a participant explanation. Evidence: pending.
- [ ] **M5.4** Connect feasible macro, equity/rate/dollar/commodity, catalyst and fundamental/on-chain references with publication/availability clocks; keep unsupported causal claims unassessed and source costs visible. Evidence: pending.
- [ ] **M5.5** Demonstrate cross-market explanations and instrument eligibility under outages/revisions/new listings. Plans for pairs/options/TradFi execute only where supported and explicitly within owner-authorized scope. Evidence: pending.

## M6 — Best expression and portfolio action

Depends on M4 and relevant M5/M8 support. Reuse current risk and portfolio evidence.

- [ ] **M6.1** Represent long, short, relative-value/pairs, hedge and cash alternatives, with unsupported expressions explicitly unavailable. Define payoff/invalidation, costs, capital needs and uncertainty for each eligible expression. Evidence: pending.
- [ ] **M6.2** Compare instruments and expressions for the same thesis using quantitative evidence; explain why outright direction, a hedge, a relative trade or no trade is preferable. Evidence: pending.
- [ ] **M6.3** Measure marginal portfolio benefit and common-factor/concentration/correlation risk with existing positions; demonstrate that multiple correlated assets are not treated as independent diversification. Evidence: pending.
- [ ] **M6.4** Route the selected portfolio proposal through deterministic position/heat/leverage/circuit-breaker controls. Contradictory evidence and insufficient payoff can select cash without pressure to trade. Evidence: pending.
- [ ] **M6.5** Demonstrate replay and authorized demo decisions whose expression/allocation is changed by the new comparison, with recorded alternatives, vetoes and rollback. Multi-leg support requires failure/protection acceptance in M8 first. Evidence: pending.

## M7 — Evaluated learning improves the process

Depends on M2/M4 and sufficient untouched evaluation evidence. Existing agent
calibration/weighting is a reusable component, not proof of this milestone.

- [ ] **M7.1** Define a versioned learner for attention priority, state/pattern estimates or strategy selection, with bounded resources, eligible inputs, drift detection and proposed-change records. Freeze the first comparison before evaluation. Evidence: pending.
- [ ] **M7.2** Measure memory/learner vs frozen no-memory/static baselines on untouched chronological data with episode/market dependence, selection bias and uncertainty handled. A no-improvement verdict is preserved. Evidence: pending.
- [ ] **M7.3** Evaluate probability quality separately from outcome counts; publish calibration/reliability and proper scoring only with adequate coverage. Until then probabilities remain null or labelled unvalidated subjective estimates. Evidence: pending.
- [ ] **M7.4** Admit only validated bounded revisions, monitor drift and regression, and demonstrate automatic rollback/suspension without altering risk or bypassing strategy admission. Evidence: pending.
- [ ] **M7.5** Show a complete outcome-to-revision-to-next-decision trace, including learning from failures/skips and changes refused for insufficient evidence. Doctrine changes remain versioned proposals; frozen doctrine stays intact. Evidence: pending.

## M8 — Complete deterministic execution and accounting

Runs alongside intelligence for concrete dependencies. Reuse
`engine/{executor,recovery,protective,reconcile,exits,risk,state}.py` and journal.
M0.5 is the already-delivered bounded entry recovery, not completion of this list.

- [ ] **M8.1** Attribute actual fills, commissions, funding and realized P&L across normal, partial, emergency and restarted trades; explicit unknowns/estimates cannot enter learning as fabricated actual results. Partial: emergency order archive plus normal/partial/close booking provenance and offline replay implemented and demo-deployed; 195 relevant final-source tests passed. Exact leg evidence is distinguished from estimates and unverified symbol-window subtotals. A subsequent manual whole-trade reconciler and verified-outcome bridge pass 232 final-source tests: exact ownership/quantity joins, bounded history completeness, signed funding-event matching and explicit fee currencies. Synthetic complete receipts replay; at that (historical) snapshot actual funding/economics and first natural booking were unknown. Since then the controlled diagnostic sample `pos_05a5e8f981` completed with actual funding/economics (September 17), while natural non-diagnostic complete receipts remain unavailable (September 18: LINK/AAVE legacy entries cannot clear under the current reconciler by time or publication alone; NEAR/ZEC/UNI waiting close). A bounded automatic capture/retry worker is now demo-enabled through the watchdog; 247 relevant tests pass. See [worker delivery](../reports/2026-09-17-accounting-worker-delivery.md). A manual emergency-whole-accounting/native-exit-provenance slice is now implemented (DERIVED whole-trade arithmetic, never learning-eligible; native leg-only mapping against the official Binance algo-order field contract, partial exits unsupported/retry); 246 final-source targeted tests pass. See [exit-provenance delivery](../reports/2026-09-17-exit-provenance-delivery.md). Natural complete capture/import, non-USDT conversion acceptance (attribution evidence blocked, see [conversion evidence](../reports/2026-09-17-conversion-evidence-delivery.md)), an actually observed natural emergency/native exit and legacy consumer migration remain unfinished. See [whole-trade delivery](../reports/2026-09-16-whole-trade-accounting-delivery.md). See [normal accounting delivery](../reports/2026-09-16-normal-accounting-delivery.md) and [emergency accounting delivery](../reports/2026-09-16-emergency-accounting-delivery.md).
- [ ] **M8.2** Provide a reviewed recovery workflow for unknown order existence, ownership/size conflicts, ambiguous protection and residual stops; preserve protection and owner holds while states remain unresolved. Evidence: pending.
- [ ] **M8.3** Verify panic, close/retry, partial-stop residues and continuous reconciliation across restarts/outages without prematurely deleting protection or booking guessed fills. Evidence: pending for complete failure matrix.
- [ ] **M8.4** Preserve deterministic risk under concurrent decisions, stale balances, correlation/heat limits and any newly supported multi-leg execution; bound leg risk and verify venue truth before allowing more exposure. Evidence: pending.
- [ ] **M8.5** Complete a synthetic failure matrix plus authorized demo observation/reconciliation soak, with latency, retry/drop diagnostics and explicit unresolved cases. Quantitative candidate evaluation consumes only adequately attributed data. Evidence: pending.

## M9 — Rolling economic viability

Depends on M8 and measured opportunity evidence from M4/M6. Reuse cost/rent and
LLM budget components, with owner cost inputs explicitly pending where absent.

- [ ] **M9.1** Record agreed infrastructure/data/API/LLM/service/other costs with period, currency, provenance and actual/estimated status. Missing owner inputs remain unknown; do not invent weekly income targets. Evidence: pending.
- [ ] **M9.2** Reconcile gross results to net economic contribution without double-counting fees/slippage/funding already represented in fills; separate demo, hypothetical and real cash amounts. Evidence: pending.
- [ ] **M9.3** Publish rolling weekly/monthly cost coverage, capital efficiency, drawdown and cost-to-result diagnostics with uncertainty and missingness. Do not claim a calibrated coverage probability without its own evidence. Evidence: pending.
- [ ] **M9.4** Test economic-pressure invariance: a higher cost target cannot loosen risk, bypass admission or force a trade. Cash remains a valid survival decision. Evidence: pending.
- [ ] **M9.5** Deliver a viability verdict backed by measured rolling observations: supported, unsupported or inconclusive. Engineering of this verdict can complete without profitability; the owner's eventual self-funding objective stays pending until observed. Evidence: pending.

## M10 — Integrated autonomous demo acceptance and owner operations

Depends on M1–M9. Visibility and diagnostics must also ship with each earlier slice.

- [ ] **M10.1** Owner view answers focus, why selected/ignored, hypotheses/contradictions, confidence status, candidate/strategy lineage, instrument choice, risk veto, execution, learning, costs and degraded components in plain language. Evidence: pending for full narrative.
- [ ] **M10.2** Reconstruct event → evidence → hypothesis → candidate/version → decision → order/fill/protection → outcome → learning from retained versioned inputs; test bounded retention, redaction, log failure/drop signals and owner control audit. Evidence: pending.
- [ ] **M10.3** Demonstrate fast/medium/slow work isolation with measured latency/resource/LLM budgets, outages, watchdog/restart behavior, rollback and deterministic safety unaffected by slow reasoning or log sinks. Evidence: pending for integrated system.
- [ ] **M10.4** Run a predeclared autonomous demo acceptance campaign across sufficient events/regimes and failures. Review functionality, predictive/economic evidence and unresolved gaps separately; no invented sample count, forced trade quota or calendar guarantee. Evidence: pending.
- [ ] **M10.5** Publish a final owner-goal acceptance matrix and operational handoff with evidence for every requirement and remaining conditional scope decisions. Explicit owner authorization is required before any real-money or unsupported-market expansion. Evidence: pending.

## Session update block

**Latest: check-now, September 18 18:46–18:50 UTC (bounded, snapshot only).**
[Check-now 1846 report](../reports/2026-09-18-check-now-1846.md).
**⚠ Session error:** an accidental run of legacy `scripts.validate_population` retired
`spec_funding_filtered_trend_pullback` (paper → retired, false "duplicate"), wrote brain_events 1602–1605,
rewrote vault notes, and may have sent Telegram. **Corrected at 18:50:46 UTC**: restored paper, cleared false reason,
restored the previously clean Funding note, retained audit event 1606. Historical prior timestamp unknown; correction uses actual time.
Notification delivery remains unverified. Do not rerun this legacy script for inspection; a separate guard/spec-dedup fix is recorded.
Active window 18:47: 215/170 matched (was 111/97), 32 forecasts (08:00 cohort 16/16 resolved, 16:00 cohort 16 unresolved),
investigations HYPE and new BNB, both unresolved. There were no new cadence gaps and no missing members. New error: AVAX `ValueError` at 16:00
(1 missing-target retry, resolved 16:05). BTC is terminal and unchanged; BZ is still not terminal (expires Sep 19 04:00). M8.1: new retry
XRP `pos_3f5e7f308c` (6 attempts), LINK/AAVE at 10 each, no natural complete, controlled SOL still complete. Sep 19 04:00 cut
review is still due; the Sep 19 08:00 window is inactive.

**Previous: check-now, September 18 12:44–12:52 UTC (read-only).**
[Check-now report](../reports/2026-09-18-check-now-1244.md).
**Observed execution/collection interruption around 11:31→12:42 UTC; root cause unconfirmed.**
NIC/clock and cron evidence is consistent with an environment pause, not proof of host suspension.
The active window has an explicit ~75-minute cadence gap (11:30→12:45). The 12:45 resume scan logged
AAVE/AVAX/BNB/BR `TimeoutExpired`, and 09:00 logged BTC/DOGE/SOL `ValueError`. Every other receipt was available,
and LSK/TAO/SUI appeared in every scan. Active captures replay: 108/92 → 111/97 matched. There are 16 unresolved
immature forecasts and 1 new HYPE investigation. Old window unchanged: 347/240 matched,
at-cut 331/239 plus 17 late supplements. **BTC `investigation_ec2bad86c8f4189e` terminal at 12:45:14 UTC**
(measured; same/opposite contradicted, normalization compatible), 45 minutes after the deadline, following the observation gap.
This is supplemental ledger evidence. BZ is still unresolved (10 exact keys missing, expires Sep 19 04:00).
M8.1: no natural complete receipt. Controlled SOL is still complete. LINK/AAVE have 9 retries each, NEAR/ZEC/UNI
are waiting on close, and a new open HYPE trade is queued. The window stays open to Sep 19 04:00, and the Sep 19 08:00 freeze
is inactive. Next steps unchanged. The interruption and 09:00 ValueError are recorded, not fixed. Latest 12:50 scan is 16/16 available;
12:49 venue check verifies all five native stops, ACTIVE/demo and watchdog enabled. **12/55**.

**Latest: declared-universe collection deployed, September 18.**
[Delivery report](../reports/2026-09-18-declared-population-delivery.md).
Main `51101d0`; **203 relevant tests passed**, isolated review and guarded deployment.
New off-path collector attempts every frozen member, independent of trading selection
and the first-16 cap; per-symbol availability/error receipts and exact first-seen/version
provenance retained. **Natural 02:35 UTC pass: 16/16, both consumers accepted the same scan**;
investigation memory remains separately source-refused. Native demo stops, unchanged
`ACTIVE`/empty recovery and restored watchdog verified. 882 prior files unchanged;
12 captures replayed. Claude session-limited; authorized direct work completed.
Old window reviewed, coverage unaccepted: frozen 331/239 unchanged; current old exports
reconcile 347/240, late evidence supplemental (28 resolved / 2 unresolved forecasts
at capture; 14/16 at the original cut). Existing Sep 18 04:00 → Sep 19 04:00 freeze
preserved, still coverage-risk. **Distinct Sep 19 08:00 → Sep 20 08:00 window frozen,
not activated**, same universe. Next: Sep 18 04:00 active-window receipt/BZ supplemental
check; Sep 18 12:00 BTC `investigation_ec2bad86c8f4189e` in underlying ledger; Sep 19
04:00 full cut review/reconciliation, then activate the already-frozen future config
before 08:00. Report has exact procedure and rollback `28e03d7`.
M8.1 no natural complete receipt; controlled SOL stays complete; LINK/AAVE legacy
retries and NEAR/ZEC/UNI waiting close. No M3.2, Gate 2, backfill or deferred diagnostic
exclusion change. **12/55**. These are engineering/deployment observations, not
coverage/performance acceptance. Legacy outcome verifier reads only kernel attention;
use retained source receipts/new bound snapshots for declared scans (recorded follow-up).

**Latest: recovery deployed and new window activated, September 18.**
[Delivery report](../reports/2026-09-18-collector-recovery-delivery.md).
Main `e344a65`; 186 distinct relevant tests passed; guarded graceful restart,
native demo protection and `ACTIVE` verified; watchdog restored. Natural 02:15 pass
accepted exact collector evidence in both streams; investigation remains separately
`memory_source_refused`. Seven frozen captures replay; 867 prior files preserved.
New frozen window: **Sep 18 04:00 → Sep 19 04:00 UTC**, original 16-symbol universe,
separate exports/bindings; manual pre-window activation 1/1 receipts and replay passed.
**Known blocker: waiting alone cannot fix symbol coverage.** Current scan captures
16 of 20 candidates and omits LSK/TAO; public production candles for LSK/TAO/SUI
are available. **Next engineering step:** bounded declared-universe observation
independent of trading selection, with explicit per-symbol receipts. Preserve this
freeze; any collection-protocol change needs a distinct subsequent frozen window.
Next evidence checks: BZ 04:00 (outside-universe supplemental), BTC investigation
Sep 18 12:00 in the underlying ledger (old export stream closed), full new-window
review Sep 19 04:00. Old cut remains coverage-unaccepted; new late old-export events
are supplemental, not a revised at-cut claim (now 347/240, original 331/239 unchanged).
M8.1 no natural complete receipt; controlled SOL complete; LINK/AAVE legacy-entry
retries, NEAR/ZEC/UNI waiting close. No forced trade, M3.2, Gate 2, diagnostic exclusion
fix or research flag change. **12/55**.

- **Historical pre-implementation: collector-health diagnosis rev. 2 (coordinator-reviewed), September 18:** [diagnosis](../reports/2026-09-18-collector-health-diagnosis.md),
  [brief](2026-09-18-collector-recovery-health-brief.md) — **proposal only; implementation and
  runtime verification pending; no proposed-fix test run or passed** (only the existing failure
  was reproduced). Read-only; production DBs byte-copied (never opened) or `mode=ro`; no
  source/config/control change, no restart/trading/import/freeze; frozen hashes match.
  **74 worker errors:** strongly supported inference, not per-event observation — git operations
  in the live tree removed `trader/observability/` from 15:20:50 to 15:57:48 UTC; 37 cycles
  (#255–#291) ended inside, 37 × (scan+causes) = 74; no per-job trace retained. Saved counters
  (timeline 01:38:05.970): submitted 1736, processed 1662, errors 74, 0 drops/capture/timeouts.
  Retained scans 23:11:48.994→01:37:18.890, all complete. Class reproduced (`JSONDecodeError`), not
  retained. **16:00 onset** = first consumer pass after restore; cumulative `errors` cannot recover.
  **15:20→16:00:** old checked-out `watchdog.sh` without consumers (observed). **07:05→11:05:**
  guest-execution interruption hypothesis (journal empty 07:09:55→11:04:25, NIC down/up, clock
  change); host suspend not observed. Transient refusals: clock race is a reproduced possible
  explanation, not determined. Brief: instance-UUID + failure generation/fence (highest issued
  seq) + exact complete-scan certificate, health re-read after snapshot, strict clocks (no future
  tolerance), producer failure channel, bounded tracking fail-closed; 2 scans/300 s are engineering
  defaults. Deployment needs separate authorization and a graceful-stop barrier (`watchdog.off`
  alone does not stop the kernel), recorded-state restore and a pre-reviewed revert. **Next:**
  Slice 1 (recovery + diagnostics), optional Slice 2 (scheduler receipts); no new freeze before
  healthy verification; no branch operations in the live tree. **M8.1 unchanged** (01:37): SOL
  complete (diagnostic); LINK/AAVE legacy-entry retry; NEAR/ZEC/UNI `waiting_close`; no natural
  receipt. **12/55**.

- **Previous review September 18 (evidence from 01:17:05 UTC):** [M3.1 window-close review](../reports/2026-09-18-m31-window-close-review.md).
  Read-only; no source/config/control change, no worker invocation, import,
  trading or restart. New maturity/capture/review files; capture `d95e8e1a…`
  **replay passed**, freeze identical, five frozen hashes match, **0 events after
  cut**; indices = exports **331/331, 239/239**; independent
  [coordinator cut verification](../artifacts/pit-dataset/2026-09-18-coordinator-cut-verification.json)
  reproduces the at-cut view, 257 pre-existing files preserved. At-cut
  denominator **31**: forecast 14 terminal-resolved, **16 mature-unresolved-overdue**
  (LSK target missing; 15 of the 11:10 batch, deadline 16:00, with no scan/outcome
  processing recorded after 15:20); BTC `investigation_ec2bad86c8f4189e`
  immature/unresolved (matures Sep 18 12:00). No capture-vs-cut differences: no
  post-cut events and no deadline between cut and capture. Typed dataset only
  **14 rows**; `false_signal`/`regime_transition`/`skip` absent, 0 sequences,
  LSK/TAO absent. Verifier: 55 matured = 33 source-version retry + 22 outcome
  retry. Supplemental, outside frozen universe: BZ
  `investigation_3864bce371354aac` (04:00, immature at cut) and 7 forecasts incl.
  SAMSUNG — not in the frozen denominator. **Review complete; coverage NOT
  accepted:** no scans after **15:20:02 UTC**; gap share 53.1%/53.8% (sum of
  >10-minute observation-gap intervals ÷ 24h, not a precise missing-observation
  measure; missed counts unknown); every scan misses at least one declared symbol
  (LSK, TAO or SUI). Present refusal condition
  ([collector-health review](../artifacts/pit-dataset/2026-09-18-collector-health-review.json)):
  consumers refuse the collector's cumulative `errors` (74) despite `status=ok`;
  origin of the 74 errors, the 16:00 onset and the watchdog pass gaps remain
  **unresolved**. Builder limitations recorded, not fixed.
  **M8.1:** no natural complete receipt. LINK `pos_0136aef3df` (8 attempts) and
  AAVE `pos_57c828f65a` (7) refuse `legacy_entry_receipt_missing_retry`
  (pre-receipt entries) — cannot clear by time/publication under the current
  reconciler; NEAR/ZEC/UNI opened Sep 17 are `waiting_close` — next natural
  candidates. Controlled sample stays complete. Diagnostic-consumer exclusion
  owner-deferred. **Next:** bounded read-only investigation of the worker errors
  and collector recovery/health semantics, producing a concrete proposed fix for
  owner review before implementation (no status-only weakening of the safety
  contract); separately, a new frozen window. Owner merge validation (2015 passed,
  5 pre-existing failures, equivalence PASS, 373.9x) is validation only — not
  runtime deployment or performance evidence. M3.1 and M8.1 partial, **12/55**.

- **Latest review September 17 11:05–11:16 UTC:** [post-publication accounting review](../reports/2026-09-17-post-publication-accounting-review.md).
  Read-only; no production source/config/control change, no trading, no restart,
  no memory import, no research or design change. **Accounting for
  `pos_05a5e8f981` is complete.** Worker attempt 7 (captured **11:05:16.617
  UTC**) replays `complete_as_of_venue_history`, `reasons: []`,
  `funding_complete=true`, `funding_net=0.0`, `learning_eligible=true`; queue job
  `complete`, 7 attempts, no further retry. Complete economics gross **−0.0255**,
  fees **0.04035323**, funding **0.0**, net **−0.06585323 USDT**, identical to
  journal realized P&L with ownership proof. The SOL `fundingTime` **08:00:00
  UTC** rate published and cleared six prior
  `funding_publication_frontier_pending_retry` attempts; exposure at that
  boundary was 0. Attempt 7 ran **3h05m** past its 08:00:10 due time; **no
  watchdog passes or receipts are recorded** for 07:05→11:05 UTC. The **existing
  watchdog autonomously** restarted PID 300976 at 11:05:08 UTC (heartbeat
  14,175s stale), PID 341703 completed cycle 1 at 11:07:23 UTC, heartbeat 8.75s
  old at 11:08:46.722 UTC. No session restart. **Runtime verification is
  completed; the gap root cause is unresolved.** Runtime verified ACTIVE/demo,
  SOL flat, original three positions native-stop protected, five frozen hashes
  unchanged.
  **Deferred by owner decision — do not implement:** the diagnostic sample is
  included by the *existing legacy meta-label validation query* at row **316 of
  317** (cut 221); latest model `ready=false`, `w=null`, test Brier **0.2701**;
  **0 agent votes**, EWA cursor folded it without per-agent weight updates; no
  typed accounting-memory import. Owner chose to **keep the review read-only**
  and record a diagnostic-sample consumer-exclusion fix for later. One legacy
  consumer with a not-ready model — **not** a broad no-learning-contamination
  claim. M3.1 at 11:15:16.650/11:15:51 UTC, new files, capture replay passed:
  **52 forecasts, 34 mature** (32 `exact_source_version_missing_retry`, 2
  `exact_outcome_missing_retry`), 18 immature (2 at 12:00, 16 at 16:00 UTC); 34
  raw missing-version retries; scan retained for 16 of 52. **TAO/USDT's 20:00 UTC
  target resolved**; the stuck pair is now **LSK/USDT and SAMSUNG/USDT**. Cohort
  **30 rows**, window **open**, 3 selected/27 ignored, 14 resolved/16 unresolved,
  **14 terminal**, 1 dependence group, 0 orphans, `search_ready=false`. **Indices
  match exports: forecast 137/137, investigation 91/91.** Seven `explicit_gaps`
  record the outage; **missing declared symbols are broader than TAO** — TAO
  86/88, LSK 61/88, SUI 48/88 forecast scans. One investigation matures
  **2026-09-18 04:00 UTC, after the cut**: it **must remain visible in neutral
  coverage at the cut as immature/unresolved**; only its post-cut terminal
  outcome is unavailable at the cut. Full-window review remains due
  **September 18 00:00 UTC**. **251 focused tests passed, 15.50s**; no unrelated
  failures repaired, no clean-full-suite claim. No new candidate, no
  better-performance claim; `research.referee=false`/`research.handoff=false` and
  the recorded gate stop untouched. M8.1 and M3.1 partial, **12/55**.

- **Latest operation September 17 02:30 UTC:** [controlled demo sample](../reports/2026-09-17-controlled-demo-sample.md).
  Owner-authorized SOL long 0.51 opened/protected/closed via existing Luffy; four
  fills, two journal booking receipts, no new service or persistent design change.
  SOL flat; original three positions protected; same ACTIVE kernel/watchdog restored.
  Fill subtotal excluding funding -0.06585323 USDT; complete economics unknown.
  Manual 02:23 and scheduled 02:30 worker attempts both replay and preserve
  `funding_publication_frontier_pending_retry`. Next: observe publication/retries
  after the scheduled 08:00 UTC SOL boundary. No more trades needed for that wait;
  no controlled-sample strategy-performance import. Temporary authorization already
  granted; future design changes require review. M8.1 partial, **12/55**.

- **Latest: controlled demo sample, September 17 02:20–02:21 UTC:**
  [report](../reports/2026-09-17-controlled-demo-sample.md). Owner answered
  the prior open question: authorization is for **temporary demo samples
  only**, not a persistent design/config change. Coordinator ran one SOL/USDT
  round trip (`pos_05a5e8f981`, long 0.51 @ 98.93→98.88, 4 fills) through the
  existing main Journal/RiskManager/Executor with a native protective stop
  (placed and removed clean). Kernel PID 300976 briefly `SIGSTOP`/`SIGCONT`'d
  under an independent 90s watchdog-flag guard, 11.823161s, then resumed;
  original three positions unchanged/protected throughout. Fill subtotal
  **excluding funding**: -0.06585323 USDT — not complete P&L; funding retries
  on `funding_publication_frontier_pending_retry` until 08:00 UTC publication.
  13 disposable helper tests passed (0.94s); the 246-test/5-unrelated-failure
  evidence above is unchanged build-time evidence. No memory/strategy import,
  no persistent worker/design change. Do not repeat without a fresh owner ask.
  M8.1 partial, **12/55**.

- **Latest build September 17 01:41 UTC:** [exit-provenance delivery](../reports/2026-09-17-exit-provenance-delivery.md).
  Owner-approved emergency whole-accounting (DERIVED, never learning-eligible)
  and native exit provenance (leg-only, `whole_economics` always unknown,
  partial exits unsupported/retry) delivered as two manual CLIs against the
  official Binance algo-order field contract. **246 final-source targeted
  tests passed, 8.93s.** No clean full-suite claim: 5 unrelated failures
  (`test_cognition_contracts.py`, `test_partial_close_shrinks_notional.py`)
  isolated and independently re-run — 5 failed, 10 passed, 1.11s — confirmed
  in code this slice never touched, not fixed here, not claimed pre-existing
  without a baseline rerun. ACTIVE/demo, unchanged kernel, all three positions
  matched/protected, frozen hashes match; three live demo native-exit reads all
  `algo_not_triggered_retry` (no natural trigger yet). No repeat design
  approval needed for delivered scope; future design changes (learning wiring,
  automatic worker integration) still need review. M8.1 partial, **12/55**.

- **Latest M3.1 snapshot (unrelated to this slice), 01:07:05.242 UTC:** 33
  forecasts, 16 mature (15 `exact_source_version_missing_retry`, 1
  `exact_outcome_missing_retry`), 17 immature. **17 raw missing-version
  retries** total — all 16 original (15 resolved + TAO) plus BZ/USDT, whose
  own maturity is 04:00 UTC; this raw check does not establish whether
  independent retained receipts can resolve it. Cohort 15 unresolved/2
  selected/13 ignored/1 dependence group/0 terminal; index/export 30/30 and
  15/15, 14 natural scans each; TAO absent from both. Supersedes the 00:12:59
  UTC counts in the historical bullets below. See
  [review](../artifacts/pit-dataset/2026-09-17-exit-provenance-review.json),
  [capture](../artifacts/pit-dataset/2026-09-17-exit-provenance-capture.json),
  [maturity](../artifacts/pit-dataset/2026-09-17-exit-provenance-maturity.json).

Everything below this point is an earlier session's historical snapshot,
superseded by the three bullets above.

- **Latest build September 17 00:22 UTC:** [conversion evidence](../reports/2026-09-17-conversion-evidence-delivery.md).
  Owner approved actual-conversion receipts. Manual immutable capture/replay added,
  **95 tests passed**, five frozen accounting artifacts replayed. Provider contract
  lacks originating cashflow/account linkage and complete-cost proof; observations
  cannot grant conversion accounting or learning eligibility. No scalar net accepted.
  Next: obtain authoritative linkage before an accepting adapter; meanwhile natural
  booking/receipt/import observation continues. Do not re-request this design approval
  or invent a valuation policy. ACTIVE/demo, all three positions matched/protected,
  unchanged kernel; zero natural accounting receipts. No worker/import/risk changes.
  M8.1 partial, **12/55**. Other dependency-ready design changes need owner review.

- **Latest continuation September 17 00:13 UTC:** [natural-evidence review](../reports/2026-09-17-natural-evidence-continuation.md).
  Natural accounting remains empty (zero jobs/attempts/receipts); next exact work
  remains natural booking export/replay, complete receipt and explicit forward-only
  import. ACTIVE/demo with three verified native-protected positions, PID unchanged.
  New population capture replays: 15 unresolved rows, indices match 18/18 and 3/3;
  TAO gap retained. No complete coverage acceptance. Exact verifier now preserves
  missing-source retries without aborting: **43 tests passed**. 33 forecasts,
  15 mature terminal records lacking exact sources, one mature missing outcome,
  17 immature; all 16 original baseline retries retained. Investigation degraded
  by explicit memory-source refusals, collection continues. Next maturity 04:00,
  08:00 and 16:00 UTC; full-window cut September 18 00:00 UTC. No design change;
  ask owner before one. **12/55**, no candidate/performance claim or research restart.

- **Latest implementation (September 17):** [M8.1 accounting worker](../reports/2026-09-17-accounting-worker-delivery.md),
  **247 final-source tests passed**. Separate bounded queue, immutable captures,
  retry backoff and crash recovery reuse the delivered reconciler. Watchdog opt-in
  enabled after existing population consumers; no kernel restart or auto import.
- **Next M8.1 work (updated by the 01:41 UTC bullet above):** verify natural
  booking export/replay, scheduled attempts, a naturally complete receipt and
  explicit forward-only memory import. Do not redo queue/reconciler engineering.
  Emergency/native exit provenance manual capture/replay is now delivered
  (owner-approved design, no repeat request); its remaining work is observing
  natural evidence with the delivered CLIs, not further engineering — folding
  either into the automatic worker or into whole-trade memory is separate,
  future-reviewed scope. Conversion attribution remains blocked on provider
  evidence; legacy consumer migration remains separately reviewed. M8.1
  partial; **12/55**.
- **Runtime September 16 23:56 UTC:** ACTIVE/demo, same PID 300976, all three
  positions matched and protected; zero natural receipts, no forced trades.
  Frozen hashes and collection preserved; referee/handoff false.

- **Scheduled opening review September 17 00:01 UTC:** capture/replay passed;
  both indices match two exports each, one natural scan per stream. 16 rows,
  15 declared eligible; TAO/USDT missing, zero new registrations/cohort terminals.
  No complete sampling claim. Accounting worker naturally ran at 00:00:03, empty.
  Exact verifier: 16 mature with missing outcomes and baseline-version retries;
  two immature until 04:00 UTC. Preserve retries; full review due September 18
  00:00 UTC. See [opening review](../reports/2026-09-17-accounting-worker-delivery.md#scheduled-opening-and-maturity-review--september-17-0001-utc).

### Previous session evidence (historical snapshots)

- **Latest implementation:** [M8.1 whole-trade reconciliation](../reports/2026-09-16-whole-trade-accounting-delivery.md),
  **232 final-source tests passed**. Manual demo capture joins exact entry/reduction/
  exit orders, quantities and whole-symbol histories; signed funding requires
  matching published events and publication past final fill. Separate fee/funding
  currencies; missing conversion stays unknown. Complete-only typed replay/import
  bridge retains raw evidence and forward activation. M8.1 partial, **12/55**.
- **Deployment/runtime:** prior emergency/normal slices remain demo-deployed from
  September 16 22:09 UTC, kernel PID 300976. New manual CLI needs no restart;
  no automatic capture/import configured. At 22:35 UTC, ACTIVE/demo, three matched/
  protected positions, owner login and natural population hooks healthy, watchdog
  enabled, no pending recovery. Zero natural accounting receipts; no forced trades.
  Read-only history endpoints smoke-tested at 22:41 UTC, not trade attribution.
- **Next dependency-ready work:** observe/export/replay natural bookings; retain
  incomplete attempts and retry whole-trade capture after missing evidence/funding
  publication. Verify a natural complete receipt and memory import. Engineer a
  bounded off-path automatic retry service, proven non-USDT conversions and
  emergency/native/ambiguous exit coverage. Legacy P&L consumers reviewed but not
  migrated; reporting/rent and decision consumers remain separate work.
- **Actual UTC:** final read-only check September 16 22:51 UTC was still before September 17 opening;
  in-window population and mature exact-outcome checks were premature. Full-window
  review remains due at/after September 18 00:00 UTC across both indices and all
  classification-neutral terminals. Keep collection and missing_versions_retry;
  no coverage acceptance, M3.2 search, backfill or Gate 2 restart. Referee/handoff false.

- **Latest review (September 16 21:40 UTC):** [pre-window coverage review](../reports/2026-09-16-pit-coverage-prewindow-review.md).
  New immutable capture replay passed; both indices match one activation export
  each, with zero missing/pending exports and zero cohort rows. Window remains
  `not_started`: complete-window review is premature, no coverage acceptance.
  Natural consumers healthy at 21:40:03 UTC; ACTIVE/demo, three journal trades,
  authenticated API/login/cookie access verified on the unchanged binding.
  Exact verification: 18 immature forecasts, 16 original missing-version retries,
  five investigations. No source change; prior test evidence reused. The next
  exact item below is unchanged. Earlier snapshots below remain historical evidence.

- **Last completed work:** M3.1 classification-neutral cohort, coverage reconciliation
  and V2 capture/source-free replay implemented; **277 final-source tests passed**
  across 16 relevant modules, including eight new cohort tests. Earlier 268/269-test
  evidence retained. M3.1 remains partial, acceptance **12/55**.
- **Next exact item:** verify naturally scheduled in-window receipts after September
  17 00:00 UTC, capture/replay to a new file, then review the complete window at/after
  September 18 00:00 UTC. Reconcile eligibility/selection/registration reasons,
  missing symbols/receipts, pending exports, orphan results, unresolved/expired and
  classification-neutral terminals. No M3.2 before coverage review and a separate
  search protocol; no Gate 2. Do not backfill gaps or repeat completed engineering.
- **Actual-clock evidence:** September 16 **21:30:54 UTC**, window not started; each
  stream has one indexed activation receipt matching its export, no pending/missing
  exports, zero registrations/cohort rows. Zero pre-window rows prove no coverage.
  Both consumers ran naturally at **21:30:01 UTC**, healthy with population enabled.
- **Declaration unchanged:** freeze September 16 **20:38:40.140 UTC**, 16 symbols;
  registration window September 17 00:00 to September 18 00:00 UTC exclusive.
  Preserve export directory and both local indices. Collection enabled does not
  establish complete sampling. V1 frozen artifacts still replay.
- **Capture:** `./venv/bin/python -m scripts.capture_pit_population --config
  data/pit_population.json --output /tmp/NEW-UTC.json`. CLI is not scheduled;
  five-minute watchdog consumers export receipts. Inspect the new `cohort` view.
- **M2.1 background:** **21:30:54.842 UTC September 16**, 18 forecasts, zero mature;
  each original still lacks one exact baseline version, two newer receipts remain
  independent. Original forecast close September 17 00:00 UTC, newer 04:00 UTC;
  original BR/FIL investigation windows 16:00 UTC. Rerun exact verifier with a new
  filename when mature; retain missing_versions_retry. Five investigations retained.
- **Runtime/access:** ACTIVE, demo true, three open journal trades, healthy attention
  and scheduled consumers, referee/handoff false. No service restart/control change.
  Actual dashboard bind **192.168.126.131:8080** supersedes old loopback snapshot;
  unauthenticated API 401, authenticated API/dashboard and browser login 200.
  Preserve private-token login. SSH target must use the actual bound address.
- **Tools:** Claude probe session-limited; authorized direct implementation used.
- **Limits:** engineering and pre-window observation only. No candidate, admission,
  changed trading decision, population rate or performance claim. Accounting remains
  M8-dependent; absent strategy-definition versions stay unknown. Frozen research,
  doctrine, BR/FIL registrations and unrelated workspace changes preserved.
- **Latest evidence:** [cohort delivery](../reports/2026-09-16-pit-cohort-delivery.md).
  [Population producer](../reports/2026-09-16-pit-population-delivery.md) and
  [PIT builder](../reports/2026-09-16-pit-dataset-delivery.md) evidence remains valid.
