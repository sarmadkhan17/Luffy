# OUT-01 decision → terminal action → outcome evidence map

Result: **BLOCKED** at architecture/offline scope. Work is limited to OUT-01.

The exact tracker condition is: each supported scan/allocation/HOLD/Risk
refusal/execution chain retains its original ID, source manifest and terminal
registration without manual priming. Root/allocation mismatch, failed delivery
and fabricated fallback identity must not yield a verified chain.
Original dependencies: DEC-04 and DATA-02. DATA-02 is CLOSED (cae1128,
data02-provenance-lineage/closure.yaml). DEC-04 is EVIDENCE_TO_MAP and depends
on DEC-01; it is not satisfied by inspecting its implementation. This package
does not modify or close DEC-04 or DEC-01. OUT-01 cannot close through that gate.

| Behavior | Classification | Exact evidence and limits |
|---|---|---|
| Normal scan registration and original source manifest | PROVEN offline | Orchestrator.journalize → Journal.log_decision → capture_runtime.decision → capture.register. Manifest and registration are inserted in the original transaction. Existing test_scan_decision_names_later_stages_and_ignores_injected_learning_sources, test_no_producer_attribute_can_inject_learning_sources and stage7 scan HOLD tests |
| Original allocation decision and terminal registration | PROVEN offline | portfolio.current.checkpoint → deliver_allocation binds the existing decision/cycle and parent registration, proposal and intent; test_portfolio_stage_is_the_same_decision_not_a_new_identity verifies identity, sources and idempotent delivery |
| Normal HOLD and requested OPEN Risk refusal through forward outcome | PROVEN offline | test_normal_hold_and_risk_block_to_learning uses persisted normal producer inputs and current.checkpoint, then engine.outcomes.resolve_pending. It does not call journalize_allocation or insert source manifests. CASH/REJECTED/RISK_BLOCKED remain different outcomes; unmeasured refusal is unassessable |
| Failed delivery, changed parent and invented fallback refusal | PROVEN offline | capture.forward_event refuses failed terminal delivery instead of falling back to SCAN; register validates parent ID/decision/cycle/cut; verify_ancestors validates inner source hashes. Existing exact-intent resolver test refuses unknown intent; fresh refusal tests recorded in tests.txt |
| Execution trade resolves the original intent stage | PROVEN offline for resolver contract | test_execution_resolves_the_exact_intent_stage_and_legacy_trade_stays_on_original verifies exact intent matching, unknown-intent refusal and legacy original ownership. The test inserts synthetic trade rows; it does not prove full normal execution delivery |
| Full execution → booking/outcome through normal execution adapters without priming | IMPLEMENTED_BUT_UNMAPPED | booking.record calls capture_runtime.booking automatically; trade_event binds entry_identity_json.trade_intent_id to its unique registered allocation, and booking records EXECUTED and attaches an outcome. Closed booking without complete monetary evidence remains UNASSESSABLE. Exact full normal execution-path acceptance is not established by the mapped suites; the realized-accounting fixture manually registers sources and is not used to certify this clause |
| Historical stage reports before exact row mapping | IMPLEMENTED_BUT_UNMAPPED at intake | Stage7 source-integration and normal replay/world reports identify implementation and synthetic acceptance. This package maps the supported clauses above; historical claims do not grant blanket row closure or live proof |
| Demonstrated production implementation defect | ACTUAL_GAP: none demonstrated | Missing item-level execution proof and unmet DEC-04 are explicit evidence/dependency gaps. They are not evidence that the normal execution hook is absent. No speculative fix is made |

Evidence reuse: out03-replay-map-sol contains an 82-case batch completed across
selective retries after filesystem exhaustion. Its seven recorded source/test
hashes still match exactly at this intake; reuse-validation.json records that
comparison. These are prior successful offline checks, not 82 new tests in this
package. Relevant assertions and fixture limits were inspected rather than
assuming all tests prove normal adapters. Historical reports are
2026-10-02-stage7-decision-replay-source-integration-r1.md and
2026-10-03-stage7-normal-replay-world-consumer-closure-r2.md under
docs/superpowers/reports. Source-manifest.json records this inspection's exact
bytes and dirty-checkout identity; it is not a deployed-process identity.

Fresh checks target failed terminal delivery, corrupt inner parent provenance
and injected-source refusal. TMPDIR, SQLITE_TMPDIR and pytest basetemp use the
workspace volume. No Kernel, Dashboard, provider, venue or trading launch is
performed. Existing DATA-03 source changes are observed read-only and preserved;
no DATA-03 tests or implementation are changed.

Change: only OUT-01 receives row-level status/evidence mapping. STATE/NEXT and
the bundle receive the matching terminal selection and impact notes. The
optional Markdown row is synchronized. Other requirement rows, original
closing conditions/dependency edges, runtime and visual obligations remain
unchanged. No application code change or new commit is made by this package.

Impact checks:

- OUT-03 remains BLOCKED on OUT-01, DATA-03 and WRLD-06. OUT-01 is still unmet;
  DATA-03 belongs to the other Sol lane. OUT-03's row is unchanged.
- GUI-02 remains BLOCKED; its OUT-03/ACC-02/OWN-01 dependencies and exact-link
  requirement remain unchanged. This package does not build its screen.
- OUT-02/OUT-04/OUT-05, LRN-01 through LRN-06 and MEM-01/MEM-02 receive supporting
  identity evidence only. No row closes, no learning application is authorized,
  and no unassessable/partial/legacy chain becomes VERIFIED_REPLAY.

Next item: DEC-04 dependency mapping in a separately scoped package, including
its DEC-01 gate; full normal execution-path OUT-01 acceptance must also be mapped
before OUT-01 closes. DATA-03 remains with its existing lane. No successor is
selected by this terminal report.

Final validation: **3 fresh pytest cases passed**, isolated root decision/cycle
mismatch checks passed, and **16 control checks passed**. Prior 82-case
replay evidence is reused only after the recorded hash comparison. Application
source bytes remain unchanged by this package. No commits created.
