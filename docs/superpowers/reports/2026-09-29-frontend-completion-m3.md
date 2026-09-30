# LUFFY-FRONTEND-COMPLETION-M3: final product and visual completion

## A. Status

**M3_STATUS: PASS**, based on the evidence in §H–§K. This includes the MUST FIX for the mobile LUFFY conversation (§F).

| | |
|---|---|
| Worktree | `/home/sarmad/trader/.claude/worktrees/owner-frontend-m3` |
| Branch | `owner-frontend-m3-v1` |
| Base | `dc78d233b1e47003b0496d1ee3ed654c721a3773` |
| Head | `dc78d233b1e47003b0496d1ee3ed654c721a3773`. All work is **uncommitted**. |
| Deployment | **Not deployed.** Production (`/home/sarmad/trader`) was not touched. Its venv Python was only used read-only as `LUFFY_PYTHON` to run the temporary test server. |
| Backend | **NONE.** No change under `trader/`, `tests/*.py`, config, kernel, Risk, Execution or Supervisor. `git diff --name-only -- trader tests SDD.md config.yaml` returns 0 files. |
| Graphify | `graphify update .` was run (AST only, no semantic extraction): 16 895 nodes, 38 109 edges. |

## B. Shared product system

The same product system is used on all nine routes. It is in `src/product.css`, which loads after `styles.css` and `live.css`, and in `src/components/product.tsx`.

- **Shell**
  - A persistent left rail groups the nine locked routes as **Desk** (Overview, Trades, Research, Strategies), **Partner** (LUFFY) and **Machine** (Operations, Live System, Knowledge, Diagnostics). Each route has a lucide icon and a brass active indicator.
  - A slim sticky context bar shows group / route, the mode badge, the session and sign-out.
  - The page heading is compact (21 px). There are no giant headings.
  - A static bearing plate (the existing local `chart-room.svg`) sits at the foot of the rail.
  - Below 760 px the rail becomes the existing 3-column mobile grid, including the "Controls · Panic" hint.
- **Material**
  - Surfaces are machined ink panels with hairline borders and an inset top highlight.
  - Brass is reserved for LUFFY's own presence and primary tiers.
  - Sea-teal is used only for VERIFIED, RECORDED and OBSERVED states. Amber means UNAVAILABLE; rose means FAILED or MALFORMED.
  - There is no neon, no animation of data, and no remote asset.
- **Tiering**
  - `tier-primary` marks primary surfaces with a brass hairline; `tier-quiet` makes secondary ones recede.
  - Record fields use a dense bordered grid, and long values span the full row.
  - Tables use uppercase micro-headers with tabular numbers.
  - The minimum text size is 11 px, as the existing v2.1 readability test requires.
- **Primitives**
  - `Figure`: an UNAVAILABLE value renders small and amber, never as a large number.
  - `Truth` / `truthClass`
  - `LineageStrip`: parent → this record → children.
  - `Distribution`: proportional bars, each labelled with its exact count.
  - Stage rail and `StageSection` (Evidence.tsx)
  - `RecordTable.open`: a direct link to the row's own recorded id.
- **Chrome labels** are now product-neutral: "OWNER WORKSPACE" or "FRONTEND PREVIEW", and a footer reading "LUFFY · OWNER WORKSPACE · backend commit <bootstrap commit | unknown>". The commit is the value the bootstrap session returns; no build label is invented.

## C. Routes

| Route | M3 result |
|---|---|
| **Overview** | Tiered for the owner, not a grid of equal cards: (1) an attention band with Needs You, control state, protection verification, kernel heartbeat, data source, News Guard UNAVAILABLE and Approval objects UNAVAILABLE; (2) a Capital panel with equity, realized-today and exposure figures beside the trajectory chart; (3) positions with protection, plus protection evidence; (4) "Recent recorded actions": decisions (primary), strategy activity, research, recorded risk/rent, owner attention records; (5) the decisions journal window. Positions link to their trade stories. |
| **Trades** | The trade story is a horizontal forensic **stage rail** of 8 numbered stages (opportunity → market context → decision → signals & votes → strategy → execution → accounting → outcome). Each stage shows its recorded status and linked ref. Below it, stage-numbered sections (02–08) hold the exact recorded fields in two columns. The rail becomes vertical in the drawer and on mobile. The Trade book adds a direct **Story** link per row. Keyset paging and history-change notices are unchanged. |
| **Research** | A primary ledger shows a verdict·status distribution with exact counts, beside a **Lab relationships** map: Questions & plans are UNAVAILABLE (dashed, with the backend reason); assessed ideas, results, candidate bank, registrations and seeded-by-strategy show counts of rows loaded in this read. A research item has a **parent → this → descendants** lineage strip, then its seeding strategy, candidate/evaluation, registrations, and parts/ablation. The directory keeps the locked design order; Questions remains the default. |
| **Strategies** | The workspace shows identity (name, registry state, id) and journal economics figures ("journal-booked, not venue-verified"). Identity/spec and the lifecycle timeline sit side by side. A family lineage strip shows parent → family/generation → children, then siblings. Source idea (provenance) sits beside "Recorded postmortem verdict · historical" ("a verdict at that time, not current health"). Then: research seeded, brain events by exact subject, recent trades, UNAVAILABLE health/capacity/allocation/version history. The registry adds a **Workspace** link per row. |
| **LUFFY** | The conversation is primary. Beside it is a mechanical core panel (the existing machined SVG, animated only while a request is pending). Replies carry a framed "Records mentioned by exact id · context, not the reply's source" block, with consulted reads as monospace chips. Chat remains non-operational: the browser tests assert zero gateway calls at every width. Composer and cancel are unchanged. Layout: stacked below 1100 px, a 2-column core/notes aside from 760 to 1099 px, and a side column only at ≥1100 px (§F). |
| **Operations** | Owner Interface and controls are unchanged. **What Luffy recorded doing** is a scannable chronology: UTC day separators, a monospace time column, kind markers (decision / trade opened / trade closed / control / lifecycle), per-kind counts, an exact-ref link and registry-checked strategies. The filter and cooldown count are kept. Current activity, signals, orders, control events and runtime status follow. |
| **Live System** | Declared topology is labelled "DECLARED architectural connection (repository map) · no throughput implied". Observed data movement is drawn as **static lanes**, one line style per evidence class: solid = records observed in window; dotted grey = none in window; dotted amber = read unavailable; dashed steel = declared with no proving record. There are no pulses and no animation. The completeness-claim logic is unchanged. |
| **Knowledge** | Largest change: a **clustered constellation** replaces the rectangular grid. There is one ring cluster per recorded `kind`; the best-connected note sits at the hub. Clusters are labelled "KIND · n", and the legend states that layout position carries no meaning. Notes are compact pills with a kind mark, and relations are drawn as straight lines. Recorded-kind filter chips (exact kinds only) and a reset are added. Selecting a note focuses it: non-neighbours are dimmed to 28% and non-incident edges to 12%, and labels appear only on focused edges. The inspector order is title → Explore neighbours → Connections · n → provenance → path trace. A "Stored record" panel under the graph shows the note body, linked records (existing exact links), source/code links, chronology and git history. Lenses, list view, timeline, 40-node cap, undrawn-edge disclosure and keyboard directory are unchanged. |
| **Diagnostics** | An owner summary board restates returned values: store probes (x of y OK; FAILED count), collectors by recorded status, the watchdog flag, disk free, host memory and the execution recovery ledger. Then: probes, storage/resources, watchdog/collectors, incidents/recovery/owner audit, control event history (keyset), and known visibility limitations. Session, Owner Interface, logs, runtime cards and investigations follow. |

## D. Truth preservation

The M2 adapter and contract layer is unchanged: `readContracts.ts`, `live.ts` and `contracts.ts` were not edited. MALFORMED and unavailable data still render as "Data unavailable" with the reason, distinct from VALID_EMPTY ("No records returned", "none recorded").

New visuals follow the same rules:
- The lab map shows a station without a store as UNAVAILABLE, not 0.
- Lineage strips show "none recorded" or "not in the ledger" / "not in registry" rather than guessing.
- Distributions and figures show exact backend numbers. `Figure` renders "Unavailable" as amber text, not as a value.
- Observed lanes use the backend `status` / `count` only.
- Cluster membership is the note's recorded `kind`. Kind filters remove, never relabel.
- Knowledge focus uses recorded edges only.

No inference by name, fuzzy match, substring or uniqueness was added. Links render only for backend-returned ids: `Story` / `Workspace` links use the row's own `id`. Chat mentions stay exact-id and labelled contextual, not the reply's source.

These remain as before:
- The postmortem is labelled historical, not current health.
- Journal figures are labelled journal-booked, not venue truth.
- Close Trade is absent.
- News Guard is UNAVAILABLE.
- Protection shows VERIFIED only through the existing expiry contract; the v2.1 and protection-r1/r2 suites pass.
- Questions, plans, recall, costs, shadow reports, health, capacity, allocation, version history, kernel process metrics, open venue orders, latency history and the incident ledger remain UNAVAILABLE.

The production bundle still contains no fixture module (asserted in `live.spec.ts`).

## E. Evidence routing (historical evidence preserved)

The existing browser suites wrote into historical `frontend/evidence/*`.
- `tests/browser/evidencePath.ts` adds `ev()`. With `EVIDENCE_SINK` set, every suite write under `evidence/` is redirected. With it unset, behaviour is unchanged.
- Every regression run in M3 used `EVIDENCE_SINK=/tmp/luffy-m3-sink…` and `OWNER_EVIDENCE_DIR`.
- `git status --short frontend/evidence` shows only the new `evidence/m3/`. No historical evidence file was modified.
- New M3 outputs go only to `frontend/evidence/m3`.

## F. MUST FIX: mobile LUFFY conversation (coordinator review)

**Defect.** In `/tmp/m3iter/m3/mobile-luffy.png`, an unconditional `product.css` `.conversation-layout {grid-template-columns: minmax(0,1.65fr) minmax(320px,1fr)}` came after the mobile rules. It squeezed the conversation to about 26 px at 390 px. At 768 px it would also have left about 180 px.

**Fix.**
- The layout is stacked by default. The two-column layout applies only at `min-width: 1100px`, where a 320 px side column fits beside a usable transcript.
- From 760 to 1099 px the aside becomes a 200 px core with the notes beside it.
- Below 760 px the conversation is `min(660px, 100dvh-40px)` tall, and the decorative chart-room overlay is hidden behind text.

**Test that would have caught it.** The new browser test `M3 LUFFY conversation is usable at 390, 759, 768, 1024 and 1440 px` measures real geometry and interaction:
- when stacked, conversation width ≥ the content width (≥560 px when side by side);
- transcript ≥240 px tall;
- composer width ≥ min(300, w−90) and inside the viewport;
- the conversation fits the viewport, and Send is on screen once it is scrolled to;
- a real send renders a reply with exact-id evidence, and the transcript stays in view;
- zero owner/gateway calls;
- zero horizontal overflow.

Before the fix, 390 px would have failed the width assertion (about 26 px against ≥ ~360 px). This is inferred from the review screenshot; it was not re-run as a negative control.

Measured (`evidence/m3/luffy-widths.json`):

| Viewport | Conversation | Transcript h | Composer | Reply evidence | Overflow |
|---|---|---|---|---|---|
| 390 | 362 | 371 | 324 | 290 | 0 |
| 759 | 731 | 371 | 693 | 659 | 0 |
| 768 | 516 | 384 | 478 | 361 | 0 |
| 1024 | 772 | 384 | 734 | 571 | 0 |
| 1440 | 712 (side by side) | 444 | 674 | 522 | 0 |

## G. Commands (from `frontend/`)

```bash
npm ci                                   # worktree had no node_modules
npm run typecheck                        # exit 0
npx vitest run                           # 139/139
npm run build                            # exit 0 (tsc + vite build)
LUFFY_PYTHON=/home/sarmad/trader/venv/bin/python EVIDENCE_SINK=/tmp/luffy-m3-sink \
  OWNER_EVIDENCE_DIR=/tmp/luffy-m3-sink/ownerdir PW_RESULTS=/tmp/luffy-m3-sink/results.json \
  npx playwright test                    # full suite: 101/101
LUFFY_PYTHON=… PW_RESULTS=evidence/m3/capture-results.json \
  npx playwright test -c playwright.m3.config.ts m3-capture   # 4/4, writes evidence/m3
LUFFY_PYTHON=… EVIDENCE_SINK=/tmp/luffy-m3-sink2 OWNER_EVIDENCE_DIR=evidence/m3 \
  PW_RESULTS=evidence/m3/live-perf-results.json npx playwright test live-perf   # 3/3
cd .. && graphify update .
```

## H. Tests

| Suite | Result |
|---|---|
| Typecheck | pass (exit 0) |
| Vitest | **139/139** (12 files; M2 contract/truth tests unchanged) |
| Production build | pass |
| Playwright full suite, final run after all changes (`frontend/evidence/m3/full-browser-output.txt`, results `frontend/evidence/m3/full-browser-results.json`; copied from the run's `/tmp` outputs) | **101/101** in 6.4 min. That is the 97 existing tests plus 4 M3 tests. |
| M3 capture (`evidence/m3/capture-results.json`) | 4/4: desktop nine routes and details; mobile; axe; LUFFY widths |
| live-perf after M3 (`evidence/m3/live-perf-results.json`) | 3/3 |

The full suite covers:
- M1 route delivery and failed chunks;
- all M2 workflows: connected hops, drawer → record, exact-id LUFFY mentions with zero gateway calls, injected read failures, unknown ids, session loss, slow-read concurrency;
- Trade History paging;
- protection R1/R2;
- Close Trade deferred;
- the v2 and v2.1 truth tests;
- preview and topology regressions.

An intermediate full run found 3 failures, all fixed:
1. axe `scrollable-region-focusable` on the LUFFY transcript. This was a real a11y defect: the transcript is now `tabIndex=0`.
2. The v2.1 test expected the Gross exposure figure to be a headed `section`. Figures are now `section` + `h3`.
3. The v2.1 minimum 11 px badge/eyebrow size. All product text is now ≥11 px.

## I. Accessibility

`evidence/m3/accessibility.json` records axe (WCAG 2.0 A/AA and 2.1 AA) on 12 LIVE targets:
- all nine routes;
- the trade story;
- the strategy workspace;
- the research item.

All 12 have **0 violations**.

The existing demo axe suites (overview, LUFFY, knowledge, live-system, evidence drawer) also pass, as do the keyboard graph-selection, focus-return and mobile no-overflow suites.

Added accessibility behaviour:
- rail icons are `aria-hidden`;
- kind filter chips are `aria-pressed` buttons;
- decorative cluster rings are `aria-hidden`;
- the lab map and distributions are labelled lists.

## J. Performance: same harness, baseline vs M3

`evidence/m3/baseline/performance-live.json` was captured at base code before any M3 change. `evidence/m3/performance-live.json` was captured after. Both use the same live-contract test server and Chromium. The comparison is in `evidence/m3/performance-comparison.json`. Each value is a single run (cold loads are the median of 3).

| Metric | Baseline | M3 |
|---|---|---|
| Cold useful Overview (median ms) | 319 | 321 |
| Cold chart (median ms) | 349 | 375 |
| Cold usable navigation (median ms) | 211 | 205 |
| First visit: Knowledge / Live System / Operations / LUFFY (ms) | 480 / 895 / 351 / 392 | 493 / 855 / 360 / 408 |
| Cached route (median / max ms) | 53 / 117 | 74 / 129 |
| While graph loading: navigate away / type 5 chars / chat reply (ms) | 361 / 64 / 106 | 358 / 71 / 108 |
| JS heap after churn / Knowledge (MiB) | 8.47 / 8.25 | 8.76 / 8.53 |
| Live System DOM nodes | 1545 | 1969 (observed lanes) |
| Server RSS (MiB) | 114.6 | 110.7 |

**Bundle:**
- main JS 333.10 → 337.68 kB (gzip 104.23 → 105.65);
- main CSS 31.82 → 66.74 kB (gzip 7.50 → 14.38);
- GraphView 196.39 → 200.72 kB;
- Evidence 20.40 → 26.35 kB;
- LiveReads 18.96 → 23.10 kB.

Build outputs are in `evidence/m3/baseline/build-output.txt` and `evidence/m3/build-output.txt`.

**Assessment:** no material regression. Changes are within tens of milliseconds on single samples. The cached-route median rose by 21 ms, still under 130 ms max. Live System has +424 DOM nodes, from the static lane rendering.

## K. Evidence (all SYNTHETIC SEEDED: temporary FastAPI test server over a fixture journal; not production data)

Everything below is in `frontend/evidence/m3/`. `evidence-manifest.txt` lists the sha256 of every file except itself.

- Desktop 1440, all nine routes:
  - `desktop-overview.png`, `desktop-trades.png`, `desktop-research.png`
  - `desktop-strategies.png`, `desktop-luffy.png`, `desktop-operations.png`
  - `desktop-live-system.png`, `desktop-knowledge.png`, `desktop-diagnostics.png`
- Connected and detail workflows:
  - `desktop-detail-trade-story.png`, `desktop-detail-decision.png`
  - `desktop-detail-strategy-workspace.png`, `desktop-detail-strategy-child.png`
  - `desktop-detail-research-item.png`, `desktop-detail-knowledge-selected.png`
  - `desktop-detail-luffy-reply.png`, `desktop-detail-operations-decisions.png`
- Mobile 390:
  - `mobile-overview.png`, `mobile-trade-story.png`, `mobile-luffy.png`
  - `mobile-knowledge.png`, `mobile-operations.png`, `mobile-strategy-workspace.png`
  - `mobile.json` (overflow 0 for each)
- LUFFY width matrix: `luffy-width-{390,759,768,1024,1440}.png` and `luffy-widths.json`
- Route readiness: `routes.json`
- Accessibility: `accessibility.json`
- Performance: `performance-live.json`, `performance-comparison.json` and `baseline/*`
- Results: `capture-results.json`, `live-perf-results.json`
- Full browser suite receipt: `full-browser-output.txt`, `full-browser-results.json` (101/101).
- Build: `build-output.txt`
- Hashes: `source-sha256.txt` (sha256 of every changed or added source and test file at completion)
- Changed-file manifest: `changed-files.txt` (git modified and untracked paths, including regenerated Graphify artifacts and itself)

I inspected the screenshots myself and corrected these problems before the final capture:
- the squeezed mobile conversation;
- the mobile heading offset;
- nowrap captions that pushed mobile card values off-screen;
- a checkbox stretched by a generic input rule;
- clipped Knowledge clusters (now fitted to cluster bounds);
- long inspector whitespace (the note record moved under the graph);
- decorative lines behind text on mobile.

## L. Changed files

New:
- `frontend/src/product.css`
- `frontend/src/components/product.tsx`
- `frontend/playwright.m3.config.ts`
- `frontend/tests/browser/evidencePath.ts`
- `frontend/tests/browser/m3-capture.spec.ts`
- `frontend/evidence/m3/*`
- this report

Modified source:
- `frontend/src/Shell.tsx`
- `frontend/src/liveRoot.tsx`
- `frontend/src/demo.tsx` (CSS import)
- `frontend/src/components/records.tsx` (wide fields)
- `frontend/src/components/workspace.tsx` (wide drawer fields; `open` link column)
- `frontend/src/views/Overview.tsx`
- `frontend/src/views/Evidence.tsx`
- `frontend/src/views/LiveReads.tsx`
- `frontend/src/views/Routes.tsx`
- `frontend/src/views/Luffy.tsx`
- `frontend/src/views/GraphView.tsx`
- `frontend/src/views/graphGeometry.ts` (`clusterLayout`)

Modified tests:
- `frontend/tests/harness/main.tsx` (loads `product.css`)
- `frontend/tests/browser/{corrections,live-perf,m1-routes,m2-routes,performance,preview,v2}.spec.ts`: output paths only, via `ev()`; no assertion changed.

The initial git gate was clean. A scoped Graphify query during M3 modified `graphify-out/cache/last_query_stamp`; the required `graphify update .` then regenerated the outputs under `graphify-out/`.

## M. Real limitations and blockers

- **No blockers.**
- Knowledge text is small when many clusters are fitted at once (for example, 11 single-note clusters). Zoom, list view, the directory and the inspector remain the readable paths. Layout position is explicitly not meaningful.
- The Live System topology still comes from the declared repository map. Observed lanes are a separate panel: joining observed flows onto graph edges would need an exact id contract the backend does not provide, so no join was made.
- Diagnostics shows `frontend_build FAILED` in the synthetic server. That is the test server's own probe result for its temporary root, shown honestly.
- Performance figures are single-run samples on the synthetic harness, not production.
- Kernel-owned evidence listed in M2 §F remains unavailable, unchanged.
- Nothing is committed. Generated Graphify changes were removed during commit-boundary cleanup and are excluded from the M3 package.

## N. Accepted M3 commit boundary

The owner accepted the M3 functional/product review. Commit preparation made no product, visual, source, test or config changes and did not rerun the review. The 25 frontend source/test/config files still match `source-sha256.txt`.

The 142 generated Graphify paths matched the completion manifest exactly (8 tracked modifications and 134 new files). After a verified archive outside the worktree, the 8 tracked paths were restored to the frozen base and only the 134 manifest-listed new files were removed. No unrelated paths or M1/M2 evidence were changed. Graphify is clean and excluded from staging.

The final package is 71 files: 14 frontend source files, 11 test/config files, 45 M3 evidence files and this report. `frontend/playwright.m3.config.ts` is the sole changed configuration file: it scopes M3 captures to the existing production-build temporary FastAPI test server and M3 test selection/reporting. No dependency, production build, backend, kernel or trading configuration changed. `changed-files.txt` now lists this exact package; the evidence manifest was refreshed for that metadata change.

The staged whitespace check found one trailing space in the copied browser log. Only that space was removed; its original was retained outside the worktree, and the evidence hash was refreshed. No test result or source content changed.
