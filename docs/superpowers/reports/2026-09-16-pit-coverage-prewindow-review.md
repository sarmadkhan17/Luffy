# M3.1 pre-window coverage review — September 16, 21:40 UTC

**Premature for coverage acceptance. M3.1 remains partial; 12/55 accepted.**
Actual UTC was September 16 21:40:47 at runtime verification. The registration
window opens September 17 00:00 UTC and closes September 18 00:00 UTC. The complete
frozen-window review cannot yet be performed. No source implementation was repeated.

## Capture and reconciliation

New [capture](../artifacts/pit-dataset/2026-09-16-2140-coverage-review-capture.json) at 2026-09-16T21:39:55.644000+00:00
replayed successfully with the existing `replay_capture` integrity, dataset and
classification-neutral cohort checks. [Reconciliation](../artifacts/pit-dataset/2026-09-16-2140-coverage-review-reconciliation.json)
retains the full scan coverage, receipt reconciliation, gaps and status fields.
The original declaration, freeze and prior artifacts were reused unchanged.

| Check | Forecast | Investigation |
|---|---:|---:|
| Indexed / exported activation receipts | 1 / 1 | 1 / 1 |
| Missing / pending exports / exports absent from index | 0 / 0 / 0 | 0 / 0 / 0 |
| Observed scans / eligible rows / registrations | 0 / 0 / 0 | 0 / 0 / 0 |
| Neutral cohort rows / terminal results | 0 / 0 | 0 / 0 |

Both indices match. Eligibility, attention/selection and registration reason maps
are empty. There are no observed missing symbols, unmatched registration claims,
orphan results, missing-target receipts or explicit gaps in this pre-window
capture. Unresolved, expired, unavailable, measured and all other terminal classes
have no cohort rows yet. These empty checks do **not** establish zero missed
registrations, complete sampling or population rates. `window_status=not_started`,
`complete_sampling_claim=false`, `search_ready=false` remain explicit.

## Natural scheduling, runtime and owner access

[Runtime receipt](../artifacts/pit-dataset/2026-09-16-2140-coverage-review-runtime.json) verifies active
five-minute watchdog cron, no watchdog-off flag, both hooks enabled since their
original activation, and successful natural consumer runs at September 16
21:40:03 UTC. No consumer was manually invoked. Forecast consumer skipped 16
existing episodes; investigation consumer skipped three. These are pre-window
health observations, not in-window denominator evidence. Investigation health
retains four historical failed invocations; current status is ok, and no historical
failure counter or gap was erased. Attention worker is alive and current.

Journal control is ACTIVE, demo true, three open journal trades; referee and
handoff remain false. Kernel PID 262048 and dashboard PID 297420 were present.
This is not venue position/protection reconciliation. No operational change was made.

Actual listener remains `192.168.126.131:8080`. Unauthenticated API and login page
return 401; authenticated API/dashboard, login POST and cookie-authenticated
dashboard return 200. Credentials/cookies were neither printed nor saved.
Use the existing private DASH_TOKEN login at `http://192.168.126.131:8080`, or
`ssh -L 8080:192.168.126.131:8080 <server>` then `http://127.0.0.1:8080`.

## Exact outcomes and validation

[Exact verifier](../artifacts/pit-dataset/2026-09-16-2140-coverage-review-maturity.json), September 16
21:40:10.440 UTC: 18 forecasts, zero mature, five investigations retained.
Each original forecast still has one `missing_versions_retry`; the two newer
forecasts have none. No outcome was substituted or resolved by this read-only
verification. Original forecast targets mature September 17 00:00 UTC, newer
ones 04:00 UTC; original BR/FIL investigation windows end 16:00 UTC.

No application source changed, so the existing 277 final-source test evidence
(and earlier 268/269 evidence) is reused rather than rerun. New capture replay
and exact-version assertions passed. Documentation whitespace check passed.
Claude CLI execution probe returned a session limit; direct work used the owner's
existing authorization. Sandbox namespace initialization failed; approved external
execution was used. [Troubleshooting](../artifacts/pit-dataset/2026-09-16-2140-coverage-review-troubleshooting.jsonl)
records those limitations and verification results.

## Next exact work

After September 17 00:00 UTC, verify naturally scheduled in-window receipts,
capture to a **new** filename and replay. At/after September 18 00:00 UTC, review
the entire frozen window across both indices, eligibility/selection/registration
reasons, missing registrations, gaps, unresolved/expired and every terminal class.
Rerun exact outcome verification once mature, retaining missing-version retries.
Do not backfill prospective rows or overwrite exports/indices. Keep the original
September 16 20:38:40.140 UTC freeze and 16-symbol declaration.

No M3.2 search until coverage review and a separate frozen search protocol; no
Gate 2 restart. Missing strategy-definition versions remain unknown and complete
accounting remains M8-dependent. No changed trading decision, new candidate,
admission or better-performance evidence resulted. Unrelated work, frozen research,
doctrine and current authenticated access were preserved. No roadmap change.
