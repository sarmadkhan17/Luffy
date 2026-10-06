# OWN-02 typed owner authority evidence map

Scope: current architecture/offline build completion. The exact intake row,
dependency and unchanged closing condition are retained in `intake.yaml`.
`source-manifest.json` pins the inspected implementation and tests to repository
revision 1120ac1. This is source identity, not loaded deployment identity.

## Existing implementation mapped

| Closing requirement | Implementation | Current executable evidence |
| --- | --- | --- |
| One typed authority across controls | `trader/owner/contract.py`, `service.py`; dashboard adapter/GraphQL controls, Kernel Telegram dispatch and optional gateway adapters enter OwnerService. Browser/chat cannot execute orders or edit trading truth. | `test_owner_interface.py`: dashboard double-submit/legacy mutation gateway, Telegram sender/redelivery, optional provider duplicate/confirmation binding. `test_owner_interface_boundary.py`: import/AST authority boundaries, chat zero controls and semantic negative controls. |
| Authenticate actor and scope | `authz.py` resolves channel/identity to principal and operation grants; `ipc.py` authenticates per-channel transport. Requests cannot assert their own principal. | Core interface tests k/k2, w3, w4, p2, w1/w2, r2: unbound identity/grants, forged channel/listener, HTTP authentication, wrong sender/principal, confirmation collision/expiry. |
| Exact request/version/evidence/configuration hashes | `approvals.py` retains proposal context and source hashes, configuration digest, immutable decision receipt, expiry and supersession; Strategy Factory first-live objects bind version/spec, validation, probation and current install. `contract.py` restricts approval arguments to exact item/hash/decision. | `test_stage8_owner_os.py`: all approval classes, replay/actor/audit/hash refusal, configuration/scope/cost/safety/expiry/supersession staleness, first-live version/policy, artifact change, configuration invalidation consumed by Factory and legacy missing configuration refusal. |
| Needs You submits the same authority | Owner API approval route forwards `approval_decision` to gateway. `OwnerEvidence.tsx` sends item/hash/decision with persisted request identity; stale/expired records disable submission. Recovery links to existing guarded Operations path. | Stage8 API gateway test; `frontend/tests/stage8.test.tsx`: exact binding, same ID after unknown outcome, stale/expired submission refusal. |
| Distinct accepted/refused/completed outcomes | `contract.py` separates status and disposition; `service.py` reserves/claims/records results. Accepted approval means decision recorded with no execution. Accepted close/panic intents have kernel completion evidence; unknown/pending outcomes retain identity. | Core interface tests h/h2/i/j/j2/w8/n/o: duplicate, conflicting body, concurrent delivery, stale/future, restart, persistence failure, timeout/reconnect. Stage8 approval/first-live tests prove acceptance without activation. |
| No replay scope expansion or Risk/Governor bypass | Request fingerprint, immutable receipts and fresh context revalidation; controls invoke guarded Supervisor recovery. Approval records no spend, configuration mutation or order; first-live is only approval state, not activation. | `test_owner_recovery_risk_guard.py` and concurrency suites exercise real guarded code with temporary journals/recording venue doubles and negative controls. Stage8 normal authority preservation and Factory consumer tests cover unchanged Risk/Execution gates. |

Historical evidence: `docs/superpowers/reports/2026-09-28-owner-interface-gateway-v1.md`
records revision-6 review and reservation-race/crash tests. Its 808-test claim and
historical review apply only to that report's snapshot; they are not a current
suite pass or deployment claim. Current main also contains the Stage8 approval
implementation (52aac90) and owner gateway (de916ac).

## Actual gap and change

No product implementation gap demonstrated against OWN-02. The tracker had only
stage-level claims and no exact row evidence. Map the existing authority and
current offline regressions; close only OWN-02 at that scope. No implementation,
configuration, risk policy, provider or trading change is needed.

Initial collection of revision-3 through revision-6 suites failed because
`test_owner_interface_r3.py` eagerly reads removed legacy
`trader/dashboard/web/index.html`; the other suites import it. See
`legacy-collection.txt`. These suites were not passed on current main and no
replacement HTML was fabricated. Current service/boundary/recovery and React
tests supply the mapped proof. Repair of retired browser harnesses is outside
this row's demonstrated product gap.

## Build policy and cross-tracker impact

SEC-01, RUN-01 and GOV-07 remain DEFERRED, with all original conditions and
dependency memberships preserved. Their current-build exclusion applies
transitively. No live Kernel, real provider, trading, process launch or deployment
proof is a new OWN-02 closure gate. Tests may call Kernel methods on constructed
fixtures and run temporary IPC; they do not boot the production loop.

- OWN-03: its OWN-02 dependency is satisfied at architecture/offline scope.
  Exact approval classes and ordinary-autonomy behavior still need their own
  row mapping; leave its status unchanged. Recommended next GUI dependency:
  OWN-03, not selected or started.
- OWN-04: no dependency on OWN-02; MON-01/MON-05 mapping and honest observed-state
  proof remain its work. No runtime truth is inferred from this closure.
- GUI-01: OWN-03, OWN-04 and ACC-03 remain its dependencies. Typed Needs You
  evidence is reusable, but this does not award screen functional/runtime
  acceptance or VIS-01 owner visual approval. Leave GUI-01 unchanged.

Current checks: **23 approval/Factory tests passed**, **15 frontend tests passed**
and **11 control-document checks passed**. The broad run had **338 passed, 1
failed**: `test_portfolio_exact_cut_candidates_rejection_intent_risk` fails at
`queries.py:205` because `json.loads` is called on a retained
`!luffy-journal-detail.v1!` envelope. This is an owner portfolio-query gap,
not an approval/control authority gap; its exact trace is retained in
`focused-tests.txt` and no whole-suite PASS is claimed. No unrelated source is
changed.

Test results and control-document checks are recorded in `closure.yaml` and
`control-validation.json`. No current loaded runtime identity is claimed.
