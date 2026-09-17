# Post-publication accounting review — September 17, 2026

Read-only review. **No production source, config or control was changed.** No
trading, no worker invocation, no memory import, no research or design change.
Every existing artifact was preserved; new evidence went to new files.

Two things were reviewed at the actual UTC clock: the accounting worker's
post-publication outcome for the controlled demo sample `pos_05a5e8f981`, and
the scheduled M3.1 population coverage/replay check.

---

## 1. Accounting for `pos_05a5e8f981` is complete

The seventh worker attempt, captured **2026-09-17 11:05:16.617 UTC**, replays
`complete_as_of_venue_history` with `reasons: []`, `retry_required: false`,
`history_complete: true`, `funding_complete: true`, `learning_eligible: true`.
The queue job is `complete` with 7 attempts and empty reasons; because
`observability/accounting.py` only selects `status != 'complete'`, it will not be
retried again (its stored `due_ms` of 16:25:16.624 UTC is inert).

### Attempt timeline

All seven attempts were re-replayed from the immutable artifacts; queue rows were
read `mode=ro`. There are exactly seven attempt rows — no hidden failed captures.

| # | Attempt id | Captured (UTC) | Trigger | Replay verdict |
|---|---|---|---|---|
| 1 | `ba060305170a493baa00661dd5d620ad` | 02:23:18.812 | manual | `funding_publication_frontier_pending_retry` |
| 2 | `9a6b32a8311e4995ab941e9480179142` | 02:30:08.140 | watchdog | same |
| 3 | `51771f428e9843a28786548e656cb21b` | 02:45:08.350 | watchdog | same |
| 4 | `c91e6105b6df49d48460f69d6a06d7ad` | 03:10:09.613 | watchdog | same |
| 5 | `7e514074134e4287a465c9260d78447e` | 03:55:07.615 | watchdog | same |
| 6 | `661d9dd1550f442883b76ce7b03ddcd7` | 05:20:10.280 | watchdog | same |
| 7 | `a410f33a76614362942117767f59e40a` | **11:05:16.617** | watchdog | **complete** |

The delivered backoff (5/10/20/40/80/160 min) reproduces exactly. Attempts 2–6
each fired **1.7–5.0 minutes** after their `due_ms`, which is the ordinary
five-minute watchdog grid. Attempt 7 was due **08:00:10.286 UTC** and ran
**11,101,364 ms (3 h 05 m 01 s) late** — the one real anomaly, discussed in §2.

### Funding publication evidence

The gate is `trader/engine/trade_accounting.py:193-195`: a funding event must be
published *strictly after* the last fill before the trade's funding can be called
complete. Last fill = **02:20:57.217 UTC**.

- Attempts 1–6: zero funding events in the post-trade page; the newest published
  `fundingTime` was **2026-09-17T00:00:00 UTC**, earlier than the last fill →
  retry. `funding_history` (income) empty.
- Attempt 7: one event — **`fundingTime` 2026-09-17T08:00:00 UTC, rate
  `0.00010000`** — past the last fill → frontier cleared.
- Owned exposure at that 08:00 boundary was **0** (flat since 02:20:57.217), so
  `expected_times` is empty and matches the **zero** actual income rows.
  `funding_net = 0.0`, `funding_complete = true`.

Ownership was independently proven at attempt 7: `overlapping_trade_ids: []`,
venue `positionAmt: "0.00"`, and the fill `orderId` set equals the order set.

### Complete economics (venue-observed, demo)

| Component | USDT |
|---|---|
| Gross realized | **−0.02550000** |
| Fees (USDT) | **0.04035323** |
| Funding | **0.00000000** |
| **Net trade P&L** | **−0.06585323** |

Fills: entry `92812087` (order `4205684574`) BUY 0.51 @ 98.93, commission
0.02018172, 02:20:53.040 UTC; exit order `4205684620` SELL 0.51 @ 98.88 in three
fills `92812100` / `92812101` / `92812102`, commissions 0.00593280 / 0.00435072 /
0.00988799, realized −0.00750 / −0.00550 / −0.01250, all at 02:20:57.217 UTC.

Derived, on an entry notional of **50.4543 USDT**: net **−13.0521 bps**, hold
**4.177 s**, and **61.28 %** of the loss is commission (4 bps taker each way).
The journal's `realized_pnl` for the trade is **−0.06585323** — identical to the
venue-reconciled net, now backed by ownership proof rather than by the
symbol/time-window subtotal the close receipt originally used.

These are **demo** venue numbers for one 4.177-second round trip. They establish
that the accounting path closes end-to-end; they say nothing about any strategy
or edge, and demo volume is not production volume.

### Learning status — and the deferred fix (read this)

`learning_eligible: true` is the **accounting** verdict only. The worker's
`automatic_memory_import` remains **false** and no typed accounting memory was
imported. The trade's `strategy_id` is `diagnostic:owner-demo-probe`.

**Limitation, recorded and deliberately not fixed:** the coordinator's
[legacy-consumer review](../artifacts/execution-accounting/2026-09-17-postpublication-legacy-consumer-review.json)
(11:08:26.197 UTC) found that the diagnostic sample **is** picked up by the
*existing legacy meta-label validation query* — it appears as row **316 of 317**
eligible meta rows, past the training cut of **221**. The latest model there is
`ready=false`, `w=null`, test Brier **0.2701**. The sample carries **0 agent
votes**, so the EWA cursor advanced over it **without any per-agent weight
update**.

The owner explicitly chose to **keep this review read-only** and to record a
diagnostic-sample consumer-exclusion fix **for later**. It is not implemented
here. Scope this precisely:

- This is one legacy meta-label validation consumer, with a not-ready model and
  no weight updates from the sample.
- It is **not** a claim of broad no-learning contamination, and no such claim is
  made or implied by this report.
- The deferred fix is: exclude `diagnostic:*` strategy ids from that legacy
  consumer's eligibility query. Needs owner review before implementation.

---

## 2. Runtime — verification completed, gap root cause unresolved

Runtime verification is **complete**. The **root cause of the scheduling gap
remains unresolved.**

[`2026-09-17-postpublication-runtime-110713.json`](../artifacts/execution-accounting/2026-09-17-postpublication-runtime-110713.json)
(11:07:09.077 UTC) verifies `control_state: ACTIVE`, `demo: true`,
`sol_flat: true`, `protection_and_ownership_verified: true`,
`watchdog_off: false`, `referee: false`, `handoff: false`, the original three
positions still native-stop protected — XRPUSDT −68.9 (`pos_3f5e7f308c`, stop
`1000000206428307`), LINKUSDT −36.24 (`pos_0136aef3df`, stop `1000000206884480`),
AAVEUSDT −0.9 (`pos_57c828f65a`, stop `1000000206882763`) — and the five frozen
`preserved_files_sha256` unchanged, including `data/doctrine.json`.

The **existing watchdog autonomously** restarted PID 300976 at **11:05:08 UTC**
on a heartbeat **14,175 s** stale (threshold 600 s); PID **341703** completed its
first cycle at **11:07:23 UTC**.
[`…-runtime-recovered.json`](../artifacts/execution-accounting/2026-09-17-postpublication-runtime-recovered.json)
shows a fresh heartbeat at **11:08:46.722 UTC**, age **8.75 s**. **No session
restart was issued. The cause of the preceding gap is not established.**

That same gap is what delayed accounting attempt 7 by 3 h 05 m, and it is
independently recorded in the population capture as explicit gap events (§3).

---

## 3. M3.1 population coverage and replay review

Run at the actual UTC clock to new files; all prior artifacts preserved.

### Maturity — `scripts.verify_memory_outcomes`, 11:15:16.650 UTC

**52 forecasts, 34 mature, 18 immature**, `retry_required: true`.

| Verification | Count |
|---|---|
| `exact_source_version_missing_retry` | **32** |
| `exact_outcome_missing_retry` | **2** |
| `not_matured` | 18 |

Status split 32 `resolved` / 20 `pending`; **34** rows carry a raw
`missing_versions_retry`; registration scan envelope retained for only **16** of
52. Selected 7, not selected 45.

- **TAO/USDT resolved.** The target bar (2026-09-16 20:00 UTC) that drove **85**
  consecutive `exact_target_missing_retry` attempts through 07:05:02.211 UTC is
  now `resolved`, though still `exact_source_version_missing_retry` for import.
  A newer TAO forecast (target 12:00 UTC) is immature — and is one of only 16
  rows whose registration scan is retained.
- The two `exact_outcome_missing_retry` rows are **LSK/USDT** and
  **SAMSUNG/USDT**, both target 04:00 UTC with deadline 08:00 UTC — mature for
  over three hours and still unresolved. LSK produced two fresh
  `missing_target_receipts` at 11:10:02.271 and 11:15:01.402 UTC.
- Immature deadlines: **2 at 12:00 UTC**, **16 at 16:00 UTC**.
- Six investigations: 2 due 16:00 UTC (`pre_memory_registration`), 3 due 20:00
  UTC and 1 due **2026-09-18 04:00 UTC** (all `frozen_at_registration`). That
  last one matures **after** the common 00:00 UTC cut. It **must remain visible
  in neutral coverage at the cut as immature/unresolved**; only its post-cut
  terminal outcome is unavailable at the cut. Dropping it from the full-window
  review would bias the denominator.

### Cohort and reconciliation — `scripts.capture_pit_population`, 11:15:51 UTC

Capture `sha256 e2809697cbbfc685d6b0c39c679bac49f56386f11bbcfc259c998140e4d8bcff`;
**`replay_capture` passed** (integrity, freeze validation, event verification,
dataset replay and full cohort re-derivation all reproduced).

**Both local indices match both exports exactly:**

| Stream | Indexed | Exported | Missing | Pending | Absent from index |
|---|---|---|---|---|---|
| forecast | **137** | **137** | 0 | 0 | 0 |
| investigation | **91** | **91** | 0 | 0 | 0 |

Cohort: **30 rows**, all forecast stream, `window_status: open`,
**1 dependence group**, **3 selected / 27 ignored** (`selected` 3,
`below_min_salience` 27), **14 resolved / 16 unresolved**, **14 terminal** (all
14 terminal-known-by-cut), 15 mature at capture, **0 orphan results**.
Coverage: **30 registrations**, **14 terminal results**, 173 population receipts,
228 verified local receipts, `typed_outcome_refusals: []`, **`search_ready:
false`**, reason `requires_window_close_coverage_review_and_separate_search_protocol`.

Scan coverage: forecast 88 scans / 1408 observed rows / 1213 declared eligible /
34 registered / 1374 existing-episode; investigation 85 scans / 1360 rows / 1170
eligible. `unmatched_registration_claims` empty in both.

### Coverage gaps — explicitly recorded, not waived

Seven `explicit_gaps` are now in the capture, so the outage is inside the
evidence rather than inferred from logs:

| Stream | Reason | When (UTC) |
|---|---|---|
| forecast | `collection_cadence_gap` | 07:05:02.211 → 11:05:09.798 |
| forecast | `stale_future_or_wrong_timeframe` | 11:05:09.798 |
| investigation | `collection_cadence_gap` | 07:05:02.572 → 11:05:10.003 |
| investigation | `stale_or_future_scan` | 11:05:10.003 |
| investigation | `collector_unhealthy` | 01:10:01.803, 02:05:01.871, 05:20:02.728 |

Observation gap intervals: forecast **07:05:02.211 → 11:10:02.271**;
investigation **02:00:01.825 → 02:10:02.965** and **07:05:02.572 → 11:10:02.586**.

**Missing declared symbols are broader than TAO.** Every scan in both streams is
missing at least one declared symbol:

| Symbol | Forecast scans missing (of 88) | Investigation scans missing (of 85) |
|---|---|---|
| TAO/USDT | **86** | **83** |
| LSK/USDT | **61** | **60** |
| SUI/USDT | **48** | **47** |

This is a real point-in-time coverage gap against the frozen 16-symbol
declaration and must be carried into the window-close review. It is not resolved
by the fact that TAO's earlier outcome finally matured.

### What this does not establish

Collection being enabled is not complete population sampling; the window is
**open** until **2026-09-18 00:00 UTC**, roughly **12 h 44 m** after this
capture. Classification-selected typed rows cannot establish population rates,
repeated scan rows are not episodes, and `search_ready` stays **false** — no M3.2
search and no Gate 2 follow from this review.

---

## 4. Tests

**251 focused tests passed, 15.50 s** — `tests/test_accounting_worker.py`,
`test_whole_trade_accounting.py`, `test_execution_accounting.py`,
`test_trade_booking.py`, `test_real_funding.py`, `test_verify_memory_outcomes.py`,
`test_pit_population.py`, `test_population_cohort.py`, `test_pit_dataset.py`,
`test_emergency_whole_accounting.py`,
`test_capture_emergency_whole_accounting_cli.py`, `test_pit_alignment.py`,
`test_population_spec_path.py`, `test_spec_population.py`. Output:
`docs/superpowers/artifacts/execution-accounting/2026-09-17T111500Z-postpublication-review/focused-tests.txt`.
No failures; no unrelated failures were repaired. This is a targeted run, not a
clean-full-suite claim.

## 5. Artifacts

New (nothing overwritten):

- `docs/superpowers/artifacts/execution-accounting/2026-09-17T111500Z-postpublication-review/`
  — `README.md`, `worker-attempts/` (7 attempts + `health.json`, byte copies),
  `accounting-replay-review.json`, `focused-tests.txt`, `troubleshooting.jsonl`,
  `postpub_review.py`, `pit_review.py`.
- `docs/superpowers/artifacts/pit-dataset/2026-09-17-postpublication-maturity.json`
- `docs/superpowers/artifacts/pit-dataset/2026-09-17-postpublication-capture.json`
- `docs/superpowers/artifacts/pit-dataset/2026-09-17-postpublication-review.json`

Coordinator-supplied:
`…/execution-accounting/2026-09-17-postpublication-runtime-110713.json`,
`…-postpublication-runtime-recovered.json`,
`…-postpublication-legacy-consumer-review.json`.

Sources left untouched: `data/accounting-worker/` (7 attempts, `health.json`,
`queue.db` — read `mode=ro` only), `data/pit-population-20260917/`, both local
population receipt indices, all earlier pit-dataset and execution-accounting
artifacts.

## 6. Unresolved, and the next exact work

1. **Deferred by owner decision:** exclude `diagnostic:*` strategy ids from the
   legacy meta-label validation consumer's eligibility query. Recorded, not
   implemented, needs owner review.
2. **Runtime verification is completed; the root cause of the 07:05 → 11:05 UTC
   gap is unresolved.** No watchdog passes or receipts are recorded during that
   interval; watchdog recovery afterwards worked autonomously and is verified.
3. **M3.1 full-window review is due 2026-09-18 00:00 UTC**, with both indices and
   all neutral terminal classes. Carry forward: the TAO/LSK/SUI missing-symbol
   coverage gap, the seven explicit gaps, the two stuck
   `exact_outcome_missing_retry` rows (LSK, SAMSUNG), the 32 rows still missing
   exact source versions, and the one investigation maturing after the cut —
   which stays in neutral coverage as immature/unresolved at the cut.
4. Natural (non-diagnostic) complete receipt plus explicit forward-only memory
   import remains the open M8.1 step. The controlled sample proves the path; it
   is not a natural trade.

## 7. Statements of record

- **Trading decisions did not change.** No orders, no control-state change, no
  restart from this session. `research.referee=false`, `research.handoff=false`
  and the recorded dependence-corrected gate stop are untouched.
- **No new strategy candidate was produced.**
- **No better-performance claim is made.** A −0.06585323 USDT demo round trip is
  execution/accounting evidence, not performance evidence.
- **No memory import occurred** from this review, and the diagnostic sample must
  not enter strategy performance learning.

M8.1 remains partial. M3.1 remains partial. Acceptance count remains **12/55**.
