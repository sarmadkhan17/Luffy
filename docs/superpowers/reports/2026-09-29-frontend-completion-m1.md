# LUFFY-FRONTEND-COMPLETION-M1 — route delivery + core read paths

Base `8385e9d`, branch `owner-frontend-live-binding-v1`. Uncommitted, not
deployed; production dashboard (cwd `/home/sarmad/trader`) untouched. No
trading, execution or kernel code changed.

## Root cause

`decisions` in `trader/api/graphql_schema.py` was a **sync** Strawberry
resolver. Strawberry runs sync resolvers inline on the ASGI event loop, and it
issued `SELECT * FROM votes WHERE cycle_id=?` once per decision whether or not
`votes` was selected. `votes` (4.47 M rows) has no `cycle_id` index, so each
lookup is a full scan (~0.87 s). Overview's 101-decision read therefore held
the event loop for ~89 s; no JS chunk, API read or asset could be served, and
every lazy route stayed on "Loading view…".

Fix:

- `votes` is a field resolver: read only when selected, and batched through a
  per-request DataLoader (one `IN (…)` read per request, same `cycle_id`
  semantics; `recent_vetoes` now returns its real votes instead of `[]`).
- Every `Query` resolver runs in a worker thread (`_offloop`); Journal
  connections are thread-local.
- Limits bounded (`MAX_ROWS=1000`, equity 5000; negative offsets → 0).
- `/owner-api/v1/system` no longer sorts all 640 k decisions for one `ts`.
- Frontend: `lazyChunk` fails a chunk after 20 s or on load error
  (`ChunkLoadError`), never caches the failure past an explicit Retry or
  navigation, and the route boundary shows "This view could not be loaded" with
  Retry. Overview's recent-activity island has its own boundary.

Production measurement (read-only `mode=ro`, `frontend/evidence/m1-decisions-prod.json`):
before = 5.8 s loop stall for 5 of the 101 votes scans (projected ~89 s);
after = 1.5 s wall, **0.0 s** event-loop lag, 0 votes reads.

## Read contracts (`trader/dashboard/owner_reads.py`)

All read-only, bounded, joined only by recorded ids; gaps are returned as
`unavailable: [{field, reason}]` and rendered as UNAVAILABLE.

| Route | Endpoint | Recorded data |
|---|---|---|
| Trades | `GET /owner-api/v1/trades/{id}/lineage` | decision (scan id, signals, score), strategy (current row + spec sha256), booking receipts with sha256 replay integrity and fill assessment, outcome, MFE/MAE |
| Strategies | `/strategies` (enriched), `GET /strategies/{id}` | generation/parent, spec & params sha256, declared spec, registry stats, journal economics per strategy id, lifecycle (created, state change, `strategy_*` control events), recent trades |
| Research | `GET /research` | results (combos), runs (batches), evidence (controls, gauges), research bank (slices, candidates), registrations (tests); investigations via existing `/api/investigations/latest` |
| Operations | `GET /operations/activity` | newest 500 decisions (insertion order): risk/skip classes, strategy signals, scan ids; 20 newest journal trades; 30 control events; execution recovery; attention scan via `/api/attention/latest` |
| Knowledge | `GET /knowledge/note?id=` | note body (≤64 kB), frontmatter, wikilinks, `source_paths` classified code/document with existence, chronology (frontmatter dates, file mtime, git log) |
| Diagnostics | `GET /diagnostics` | data store sizes, disk, host load/memory, dashboard RSS/threads, watchdog flag + log tail, collector health files, control events, owner audit, recovery ledger |

## Still unavailable (kernel-owned evidence not recorded)

Strategy version/spec hash at trade entry; venue-fill-verified accounting (all
current receipts are `unattributed_journal_booking`); funding attribution;
per-strategy health, capacity and allocation; spec version history; research
questions, frozen plans, costs, shadow reports, prior recall; kernel process
resources; structured incident ledger; API latency history; open venue orders
(read only inside the kernel's protection snapshot). A `votes(cycle_id)` index
would make selected-votes reads cheap; it is a kernel-schema change on a live
1.8 GB journal and was not made.

## Correction pass (Astra's three blockers)

1. **Lazy retry.** Chromium keeps a failed dynamic import in its module map, so
   recreating `React.lazy` sent no request. `lazyChunk` no longer pretends:
   after any chunk failure, the boundary's Retry and the next navigation reload
   the current URL (hash route and query kept). Browser tests assert a real new
   chunk response after Retry and after navigating away/back.
2. **Lifecycle attribution.** `strategy_*` control events are attributed only
   by exact equality of the parsed `detail.id` string. Events with no id
   (production `strategy_promote`/`strategy_analyst_add` rows) are counted as
   `unattributed_lifecycle_events`, never matched by text.
3. **Fill verification.** A receipt's assessment is taken only from
   `booking.replay` and only when row, receipt and booked snapshot all bind
   to this trade; otherwise `assessment=null` with `integrity=failed:<reason>`.
   `fills_verified` requires every booked receipt to replay with
   `verified_leg_fills_only`. Production: 15 trades with receipts, 0 fully
   verified; 2 have only the close leg venue-verified ("1 of 2").

## Tests

- Backend: `tests/test_owner_reads.py` (13, incl. a negative control proving
  the responsiveness check fails with inline resolvers) plus owner API, trade
  history, rent, boundary, attention, auth, read-cache, pipeline, owner
  interface: 332 passed.
- Frontend: typecheck, production build, vitest 86/86 (`tests/m1.test.tsx`
  adds chunk timeout, explicit retry, lineage, research, contract rejection).
- Browser: Playwright 87/87 (`tests/browser/m1-routes.spec.ts`: 9 routes under
  2 s slow decisions reads, concurrency + paging, failed chunk, drawers).
  Evidence: `frontend/evidence/m1-routes.json`, `m1-concurrency.json`.
  The full run also regenerated existing screenshot/performance evidence.
