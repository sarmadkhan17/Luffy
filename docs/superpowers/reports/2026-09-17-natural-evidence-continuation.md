# Natural accounting and in-window evidence continuation

Actual runtime observed September 17 **00:08:57 UTC**; exact maturity reviewed
**00:12:59 UTC**. M8.1 and M3.1 remain partial, acceptance **12/55**.
No design, trading decision, risk setting, candidate or admission changed.

## M8.1 natural evidence and runtime

The [read-only venue check](../artifacts/execution-accounting/2026-09-16-worker-runtime-000900.json)
verified ACTIVE/demo kernel PID 300976, three journal/venue matches (XRP short
68.9, LINK short 36.24, AAVE short 0.9), each with native reduce-only STOP_MARKET
protection. The inherited script uses a September 16 filename prefix; embedded
September 17 UTC is authoritative. Watchdog remains enabled. Frozen doctrine,
PIT declaration/freeze/dataset and population configuration hashes match.

Zero execution-accounting or normal-booking receipts existed. The scheduled
accounting worker ran naturally at 00:10:03 UTC, status ok, zero jobs/attempts,
no capacity/storage block and automatic memory import false. No natural booking
was available to export/replay; no complete receipt could be imported. No trades
were forced and no reconciler/queue engineering was repeated. The original
legacy entries still lack the provenance needed for complete accounting.

## M3.1 capture and maturity

The new [immutable capture](../artifacts/pit-dataset/2026-09-17-continuation-0010-capture.json)
passed source-free replay. Its [review](../artifacts/pit-dataset/2026-09-17-continuation-0010-review.json)
records two natural scans per stream: 32 observed rows, 30 declared eligible,
six selected/26 ignored scan rows. TAO/USDT is missing in both scans in both
streams; nothing was backfilled. Forecast index/export counts 18/18 and
investigation counts 3/3 match, with no missing/pending/unindexed exports.
These are capture-time counts; later natural passes continue independently.

There are **15 new unresolved forecast cohort rows**, two selected/13 ignored,
one dependence group and zero terminals. Forecast registration reasons: 15
registered, 17 existing episodes; investigation: six existing episodes and 26
not selected. No orphan results or unmatched registration claims. The window
remains open; complete_sampling_claim and search_ready remain false.

The [exact maturity review](../artifacts/pit-dataset/2026-09-17-continuation-0010-maturity.json)
now contains 33 forecasts: 16 mature, 17 immature. Of the mature originals,
15 have resolved ledger records but fail exact-source verification with
`exact_source_version_missing_retry`; one remains `exact_outcome_missing_retry`.
All 16 original missing baseline-version retries persist. No mature outcome
was accepted. Investigation health is **degraded / memory_source_refused**,
with the same 15 explicit refusals; population collection remains enabled and
continues to export. This is an evidence limitation, not permission to bypass
verification or reconstruct missing history.

## Narrow repair and validation

The existing read-only verifier aborted on the first resolved forecast with
missing exact source evidence. It now records only that known missing-data
condition as a retry and continues reviewing the remaining records. Conflicts
and other validation errors still raise. No runtime consumer, source ledger,
terminal outcome or memory-import rules were changed.

Three regression cases test missing baseline, missing target, continued review
of unaffected forecasts and hard refusal of source conflicts. Initial tests
exposed a pre-existing fixture clock dependency: historical synthetic scans were
pruned against actual wall time. The shared test publisher now supplies its
synthetic clock to the store only while publishing. Live clocks and retention
are unchanged. Final-source command:

```bash
./venv/bin/python -m pytest tests/test_verify_memory_outcomes.py tests/test_typed_outcomes.py tests/test_attention_learning.py tests/test_population_cohort.py -q
```

**43 passed in 5.48 seconds.** Source hashes are in the review artifact. Prior
247-test worker and 277-test cohort evidence is retained; those entire suites
were not rerun or represented as new evidence. No service restart was required.
Claude probe remained session-limited; existing direct-implementation authorization
was used. Sandbox namespace initialization failed; approved external execution
was used. [Troubleshooting events](../artifacts/execution-accounting/2026-09-17-continuation-events.jsonl)
retain the failures and recovery without credentials.

## Exact next work and limits

Observe/export/replay the first natural booking and scheduled immutable accounting
attempt. Keep missing evidence/funding retries until a naturally closed trade
with complete entry provenance permits a complete receipt; then verify explicit
forward-only memory import. Do not rebuild delivered accounting components.
Non-USDT conversions, emergency/native/ambiguous provenance and legacy consumer
migration remain unfinished; ask the owner before any design change.

Repeat exact checks as natural evidence arrives. Two forecasts mature September
17 **04:00 UTC**, 15 new forecasts **08:00 UTC**, and original investigation
windows **16:00 UTC**. Review the full population window at/after September 18
**00:00 UTC**, using both indices and all neutral terminal classes. Preserve all
missing-version retries, collection and frozen artifacts. No M3.2 without coverage
review and a separate search protocol, no Gate 2, referee/handoff remain false.
No improved-performance, profitability or complete-coverage claim is supported.
