# Typed outcomes and retained memory replay

Date: 2026-09-16. **Implemented and tested engineering delivery for the requested
M2.2 forecast/trade/skip/missed-opportunity adapters and dependency-ready M2.5
retention/export/replay.** The complete M2.2/M2.5 checklist items remain partial:
regime/false-signal producers and populated broader links are not yet connected.
M2.1 awaits natural maturity. No predictive, economic or better-strategy claim.

## Behavior delivered

[Pure outcome adapters](../../../trader/cognition/outcomes.py) retain a full
source snapshot and SHA-256 source version, registration, resolution,
availability and import clocks. They use separate `observation`, `simulation`
and `actual_execution` fields. Terminal records refuse conflicting replacement.

- Selected and ignored forecasts replay the frozen protocol, episode key,
  baseline window, direction, neutral band, exact target key/version and terminal
  measurement. Both groups remain represented. Price change is not P&L.
- Closed journal trades and non-executed decisions have distinct trade/skip
  records. Legacy `realized_pnl` remains source evidence, **not verified actual
  accounting**: the typed net result is null with a missing-attribution reason.
  Journal ingestion begins at adapter activation; old decisions are not mined.
- The execution adapter accepts complete versioned reconciliation receipts with
  joined fill identities, currency, venue/environment, fees and funding. It
  computes net from the attributed components. Incomplete receipts remain
  unknown. **This is a tested receipt contract, not a new venue reconciler or
  evidence that current legacy trades have complete accounting.** M8 owns that.
- Skip/missed-opportunity close-to-close simulations freeze direction, baseline,
  target, notional and costs. Persistent registration refuses backdating or an
  already-open target; the worker resolves exact targets, retries missing ones,
  and records explicit expiry. Simulated P&L is never actual P&L or evidence of
  achievable fills. No automatic simulation registrations were added to trading.

The frozen direction-competition.v1 protocol now lives in a
[pure shared module](../../../trader/cognition/forecast_protocol.py). Its values
and protocol ID are unchanged. The learning worker stores immutable registration
and outcome receipts in its own ledger, within existing allocation/retention,
so subsequent source-scan expiry does not prevent replay. Missing exact targets
now generate explicit per-episode retry diagnostics.

## Prior evidence and clocks

The existing investigation worker imports typed records independently of trading.
Only earlier, same-symbol selected/ignored price observations enter its new
price-memory path. Actual resolution, availability, original import and local
archive-restoration clocks must all precede the new registration. Trades/skips/
simulations are deliberately excluded from price analogues. Existing investigation
matching rules remain unchanged.

A price observation adds a concrete comparison to the earlier path, its measured
return and outcome, with an explicit check for different horizons. The original
next action is retained alongside the modified evidence request. No probability,
frozen target, assessment, risk rule or trading authority is changed. The owner
panel shows the typed observation and inclusion/exclusion reasons.

The [synthetic paired investigation](../artifacts/typed-outcomes/2026-09-16-synthetic-paired-investigation.json)
contains a later changed request and its no-memory comparator. Its
[forecast archive](../artifacts/typed-outcomes/2026-09-16-synthetic-forecast-archive.json)
contains six selected/ignored outcomes with complete receipts. Both pass offline
replay. This is synthetic engineering evidence. **No naturally resolved live
memory case has changed a later live investigation.** BR/FIL retain
`pre_memory_registration`; they received no retroactive context.

## Retention, export and broader links

The typed store retains at most 256 records, at most 128 of them skips, up to
90 days from local import; individual payloads are capped at 64 KiB. Eviction
prefers skips and increments an explicit counter. It shares the investigation
32 MiB allocation and 20-second worker deadline. At most 32 typed import attempts
and 32 pending-counterfactual resolutions occur per invocation; source SQLite
reads have a two-second progress deadline. The journal scan returns a bounded
256-row tail. The existing investigation limits are preserved.

Frozen target contexts embed the selected typed records. Matched investigation
cases additionally embed their exact raw source inputs and source updates before
source retention can remove them. A single investigation export is capped at
2 MiB. Export hashes and pure replay reconstruct baseline registration, outcomes
and changed requests without upstream databases. Archive restore is atomic,
checks integrity/capacity and preserves original record clocks while separately
recording actual local import, preventing historical replay from becoming earlier
knowledge. Retention is not indefinite historical storage; export before expiry
when a longer-lived artifact is required.

Typed links carry kind, identity, version and availability. Tests retain failure,
regime, pattern, cross-market, strategy, decision and investigation references;
owner-preference and doctrine-proposal references remain separate types. Links
unknown at registration are refused. This is a **link contract**, not an automatic
regime detector, learned pattern producer, preference editor or doctrine writer.
Frozen doctrine is untouched. Populating these links from those producers remains
open, so the broad M2.5 checkbox is not claimed complete.

Read-only export/replay commands (use a new output filename):

```bash
./venv/bin/python -m scripts.memory_archive export data/investigation.db /tmp/typed-memory.json
./venv/bin/python -m scripts.memory_archive replay /tmp/typed-memory.json
# Add --investigation <id> to export one investigation and its frozen prior sources.
./venv/bin/python -m scripts.verify_memory_outcomes --output /tmp/exact-outcomes.json
```

## Final-source verification

**205 passed in 18.23 seconds**: typed outcomes, investigation memory/consumer/view,
market investigations, dashboard authentication, attention learning/view/telemetry,
cognition contracts/attention/replay and single strategy-creation boundary.
This is the relevant regression suite, not the full repository or a performance
validation. Whitespace, watchdog shell syntax and dashboard JavaScript syntax
checks also passed. [Test record](../artifacts/typed-outcomes/2026-09-16-tests.json),
[source hashes](../artifacts/typed-outcomes/2026-09-16-source-manifest.json).

Tests cover changed source versions/keys/registration/measurements, selected and
ignored coverage, delayed knowledge/import/restore, unknown journal accounting,
complete/incomplete execution receipts, simulated costs, forward registration,
missing-target retry, terminal immutability, failed atomic restore, source-retention
replay, storage capacity, skip eviction and safe rendering/authentication.

## Current UTC, maturity and deployment

At **18:41:23 UTC**, the read-only exact check found **16 original pending
forecasts, zero matured**. First target close is still September 17 **00:00 UTC**;
BR/FIL investigation windows close September 17 **16:00 UTC**. The original scan
envelopes have expired, but all exact baseline version IDs and payloads still
joined the shared version store and the immutable predictions. No nearby bars
were substituted. The earlier report retains the scan-level verification performed
before envelope expiry. New forecasts retain independent receipts prospectively.
There are still no natural terminal measurements to verify for M2.1.
[Exact maturity evidence](../artifacts/typed-outcomes/2026-09-16-final-maturity.json).

The final-source worker invocation succeeded (`ok`, approximately 1.59 seconds),
with two active original investigations, no measured investigation memories,
no typed-source refusals, and no registered counterfactual experiments. The typed
store contained **128 forward-observed skip records** at the final snapshot.
The watchdog invokes these modules afresh, so typed imports and the receipt code
are installed for subsequent invocations. This is deployed worker functionality;
forecast import and simulated/verified execution paths have synthetic acceptance
but no new naturally resolved deployment observation yet.

Runtime remained **FROZEN**, demo true, three open legacy journal trades;
research referee/handoff false. No kernel/dashboard restart, orders, venue stops,
operator controls, watchdog pause or Gate 2 evaluation changed. This was not a
venue reconciliation. Dashboard bind remains **127.0.0.1:8080**. Live unauthenticated
API returned 401; private-password browser-session login and authenticated
investigation API returned 200. SSH tunnelling remains the remote-access method.
Credentials were not printed or retained. [Runtime/access evidence](../artifacts/typed-outcomes/2026-09-16-runtime.json).

## Troubleshooting and exact next work

Claude CLI reported its session limit; direct implementation used the owner's
existing authorization. The sandbox failed before execution; approved escalation
was used. An invalid synthetic OHLC revision fixture was corrected. The full suite
caught a pure-module dependency on the runtime worker; extracting the unchanged
forecast contract fixed it without permitting runtime imports into cognition.

Four degraded consumer invocations exposed a health-clock race: slow journal
imports occurred before reading the collector heartbeat. Reading the coherent
market snapshot first fixed the false-future check; the final invocation succeeded.
The cumulative failure count was preserved. [Structured log](../artifacts/typed-outcomes/2026-09-16-troubleshooting.jsonl).

**Next exact engineering item: remaining M2.2/M2.5 producers and links.** Connect
explicitly versioned false-signal/regime-transition cases and existing decision/
strategy/cross-market references to the typed contract, and connect a declared
non-trade experiment caller to `register_counterfactual` without inventing costs
or backdating registrations. Reuse the completed adapters and archive CLI.
Broader discovered-pattern links depend on M3; complete actual accounting depends
on M8. Keep those dependencies explicit rather than treating null accounting or
link references as completed producers.

In parallel, rerun the read-only exact check after September 17 00:00 UTC and
verify naturally resolved target versions and frozen measurements. If exact source
or outcome evidence is absent, retain explicit retry/refusal. At 16:00 UTC verify
the original investigation windows. Waiting must not block dependency-ready work.
No candidate was created or admitted, no trading decision changed through this
memory path, and no better strategy or calibrated performance has been established.

## Subsequent integration

The [producer integration report](2026-09-16-memory-producer-integration.md)
records the delivered remaining case producers, populated references, declared
caller, 221-test final-source evidence and the later exact-baseline retention loss.
The snapshots and next-work statements above describe this earlier delivery.
