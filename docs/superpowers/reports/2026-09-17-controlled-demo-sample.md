# Controlled demo sample — existing Luffy execution and accounting

The owner authorized temporary demo samples through Luffy, without a new system
or persistent design change. This pass completed **one sample**, then stopped
because funding publication, not trade count, is the remaining accounting blocker.
The authorization is recorded; do not request it again for the same scope.
Future design changes still require owner approval.

## Actual venue records

Trade `pos_05a5e8f981`, labelled `diagnostic:owner-demo-probe`, is closed.

| Field | Observed value |
|---|---|
| Instrument / side / quantity | SOL/USDT, long, 0.51 SOL |
| Entry / exit | 98.93 / 98.88 USDT |
| Entry / close order IDs | 4205684574 / 4205684620 |
| Fills | 4: one entry fill, three fills on the single close order |
| Gross fill P&L | -0.02550000 USDT |
| Actual commissions | 0.04035323 USDT |
| Fill subtotal **excluding funding** | **-0.06585323 USDT** |
| Complete economic P&L | Unknown; funding-publication retry |
| Journal close UTC | September 17, 02:20:58.293722 |

A native reduce-only stop (`1000000207723000`) protected the sample and was
removed after flat confirmation. There was one entry attempt and one close
attempt, with no execution errors or remaining recovery intent. SOL is flat.
Selection used a current public, fully closed 15-minute observation: close
99.03 above EMA20 98.624236, with a preceding-hour rise of 0.3852%. This is
an owner-directed sample, not an admitted strategy, natural selection episode,
or evidence of predictive superiority. No strategy-registry row was added.

The existing main Journal, RiskManager and Executor produced the records.
The decision's executed flag was completed through the existing journal API
following actual receipt verification, with an operator audit event. Normal
executor journal/outcome records were retained; no typed-memory import occurred.

## Evidence and runtime

[Verified arithmetic and runtime summary](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/verified-summary.json),
[booking receipt](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/booking.json),
[whole-trade capture](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/whole-accounting.json),
[native protection](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/protected-position.json),
[post-trade venue check](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/runtime-after.json).
The booking, whole-trade capture and both saved worker attempts replay PASS.
Replay success does not turn an incomplete economic assessment into a pass.

Kernel PID 300976 was paused from 02:20:49.503964 to 02:21:01.327125 UTC
(**11.823161 seconds**) to avoid concurrent execution, with an independent
90-second restoration guard. It resumed without restart; its watchdog flag
was removed. Existing exchange stops stayed active. XRP short 68.9, AAVE short
0.9 and LINK short 36.24 remained matched and protected. The 02:20 population
consumer run preceded the pause; no claim of zero per-minute collection delay
is made. At 02:26:33 UTC, the kernel was ACTIVE, frozen artifacts and production
source hashes matched, and the journal had two bookings and zero emergency
archives. Research stops and configuration are unchanged. No new trading service
or automatic sampling loop was installed.

The worker was first invoked manually at 02:23:13–02:23:18 UTC and saved one
immutable retry attempt. The normal **02:30:02–02:30:08 UTC scheduled run** then
saved a second attempt. Both retain `funding_publication_frontier_pending_retry`.
[Scheduled retry evidence](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/scheduled-retry-review.json)
records two total attempts, a healthy worker and the next due time 02:40:08.147 UTC
(the next eligible watchdog pass may be later). The 02:25 pass made zero new
attempts because the first attempt was still in backoff.

## Checks and exact next work

The approved [provenance build](2026-09-17-exit-provenance-delivery.md) passed
246 final-source tests. Temporary operational helpers passed 13 tests in 0.94s,
plus an actual Journal/cycle-foreign-key/sample-selection integration check.
The five broader-suite failures documented in the build report remain unresolved;
there is no clean full-suite claim. Temporary scripts are audit artifacts, not
a new supported execution system; the isolated-journal draft was not executed.

The [venue clock](../artifacts/execution-accounting/2026-09-17-controlled-demo-sample/funding-clock.json)
schedules SOL's next funding boundary for **September 17, 08:00 UTC**. Observe the
existing worker's retries after actual publication, preserving every earlier
artifact. Do not assume zero funding or import this controlled sample into
strategy-performance learning. Additional trades cannot resolve this publication
wait. A naturally selected complete trade receipt and forward-only memory import
remain unobserved. Native-trigger and emergency live paths remain unobserved;
non-USDT conversion still lacks actual attribution evidence (owner reports no
conversion history in three months).

M8.1 remains partial, **12/55**. Preserve M3 maturity reviews at 04:00, 08:00 and
16:00 UTC, and the full-window review September 18 at 00:00 UTC. Missing-data
retries, frozen artifacts and the Gate 2/M3.2 stops remain unchanged.
