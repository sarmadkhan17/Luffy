# Forward loop continuation: independent verification and recovery review

Verified September 16 at approximately 16:17 UTC / 19:17 Bahrain. No application
source, configuration, runtime controls, research artifacts or doctrine changed.
Preserved the extensive pre-existing workspace diff. No restart or order action.

## Verified runtime

- Watchdog is enabled; cron invokes it every five minutes. Its 16:05, 16:10 and
  16:15 UTC records consumed successive scans with zero new registrations and
  `existing_episode: 16`. This is scheduled consumption evidence, not just a
  manual consumer run.
- Collector was healthy with zero drops, errors, timeouts or budget overruns;
  32.71ms maximum producer time in the captured health snapshot. This is an
  observed maximum in this process, not a new benchmark or hard latency bound.
- Verified scan `scan_1966c38e5bc6423f89e8725640810ac7`: 19 linked journal
  decisions, 16 captured decisions, 160 evaluator receipts, zero missing evaluators.
- Chromium rendered attention OK and learning OK with zero page JavaScript errors.
- Ledger: 16 pending predictions, 2 selected and 14 ignored; no duplicate pending
  symbols. Zero matured targets, zero outcomes; descriptive feedback remains empty.
- First deadline remains September 17 at 00:00 UTC / 03:00 Bahrain. Live exact
  outcome joins cannot yet be verified. Source review confirms exact target open
  timestamp, closed-bar and availability checks, immutable published outcomes,
  and explicit unavailable status after grace. These are code observations, not
  evidence that live resolution has happened.
- Direct demo venue read: XRP 68.9 short, LINK 36.24 short, AAVE 0.9 short; all
  match the journal and each has a native algo stop of matching size and side.
- Research referee/handoff remain false. No Gate 2 work or research evaluation.
- Existing focused verification: **15 tests passed in 5.21s** across learning and
  dashboard view tests. Prior 295-test result was not rerun or claimed anew.

Artifacts: [runtime](../artifacts/attention-rollout/continuation-verification.json),
[venue](../artifacts/attention-rollout/continuation-venue.json),
[structured troubleshooting](2026-09-16-forward-continuation.jsonl).

## Recovery findings and bounded next implementation

`trader/engine/executor.py` returns after an unconfirmed fill and relies on next
boot adoption. A submission exception can also be ambiguous: the venue might
have accepted the order before the response failed. The stop-failure handler
labels an emergency close recovered after submission without confirming that
exposure is flat. No persistent retry work item connects these cases to the
running kernel. `kernel._detect_exchange_exits` only adjusts journalled exposure;
it cannot discover an unjournalled entry. Boot adoption cannot reconstruct a
missing original stop intent, and simply scheduling the whole boot reconciler
would also repeat its unrelated ghost accounting and stop sweep behavior.

Smallest useful safety slice for Claude implementation, followed by independent
coordinator review:

1. Persist execution intent and correlation IDs before entry submission. Capture
   intended stop geometry and strategy/decision references. Distinguish explicit
   rejection, ambiguous submission, unconfirmed fill, unconfirmed emergency close,
   confirmed protected exposure, and confirmed flat exposure.
2. Gate new entries deterministically while unresolved exposure or failed venue
   reads prevent reliable portfolio risk accounting. Keep existing exit management
   running and preserve owner holds; recovery must not clear an operator freeze.
3. Add a bounded continuous recovery path, serialized with execution. Reconcile
   actual venue side/size and order status, retaining unresolved state on errors.
   Do not blindly resubmit an ambiguous entry. Pending orders must be terminal
   before a momentary flat position can clear an entry uncertainty.
4. Use existing protective helpers; preserve existing native stops, check actual
   protection size/side, and use saved risk intent rather than invented stop levels.
   Any emergency reduce-only close remains pending until confirmed. Account for
   partial fills, restart, journal failures, and repeated calls without duplicates.
5. Record transitions, attempts, source order/decision IDs and explicit failure
   reasons. Surface unresolved recovery to the owner. Test ambiguous acceptance,
   late and partial fills, unavailable venue, close failure, restart persistence,
   existing stops and operator hold preservation before a coordinated demo rollout.

This is a proposed implementation contract, not implemented behavior. Claude CLI
returned HTTP 429/session limit, resetting at 22:20 Bahrain. CLAUDE.md assigns
implementation and routine labour to Claude; the coordinator did not transfer that
work to another model. No implementation or deployment was attempted this turn.

For learning, retain the current forward protocol until real targets mature.
At the first post-deadline scan, verify each outcome against the exact retained
version and baseline, verify selected/ignored descriptive aggregation, and confirm
repeat invocations do not revise terminal outcomes. Missing data remains pending
then unavailable; do not use raw counts as calibration, profitability or admission.

Follow-up: the owner subsequently authorized direct implementation while Claude is limited. The implementation blocker above is superseded by [the deployed exposure-recovery report](2026-09-16-exposure-recovery.md).
