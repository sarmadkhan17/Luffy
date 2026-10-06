# OWN-03 approval boundary evidence

Scope: current architecture/offline build. OWN-02 is CLOSED and satisfies the
dependency. Source identity is pinned in source-manifest.json; it is not loaded
deployment identity. The unchanged row condition is retained in intake.yaml.

## Evidence mapped before changes

| Required behavior | Existing implementation | Executable evidence |
| --- | --- | --- |
| First real-money use needs an owner decision | Strategy Factory probation creates an exact first-live request; eligible_for_first_live requires APPROVED, exact version/spec/validation/probation/config and current installation. live_entry_block keeps activation separate. | Factory tests: rejected approval, another version, changed spec, derived version, changed evidence/policy, missing inputs/install. Stage8 gateway records approval without activation. New routine-question test retains owner_approval_missing. |
| New scope/access and spend surface for approval | approvals.CLASSES capability and paid_spend; propose retains action/object, scope/cost/safety, configuration and source hashes. items exposes exact Needs You records. Producers register proposals locally; no HTTP/chat/LLM proposal writer. | Stage8 all-class, exact binding, actor/hash/replay and scope/cost/config/safety/expiry/supersession tests. |
| Hard-limit and owner architecture/governance changes surface | Existing risk_boundary and production_code classes. Production artifacts and retained governance context are hashed. Decisions grant no configuration mutation, spend, activation or orders. SDD sections 2.5/2.6 reserve adoption to owner-authorized development/policy workflows. | All-class tests and production artifact invalidation; normal Risk/Execution authority preservation test. No new approval class needed. |
| Serious recovery needs owner acknowledgement | Supervisor serious-fault predicate sets needs_owner and retains its hold; Needs You links the current evidence to guarded resume. Generic approval_decision refuses serious_recovery. OwnerService authenticates resume; Supervisor refreshes proof and fences current control/risk. | Owner recovery, concurrency and risk-guard suites; non-owner rejection, stale-safe refusal, containment reassertion, repeated request audit and negative controls. |
| Routine HOLD, research and decisions remain autonomous | Orchestrator HOLD/decision path, bounded registered research_pass, typed read-only queries. No call to approval proposal creation from those producers. Missing evidence is unavailable, not a new permission request. | Stage8 grounded no-trade/read-only research tests. New test executes bounded research with a fake retrieval child, journals HOLD/BUY decisions and repeatedly reads them; zero approval items/requests or query writes. |
| Approved strategy operation stays autonomous inside limits | govern_version permits strategy_governor pause/degrade/reactivate/retire with the existing approval and fresh capacity/input/risk boundaries. ControlStateMachine preserves normal entry/exit permissions. | Universal authority governor lifecycle regression and control-state recovery suite. These are temporary offline worlds; no production activation. |
| Expired/rejected approval cannot authorize or expand | validity rechecks time/config/context/artifacts/supersession. Receipts are immutable; rejected first-live is ineligible. Exact item/hash and decision flow through OwnerService. | New expired-submit/rejected-reapprove tests plus Factory rejected/wrong-version and Stage8 configuration consumer tests. |

## Actual gap and change

No product implementation gap demonstrated. Exact OWN-03 evidence was unmapped,
and an explicit regression for repeated routine questions creating no blanket
approval loop was absent. Add seven focused offline regression cases, map the
existing six classes, and close only OWN-03 at this scope. No product, RISK-01,
configuration, policy or approval-class change.

The first test run exposed a new fixture's missing cycle foreign key (one failure,
207 passes). The fixture now records its cycle before decisions; the initial log
is retained. This was a test setup error, not a product defect. One unrelated
Stage8 portfolio query test is explicitly deselected: OWN-02 already retained its
journal-envelope parsing failure. No whole-suite pass is claimed.

The local Claude availability probe returned “Not logged in”; SOL performed the
bounded tests/evidence work directly. No agents or external providers were used.

## Closure impact

- GUI-01: OWN-03 dependency satisfied; ACC-03 and OWN-04 are still EVIDENCE_TO_MAP.
  Screen freshness/unknown-state proof and separate owner visual acceptance remain.
- OWN-04: unchanged. MON-01/MON-05 and declared-versus-observed telemetry proof
  remain independent of the approval boundary.
- Other Owner interface rows: OWN-01 still needs OUT-03/MEM-02 and the full exact
  evidence question matrix; OWN-02 was already CLOSED. No other row now closes.
- Next GUI dependency recommendation: OWN-04, unselected. Mapping MON-01/MON-05
  comes first if their retained evidence does not satisfy that row's dependencies.
- SEC-01/RUN-01/GOV-07 remain deferred and transitively excluded from current
  build blockers. Future activation gates remain intact. No runtime, deployment,
  browser observation or visual approval is inferred from these offline tests.

Approval proposals surface owner authority; their recorded decisions do not
execute capability/spend/risk/code adoption. This proof does not claim a generic
executor for those workflows or permission to launch Kernel/providers/trading.
