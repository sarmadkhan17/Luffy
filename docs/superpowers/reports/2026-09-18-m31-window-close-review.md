# M3.1 window-close review — September 18, 2026

Read-only review on `main` `b6cc08a`. Evidence was captured from **01:17:05 UTC**;
the final documentation corrections were completed at **01:26:01 UTC** (clock
checked before writing). **No production source, config or control was changed.**
No worker invocation, no memory import, no trading or restart, no M3.2 search and
no Gate 2. The frozen declaration and every earlier artifact are untouched; new
evidence went to new files only. `research.referee=false` and
`research.handoff=false` (checked in `config.yaml`).

**Verdict:** the full-window review is **complete**. The frozen window's coverage is
**not accepted** (§4). M3.1 remains partial. M8.1 still has no natural complete
receipt.

---

## 1. Integrity

| Check | Result |
|---|---|
| Maturity `scripts.verify_memory_outcomes` | 01:17:05.098 UTC: 55 forecasts, **55 matured** (deadline passed), **33** `exact_source_version_missing_retry`, **22** `exact_outcome_missing_retry`, `retry_required: true` |
| Capture `scripts.capture_pit_population` | 01:17:05 UTC (`observed_capture_ms` 1789694225284), `sha256 d95e8e1a…7c87100` |
| `replay_capture` | **passed** |
| Freeze vs 2026-09-17 capture | **identical** |
| Five frozen hashes vs [runtime 11:07:09 UTC](../artifacts/execution-accounting/2026-09-17-postpublication-runtime-110713.json) | **all match**: `doctrine.json`, `pit_population.json`, forward dataset, declaration, freeze |
| Events whose available/import clock is after the cut | **0** of 570; latest clock **23:55:02.366 UTC** |
| Independent coordinator at-cut replay | [coordinator-cut-verification](../artifacts/pit-dataset/2026-09-18-coordinator-cut-verification.json), 01:19:06.800 UTC: a pure at-cut replay reproduces 14 resolved / 16 unresolved forecasts and 1 immature investigation (BTC), 0 post-cut events excluded, reconciliation matched, **257 pre-existing files preserved** (`changed_preexisting_files: []`) |

**Both local indices match both exports.**

| Stream | Indexed | Exported | Missing | Pending | Absent from index |
|---|---|---|---|---|---|
| forecast | **331** | **331** | 0 | 0 | 0 |
| investigation | **239** | **239** | 0 | 0 | 0 |

## 2. At-cut view vs capture view

`trader/observability/cohort.py:89-99` sets a row's `status` from its latest event
at **capture** time. `mature_at_capture` also uses the capture time. Only
`terminal_known_by_cut` refers to the cut. The at-cut view was re-derived by the
[review helper](../artifacts/pit-dataset/2026-09-18-window-close-review.py) and
independently by the coordinator, in both cases using only events whose available
and local-import clocks are both at or before 2026-09-18 00:00 UTC.

No row's status or maturity differs between the at-cut view and the capture view,
for two reasons. First, the capture holds **no events after the cut**. Second, **no
registered deadline falls between the cut (00:00) and the capture (01:17)**: the
forecast deadlines are 08:00 and 16:00 on September 17, and BTC's is 12:00 on
September 18. The builder does not guarantee this equality in general.

### Frozen registered denominator (in-universe registrations): 31 rows

| Stream | Class at cut | n |
|---|---|---|
| forecast | terminal resolved (known by cut) | **14** |
| forecast | mature, **unresolved, overdue** | **16** |
| forecast | terminal unavailable | 0 |
| investigation | **immature, unresolved** | **1** |

- Forecasts: 3 selected and 27 ignored (`below_min_salience`). 1 dependence group, 0
  orphan results. `window_status: cut_reached_review_required`,
  `search_ready: false`, `complete_sampling_claim: false`.
- The 14 resolved rows are the 00:05 UTC batch (deadline 08:00). A later
  observation (01:17) shows every one still refusing import with
  `exact_source_version_missing_retry`.
- The 16 overdue rows:
  - **LSK/USDT** (00:05 batch): the target bar at 04:00 is missing; there are
    **50** `exact_target_missing_retry` receipts, the last at 15:20:02.
  - **15 rows from the 11:10 batch** (deadline 16:00): no scan or outcome
    processing is recorded after 15:20. At 01:17 all 15 are still
    `exact_outcome_missing_retry`.
- **BTC `investigation_ec2bad86c8f4189e`** (in universe) was registered
  12:05:01.668 and matures **2026-09-18 12:00 UTC**. Its one update before the cut
  is at 12:10:01.625. It is **immature/unresolved at the cut** and stays in the
  denominator.

### Typed dataset in the capture

The capture's typed dataset has only **14 rows**: 12 `ignored_forecast` and 2
`selected_forecast`, all `original_after_freeze`. The expected kinds
**`false_signal`, `regime_transition` and `skip` are absent**. It has **0 sequence
rows**. Terminal-row symbols **LSK/USDT and TAO/USDT are missing**.

Its reason codes include `declared_symbol_absent`, the three `kind_absent:*` codes,
`code_version_changed_between_freeze_and_capture`,
`retention_bias_bounded_retained_store`, `retention_unmeasured` and
`cut_reached_not_search_ready`. It supports no population rates.

### Supplemental neutral coverage (not in the frozen denominator)

- **BZ `investigation_3864bce371354aac`** was registered 04:05:02.618. BZ is outside
  the frozen universe; the measurement targets 16 symbols, including BZ, INTC and
  SAMSUNG. It matures **2026-09-18 04:00 UTC** and had 4 updates, all before the
  cut. It is **immature/unresolved at the cut**. It is shown here and in the review
  JSON; the frozen denominator is not altered. This is the "04:00 investigation"
  named in the September 17 report.
- **Seven in-window forecasts outside the universe** are excluded from the cohort:
  SAMSUNG (02:15), BZ and INTC (04:05), DASH (11:10), and QQQ, NVDA and BZ (15:10).
  With them, 30 + 7 = 37 matches the scan count of 37 `registered` rows.
  **Correction:** the September 17 report's "stuck pair LSK/SAMSUNG" mixed an
  in-universe row with an out-of-universe one. SAMSUNG belongs here, as
  supplemental coverage.
- **Five pre-window investigations** (BR, FIL, NEAR, AAVE and INTC; deadlines 16:00
  or 20:00 on September 17) are mature and unresolved at the cut. They are
  context only.

## 3. Collection coverage over the full frozen window

"Gap share" below is the **sum of observation-gap intervals longer than ten
minutes, divided by 24 h**. It is not a precise measurement of every missing
observation: shorter gaps and individually missed passes are not counted, and the
number of missed registrations is unknown.

| Stream | Scans (first → last) | Gap share | Gap intervals >10 min (UTC, Sep 17 → 18) |
|---|---|---|---|
| forecast | 136 (00:00:01 → **15:20:02**) | **53.1 %** | 07:05:02 → 11:10:02; **15:20:02 → 00:00** |
| investigation | 134 (00:00:01 → **15:20:02**) | **53.8 %** | 02:00 → 02:10; 07:05 → 11:10; **15:20:02 → 00:00** |

Explicit gap receipts:

- **forecast:** 97 `collector_unhealthy` (one at 13:25, then **96** from 16:00:02
  to 23:55:02), 2 `collection_cadence_gap` (07:05 → 11:05 and 15:20 → 16:00), and
  1 `stale_future_or_wrong_timeframe`.
- **investigation:** 99 `collector_unhealthy` (three earlier, then 96), 2 cadence
  gaps, and 1 `stale_or_future_scan`.

**Present refusal condition.** This is established by the coordinator's
[collector-health review](../artifacts/pit-dataset/2026-09-18-collector-health-review.json)
(01:20:18 UTC) and by source inspection.

- The collector publishes `errors = capture_errors + worker_errors` as a cumulative
  count (`trader/observability/collector.py:106, 151, 154`).
- Both consumers refuse any non-zero `errors`
  (`trader/observability/learning.py:124-127`,
  `trader/observability/investigation.py:208-211`).
- Current health reads `status: ok`, `last_error: null`, `worker_alive: true` and
  `errors: 74`. A current `ok` does not clear the cumulative count, so both
  consumers **currently** refuse.

This explains the present refusal. It does **not** establish:

- what caused the original 74 worker errors, or when they happened;
- the exact cause of the outage that began at 16:00;
- the cause of either watchdog pass gap (07:05 → 11:05 and 15:20 → 16:00).

All three remain **unresolved**. `logs/watchdog.log` records only the 11:05 UTC
restart.

**Missed registrations.** Neither stream has unmatched registration claims. But
the 16:00 and 20:00 4h-bar cycles fall inside unobserved time, and the 08:00 cycle
did too until the late 11:10 batch. Registrations those scans would have produced
are **unobserved: the count is unknown, not zero**.

**Missing declared symbols.** Every scan misses at least one declared symbol (LSK,
TAO or SUI). The counts show it is not all three every time:

| Symbol | Forecast scans missing (of 136) | Investigation scans missing (of 134) |
|---|---|---|
| LSK/USDT | 109 | 109 |
| TAO/USDT | 89 | 86 |
| SUI/USDT | 51 | 50 |

## 4. M3.1 coverage acceptance: not accepted

The review of the frozen window is complete. Its coverage is **not accepted** as
population evidence, because:

- more than half of the window lies inside observation-gap intervals longer than
  ten minutes;
- every scan misses at least one declared symbol;
- 16 of 30 forecast rows are overdue with no terminal outcome at the cut;
- the only in-universe investigation is immature;
- the typed dataset lacks three declared kinds and all sequences;
- registrations after 15:20 are unobserved.

The frozen declaration is not changed, extended or backfilled. `search_ready`
stays false; no M3.2 search and no Gate 2 follow. Reaching the cut is a clock
fact, not coverage.

### Builder limitations recorded (not implemented)

1. `cohort.py` reports status and maturity as of capture time. It has no explicit
   at-cut status or maturity.
2. There is no `overdue` or `expired` class. A mature row with no terminal event is
   `unresolved`, the same as an immature one.
3. Out-of-universe registrations never reach the cohort: `cohort.py:87` raises on
   them. The builder produces no supplemental neutral coverage.
4. Scan `registration_reasons` counts all rows (37), while the cohort counts only
   in-universe rows (30).
5. The verifier's "matured" means the deadline has passed, not that the row
   resolved. Of 55 matured rows, 33 are source-version retries and 22 are outcome
   retries.
6. Consumer health is refused on the cumulative collector error count, as
   described in §3.

## 5. M8.1: read-only queue and journal check

The queue was read with `mode=ro`, and the latest attempt per trade was replayed:
[accounting check](../artifacts/execution-accounting/2026-09-18-window-close-accounting-check.json).
The worker health embedded in that artifact was updated at **01:20:03.363 UTC**:
`complete` 1, `retry` 2, `waiting_close` 3, `automatic_memory_import: false`.

| Trade | Journal | Worker | Replay of latest attempt |
|---|---|---|---|
| `pos_05a5e8f981` SOL (controlled, `diagnostic:owner-demo-probe`) | closed | complete, 7 attempts | `complete_as_of_venue_history`, net −0.06585323. **Not reopened.** |
| `pos_0136aef3df` LINK short, stop fill 12:21:52 | journal −17.78699789 | retry, **8** attempts | `incomplete`, `legacy_entry_receipt_missing_retry`, not learning-eligible |
| `pos_57c828f65a` AAVE short, stop fill 14:13:04 | journal −6.3074196 | retry, **7** attempts | same |
| `pos_f8ff76b0e1` NEAR long, opened Sep 17 12:00 | open | `waiting_close` | none |
| `pos_be9ea9069e` ZEC long, opened Sep 17 16:00 | open | `waiting_close` | none |
| `pos_bfc25f8c23` UNI long, opened Sep 17 20:00 | open | `waiting_close` | none |

There is **no natural non-diagnostic complete receipt**, so nothing was copied or
imported.

For LINK and AAVE, the current reconciler requires the first booking receipt to be
an entry (`trade_accounting.py:36-37`). Both trades opened on September 16, before
per-trade entry receipts existed. Under the current reconciler, the passage of time
or further publication **cannot clear** this refusal. Whether separately reviewed
legacy evidence could account for them is left open.

Journal P&L values are not venue-reconciled and support no performance claim. The
XRP position `pos_3f5e7f308c` (opened September 15) is still open and has no queue
job. The last kernel heartbeat seen (01:18:23 UTC) showed `state: ACTIVE`; venue
protection was not re-verified this session. The diagnostic-consumer exclusion
remains owner-deferred.

## 6. Tests and validation

No source change was made and no tests were rerun. The owner-supplied merge
validation is **validation only**: 2015 tests passed, the five failures reproduce
on the pre-merge tree, backtest equivalence passed, and the benchmark measured
373.9x. It is **not** evidence of runtime deployment and **not** evidence of
trading performance.

## 7. Artifacts (new; nothing pre-existing overwritten)

In `docs/superpowers/artifacts/pit-dataset/`:

- `2026-09-18-window-close-maturity.json` (`9ff4d612…`)
- `2026-09-18-window-close-capture.json` (file `462f3e71…`, internal sha `d95e8e1a…`)
- `2026-09-18-window-close-review.json` (`ecd7f6cd…`; generated 01:20:50.579 UTC)
- `2026-09-18-window-close-review.py`: the review helper. After the recorded run, an
  I/O guard was added; the review logic is unchanged. The helper now **requires
  two fresh output paths and refuses to overwrite existing files**, so never run
  it without them. Current sha `275b6d86…`. The recorded outputs came from the
  pre-guard version (`8295c2ef…`), which wrote to fixed paths.
- `2026-09-18-window-close-troubleshooting.jsonl`
- Coordinator: `2026-09-18-coordinator-cut-verification.json`,
  `2026-09-18-collector-health-review.json`

In `docs/superpowers/artifacts/execution-accounting/`:

- `2026-09-18-window-close-accounting-check.json` (`e77e6bf9…`)

## 8. Exact next step

1. **Bounded read-only investigation**, which needs no special permission: the
   attention worker errors (when and why the 74 occurred), the watchdog pass gaps,
   and the collector's recovery/health semantics. Preserve the current refusal and
   all old evidence.
2. The output of that investigation is a **concrete proposed fix, submitted for
   owner review before any implementation**. The proposal must keep the current
   safety contract. It must not weaken acceptance to a status-only check.
3. Separately from the fix, freeze a **new** forward window; this one stays as
   recorded. Before any coverage acceptance, decide how LSK/TAO/SUI availability
   will be handled.
4. **M8.1:** observe NEAR, ZEC and UNI through their natural closes; each has an
   entry recorded after entry receipts existed. Then verify explicit forward-only
   memory import. LINK and AAVE cannot clear under the current reconciler.
5. Outcomes for BZ (04:00) and BTC (12:00) are later observations only. They do not
   change the at-cut view.

## 9. Statements of record

No trading decision changed. No new candidate was produced. No better-performance
claim is made. The acceptance count remains **12/55**, with M3.1 and M8.1 partial.
