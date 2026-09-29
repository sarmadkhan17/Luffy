# LUFFY-TRADE-HISTORY-PAGING-R1

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
Base: the `owner-frontend-live-binding` worktree with V2.1 (uncommitted).

**Correction passes (same date).** Astra's first review found two blockers:
cursors were decoded leniently, and the count-based history-change notice
could be cancelled by opposing changes. The first correction pass fixed both
(strict cursors; a membership digest in the cursor). Astra's recheck accepted
both fixes and asked for three small corrections, made in the second pass:
- a cursor field holding invalid Unicode (a lone surrogate) now fails as
  `invalid_cursor` instead of surfacing the raw encoding error;
- `scripts/bench_trade_history.py` builds the current cursor format again;
- this report and the performance evidence are refreshed. The history check's
  cost grows with the depth of the traversal (section F); the earlier claim
  that memory does not change with depth no longer holds for the check.

Sections A–I below describe the package as corrected.

**Not done:**
- no commit, deploy or restart;
- no watchdog change;
- no production read or write;
- no `graphify update .`;
- no venue call.

**Out of scope, unchanged:**
- The Protection Snapshot is untouched: it keeps its credential policy, and the live venue gate stays deferred.
- Close-trade is not implemented.

## Summary

The React Trades page used to show only the latest 50 journal trades. It now pages through the whole journal history:
- **Paging:** keyset (cursor) pages, newest first.
- **Status filter:** applied by the server over the full history.
- **Positions:** each page shows its exact position ("Records 51–100 of 183").
- **Changes behind the reader:** if the set of matching trades on pages already read changes during a traversal, every later page says so until the owner restarts from Newest. This includes a trade that entry recovery books later with an older opening time, and a trade leaving or entering a status filter. Opposing changes cannot cancel the notice.

## A. Cursor contract

`GET /owner-api/v1/trades` is read-only, authenticated as before, and sent with `Cache-Control: no-store`.

**Request parameters:**

| Param | Values | Default |
|---|---|---|
| `limit` | 1–200; values outside the range are clamped, and a non-integer returns 422 | 50 |
| `status` | `all` \| `open` \| `closed`, applied by the server | `all` |
| `cursor` | an opaque token taken from a previous `next_cursor` | none, which means the newest page |

**Response:** the page fields, `generated_at`, `source` and `consistency` (see C).

```json
{ "trades": [...], "limit": 50, "status": "all",
  "order": ["opened_at DESC", "id DESC"],
  "page": { "cursor": null, "next_cursor": "eyJ2Ijox…", "has_more": true,
            "preceding": 0, "total": 183,
            "first": {"opened_at": "…", "id": "…"},
            "last":  {"opened_at": "…", "id": "…"},
            "history": {"changed": false, "reason": null} } }
```

**Page fields:**
- `has_more` comes from reading `limit + 1` rows. `next_cursor` is `null` exactly when `has_more` is false.
- `preceding` is how many matching trades sort before this page. `total` is how many match the filter. Both are read in the same read transaction as the rows.
- The cursor is unpadded base64url JSON `{v:1, f:<status>, o:<opened_at>, i:<id>, a:[<anchor opened_at>, <anchor id>], h:<32-hex digest>, c:<bool>}`: "rows after this row", plus the traversal's history check (section B).
- `history.changed` is `false` when the trades already read are unchanged, `true` when they changed (`reason`: `membership_changed` on the page that detects it, `earlier_page` on every later page), and `null` for a legacy 4-field cursor, which has nothing to verify against (`reason`: `unverifiable_cursor`).

**Errors:** each returns HTTP 400 with no rows.

| Condition | Error code |
|---|---|
| Malformed cursor: not canonical unpadded base64url (any other character, padding, spare trailing bits), not a JSON object, duplicate keys, NaN, `v` not the integer 1 (`true`, `1.0` and `"1"` fail), missing or extra keys, empty, non-string or invalid-Unicode values, a malformed anchor/digest/flag, or over 1024 characters | `invalid_cursor` |
| Cursor issued for a different status filter | `cursor_filter_mismatch` |
| Unknown status | `invalid_status` |

**Compatibility:** requests without `cursor` behave like the old endpoint (newest first, `limit` clamped at 200). The response gains fields and removes none.

## B. Ordering identity

Order is `opened_at DESC, id DESC`. Both fields are immutable journal identity:
- `opened_at` is written once by `Journal.add_trade`. Nothing updates it, and `trade_accounting` lists it as identity.
- `id` is the primary key (`<prefix>_<uuid hex>`) and breaks ties.

The cursor comparison `(opened_at, id) < (?, ?)` uses SQLite's TEXT ordering, the same ordering as `ORDER BY` and the index. So a forward traversal never repeats a row and never skips a row inside its range, whatever the timestamp format.

Newest-first is chronological for the uniform ISO-8601 UTC strings that `Position` writes. With microseconds omitted, `+` sorts before `.`, which is still correct. I did not inspect production rows (out of scope). Any non-ISO legacy values would sort by text, exactly as the existing legacy views already do.

**Found during implementation: backdated inserts.** Entry recovery inserts the trade later, using the `opened_at` from when the order was submitted:
- `executor.py:180` calls `recovery.begin(Position(...))`;
- `recovery.py:220` then calls `add_trade(Position(**template))`.

That row can land behind a cursor that has already passed. Keyset paging alone would not show it, and counts cannot reliably show it either: a late insert plus a shown trade leaving the filter leaves `preceding` and `total` unchanged (Astra's cancellation case).

**History check.** The first page fixes the traversal's anchor (its first row). Each cursor carries the anchor and a SHA-256 digest (first 128 bits) of exactly which matching trades lie in `anchor >= (opened_at, id) >= cursor`, read in the same transaction as the page. The next request re-reads that range and compares; a different set reports `changed: true`, and every later cursor carries the flag, so a later opposite change cannot clear it. Trades newer than the anchor are new rather than missed: they show as `preceding` exceeding the rows shown, and the UI notes them separately. This detects membership differences at read boundaries; it is not an event log or a cross-page snapshot (a trade that leaves and returns between two reads is not reported, and nothing was missed).

## C. Backend implementation

**New module `trader/dashboard/trade_history.py`:**
- `trade_page()` opens `protection_snapshot.ro_connect`: `mode=ro`, an authorizer that allows only SELECT and READ, and ATTACH disabled.
- It reads the rows, `total`, `preceding` and the history check in one `BEGIN … COMMIT` read transaction.
- The history check is one query over the consumed range and this page, in index order on the covering index (no temp B-tree). Rows are hashed as they stream, so its Python memory does not grow with the range; its time does (section F).
- It returns only journal columns: `id, decision_id, symbol, side, amount, entry_price, exit_price, notional_usdt, leverage, stop_loss, take_profit, sl_order_id, initial_risk, tp1_done, strategy_id, strategy_name, market_type, exec_mode, status, realized_pnl, close_reason, opened_at, closed_at, mfe_r, mae_r`.
- Nothing is derived. NULL stays `null`: an open trade has `exit_price: null`, and unmeasured MFE/MAE is `null`.
- `excursion_json` is omitted because it is bulky provenance.

**Fields preserved:**
- open and closed status;
- journal-booked `realized_pnl` (the UI labels an open trade's value "Realized so far");
- strategy fields (`strategy_id` and `strategy_name`; the journal has no per-trade version column);
- timestamps;
- lineage (`decision_id`);
- the protection reference (`sl_order_id` and `stop_loss`), labelled in the drawer as a journal record, not venue verification.

**Other backend changes:**
- `trader/dashboard/owner_api.py`: the `/trades` route delegates to `trade_page` and maps its errors to 400.
- `trader/core/journal.py` adds two indexes to `SCHEMA`: `idx_trades_opened(opened_at, id)` and `idx_trades_status_opened(status, opened_at, id)`. Page and count queries then use covering indexes, with no sort (plans are in `performance.json`).
- **Deployment note:** these are `CREATE INDEX IF NOT EXISTS`, created once when the next Journal opens the production DB. That is a one-time write when the kernel or dashboard next starts.

## D. Frontend UX (Trades → Trade book)

**Layout:**
- `TradeBook` replaces the old 50-row registry panel.
- Strategies are split into their own `Strategies` component, with unchanged behaviour.

**Paging:**
- **Newest**, **Previous page** and **Next page** buttons keep a stack of cursors.
- **Next** is disabled on the last page, and while the current page is refetching.
- The paging bar stays visible while a page loads, so the owner can always go back.

**Loaded range:**
- It reads "Records 51–100 of 183 trades · page 2 · newest first · opened … → … · up to 50 per page. Positions as of the read time."
- A page with no rows says so; for example, the empty scenario shows "No trades on page 1 (0 in total)".

**Filter and search:**
- **Status filter** ("Status (server-side, full history)", `Filter Trades`): applied by the server. Changing it restarts paging at page 1.
- **Search** (`Search Trades`, placeholder "Search this loaded page…"): client-side over the loaded page only. The source strip says so ("search covers only the loaded page"), and the counter reads "x of y loaded records".
- The search text survives page changes.

**Other behaviour:**
- **History changed:** when any page reports `history.changed: true`, an amber notice appears: "Trade history changed while you were paging. Restart from Newest for a complete current view." It stays while pages load, on later pages and after Previous (page 1 included, where Newest stays enabled). Only Newest or a filter change clears it; each restart is a fresh traversal with its own page cache. A legacy cursor (`null`) shows that changes "cannot be ruled out".
- **Newer trades:** when the server confirms the pages already read are unchanged but `preceding` exceeds the rows shown, a quieter note says how many newer trades were booked since the traversal began.
- **Selected trade:** table rows are now keyed by trade id, not by index. An open trade drawer stays open when a refresh adds a newer row. This also applies to the other `RecordTable` users.
- **Freshness:** trade pages use `staleTime: 0`. Previous and Newest re-read at once; the cached read stays on screen, labelled with its read time, until the re-read arrives. This was found by the browser test: with the app-wide 15 s `staleTime`, **Newest** showed stale rows.
- **Errors:** an invalid cursor or a failure shows the "Data unavailable" alert with the backend error code, no rows, and **Return to newest trades** or **Retry request**.
- **DEMO mode:** without the `tradePage` capability, the fixture adapter renders an explicit "Unavailable" panel. Previously it stayed on "Loading fixture data…" forever.
- **Adapter:** `tradePage()` validates the full page contract. It rejects a response for a different cursor or filter, `has_more` without a cursor (or a cursor on the last page), counts that are not non-negative integers, more rows than `limit`, malformed page edges, a missing or malformed `history`, and a first page claiming a change.

## E. Concurrency results

The paging tests are backend (pytest), browser (live app over FastAPI) and component (vitest). The component and browser results use the controllable adapter and the real server respectively.

| Case | Backend (pytest) | Browser (live app) | Result |
|---|---|---|---|
| New trade inserted after page 1 | the traversal still equals the original set; later pages report `preceding` 51, 101, 151, 201; `changed` stays false | page 2 shows "Records 52–101 of 184" and the newer-trades note, no history warning; no row repeats; **Newest** shows it | no duplicate, no silent gap |
| Backdated insert behind the cursor | not in the ongoing traversal; `changed` false, false, then true on every later page; a fresh traversal includes it and does not warn | warning on pages 3 and 4 and after Previous back to page 1; **Newest** clears it and shows the trade | reported, not silent |
| Astra cancellation (120 open; 100 read; backdated insert + a shown trade closed) | page 3: `preceding` 100 and `total` 120 as before, `changed: true` (pre-correction logic: no warning) | `closed` filter, same shape: "Records 101–150 of 181" as before, and the warning | reported |
| Filter membership change | leaving `open` and entering `closed` in the read range are reported; status changes in `all` and changes in the unread range are not | (backend only) | pass |
| Warning persistence | a reverted change still reports `earlier_page` on all remaining pages | also in component tests | pass |
| Close during paging (`all`) | the row appears once, with the current `realized_pnl` | H0100 closes before page 3 loads; it appears once, closed, showing −3.25; no notice | pass |
| Close during paging (`open` filter) | the next page reports `preceding` 9 against the 10 shown and `changed: true`; no overlap; no other row lost | (backend only) | reported |
| Repeated page request | two reads with the same cursor are identical | (component: Previous shows the cached page, then re-reads) | pass |
| Late page response | (n/a) | page 2's response is held, the owner goes back to page 1, then the response is released: page 1 stays (component test agrees) | no stale overwrite |
| Filter change while a request is pending | (n/a) | the `open` request is held, the filter switches to `closed`, and `open` is released late: only `closed` is shown, at page 1 | no stale overwrite |

## F. Performance

`scripts/bench_trade_history.py` builds temporary journals of 10k and 100k trades (about 1 % open). Latency is over 40 untraced repeats; peak memory is one separate traced call (tracemalloc; SQLite's native allocations are not traced). Deep cursors are the endpoint's own anchor and digest, and the script asserts they equal the cursors of a real 4-page walk. Evidence: `2026-09-29-trade-history-paging-r1-evidence/performance.json` (regenerated in the second correction pass; the first-pass numbers are superseded).

The page size is 50 (maximum 200). "Response" is the JSON body size.

| Trades | Status / depth | Median | p95 | Peak traced Python memory | Response |
|---|---|---|---|---|---|
| 100k | all / first | 6.2 ms | 8.0 ms | 89 KiB | 30.1 KB |
| 100k | all / 50k | 139 ms | 156 ms | 90 KiB | 30.4 KB |
| 100k | all / last page | 250 ms | 283 ms | 89 KiB | 29.9 KB |
| 100k | open / first | 2.3 ms | 2.6 ms | 82 KiB | 28.6 KB |
| 100k | closed / last page | 258 ms | 286 ms | 89 KiB | 30.0 KB |
| 10k | all / 5k | 20.7 ms | 23.7 ms | 90 KiB | 30.3 KB |
| 10k | all / last page | 38.0 ms | 45.5 ms | 89 KiB | 29.9 KB |

**Complete traversal:** 10k trades, 200 pages of 50: 3.1 s in total (the cost of each page grows with its depth, so a whole traversal grows quadratically; 100k is not measured because it would take minutes).

**For contrast, at 100k trades:**
- full-history serialization: 3.3 s and 80.9 MB;
- legacy offset read of the deepest page: 10.4 ms.

**What is bounded and what is not.**
- Row reads and serialization are bounded: the page query is a covering-index `SEARCH` with no sort at every depth, about 0.4–0.7 ms at 100k (`query-breakdown-100k.txt`). Response size does not change with depth.
- The **history check is not constant-cost.** It reads every matching key between the anchor and the page, so its time grows with the traversal's depth: about 230–270 ms at the deepest 100k page (`query-breakdown-100k.txt`, `membership_deep`), which is most of that page's latency.
- **Sorting and allocation.** The first correction pass built the check with `group_concat(… ORDER BY …)`, whose plan added two temp B-trees for the aggregate ordering and held the whole range as one string (≈11.5 MiB traced Python at 100k deepest; Astra measured 7.7 MiB and 545–567 ms). The second pass streams the keys in index order and hashes them as they arrive: the plan is only the covering-index `SEARCH` (`plans.*.membership_status` in `performance.json`; guarded by `test_membership_check_streams_the_index_without_a_sort`), traced Python memory stays at ≈89 KiB at every depth, and the deepest 100k page fell to about 250 ms. SQLite's own page-cache reads still scale with the range.
- The two exact counts remain linear too:

| Query (100k trades, deepest page) | Median |
|---|---|
| page rows | 0.4–0.7 ms |
| `total` | 4.8–8.5 ms |
| `preceding` | 19–22 ms |
| history check | 230–270 ms |

**Suitability.** Production holds 65 trades; there, pages take about 1–2 ms (Astra's measurement on an isolated copy). At 10k trades the deepest page is about 40 ms. The check is acceptable at the current scale; it must be reassessed before claiming suitability for substantially larger histories. A constant per-page cost would need a write-side change record (for example a trigger-maintained revision in the journal), which is a schema migration and is deliberately not part of this package.

## G. Tests

**New:**
- `tests/test_trade_history.py`: 40 tests.
  - Traversal equals the fully sorted history for every filter and for page sizes 1, 7, 50 and 200.
  - Exact `preceding` and `total`; ties broken by id; the empty book.
  - Fields passed through unchanged and nothing derived.
  - New insert, backdated insert, and close during paging (`all` and `open`); repeated request.
  - 11 invalid-cursor variants; invalid status; limit bounds.
  - Index plans with no temp B-tree; the read connection cannot write.
  - The HTTP contract, a full HTTP traversal, and HTTP errors (400, 422 and 401).
- `frontend/tests/tradeHistory.test.tsx`: 19 tests.
  - The adapter URL and mapping.
  - 9 contract violations rejected; an invalid-cursor code; session expiry.
  - Paging with exact ranges and the search kept across pages.
  - A late response, and a filter change while a request is pending.
  - The history-changed notice; the drawer kept open through a refresh.
  - Invalid-cursor recovery; DEMO unavailability.
- `frontend/tests/browser/trade-history.spec.ts`: 9 tests (see E), plus:
  - full traversal and legacy parity;
  - search labelling and persistence;
  - backend 500 and retry;
  - an invalid cursor rewritten in flight;
  - session expiry while paging (signs out, shows no trade data);
  - an empty history.

**Updated:** `frontend/tests/browser/v21.spec.ts`. "Showing latest 3" became `trade-range` containing "Records 1–3 of 3 trades", because the old wording described the removed 50-row window.

**Added in the correction passes:**
- `tests/test_trade_history.py` (89 tests in total now):
  - 30 strict-cursor cases: invalid characters prepended or appended, padding, spare trailing bits, the standard alphabet, malformed JSON, version `true`/`false`/`1.0`/`"1"`/`null`, missing or extra fields, malformed anchor/digest/flag, duplicate keys, NaN, and lone surrogates in `a`, `o`, `i` and `f`;
  - HTTP 400 `invalid_cursor` with no rows for five mangled forms, including the surrogate anchor;
  - issued and legacy cursors still decode (legacy reports `unverifiable_cursor`);
  - Astra's cancellation case; unchanged traversals never warn; newest insertion is not a change; a single backdated insert; filter membership in both directions; the unread range; persistence after a reverted change; restart clears it; the HTTP history field;
  - the history-check query plan has no temp B-tree.
- `frontend/tests/tradeHistory.test.tsx`: history contract violations; the newer-trades note; the sticky warning with equal counts, through loading, later pages and Previous, cleared only by Newest; the unverifiable warning; a filter change clears it.
- `frontend/tests/browser/trade-history.spec.ts` (12 tests): a backdated insert warns on every later page until Newest; Astra's cancellation with equal counts; an unchanged traversal never warns; the newest-insert test now expects the newer-trades note instead of a warning.

**Negative controls.** Pre-correction logic accepts `!!!!`+cursor, cursor+`!!!!`, `v:true` and `v:1.0` and gives no warning in the cancellation case (preceding − shown = 0); the corrected logic rejects or reports each. The pre-correction `_text` check lets a lone-surrogate anchor through to SQLite, which raises `UnicodeEncodeError`; now it is `invalid_cursor`. The first-pass `group_concat` query plans two temp B-trees; the streaming query plans none.

**Results (first pass, kept for the record):**
- **pytest:** `tests/test_trade_history.py` 40 passed.
- **Full pytest suite** (`--continue-on-collection-errors`): 3481 passed, 55 failed, 12 errors.
  - **Baseline comparison.** I ran all 18 affected files in a scratch copy of this worktree with `journal.py` at `HEAD` (no new indexes, and none of this package's other files).
  - **Same failures.** 65 of the 67 failing test IDs fail identically there.
  - **The other 2** are in `test_research_universe_depth.py`. They skip when `data/candles.db` is absent, as it was in the scratch copy. In this worktree they fail because its `candles.db` has 0 discovery symbols at 4h. That is the candle store, not the trades journal.
  - **Causes:** missing untracked artifacts, `./venv/bin/python` missing from the worktree, and pre-existing cognition, M32, research and naked-position/partial-close failures.
  - **Conclusion:** none of the failures come from this package.
- **vitest:** 9 files, **70 passed**.
- **Playwright:** the full suite, **76 passed** (4.2 min). JSON is in `…-evidence/browser-results.json`.
- **Typecheck:** `tsc --noEmit` is clean.
- **Evidence preserved:** the full Playwright run rewrites some V2.1 evidence files (`frontend/evidence/luffy.png`, `performance.json`, `scale-performance.json`, `system-stale.png`). I backed that directory up before the run and restored it afterwards; it is verified byte-identical (145 files, sha256). The V2/live-perf screenshot output went to a scratch directory (`OWNER_EVIDENCE_DIR`).

**Results (second correction pass):**
- **pytest:** `tests/test_trade_history.py` 89 passed. With `tests/test_owner_*.py`, `tests/test_dashboard_*.py`, whole-trade accounting, single-creation-path and excursion: 624 passed, 1 failed (environment, below).
- **vitest:** 76 passed (backend-only changes in the second pass; frontend unchanged since the first).
- **Playwright** (full suite, run from `frontend/`): 79 passed, including all 12 trade-history tests.
- **Typecheck:** `tsc --noEmit` clean.
- Astra's independent first-pass recheck: 146 backend, 76 vitest, 79 Playwright, typecheck clean.
- **Evidence preserved:** the full Playwright runs of both correction passes rewrote 25 V2/V2.1 evidence files (`frontend/evidence/*.png`, `performance.json`, `scale-performance.json`, `v2/*`, `live-binding/performance-live.json`). They were restored from the R1 pre-run backup; `frontend/evidence/` is verified byte-identical to the R1 manifest again (145 files, sha256).
- `test_whole_trade_accounting::test_source_free_cli_replay_and_complete_import` needs `./venv/bin/python`, which this worktree lacks; it passes with the interpreter supplied (Astra).
- Playwright must run from `frontend/`: `v21.spec.ts` reads `dist/assets` relative to the working directory.

## H. Remaining blockers and caveats

- **History check cost grows with depth** (section F). Accepted by Astra at the current 65-trade scale; reassess before much larger histories. No journal migration is introduced.
- **The history check is a boundary comparison**, not an event log or snapshot: it reports a different set of trades in the range already read; a change that is undone between two reads is not reported, and then nothing was missed.

- **No production data inspected.** Whether every production `opened_at` value is uniform ISO-8601 was not checked. Paging correctness does not depend on it; chronological order does.
- **Count cost is linear** (section F), smaller than the history check.
- **Search is page-local by design.** Server-side text search is not implemented, and the UI says so.
- **Index creation on deploy.** The two new indexes are built once, the first time the updated Journal opens the production DB.
- **Pre-existing full-suite failures.** 55 failures and 12 errors, all shown to be independent of this package (section G). Examples:
  - `tests/test_exit_ab_shadow_*` needs `docs/superpowers/artifacts/exit-geometry/2026-09-19-prospective-ab-protocol.json`, which is untracked and exists only in the main checkout.
  - The naked-position/re-arm and partial-close tests fail on the baseline with identical messages.
  - These deserve their own investigation. They are not trade-history blockers.
- **Protection Snapshot live venue gate:** still deferred by owner decision.

## I. Changed files (this package only)

New:
- `trader/dashboard/trade_history.py`
- `tests/test_trade_history.py`
- `scripts/bench_trade_history.py`
- `frontend/tests/tradeHistory.test.tsx`
- `frontend/tests/browser/trade-history.spec.ts`
- `docs/superpowers/reports/2026-09-29-trade-history-paging-r1.md`
- `docs/superpowers/reports/2026-09-29-trade-history-paging-r1-evidence/` (`performance.json`, `query-breakdown-100k.txt`, `query_breakdown.py`, `browser-results.json`, `pytest-full-summary.txt`, `pytest-baseline-failures.txt`)

Modified:
- `trader/dashboard/owner_api.py`: the `/trades` route and the `trade_history` import
- `trader/core/journal.py`: two indexes in `SCHEMA`
- `tests/owner_frontend_fixture.py`: `add_history`, `insert_trade` (with an optional status), `close_trade` and `reopen_trade`
- `tests/owner_frontend_server.py`: `history`, `insert_trade`, `close_trade` and `reopen_trade` test hooks
- `frontend/src/styles.css`: `.history-warning` (correction pass)
- `frontend/src/adapters/contracts.ts`: the `TradePage` and `TradeStatusFilter` types, and `tradePage?`
- `frontend/src/adapters/live.ts`: `tradePage()`, including the `history` field
- `frontend/src/components/workspace.tsx`: `RecordTable` controlled search, `searchNote`, and id-keyed rows
- `frontend/src/views/Routes.tsx`: `TradeBook`, `Trades` and `Strategies`, replacing `Registry`
- `frontend/tests/browser/v21.spec.ts`: one assertion
