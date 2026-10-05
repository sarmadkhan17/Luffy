# OWNER-FRONTEND-V1: isolated baseline profiling

Scope: **baseline profiling only**, per the owner's latest instruction. Frontend replacement has not started. NOT_DEPLOYED; no commit. The full milestone is not closed.

Checkout `/home/sarmad/trader-world`, branch `world-model-foundation`, base HEAD `1eb00e151113d0add6033ef809ccdf6e30623003`. The original server was loaded directly from this commit for the browser and HTTP baseline. The prior turn's worker-pool patch in `trader/dashboard/server.py` and its responsiveness test remain present, but were excluded from these original-HEAD measurements. `OWNER-FRONTEND-V1.md` is the owner's supplied, untracked brief; it was read and not changed. SDD/STATE/NEXT, production and the health-production candidate were not modified.

## Finding

The primary reproduced delay is serial external I/O on the useful-data path. `/api/summary` awaits no async work: its original async handler synchronously calls `_account_snapshot()` (two HTTP requests), `_position_marks()` (one ticker per open position), and `_universe_prices()` (ten serial ticker requests). Those waits occupy the server event loop. The browser's `refresh()` waits for GraphQL status and then this summary before displaying journal equity and other Overview data.

With no open positions, twelve simulated 5.05-second waits caused **60.715 seconds of browser-observed summary fetch latency** and **60.912 seconds until the journal equity value appeared**. This reproduces the reported symptom through a demonstrated mechanism. It does **not** establish the actual production venue latency or prove this is the only production cause. No venue was contacted.

Independent secondary findings:

- Parser-blocking Lightweight Charts, vis-network and Motion script tags precede the application script. Adding an isolated eight-second delay to the Motion script moved navigation/composer readiness to **8.332 seconds**. Overview's first displayed journal value arrived at **9.296 seconds**.
- `setInterval(refresh, 8000)` has no in-flight guard. During the slow summary, the browser reached **15 concurrent fetches**. Seven periodic status requests queued before the summary completed. Attention and investigation polling each incurred six five-second aborts. Those panels are logically separate, but event-loop blocking still delayed their service.
- Cache timestamps are recorded before network work: the ten-ticker operation takes 50.5 seconds in the slow model, exceeding its 15-second TTL before it returns. Slow service can therefore leave the price cache already expired and subsequent refreshes repeating the expensive path.
- Cold SQL totaled **81.14 ms** in the direct-handler probe. The latest-agent subquery scanned the votes index (~21 ms); two decision aggregates took ~22 ms each; closed-trade aggregates scanned closed rows. These are real scaling concerns, but did not explain this fixture's minute delay.
- A normal WAL read during an active writer took **0.076 ms**. By contrast, constructing `Journal` while a writer was held for 250 ms took **335.60 ms**, because initialization executes schema/migration/backfill statements. This is a startup lock risk distinct from ordinary read contention.
- Payloads were small: document **133,770 bytes**, summary **1,263 bytes** over HTTP, and the tested 25-trade GraphQL list **2,096 bytes**. Full graph/history serialization was not on the measured Overview startup path. The direct-handler JSON dump was 1,398 bytes because its serializer uses different whitespace.

## Conditions and measured results

Linux 7.0.0-31-generic x86_64 on VMware, four visible AMD Ryzen 5 5600H logical CPUs, approximately 7.7 GiB RAM. Headless Chromium **151.0.7922.34**, 1440 × 1000 viewport, local loopback server on port 18786, no CPU/network throttling except explicitly simulated dependencies.

The synthetic SQLite/WAL fixture contains 100,000 votes, 100,000 decisions, 10,000 closed trades, one equity row and no open positions. Initial DB plus sidecars: **66,719,744 bytes**. It is a query-shape fixture, **not production-equivalent**: uniform timestamps and shared cycle IDs, no populated research/Attention/investigation stores, and no open-position verification workload. The synthetic equity value was read from the fixture database, not substituted in browser responses. All screenshots carry an explicit synthetic/simulated banner.

All browser external requests were intercepted locally or aborted. Lightweight Charts used a local no-op shim; vis-network and Motion were empty. Consequently, actual third-party parsing, graph layout, chart rendering, WebGL/GPU work and external font performance are **not established**. No LLM, paid service, venue or production API was called. Server network/DNS and non-fixture data access were guarded. The legacy UI's existing default ACTIVE/zero/freshness labels were not corrected in this profiling task and are not evidence of verified protection/control.

| Original-HEAD browser condition | Document first byte | Navigation + composer verified | First journal equity displayed |
| --- | ---: | ---: | ---: |
| Cold browser/server adapters, 80 ms per simulated call | 25.0 ms | 1,276.8 ms | 1,634.2 ms |
| Same browser context, warm server adapter caches | 9.3 ms | 251.8 ms | 377.4 ms |
| Fresh context/adapters, Motion script delayed 8 s | 7.6 ms | 8,332.0 ms | 9,295.5 ms |
| Fresh context/adapters, 5.05 s per simulated call | 6.6 ms | 314.7 ms | 60,912.4 ms |

Each browser condition was run once. Readiness includes an actual navigation click and typing into the composer without sending a chat request, then returning to Overview. These are instrumented scenario observations, not browser p95 or production acceptance results. Playwright routing disables browser HTTP caching; “warm” here means same browser context and server adapter caches. OS disk caches were not flushed. A skeleton/spinner was not counted as useful data.

Cold-browser CDP script time was **25.0 ms**, layout time **314.1 ms**, total task time **880.2 ms**, and JS heap **3.10 MB** at capture. Its longest observed main-thread task was **229 ms**. Warm values were **12.0 / 81.1 / 294.8 ms**, heap **3.09 MB**, longest task **52 ms**. The slow-network run had only **1.57 s** total browser task time over approximately 61 seconds; network/server waiting dominated. Browser instrumentation and the navigation/composer exercise contribute to these costs.

Separate loopback API run, original HEAD, fresh adapter caches then 30 sequential warm requests:

| API | Cold response | Warm p95 (nearest rank, n=30) |
| --- | ---: | ---: |
| Summary, 80 ms simulated network | 1,063.1 ms | 95.3 ms |
| GraphQL 25-trade list | Not isolated as cold | 11.3 ms |

All 61 API requests returned HTTP 200; the list returned no GraphQL errors. The list's SQL-only p95 over 25 samples was **3.70 ms**. These are selected endpoints, not coverage of every dashboard API. No post-fix or target-achievement claim is made.

## Isolated resource/lock observations

A separate 100 ms cadence synthetic SQLite write/read loop was measured with the browser closed and then during the above API burst. It is **not the actual trading core**, and the burst is heavier than ordinary eight-second polling.

| Measurement | Closed/idle (3.00 s) | API burst (3.85 s) |
| --- | ---: | ---: |
| Dashboard server CPU seconds | 0.01 | 2.77 |
| Server ending RSS | 108.90 MiB | 112.77 MiB |
| Synthetic writer p95 | 3.24 ms (29 samples) | 7.71 ms (37 samples) |
| Synthetic write/read loop p95 | 3.51 ms | 9.60 ms |
| Recorded lock errors | 0 | 0 |

A passive checkpoint on the isolated store returned `(busy=0, log_frames=658, checkpointed_frames=658)`. This does not establish production WAL growth or safe production cadence. The browser-run probe samples are also retained, but an intentional 250 ms writer-lock experiment overlapped that run; they must not be used as an uncontaminated isolation comparison. The separate table above was collected afterward without that injected lock.

## Proposed fix — not implemented in this scope

1. **Make first useful Overview data independent of venue requests.** Add a thin authenticated, read-only local snapshot endpoint for the bounded journal/account/control evidence that actually exists. Return source timestamps and explicit missing/stale/unverified states. Account balances/marks absent from an existing trustworthy snapshot remain unavailable; do not present stale protection/control as freshly verified or invent a new collector to populate the screen. Load optional prices/wallet enrichment separately from useful data.
2. **Keep synchronous work off the API event loop.** Retain/review the earlier worker-pool fix, but do not mistake it for a latency fix: the measured earlier cold-handler wall time remained about 1.06 seconds with 80 ms network waits. Use bounded concurrency and avoid performing heavy verification in the initial Overview request.
3. **Remove duplicate refresh work.** Use one in-flight request per resource, completion-based scheduling, cancellation on navigation, and visibility-aware polling/subscriptions. Preserve source time separately from retrieval/cache time, and scope caches by source. Avoid a stale cache being advertised as fresh authority. Replace WebSocket-triggered page reload with bounded reconnect handling in the eventual frontend implementation.
4. **Remove parser-blocking optional assets from startup.** Bundle essential assets locally; load graph/3D/animation code only for the visible tab. Useful Overview data and navigation must not depend on them. Measure actual libraries later; the stubbed baseline cannot certify rendering budgets.
5. **Keep read startup read-only and bound queries.** Avoid `Journal.__init__` migration/backfill on the owner read path. Consolidate the repeated daily aggregates and defer nonessential full-history/grouped queries. Propose any index only after a separate measured query comparison; no index/schema change was made here. Preserve full source verification for detail/approval flows rather than weakening it to hit a summary target.

Proposed affected areas for a later authorized implementation: `trader/dashboard/server.py`; a dedicated read-only owner adapter under `trader/dashboard/`; the approved React/TypeScript/Vite frontend; focused API/browser tests and measurement scripts. No changes to trading authority, verification predicates, SDD/STATE/NEXT or production operations are needed for this proposal. The frontend replacement remains paused under the owner's latest instruction.

## Evidence and reproduction

Evidence directory: [owner-frontend-baseline-20260927](owner-frontend-baseline-20260927/manifest.json). It contains raw timings, SQL plans, request traces, screenshots and profiling scripts with SHA-256 manifest. The baseline server script loads the original server from the exact Git SHA; fixture and scripts use only `/tmp/owner-frontend-v1` and loopback 18786. The profile-specific model endpoints exist only in that isolated harness.

From `/home/sarmad/trader-world`, with the retained `/tmp/owner-frontend-v1` directory and no other process on port 18786:

```bash
mkdir -p /tmp/owner-frontend-v1
/home/sarmad/trader/venv/bin/python docs/superpowers/reports/owner-frontend-baseline-20260927/fixture_profile.py
/home/sarmad/trader/venv/bin/python docs/superpowers/reports/owner-frontend-baseline-20260927/browser_server.py
# In another terminal:
/home/sarmad/trader/venv/bin/python docs/superpowers/reports/owner-frontend-baseline-20260927/browser_profile.py
```

The slow-network browser case can leave already queued requests executing synchronous waits after the page closes. Stop **only this isolated server**, then start a fresh harness process before running `api_profile.py`; the recorded API run did this. Run `sql_profile.py` separately from the uncontaminated API/resource probe. `PROFILE_DELAY_S=5.05 PROFILE_OUTPUT=baseline.json` selects the 60-second direct-handler reproduction with `fixture_profile.py`; default is 80 ms. Re-running overwrites temporary outputs, not the retained evidence directory. No production configuration is loaded.

The isolated servers started for this profile were stopped. No production process, watchdog or control state was changed. Unrelated dirty files were preserved. Current-turn repository additions are this report and its evidence directory only. The prior responsiveness patch/test were neither expanded nor reverted. `git diff --check` passed; no new application code or frontend tests were warranted for this narrowed profiling-only turn. Prior-turn dashboard regression results remain **8 passed**, not rerun/claimed as new acceptance evidence.
