# LUFFY-STAGE5-UNIVERSAL-STRATEGY-AUTHORITY-R1

Authority boundary implemented and tested; no new real execution authority.
No commit, deployment, restart, venue requests/orders, Risk limit changes or
research/referee/handoff activation. Existing dirty work was preserved. Claude
execution was checked and returned `Not logged in`; implementation continued
locally under this session's explicit package instruction.

## Authority and activation

`_mechanism_once → SpecWriter → Analyst.admit → _install_spec(version_id=None)`
remains an explicit research/paper-only proposal/install path. Admission and
nominal population state never confer real execution permission. Kernel and
Executor both enforce `live_entry_block`; an unversioned strategy without an
exact grandfather receipt refuses `VERSIONED_AUTHORITY_REQUIRED`. The original
bypass is reproduced with a valid BUY and ACTIVE global control against a mock
venue: zero submissions, even after direct nominal `state='active'` mutation.

The sole Factory creation path remains candidate/spec → registered referee
validation → immutable StrategyVersion → exact paper installation → paper and
net-cost probation → exact owner decision → eligibility → Governor activation.
An unversioned proposal cannot overwrite an existing Factory identity through
the Kernel install path. No change relaxes Factory research or economic gates.

Governor activation/reactivation requires an exact reverified immutable version,
validation receipt, installed definition/exit binding, replayed probation with
complete economic cost evidence, exact owner approval, currently established
capacity, required inputs, legal lifecycle state and owner allocation boundary.
It also requires explicitly persisted ACTIVE global control and a current
RiskManager-issued authoritative RiskRelease, matching configured Risk policy.
The shared control fence and Risk's IMMEDIATE transaction hold those prerequisites
through evidence checks and the lifecycle append. Capacity checks use actual
current time; stale caller audit clocks are refused. No control or Risk policy
is changed. Risk retains the separate final entry/sizing authority.

Activation records lifecycle/allocation authority only. The separate Factory
real-entry fence remains closed, including for ACTIVE/REACTIVATED. No automatic
activation caller exists. Full TEST-ONLY evidence produces APPROVED_FIRST_LIVE →
ACTIVE and still zero mock-venue submissions. Missing/changed version, validation,
install, probation, economic evidence, approval, capacity, inputs, lifecycle,
control, Risk proof or clock refuses activation. Pause, degrade, reactivation and
retirement run through the same Governor. Preapproval retirement also uses it.

Database triggers reject direct Governor-event insertion, direct active lifecycle
insertion, versioned strategy state mutation/replacement/deletion, and versioned
nonpaper insertion outside Governor. Journal connections expose a scoped write
capability used only around the verified Governor append/projection. Reopening
an existing Factory journal installs the same guards. Kernel retirement no longer
writes the versioned projection independently; Governor writes retirement in its
transaction. Unversioned paper retirement grants no execution authority.

Historical exceptions require an immutable exact-definition owner receipt in
`strategy_legacy_authorities`, with an explicit authority claim and issuance
before the fixed Factory boundary (2026-09-30 UTC). No absence-of-version or
creation-date heuristic grants an exception. There is no runtime issuer: insert,
update and delete are blocked. This package creates zero such receipts. Tests
simulate preexisting receipts only in temporary journals; changed definitions
invalidate them. Existing positions' exits/protection remain independent.

## Entry and lifecycle path audit

Search covered Python source under trader/ and scripts/, including inserts,
upserts, installs, state writes, deployment/eligibility flags and enum/state
assignments. `is_trade_eligible` in population adapters remains a signal-evaluation
flag needed by paper/research; it does not substitute for Kernel/Executor real
authority. Dashboard/API/chat have no strategy install or first-live activation
writer. Research/LLM producers only propose; no alternate real grant issuer was
found. Prior defects below are closed, not residual BYPASS paths.

| Path | Classification | Boundary |
| --- | --- | --- |
| Kernel._research_handoff → create_version → _install_version | GOVERNED | Registered referee, immutable exact version, paper installation |
| Kernel._mechanism_once → SpecWriter → analyst.admit → _install_spec(None) | LEGACY_EXPLICIT | Explicit research/paper only; real authority refused |
| Journal.upsert_spec / upsert_strategy | GOVERNED | Storage cannot grant execution; versioned lifecycle writes guarded |
| Kernel._load_population / _load_spec_population → orchestrator | GOVERNED | Signal proposal only; Kernel and Executor independently fence real entry |
| Kernel._try_enter / Executor.open | GOVERNED | Explicit version/grandfather authority; Factory real-first-live remains disabled |
| factory_handoff.record_owner_decision / eligible_for_first_live | GOVERNED | Exact bound eligibility only; no activation caller |
| factory_handoff.govern_version / retire_version | GOVERNED | Sole exact-version lifecycle authority with held activation prerequisites |
| Kernel mechanism decay / exact-install refusal retirement | GOVERNED | Versioned retirement and projection through Governor |
| strategy.promotion.evaluate_population | LEGACY_EXPLICIT | Specs excluded; genome upward transition requires explicit grandfather authority |
| brain.strategist._apply | LEGACY_EXPLICIT | Existing legacy keep/retire only; no creation or upward grant; versioned SQL guarded |
| brain.tv.validate_population | LEGACY_EXPLICIT | Specs excluded from duplicate retirement; legacy advisory/bookkeeping only |
| scripts/repair_proxy_demotions.py | LEGACY_EXPLICIT | Versioned rows refused; paper repair confers no real authority |
| strategy.library / genome / seed_specs | LEGACY_EXPLICIT | In-memory seed/proposal construction; no installation or real grant |
| research runner / scraper / crawler / SpecWriter / Analyst / dashboard / chat | GOVERNED | Proposal/evidence/control surfaces; no independent strategy real grant |
| legacy_authority.grandfathered | LEGACY_EXPLICIT | Read-only exact immutable preexisting receipt; no issuer/migration grants |

BYPASSES_REMAINING: NONE within the audited repository entry/lifecycle surfaces.
Database guards protect application writers, not an administrator deliberately
dropping SQLite constraints. No adversarial administrator capability is claimed.

## Current evidence and validation

[Current inventory](../evidence/stage5-universal-strategy-authority-r1/current-inventory.json)
was read through SQLite `mode=ro`. It shows FROZEN, zero StrategyVersions,
validation/probation/approval/install/Governor/capacity records and zero grandfather
receipts. Governor was probed only against a disposable SQLite backup; no exact
version exists, so activation refused `version_missing` and appended zero events.
Current real evidence remains incomplete; CURRENT_FIRST_LIVE_READY: NO.

Targeted checks: 100 focused tests, 411 control/Risk/economics/allocator
regressions and 109 exit/accounting tests passed (620 total). The final
Kernel install-return check also passed 33 creation/authority tests. STATE/NEXT YAML
parse and git diff whitespace checks passed. Graphify AST-only update completed
without semantic extraction/API cost. Test artifacts in the linked evidence
directory cover the original bypass,
complete synthetic activation, individual prerequisite refusals, lifecycle ownership,
legacy immutable exceptions, mutation/SQL bypasses, cost/provenance, control/Risk,
allocator/economics and exits/accounting. Synthetic cost and missing capacity
sources are explicitly TEST-ONLY; no real economic readiness is inferred.
Existing execution/provenance tests now declare exact synthetic historical
exceptions instead of assuming every strategy ID has authority. The older
single-creation fixture now supplies registered TEST-ONLY referee evidence and a
supported fixed exit; the size-aware exit test double accepts the existing
accounting argument. No trading implementation was changed for these fixtures.

STATE.yaml and NEXT.yaml updated. Next recommended package:
LUFFY-STAGE6-RUNTIME-PORTFOLIO-INTEGRATION-R1. No first-live permission is implied.
