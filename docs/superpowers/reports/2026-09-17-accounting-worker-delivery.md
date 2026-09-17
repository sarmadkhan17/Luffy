# M8.1 bounded accounting capture and retry worker

**247 relevant final-source tests passed in 16.02 seconds. M8.1 remains partial;
12/55 acceptance items checked.** The existing whole-trade reconciler is reused.
No natural accounting receipt or verified memory import has yet been observed.

## Delivered and deployed

The [worker](../../../trader/observability/accounting.py) discovers booking IDs
from the read-only journal into its separate SQLite queue. Open trades wait for a
natural close. Closed trades receive new, exclusive capture filenames; incomplete
attempts remain retained and retry after exponential backoff from five minutes to
eight hours. Complete receipts stop automatic retries and remain immutable.
Every successful capture replays through the existing strict accounting verifier.
No estimated journal P&L is promoted. Missing entry provenance, funding publication,
FX conversion and ambiguous exits retain the reconciler's refusal behavior.

The queue reserves attempts before network work. Following an interruption, it
replays any saved file before deciding whether to retry. A complete file saved
before a queue commit therefore does not trigger another venue capture. Missing
or partial files remain retryable. Diagnostics retain trade IDs, unique attempt
IDs, paths, UTC timestamps, reasons and exception types, without exception text.

The process has an exclusive nonblocking lock, a 45-second alarm, five-second
venue request timeout and watchdog outer timeout of 50 seconds plus a five-second
kill grace. Each pass discovers at most 100 receipts and considers at most two
trades. Existing per-history/page/order bounds remain unchanged. Queue capacity
is 10,000 jobs and 10,000 attempts; capture storage has a 256 MiB budget with an
8 MiB per-artifact ceiling. Reaching capacity pauses explicitly; nothing is pruned
or silently dropped. SQLite metadata is additional bounded-count storage. Capacity
rotation/archival remains operator work; disabling capture does not delete evidence.

The [watchdog](../../../scripts/watchdog.sh) runs the opt-in worker **after** the
existing population consumers. `data/accounting.enabled` is enabled. Its separate
queue/artifacts live in `data/accounting-worker/`; health is `health.json` there.
The CLI is `./venv/bin/python -m trader.observability.accounting --once --enable`.
Removing only the accounting opt-in flag suspends future accounting passes. Do not
use the global watchdog-off flag for that purpose. No kernel/dashboard restart,
trading/risk configuration, research stop or population configuration changed.

Automatic memory import is deliberately absent. After a naturally complete receipt
exists, use the existing explicit whole-trade replay/import CLI, preserving forward
activation and terminal conflict checks. Completion is as-of captured venue history;
this worker does not reconcile later venue corrections to terminal receipts.

## Evidence and limits

- [Verification](../artifacts/execution-accounting/2026-09-17-worker-verification.json):
  source hashes, test result and empty-queue demo smoke. 15 new worker tests plus
  the previous 232-test accounting/recovery/protection/memory/population regression
  set pass. These are synthetic/offline checks, not observed trading performance.
- [Tests](../../../tests/test_accounting_worker.py) exercise immutable retries,
  funding publication, backoff, open/empty queues without venue calls, crash before
  and after saving evidence, capacity pauses, redaction, process locking, deadline
  health and non-demo refusal. An initial fixture import error was corrected before
  the final-source run. Watchdog shell syntax and workspace whitespace checks pass.
- [Read-only runtime](../artifacts/execution-accounting/2026-09-16-worker-runtime-235651.json),
  September 16 23:56 UTC: kernel PID 300976, ACTIVE/demo; XRP short 68.9, LINK short
  36.24 and AAVE short 0.9 match journal and native reduce-only STOP_MARKET protection.
  Watchdog enabled; both natural population consumers healthy at 23:55 UTC. Zero
  normal/emergency accounting receipts. Frozen doctrine, declaration, freeze,
  dataset and population configuration hashes match prior evidence.

Natural complete accounting, live memory import, non-USDT conversion, emergency
funding/ownership, native/ambiguous-exit provenance and legacy P&L consumer migration
remain unfinished. Capacity alarms appear in worker health and watchdog output;
there is no new dashboard alert integration. No M8.1 acceptance or economic-success
claim follows from an empty queue. The original legacy positions remain potentially
unattributable; do not invent entry provenance or force a trade for this test.

Claude's probe returned API 429/session limit; direct implementation used existing
authorization. Sandbox namespace initialization failed; approved external execution
was used. No new candidate, admission, trading-selection change, profitability or
improved-performance claim. Roadmap dependencies unchanged.

## Next work

Observe/export/replay the first natural booking and a naturally closed trade with
complete entry provenance. Check scheduled worker attempts and retain all incomplete
captures until funding publication or missing evidence permits completion. Verify
one naturally complete receipt and explicit forward-only memory import. Next
engineering remains verified non-USDT conversions, emergency/native exit provenance,
and separately reviewed legacy reporting/economic consumers. Do not rebuild the
reconciler or queue.

Keep M3.1 collection and all missing-version retries. Full-window coverage review
is due at/after September 18 00:00 UTC across both local receipt indices, eligibility,
selection, missed registrations, unresolved/expired and all classification-neutral
terminals. No M3.2 search without that review and a separate frozen search protocol;
no Gate 2. Referee/handoff remain false.

## Scheduled opening and maturity review — September 17 00:01 UTC

The actual UTC clock crossed the frozen opening during this session. The naturally
scheduled consumers ran at **00:00:01 UTC**; the accounting worker ran at
**00:00:03 UTC**, status ok, zero queued jobs/attempts and no automatic memory import.
This verifies scheduler deployment, not natural trade accounting.

A new [opening capture](../artifacts/pit-dataset/2026-09-17-opening-worker-capture.json)
passed source-free replay. The [review](../artifacts/pit-dataset/2026-09-17-opening-worker-review.json)
reconciles both indices: two indexed/two exported receipts each (activation plus
one scan), no missing/pending exports and no exports absent from index.
`window_status=open`, `complete_sampling_claim=false`, `search_ready=false`.

Each stream observed 16 rows, 15 eligible under the frozen declaration, three
selected and 13 ignored. Declared **TAO/USDT is missing** in both opening scan
receipts; this gap is retained, not backfilled. Attention reasons: four below
minimum salience, nine beyond top-k, three selected. Forecast registration reasons
are 16 existing episodes; investigation reasons are three existing episodes and
13 not selected. Zero new registrations, zero cohort rows, zero terminal results;
no unmatched registration claims or orphan results. These facts do not establish
complete sampling or population rates. Review the missing symbol and subsequent
natural receipts at the full-window cut; do not change the frozen declaration.

The [exact maturity verifier](../artifacts/pit-dataset/2026-09-17-opening-worker-maturity.json)
at **00:01:02 UTC** found 18 forecasts, **16 mature with
exact_outcome_missing_retry**, two immature (04:00 UTC target), and each original
still carrying a missing baseline version. No matured outcome is accepted or
substituted. Preserve all `missing_versions_retry`; rerun to a new file after
natural exact evidence arrives. Original BR/FIL investigation windows end 16:00
UTC. The full population-window review remains due September 18 00:00 UTC.

[Structured events](../artifacts/execution-accounting/2026-09-17-worker-troubleshooting.jsonl)
record tool limitations, validation, the natural worker pass and retained gaps.

Final [post-deployment runtime](../artifacts/execution-accounting/2026-09-16-worker-runtime-000130.json)
was observed **September 17 00:01:27 UTC** (the filename retains the runtime script's
prior date prefix; the embedded UTC is authoritative). Same ACTIVE/demo kernel,
three matched/protected positions, zero accounting receipts, healthy natural
consumers, watchdog on and frozen hashes unchanged. No natural import was possible.
