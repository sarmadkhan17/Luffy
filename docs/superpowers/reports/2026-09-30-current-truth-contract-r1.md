# LUFFY-CURRENT-TRUTH-CONTRACT-R1

Date: 2026-09-30. Implementer: Claude (delegated worker). Base: `b86d772c`.
The change is uncommitted, and there was:
- no deploy or restart;
- no venue or production-DB access;
- no frontend change;
- code-only `graphify update .` completed by the coordinator after implementation (no semantic extraction).

This revision includes:
- the coordinator's review corrections 1–8 (`/tmp/current-truth-r1-review.txt`);
- the final-review integration completion A–D (`/tmp/current-truth-r1-final-review.txt`; see "Final-review completion").

**Verdict: IMPLEMENTED_FOR_REVIEW. The final-review blockers are resolved in code and tests:** the legacy partial aggregate (A) and the provenance race (B), plus old-schema compatibility (C) and generated-file hygiene (D). This is **not** an activation-readiness claim. The kernel producers (account, Risk and News records, and equity-row provenance) write data only after a restarted kernel runs this code. Until then, the owner surfaces report those records as missing or unproven.

## Files

New:
- `trader/core/truth.py`: strict time parsing and freshness (fresh | stale | invalid | unavailable), `display_age`, `is_after`, `finite`, `digest`.
- `trader/dashboard/current_truth.py`: owner readers for account (plus per-row provenance), News Guard, and Risk (current, baseline and configured).
- `trader/dashboard/valuation.py`: a pure unrealized-P&L estimate.
- `tests/test_current_truth_contract.py`: 127 tests.
- `tests/test_current_truth_corrections.py`: 65 tests.
- `tests/test_current_truth_integration.py`: 24 tests (final review A–C).
- `tests/test_current_truth_completion_clock.py`: 25 tests (read-completion clock and current-status naming); `tests/test_current_truth_corrections.py` now has 65.
- This report.

Modified:
- `trader/kernel.py`: account observation, equity-row provenance, Risk assessment, News publication and the `/news` text.
- `trader/core/journal.py`: `equity_provenance` table, four partial indexes, and `log_equity(..., provenance=)`.
- `trader/engine/risk.py`: `policy_from_config` and `RiskManager.policy()`, both returning exact `effective`, a display `limits` view, and `digest`.
- `trader/agents/news_guard.py`
- `trader/brain/scraper.py`: `fetch_feed`, `_parse_feed`, and a per-item `published_status`.
- `trader/data/feed.py`: `ticker_quote`.
- `trader/dashboard/owner_api.py`
- `trader/dashboard/owner_reads.py`
- `trader/dashboard/server.py`
- `trader/api/graphql_schema.py`
- `tests/owner_frontend_fixture.py`: seeds the corrected records and fakes `_position_quotes`.
- `tests/test_scout_upgrades.py`: its 3 NewsGuard tests now patch `fetch_feed`.

## Schema / migration

All additions are additive and are created **only by normal `Journal` initialization** (the `SCHEMA` executescript). Nothing was applied to any live database.

- **`equity_provenance(ts PRIMARY KEY, provenance TEXT)`**
  - It is a side table keyed by the equity row's `ts` and written in the same transaction as the row.
  - The `equity` table itself is unchanged, because existing writers insert 4 positional values.
  - A row without provenance has unknown origin. `log_equity` without provenance deletes any stale provenance for that `ts`.
- **Four partial indexes on `decisions(ts)`**: `idx_decisions_executed_ts` (executed=1), `_directional_ts` (action≠HOLD), `_skipped_ts` (directional and not executed), `_rejected_ts` (skipped with a non-empty `skip_reason`).
- **New `state_kv` keys**: `account_observation` (schema 2) and `risk_assessment` (schema 2). `news_guard_state` is extended; its legacy keys `active`, `why` and `ts` are kept.

## Backend contracts (as corrected)

1. **Account provenance.**
   - `account_observation` has `status`: `FRESH`, `VENUE_FALLBACK` (ccxt USDT total), `JOURNAL_FALLBACK` or `UNAVAILABLE`.
   - Other fields: `value`, `basis`, `attempted_at` (start), `observed_at` (source time of the value), `recorded_at`, `successful_read_at`, `fallback` (`row_written_at`, `row_provenance`, `value_origin_read_at`, `value_origin_basis`), `authoritative`, `consecutive_failures`, `attempt_errors` and `reason`.
   - A successful read is timed at the read's **completion** (`_balance_read.completed_at`), bounded to [attempt, record].
   - A journal fallback takes its source time **only from the reused equity row's own provenance record**, and only if that record describes the row's exact value. Equal numbers are never treated as provenance. A legacy row, or a row whose provenance describes another value, gives `observed_at: null` with `fallback_row_provenance_unknown`.
   - Every cycle's equity row gets provenance: `venue_observation`, `fallback_reuse` (which keeps the original `observed_at`), `unknown_origin_reuse` or `no_value`. A repeated fallback therefore never presents a new valuation observation.
   - The reader enforces:
     - `authoritative` must match the status;
     - the value must be finite and positive, or `null` only for `UNAVAILABLE`;
     - `attempted_at` and `recorded_at` are required;
     - a fresh read has `attempted ≤ observed = successful ≤ recorded`;
     - a fallback's `observed_at` and `successful_read_at` are no later than the attempt, and the fallback row is required;
     - any time later than the reader's clock is invalid.
   - The equity series (`/overview.equity_series.points`) and GraphQL `equity_curve` expose `row_kind`, `basis` and `source_observed_at`. `time`/`ts` remains the row write time.
2. **Unrealized P&L** (`GET /owner-api/v1/valuation`, and the quote path of `/enrichment/marks`).
   - Each trade is valued separately, so two trades on one symbol are two positions.
   - A local `received_at` is required. The ticker `source_ms` must not be later than the receipt, and nothing may be in the future. A missing receipt, or a source time whose relationship to the receipt is unproven, makes the position unavailable.
   - Zero quantity is valid (an estimate of exactly 0.0), and so is P&L at the entry price.
   - The total is `COMPLETE` only when every position is OK. It is computed with `fsum`; an overflow (finite parts, infinite sum) gives `UNAVAILABLE` / `aggregate_not_finite`, never infinity. A partial sum is never published.
   - Labels: "not the exchange mark price"; `fees_funding: EXCLUDED_UNPROVEN`.
   - The **legacy mark provider without quote times** now publishes nothing: `observed_at: null`, `marks: {}`, `unvalued`, `error: marks_provenance_unavailable`. The request time is never used as a source time.
3. **Current Risk observation.** The kernel writes `risk_assessment` each cycle after the entry gate, using the existing `update_equity` result and RiskManager's formulas. There is no new algorithm and no behavior change.
   - A book field (entry, notional, stop) that is missing, malformed, negative or non-finite makes `portfolio_heat` and `total_margin` `not_evaluated` with `book_malformed:<trade>:<field>`. It is never treated as 0. A missing or 0 stop still uses the existing unprotected 5 % formula.
   - Input freshness is recorded explicitly (`equity.age_at_assessment_s`, `fresh_at_assessment`, `book.read_at`). `PASS` requires a fresh authoritative venue equity input (≤ 300 s) and every constraint evaluated; otherwise the status is `DEGRADED`, `BLOCK` or `UNAVAILABLE`.
   - The record includes `baseline` (peak, day start, day key) and `policy.effective`.
   - The reader requires:
     - all 8 named constraints, each exactly once, with results in {`pass`, `block`, `not_evaluated`, `applies_at_entry`};
     - finite limit and observed values for any `pass`/`block`;
     - `PASS` with no `block`/`not_evaluated` and with fresh authoritative equity;
     - equity `observed_at` no later than `assessed_at`;
     - drawdown and daily P&L reproducible from the recorded baseline and equity (±0.011);
     - a policy digest equal to SHA-256 of `effective`.
   - A `PASS` whose equity source is no longer fresh *now* is reported as `STALE` with `last_reported`, and publishes no numbers.
4. **Configured limits.** `/risk.configured` is the dashboard's config parsed exactly as `RiskManager.__init__` parses it.
   - The identity is `digest` over the **exact, unrounded effective values**, so policies that differ anywhere get different digests. `limits` is only a rounded display view.
   - A non-finite or non-numeric value is refused (`ValueError` → `risk_config_unreadable`).
   - `policy_match` compares this digest with the kernel's recorded digest.
5. **News truth.**
   - `fetch_feed` returns an explicit ok/`error_code`, `http_status` and `completed_at`. Each item carries `published_status` (`ok`/`missing`/`malformed`).
   - NewsGuard truths:

     | Truth | Meaning |
     |---|---|
     | `ARMED` | the rule is met by dated, current, non-future headlines |
     | `UNCERTAIN` | the rule is met only via undated, malformed or future-dated items (still dampened, exactly as before), or it is not met while such items exist |
     | `QUIET` | ≥1 dated in-window item, nothing uncertain, no failure — the **only clear** |
     | `FEED_STALE` | a successful fetch in which every item is outside the window |
     | `EMPTY_FEED` | a successful fetch with no entries |
     | `FETCH_FAILED` / `PARSE_FAILED` | the feed could not be fetched or parsed |
     | `ASSESSMENT_FAILED` | the feed was read but assessing it raised (new) |
     | `DISABLED` | the guard is switched off |

   - A publication later than the fetch's completion, by any amount, is future-dated. There is no 300 s allowance.
   - Dampening behavior is unchanged: `active` uses the old rule and fail-open is kept, with no veto. Behavioral tests show `decide()` changes by exactly +0.08 threshold and ×0.75 score, once per decision, for both `ARMED` and `UNCERTAIN`-active records, and not at all for failures.
   - The record includes `fetched_at`, `dated_hits`, `dated_severe`, `items_total`, `dated_in_window`, `undated_items`, `malformed_dates` and `future_dated_items`.
   - The reader enforces that:
     - counts nest (severe ≤ hits ≤ considered ≤ total, with the dated subsets inside) and `min_headlines` ≥ 1;
     - `active` agrees with the recorded counts;
     - each truth agrees with its evidence;
     - times are ordered attempted ≤ fetched ≤ assessed ≤ published;
     - a publication later than the fetch appears only when recorded as future-dated.

   Any contradiction makes the record `UNAVAILABLE`.
6. **Freshness.**
   - Any source time later than the reader's clock, even by 0.01 s, is `invalid`.
   - The comparison uses unrounded times; a future age is never rounded to 0.0.
   - There is no skew constant in `core.truth` or in `owner_api`, and the protection reader's `max(age, 0)` was removed.
   - Malformed times are `invalid` (including a present but malformed heartbeat timestamp); missing times are `unavailable`.
7. **False defaults** (unchanged from R1):
   - GraphQL `status`/`news_guard` nullable fields.
   - The legacy summary and company cockpit show `UNKNOWN` control and never read "clear" without evidence.
8. **Exact latest records.**
   - Filtered latest reads use the partial indexes with `INDEXED BY`, so a missing index fails loudly instead of scanning. `ORDER BY ts DESC, rowid DESC LIMIT n`.
   - There is **no lookback bound**: a test finds a 400-day-old rejection behind 250 000 newer rows. The `MAX`/`MIN(rowid)` aggregate was removed.
   - The canonical summary `overview/activity.decisions.executed/skipped` is now exact over all decisions. The newest-500 window feeds only the block counts, and `decisions_basis` says so.
   - `/operations/activity` and `/overview/activity` embed `latest`.
   - `latest_venue_filled` is replaced by:
     - `latest_journal_execution`, with `fill_evidence` `VENUE_FILL_VERIFIED` or `JOURNAL_BOOKED_UNVERIFIED`;
     - `latest_proven_fill`, which is `PROVEN` only when that execution's booking receipt verifies exact fills, and otherwise `UNKNOWN` (`latest_journal_execution_not_verified`).
9. **Protection** (unchanged from R1):
   - `UNAVAILABLE` without a snapshot.
   - Stale Supervisor checks are `null`, with `last_reported_checks`.
   - `naked_exposure` has a value only from a fresh, complete snapshot.
   - No venue work.

## Final-review completion (A–D)

**A. Legacy owner paths**

- **`/api/summary`** (in `trader/dashboard/server.py`):
  - `equity` now comes from `current_truth.read_account`, with an `account` block giving status, basis, `observed_at` and freshness. It no longer uses the latest row's write time.
  - `equity_prev` carries `equity_prev_provenance`.
  - `total_upnl` now uses the shared `valuation.estimate` via `_position_quotes`. It is `null` unless the status is `COMPLETE` (or `NO_POSITIONS` → 0.0), and also `null` if the valued position set differs from the open set (`position_set_changed`).
  - Each open position gains `valuation_status`, `valuation_reasons` and `quote_observed_at`. `mark`/`upnl`/`upnl_pct`/`sl_dist`/`tp_dist` are set only for OK positions.
  - `valuation` is summarized, including the fees/funding exclusion.
  - `assets_total` is `null` when there is no snapshot or any asset has no USD value; it no longer defaults to 0 or a partial sum.
  - `long_exposure`/`short_exposure` are the full journal entry notional (`null` if any is unusable), labelled `exposure_basis`. Previously they were a subset of marked positions.
- **`/ws/live`**: `equity` keeps its list shape, but now carries status, basis, `observed_at` and freshness, and is `[]` when the account is unknown, with `account_error`.
- **Company cockpit**:
  - Equity comes from the account observation (`—` when unknown, not $0). This also fixes a bug I introduced: my `acc` variable shadowed the function's agent-accuracy `acc`.
  - Day P&L is 0 only when there were no closes, and `—` when any close has no booked P&L.
  - The risk card adds a `["Risk", <current assessment status>]` stat beside the configured-cap heat.
- **`_today_stats.realized_pnl_today`** follows the same rule as Day P&L (0 / `null`).
- **GraphQL `status.heartbeat_age_s`** is `"invalid"` for a future or malformed heartbeat. The path is now the `HEARTBEAT_PATH` constant, so tests never touch the real `data/`.
- **Not present:** there is no `/api/equity` route in this codebase. The historical series is `/owner-api/v1/overview.equity_series` plus GraphQL `equity_curve`, and both carry provenance.

**B. Fallback provenance race** (in `trader/kernel.py`)

- `_last_equity_fallback` captures the exact row (ts, value and provenance) in one `LEFT JOIN` read (`_fallback_row`). Its returned value is unchanged. `_record_account_observation` uses that captured row and never re-queries.
- A test lands a same-value row with other provenance between the read and the publication. The observation still names the reused row and its own origin.
- `_account_obs` is cleared before each cycle's observation is built. `_equity_provenance` also requires `obs.attempted_at == _risk_attempt_at`, the current cycle's attempt. A failed build, or an earlier cycle's equal-value observation, therefore yields `None` (unknown).
- The trading values (`balance` and the Risk input) are unchanged, and a test shows this.

**C. Pre-migration journals**

- `current_truth.has_provenance_table` and `equity_rows_sql(provenance=...)` select `NULL provenance` when `equity_provenance` is absent. `read_equity_series`, GraphQL `equity_curve`, the legacy account read and `/api/summary` then serve old history as `unknown`.
- Exact reads check that the partial index exists and raise `ExactIndexMissing` if not.
  - `latest_activity` then reports `{found: null, status: UNAVAILABLE, reason: exact_index_missing:<idx>}`.
  - `overview/activity` executed/skipped become `null`, with an `unavailable` entry.
  - The route answers 200, never 500.
- Tests use a real old-schema SQLite file opened with `mode=ro`, with no `Journal` initialization. They show:
  - no table or index is created;
  - the file hash is unchanged;
  - history reads as unknown provenance;
  - exact reads return `UNAVAILABLE`.

**D. Generated files**

- `knowledge/00 Company/Company.md`, `Manager.md` and `Theorist.md` were restored to HEAD (`git checkout HEAD -- <3 files>`).
- The 1326-test focused regression afterwards left them clean. The generating test belongs to the earlier broad run; it was not identified, and it is not in the focused set.
- The pre-existing dirty knowledge files (`Donchian_Breakout_Trail.md`, `MOC.md`) and the frontend files were left as they were.
- All 301 frontend files in `/tmp/current-truth-r1-frontend-before.json` match their recorded hashes. The unrecorded files are gitignored or pre-existing build and evidence output (`dist`, `.demo-dist`, `.test-dist`, `evidence/m4`, `test-results`, plus the pre-existing untracked m4 sources), last modified at 02:27, before this pass; none were touched.

## Final blocker: read-completion clock (reproduced by the reviewer)

**Defect.** `_record_account_observation` replaced a successful read's `completed_at` with the record time whenever the completion fell outside [attempt, record]. A future completion (+60 s) was therefore persisted as roughly "now" and read as fresh.

**Fix** (in `trader/kernel.py` and `trader/dashboard/current_truth.py`):
- **Producer:**
  - A numeric, finite completion is kept **exactly** as `observed_at` = `successful_read_at`, with `completion_relation` set to `ok`, `completion_before_attempt` or `completion_after_record`.
  - Missing completion metadata gives `observed_at: null` (`completion_missing`), and non-numeric, non-finite, bool or overflowing metadata gives `null` (`completion_malformed`). The publication time is never substituted.
  - Status, value, `authoritative` and `risk_input` are unchanged, so the Risk and execution semantics are unchanged.
  - The Risk assessment's input freshness now also requires `completion_relation == "ok"`, so an impossible or unknown clock cannot support `PASS`.
  - The equity-row provenance carries `completion_relation`. A `venue_observation` row with a bad clock is never carried into a later fallback (`fallback_row_source_time_invalid`).
- **Reader:**
  - A live read with an impossible completion is not treated as a malformed record: the equity is shown with freshness `invalid` and `invalid_clock:<relation>`.
  - Missing completion gives freshness `unavailable`; malformed completion gives `invalid`.
  - A `completion_relation` that contradicts the recorded times is refused as malformed.
  - `row_provenance` withholds `source_observed_at` for a bad-clock venue row and reports `source_clock`.
- **Tests (producer → reader):**
  - completion at +1 µs and +60 s after the record, and 1 µs and 30 s before the attempt (with the kernel clock frozen);
  - the reviewer's exact stub-journal reproduction;
  - a valid completion, a missing one, and malformed values (`"abc"`, NaN, inf, True, 1e20, list);
  - no Risk `PASS` on a bad clock;
  - no carry-forward from a bad-clock row.

  One earlier expectation changed: a before-attempt completion is now an invalid clock, not a refused record. The fixture now records `completion_relation: "ok"`.
- **Negative controls** (4 mutants: repair to the record time, invent a time when missing, Risk `PASS` on a bad clock, reader ignores the clock): **4/4 caught**.

## Final naming correction: current account status

**Defect.** An independent probe showed a +60 s future completion read as `status: FRESH`, `freshness: invalid`, `authoritative: true`, which is a contradictory current status.

**Contract** (`current_truth.read_account` / `current_account_status`; the producer is unchanged):
- `status` is the **current** status:
  - the producer status (`FRESH`, `VENUE_FALLBACK` or `JOURNAL_FALLBACK`) only while the value's source time is `fresh`. A fresh `JOURNAL_FALLBACK` keeps its original source time and age.
  - `STALE` when the source time is stale.
  - `UNAVAILABLE` when the source time is invalid, missing or unproven, when the producer status is `UNAVAILABLE`, or when the status is unknown (for example `PROVENANCE_UNRECORDED`).
  - It is never `FRESH` unless the freshness is `fresh`.
- `source_status` is the producer's status, unchanged. `last_reported` equals `source_status` whenever that is not the current status; otherwise it is `null`.
- `authoritative` is true only while the status is currently `FRESH` or `VENUE_FALLBACK` and the producer marked the read authoritative. `source_authoritative` keeps the producer's flag.
- Evidence is preserved: equity/balance, `observed_at`, ages, times, `fallback`, `attempt_errors` and `reasons`.
- Consumers inherit the current status: `/owner-api/v1/overview.account`, the legacy `/api/summary.account` and `/ws/live` equity `status`. GraphQL uses `equity_freshness`.
- The frontend is untouched. The React adapter reads only `account.equity/observed_at/source/stale_after_s/currency/freshness`, never `account.status`, so it stays compatible. The 301 recorded frontend hashes were not affected by this pass, which is backend-only.

**Tests** (9 new, in `tests/test_current_truth_completion_clock.py`):
- future (+1 µs, +60 s) and before-attempt completions are currently `UNAVAILABLE`, with source `FRESH`, `last_reported` `FRESH`, `authoritative` false, and evidence kept;
- missing and malformed completions are currently `UNAVAILABLE`;
- a fresh successful read is `FRESH`; the same read once stale is `STALE`;
- `VENUE_FALLBACK` stays current while fresh;
- a fresh `JOURNAL_FALLBACK` keeps its status and its original 120 s age;
- an exhaustive status × freshness property: never `FRESH` unless fresh. This caught an unknown producer status passing through as current, which is now `UNAVAILABLE`;
- a future account observation is `UNAVAILABLE` on the owner overview, `/api/summary`, `/ws/live` and GraphQL (`equity_freshness` `invalid`).

Updated expectations: the legacy no-provenance account is now status `UNAVAILABLE` with `source_status` `PROVENANCE_UNRECORDED` (3 assertions). A stale fallback is now `STALE` with `source_status` `JOURNAL_FALLBACK` (1 assertion).

## Tests (focused, per coordinator instruction; no broad suite)

| Run | Result |
|---|---|
| `tests/test_current_truth_contract.py` + `tests/test_current_truth_corrections.py` + `tests/test_current_truth_integration.py` + `tests/test_current_truth_completion_clock.py` | **241 passed** (127 + 65 + 24 + 25) |
| Naming-correction targeted run (the four truth files — which include the GraphQL tests — plus owner frontend API, owner reads, owner reads M2, dashboard auth, company) | **344 passed** |
| Final-blocker narrow regression (the 4 truth files, owner frontend API, activation-risk v1/v2, kernel boot, control-state recovery, risk persistence, attention telemetry, owner recovery risk guard, company) | **636 passed** |
| Final-review negative controls (9 mutants: fallback re-query race, observation not cleared, no attempt binding, partial-sum `total_upnl`, `assets_total` default 0, unconditional provenance JOIN on an old DB, index-missing → 500, company equity from the raw row, future heartbeat age) | **9/9 caught**, sources restored |
| **Final focused regression** (37 files: the three truth files, owner API/reads/cache, scraper/crawler/scouts, company, risk persistence/margin caps, protection snapshot, activation-risk v1/v2, kernel boot, control-state recovery, attention telemetry, trade history, dashboard pipeline/auth, phase0, owner recovery/risk guard, entry recovery, orphans, Owner Interface/boundary/telegram, chat agent, Supervisor, entry control fence, strategy signal gate, orchestrator abstention) | **1326 passed** |
| Correction negative controls (11 mutants: future skew, publication skew, all-old feed = QUIET, equal-value provenance, missing notional = 0, PASS without fresh input now, lookback window, overflow sum, marks request time, rounded identity, missing receipt relation) | **11/11 caught**, sources restored |
| R1 negative controls (8 mutants) | 8/8 caught (earlier run) |
| Focused regression (27 files: owner API/reads/cache, scraper/crawler/scouts, company, risk persistence/margin caps, protection snapshot, activation-risk v1/v2, kernel boot, control-state recovery, attention telemetry, trade history, dashboard pipeline/auth, phase0, owner recovery, entry recovery, orphans) | **952 passed** |
| Owner Interface / GraphQL / Supervisor / orchestrator regression (9 files) | **350 passed** |

Test history, stated truthfully:
- The coordinator's interrupted broad run reported 1682 passed, 8 failed and 4 collection errors.
- 6 of those 8 failures were reproduced identically on a pristine `git archive` export of `b86d772`:
  - `test_cognition_contracts::test_cognition_imports_only_stdlib_and_itself`
  - `test_m32_pilot_v4::test_no_pilot_run_or_verdict_exists`
  - 4 in `test_m32_scheme_d_validation`
- The other 2, both `*_quick_host_run_end_to_end_*` in `test_m32_host_qualification_v4` and `test_m32_qualification`, were **not re-verified at base**. They are research (m32) tests that import none of the changed modules.
- The 4 collection errors (`test_owner_interface_r3`–`r6`) are pre-existing: `trader/dashboard/web/index.html` was removed in `8385e9d`.
- No broad suite was rerun after the corrections.
- One regression found during the corrections was fixed without changing the old test: `test_owner_reads_m2::test_empty_journal_reads_truthfully` asserts the exact `decisions` shape, so the basis labels moved to a sibling `decisions_basis` key.
- One test-timing bug of mine was fixed: parametrized times were being computed at collection time.

## Risks / remaining limitations

- **Activation is required** (a kernel restart; not done). Until then:
  - account shows `PROVENANCE_UNRECORDED`;
  - Risk and News show missing;
  - pre-existing equity rows remain `unknown` provenance permanently.
- **Index build at the next `Journal` init.** The first dashboard or kernel start on the production journal will run `CREATE INDEX` for four partial indexes on the large `decisions` table. This takes a write lock and may take noticeable time at startup. It has not been measured on production data.
- The latest reads order by `decisions.ts`, with `rowid` breaking ties. A writer clock stepping backwards would reorder them.
- Strict future rejection has no tolerance. Any backwards clock step between kernel and dashboard, or an exchange ticker `timestamp` ahead of local receipt, makes the value `invalid`/unavailable.
- The News truth is intentionally conservative. `FEED_STALE`, `EMPTY_FEED` and `UNCERTAIN` (for example, one undated item) will often prevent `clear`.
- `risk_assessment` is written before the scan. Per-symbol and per-position caps remain `applies_at_entry`. The configured identity is the dashboard's loaded config, so a later file edit shows only in `config_file_sha256_now`.
- The legacy `/api/summary` now performs the same quote fetch as `/owner-api/v1/valuation`; it already made per-symbol ticker calls through `_position_marks`. Its `open_positions[].mark/upnl` are now absent for unvalued positions, and `total_upnl` is often `null`.
- On an unmigrated journal the exact latest reads are `UNAVAILABLE` until normal `Journal` initialization creates the indexes. That initialization is not separately authorized here.
- The broad-run test that rewrites `knowledge/00 Company/*` in the real vault was not identified. It is not one of the files changed in this package.
- The frontend is untouched and the new fields are backend-only. `overview/activity.decisions.executed/skipped` are now ordered by `ts` rather than insertion order.

## Coordinator completion verification

CURRENT_TRUTH_CONTRACT_R1: PASS (implementation package; not activation).

- Final targeted check: 344 passed, including all 241 truth tests. Earlier focused integration regression: 1326 passed; subsequent completion-clock regression: 636 passed. These are overlapping runs, not additive counts.
- Independently reproduced the small-future-timestamp and source-completion-time repair defects, then verified their corrections.
- All 301 tracked frontend files match the task-start hashes. Approved Overview and paused M4 remain untouched.
- HEAD remains b86d772c016d3d4480adc43b49b5765e9877d83f. No commit, deployment or restart.
- git diff --check passed. The three initially clean company notes rewritten by broad tests are restored; pre-existing dirty knowledge files are retained.
- graphify update . completed: 17301 nodes, 39356 edges, 964 communities; generated graph, report, manifest, caches and automatic backup updated. No implementation or test files were modified after that rebuild.
- The interrupted broad-suite result is not a full-suite PASS; its failures and attribution limits remain recorded above.
- Protection remains UNAVAILABLE without separately authorized venue proof. Venue work: NONE.
- FRONTEND_TRUTH_READY: YES for backend contract consumption; frontend binding was not implemented. ACTIVATION_READY: NO. Package blockers: NONE.

## Final closure evidence (2026-09-30, closure worker)

Scope: evidence only. Implementation and tests were not modified; there was no deploy, restart, production-DB connection, venue access, frontend/M4 change, commit or staging. The only repository edit is this appended section; everything above is preserved as written. Artifacts are in `/tmp/current-truth-closure/`, with a combined summary in `closure-summary.json`.

### A. The interrupted broad run's 8 failures and 4 collection errors

- **Source:** `/tmp/full_suite_3.txt` (8 failed, 1682 passed, 108 deselected, 4 errors). The selection that deselected 108 tests is not recorded.
- `.pytest_cache/v/cache/lastfailed` is cumulative and stale, so it was not used as the source.
- **Method:** `repro_failures.sh <tree> <out>` ran the 8 node IDs and `--co` on the 4 collection-error files in two isolated trees, with `-p no:cacheprovider` and a per-tree `--basetemp`:
  - a pristine `git archive b86d772c` (`base-b86d772c/`);
  - a snapshot of the current working tree (`current-snapshot/`). All 19 implementation/test package files are byte-identical to the working tree; this report was subsequently appended.
- In both trees, `trader` resolved to that tree's own copy.
- **Result:** all 12 reproduce identically at base and on current (`repro-base.txt`, `repro-current.txt`). The outputs are identical after normalizing tree paths, durations, receipt hashes and object addresses.
- No test-file, `trader/cognition`, pilot-artifact or `trader/dashboard/web` difference exists between the trees.
- Importing the 5 failing test modules loads none of the 13 changed `trader` modules (`import-closure.txt`).

| Node ID | Exact error (base = current) | Cause | Production relevance / effect on package |
|---|---|---|---|
| `tests/test_cognition_contracts.py::test_cognition_imports_only_stdlib_and_itself` | `AssertionError: dataset.py imports trader.engine.trade_accounting` | Test/code drift at base: `trader/cognition/dataset.py:392` (last changed in `a113de0`) | Research contract test. None; the file is not in the package. |
| `tests/test_m32_host_qualification_v4.py::test_quick_host_run_end_to_end_seals_a_receipt_and_combine_refuses_it_as_a_qualification` | `assert (2 == 0)`; the receipt verdict is `FAIL`, with `environment_gate` detail `IntegrityError:openblas_threads_mismatch`, `OPENBLAS_NUM_THREADS: null` | **Environment/tooling.** The test's `ENV` (with `OPENBLAS_NUM_THREADS=1`) is passed only to subprocesses. `Q.main` runs in-process, and `m32_scheme_d_validation.py:161` reads `os.environ`. | None; research host qualification. With `OPENBLAS_NUM_THREADS=1` exported, it **passes on both base and current** (`quick-host-env1.txt`: 2 passed per tree). |
| `tests/test_m32_qualification.py::test_quick_host_run_end_to_end_seals_a_receipt_and_combine_refuses_it_as_a_qualification` | Same (`2 == 0`, `openblas_threads_mismatch`) | Same environment cause (v3 `qualify` tool) | Same; passes on both trees when the variable is set. |
| `tests/test_m32_pilot_v4.py::test_no_pilot_run_or_verdict_exists` | `At index 3 diff: 'PILOT_VERDICT.json' != 'evaluate_pilot.py'`; `out` is extra | Stale test: `PILOT_VERDICT.json` and `out/` are **committed** at `b86d772c` | None (research artifact). |
| `tests/test_m32_scheme_d_validation.py::test_seed_registry_allows_only_unique_phase_90_tuples` | `IntegrityError: pre_rng_rng_construction_refused` | The module deliberately refuses while pre-RNG | None (research). |
| `tests/test_m32_scheme_d_validation.py::test_actual_statistic_harness_calls_all_384_imported_statistics` | `IntegrityError: pre_rng_world_generation_refused` | Same | None. |
| `tests/test_m32_scheme_d_validation.py::test_phase90_injections_preserve_membership_masks_and_strata` | `IntegrityError: pre_rng_world_generation_refused` | Same | None. |
| `tests/test_m32_scheme_d_validation.py::test_symbolic_truth_tables_are_complete_in_shape_and_honestly_refuse_ambiguity` | `KeyError: 'fully_classified'` | Test/module drift at base | None. |
| `tests/test_owner_interface_r3.py` (collection) | `FileNotFoundError: …/trader/dashboard/web/index.html` | The legacy HTML was deleted in `8385e9d` and is absent at base | Existing production-interface test coverage gap; does not invalidate the focused R1 results. |
| `tests/test_owner_interface_r4.py` (collection) | `FileNotFoundError: …/trader/dashboard/web/index.html` via import of `r3` | Same committed legacy-path deletion | Existing production-interface test coverage gap, not an R1 runtime failure. |
| `tests/test_owner_interface_r5.py` (collection) | `FileNotFoundError: …/trader/dashboard/web/index.html` via import of `r3` | Same committed legacy-path deletion | Existing production-interface test coverage gap, not an R1 runtime failure. |
| `tests/test_owner_interface_r6.py` (collection) | `FileNotFoundError: …/trader/dashboard/web/index.html` via import of `r5` then `r3` | Same committed legacy-path deletion | Existing production-interface test coverage gap, not an R1 runtime failure. |

For every row: R1-caused **NO**, pre-existing **YES**, R1 contract invalidated **NO**. Only the two BLAS cases are environment-only; the four collection errors are repository fixture/path debt, not missing Python dependencies. These results prohibit a full-suite PASS claim and leave the old interface coverage gap explicit.

**Correction to "Test history" above.** The two `*_quick_host_run_end_to_end_*` failures are now baseline-verified. They fail identically on base and current, pass on both when `OPENBLAS_NUM_THREADS=1` is exported, and are caused by the environment, not by this package. All 8 failures and 4 collection errors are pre-existing at `b86d772c`, and none is a package regression. No historical debt was fixed.

### B. Migration benchmark (isolated synthetic journal)

**Reproduce** with `bench_migration.py {build,isolated,startup,noop,concurrent,failure} --db <path>`. The unmigrated file is created by the **base** `Journal` from the pristine archive, and the migration is run by the **current** `Journal`. Journal startup is traced with a `sqlite3` trace callback, so each new object's time is measured inside real `Journal.__init__`.

**Population and its justification.** There was no production SQLite connection.
- `stat`: `data/luffy.db` is 1,927,069,696 B, and its WAL is 16.4 MB.
- The retained logs (`luffy.log.3` → `luffy.log`) are contiguous from 2026-08-24 22:19 to 2026-09-30 13:07. They record **659,854 cycle decisions**, with at most 21 per cycle. The repository was created on 2026-08-24.
- The exact production row count, row width and HOLD/directional mix were **not measured**.

The synthetic journal therefore uses deliberately heavier-than-evidence parameters:

| Parameter | Value |
|---|---|
| Decisions, primary run (on disk, `/tmp`, same filesystem as production) | **1.1 M** (1.67× the logged count) |
| Decisions, scaling run (on tmpfs) | **2.0 M** (3.0×) |
| Symbols × cadence | 20 symbols per cycle, one cycle per minute from 2026-08-24 |
| HOLD / directional | 60 % HOLD / **40 % directional** (index-heavy) |
| Directional rows executed | 0.5 % |
| Skipped directional rows with a reason | 90 % |
| Average decision row | 326 B (`signals_json` payload) |
| Decisions table size at 1.1 M | 387 MB |
| Equity rows | one per cycle |

Rows matching each partial index at 1.1 M (2 M): executed 2,248 (4,020), directional 439,696 (799,218), skipped 437,448 (795,198), rejected 393,679 (715,608). The new index sizes at 1.1 M are 0.1, 18.2, 18.1 and 16.3 MB.

**Timings at 1.1 M decisions, on disk.** Times are median / max in seconds.

| Measurement | 1.1 M |
|---|---|
| `equity_provenance` table (n=7) | 0.002 / 0.009 |
| `idx_decisions_executed_ts` (n=7) | 0.209 / 0.242 |
| `idx_decisions_directional_ts` (n=7) | 0.578 / 1.129 (1.129 was the first build on the fresh file) |
| `idx_decisions_skipped_ts` (n=7) | 0.571 / 0.574 |
| `idx_decisions_rejected_ts` (n=7) | 0.556 / 0.605 |
| Full current `Journal()` startup, migrating (n=5) | **1.873 / 1.897** |
| Base `Journal()` on the same unmigrated file (n=5) | 0.001 / 0.002 |
| No-op current `Journal()` on the migrated file (n=10) | 0.0015 / 0.0017 |
| No-op base `Journal()` on the migrated file (n=10) | 0.0012 / 0.0016 |

Each existing-object `IF NOT EXISTS` statement takes about 10 µs. The schema hash and row counts are unchanged after the no-op inits.

**2 M scaling point (tmpfs; CPU scaling, not a disk timing).**
- Per index: executed 0.366 / 0.416; directional 0.972 / 1.172; skipped 1.008 / 1.197; rejected 0.999 / 1.181.
- Full migrating startup: **3.342 / 3.379** (n=3). This is about linear in rows: 1.87 s at 1.1 M × (2 / 1.1) ≈ 3.4 s.
- Uncached direct disk read of the synthetic file (`dd iflag=direct`, 3 runs) was 1.4–1.5 GB/s on this VM disk. This is supporting storage-throughput evidence, not a measured cold SQLite migration or an upper bound on cold scan latency.

**Concurrency (5 trials at 1.1 M; 3 at 2 M).** Four helper processes ran during the startup migration:
- an exact-reads reader running the real `owner_reads.latest_activity` without `Journal` init;
- a legacy reader (newest-500 decisions, latest equity, a per-symbol count);
- a kernel-like writer committing one decision every 100 ms with `timeout=30`, as `Journal._conn` does;
- a write-lock probe (`BEGIN IMMEDIATE`, no wait, every 5 ms).

| Metric | 1.1 M | 2 M |
|---|---|---|
| Migration duration | 1.90–2.37 s | 3.43–4.05 s |
| Reader errors | **0** | **0** |
| Exact-reader max latency, during vs outside migration | ≤ 3.7 ms vs ≤ 2.8 ms | ≤ 3.3 ms |
| Legacy-reader max latency, during vs outside migration | ≤ 10.2 ms vs ≤ 11.5 ms | ≤ 25 ms |
| Longest contiguous write-lock hold | 0.74–1.35 s | 2.36–2.39 s |
| Total write-lock time | 1.84–2.27 s | — |
| Longest write-lock hold outside migration | ≤ 12 ms | — |
| Writer max wait during migration | **1.17–1.35 s** | **2.36–3.48 s** |
| Writer max wait outside migration | ≤ 15 ms | — |
| Writer errors | 0 | 0 |

- No reader lock blocking or errors were observed in these WAL trials; this is not a guarantee for arbitrary workloads.
- During the migration, exact reads move per index from `UNAVAILABLE` to found, as the contract specifies, with no exception.
- Each index statement autocommits separately. Because the busy-handler backoff can sleep through the brief gaps between statements, a writer can wait longer than a single index takes.
- The kernel impact is therefore a stall of one write of about 1.2–1.4 s (up to about 3.5 s at 2 M). This is far below the 30 s busy timeout and caused no errors.

**Failure, retry and idempotency (1.1 M; `failure-1100k.json`).**
- **F1: SIGKILL during startup at 0.25, 0.6, 1.0, 1.4 and 1.8 s.**
  - The runs left 0, 1, 2, 3 and 3 new indexes. Each statement is atomic, so each index is either complete or absent, and the table is created last.
  - `integrity_check` was `ok` after every kill.
  - The retry `Journal()` succeeded every time, and its schema equals the clean-migration reference.
  - For all 4 indexes, the newest-20 rows and the counts via `INDEXED BY` equal a forced `NOT INDEXED` full scan.
- **F2: another connection holds the write lock for more than 30 s against an unmigrated file.**
  - The current `Journal()` **raises `sqlite3.OperationalError: database is locked`** after 30.25 s, with no object created. The retry after release succeeds with the reference schema and integrity `ok`.
  - Under the same held lock, the base `Journal()` on a migrated file and the current `Journal()` on a migrated file both wait 30.3 s and **succeed**, because the pre-existing backfill `UPDATE` error is swallowed and the new no-op statements need no lock (`f2-*.json`).
  - This failure mode is new and exists **only until the migration has completed once**.
- **F3: a partial prior migration (the table plus 2 of 4 indexes).** Init builds the missing 2 (about 0.65 s each). The schema equals the reference after whitespace normalization. The raw SQL text differs only because the hand-made objects were written with different whitespace, and `IF NOT EXISTS` keeps an existing object by name.
- **F4: 3 repeated inits.** The raw schema is unchanged, and exact-read equivalence holds.

**Limits of this benchmark.**
- The data is synthetic, and the true production decision count, row width and action mix are unmeasured. The bounds above rest on log counts and a deliberately heavier population.
- The page cache was warm after the build (I have no root to drop caches). Cold-cache migration duration remains unmeasured. The direct-read measurement is from a VMware virtual disk that may itself be host-cached and does not establish a strict bound.
- The production kernel was running on the same 4-vCPU VM during the measurements.
- The 2 M run was on tmpfs, so it is a CPU scaling point, not a disk timing.
- Disk reserve was constrained; the coordinator observed approximately 920 MiB available during the run. The large databases were deleted afterwards; only JSON/text artifacts remain.
- There was no speculative optimization.

### C. Test reruns (isolated snapshot; the real `knowledge/` is untouched)

The runs used `cwd=/tmp/current-truth-closure/current-snapshot` and `/home/sarmad/trader/venv/bin/python -m pytest -p no:cacheprovider -q --basetemp=<snapshot>/.pt-tmp …`:

| Command target | Result |
|---|---|
| `tests/test_current_truth_contract.py tests/test_current_truth_corrections.py tests/test_current_truth_integration.py tests/test_current_truth_completion_clock.py tests/test_owner_frontend_api.py tests/test_owner_reads.py tests/test_owner_reads_m2.py tests/test_dashboard_auth.py tests/test_company.py` | **344 passed** in 40.5 s |
| The 4 truth files alone | **241 passed** in 15.7 s |

- The snapshot's `knowledge/` hashes were unchanged by the run (0 diff lines).
- The real repository's `git status knowledge` is identical before and after.
- The commands are in `targeted-tests.txt`. No full suite was run.

### Corrections to earlier limitations

- **"Index build at the next `Journal` init … not measured on production data"** is now measured on a representative synthetic journal:
  - about 1.9 s of first-start migration at 1.1 M decisions and about 3.4 s at 2 M;
  - no reader blocking observed in the measured trials;
  - one kernel write stalls up to about 1.4 s (1.1 M) or about 3.5 s (2 M), with no errors;
  - the no-op cost after migration is about 0 ms (~10 µs per statement);
  - SIGKILL, partial-state and retry are safe and idempotent.
- **New limitation (F2).** Until the first successful migration, a `Journal()` start that finds the write lock held continuously for more than 30 s by another connection now raises instead of proceeding. Pre-package, that start waited and succeeded. A retry (for example a watchdog restart) completes the migration. The effect is transient and confined to the first start; the operational step is to avoid long-running writers during activation.
- **Test history.** All 8 failures and 4 collection errors are verified as pre-existing at `b86d772c`. The two quick-host failures are environment-caused (`OPENBLAS_NUM_THREADS` unset in the pytest process).

CURRENT_TRUTH_R1 closure: package blockers NONE. ACTIVATION_READY remains NO (a restart is required and not authorized here).


### Coordinator final boundary verification

- CURRENT_TRUTH_R1_FINAL_GATE: PASS for package closure, not deployment authorization.
- FIXES_MADE: no backend/test fixes; closure evidence and limitations appended to this report.
- The 1.1 M disk database was 515,518,464 bytes before migration, using the baseline journal schema; the 2 M scale check was tmpfs. Neither is a production copy. Population comparability is supported by 659,854 logged decisions, not an exact live DB census. Reported timings are observations, not worst-case guarantees.
- Eight tracked generated Graphify files restored to HEAD and 153 package-generated untracked files removed. The pre-update graph, labels, report and manifest backup matched HEAD byte-for-byte. A recovery archive and exact inventory remain in `/tmp/current-truth-closure/`.
- Exact stage manifest: 13 backend files, 6 tests/fixtures, and this report (the 20 files listed in the package Files section); no frontend/M4, Graphify, or unrelated knowledge/report files staged.
- All 19 implementation/test files match both the closure-start hashes and the tested isolated snapshot. All 2,350 non-package files in the closure-start tracked/untracked file inventory retain their hashes.
- `git diff --cached --check`, `git diff --cached --name-status`, and `git diff --cached --stat` completed. No commit, deploy, restart, production DB connection, or M4 work.
- BLOCKERS: NONE for committing the package. First-start lock contention, synthetic/cache limitations and separate activation authorization remain as documented above.
- SAFE_TO_COMMIT_CURRENT_TRUTH_R1: YES.


## Final Opus corrections (2026-09-30)

Scope: six user-listed corrections only. There was no commit, deploy, restart, live DB access or venue call. No frontend or M4 work was done, and nothing was staged. Graphify: see the closing note below.

1. **Quote timing (`DataFeed.ticker_quote`).**
   - `attempt_started_at` is taken immediately before `fetch_ticker`.
   - `received_at` is taken immediately after a successful return. It is `null` on failure, so no receipt is fabricated.
   - `source_ms`, the ticker's own timestamp, is kept independently.
   - If the receipt is earlier than the attempt (the local clock stepped back), the quote fails closed: `price=null`, `error=quote_receipt_before_attempt`.
   - `valuation._quote_view`:
     - When `attempt_started_at` is present, it must parse and must not be after the receipt. Otherwise the result is `quote_attempt_malformed` or `quote_receipt_before_attempt`.
     - Fixtures without `attempt_started_at` stay valid.
     - `quote_time_after_receipt` is kept. With the receipt now taken after the return, a source time later than the receipt is impossible except through venue/local clock skew, and that is refused, not tolerated.
2. **Telegram `/news`.** The reply now uses `kernel.news_status_line`:
   - ARMED 🚨 (dampened);
   - QUIET ✅, rendered as "CONFIRMED QUIET". This is the only ✅.
   - UNCERTAIN ⚠️. It says "entries dampened" when `active`.
   - FEED_STALE/EMPTY_FEED ⏳;
   - FETCH/PARSE/ASSESSMENT_FAILED ❌;
   - DISABLED ⛔;
   - unknown ❓.
   - A check older than `stale_after_s`, untimed or future-timed is shown as STALE ⏳ whatever its truth.
   - Enforcement is unchanged: display only.
3. **Company cockpit (`build_company` risk card).** Only a fresh, confirmed QUIET (`status=QUIET` and `clear=true`) may read nominal/clear. Every other news status names itself.
   - Breaker values, in precedence order:
     - `armed`: control HALTED/FROZEN/RECOVERY, or news ARMED (control precedence unchanged).
     - `dampening`: news UNCERTAIN with `active=true`. The card state stays `alert`.
     - `nominal`: news QUIET and clear only.
     - Otherwise the status label: `uncertain` (UNCERTAIN, inactive), `feed stale` (FEED_STALE), `feed empty` (EMPTY_FEED), `stale` (record older than `stale_after_s`), `failed` (FETCH/PARSE/ASSESSMENT_FAILED), `unavailable` (missing/malformed/invalid clock), `disabled` (DISABLED), `unknown` (unrecognised).
   - Card output (`last_output`): QUIET keeps the exposure text or `N positions · limits nominal`; armed and dampening keep their existing text; every other status reads `news <label> · not confirmed quiet · <exposure or N positions>`, so "limits nominal" never appears unless news is confirmed quiet.
   - The Guard stat keeps each distinction: `clear`, the armed reason, `uncertain · dampening`, `uncertain`, `feed_stale`, `empty_feed`, `stale`, `fetch_failed`, `parse_failed`, `assessment_failed`, `disabled`, `unavailable`.
   - Presentation only: no enforcement change, no new health score.
4. **Least authority.** `_position_quotes` builds `make_exchange("futures", with_keys=False)`. A keyless instance still follows `BINANCE_DEMO`: the offline check showed `apiKey=''` and fapiPublic `https://demo-fapi.binance.com/fapi/v1`. The venue and `valuation.BASIS` are therefore unchanged. This was verified by mock test only.
5. **`/enrichment/marks` identity.**
   - The contract is per position:
     - `valuation.estimate` values per trade;
     - `/valuation` publishes per trade;
     - `frontend/src/views/Overview.tsx` renders one row per position using `marks[p.symbol].upnl`.
   - Symbol keying therefore let a second trade on the same symbol overwrite the first. This does not rely on Risk uniqueness.
   - The fix is additive:
     - New `by_trade`, keyed by `trade_id`: `{symbol, status, mark, upnl, quote_basis, quote_observed_at, quote_age_s, reasons}`.
     - `marks[symbol]` keeps its shape for single-trade symbols.
     - A symbol held by several trades publishes the shared quote with `upnl: null`, plus `trade_ids` and `upnl_scope: "per_trade_only:multiple_positions"`.
     - Any unvalued trade on a symbol moves the symbol to `unvalued` with the union of reasons.
     - `total_upnl` is unchanged (per trade).
6. **Additional failures reported by independent Opus review.**
   - 13 failures:
     - `tests/test_macro_guard.py`: 1 (`TestMacroGuardNoToken::test_no_token_clears`);
     - `tests/test_naked_position_detection.py`: 3;
     - `tests/test_naked_position_is_rearmed.py`: 5;
     - `tests/test_partial_close_shrinks_notional.py`: 4.
   - The Opus evidence says all 13 reproduce identically on pristine `b86d772`. This is now also substantiated locally:
     - the same 4 files were run in a `git archive b86d772` extraction and in the current isolated snapshot;
     - both gave `13 failed, 21 passed`, and the `-rf` failure lines were byte-identical (`/tmp/current-truth-final-corrections/opus-13-repro.txt`).
   - They are pre-existing and not caused by this package.
   - **No full-suite PASS is claimed.**

### Tests (exact)

- Focused tests appended to the staged `tests/test_current_truth_corrections.py`: 36 cases:
  - attempt/receipt ordering; failure with no receipt; clock-back fail-closed;
  - valuation attempt/source/stale/after-receipt checks; keyless exchange;
  - `/news` icon per truth (11); stale/untimed never healthy (5); the `/news` command path;
  - cockpit Breaker/Guard/output (12): QUIET, ARMED, UNCERTAIN active, stale record, missing record, and — from real records that `read_news_guard` accepts with `status` equal to their truth — UNCERTAIN inactive, FEED_STALE, EMPTY_FEED, FETCH_FAILED, PARSE_FAILED, ASSESSMENT_FAILED, DISABLED. The test locates the single risk card in the `build_company` structure (not whole-company text) and asserts exact Breaker/Guard, that `nominal`/`clear` appear only for QUIET, and the status-specific `last_output`;
  - duplicate-symbol marks (2).
- Whole file `tests/test_current_truth_corrections.py`: **101 passed** (real tree, temp journals only).
- Negative control (earlier, before the final cockpit refinement): the first 29 cases run against the staged pre-correction sources gave `27 failed, 2 passed`. No further negative controls were run.
- Final targeted truth regression — one clean, complete run:
  - 38 files: the recovered original 37 plus `tests/test_current_truth_completion_clock.py` (list: `/tmp/current-truth-final-corrections/regression-files.txt`).
  - All 20 manifest files were copied from the real tree into the existing snapshot `/tmp/current-truth-final-corrections/snap` and byte-compared (0 differences). The repository was not copied again.
  - Rollback tests read history with `git archive 51101d0` only. For this run the snapshot had a temporary `.git` file (`gitdir: /home/sarmad/trader/.git`) so that read-only archive works; it was removed afterwards.
  - Command: `cd snap && /home/sarmad/trader/venv/bin/python -m pytest -p no:cacheprovider -q --basetemp=/dev/shm/ct-final-<pid>-<epoch> <38 files>`; the basetemp was removed after the run.
  - Result: **1388 passed, 0 failed, 0 errors** (rc=0, 182 s; output `/tmp/current-truth-final-corrections/final-regression-run.txt`). This supersedes the earlier `1378 passed, 3 errors` run and its net-count reconciliation. The +7 over 1381 are the new cockpit cases.
  - The snapshot `knowledge/` hashes were identical before and after the run, and the real-tree `knowledge/` hashes equal the session baseline.
- The staged name set still equals the initial manifest; no files outside it were edited (other unstaged diffs predate this work).

### Transient disk exhaustion (candid)

- During an earlier snapshot attempt in this correction pass, the worker's copy included `.claude/worktrees` and filled `/` until writes failed with ENOSPC. The copy was deleted immediately.
- Known: afterwards `logs/` showed no disk-error entries dated 2026-09-30 (the "No space left" entries found are dated 2026-09-29), and the kernel logged cycle #1290 normally after the incident. Free space is small (about 0.4–0.5 GB on `/` at the final run).
- Unknown: whether any process (kernel, dashboard, watchdog, SQLite WAL checkpoint or log writer) attempted a write during the ENOSPC window and lost or truncated it. The logs cannot prove a negative, and no production DB or venue inspection was performed in this task. No claim of zero impact is made.
- The final regression used `/dev/shm` for temporary data and did not copy the repository.

### Graphify

The coordinator ran `graphify update . --no-cluster` in the snapshot successfully (AST-only). The generated output was removed to preserve disk space. No semantic extraction was run and no Graphify output is staged.

### Final correction boundary verification

- CURRENT_TRUTH_R1_FINAL_CORRECTION: PASS. Focused correction file: 101 passed (36 new cases). Complete targeted truth regression: 1388 passed across 38 files in one clean run. No full-suite PASS claim.
- All 19 implementation/test files in the original manifest match the tested snapshot byte-for-byte. The corrections touch only five backend files, the existing corrections test file and this report.
- Original 20-file staged manifest retained. M4/frontend, Graphify and unrelated dirty work remain excluded. Unrelated tracked diffs match the initial inventory; the Graphify query timestamp created in this turn was restored to its initial clean state.
- Coordinator completed the final AST-only Graphify refresh in the isolated snapshot; logs retained at `/tmp/current-truth-final-corrections/graphify-final-update.txt`. Disposable generated output removed.
- Package blockers: NONE. SAFE_TO_COMMIT_CURRENT_TRUTH_R1: YES, subject to the documented operational limitations; this is not deployment or activation authorization. No commit, deploy or restart performed.
