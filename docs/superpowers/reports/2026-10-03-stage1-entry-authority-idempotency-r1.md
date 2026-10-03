# LUFFY-STAGE1-ENTRY-AUTHORITY-IDEMPOTENCY-R1

Result: **PASS for this bounded implementation package**. Stage1 build completion remains **BLOCKED** by MI-2, MI-3, MI-6 and MI-7. This is pre-start implementation/test evidence. It does not authorize startup, deployment, trading or venue observation.

Branch: `luffy-stage1-entry-authority-idempotency-r1`. Implementation base HEAD: `f571c988b866fdeaa27957d2d950625aa292ebd9`. Package finalized for the commit recorded in git history. Real order submissions: **0**. Trading authority was not expanded or activated. No Risk limits, first-live approvals, StrategySpec authority, Stage5–8 responsibility or LLM order authority changed.

## Architecture and evidence

The final new-exposure submission now lives in `trader/engine/entry_authority.py`. The only normal entry caller remains `Kernel._try_enter → Executor.open`. A caller must present a current permission issued by the actual deterministic RiskManager for the exact proposal. `ACTIVE` alone, an upstream sizing result, a copied permission object, or a raw symbol does not authorize an order. Private Executor open helpers also terminate at this boundary.

The existing immutable RegistrySnapshot is the single instrument authority. `register_capability` records its exact snapshot/record/account identity, constraints, loaded venue-market hash and replayable leverage observation in the existing Journal. It does not create another registry or fetch venue metadata. `observe_leverage` projects already observed symbolConfig and leverageBracket records, retains those records, and hashes their exact account/instrument/snapshot/source/time binding. Admission reconstructs this projection and checks the bracket covering the proposed notional. Desired leverage is never treated as verified leverage, and the former best-effort leverage setter was removed. Unsupported exposure/margin models and missing eligibility, shortability, margin, leverage or freshness evidence refuse entry.

Capability provenance is captured on Snapshot before strategy evaluation, retained on Decision and frozen Portfolio sources, and included in bound immutable TradeIntent lineage. Final Risk and Execution compare the exact receipt with current trusted state. A symbol remains a venue-routing label; it cannot supply canonical identity. Account permission is never inferred from public metadata, account-global canTrade or a populated position list. The public registry producer continues to return UNKNOWN for facts it cannot prove. The production ingestion contracts accept real observed receipts later; fixture publication does not establish real account eligibility or leverage.

Risk permission binds logical action/decision/cycle, canonical instrument, direction, requested and allowed quantity, strategy/version/spec identity where applicable, Portfolio lineage, policy digest, account/margin/book receipts, capability receipt, RiskRelease, decision time and freshness basis. Execution verifies the same issued object and revalidates existing RiskRelease and current input receipts. It rejects invalid/nonfinite/underflow quantities, prices, ATR/stop geometry, equity, margin and held exposure inputs. It also checks venue quantity-step/min/max/notional constraints. Existing account freshness, registry age and RiskRelease freshness authorities are reused; no new live threshold was invented.

Account observations, margin receipts and venue-position snapshots preserve the opaque identity of the actual credential, endpoint environment and venue used for the read. Entry recomputes this identity from the executor's current context. A changed credential or receipt from another account refuses. The account producer refuses a configured read key that differs from the trading context and uses the existing observed venue host rather than guessing an endpoint. Raw credentials are not recorded in these receipts.

The existing Journal and EntryRecovery own execution persistence. `execution_requests` adds permanent logical/decision/client-order identity and archived recovery/results within that Journal; it is the durable history of the same recovery mechanism. Reservation and the active recovery record commit atomically **before** submission. The deterministic client-order ID is stable across retry/restart. States include RISK_AUTHORIZED, SUBMISSION_ATTEMPTED, ACKNOWLEDGED, PARTIAL, UNKNOWN_OUTCOME, REFUSED and TERMINAL, with order/fill/protection detail retained in recovery/booking records. Terminal results and pre-upgrade durable recovery history prevent a completed logical action from becoming a fresh order.

The existing control flock serializes the authoritative entry fence against containment. The permission also retains the existing owner-control intent watermark, so a later freeze/halt invalidates queued work even if ACTIVE subsequently returns. The existing Risk IMMEDIATE transaction revalidates and holds exact policy/account/capability/book state through the bounded venue submission. A separately committed reservation survives an acknowledgement write/commit failure. UNKNOWN_OUTCOME never becomes a fresh submission; recovery reads the original venue order identity and reconciles positions/protection. Unavailable or conflicting truth retains containment. Actual database contention and venue-call latency remain operational observations, not measured production guarantees.

Booking, accounting, reconciliation and recovery retain canonical instrument, account, capability and logical-action binding. Bound fills require exact venue identity. Recovery rejects conflicting raw venue-position identity before protection/adoption. Historical unbound records remain historical recorded truth; they do not silently become canonical verified exposure. Journal observations and acknowledgements are not claimed to be exchange-authoritative proof.

## Entry-path audit

The AST acceptance test enumerates all `create_order` callsites under `trader` and checks every engine call outside the final authority boundary is explicitly reduce-only. Search evidence is in `submission-callers.txt`.

| Path | Classification and fence |
| --- | --- |
| Kernel strategy/orchestrator entry | One Executor.open caller; obtains exact final deterministic Risk permission. |
| Portfolio/TradeIntent | Proposal to Risk only; canonical receipt and lineage retained. No direct venue call. |
| Executor.open and private open helpers | Same canonical/Risk/control/durable boundary; missing permission fails closed. |
| Executor exits, trailing reductions and emergency closes | Explicit reduce-only; cannot create new exposure. |
| EntryRecovery | Reconciliation/adoption, protection and reduce-only closure; never resubmits unknown entry. |
| Reconciliation and protective helpers | Explicit reduce-only orders; exact binding checked where available. |
| ObservedExchange | Passive transport/capture adapter around the same exchange object; its forwarding call is used by the fenced entry or reduction callsites, not an independent entry producer. |
| Owner UI/chat/research/learning | No direct entry submission caller. Owner controls change control intent, not exposure. Research/Portfolio Risk-intent helpers remain detached and confer no execution permission. |

First-live StrategyVersion gates remain checked at Executor admission and again at the final submission boundary. No owner/chat/research path was given entry authority. Direct access to a venue SDK outside repository entry producers is not represented as a new authorized LUFFY route.

## Test repairs and negative controls

The 12 audited boot/recovery/entry-boundary failures were investigated. Eleven entry probes were masked by missing TEST-only legacy Strategy authority, and one Supervisor fixture constructed an incomplete Kernel without its Journal. Fixtures now provide genuine isolated authority/context before exercising production containment/Risk paths. These changes do not approve any real StrategyVersion or owner first-live transition. Owner recovery still cannot produce an order from ACTIVE without exact Risk.

The stale Risk inventory referred to a retired learning checkpoint ACTIVE setter. The inventory now matches current production setters. The missed cross-connection Risk-lock mutant previously failed during construction of the second Journal, before reaching the intended compare-and-set assertion. Constructing that Journal before the contested transaction lets the test detect the missing lock for the intended reason.

The registry suite imported absent historical fixture modules and expected retired Kernel supplemental-registry hooks. Restoring the old fixtures exposed the obsolete hooks rather than a current production function. The suite now exercises the still-current production RegistryAttention, RegistryRefresher, persistence and frozen-source contracts directly. `registry_observation_fixtures.py` contains only current offline exchange/response/frame helpers. No test-only registry implementation or retired Kernel path was introduced to satisfy collection.

Four negative controls deliberately remove Risk-issued permission validation, identity validation, durable dedup retention and the current control fence. Each triggers its intended failed safety assertion. Dedup removal demonstrably submits the completed fake logical request again. These mutants run in isolated tests; production code retains all four fences. The existing Risk baseline mutation proofs also pass.

The broad relevant truth regression exposed a future-news fixture timestamp becoming historical during collection/execution. Its timestamp is now evaluated at assertion time; production news behavior was not changed.

## Verification

- `focused-tests.txt`: **854 passed in 147.75s**. Focused Risk, execution/recovery, control/owner/boot, booking/accounting/reconciliation, registry, Stage5 Strategy authority, Stage6 Portfolio, and relevant current-account truth regressions. The final recovery identity assertion was added after this run.
- `acceptance-tests.txt`: **116 passed in 60.87s** on the final entry/recovery/reconciliation code, including canonical alias-collision refusal and control/Risk-state persistence regressions, fresh-interpreter dedup, uncertain acknowledgement transaction, DB-lock failure, partial fill, stale/changed capability and account truth, unverified leverage, exact size/policy/control binding and the four mutants.
- `boundary-tests.txt`: **168 passed in 32.20s** during implementation; superseded by the final focused/acceptance results.
- `registry-collected-tests.txt`: **46 tests collected**; all are included in the passing focused run.
- `graphify-update.txt`: local code-only AST graph update; no semantic extraction/API cost.
- `verification.txt`: **PASS** for `git diff --check`, JSON/YAML parse, scoped control-plane consistency, unchanged HEAD and empty index.

All order behavior above is fake-venue evidence using temporary Journals and isolated configs. No live venue, production Journal, completed-system startup, deployment or service restart occurred.

## Gap classification and next package

Package-local MISSING_IMPLEMENTATION: **NONE**. Package-local PRE_START_TEST_BLOCKER: **NONE**. Closed current audit gaps: MI-1, MI-4, MI-5, PT-1, PT-2 and PT-3.

Remaining Stage1 MISSING_IMPLEMENTATION, each retained separately in STATE:

- MI-2: normal market-feed source/receipt/quality and historical revision provenance.
- MI-3: native HTF close/availability alignment and rejection of future cached observations.
- MI-6: integrity-checked critical-store/config/evidence backup/restore with an off-host seam.
- MI-7: critical storage/journal-unavailable and missing-heartbeat safety alert paths.

POLICY_NOT_CONFIGURED: no new package-local policy values or thresholds introduced. Existing lists remain truthful. NEEDS_REAL_RUNTIME_EVIDENCE: sustained completed-system recovery, latency/resource behavior, publication and alert delivery; operational off-host drill remains dependent on MI-6 implementation. NEEDS_REAL_VENUE_EVIDENCE: actual eligibility/permissions, instruments, leverage/brackets, commissions/rate budgets, balances, positions/orders/fills, protection and reconciliation. DEPLOYMENT_NOT_PERFORMED: the completed-system architecture remains undeployed/unstarted. These evidence categories do not excuse missing deterministic tests or the four remaining implementation gaps.

STATE records this package as TESTED/PASS and undeployed. The original BLOCKED audit record is preserved as historical evidence. Whole Stage1 remains TESTED with build_complete false and pre_start_closure BLOCKED; it is not PROVEN or ACTIVE.

NEXT selects **LUFFY-STAGE1-DATA-PROVENANCE-PIT-R1**, status **RECOMMENDED_NOT_STARTED**. After all Stage1 implementation and pre-start blockers close, the startup-readiness recommendation remains **LUFFY-PRE-START-SYSTEM-READINESS-R1**. Neither next package was executed.

## Finalization R1

The user supplied final independent review `STAGE1_ENTRY_AUTHORITY_IDEMPOTENCY_INDEPENDENT_REVIEW_R1`: **PASS**, blocking defects **NONE**, safe to commit **YES**. STATE and NEXT record the review. This finalization changes only control-plane records and this report; the reviewed source/tests remain unchanged. Implementation and independent-review test campaigns were not rerun. Existing test evidence remains offline fake-venue evidence; real order submissions remain **0**. Stage1 build completion remains false, with exactly MI-2, MI-3, MI-6 and MI-7 open. NEXT remains `LUFFY-STAGE1-DATA-PROVENANCE-PIT-R1`, `RECOMMENDED_NOT_STARTED`. No deployment or startup was performed.
