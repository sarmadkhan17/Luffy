# LUFFY-DASHBOARD-PERFORMANCE-V1 — implementation handoff (revision 4)

Status: **REVIEWABLE / NOT_DEPLOYED**. Not committed. No service restarted, no
watchdog state changed, no production data, venue or LLM contacted.

- Worktree / branch: `/home/sarmad/trader-dashboard-performance`, `dashboard-performance-v1`
- Baseline: `1eb00e151113d0add6033ef809ccdf6e30623003` (trader-world HEAD, verified)
- Implementer: Opus (owner-approved role swap). Revision 1 → Astra BLOCK (`/tmp/luffy-perf-astra/REVIEW.md`, 7 findings). Revision 2 → Astra BLOCK (`/tmp/luffy-perf-rereview/REVIEW.md`: 4 remaining groups, plus the `/api/v2/summary` decision). Revision 3 → Astra BLOCK (`/tmp/luffy-perf-r3/REVIEW.md`: four P2s; `/api/v2/summary` + 410 accepted). Revision 4 addresses those; see the next section. **A re-review is required.**

## Revision 4: third-review P2s → fixes → evidence

| # | Finding | Fix | Regression evidence (each mutation-checked: the test fails with the fix disabled) |
|---|---|---|---|
| 1 | Ticker routing re-read configuration when building the exchange | `Enrichment._exchange(route)` passes the route captured at admission explicitly: `make_exchange("futures", demo=(route == "demo"))`. Configuration is never re-read there. | `test_exchange_uses_the_captured_route_not_current_config` (config says production; admitted `demo` → `demo=True`) |
| 2 | A late page from an older book version was appended after Discovery reloaded | Pagination is bound to a client snapshot epoch. `loadDiscovery()` increments `bookEpoch`, and every `loadBookRest()` completion *or error* from an older epoch is discarded. A page whose `version` differs from its snapshot's also marks the list as changed. | browser `test_late_page_from_a_superseded_book_is_discarded` (A's page released after B loaded → not appended; "listed 500 of 502" kept) |
| 3 | Failed Agents/Company refreshes kept the old display unlabelled | On HTTP/network failure, the soft refresh keeps the display and labels it "refresh failed — showing data N old". The age runs from that display's own observation time (last successful load, adjusted by any snapshot age), not from the failure. | browser `test_failed_panel_refresh_is_labelled_stale` (HTTP 500 → both notes read "refresh failed") |
| 4 | The attention venue guard was weakened (zero-argument lambda → TypeError swallowed in the worker) | `venue_guard()` accepts any arguments, **records** attempts (account, tickers, exchange construction) and refuses them. The `server` fixture asserts `attempts == []` on the test thread at teardown. | `test_venue_guard_detects_attempts_negative_control` (identity resolvable → the guard records `account`, `tickers`, `exchange`; nothing cached) |

Browser-test harness notes (revision 4): Playwright `evaluate` awaits a returned promise, so the late-page test assigns and returns `0`. A delayed route is fulfilled from a task, because route handlers otherwise serialize.

## Revision 3: re-review blockers → fixes → evidence

| # | Re-review blocker | Fix | Regression evidence |
|---|---|---|---|
| R1 | Identity change during the first fetch leaked the previous account; an unresolvable identity kept the old value | The identity is bound **at admission**, atomically under the cache lock. A different identity drops the value and advances the generation *before* any job is joined or started. Each job carries the generation it was admitted under, and its success **or failure** commits only if that generation is still current. A job for the old identity keeps its worker until it really ends, and no replacement is started meanwhile (the new identity's request joins it and sees `unavailable`). Credentials are read once per refresh and captured in the job, not re-read inside it. An unresolvable identity (credentials missing or unreadable, routing unreadable) rebinds the entry to `unresolved`: value dropped, no job, `reason: identity_unresolved`. The same applies to the ticker routing. | `test_identity_switch_during_first_fetch_never_leaks_previous_account` (hold A's first fetch → switch to B → B sees no value, A's result is discarded after release, then B's own value), `test_unresolved_identity_fails_closed`, `test_failure_of_a_superseded_job_is_discarded_too` |
| R2a | Missing or future ticker times counted as fresh | Server and client: fresh requires a valid exchange observation time no more than 5 s ahead of the clock and within max age, plus a recent retrieval. Missing, invalid or future times are `unknown` (server) or not fresh (client). The section is `fresh` only if every symbol is. Future recorded times for equity/heartbeat are also never fresh. | `test_missing_or_future_ticker_time_is_not_fresh`; browser `test_lower_generation_and_missing_ticker_time` (no "prices fresh", STALE shown) |
| R2b | Generation 1 overwrote generation 2 | Client compares generations numerically within an instance: lower is rejected, higher wins, and for equal generations the later `retrieved_at` wins. A new instance (restart) supersedes. | browser `test_lower_generation_and_missing_ticker_time` ($2,000 stays after a generation-1 response) |
| R2c | A cached snapshot's `generated_at` was used as the server's current time | Every shared response carries `cache.served_at` (this response's server time). The client measures clock offset from `served_at`, so a stale fallback keeps its true age. Company, Agents, Discovery and the Memory graph now show "showing a snapshot N old — server busy/slow" when `cache.stale`. | `test_shared_responses_carry_their_own_serve_time`; browser `test_cached_overview_snapshot_is_aged_from_serve_time` (1-hour-old snapshot → "recorded 60m ago · STALE", "server busy") |
| R3 | "Load remaining" could claim completeness over a changing book | The book and its version (hash of the ordered rows) come from **one** SQL statement. `/api/pipeline` returns `book_version`, and `book_rest` carries `version=`. `/api/pipeline/book` answers **409 `changed`** if the book no longer matches. The client dedupes by id and, on 409, shows "population changed while listing — N rows, not complete" with a reload action. Completeness is never inferred from concatenated length. | `test_book_pages_are_bound_to_a_version` (insert between pages → 409, total 6); browser `test_changed_book_refuses_the_completeness_claim` |
| R4 | v2 summary published a partial wallet subtotal as `assets_total` | `assets_total` is null unless every asset converted. `assets_known_usd_subtotal` and `assets_total_complete` are added, and the completeness also appears in `freshness`. | `test_v2_summary_never_reports_a_partial_wallet_as_total` (100 USDT + 1 BTC → total null, subtotal 100, complete false) |
| — | Versioning decision | v2 moved to **`GET /api/v2/summary`**. `GET /api/summary` answers **410 Gone** with `use: /api/v2/summary` and migration guidance. It never silently serves v2 or fabricated v1 values. | `test_v1_summary_is_retired_with_migration_guidance`, auth test covers both paths |
| — | Expiry test ordering | The browser test now starts `refresh()` without awaiting it, waits until that response is actually held, induces the 401, then releases the late 200. | `test_session_expiry_conceals_data_and_stops_everything` |

## Revision 2: first-review findings → fixes → evidence

| # | Finding | Fix | Regression evidence |
|---|---|---|---|
| 1 | Session expiry left data visible and activity running | One client session lifecycle. All HTTP goes through a session-aware `fetch`. A 401 anywhere calls `endSession()`, which aborts every pending request (shared `AbortController`), bumps an epoch so late completions cannot render, stops all timers, closes the socket and replaces the whole document body with a sign-in notice. Attention/investigation panels stop polling when `luffySession.lost`. | `test_session_expiry_conceals_data_and_stops_everything` (browser): a held 200 response released after expiry does not render, neither `$1,000` nor the held value appears in the DOM, 0 requests in the next 12 s, socket null. Harness: `requests_next_17s: 0`, `equity_visible: false`, `ws_closed: true`. |
| 2 | Unknown accounting became zero | `realized_pnl_today` is null when any close today lacks P&L (`realized_pnl_missing`, `realized_pnl_known_sum`). The win rate uses trades with recorded P&L only (`winrate_basis_trades`, `pnl_missing`). Per-strategy `missing` counts. Exposure comes from a whole-book SQL aggregate: `long`/`short` are null unless every open row has notional, and `known_*` are labelled partial. A known empty book is still a true 0. The client renders "—" plus a partial/missing label. | `test_unknown_accounting_is_null_not_zero`, `test_known_empty_book_is_a_true_zero` |
| 3 | Enrichment freshness/ordering incomplete | Server: a partial ticker failure keeps the previous symbol price with its own timestamps and `last_error`. Per-symbol freshness requires both a recent retrieval and a recent ticker observation. Account `updateTime` is returned as `last_balance_change_at`, not as freshness. Each section carries `instance`/`generation`. Client: accepts a section only if it is not older (a new instance/generation supersedes; otherwise a later `retrieved_at` wins). Ages are recomputed locally from the sources' timestamps (clock offset from `generated_at`) every 5 s, so retained values turn stale by themselves. A failed request marks values stale with "last refresh failed". Marks derive from the accepted ticker version. | `test_partial_ticker_failure_keeps_old_price_as_stale`, `test_fresh_retrieval_of_an_old_ticker_is_stale`, `test_account_update_time_is_not_a_freshness_time`, browser `test_enrichment_ordering_failure_and_source_age` (older version rejected, failure labelled STALE, old ticker STALE) |
| 4 | Truncation concealed | Pipeline: `book_total`, `book_truncated`, `book_rest`, and a new paged `GET /api/pipeline/book?offset=&limit=` (authenticated, ≤500 per page). The UI shows "listed N of M strategies" with a "load remaining" action. Positions: `open_total`/`shown`/`truncated`, and the UI shows "showing N of M". The strategy P&L cap was removed. Agents show "showing 6 of N". The uPnL total needs every position marked, otherwise "k of n marked". | `test_pipeline_book_cap_is_disclosed_with_a_route_to_the_rest`, `test_position_cap_is_disclosed_and_exposure_covers_all`, browser `test_pipeline_truncation_is_disclosed` |
| 5 | Client single-flight per component, not per resource | `getJSON`/`gqlRead` share one in-flight request per identity (URL, or GraphQL query+variables) across all consumers. Every direct loader was routed through them. Reads are refused while hidden or signed out, including continuations after awaiting a library. Mutations keep `gql()`. | Browser `test_org_is_one_request_across_consumers` (Company + Agents + direct loaders → 1 request while held), `test_no_reads_while_hidden_even_after_a_library_await`; static test: only POSTs/mutations call `fetch` directly |
| 6 | Shared work could exhaust worker capacity | `SharedTTL` is async: waiters await the shared task on the event loop (no worker token). Only the computation holds one worker, until it really finishes. Admission is ≤32 waiters per key; refused or timed-out (10 s) callers get the last value marked `cache.stale` (with `reason`) or `503 {"status":"busy"}` + `Retry-After`. | `test_waiting_on_shared_work_holds_no_worker_token` (45 org waiters on a stalled computation; Overview answers < 2 s; 13 get 503), `test_shared_work_timeout_serves_stale_value_labelled` |
| 7 | Enrichment cache had no identity boundary | One `Enrichment` per app (no process-global cache or exchange). The account entry is bound to a fingerprint of the configured API key (sha256, internal, never returned). The ticker entry is bound to the routing (demo/production). An identity change invalidates before any request and bumps the generation. A still-running job keeps its worker, and its result is discarded if the identity changed. | `test_enrichment_cache_is_bound_to_app_and_account` (app A → A's value, app B → B's value, A after identity change → never A's old value) |

Harness caveats from the review were also fixed: the route is now context-wide, so the warm page is guarded. The hidden-return boundary is taken before the visibility event. The session check verifies removal and quiescence. Evidence logs were renamed `*.log.txt` so they are not gitignored.

## API versioning (owner decision applied)

- `GET /api/v2/summary`: the v1 keys composed from local data plus cached enrichment, never waiting on the venue. A missing or incomplete value is null (equity, control_state, market_type, exposure, total_upnl, assets_total). Exposure is entry notional. The full journal position rows are included, with `sl_dist`/`tp_dist`, and `mark`/`upnl`/`upnl_pct`/`mark_freshness` when marked. `assets` holds converted USD plus unconverted strings. `assets_known_usd_subtotal` and `assets_total_complete` are added. A `freshness` block gives per-source state and completeness.
- `GET /api/summary` (v1): **410 Gone**, with `use` and `migration` text. External v1 consumers fail explicitly rather than reading fabricated or silently changed values.

`/api/pipeline` gained `book_total`, `book_version`, `book_rest` and a stable tiebreak (`created_at DESC, id`). The book is still capped at 500. The partial-result contract is `book_truncated` plus version-bound `/api/pipeline/book` (409 on change). Shared endpoints (`/api/overview`, `/api/org`, `/api/pipeline`, `/api/vault/graph`) gained an additive top-level `cache: {age_s, stale[, reason], served_at}` key and may answer `503 busy` under saturation.

## BEFORE / AFTER (isolated, synthetic; 3 fresh-server runs per mode)

Measurement provenance: the tables below were measured on revision-3 code. Revision 4 changes only paths the measurements do not exercise: exchange construction arguments, Discovery pagination guards, panel failure labels and test guards. So it was not re-measured. Astra's independent revision-3 samples (cold 472/563 ms, warm 385/532, delayed script 432/564, stall 404/518 navigation/useful data) also meet the targets.

Same harness for both modes (`perf_server.py before` loads server + web assets from `git show 1eb00e1`). Synthetic store: 100k votes, 100k decisions, 10k closed trades, 1 fresh equity row, 0 open positions, 11 MB log. External calls simulated at 80 ms or 5.05 s; CDN libraries stubbed (Motion delayed 8 s in one scenario). Headless Chromium, loopback. **Not production measurements.** BEFORE runs are from the revision-2 batch (before-mode code and harness unchanged since). AFTER runs are fresh on revision-3 code. The VM remains noisy.

| Scenario | Usable navigation BEFORE → AFTER (median ms; after runs) | First useful LOCAL data BEFORE → AFTER (median ms; after runs) |
|---|---|---|
| Cold, 80 ms calls | 497 → 681 (778/552/681) | 1,428 → 879 (879/662/943) |
| Warm (guarded context) | 499 → 336 (336/315/420) | 579 → 512 (512/402/571) |
| Motion script delayed 8 s | **8,318 → 418** (374/418/435) | **9,351 → 568** (568/596/546) |
| 5.05 s per external call | 283 → 442 (442/346/473) | **60,961 → 620** (701/456/620) |

Every AFTER run met navigation < 1 s (max 778 ms) and first useful local data < 3 s (max 943 ms). The cold/stall navigation medians are slightly higher than BEFORE. That cost comes from the larger client script and session wrapper plus VM noise, and it stays within target. Disclosed miss: in one of three stall runs, the *extra* interaction measured during the stall (a navigation plus typing, beyond the target metric) took 1,082 ms; the other runs took 226 and 327 ms. Max concurrent browser fetches in the stall: 15–16 → 6. Wallet enrichment appeared at ~9.9 s under the stall, with local data already shown.

| API (80 ms model; cold / warm p95 n=30, ms) | BEFORE | AFTER |
|---|---|---|
| `/api/overview` (local) | — | 135 / 4.4 |
| `/api/enrichment` (optional) | — | 811 / 6.3 |
| `/api/summary` v1 → `/api/v2/summary` | 1,069 / 106 | 7.0 / 5.6 |
| `/api/org` | 87 / 9.7 | 722 / 5.6 (single cold outlier) |
| `/api/logs?lines=90` (11 MB log) | 84 / 92 | 8.4 / 7.7 |
| `/api/pipeline`, `/api/review_status` | ≤ 8.8 p95 | ≤ 8.6 p95 |
| GraphQL 25-trade list | 11.6 / 19.6 | 17.6 / 26.1 |
| `GET /` p95 during a 5.05 s stall | **60,218** | **10.5** |
| Local endpoint p95 during the stall | 2.6 (`review_status`) | 4.5 (`overview`) |

GraphQL 25-trade list (resolvers unchanged by this package): 11.6 / 19.6 ms before, 17.6 / 26.1 ms after (cold / warm p95), with stall p95 9.8 → 17.3 ms. The difference is within this VM's noise; Astra's independent re-review measured 9.38 ms p95 after. Behaviour checks (after, final code): 0 requests while hidden for 20 s, then 9 on return. WebSocket drop keeps the same document with exactly one replacement socket. Ten refreshes → 2 overview requests. An older overview is ignored. With enrichment aborted, local equity shows at 638 ms and the wallet reads "unavailable · last refresh failed". Session expiry: body replaced, 0 requests in 17 s, socket closed.

Resource probe (synthetic 100 ms SQLite writer, not the trading core; 4 clients polling every 1 s for 10 s): server CPU 3.74 s → 0.66 s; RSS 113 → 119 MiB; writer p95 7.1 → 7.2 ms, loop p95 7.5 → 8.1 ms under load (after idle: 5.3 / 5.8 ms); 0 lock errors; passive checkpoint busy=0. These numbers do not establish trading-core isolation. Seven `ClientDisconnect` tracebacks in the after-scenario logs come from GraphQL POST bodies cut off when the harness closes pages, as in before mode.

## Source, freshness and cache semantics (current)

- `/api/overview`: sections carry `source`, `observed_at`, `age_s`, `freshness`, `stale_after_s`. Equity is `journal.equity` (`venue_confirmed:false`, stale after 180 s). Control is `journal.state_kv` intent (`verified:false`, no ACTIVE default). SL/TP sit under `protection.venue_verified:false`. Unreadable sections are `unavailable`. Shared for 2 s; the value time is set at completion; `cache` metadata is included.
- `/api/enrichment`: `account` (freshness = retrieval time; `reason: identity_unresolved` when it failed closed) and `tickers` (per-symbol `price`, `source_time`, `retrieved_at`, ages, `freshness` fresh/stale/unknown, `last_error`; section `fresh`/`partial`/`stale`/`unknown`/`unavailable`), each with `max_age_s`, `instance`, `generation`, `refreshing`, `last_refresh_failed`. Marks are estimates. Account max age 60 s, tickers 15 s; 10 s failure back-off.
- Concurrency and identity: one job per key per app, admitted under the current identity/generation; callers join; the 1.5 s HTTP wait or a client abort neither cancels nor replaces a job; a job whose generation was superseded commits neither its value nor its error. Pool: 2 workers per app. Timeouts: account (3.05 s, 5 s); ticker 4 s per call; the 12 s ticker budget is checked before each call, so a job can overrun by at most one call timeout.

## Files

New: `trader/dashboard/local_view.py`, `trader/dashboard/enrichment.py`, `tests/test_dashboard_performance.py` (40 tests), `tests/test_dashboard_client_browser.py` (10 headless-Chromium tests; skip without Chromium), this report and `dashboard-performance-v1-20260927/`. Modified: `trader/dashboard/server.py`, `trader/dashboard/web/index.html`, `attention.js`, `investigation.js`, and `tests/test_attention_view.py`. In that file the `server` fixture's venue guard now records attempted account, ticker and exchange access and asserts none at teardown on the test thread (negative-control test in `test_dashboard_performance.py`), and the node mock `document` gained `hidden`/`addEventListener`. No existing assertion changed. `graphify-out/cache/last_query_stamp` shows as modified from the reviewer's disclosed graphify query. It is not part of this package and was left untouched.

## Tests

```bash
cd /home/sarmad/trader-dashboard-performance
/home/sarmad/trader/venv/bin/python -m pytest -q tests/test_dashboard_performance.py tests/test_dashboard_client_browser.py \
  tests/test_attention_view.py tests/test_dashboard_auth.py tests/test_dashboard_pipeline.py tests/test_company.py \
  tests/test_rent_wiring.py tests/test_theorist_removed.py tests/test_candle_cache_reuse.py \
  tests/test_single_creation_path.py tests/test_research_shadow_isolation.py
```

Result: **177 passed, 1 failed**. The failure is the pre-existing `test_harness_modules_satisfy_the_cognition_import_contract` (`HARNESS_IMPORTS`), identical on clean 1eb00e1. `git diff --check` is clean and `node --check` passes for all client JS. The browser tests were not run against the revision-1 client. They mirror the review's reproductions, but "fails on the old code" was not demonstrated.

Measurements: see `dashboard-performance-v1-20260927/` (`perf_fixture.py`, `perf_server.py`, `perf_browser.py`, `perf_api.py`, `run_part.sh`, `summarize.py`, raw JSON, `*.log.txt`, `manifest.sha256`). Run each part on a fresh server: `PERF_ROOT=… ./run_part.sh before|after scenarios|behaviours|api [rep]`; the fixture needs `org.yaml` copied into `$PERF_ROOT`.

## Remaining limitations

- Real chart/graph/animation/font/GPU costs are unmeasured (stubbed); CDN sources are unchanged and not bundled.
- `Journal(...)` still migrates once at dashboard startup. `org`, `pipeline`, `pipeline/book`, `review_status`, `last_autopsy` and GraphQL read through the write-capable Journal. GraphQL resolvers are synchronous: an inherited event-loop concern, not changed here.
- Physical work stays unbounded where no index exists: the decisions-today scan (one pass), the WS decision ordering, and the org vote/history scans. No schema or index change was made. The agents panel reads only the newest 2,000 votes.
- The legacy `/api/klines` still calls the venue (worker pool, ccxt default timeout). `initChatChart` is dead code.
- Client ages rely on the server/client clock offset measured at receipt, so network delay adds a small error.
- Harness artifact: the model-reset endpoint replaces the enrichment pool while old jobs may still run (max_concurrent 3). Production has no reset.
- Synthetic fixture with no open positions, so marks were exercised by unit and browser-route tests only.

## Chat-safety integration

Committed chat safety (da62d0a, 9aea9c6) touches `server.py` chat handler (`do_ops=False`, now line ~501 here), the `index.html` Freeze button (line 604) and the `sendChat` failure text (line ~1865). `git apply --check` of `1eb00e1..9aea9c6` for those files succeeds on this branch (offsets only). Semantic touchpoints for the combined regression:
- `setState`/`panic`/`closeTrade`/`sendChat` call `refresh()`, which is now the coalesced overview refresh.
- Mutations go through the session-aware `fetch`: after session loss they reject without a request, and the body has already been replaced.
- The Freeze button's `setState('FROZEN')` confirmation flow is unchanged.

The combined behaviour needs its own regression.

## Checks

`git diff --check`: clean. `graphify update .` was not run, because the package forbids editing `graphify-out/`.
