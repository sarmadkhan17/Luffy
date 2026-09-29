# LUFFY-OWNER-FRONTEND-V2.1 — focused correction pass

Date: 2026-09-29. Implementer: Astra. Independent reviewer next: Opus.
Worktree: `/home/sarmad/trader/.claude/worktrees/owner-frontend-live-binding`.
Base: the existing uncommitted V2 candidate, HEAD `7840965d2a9003d2a80cbc2b0f3306e737dd4a7c`.
Authority: the Opus independent review supplied by the owner in this conversation; no separate review file exists. This is implementation evidence, not an independent review or deployment authorization.

## A. Corrected findings

Opus T3 truth labels, T4 cancellation, T5 mobile discovery and T8 legacy destinations are corrected. T6 now binds existing Decisions offset pagination and provides an explicitly bounded Trades paging shell. T7 shows the authorized truthful fallback, **News guard: unavailable**; current enforcement is not established by a reviewed contract. Selected U/C/D/L defects are corrected: heading ornament, small repeated text, UTC formatting, table spacing, transcript height/mobile core, dead preview labels and production sourcemaps, Knowledge read cache, login route retention, exposure freshness and protection evidence checks. Research uses a directory/detail shell; Strategies has a clearer identity/lifecycle detail hierarchy.

No redesign, new visual dependency, kernel change, production runtime access, deployment, service restart, watchdog change, commit, route switch or graph update was performed. Existing staged and uncommitted foundation work remains intact. Graphify was queried read-only under repository instructions; its query-stamp side effect is separately disclosed below.

## B. LUFFY cancellation

Cancel and Send have distinct React keys, so activation cannot reuse a Cancel DOM button as a submit button. Cancel remains `type="button"`, prevents its activation default and cancels the pending request. It does not clear the draft. Deterministic browser probes hold the backend response pending, write a second draft, and cancel by click, Enter and Space: exactly one original POST, zero additional submissions, unchanged draft and an explicit cancelled/no-reply marker. Cancellation does not claim the backend never received the original request. Conversation remains separate from controls.

## C. Mobile navigation

A visible three-column, three-row navigation grid exposes all nine destinations, including **Operations / Controls · Panic**, without horizontal scrolling. Desktop route order is unchanged. Links retain native keyboard/touch behavior, focus styling and `aria-current`. Browser tests check all nine link bounds at 390×844 and activate each with the keyboard. All nine mobile routes are captured and checked for document overflow and accessibility.

## D. Truth labels

- Architecture components without a telemetry source say **NO TELEMETRY**. They are not inferred outages. Supporting provenance says no telemetry source/no observation time.
- Owner Interface topology and runtime cards use the existing health read, sharing the same query as Operations/Diagnostics. Available, unavailable, failed, stale and invalid-timestamp responses are distinguished. Health reads are polled every 30 seconds; observations older than 60 seconds or in the future are stale. No flow animation is inferred from health.
- Knowledge file modification timestamps carry age and **freshness not assessed**. A file's existence or mtime does not establish freshness. Page-level Knowledge/topology timestamps are labelled **Read**, not current system health.
- Open-trade rows and drawers say **Realized so far**; missing values remain unavailable. Positive, zero and negative realized-today values use mint, neutral and rose respectively.
- Exposure displays the equity source's freshness. Diagnostics uses mint for an available Owner Interface.
- VERIFIED requires the currently exposed venue-position, reconciliation and venue-protection checks, plus the existing backend timing/position-coverage conditions. Missing/false checks produce PARTIAL; aged evidence stays STALE. The adapter also refuses a VERIFIED claim lacking the three supporting checks. No periodic kernel verification was added.
- News guard explicitly remains unavailable. `news_guard` in the existing GraphQL schema defaults missing data to `active=False`; its raw timestamped flag has no current enforcement/freshness contract. Kernel usage describes threshold adjustment, so that flag alone is not evidence of current entry suppression. This pass does not label entries suppressed or clear based on it.

## E. Paging and completeness

Trades still reads the existing latest-50 endpoint. It displays **Showing latest N**, the cap, and that history is incomplete; paging controls are disabled because this endpoint has no cursor/offset contract. Search/filter operates on loaded records only.

Decisions uses the existing GraphQL `limit`/`offset` contract: 100 displayed records plus one lookahead, previous/next controls, and an explicit live-window warning. Offset queries are not a frozen snapshot and can shift as the journal changes. Compact Overview/Operations summaries show their displayed count and link to decision history. No decision-to-trade lineage is invented.

Backend requirement: expose stable bounded trade-history pagination (including next/previous or continuation semantics and ordering). Prefer snapshot/cursor semantics for stable traversal; current Decisions offset limitations are disclosed.

## F. Legacy destinations

Research and the intentional Operations legacy fallback target `/legacy/`. The misleading market-type-switch parity text was removed; that operation is outside this scope. No server route was switched or added: `/` still belongs to legacy, and `/legacy/` availability remains a separate routing concern until an authorized cutover.

## G. Visual and motion result

The heading's prohibited-sign-like ornament is removed. Repeated 9–10 px UI sizes in the shared stylesheet are raised to 11 px where used; timestamps use UTC to second precision. Workspace table vertical padding is reduced. The desktop transcript has a bounded viewport-related height; mobile core increases to 88 px. Knowledge ordinary-link labels no longer overlap borders: the link strokes, legend, relation list, path semantics and undisplayed-link counts remain intact. Its mobile explanatory footnote stacks vertically.

Research's ten repeated placeholder cards become a capability directory and one selected detail workspace. No results or progress are simulated. Strategy details group identity, registry ID, kind/origin, lifecycle timestamp and retirement evidence, preserving explicit missing authority/health/lineage contracts.

Production excludes dead DEMO UI strings and sourcemaps; the truthful `BINANCE_DEMO` account identifier remains. Graph, avatar and chart chunks stay lazy. No animation or library was added; reduced-motion, hidden-page cleanup and event-backed activity requirements remain in force.

## H. Authentication and deep links

Login preserves the current same-origin pathname and hash, strips query parameters, and explicitly reloads after successful authentication. Reload is necessary because navigating to the same hash URL alone can leave the login document in place. The signed-out shell also preserves the requested route on reauthentication. Browser coverage includes unauthenticated `/owner-preview/#/knowledge` and sign-out/sign-in from Knowledge. Cookie guards, expiry clearing, lazy-chunk expiry handling and private cache clearing remain unchanged.

## I. Knowledge cache

A small per-app read-layer cache retains one encoded response, at most 2 MiB and 20,000 source-file signatures. It writes nothing to the vault. Every read checks sorted relative paths, device/inode, size, nanosecond mtime and ctime; edits, additions, deletions and renames invalidate it. The parser's before/after signatures must agree; a moving source is retried once, then refused. Concurrent builds serialize under a lock. Oversized results are returned without retention.

Any source-read/fingerprint failure clears the cache. The endpoint returns 503 instead of serving the previous snapshot. HTTP caching stays disabled. Original snapshot `generated_at`, note modification times and source provenance are preserved on cache hits. Tests cover same-mtime edits, add/delete/rename, failed rebuild, continuously changing source, concurrent readers, size/file-count bounds, endpoint failure and auth.

## J. Performance before/after

Final measurements and scope are in `frontend/evidence/v2.1/performance-live.json` and `knowledge-cache-performance.json`. Browser measurements use an isolated FastAPI fixture server and fake Owner Interface/chat, not production. V2 figures are saved pre-pass evidence, not a controlled contemporaneous A/B test.

| Measurement | V2 saved baseline | V2.1 |
|---|---:|---:|
| Cold usable navigation | 171–204 ms | 211–315 ms |
| Cold useful Overview | 217–297 ms | 296–349 ms |
| Cached route visits | 27–106 ms | 49–165 ms |
| First Knowledge visit | 407 ms | 993 ms |
| First Live System visit | 128 ms | 93 ms |
| Type five chars while graph loads | 58 ms | 108 ms |
| Heap before → after 108 route changes | 4.22 → 7.78 MiB | 4.3 → 7.86 MiB |
| Test server RSS before → after 400 reads/churn | 63.5 → 63.9 MiB | 63.6 → 64 MiB |

All sampled navigation, useful-Overview and cached-route targets pass. First Knowledge visit is close to the 1-second target in this run; remeasure on the deployment candidate before cutover. These small samples and different host loads cannot establish a causal speed regression/improvement. Lazy module/query retention contributes to the post-churn heap increase; this is not a long-duration leak proof.

The final LIVE entry bundle is approximately **321 kB raw / 100.3 kB gzip**, versus V2's approximately 322 / 101 kB. Graph, avatar/Motion and chart remain separate lazy chunks; the build audit records exact emitted sizes.

Read-layer comparison on this worktree's **171-note, 278,932-byte** vault (same corpus and compact encoding): uncached median **349.47 ms**, cold-cache median **396.15 ms**, warm-cache median **15.56 ms** across 20 warm reads. The cold cost includes two signature scans. This differs from Opus's 252-note/403 KB corpus; do not compare the absolute timings as equivalent workloads. No production memory measurement was made.

## K. Tests and visual evidence

| Check | Final result |
|---|---|
| Typecheck | PASS |
| Production build | PASS; no sourcemaps or fixture/dead DEMO labels |
| Component/adapter tests | 35/35 |
| Affected backend tests | 24/24 |
| Playwright full suite | 59/59, 4.1 minutes |
| Axe, all nine desktop + mobile routes | 18/18 combinations; zero violations |
| Mobile, graph geometry/path tracing, reduced motion | PASS |
| Auth/session expiry, login + reauthentication route retention | PASS |
| All five owner controls, durable retry IDs, unknown/in-progress results | PASS |
| Cancellation with draft: click / Enter / Space | PASS; zero additional submissions |
| LIVE error/no-fixture-fallback and build audit | PASS |
| 108-route LIVE churn, graph-heavy load/chat responsiveness | PASS; measured above |
| Whitespace check | `git diff --check` clean |

New correction regressions live in `tests/browser/v21.spec.ts` (17 cases), `tests/v21.test.tsx` (6 cases) and the affected Python tests. Existing assertions were updated for the intentional larger core, additional observed Owner Interface health and accessible mobile navigation hint; safety checks were preserved. During development, browser tests caught a same-fragment login reload bug and missing non-color styling on the new legacy link. Both were fixed and are covered by the final successful run.

The final desktop screenshots cover Overview, Trades, Research, Strategies, LUFFY, Operations, Live System, Knowledge and Diagnostics. Mobile captures cover all nine routes, including the requested five. `accessibility.json` records the 18 route/viewport checks; `mobile-path-trace.png` adds the Knowledge detail state. Actual rendered screenshots were visually inspected. Prior V2 and foundation evidence is preserved; this package's evidence is under `frontend/evidence/v2.1/`.

No full-repository pytest rerun is claimed. Opus's supplied review established that V2's broad-suite failures matched its clean baseline; this pass reruns affected dashboard/backend tests plus frontend suites. That historical result is attributed to Opus, not newly reproduced here.

## L. Remaining frontend-only blockers

No known unresolved frontend-only P1 defect within this correction pass, subject to Opus re-review. Trades cannot traverse beyond its bounded endpoint; the UI shell is ready but requires the backend contract. Research remains capability-blocked; Strategies remains a read workspace over limited fields. Entry/exit price and leverage remain available in the trade drawer rather than new dense-table columns. Cross-record decision/trade selection is not added without real lineage. These limitations are not represented as completed functionality.

## M. Backend and separate-package blockers

- **LUFFY-PROTECTION-SNAPSHOT-R1**: kernel-owned periodic verification in every control state, with timestamped read-only snapshot. Normal ACTIVE/FROZEN operation can still age the current Supervisor evidence to STALE.
- **LUFFY-OWNER-CLOSE-TRADE-R1**: reviewed close-one-trade binding, confirmation and durable Owner Interface request/retry semantics. No close-trade action is implemented here.
- Trade-history paging contract and authoritative current news/entry-suppression evidence remain parity gaps.
- Stage-3 owner endpoints, strategy health/performance/lineage, forensic joins, structured diagnostics, note bodies and voice remain the already-disclosed capability gaps.
- Opus's pre-existing chat default-ACTIVE and copied-cookie revocation observations are not altered by this frontend correction.

## N. Route-switch recommendation

**Frontend correction candidate: ready for Opus review after the recorded checks.**
**ROUTE_SWITCH: NO.** No known scoped frontend-only P1 remains, but the two separate packages, trade-history/current-news parity requirements, independent re-review and explicit cutover authorization are still outstanding. This package does not authorize or perform `/` switching, `/legacy/` removal, deployment or production measurement.

## O. Exact changed files

The V2.1-only source/test/config delta is listed below and in `frontend/evidence/v2.1/changed-files.txt`, compared with the pre-pass SHA-256 inventory (the worktree was already dirty). `artifact-files.txt` lists every new evidence/report artifact. `delta.patch` records source changes against that captured V2 baseline, including the explicitly listed new files. The full original foundation manifest is not being claimed as V2.1 work.

- `frontend/src/Shell.tsx`
- `frontend/src/adapters/contracts.ts`
- `frontend/src/adapters/live.ts`
- `frontend/src/components/HealthStatus.tsx`
- `frontend/src/components/ui.tsx`
- `frontend/src/components/workspace.tsx`
- `frontend/src/context.tsx`
- `frontend/src/live.css`
- `frontend/src/liveRoot.tsx`
- `frontend/src/styles.css`
- `frontend/src/time.ts` (new)
- `frontend/src/useOwnerTelemetry.ts` (new)
- `frontend/src/views/Activity.tsx`
- `frontend/src/views/GraphView.tsx`
- `frontend/src/views/Luffy.tsx`
- `frontend/src/views/Operations.tsx`
- `frontend/src/views/Overview.tsx`
- `frontend/src/views/Routes.tsx`
- `frontend/tests/browser/corrections.spec.ts`
- `frontend/tests/browser/live-perf.spec.ts`
- `frontend/tests/browser/live.spec.ts`
- `frontend/tests/browser/preview.spec.ts`
- `frontend/tests/browser/v2.spec.ts`
- `frontend/tests/browser/v21.spec.ts` (new)
- `frontend/tests/v21.test.tsx` (new)
- `frontend/vite.config.ts`
- `tests/test_owner_frontend_api.py`
- `tests/test_owner_read_cache.py` (new)
- `trader/dashboard/auth.py`
- `trader/dashboard/owner_api.py`

Tool side effect outside the package: `graphify-out/cache/last_query_stamp` was touched by the required read-only query. No graph generation/update was run. Existing legacy web assets, dashboard server wiring, root routing, kernel and watchdog were not changed by V2.1.
