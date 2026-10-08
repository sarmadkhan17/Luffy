# [SOL-1][EXE-04] BLOCKED — approved exit-plan authority audit

This is a baseline audit and reproducible failure proof, not an engineering
closure. No execution implementation or new authority was added. The task's
instruction to report missing registered invalidation/safety contracts rather
than invent them prevents treating unsupported capabilities as proven positives.

## Baseline / isolation

- Baseline: `1ec9870a09868d108293b10c3417ffd14368ed2e`.
- Branch: `sol1/exe04-exit-authority`.
- Worktree: `/mnt/luffy-data/luffy/workspaces/sol1-exe04-exit-authority`.
- DEC-03 engineering commit is the baseline itself.
- STR-04 implementation `872131f3755ab2d156e9cce73f0deae3b7630df2` and
  owner-binding fix `9814fddce8f655ce73bd79050b536e6668dba55d` are ancestors.
  Canonical STR-04 reconciliation `37d210787c448053c563c92436d9ac17ab672e12`
  is the baseline's parent.
- STR-03 exact approval implementation
  `2b119a318bdca34599eb8620c9f1f1cc62a78f7e` and its risk-scope proof
  `e8ad27481172f0663b9ffada4ffe3d1d365f9568` are ancestors.
- DEC-02 comparison implementation
  `9f36fc7384de6ecd53b1b3f85e45f8a8c6508a81` and R3 tie fix
  `f1bbd7a74600ce2f8355babb337854bc6693bc35` are ancestors.
- Production canonical commit `2b5d250c418aef3268eb53dc7d1efbd081efc44f`
  is a separate documentation reconciliation, not substituted for engineering.

## Actual authority path

1. `factory_handoff.create_version/load_version` binds immutable StrategySpec,
   version, spec hash, research lineage and requirements. The evaluated
   research record/validation receipt binds `EXIT_SEMANTICS_ID` and the exact
   exit specification. `record_exact_install/verify_install` binds version,
   spec, validation receipt and install identity.
2. The entry producer in `kernel.py::_try_enter` reads that exact installed
   version, uses `versioned_exits.entry_contract` to obtain the closed signal
   bar's close and ATR on the declared timeframe, and calls
   `exit_policy.initialize`. It freezes stop, target, original R, side,
   reference, ATR, timeframe step and max bars in `entry_identity.exit_state`.
3. `RiskManager.authorize_entry` and `entry_authority` provide the existing
   final entry permission. `Executor` attaches the original `execution_binding`
   before booking. `trade_provenance` makes `trades.entry_identity_json`
   immutable. This is the original entry/action lineage, not a new exit plan.
4. DEC-03 `portfolio.current` captures account-position bindings;
   `position_evidence._bindings` verifies version/spec/install/semantics and
   re-derives the initial policy from the exact frozen spec. `build` joins the
   reconciled journal episode to independent venue holdings. Its account slot
   identity hashes account scope, environment, instrument and market type;
   `booked_position_id` is the trade episode. The receipt includes
   `entry_identity_sha256`, `exit_plan_sha256`, validation identity and initial
   `exit_state`. `runtime-portfolio-checkpoint.v2` retains and replays it.
5. Normal `ExitEngine.manage` refreshes the journal trade. For a versioned
   position it routes to `LiveExitAdapter`, which verifies exact historical
   validation/install with `current=False`, advances closed bars using
   `exit_policy.advance`, persists `versioned_live_exit_states`, and calls
   `Executor.close`. That boundary currently accepts a reason/caller evidence
   without independently verifying exit-plan authority.

DEC-03 position evidence is an observation-only read model. It is not currently
consumed/verified as an execution authorization by the live adapter, stop mover,
partial reservation or final close boundary. No second position registry is
needed or created by this audit.

## Authoritative objects and precise gaps

| Concern | Existing object / producer | Result |
|---|---|---|
| Initial stop / targets | `ExitSpec.stop/target`, validation/install receipts, `Policy.stop/target`, immutable entry JSON | Supported exact geometry exists; live consumer lacks DEC-03's policy re-derivation and final independent verification |
| Trailing | `ExitSpec.trail`; registered semantics permit only `kind=none` | ATR trail/arming updates are explicitly unsupported; no admitted versioned update contract |
| Partial exits | Legacy `ExitConfig.tp1_fraction/tp1_r_mult`; durable `partial_exit_intents` | Versioned semantics require full remaining quantity; no attached partial fraction/schedule/action producer |
| Invalidation | `StrategySpec.invalidation` prose; optional `ExitSpec.signal_exit` | Prose is length-validated, not an executable registered condition; versioned signal exits explicitly refused |
| Scale-in | Existing entry Risk rejects `already exposed here` | No ExitSpec allowance, limit, geometry, count or scale-in producer; no positive authorized scale-in path |
| Plan/version/hash | Immutable entry JSON plus DEC-03 hashes | Live adapter does not consume those receipt hashes or account-position ID |
| Side/size/action | Booked trade plus original execution binding | Live state has trade ID but no verified DEC-03 slot/episode/action join at the final exit boundary |
| Safety | Owner grants/typed request, panic hold and pending request/event ID | Explicit containment exists; it is not a plan-replacement or geometry-rewrite capability |

Legacy normal exits use mutable per-strategy `spec_exits`, global TP1 parameters,
trailing defaults and score-flip exits. Their entry attribution remains UNKNOWN;
these paths cannot prove that **all** live geometry follows an exact approved
StrategyVersion or explicit safety receipt.

## Reproducible live-boundary counterexamples

`tests/test_exe04_authority_audit.py` deliberately asserts observed baseline
acceptance in tests named `counterexample`. Their passing result is failure
evidence, not a claim that EXE-04 requirements passed.

- Substitute stop, target or max bars in caller-supplied initial `exit_state`,
  including a confidence value: DEC-03's existing verifier refuses
  `POSITION_EXIT_PLAN_DIFFERS`. `LiveExitAdapter` emits intent using the changed
  policy. The real journal identity remains unchanged. Comparing initial JSON
  with mutable state does not prove agreement with the registered spec.
- Supply a fully consistent second registered/installed version binding for an
  A-booked trade: direct `LiveExitAdapter.manage` accepts B, while the immutable
  journal still identifies A. Normal `ExitEngine.manage` refreshes the trade
  first, but the adapter and final execution boundary do not enforce that
  invariant themselves. Single-field mismatches do refuse; that narrower proof
  does not establish complete wrong-version refusal.
- Corrupt persisted state to a terminal `target` while preserving policy and
  version/install/spec fields: adapter emits target exit without observations.
  Initial policy equality alone cannot establish historical state-transition
  provenance. No durable live observation replay proof equivalent to the paper
  exit evidence is verified here.
- Reserve a partial quantity of 3 for a versioned trade whose registered plan
  specifies full remaining quantity: `partial_intent.reserve` accepts it with
  no execution binding/partial-plan action. This local reservation sends no
  order. Its retry still reuses one TP1 identity. EXE-03 idempotency is not a
  partial-plan approval.

## Safety and lifecycle

Existing owner authorization is `owner.authz` plus typed `owner.contract`
operations and `OwnerService.execute`. `_panic` applies FROZEN (or retains
HALTED), records principal/operation, and queues exact request/intent-event
identity. The existing consumer drains it into `reconcile.flatten_all`, which
sends reduce-only market closes independently of strategy plan availability.
This audit only reads that consumer; it does not run Kernel or submit orders.
The entry control fence blocks exposure in FROZEN/RECOVERY/HALTED.

Reconciliation can restore a missing stop at the existing journal stop level
and actual observed remaining size; it refuses to invent a missing stop level.
This is a mapped recovery behavior, not proof of a registered capability to
rewrite stale/unavailable strategy geometry. `_move_stop` has no explicit
plan/safety receipt gate. Native protection verification is EXE-05 and was not
implemented or exercised here.

Tests show a newer derived version and caller confidence do not substitute A's
plan in the ordinary adapter path. Governed DEGRADED, RETIRED and PAUSED
transitions preserve historical A validation/install and policy resolution.
PAUSED is exercised through the permitted SHADOW -> DEGRADED -> PAUSED path;
the fixture does not manufacture owner approval or live activation.

## Required matrix assessment

| IDs | Evidence / unmet requirement |
|---|---|
| 1 | Exact frozen stop emits strategy intent; restart repeats identical pending intent |
| 2–3 | Single-field wrong binding and unvalidated derived version refuse; complete registered B substitution is a counterexample at direct adapter boundary |
| 4 | Ordinary confidence does not mutate A; altered initial stop plus confidence is accepted by live adapter and refused by DEC-03 |
| 5–6 | `trail=none` is exact supported behavior; ATR activation/update positive cannot be proven; substituted/extra trailing fields refuse |
| 7 | No registered versioned partial contract; arbitrary local reservation counterexample |
| 8 | Existing EXE-03 duplicate/redelivery/restart reduction tests retained and run |
| 9 | Partial reservation requires no partial-plan evidence; no confidence-generated partial refusal proof at that boundary |
| 10 | BLOCKER: no independently registered position invalidation producer/evidence |
| 11 | No DEC-04 authority borrowed; registered versioned signal exit refuses; no final close gate proves rejection of an unverified invalidation reason |
| 12 | BLOCKER: no permitted exact-plan scale-in contract or positive path |
| 13–15 | Normal Risk rejects already-held symbol; per-plan confidence/version/allowance checks cannot be proven without the absent contract |
| 16–17 | New version/confidence and governed lifecycle tests preserve A's historical plan |
| 18–19 | Owner panic and entry fence mapped; no new safety authority or exposure granted; full end-to-end containment execution not exercised under No Kernel/No orders constraints |
| 20 | Exact frozen stop pending intent identical on adapter restart; DEC-03 receipt replay tests pass; corrupt live state remains a counterexample |
| 21 | DEC-03 refuses corrupt initial plan; live adapter accepts it: requirement fails |
| 22 | Missing `exit_state` or unbound identity refuses versioned adapter |
| 23 | Legacy DEC-03 attribution remains UNKNOWN; no retrospective backfill |
| 24 | Existing DEC-03 account/instrument/episode identity tests pass; execution consumption is still absent |
| 25 | EXE-03 regression suite passes; single-TP1 identity/scope preserved |

No future/current strategy fallback was added. Existing historical evidence
remains unchanged. A full original-cut live state replay proof is outstanding,
as is exact account/episode/action verification at geometry-changing boundaries.

## Validation and boundaries

Only `/home/sarmad/.local/bin/luffy-pytest` runs tests; the wrapper allocates its
unique basetemp under `/mnt/luffy-data/test-tmp/sarmad`.

- Initial audit + EXE-03 + DEC-03: **46 passed**, 39.23s.
- Final counterexample/audit file: **20 passed**, 17.24s.
- Broader audit + EXE-03 / DEC-03 / STR-04 run: **123 passed**, 86.00s;
  19 audit cases and 104 existing regression cases. The final 20-case audit
  separately covers the added complete-B-substitution case: 124 distinct cases
  verified across the two overlapping runs. Commands/results are recorded in
  `exe04-authority-evidence/validation.txt` and retained JUnit XML.

No runtime source edits. No tracker, STATE, NEXT, manifest, production checkout,
PORT-01, GOV-07 or DEC-04 changes. The scoped Graphify query was read for mapping;
its generated local query-stamp change was restored. No code update or graph
update was required. No provider/venue APIs, actual orders, Kernel, Dashboard
restart, trading activation or EXE-05 work. All new live-adapter tests use an
intent recorder; local partial tests reserve only. Existing regression fixtures
use temporary journals and in-process venue doubles.

EXE-04 grants no venue/order, Portfolio, Risk bypass, owner, Governor, capital,
strategy replacement or plan-mutation authority. There is no cross-lane edit
overlap. A future implementation must consume existing DEC-03 identity and
existing plan evidence, and cannot silently introduce the missing positive
contracts or a general safety override.

**EXE-05 now eligible: NO. Ready for EXE-04 canonical reconciliation: NO.**
