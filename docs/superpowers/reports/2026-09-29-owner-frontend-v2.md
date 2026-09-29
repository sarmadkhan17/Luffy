# LUFFY-OWNER-FRONTEND-V2

Implementer: Astra. Reviewer afterward: Opus (pending; no independent PASS claimed).
Worktree: `/home/sarmad/trader/.claude/worktrees/owner-frontend-live-binding`.
Base HEAD: `7840965d2a9003d2a80cbc2b0f3306e737dd4a7c`, with the pre-existing uncommitted LIVE-BINDING-V1 foundation preserved.

## A. Overall frontend status

Nine intentional owner surfaces implemented and exercised against the existing contracts. **Frontend implementation is ready for independent review; the complete live owner product remains PARTIAL because of data and legacy parity gaps. NOT_DEPLOYED.** No fixture substitution, new backend architecture, production access, service restart, watchdog change, commit, staging, route switch or Graphify update. Existing backend/auth/Owner Interface implementation is byte-for-byte unchanged from the start-of-turn hash capture.

Navigation remains exactly: Overview, Trades, Research, Strategies, LUFFY, Operations, Live System, Knowledge, Diagnostics. The existing maritime workstation design, tokens and graph renderer were retained. No template or mockup cycle; no dependencies added.

The foundation was already dirty/staged on arrival. Use `frontend/evidence/v2/baseline-sha256.json` and `changed-files.txt` for the V2 delta, not a bare diff against HEAD. The index still contains the pre-existing imported frontend; V2 changes are unstaged.

## B. Overview

Preserves fast local account equity, realized journal P&L, entry-notional exposure, open positions, optional venue-price estimates, Supervisor protection, control state and heartbeat. Adds bounded recent decisions, source/read timestamps, and explicit Risk/approval-object unavailability. Needs You still comes only from the Supervisor owner-attention record and retains stale warnings; it contains no fabricated approval objects. Mobile positions and decision rows stack, keeping protection and evidence controls accessible. Account table scroll regions have keyboard access.

## C. Trades

Searchable, status-filtered journal trade book with original-field detail drawers and entry/exit timelines. Source window explicitly limited to latest 50 trades. Journal strategy names, prices, sizes, leverage, timestamps, close reason, execution-mode field and booked realized P&L remain inspectable. Independent decision journal (latest 100) includes executed flag, action, score, threshold, confidence and rejection/skip reason. No decision-to-trade join is inferred. Open-position protection links to the existing Overview contract.

Missing: complete paginated history, venue fills/commissions, versioned execution lineage and per-trade autopsy links. Empty/missing values remain unavailable; returned numeric zero remains zero. The drawer does not treat journal values as fresh venue verification.

## D. Research

Replaces the empty route with an explicit Stage-3 workspace: Questions, Plans, Evidence, Results, Runs, Research Bank, Prior recall, Registrations, Costs and Shadow reports. All are marked unavailable because this checkout exposes no reviewed Stage-3 owner endpoint. Context navigation and the existing legacy pipeline remain reachable.

INCONCLUSIVE, NOT_ASSESSED, NOT_ESTABLISHED and context_only remain distinct; TESTED does not imply deployed. The Q→P→E→R strip describes the contract chain, not observed progress. No fake runs, costs, approvals or research activity. This surface is intentionally partial, not a claim of complete research binding.

## E. Strategies

Search/filter registry by lifecycle, inspect identity/origin/kind/retirement reason and creation timeline. Explicitly separates registry state from health/admission. No approval controls or strategy authority added. Versioned health, research linkage, parent/child relationships, survival/pruning evidence and trustworthy performance observations remain unavailable.

The existing `strategy_full` resolver defaults missing statistics to zero and does not provide their observation provenance. It is not used to imply performance availability or health. This is a concrete contract gap, not a fabricated zero-performance result.

## F. LUFFY

Conversation remains primary. Enlarged mechanical navigation core has restrained ambient depth and a dedicated presence area; the compact mobile core stays in the conversation header. Avatar, Motion and conversation chunks load lazily. The core displays only idle/pending/responding/error; cancellation returns it to idle while the transcript retains its cancelled marker. Composer remains editable during requests. Owner controls have a separate Operations link/card. Voice remains unavailable. No invented thoughts, autonomous activity or evidence IDs.

Existing cancellation semantics remain: browser request is aborted, but the backend LLM has no cancellation hook. The UI does not claim the server stopped computation. Chat cannot issue owner mutations. Existing replies carry no evidence identifiers; none are invented.

## G. Operations

Preserves Freeze, Halt, guarded Resume, explicit Unhalt and Panic via the existing Owner Interface. Confirmation, fail-closed availability, retained request ID/issue time, IN_PROGRESS/OUTCOME_UNKNOWN handling, audit IDs and same-ID retries remain intact. No direct ACTIVE setter or state-mutation fallback.

Adds current runtime evidence cards, independent recent decisions/rejections, and investigation dossiers/health from `/api/investigations/latest`. The runtime contract is declared architecture plus sparse telemetry, not an activity stream. Risk is unavailable, scanning is not inferred from an old decision, research events do not prove research health, and absent investigations are labelled with source status. The supplied test backend has no investigation ledger; that is shown as unavailable.

## H. Live System

Retains distinct runtime topology, documented directed connections, component selection, evidence/freshness, stale-versus-last-reported semantics, search and pan/zoom. The existing contract exposes no component event stream, so no flow animation runs in LIVE. Mobile defaults to list/detail; list height now follows its rows to avoid cutting a component at the viewport boundary. Existing routing/geometry negative controls passed.

## I. Knowledge

Retains Knowledge | Evidence | Timeline | Code lenses, search, selection, neighbor exploration and explicit directed path tracing, pan/zoom, typed relations, provenance and context_only limits. Unsupported Evidence/Code lenses remain disabled with explanations. Source time is file modification time, not evidence verification. Relation lists preserve connections that the renderer cannot draw, with the required “N connections not drawn — see list” notice. Mobile defaults to list/detail with natural list height.

Limits remain explicit: backend response capped at 500 notes, visible graph scope at 40; no backend pagination. Directed traces use all returned edges, including those outside the drawn scope, and list their source/target, type, relation and real evidence ID when provided. A missing path is explicitly limited to returned records. No claim that omitted backend records are drawn or locally searchable. Links are vault assertions, not verified causality or trading authority.

## J. Diagnostics

Replaces logs-only UX with current runtime evidence, investigation health/dossiers, dashboard/session identity, Owner Interface/recovery response, collapsible bounded historical log tail, historical-incident availability, and unresolved visibility limitations. Log file modification time is not current health; an empty tail is not a healthy verdict. Resource utilization, structured storage/candle-store warnings, watchdog, full recovery ledger and incident ledger remain unavailable. Raw logs are supporting evidence, not the page's primary surface.

## K. Mobile result

All nine routes tested at 390×844 and desktop at 1440×1080, with no document-level horizontal overflow. Tables become labelled record rows on mobile. Controls, drawers, chat composer/core, and graph list/detail are usable. Axe audits cover all nine routes in both sizes. Final screenshots cover all nine at both sizes (18 route captures, exceeding the requested 14), plus one path-trace interaction capture.

## L. Visual and motion result

Retained dark blue/teal workstation with restrained emerald/gold accents. Added consistent panel spacing, source strips, record drawers, status strips, timelines, search/filter controls, runtime cards and unavailable sections. Visually inspected rendered desktop routes and required mobile views; corrected flush panel text, wrapped status badges, mobile table scrolling and fixed-height graph lists. No WebGL/3D dependency, decorative motion, fake graph activity or animation delaying useful data. Reduced-motion and background/route-exit cancellation regressions pass.

Screenshot index: [all desktop/mobile captures](../../../frontend/evidence/v2/README.md).

Screenshots are **test evidence**, not a live production snapshot: real LIVE build served by the real FastAPI app over a temporary seeded journal/vault, with fake Owner Interface/chat. The runtime adapter remains LIVE; fixtures are confined to test infrastructure and the separate existing DEMO build.

## M. Performance

See `frontend/evidence/v2/performance-live.json` for loopback Chromium measurements: cold navigation/useful Overview, first lazy-route visits, cached navigation, delayed-graph chat responsiveness, 108 route changes and forced-GC heap. Final cold navigation: **171–204 ms**; useful Overview: **217–297 ms**; cached route samples: **27–106 ms**. During delayed graph loading, navigation to chat took **360 ms**, five typed characters **58 ms**, and the test reply **86 ms**. Browser forced-GC heap was **4.22 MiB before / 7.78 MiB after 108 route changes**, with Knowledge **7.65 MiB** and Live System **7.81 MiB**. Test-server RSS was **63.5 → 63.9 MiB** after churn and 400 API reads. No production speed, server RSS or memory-leak claim is made from these synthetic measurements. Initial-to-churn heap includes newly loaded lazy modules and cached queries; it is not an isolated retained-leak measurement.

The LIVE initial JS is approximately 322 kB raw / 101 kB gzip. Graph (~195 kB raw), avatar/Motion (~127 kB), chart (~167 kB), LUFFY, Operations and record routes are separate chunks. All nine routes are not loaded at startup. Optional decisions arrive after useful Overview data and do not block account rendering. Graph-heavy 500-input/40-visible coverage and 206-route DEMO churn results are retained under `evidence/v2/demo-regression/`; these are separate synthetic stress checks.

## N. Tests

- Typecheck and production build: PASS.
- Component/adapter tests: **29 passed**, including missing-versus-zero, drawer keyboard behavior malformed/partial contract refusals, directed cycles and missing-reference path refusals.
- Backend/auth regression: **15 passed** (`tests/test_owner_frontend_api.py`), isolated temporary root, no production effects.
- Full browser suite: **42 passed on the final source/build**; includes LIVE no-fixture-fallback, auth/expiry, unknown control outcomes, guarded Resume, chat safety/cancellation, stale/missing/error, graph topology and scale, route churn, motion and accessibility.
- Final suite also covers all five controls, IN_PROGRESS ID/time retention, directed path tracing, mobile path-trace accessibility, and refreshed LIVE performance. Earlier focused verification: 9/9 passed before path tracing was added. The optional path screenshot was then recaptured in mobile list mode with the same path assertions passing (1/1); runtime source did not change.
- The final session-expiry/cancellation test emitted a test-server `starlette.requests.ClientDisconnect` while an aborted GraphQL body was being read; the browser assertions passed. The existing backend disconnect handling was not changed.
- Earlier failures were addressed: old tests assumed no read-only GraphQL activity and one table on Trades; mobile Overview scroll region lacked keyboard focus; new five-control test needed to distinguish health reads from operations. No production defect was concealed as a test pass.

Reproduce from `frontend/`:

```sh
npm run typecheck
npm test
npm run build
LUFFY_PYTHON=/home/sarmad/trader/venv/bin/python npm run test:browser
```

## O. LEGACY_PARITY_GAPS

Source inventory: `trader/dashboard/web/index.html`, existing GraphQL queries/mutations, `trader/dashboard/server.py`, foundation report section N. Legacy files remain unchanged. In this base the legacy page is at `/`; this package does not change `/` or remove `/legacy/`.

| Function | V2 disposition |
|---|---|
| Login/session, five control operations, safe chat | Preserved |
| Equity, positions/protection, source timestamps | Preserved; estimates labelled |
| Decisions/rejections | Added bounded read/search/detail; full pagination/vote view remains a gap |
| Investigations | Added existing read contract and original dossier/health inspection; richer assessment presentation remains partial |
| Close one trade, market-type switch | Not added; separate guarded UX/parity gap |
| Venue asset balances, TP/SL distance | Not exposed by the reviewed owner contract |
| Strategy detail/performance book | Registry supported; legacy performance defaults cannot establish missing-data truth |
| Discovery pipeline, TV harness budget/funnel | Legacy only; not substituted for Stage-3 |
| Attention diagnostics, agent/org deck | Legacy only |
| Doctrine, full vault note reader, review/handoff status | Legacy only; Knowledge graph is not full note-reader parity |
| Autopsy controls/output, candle chart | Legacy only; no fabricated per-trade link |
| Complete trade history, recovery/incident ledger | Remaining parity/contract gaps |

## P. Backend/data gaps

No reviewed owner-facing Stage-3 contracts; no approval objects; no authoritative Risk-state response; no event stream; no versioned strategy health/lineage/performance provenance; no fill/commission/decision lineage; no chat evidence IDs or voice; no structured incident/resources/storage/watchdog contracts; bounded knowledge/trade/strategy responses without paging. Supervisor evidence can become stale while contained; the frontend preserves that truth rather than requesting recovery just to refresh it. Existing server-side session revocation and server-side chat cancellation limitations are unchanged.

## Q. READY_FOR_ROUTE_SWITCH: NO

Reason: legacy parity gaps, live data contract gaps, and pending independent Opus review. A passing UI test suite does not establish a complete owner operating system, approve a production change or authorize a route switch. No deployment or route switch performed.

## R. Exact changed files

The complete manifest is `frontend/evidence/v2/changed-files.txt`, with source hashes in `source-sha256.json`. The implementation/test delta is:

- `frontend/src/Shell.tsx`
- `frontend/src/adapters/contracts.ts`
- `frontend/src/adapters/live.ts`
- `frontend/src/components/PathTrace.tsx`
- `frontend/src/components/workspace.tsx`
- `frontend/src/live.css`
- `frontend/src/views/Activity.tsx`
- `frontend/src/views/GraphView.tsx`
- `frontend/src/views/Luffy.tsx`
- `frontend/src/views/Operations.tsx`
- `frontend/src/views/Overview.tsx`
- `frontend/src/views/Routes.tsx`
- `frontend/src/views/graphPath.ts`
- `frontend/tests/browser/live.spec.ts`
- `frontend/tests/browser/v2-controls.spec.ts`
- `frontend/tests/browser/v2.spec.ts`
- `frontend/tests/graphPath.test.ts`
- `frontend/tests/workspace.test.tsx`

Report and generated evidence are listed individually in the manifest. Existing root-level DEMO evidence overwritten by the old test suite was copied to `v2/demo-regression/` and restored to its unchanged staged import. The LIVE performance suite refreshes `frontend/evidence/live-binding/performance-live.json`; V2 retains its own copy.
