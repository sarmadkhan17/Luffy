# M3.1 classification-neutral cohort and coverage review

Delivered September 16, 2026. **277 tests passed against final source** in 22.16
seconds across 16 relevant modules. M3.1 remains partial and acceptance remains
**12/55**: the frozen registration window has not opened. No new candidate,
admission, changed trading decision or better-performance claim is established.

## Delivered behavior

[cohort.py](../../../trader/observability/cohort.py) builds a deterministic view
from every registration receipt, independent of terminal classification. It
retains selected/ignored forecasts, measured investigations including those that
produce no typed classification, unresolved episodes, unavailable forecasts and
not-testable/expired investigations. Original evidence, reasons, exact clocks,
receipt identities and assessments remain linked. Missing values remain unknown;
observed movement is not execution P&L. Investigation selection is distinguished
from scanner selection (unknown scanner selection is not fabricated).

The review reconciles observed scan eligibility, attention and registration
reasons, missing symbols, registration claims without receipts, orphan updates
and terminal results, missing-target retries, activation/cadence gaps and long
unobserved scan intervals including window boundaries. Repeated scan observations
are counted separately from unique scans and registered episodes. Overlapping
padded episode windows across both streams/symbols merge conservatively for
review; this is not a claim of independent samples or permission to search.

[capture](../../../trader/observability/population.py) now reads one bounded,
read-only index snapshot per configured ledger. It validates binding, hashes and
local clocks, and exposes acknowledged-but-missing and pending export files.
An absent index means copied evidence becomes known at the actual capture clock.
Concurrent export after the snapshot can require a fresh capture/retry; nothing
is backdated or silently repaired. The snapshots reconcile receipt indices, not
exchange state or a census of all market events.

New capture schema `pit-population-capture.v2` embeds the cohort and local index
snapshots. Replay recomputes the cohort from retained inputs and rejects a changed
view even if the outer digest was recomputed. V1 frozen captures still replay.
The existing typed dataset contract is unchanged. Terminal labels learned after
the cut remain visible but are explicitly not known by the cut. Complete sampling
and search readiness remain false even once the clock reaches the cut.

The capture CLI prints cohort size, window status and per-stream reconciliation.
This is an offline artifact view; no new dashboard route or trading policy was
added. Producer hooks and consumer scheduling are unchanged. No service restart,
watchdog pause, prospective backfill or frozen-artifact rewrite was performed.

## Evidence

- [Final tests](../artifacts/pit-dataset/2026-09-16-cohort-tests.json) and
  [source hashes](../artifacts/pit-dataset/2026-09-16-cohort-source-manifest.json).
  Eight new cohort tests plus the previous population/integration suites cover
  neutral inclusion, retries/unavailable, expiry, orphan results, lost/pending
  exports, restoration/cut clocks, repeated scans, gaps, source-free replay and
  rehashed-view tamper refusal. The suite is not the full repository. Earlier
  268- and 269-test evidence is retained; totals must not be added together.
- [Synthetic capture](../artifacts/pit-dataset/2026-09-16-cohort-synthetic.json):
  six resolved and six unresolved forecasts; one measured and one unresolved
  investigation. The compatible volume investigation remains in the neutral view
  although it is neither a false-signal nor regime-transition typed result.
  Source-free replay passed after temporary live sources were removed.
- All three original datasets and the V1 initial population capture replayed.
  `git diff --check` passed. Source was not edited during final tests.
- [Troubleshooting](../artifacts/pit-dataset/2026-09-16-cohort-troubleshooting.jsonl)
  records tool limitations, fixture corrections, access retry and results.

## Actual UTC, collection and maturity

[New capture](../artifacts/pit-dataset/2026-09-16-cohort-capture.json) at September
16 **21:30:54 UTC**: both streams have exactly one activation export matching
one indexed receipt, no pending/missing exports, zero registrations and zero
cohort rows. Window status is `not_started`. Zero pre-window rows do not establish
population coverage. This capture read exports; it did not manually invoke either
consumer or create historical registrations.

[Runtime receipt](../artifacts/pit-dataset/2026-09-16-cohort-runtime.json): both
consumers naturally ran at **21:30:01 UTC**, status ok, population hooks enabled;
five-minute cron and watchdog remain enabled. Attention worker healthy, demo true,
control state ACTIVE, three open journal trades, referee/handoff false. This was
not venue reconciliation and does not independently verify venue protection.

[Exact verification](../artifacts/pit-dataset/2026-09-16-cohort-maturity.json) at
**21:30:54.842 UTC**: 18 forecasts, zero mature. Each original forecast still lacks
one baseline version; two newer forecasts retain their baselines/scan receipts.
Five investigations are retained. Missing exact versions remain explicit retries;
no adjacent-price substitution or retroactive BR/FIL memory was introduced.
M2.1 remains pending; no naturally resolved case changed later reasoning.

## Owner access correction

Current configuration and actual listener are **192.168.126.131:8080**, superseding
the earlier loopback-only snapshot. This session preserved that binding. The
unauthenticated API and login page return 401; authenticated attention API,
dashboard, browser login and cookie-authenticated dashboard return 200. The first
probe used `/api/attention`; retry used the actual `/api/attention/latest` route.
No credential was printed or saved in evidence.

Use the private DASH_TOKEN login at `http://192.168.126.131:8080`, or forward the
actual bound address with `ssh -L 8080:192.168.126.131:8080 <server>` and open
`http://127.0.0.1:8080`. Reverify binding if it changes; do not restore an auth bypass.

## Next exact work

1. After September 17 **00:00 UTC**, verify naturally scheduled in-window receipts
   and run the same capture command with a new filename. Reconcile both streams
   using `cohort.scan_coverage`, `receipt_reconciliation`, `orphan_results`, explicit
   gaps and row statuses. Preserve `data/pit-population-20260917/` and both indices.
2. At/after September 18 **00:00 UTC**, capture/replay and review the entire frozen
   window. Distinguish missing coverage from no eligible registrations and unresolved
   labels from measured terminal evidence. Do not repair missed history. Review is
   still required even when the clock says `cut_reached_review_required`.
3. Rerun `./venv/bin/python -m scripts.verify_memory_outcomes --output /tmp/NEW.json`
   once mature: original forecasts September 17 00:00 UTC, newer targets 04:00 UTC;
   original BR/FIL investigation windows 16:00 UTC. Keep exact-version retries.

Reuse the September 16 **20:38:40.140 UTC** freeze, 16-symbol universe, September 17
00:00 to September 18 00:00 UTC exclusive window. No M3.2 search before coverage
review and a separately frozen search protocol. No Gate 2 restart. Complete
accounting remains M8-dependent; strategy-definition versions remain unknown where
absent and live non-trade simulations still require declared costs/notional.
Claude's execution probe remained session-limited; direct implementation used the
owner's existing authorization. No architecture/dependency change required a
roadmap update.
