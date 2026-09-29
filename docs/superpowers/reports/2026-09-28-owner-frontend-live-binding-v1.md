# LUFFY-OWNER-FRONTEND-LIVE-BINDING-V1

**Status: implemented, uncommitted, NOT_DEPLOYED. Ready for Astra review.**
Implementer: Opus. Date: 2026-09-28.

- Worktree: `/home/sarmad/trader/.claude/worktrees/owner-frontend-live-binding`
- Branch: `owner-frontend-live-binding-v1`, based on production `7840965`
- Frontend source: `79d2df4` (Add owner frontend preview). `3f42acf` was not used.
- Nothing committed, deployed or restarted. Production, watchdog and control
  state untouched (FROZEN remains). `graphify update .` not run.

**Review aid.** The import was done with `git checkout 79d2df4 -- frontend/`, which
staged it. The index therefore holds exactly the reviewed `79d2df4` frontend;
`git diff -- frontend/` shows only this package's changes to reviewed files, and
untracked files are new. Nothing is committed.

---

## A. Imported frontend source

All 69 files of `79d2df4` (`frontend/` only) imported unchanged into a worktree
at `7840965`. Preserved: visual direction, LUFFY core/avatar interaction,
Knowledge layout, Live System topology/routing (F1), stale health presentation
(F2), accessibility, mobile list/detail behaviour, lazy graph/avatar/chart
chunks. No design round.

Changes to reviewed files are binding-only: mode-dependent copy, extra
LIVE-only fields and an explicit undrawn-connections notice.
Reviewed-behaviour check: all **16 reviewed browser tests** and **11 reviewed
component tests** pass against a DEMO build (see M). One regression I introduced (system
layout honouring node `x/y`, which moved the 40-node harness layout: 41/44
edges) was caught by the reviewed F1 test and fixed by making backend layout an
explicit opt-in field (`GraphNode.layout`). Reviewed layout is now identical.

## B. Live bootstrap contract

`GET /owner-api/v1/bootstrap` (behind the existing auth Guard):

```json
{ "mode": "LIVE", "principal": "owner",
  "session": {"authenticated": true, "method": "cookie|header|query", "expires_at": "…"},
  "backend": {"commit": "<git sha|null>", "started_at": "…", "source_time": "…", "frontend_build": "<sha256[:12] of dist/index.html>"},
  "owner_interface": {"configured": true, "check": "/owner-api/v1/owner-interface", "note": "…"},
  "providers": {"telegram": "CONFIGURED", "openclaw": "NOT_CONFIGURED", "whatsapp": "NOT_CONFIGURED"},
  "capabilities": {"voice": false, "chat": "text", "controls": ["freeze","halt","resume","unhalt","panic"]} }
```

- `principal` is resolved by the kernel's own `Authorizer.from_config(cfg)` for
  `(dashboard, session)`, not asserted by the client.
- Owner Interface **availability is not inferred from configuration**. It comes from
  a live `health` read (`GET /owner-api/v1/owner-interface`) through the existing
  dashboard gateway → `OwnerClient` IPC. The read returns `AVAILABLE` only on `ACCEPTED`.
- The frontend refuses any bootstrap whose `mode !== "LIVE"` (contract violation →
  "Cannot start").

**LIVE/DEMO isolation is decided at build time.** `src/main.tsx` branches on
`import.meta.env.MODE === "demo"`, a build-time constant. The production build is
LIVE and does not emit the DEMO branch or `adapters/fixture.ts`. Test: the emitted
JS contains no fixture strings (grep and a browser test). `PreviewProvider` no
longer has a default adapter. DEMO is reachable only through `npm run dev`
(`--mode demo`), `npm run build:demo`/`preview` (`.demo-dist/`) and the
regression harness (`--mode test-fixtures`).

## C. Overview binding

`GET /owner-api/v1/overview` is **local-first and does not call the network**. It reads the
journal, the heartbeat file and `state_kv.supervisor_status`. Each section is
isolated: a failed section returns `null` with a reason in `errors` and does not
break the page.

| Field | Source | Freshness rule |
|---|---|---|
| control | `state_kv.control_state` read **without** default (missing → `null`, not ACTIVE) + last `control_events` row | read time |
| account/equity | latest `equity` row (USDT, demo account) | stale > 300 s |
| equity series | `equity` rows, last per UTC hour, 168 h | — |
| realized today | journal trades closed today (UTC); no closed trade = real 0 | — |
| exposure | journal entry notional ÷ journal equity; any missing notional → `null` | — |
| heartbeat | `data/heartbeat_luffy.json` | stale > 240 s (stall-monitor default) |
| positions | `journal.open_trades()`; journal stop price + whether an order ref is recorded, labelled "journal record, not venue verification" | — |
| protection | Supervisor's last venue reconciliation (`checks.venue_protection`, `reasons`) | stale > 180 s (3 × 60 s interval) |
| needs you | Supervisor `needs_owner` + reasons | same as protection |

Per-position protection rules (`owner_api.protection_for`):

- `VERIFIED` needs all of the following: a fresh pass, `venue_protection` true, no
  `position_unprotected:<sym>` reason, and the position opened before that pass.
- A pass older than 180 s gives `STALE`, keeping `last_reported`. A stale result is never
  shown as current.
- `UNPROTECTED` comes from the pass's reason, `UNVERIFIED` otherwise, and
  `UNAVAILABLE` when there is no Supervisor evidence.
- Only a fresh `VERIFIED` gets the healthy (mint) tone.
- The Supervisor only runs passes at boot, in RECOVERY and on owner requests, so
  in production (FROZEN) protection will usually read **STALE**. That is the
  truthful state of the evidence.

**Optional enrichment.** `GET /owner-api/v1/enrichment/marks` reuses the existing
`_position_marks` (venue ticker through DataFeed, cached 20 s, 10 s timeout). It is
fetched only after the overview renders and never blocks it. Its output is labelled an
estimate and shows "Mark unavailable" on failure.

**Not reused.** The dashboard-performance APIs (`bcf245a`/`4a548db`) are **not on
production `7840965`**, so there was no deployed reviewed contract to bind to. The owner
API is a separate, read-only module and does not alter `/api/summary`.

## D. LUFFY chat binding

`POST /owner-api/v1/chat` calls the existing safe path `ChatEngine(journal,cfg).handle()`,
which the code documents as conversation only (never an owner request).

- Input limits: message ≤ 2000 characters; history ≤ 20 turns, each `{who: owner|Luffy, text}`
  and trimmed to 400 characters.
- Concurrency: at most 2 requests (`429 chat_busy`).
- Structured errors: `502 chat_failed`, and `503 llm_unavailable` when the agent returns its
  FALLBACK text.
- Every response carries `request_id`, `operational: false` and `evidence: []`. The backend
  supplies no evidence IDs, so none are shown.
- Cancellation, the "Cancelled — no reply" / "Failed — no reply" markers and the
  transcript behave as reviewed. Cancellation aborts the browser request, but the
  server-side LLM call runs to completion, because `ChatEngine` has no cancel hook.
- Voice remains unavailable.
- Tests: "freeze", "/panic", "resume", "unhalt" never reach the gateway and control state is
  unchanged (pytest + browser). Chat traffic is only `POST /owner-api/v1/chat`, and
  cross-origin chat gets 403 before any engine call.

## E. Owner-control binding

- **Route:** Operations. Freeze, Halt, Resume (guarded), Unhalt and Panic use the
  **existing GraphQL mutations** (`owner_control`, `panic`) → `_owner_gateway` →
  `OwnerClient` IPC → kernel `OwnerService`.
- **No other path:** there is no new control endpoint, state setter, browser-side
  fallback, or Risk/Execution/Binance authority.
- **Request IDs:** each intended action has one id and issue time, persisted with the
  **legacy dashboard's per-key protocol and prefix** (`luffy.owner.req.<id>`). Legacy and
  React therefore see each other's unresolved requests, and there is no whole-map
  read-modify-write.
- **Retry:** only `ACCEPTED/ACTIVATED/CONTAINED/ALREADY_SET/REFUSED` release the id. A lost
  answer (`null`) or `OUTCOME_UNKNOWN`/`IN_PROGRESS`/`UNAVAILABLE` keeps it, and **Retry**
  re-sends the same id and issue time without asking for consent again.
- **Fail closed:** controls are disabled unless the live health read returns
  `AVAILABLE`, including while a re-check is in flight. When the health read errors,
  controls stay disabled.
- **What is shown:** status, disposition, before → after state, reasons, request id,
  audit event ids and whether the result was replayed.
- **Tests:**
  - Resume produces exactly one `resume` Owner Interface call and returns `CONTAINED`.
    State stays FROZEN, and the browser's only POST is `/graphql`.
  - Gateway down: every control is disabled and only `health` calls are made.
  - Unknown outcome followed by Retry: the same `request_id` and `issued_at_ms` reach the
    gateway twice, and the second is `ACCEPTED`.
  - GraphQL idempotent replay.
  - `close_trade` through `owner_control` is refused.

## F. Knowledge binding

`GET /owner-api/v1/knowledge` reads the vault (`ROOT/knowledge`, which is `knowledge.vault.VAULT`).

- **Nodes:** notes, with kind = frontmatter `type` (or the folder), status, claim as
  summary, and file-modified time labelled `time_basis: file_modified`.
- **Typed relations:** frontmatter `relations` (supports / contradicts / works_in /
  fails_in / causes / precedes / derived_from), directed. Targets resolve by
  title or stem.
- **Links:** wikilinks, directed.
- **Unresolved targets are kept, not dropped:** they become edges to `unresolved:<name>`
  and are counted in `unresolved_edges`.
- **Lenses:**
  - Knowledge: available.
  - Timeline: available, labelled "file modification time — not event chronology".
  - Evidence and Code: explicitly **unavailable** (disabled with reason).
- **No paging:** at most 500 notes per response, with `truncated`/`total` shown (the
  current vault has 168 notes).
- **Layout:** nodes are ordered by degree and laid out on a deterministic grid over the
  visible scope (the backend supplies no coordinates).

The new notice **"N connections not drawn — see list"** covers every connection that
touches the visible scope but is not drawn, with its reason: endpoint record not
supplied, endpoint outside the current scope, or no route in this layout. It is
shown in both LIVE and DEMO graph modes. Nothing is dropped silently, and no links
are fabricated.

## G. Live System binding

`GET /owner-api/v1/system`:

- **Architecture:** 11 components with declared edges from the repository source map.
  These are labelled "declared architecture, not observed traffic", with source files.
- **Telemetry:** attached only where a real source exists.

| Component | Telemetry | Health |
|---|---|---|
| Kernel cycle | heartbeat file | active, stale after 240 s |
| Supervisor | `supervisor_status` | outcome → health, stale after 180 s |
| Orchestrator | latest decision | activity only → `UNKNOWN` |
| Journal | latest equity write | activity only → `UNKNOWN` |
| Research | latest `brain_events` row | activity only → `UNKNOWN` |
| Attention | `attention_health.json` | when present |
| Dashboard | this request | active |
| Market Data, Risk, Execution, Owner Interface | no source exposed | `UNAVAILABLE` |

`events: []` — there is no event stream, so there is **no activity animation**. The
UI shows that the event stream is absent. Stale telemetry uses the reviewed F2
presentation (STALE + "Last reported"). Tested: in the stale scenario only the
dashboard node remains `status-active`, and there are zero animated edges.

## H. Other routes

| Route | Binding | State shown |
|---|---|---|
| Trades | `GET /owner-api/v1/trades` (journal, ≤200) | **Partial** — no venue fills/commissions/evidence traces |
| Strategies | `GET /owner-api/v1/strategies` (registry state) | **Partial** — no admission evidence/decay |
| Diagnostics | `GET /owner-api/v1/logs` (≤200 lines, NUL-stripped, last 256 KiB) | **Partial** — no reason-code traces/investigations/attention |
| Research | none | **Unavailable** — no reviewed owner research contract; links to legacy `/` |
| Operations | Owner Interface (E) | Controls + health; close-trade/market-type not bound |

## I. Auth / session

The package reuses `DashboardAuth` (cookie `luffy_session`, HttpOnly, SameSite=strict,
12 h, HMAC). It makes three small changes:

1. The Guard serves the existing login page for `/owner-preview[/...]` as well as `/`,
   but not for `/owner-preview/assets/*`, which still get a JSON 401.
2. After a successful login the page reloads **the same path**
   (`location.replace(location.pathname)`), where it previously always went to `/`.
3. `session_info(scope)` reports the method and the verified cookie expiry.

| Case | Result |
|---|---|
| valid session | bootstrap, all owner routes 200 |
| expired session (clock +13 h) | 401 |
| forged cookie | 401 |
| 401 mid-session | `onUnauthorized` → QueryClient cancelled+cleared, `PreviewProvider` unmounted (transcript gone) → "Signed out" gate |
| time-based expiry | client clears at `expires_at` before any 401 (browser test with 6 s TTL) |
| unloaded lazy chunk after expiry | a chunk-load failure triggers a session re-check → signed-out gate, not a broken view (found and fixed during testing) |
| logout | `POST /auth/logout`, local clear, subsequent API 401 |
| unreachable backend | "Backend unreachable — no data is shown", exponential backoff 2→30 s, immediate retry on `online`; reconnect verified |
| private data after expiry | none: page body contains neither transcript nor USDT values; `localStorage`/`sessionStorage` empty |

**Kept across sessions by design:** unresolved owner-request ids (opaque ids, no private
data) so a lost control answer can still be resolved. **Existing limitation (not
introduced here):** logout deletes the cookie, but the session is not revoked server-side.
A copied cookie stays valid until expiry.

## J. Fixture isolation

- LIVE bundle: no fixture module or strings. This is checked by grep of `dist/assets/*.js`
  and by the browser test, which fetches each served script.
- The LIVE adapter (`adapters/live.ts`) imports no fixtures.
- Every failure rejects with "No substitute data was loaded", and responses are validated
  at runtime (`ContractViolation`).
- Test: `/overview` and `/knowledge` forced to 500 show an error with zero stat values,
  zero graph nodes and no "Synthetic/fixture/DEMO" text.
- The scenario selector and DEMO banner exist only in DEMO.

## K. Performance

This was measured against the live-contract test server, not production, with Chromium
151 headless at 1440×1080 on a 4 vCPU / 7.7 GiB VM. Timings include Playwright
observation. Source: `frontend/evidence/live-binding/performance-live.json`.

| Measure | Target | Result |
|---|---|---|
| Cold usable navigation (3 fresh, cache disabled) | < 1 s | 191–339 ms |
| Cold useful Overview (equity value) | < 3 s | 282–364 ms |
| Cold chart rendered | — | 301–390 ms |
| Cached route (15 samples) | < 300 ms | 42–81 ms |
| First Knowledge (lazy chunk + data) | — | 497 ms |
| First Live System / Operations / LUFFY | — | 90 / 355 / 57 ms |
| While Knowledge held 2.5 s: navigate away / type 5 chars / chat reply | responsive | 68 / 118 / 112 ms |

Overview does not wait for the graph, avatar, marks enrichment, LLM or imagery. The
graph, avatar, chart, Operations and Routes views are separate lazy chunks.

Bundle (LIVE build):

- Main JS: 326.5 KB raw, 102.2 KB gzip (Vite). The reviewed preview measured 311.7 KB raw,
  97.0 KB gzip with a level-6 measurement, so the increase is about +5 KB gzip. It comes
  from the adapter, contracts and LIVE copy.
- New lazy chunks: Operations 3.5 KB and Routes 1.4 KB gzip.
- No dependencies were added.

## L. Memory / resources

| Measure | Result |
|---|---|
| Browser JS heap, Overview before → after 108 route changes over all 9 routes | 4.06 → 7.43 MiB |
| DOM nodes / listeners, same | 910 / 200 → 941 / 204 |
| Knowledge / Live System heap | 7.35 / 7.51 MiB (1172 DOM nodes on Live System) |
| Test-server RSS, before → after browser churn + 400 owner-API reads | 63.5 → 63.5 MiB |
| Fresh-process RSS: `create_app` → after 200 owner reads → after `import trader.data.feed` | 54.7 → 60.3 → 102.8 MiB |
| WebSockets opened by the owner frontend | none (it polls: Overview 30 s, marks 60 s, Owner Interface 30 s, all visible-only) |

- Heap: the 3.4 MiB growth is a small sample. Listener and DOM counters are nearly flat.
  **No claim of long-duration leak absence.**
- Fresh-process RSS source: `scripts/measure_owner_dashboard_rss.py` →
  `evidence/live-binding/server-rss.json`.
- **No improvement over the 371 MB production legacy dashboard is claimed.** These are
  fixture-ROOT numbers.
- They show that the owner API itself is light (about 5.6 MiB after 200 reads), and that
  the DataFeed/ccxt import (about +43 MiB) is loaded in production by marks enrichment and
  by the legacy `/api/summary` and `/api/klines`.
- Production RSS with both UIs served has not been measured. It needs a later,
  authorized observation.

## M. Tests

| Suite | Result |
|---|---|
| `npm run typecheck` / `npm run build` (LIVE) | pass |
| Vitest (component/adapter/contract): 11 reviewed + 11 new (`tests/live.test.tsx`) | **22/22 pass** |
| Reviewed Playwright suites on DEMO build (preview, corrections incl. F1/F2, performance) | **16/16 pass** |
| New LIVE Playwright (`live.spec.ts`), real FastAPI app | **16/16 pass**, incl. axe WCAG 2 A/AA + 2.1 AA with zero violations on Overview, Operations, LUFFY, Knowledge, Live System and Trades |
| LIVE performance/memory (`live-perf.spec.ts`) | 3/3 pass |
| `tests/test_owner_frontend_api.py` (new backend contract) | **15/15 pass** |
| Related existing suites (dashboard auth/attention/pipeline, owner dashboard JS/browser, owner interface r1–r6, Telegram binding, chat agent) + new | **355/355 pass** |
| Full `pytest tests/` (fresh worktree) | 3328 passed, 55 failed, 12 errors — **none attributable to this package** (below) |
| Final combined Playwright run (reviewed + LIVE + perf) | **35/35 pass** |

**Full suite.** The failures and errors are in 18 files. None of these files imports
`trader.dashboard`, `owner_api` or the new fixtures:

- exit_ab_shadow ×2
- m32 ×7
- research_runner, research_universe_depth
- naked_position ×2
- partial_close, trade_booking, whole_trade_accounting
- conversion_evidence, cognition_contracts, population_cohort

The sampled causes are `FileNotFoundError` for untracked artifacts
(`docs/superpowers/artifacts/…`) and data-depth checks. Both depend on files and data
that a fresh worktree lacks. I did not re-run them on a separate base checkout, because
this session is isolated to its worktree. Treat them as environment-dependent, not
verified pre-existing on `7840965`.

Required proofs and where they are tested:

| Proof | Tests |
|---|---|
| LIVE failure cannot fall back to DEMO | `live.spec` "LIVE backend failure…", vitest contract/transport |
| Resume goes through the Owner Interface | `live.spec` "guarded Resume…", vitest, pytest |
| Chat cannot mutate controls | `live.spec`, vitest, pytest |
| Stale protection cannot appear healthy | `live.spec`, vitest, pytest |
| Owner Interface unavailable ⇒ controls fail closed | `live.spec`, 2× vitest, pytest |

Evidence handling:

- The reviewed suites write into `frontend/evidence/`. After the final run, their outputs
  were copied to `frontend/evidence/live-binding/demo-regression/` and the reviewed
  originals were restored byte-for-byte from `79d2df4`.
- Run commands: `npm run typecheck && npm run build && npm test`, then
  `LUFFY_PYTHON=<venv python> npx playwright test`. The third Playwright web server starts
  `tests/owner_frontend_server.py` on 127.0.0.1:4176.

## N. Legacy parity gaps (legacy stays at `/`)

| Area | React owner frontend |
|---|---|
| auth/session | parity (same auth), plus local expiry handling |
| status/health | control, heartbeat, Supervisor, Owner Interface health — parity for owner essentials |
| owner controls | freeze/halt/resume/unhalt/panic — parity. **Missing: close one trade, market-type switch** |
| positions/protection | journal positions + Supervisor-verified protection + mark estimates. **Missing: TP/SL distance, venue asset balances** (legacy `/api/summary` calls the venue per request) |
| chat | parity on the safe path; **no evidence IDs** (backend supplies none) |
| logs/diagnostics | log tail only. **Missing: investigations, attention panel, recovery ledger view, pipeline/research, org/agents deck, candles chart, doctrine, autopsy** |

The legacy dashboard is unchanged and remains the fallback.

## O. Deployment blockers / design for the later switch

**Blockers:**

1. **Review:** Astra review of this package.
2. **Build artifact:** `frontend/dist` is gitignored, and production has no built React
   app. Decide whether to build on the host (Node 24 toolchain) or ship a reviewed build
   artifact.
3. **Server-side session revocation:** absent (pre-existing).
4. **Legacy parity gaps (N):** close-trade and market-type are needed before `/` can move.
5. **Protection evidence:** Supervisor evidence is usually STALE while FROZEN. If the
   owner needs current protection verification without RECOVERY, that needs a reviewed
   read-only venue protection snapshot. That is a separate backend package.
6. **Production observation:** production RSS and latency with both UIs have not been
   measured.
7. **Supersession:** the dashboard-performance branch is not merged. Decide whether it is
   superseded or still needed for `/api/summary` (legacy).

**Route switch design (not performed):**

1. Build with `base: "/"`.
2. Mount the owner API unchanged.
3. Serve React `index.html` at `/` and assets at `/assets/*`.
4. Move the legacy `index.html` to `/legacy/`, fixing the legacy page's absolute
   `attention.js`/`investigation.js` paths or aliasing them.
5. Make the Guard serve the login page for `/`, `/legacy/` and client routes.
6. Keep `/legacy/` until the parity gaps close.

Rollback is re-pointing `/` to the legacy index.

## P. Exact changed files

**Backend (modified):**

- `trader/dashboard/auth.py` — preview login page, same-path redirect, `session_info`
- `trader/dashboard/server.py` — mounts `owner_api` with the existing gateway and `_position_marks`

**Backend (new):**

- `trader/dashboard/owner_api.py`
- `tests/owner_frontend_fixture.py`
- `tests/owner_frontend_server.py`
- `tests/test_owner_frontend_api.py`
- `scripts/measure_owner_dashboard_rss.py`

**Frontend (imported from `79d2df4`, then modified):**

- `.gitignore`, `index.html`, `package.json`, `playwright.config.ts`, `vite.config.ts`
- `src/adapters/contracts.ts`, `src/components/EquityChart.tsx`, `src/components/ui.tsx`
- `src/context.tsx`, `src/main.tsx`
- `src/views/{GraphView.tsx, Luffy.tsx, Overview.tsx, graphGeometry.ts}`

**Frontend (new):**

- `src/Shell.tsx`, `src/liveRoot.tsx`, `src/demo.tsx`, `src/lazyChunk.ts`, `src/live.css`
- `src/adapters/live.ts`, `src/adapters/pending.ts`
- `src/views/Operations.tsx`, `src/views/Routes.tsx`
- `tests/live.test.tsx`, `tests/browser/live.spec.ts`, `tests/browser/live-perf.spec.ts`,
  `tests/browser/liveHelpers.ts`
- `evidence/live-binding/*`

**Frontend (imported unchanged from `79d2df4`):** all other `frontend/` files.

**Docs:** this report.
