# Durable entry exposure recovery

The owner explicitly authorized the coordinator to implement while Claude CLI is
session-limited. This supersedes the working-role implementation blocker in the
previous continuation report. No secondary model or parallel implementation was
used. Existing unrelated changes remain intact; no commit/reset/cleanup occurred.

## Implemented behavior

Before submitting a futures entry, the executor persists the intended position,
stop geometry, decision/strategy references, correlation ID, client order ID and
code/schema version. A global entry lock serializes submissions with recovery.
Unresolved intent blocks subsequent entries across symbols, including later
symbols in the same kernel cycle. Spot behavior is unchanged.

The running kernel performs a bounded recovery pass each cycle in ACTIVE/FROZEN.
HALTED remains halted; recovery never changes operator control state or holds.
An unreadable recovery ledger blocks entries while leaving existing exit handling
able to run. Boot reconciliation excludes recovery-owned positions and avoids
sweeping their potentially unjournalled native protection.

Recovery queries ambiguous submissions by saved client order ID. It never blindly
resubmits an entry. Partial entries have their remaining order canceled and order
state re-read before adoption. An observed terminal fill must match actual venue
side/quantity. Protection is verified as a reduce-only STOP_MARKET of matching
size, with a stop at least as protective as the saved risk intent. Recovery can
place a missing stop once, then verify it on a later cycle before journalling.
Conflicting/ambiguous protection remains explicit and blocks entries.

A stop-placement failure's emergency reduce-only close is now persisted before
submission and marked pending, not recovered. A terminal partial close can retry
only the actual remaining venue quantity. Unknown close submissions are queried,
not blindly repeated. Explicit close rejections stay pending for another bounded
attempt. Flat exposure only clears recovery after terminal order confirmation
and a successful protective-order read showing no residual stop. Existing stops
are not deleted by this recovery path.

Fill confirmation requires a terminal order and its reported average, not an
order price hint. Recovery order observations are retained in control events;
they are **not realized P&L**. Entry blocks/reasons are visible in the authenticated
dashboard attention panel, rendered as text.

## Verification

- Final focused run: **139 passed in 20.13s**, including 24 new recovery tests,
  kernel ACTIVE/FROZEN/HALTED behavior, protection, reconciliation, existing exits,
  entry geometry, dashboard rendering/authentication, and forward learning.
- Fresh-process research-runner verification: **31 passed in 52.50s**.
- An earlier full-suite run: **1,586 passed, 14 failed**, seven existing pandas
  warnings, 439.97s. Twelve failures exposed the initial futures recovery hook
  being applied to a spot fixture. Restricted the hook to futures and expanded
  kernel tests to include pending recovery. The other two source-inspection tests
  read changed source while the long-running test process held the earlier module;
  they pass in the clean research-runner run. This is not a claim of a final full
  suite rerun. All affected checks were rerun on the final source.
- `git diff --check` and Python compilation passed.
- Before rollout, direct demo venue verification confirmed XRP 68.9 short,
  LINK 36.24 short and AAVE 0.9 short matched the journal and had matching
  reduce-only STOP_MARKET algo protection. No real orders were sent for testing.

## Scope and remaining limitations

This closes common unconfirmed-entry and failed-emergency-close recovery gaps;
it is not a complete execution/accounting rewrite. Venue order history that stays
unavailable, mismatched side/quantity/ownership, ambiguous stop placement,
conflicting protection and leftover stops after flattening remain blocked for
inspection. No automatic override or guessed risk geometry clears those cases.
A crash after persisting intent but before submission can therefore require
operator investigation if the venue cannot establish the order's existence.

Recovery observations of emergency round trips do not yet create a fully attributed
realized-fill trade ledger; their P&L remains unclaimed. Existing panic/legacy exit
accounting paths were not redesigned. Recovered entries retain decision/strategy
links; this change does not grant strategy admission or claim execution profit.

The forward shadow protocol and ledger are unchanged. Targets still mature first
on September 17 at 00:00 UTC / 03:00 Bahrain. Preserve explicit missing outcomes,
selected/ignored descriptive counts, research.referee=false, research.handoff=false,
frozen doctrine/research artifacts, and demo-only execution. Do not resume Gate 2.

Structured lifecycle and troubleshooting: [events](2026-09-16-exposure-recovery.jsonl).
Source versions and verification: [artifacts](../artifacts/exposure-recovery/).

## Deployment and final handoff

Deployed and verified September 16 at approximately **16:40 UTC / 19:40 Bahrain**.
Paused watchdog, requested graceful shutdown, verified old processes exited,
then started kernel PID 262048 and dashboard PID 262049. Boot reconciliation
reported zero adoptions, ghosts, size alignments, stop sweeps, missing protection
or failed rearms. No forced kill, manual close, test order, or control-state change.

Verified the new runtime's scan `scan_94dcba1678034b2bbe3bde85ee3bbdf3`: 19 linked
journal decisions, 16 captured decisions, 160 receipts. Collector healthy with
zero errors/drops/timeouts and a live worker. Chromium rendered attention and
learning OK with zero JavaScript errors; authenticated API exposed the new
recovery field as null (no unresolved execution incident). Recovery failure cases
were verified synthetically, not by inducing a live incident.

Watchdog supervision is restored. Its actual invocation consumed a later scan,
registered zero duplicate episodes, and reported `existing_episode: 16`.
Predictions remain 16 pending, zero resolved/unavailable, and empty descriptive
outcomes. All three demo positions were rechecked after deployment: journal
quantities/sides match, and native reduce-only STOP_MARKET orders protect each.
Final source hashes match the tested/deployed manifest, including unchanged
forward-consumer source, config and doctrine during rollout. State remains ACTIVE.

Next: allow the first target bar to close naturally at September 17 00:00 UTC,
then verify exact source/version joins and descriptive feedback. Do not infer
edge, calibration or profit from the outcome count. Before widening recovery,
add properly attributed actual fill accounting for emergency round trips and a
reviewed resolution path for the explicit conflict/unknown states above.
