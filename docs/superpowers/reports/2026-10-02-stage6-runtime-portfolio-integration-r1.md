# LUFFY-STAGE6-RUNTIME-PORTFOLIO-INTEGRATION-R1

PASS — uncommitted review package in `/mnt/luffy-data/luffy/workspaces/stage6-runtime-portfolio-integration-r1`, branch `luffy-stage6-runtime-portfolio-integration-r1`. Base is the latest committed/pushed Stage5 authority commit `1bed3958740ec2d0d575aabc817978086d31d390`. No commit, deployment, service restart, strategy activation, protection/control mutation or order submission was performed.

## Runtime boundary

The normal Kernel cycle now ends with a proposal-only checkpoint. The reader freezes actual venue holdings, current Opportunity Contexts, exact candidates, economics/capacity receipts, lineage/common-factor context and Risk/config references. Snapshot/control/version inventory changes during the bounded read refuse the cut. The existing candidate bridge and allocator enforce exact context/portfolio and receipt bindings. Sources retain content hashes and timestamps; unrelated cuts cannot supply candidates or intents.

`runtime_metrics` projects independent metrics from the verified venue snapshot, current account evidence and a complete instrument/side/quantity-reconciled journal book. Risk retains its recorded entry-price basis, including its rounded entry notional; it does not replace it with venue floating-point price serialization. Heat and entry-basis margin use `RiskManager.portfolio_metrics`, factored directly from the existing entry formulas. `check_entry` still uses those formulas and existing config limits. The optional frozen clock changes no normal caller behavior and makes Risk replay deterministic across UTC day changes.

Actual initial-margin utilization is distinct from Risk's entry-basis margin estimate. Available balance does not stand in for initial margin. Marked exposure requires the actual retained mark-price field. Optional initial margin and mark price are preserved from responses already fetched by the existing runtime; no additional venue request is introduced. Missing metrics stay UNAVAILABLE, and known per-instrument components survive another instrument's missing mark. Existing owner bounds are applied independently; no concentration policy is created. Exact lineage dedup stays authoritative, correlation stays context-only, and all new materiality/calibration registries remain empty.

The runtime Consumer uses the existing event gate for exact Opportunity Context/candidate, position open/close, Risk/control state and exact Governor lifecycle changes. Account/capital, quantity and other material changes without calibrated authority produce `MATERIALITY_POLICY_UNAVAILABLE` and cannot trigger. One separate SQLite transaction stores the previous frozen cut, event receipt, candidate-set hash, event checkpoints and resulting proposal. Checkpoints bind event/cut/snapshot IDs, trigger reason, previous/resulting proposal IDs and processed time. Duplicate events are suppressed and restart resumes from the persisted prior cut; failures roll back the transaction.

AllocationProposal → typed TradeIntent → existing `RiskManager.check_entry` → immutable typed RiskDecision stops at Risk. Each decision binds intent/proposal/portfolio, exact strategy/version/spec, instrument/direction, requested and permitted sizes, current effective Risk policy, source hashes, account evidence and as-of. Frozen Risk state is evaluated in a private in-memory journal; recorded Risk baseline/breaker state is retained. Rejection reasons are preserved. Missing size, current evidence or exact entry geometry refuses or remains INCOMPLETE. Risk approval has `execution_authority=false` and cannot call Executor, submit a venue order or modify trading control/protection.

## Prerequisite provenance and scope

The committed Stage5 tree did not contain the detached Stage6 contracts named by this request. Required portfolio, Opportunity Context, world/observation and test support was brought from the existing Stage6 implementation-closure worktree at `d256bfb`. These are prerequisites, not a replacement economics model. `package-files.json` lists all review files and hashes. Existing observational modules were reconciled to those contracts; the Store retains Stage5 persistence/schema and only adds opt-in world evaluation and replay source preservation. One existing replay fixture now retains its optional positioning/correlation inputs. No frontend or knowledge-vault change is included. Production's unrelated dirty files were left untouched. Graphify output and runtime shadow/ledger artifacts are outside the implementation manifest.

Local Claude implementation assistance was unavailable (`Not logged in`); implementation and verification continued locally under the owner's explicit package request.

## Validation

- Broad relevant regression: **611 passed**, covering portfolio/economics/common factor/opportunity bridge, Risk/baseline, Stage5 authority, entry fences/recovery, current truth and attention/investigation.
- Final focused regression after the entry-price-basis correction: **329 passed**, including **19 package acceptance tests** and common factor, whole-book cut, Risk/baseline and Stage6 closure tests.
- Current-truth corrections tested separately: **101 passed**. This keeps its collection-time future-news fixture within its intended clock window.
- Positive TEST-ONLY exact evidence produces a complete OPEN intent and existing Risk approval while an Executor sentinel proves the boundary; real evidence is never synthesized to obtain approval. Missing size/geometry, preserved Risk rejections, uncalibrated events, duplicate event/restart/new-process replay and atomic rollback are covered.
- YAML parsing, `git diff --check`, AST capability audit and code-only `graphify update .` passed. No files staged and no new commit.

Evidence: `docs/superpowers/evidence/stage6-runtime-portfolio-integration-r1/`. Earlier broad/development shadow diagnostics are retained there; schema-changing development receipts correctly fail closed on replay. The final verified shadow uses a fresh ledger under `verified-real-shadow/`; `verified-shadow-summary.json` is its compact authoritative summary. Earlier test diagnostics include an existing fixture omission subsequently corrected and a collection-time future-news fixture that passes when run separately.

## Actual bounded shadow

PASS, replay PASS. The read-only reader observed **3 holdings**, **FROZEN**, **0 exact StrategyVersions** and no fabricated candidates. Result: **CASH / NO_ALLOCATION**, **0 TradeIntents**, **0 Risk approvals**, **0 authenticated venue requests**, **0 order submissions**, **0 production mutations**. Writes were confined to the isolated shadow output/ledger.

The final cut establishes current account equity, position count, portfolio heat, per-symbol Risk exposure and Risk entry-basis position/total margin. Observed portfolio heat is approximately **3.229814%** and entry-basis total margin **6.635036%**. Actual initial-margin utilization and marked gross/per-instrument exposure remain UNAVAILABLE because the running retained source records lack those fields. The new capture path supports them when the existing source response provides them; this package was not deployed to obtain new fields.

## Control plane and next package

STATE records `RUNTIME_PORTFOLIO_METRICS=TESTED`, `EVENT_DRIVEN_REOPTIMIZATION_RUNTIME=TESTED`, `TRADEINTENT_TO_RISK=TESTED`. Real allocation is **NOT_ACTIVE**. NEXT recommends **STAGE6_RUNTIME_CLOSURE_R1**. No remaining implementation gap was identified in the requested Stage6 runtime portfolio boundary. Separate real readiness still requires exact versions/opportunities, complete authoritative economics/capacity and current required source fields. Uncalibrated materiality remains intentionally disabled; no policy or threshold was invented. Closure is a review recommendation, with no trading or deployment authorization. Prior research stops and frozen artifacts remain preserved.
