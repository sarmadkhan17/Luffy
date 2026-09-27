# OWNER-FRONTEND-V1 — F1 routing correction

**Astra implementation: ready for Opus independent re-review.**
**Uncommitted. NOT_DEPLOYED.** No backend integration or control-plane edits.

Base remains `1eb00e151113d0add6033ef809ccdf6e30623003`, branch
`owner-frontend-v1`, worktree `/home/sarmad/trader-owner-frontend`.
All package files remain new, untracked files inside `frontend/`.

This follow-up changes only `src/views/graphGeometry.ts`,
`tests/browser/corrections.spec.ts` and `tests/harness/main.tsx`, plus this handoff
and generated evidence/manifest. F2, chat, Knowledge and visual components are
unchanged. The previous exact-overlap test missed Opus's reported near-parallel
path; its former PASS did not establish F1 closure. The corrected `se3` turns
72 flow units before Research's input port and its vertical run is separated
from `se7` by 36 units. Supplied nodes, edges and directions are unchanged.

## Review findings and closure evidence

| Finding | Correction / evidence |
| --- | --- |
| **F1: misleading topology** | System nodes use directional ingress, execution and feedback rows. Every directed architecture edge has a target arrow. Orthogonal visibility-grid routing avoids node rectangles, reserves 36 flow units between parallel runs and excludes unrelated ports within a 48-unit square radius. Only local fan-in/fan-out within 72 units of the same input/output port may converge; sharing a component does not exempt remote runs. Distances scale with graph zoom; they are not a fixed screen-pixel guarantee. All nine original nodes and nine `(id, source, target)` relationships are preserved. |
| **F1 regression** | Real Chromium samples rendered SVG paths every 2 SVG units against actual DOM node rectangles, checks all directions/arrowheads, measures close parallel runs and exact point-to-segment distance from unrelated ports. Covers default 9 edges, filtered 3 edges, 40-node chain / 39 edges, search subset / 9 edges, and a 40-node fan-in/back-edge graph / 44 edges. Every expected edge must render. The review's failing `se3`/`se7` paths are injected as a negative control: the new checker must flag both `se3:se7` proximity and `se3:s8` port approach. See `evidence/topology-regression.json`, which also includes exact corrected DOM paths. |
| **F2: stale reads active** | One shared health presentation drives nodes, inspectors and lists. Primary **STALE** uses a muted stripe; historical health appears only as **Last reported: ACTIVE/IDLE/…**. Missing telemetry is **UNAVAILABLE**, distinct from fresh telemetry with **UNKNOWN** health. No stale activity animation. |
| **F2 regression** | Browser assertions cover all nine stale node labels, absence of active/idle styling and animation, inspector historical text and all nine list labels. Component checks distinguish missing telemetry from unknown health. `evidence/system-stale.png` captures the stale list and inspector. |
| **m1** | Owner messages retain **Cancelled — no reply**, with button/scenario/route-exit reason, across subsequent sends and route visits. Failed requests also retain **Failed — no reply**. |
| **m2** | Stream updates follow the bottom only when the reader is within 64 px of it; otherwise show **Jump to latest**. Browser test reads older messages while multiple chunks arrive, then verifies the jump. |
| **m5** | Missing neighbours appear as disabled **Unavailable reference: …** entries. No non-null assertion on the missing record and no graph crash. Adversarial harness supplies a dangling reference. |
| Search remount | Search no longer keys React Flow. Existing instances update and fit to changed scope. Browser test tags the graph DOM instance and verifies it survives filtering. A no-results state may still remove the graph intentionally. |
| **m7** | Solid 3 px mint keyboard outline with a dark separating ring. |
| **m8** | Explore neighbours sits immediately under the selected title/status, ahead of provenance. Inspector content flows normally rather than hiding actions inside a clipped scroll area. |
| Mobile | Graphs default to list/detail below 760 px; Show graph is explicit. Zoom controls are outside the node canvas. Mobile Overview capture waits for the actual equity value and rendered chart canvas, not a heading or arbitrary sleep. |

**Explicitly remaining:** m3 — chat evidence opens its exact drawer but does not
select a matching Knowledge record. No ID mapping was invented. m4 — Timeline
still displays the original equal-time fixture snapshot; no artificial chronology
or timestamps were introduced. m6 — the required TradingView attribution link
remains; it makes no request unless activated. Five non-priority routes still say
“Not implemented in this preview.” Voice, LLM/backend integration, approvals,
panic/resume and all trading controls remain unavailable.

Other non-blocking findings intentionally remain: never-observed components in
the stale scenario retain **STALE / Last reported: UNKNOWN** precedence;
unroutable edges have no explicit notice; fixture IDs drive the default system
positions, with a generic fallback for other IDs; Knowledge has loop-backs behind
some node bodies. The tested default, filtered and 40-node topologies render all
expected edges, but this is not a general routability guarantee for arbitrary
backend graphs.

## Visual references applied

Read all three supplied PNGs and their README in
`../LUFFY-approved-visual-references/`. Used only their material, hierarchy and
visual distinction. No NEXUS branding, invented metrics, navigation, agents,
markets or relationships were imported.

- LUFFY: larger 320 px machined core, segmented rings, fasteners and central iris;
  a single responsive instance becomes 72 px in the mobile conversation header.
  Motion remains driven only by actual fixture request state. Conversation and
  composer remain primary.
- Knowledge: kind-specific colour accents and curved relationships in an open
  constellation arrangement, distinct from process lanes. The **supplied chain
  and its code link remain a chain and code link**: branching was not fabricated
  to imitate the reference. Node kinds remain explicit text as well as colour.
- Live System: directed component rows, clear feedback tracks and status stripes.
- Maritime atmosphere: restrained instrument markings and a tiny local SVG with
  compass/rhumb lines and wave contours. No scenic image, remote asset or library.

All three priority screens, mobile core/list views and completed mobile Overview
were inspected as real browser renders. Screenshots:
[LUFFY](evidence/luffy.png), [Knowledge](evidence/knowledge.png),
[Live System](evidence/live-system.png), [Live System at 2×](evidence/live-system-2x.png),
[mobile Overview](evidence/mobile-overview.png).
The other `mobile-*.png` files show all priority surfaces.

## Validation and reproducibility

```bash
cd /home/sarmad/trader-owner-frontend/frontend
npm run typecheck
npm run build
npm test
npm run test:browser
```

The owner preview already running on **127.0.0.1:4173** was left running; its
process was not restarted. Rebuilding local static preview assets is not a
production deployment. To start it when absent: `npm run preview`.
Tests own **4174** (production build) and **4175** (separate test-only harness).
`npm ci` restores the existing exact lockfile if needed.

- Typecheck and production build pass.
- **11 component/adapter tests** across three files.
- **16 browser tests**, including F1/F2 and adversarial regressions.
- Completed mobile capture (actual values and chart) is included in the full
  browser run. `evidence/capture-check.txt` retains the earlier focused check.
- Axe: zero WCAG 2 A/AA + 2.1 AA violations on the four surfaces and evidence
  drawer; automated checks are not a complete accessibility certification.
- Reduced motion, hidden-document avatar and system cleanup, cancellation,
  WebGL-disabled rendering, keyboard focus and slow optional assets rechecked.
- Real tab backgrounding is not proven by headless Chromium; visibility changes
  are injected into the real handlers, as in Opus's review.
- Dependency versions and lockfile are unchanged. **No new runtime or development
  dependency** was installed. Existing real React Flow, Motion and Lightweight
  Charts are used in rendering measurements, never stubs.
- The isolated test harness eagerly imports graph code and emits a >500 kB
  development-test chunk warning; it is excluded from the normal production
  build and intended runtime. Scale results identify this different load setup.
- Raw browser results, axe results, topology sampling and scale measurements are
  retained in `evidence/`. Source hashes bind the implementation/tests/config.

## Preservation and intended commit set

[Exact intended commit files](evidence/changed-files.txt) is an explicit list,
relative to the worktree root. It excludes `frontend/new/`, `node_modules/`,
`dist/`, `.test-dist/`, temporary test results and the supplied reference archive
and directory. **Do not stage `frontend/` wholesale.** No files are staged.

`frontend/new/` is preserved, not deleted. SHA-256 comparison of every file in it
against `evidence/unrelated-preservation.json` verifies unchanged bytes. A local
`/new/` ignore rule additionally excludes it from this package. Reference images
outside `frontend/` are also untouched.

No Python, legacy HTML, AGENTS, SDD, STATE or NEXT edits. No chat-safety,
dashboard-performance or production worktree access/modification was needed.
No production data, services, watchdog, credentials, orders or control mutations
were touched. No commit, merge or deployment.

## Integration boundary

[Typed seams](src/adapters/contracts.ts) and the [backend binding table](README.md#later-backend-binding-work-not-implemented)
remain preview-only. Authenticated mode/bootstrap, account/protection evidence,
bounded knowledge/provenance, actual component telemetry/events and separately
reviewed cancellable chat still need explicit backend bindings. No endpoints
were invented, no fixture fallback on real-source failure was added, and DEMO
mode is never a live permission.

## Measurement scope

Chromium 151.0.7922.34 headless, Linux, 1440×1080, Ryzen 5 5600H machine reporting
4 logical CPUs and 7.70 GiB RAM, loopback without throttling. Node 24.15.0.
Default fixtures: 30 chart points, 2 positions, 12 Knowledge nodes / 11 edges
(11 nodes in its default lens), 9 system nodes / 9 edges, 1 event. Scale harness:
500 Knowledge input nodes capped to 40 rendered, and 40 system nodes / 39 edges.

Cold timings use three fresh cache-disabled contexts. Interaction times include
Playwright observation; they are not INP. Renderer CPU is CDP TaskDuration delta,
heap is JS heap after forced GC; neither is whole-browser memory or GPU usage.
Route churn revisits the same LUFFY view before/after 206 changes. No claim of
production/API performance, trading-core isolation, p95 or long-duration leak
absence follows from these synthetic samples. Backend/Journal/WAL latency was
not measured. Prior measurements are retained in `baseline-performance.json`.

| Measurement | Corrected fixture preview |
| --- | --- |
| Cold usable navigation, 3 trials | 476–721 ms |
| Cold useful Overview values | 729–839 ms |
| Cold real chart rendered | 1161–1183 ms |
| First Knowledge visit, lazy production chunk | 873 ms |
| Cached surface visits | 205–412 ms |
| Graph search / composer input | 296 / 165 ms |
| Cold long tasks | 63–151 ms |
| Idle renderer CPU, ~1.2 s samples | 11.9–16.8 ms |
| Real chat motion / supplied system event CPU, ~1.2 s | 101.8 / 68.0 ms |
| Idle JS heap across surfaces | 4.27–6.33 MiB |
| 206 route changes, LUFFY heap before → after | 6.95 → 8.23 MiB |
| 206 route changes, DOM nodes / listeners before → after | 899 / 177 → 899 / 177 |
| Closing preview to blank, heap | 1.26 MiB |

DOM and listener counters are flat over this churn sample; heap grows by 1.29 MiB.
This does not establish long-duration leak absence.

**Slower measurements versus the saved pre-routing run:** graph search
213 → 296 ms (+83 ms), composer input 151 → 165 ms (+14 ms), cached visits
150–276 → 205–412 ms. Cold useful values were 379–491 ms versus 729–839 ms now.
First Knowledge was 977 ms versus 873 ms now. These are small-sample observations;
no controlled A/B measurement attributes them to router cost or machine load.
All browser assertions pass, but this run does not prove unchanged performance.
`pre-routing-performance.json` retains the directly preceding package results;
`baseline-performance.json` retains the original implementation results.

**500-input / 40-visible scale harness:** system load 955 ms; first Knowledge
navigation 555 ms (graph already eagerly loaded); cached Knowledge 425–484 ms.
15 search keystrokes: 1247 ms wall / 1023 ms renderer CPU. Heap 6.32 MiB,
3218 DOM nodes, 400 listeners; 17 long tasks ranging from 50 to **256 ms**.
The cap and graph instance preservation pass. The previous run had system load
476 ms, cached Knowledge 144–287 ms and search 701 ms wall / 479 ms CPU; the
current run is slower. This eagerly loaded harness is not comparable to the
normal production build's lazy first-visit timing. The separate 40-node / 44-edge
fan-in/back-edge case is a correctness regression, not a performance benchmark.

## Bundle impact

No dependency additions. Gzip values below use level 6 consistently with the
saved initial bundle; they differ slightly from Vite's console compression.
The decorative local SVG adds no network service or animation runtime.

| Asset | Corrected raw bytes | Corrected gzip bytes | Gzip delta from initial |
| --- | ---: | ---: | ---: |
| Main JS | 311708 | 97023 | +447 |
| Lazy graph JS | 192527 | 61741 | +1902 |
| Lazy avatar/Motion JS | 126979 | 40903 | +303 |
| Main CSS | 21833 | 5590 | +1352 |
| Lazy chart JS | 166579 | 53540 | 0 |
| Graph CSS | 15413 | 2555 | 0 |
| Shared JSX runtime | 8823 | 3386 | 0 |

Total gzip increase: **4004 bytes** versus the initial implementation, or
**304 bytes** versus the package just reviewed by Opus, across all runtime chunks. Exact emitted
filenames/sizes are in `evidence/bundle.json`. Graph, avatar and chart remain lazy;
normal `dist/` contains no test harness. Return this uncommitted package to Opus
for independent re-review before any separate integration or commit decision.

