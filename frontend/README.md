# LUFFY owner frontend — isolated preview

Four implemented surfaces: Overview, LUFFY, Knowledge and Live System.
All nine navigation routes exist; the other five explicitly say they are not
implemented. This is a synthetic frontend preview, with no backend or trading
connection. One correction pass is complete; independent Opus re-review is pending. NOT_DEPLOYED; no commit made.

Base: `1eb00e151113d0add6033ef809ccdf6e30623003`
Branch: `owner-frontend-v1`
Worktree: `/home/sarmad/trader-owner-frontend`

## Local preview

```bash
cd /home/sarmad/trader-owner-frontend/frontend
npm ci
npm run build
npm run preview
```

Open **http://127.0.0.1:4173**. The server binds only loopback and refuses a
conflicting port. For source development use `npm run dev` on the same port
instead. No production Node server is proposed.

```bash
npm run typecheck
npm test
npm run test:browser
```

Browser tests start/stop isolated previews on 4174 and 4175. They leave the owner preview on 4173 alone. Port 4175 serves a separate test-only build containing scaled and adversarial fixture adapters; it is not included in the normal production build. They use fresh disposable browser contexts,
not owner profiles or credentials. The config reuses the existing Chromium at
`~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`; to use another
**already installed** compatible binary set `PREVIEW_CHROMIUM=/absolute/path`.
Do not run a browser installer by default. Node 24.15.0 / npm 11.12.1 were used.

## Review entry points

- [Handoff and measured performance](HANDOFF.md)
- [Exact added-file list](evidence/changed-files.txt)
- [Typed adapter contracts](src/adapters/contracts.ts)
- [Explicit fixture implementation](src/adapters/fixture.ts)
- [Browser checks](tests/browser/preview.spec.ts)
- [Performance methodology](tests/browser/performance.spec.ts)
- [Skill provenance](.skills/SOURCES.md)

## Interaction notes

The scenario selector exercises fixed normal/stale/missing/error fixtures. A
"fixed fixture snapshot" is not a claim of current production freshness. Missing
values remain unavailable. Errors reject requests; no replacement data is loaded.

LUFFY uses deterministic fixture replies, not an LLM. Enter sends; Shift+Enter
adds a line. Cancellation, navigation away and scenario changes abort the request.
Aborted owner messages retain a cancelled/no-reply marker. Streaming follows the bottom only while the reader is near it; otherwise Jump to latest is available. The transcript is retained in memory across routes, bounded at 100 messages, and
resets on page reload. Replies and request states are actual adapter progress;
there is no invented thought stream. Voice is visibly unavailable.

Knowledge provides text search, four lenses, graph/list views, pan/zoom,
selection, neighbours and provenance inspection. Timeline opens the dated list;
Code includes a code-reference link. Under 760 px, lists are the default and graphs remain available explicitly. Viewport controls occupy a strip outside node content. Graphs render at most 40 nodes; they are
read-only, without node/edge mutation. Live System separately labels architectural
connections and offers a two-second replay of one explicitly supplied synthetic
event. Stale/missing scenarios contain no replayable events.

The mechanical avatar is a machined local SVG rendered with Motion: 320 px on desktop and 72 px in the mobile conversation header. Both graphs use
React Flow (DOM/SVG); the account chart uses Lightweight Charts (Canvas2D).
These baseline renderers work without WebGL. No 3D runtime or model downloads
are required. The avatar, chart and graph implementation are separate lazy
chunks. Route unmounts dispose charts/graphs, stop animations and abort requests;
visibility changes stop motion. There are no refresh loops or live subscriptions.

## Later backend binding work (not implemented)

`OwnerAdapter` is a frontend seam, not an assertion that these production API
endpoints exist. It deliberately has only `mode: 'DEMO'`. Integration must
introduce a reviewed authenticated bootstrap/mode contract, remove the fixture
scenario selector for real sources, and map reviewed backend contracts explicitly.
Never inject real records into a preview still labelled synthetic.

| Seam | Required binding |
| --- | --- |
| `overview` | Authenticated account/equity/P&L and currency semantics; exposure, position and independently verified protection state; exact source time, freshness/error status; real Needs You objects. |
| `graph('knowledge')` | Bounded/paginated runtime domain-knowledge nodes and edges, lens membership, stable IDs, direction, typed vs ordinary relationships, classification, provenance and source timestamps. Code references must remain distinguishable from domain evidence. |
| `graph('system')` | Actual component identities; declared architectural connections; last-observed health with time and coverage; deduplicated timestamped activity events bound to real edge IDs. Stale/missing telemetry must not imply health or throughput. |
| `chat` | Separately reviewed safe query-only conversation transport; authenticated session, structured errors, cancellation, incremental replies, request/message IDs, evidence IDs and exact provenance. Do not bind the legacy `/api/chat` by assumption. |
| Cross-cutting | Session expiry, transport loss, runtime payload validation, pagination, time-based freshness, reconnect/deduplication, retention and authorization. No fixtures on real-source failure. |

No production authentication, approval, panic, resume, order, sizing or other
control mutation contract is implemented here. Integration and any control
surface require separate scope and review. Legacy parity is not claimed.
